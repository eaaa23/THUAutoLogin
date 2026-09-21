#!/usr/bin/env python3
"""THU Auto Login — Windows installer logic (no GUI).

Kept separate from ``installer.py`` so the behaviour can be exercised without a
display and without importing tkinter.

The install is deliberately split into two phases, because the extension has to
be on disk *before* the user can load it, and the extension ID can only be read
back out of the browser *after* it has been loaded:

  phase A  extract_extension()      lay the extension down
  phase B  install_host(ext_id)     copy the host, write the manifest + registry

That ordering is the fix for the recurring "wrong extension id in
com.thu.autologin.host.json" problem: the ID is no longer guessed from a path,
it is read from the browser's own configuration (or typed by the user from
chrome://extensions) after the extension is actually loaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

VERSION = "1.0.0"
HOST_NAME = "com.thu.autologin.host"
UNINSTALL_KEY = "THUAutoLogin"
DISPLAY_NAME = "THU Auto Login"

APPDATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "THUAutoLogin"
)
HOST_EXE_NAME = "thu-autologin-host.exe"
MANIFEST_NAME = f"{HOST_NAME}.json"
EXT_DIR_NAME = "extension"
SETUP_EXE_NAME = "THUAutoLogin-Setup.exe"

NM_REGISTRY_SUBKEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
UNINSTALL_SUBKEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{UNINSTALL_KEY}"

# Where each Chromium browser keeps its profile data, relative to %LOCALAPPDATA%.
# Both use the same ID derivation for unpacked extensions, so either one can be
# used to discover the ID.
BROWSERS = (
    ("Chrome", ("Google", "Chrome")),
    ("Edge", ("Microsoft", "Edge")),
)

# Manifest::LOCATION_UNPACKED in a Chromium preferences file.
LOCATION_UNPACKED = 4

# Chrome extension IDs are 32 characters drawn from a-p (a hex digest with each
# nibble mapped onto a letter).
ID_PATTERN = re.compile(r"^[a-p]{32}$")
ID_IN_URL_PATTERN = re.compile(
    r"(?:chrome|edge|chromium)-extension://([a-p]{32})", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    """Platform seam, isolated so tests can drive the installer off-Windows."""
    return os.name == "nt"


def _log_line(message: str) -> None:
    """Append to install.log, for when there is no console to print to.

    The packaged installer is a windowed build, so sys.stdout is None there;
    without this, an unattended (--silent) run would leave no trace at all.
    """
    try:
        os.makedirs(APPDATA_DIR, exist_ok=True)
        with open(os.path.join(APPDATA_DIR, "install.log"), "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


def say(message: str = "") -> None:
    """Print when a stdout exists, otherwise log to install.log."""
    stream = sys.stdout
    if stream is not None:
        try:
            print(message, flush=True)
            return
        except Exception:
            pass
    _log_line(message)


# ---------------------------------------------------------------------------
# Bundled payload
# ---------------------------------------------------------------------------


def bundle_root() -> str:
    """Directory holding the bundled payload (PyInstaller's temp dir when frozen)."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def locate_payload():
    """Return (host_exe, extension_dir), using source-tree fallbacks so the
    installer can be run without building the frozen exe first."""
    root = bundle_root()
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.normpath(os.path.join(here, ".."))

    host_exe = next(
        (
            path
            for path in (
                os.path.join(root, HOST_EXE_NAME),
                os.path.join(here, "build", HOST_EXE_NAME),
                os.path.join(here, "dist", HOST_EXE_NAME),
            )
            if os.path.isfile(path)
        ),
        None,
    )
    ext_dir = next(
        (
            path
            for path in (os.path.join(root, EXT_DIR_NAME), os.path.join(repo, "THUAutoLogin"))
            if os.path.isdir(path)
        ),
        None,
    )
    return host_exe, ext_dir


def installed_extension_dir() -> str:
    return os.path.join(APPDATA_DIR, EXT_DIR_NAME)


# ---------------------------------------------------------------------------
# Extension ID
# ---------------------------------------------------------------------------


def id_from_bytes(path_bytes: bytes) -> str:
    """Chromium's unpacked-extension ID: first 32 hex digits of the SHA-256 of
    the absolute path, with 0-9a-f mapped onto a-p."""
    digest = hashlib.sha256(path_bytes).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def extension_id_candidates(ext_dir: str):
    """Path-derived IDs, one per plausible byte encoding.

    Used only as a last-resort fallback for unattended installs. For a path with
    non-ASCII characters it is genuinely ambiguous which encoding Chromium
    hashes, which is exactly why the GUI asks the browser instead.
    """
    absolute = os.path.abspath(ext_dir)
    seen, ids = set(), []
    for encoding in ("utf-8", "mbcs"):
        try:
            data = absolute.encode(encoding)
        except (LookupError, UnicodeEncodeError):
            continue
        if data in seen:
            continue
        seen.add(data)
        ids.append(id_from_bytes(data))
    return ids


def normalize_extension_id(text):
    """Accept a bare ID or a pasted ``chrome-extension://<id>/`` URL.

    Returns the normalised 32-character ID, or None if the text cannot be one.
    """
    if not text:
        return None
    cleaned = text.strip()
    url_match = ID_IN_URL_PATTERN.search(cleaned)
    if url_match:
        cleaned = url_match.group(1)
    cleaned = cleaned.strip().strip("/").lower()
    return cleaned if ID_PATTERN.match(cleaned) else None


class IdMatch:
    """One place the extension was found loaded."""

    __slots__ = ("browser", "profile", "extension_id")

    def __init__(self, browser, profile, extension_id):
        self.browser = browser
        self.profile = profile
        self.extension_id = extension_id

    def __repr__(self):
        return f"<IdMatch {self.browser}/{self.profile} {self.extension_id}>"

    def describe(self):
        return f"{self.browser} · {self.profile}"


def browser_user_data_dirs():
    """Yield (browser_name, user_data_dir) for browsers that are installed."""
    base = os.environ.get("LOCALAPPDATA", "")
    if not base:
        return
    for name, parts in BROWSERS:
        directory = os.path.join(base, *parts, "User Data")
        if os.path.isdir(directory):
            yield name, directory


def detect_extension_ids(ext_dir: str):
    """Find the extension's real ID in Chrome's and Edge's own configuration.

    This is the authoritative source: the browser computed the ID itself, so it
    needs no assumptions about how the path was encoded. Returns a list of
    IdMatch, one per profile where the extension is loaded.
    """
    target = os.path.normcase(os.path.normpath(os.path.abspath(ext_dir)))
    matches = []

    for browser, user_data in browser_user_data_dirs():
        for profile in sorted(os.listdir(user_data)):
            profile_dir = os.path.join(user_data, profile)
            if not os.path.isdir(profile_dir):
                continue
            for filename in ("Secure Preferences", "Preferences"):
                path = os.path.join(profile_dir, filename)
                if not os.path.isfile(path):
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                except Exception:
                    # A profile can be mid-write or hold malformed JSON; skip it.
                    continue

                settings = (data.get("extensions") or {}).get("settings") or {}
                for extension_id, entry in settings.items():
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("location") != LOCATION_UNPACKED:
                        continue
                    recorded = entry.get("path") or ""
                    if not recorded:
                        continue
                    candidate = (
                        recorded
                        if os.path.isabs(recorded)
                        else os.path.join(profile_dir, recorded)
                    )
                    if os.path.normcase(os.path.normpath(candidate)) == target:
                        if ID_PATTERN.match(extension_id):
                            matches.append(IdMatch(browser, profile, extension_id))
    return matches


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def register_native_host(manifest_path: str) -> None:
    import winreg

    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, NM_REGISTRY_SUBKEY, 0, winreg.KEY_WRITE)
    try:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, manifest_path)
    finally:
        winreg.CloseKey(key)


def unregister_native_host() -> bool:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, NM_REGISTRY_SUBKEY)
        return True
    except FileNotFoundError:
        return False


def register_uninstall_entry(uninstall_exe: str) -> None:
    import winreg

    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_SUBKEY, 0, winreg.KEY_WRITE)
    try:
        values = {
            "DisplayName": DISPLAY_NAME,
            "DisplayVersion": VERSION,
            "Publisher": "THU Auto Login",
            "InstallLocation": APPDATA_DIR,
            "UninstallString": f'"{uninstall_exe}" --uninstall',
            "QuietUninstallString": f'"{uninstall_exe}" --uninstall --silent',
            "NoModify": 1,
            "NoRepair": 1,
        }
        for name, value in values.items():
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)
    finally:
        winreg.CloseKey(key)


def unregister_uninstall_entry() -> bool:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_SUBKEY)
        return True
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Phase A — lay the extension down so the user can load it
# ---------------------------------------------------------------------------


class InstallError(Exception):
    pass


def extract_extension() -> str:
    """Copy the bundled extension into %LOCALAPPDATA% and return its path."""
    _host, ext_src = locate_payload()
    if not ext_src:
        raise InstallError("安装包内缺少 extension 目录（构建不完整）")

    destination = installed_extension_dir()
    os.makedirs(APPDATA_DIR, exist_ok=True)
    if os.path.isdir(destination):
        shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(ext_src, destination)
    return destination


# ---------------------------------------------------------------------------
# Phase B — host, manifest, registry
# ---------------------------------------------------------------------------


def write_manifest(manifest_path: str, host_exe: str, allowed_ids) -> None:
    manifest = {
        "name": HOST_NAME,
        "description": "THU Auto Login native messaging host",
        "path": host_exe,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{i}/" for i in allowed_ids],
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")


def copy_self_as_uninstaller() -> str:
    """Copy the running exe into the install directory so it can act as the
    uninstaller. Returns '' when running from source (nothing to copy)."""
    target = os.path.join(APPDATA_DIR, SETUP_EXE_NAME)
    if not getattr(sys, "frozen", False):
        return ""
    try:
        if os.path.abspath(sys.executable) == os.path.abspath(target):
            return target
        os.makedirs(APPDATA_DIR, exist_ok=True)
        shutil.copyfile(sys.executable, target)
        return target
    except Exception:
        return ""


def install_host(extension_ids, progress=None) -> dict:
    """Phase B. `extension_ids` is the list of origins to allow — normally just
    the one confirmed ID."""
    def report(message):
        say(message)
        if progress:
            progress(message)

    if not _is_windows():
        raise InstallError("此安装程序仅适用于 Windows")

    allowed = [i for i in extension_ids if ID_PATTERN.match(i or "")]
    if not allowed:
        raise InstallError("没有可用的扩展 ID")

    host_exe_src, _ext = locate_payload()
    if not host_exe_src:
        raise InstallError("安装包内缺少 thu-autologin-host.exe（构建不完整）")

    os.makedirs(APPDATA_DIR, exist_ok=True)

    host_exe_dst = os.path.join(APPDATA_DIR, HOST_EXE_NAME)
    manifest_dst = os.path.join(APPDATA_DIR, MANIFEST_NAME)

    shutil.copyfile(host_exe_src, host_exe_dst)
    report(f"已安装原生主机: {host_exe_dst}")

    write_manifest(manifest_dst, host_exe_dst, allowed)
    report(f"已写入清单: {manifest_dst}")

    register_native_host(manifest_dst)
    report(f"已注册: HKCU\\{NM_REGISTRY_SUBKEY}")

    uninstall_exe = copy_self_as_uninstaller()
    if uninstall_exe:
        try:
            register_uninstall_entry(uninstall_exe)
            report("已注册卸载项（设置 → 应用）")
        except Exception as exc:
            report(f"警告: 无法注册卸载项: {exc}")

    return {
        "host_exe": host_exe_dst,
        "manifest": manifest_dst,
        "allowed_origins": allowed,
    }


def verify_host(timeout: int = 60):
    """Run the installed host's --ping and return its parsed output, or None."""
    host_exe = os.path.join(APPDATA_DIR, HOST_EXE_NAME)
    if not os.path.isfile(host_exe):
        return None
    try:
        result = subprocess.run(
            [host_exe, "--ping"], capture_output=True, text=True, timeout=timeout
        )
        return json.loads(result.stdout or "{}")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def uninstall(progress=None) -> dict:
    def report(message):
        say(message)
        if progress:
            progress(message)

    if not _is_windows():
        raise InstallError("此卸载程序仅适用于 Windows")

    removed = {"registry": False, "uninstall_entry": False, "files": []}
    removed["registry"] = unregister_native_host()
    report("已删除原生主机注册表项" if removed["registry"] else "原生主机注册表项不存在，跳过")

    removed["uninstall_entry"] = unregister_uninstall_entry()
    if removed["uninstall_entry"]:
        report("已删除“应用和功能”条目")

    # The running uninstaller cannot delete itself; it is left behind and gets
    # overwritten on reinstall.
    keep = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else ""

    ext_dst = installed_extension_dir()
    if os.path.isdir(ext_dst):
        shutil.rmtree(ext_dst, ignore_errors=True)
        removed["files"].append(ext_dst)
        report(f"已删除 {ext_dst}")

    for name in (HOST_EXE_NAME, MANIFEST_NAME):
        path = os.path.join(APPDATA_DIR, name)
        try:
            os.remove(path)
            removed["files"].append(path)
            report(f"已删除 {path}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            report(f"警告: 无法删除 {path}: {exc}")

    if os.path.isdir(APPDATA_DIR):
        leftovers = []
        for entry in os.listdir(APPDATA_DIR):
            full = os.path.join(APPDATA_DIR, entry)
            if keep and os.path.abspath(full) == keep:
                leftovers.append(entry)
                continue
            try:
                if os.path.isdir(full):
                    shutil.rmtree(full, ignore_errors=True)
                else:
                    os.remove(full)
                removed["files"].append(full)
            except OSError as exc:
                report(f"警告: 无法删除 {full}: {exc}")
                leftovers.append(entry)
        if not leftovers:
            try:
                os.rmdir(APPDATA_DIR)
            except OSError:
                pass

    return removed
