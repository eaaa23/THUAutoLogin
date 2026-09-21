#!/usr/bin/env python3
"""THU Auto Login — Windows all-in-one installer.

This file is frozen by PyInstaller into a single ``THUAutoLogin-Setup.exe`` that
carries:

  * ``thu-autologin-host.exe``  — the native messaging host, itself a frozen
    PyInstaller build, so the target machine needs **no Python and no pywin32**
  * ``extension/``              — the Chrome extension, ready to load unpacked

Running it installs everything per-user (no administrator rights required):

  %LOCALAPPDATA%\\THUAutoLogin\\thu-autologin-host.exe     the host
  %LOCALAPPDATA%\\THUAutoLogin\\extension\\                the extension
  %LOCALAPPDATA%\\THUAutoLogin\\com.thu.autologin.host.json
  HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\<host>
  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\THUAutoLogin

Run the same exe with ``--uninstall`` to remove everything again.

Why there is no ``.cmd`` launcher any more: the older, script-based install had
to point Chrome's manifest at a ``.cmd`` that re-invoked ``python.exe``, which
meant the target machine needed Python and the launcher had to pin an
interpreter path. Shipping a compiled host removes both problems.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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

# Manifest::LOCATION_UNPACKED in Chrome's preferences.
LOCATION_UNPACKED = 4


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    """Platform seam. Isolated in one function so the installer can be driven
    end-to-end by tests on a non-Windows machine."""
    return os.name == "nt"


def say(message: str = "") -> None:
    print(message, flush=True)


def die(message: str) -> None:
    print(f"\n错误: {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def pause_if_interactive() -> None:
    """Keep the console window open when double-clicked from Explorer."""
    if os.environ.get("THU_AUTOLOGIN_NO_PAUSE") == "1":
        return
    try:
        input("\n按回车键关闭…")
    except (EOFError, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# Locating the bundled payload
# ---------------------------------------------------------------------------


def bundle_root() -> str:
    """Directory holding the bundled payload.

    Under PyInstaller onefile this is the temporary extraction directory; when
    running straight from the source tree it is the script's own directory.
    """
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def locate_payload():
    """Return (host_exe, extension_dir).

    The source-tree fallbacks let the installer be exercised without building
    the frozen exe first, which is how it is smoke-tested.
    """
    root = bundle_root()
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.normpath(os.path.join(here, ".."))

    host_candidates = [
        os.path.join(root, HOST_EXE_NAME),
        os.path.join(here, "build", HOST_EXE_NAME),
        os.path.join(here, "dist", HOST_EXE_NAME),
    ]
    ext_candidates = [
        os.path.join(root, EXT_DIR_NAME),
        os.path.join(repo, "THUAutoLogin"),
    ]

    host_exe = next((p for p in host_candidates if os.path.isfile(p)), None)
    ext_dir = next((p for p in ext_candidates if os.path.isdir(p)), None)
    return host_exe, ext_dir


# ---------------------------------------------------------------------------
# Extension ID
# ---------------------------------------------------------------------------


def id_from_bytes(path_bytes: bytes) -> str:
    """Chrome's unpacked-extension ID: first 32 hex digits of the SHA-256 of the
    absolute path, with 0-9a-f mapped onto a-p."""
    digest = hashlib.sha256(path_bytes).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def extension_id_candidates(ext_dir: str):
    """Every plausible ID for `ext_dir`, best guess first.

    Chrome computes the ID from the extension's absolute path, but for a path
    with non-ASCII characters the documentation does not say which byte encoding
    it hashes. Rather than guess, emit one ID per plausible encoding and allow
    them all — they all name the same folder, so this widens nothing meaningful.
    On an all-ASCII path (the common case) the encodings are byte-identical and
    this collapses to exactly one ID.

    UTF-16 is deliberately absent: Chromium hashes the UTF-8 wide-to-narrow
    conversion, so including UTF-16 would add a bogus origin to every install.
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


def detect_extension_id(ext_dir: str):
    """Read the authoritative ID out of Chrome's own preferences, if the
    extension has already been loaded there."""
    user_data = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"
    )
    if not os.path.isdir(user_data):
        return None

    target = os.path.normcase(os.path.normpath(os.path.abspath(ext_dir)))
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
                continue
            settings = (data.get("extensions") or {}).get("settings") or {}
            for extension_id, entry in settings.items():
                if not isinstance(entry, dict) or entry.get("location") != LOCATION_UNPACKED:
                    continue
                recorded = entry.get("path") or ""
                if not recorded:
                    continue
                candidate = recorded if os.path.isabs(recorded) else os.path.join(profile_dir, recorded)
                if os.path.normcase(os.path.normpath(candidate)) == target:
                    return extension_id
    return None


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
    except OSError as exc:
        print(f"  警告: 无法删除原生主机注册表项: {exc}", file=sys.stderr)
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
    except OSError as exc:
        print(f"  警告: 无法删除卸载项: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Install / uninstall
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


def self_copy_target() -> str:
    """Where to park a copy of this installer so it can serve as the uninstaller."""
    return os.path.join(APPDATA_DIR, SETUP_EXE_NAME)


def copy_self_as_uninstaller() -> str:
    """Copy the running exe into the install directory.

    When frozen, ``sys.executable`` is the setup exe itself. When running from
    source there is nothing to copy, so the caller falls back to removing files
    directly and the uninstall registry entry is skipped.
    """
    target = self_copy_target()
    if not getattr(sys, "frozen", False):
        return ""
    try:
        if os.path.abspath(sys.executable) == os.path.abspath(target):
            return target  # already running from the install dir
        os.makedirs(APPDATA_DIR, exist_ok=True)
        shutil.copyfile(sys.executable, target)
        return target
    except Exception as exc:
        print(f"  警告: 无法复制卸载程序: {exc}", file=sys.stderr)
        return ""


def do_install(silent: bool = False) -> int:
    if not _is_windows():
        die("此安装程序仅适用于 Windows")

    say(f"THU Auto Login {VERSION} 安装程序")
    say("=" * 60)

    host_exe_src, ext_src = locate_payload()
    if not host_exe_src:
        die("安装包内缺少 thu-autologin-host.exe（构建不完整）")
    if not ext_src:
        die("安装包内缺少 extension 目录（构建不完整）")

    say(f"  原生主机 : {host_exe_src}")
    say(f"  扩展     : {ext_src}")
    say("")

    # -- payload ------------------------------------------------------------
    say("==> 复制文件")
    os.makedirs(APPDATA_DIR, exist_ok=True)

    host_exe_dst = os.path.join(APPDATA_DIR, HOST_EXE_NAME)
    ext_dst = os.path.join(APPDATA_DIR, EXT_DIR_NAME)
    manifest_dst = os.path.join(APPDATA_DIR, MANIFEST_NAME)

    shutil.copyfile(host_exe_src, host_exe_dst)
    say(f"    {host_exe_dst}")

    if os.path.isdir(ext_dst):
        shutil.rmtree(ext_dst, ignore_errors=True)
    shutil.copytree(ext_src, ext_dst)
    say(f"    {ext_dst}")

    # -- extension id -------------------------------------------------------
    say("")
    say("==> 计算扩展 ID")
    detected = detect_extension_id(ext_dst)
    if detected:
        allowed_ids = [detected]
        say(f"    {detected}  [从 Chrome 配置读取]")
    else:
        allowed_ids = extension_id_candidates(ext_dst)
        say(f"    {allowed_ids[0]}  [由安装路径推导]")
        if len(allowed_ids) > 1:
            say(f"    同时允许 {len(allowed_ids)} 种路径编码变体（同一目录）")

    # -- manifest + registry ------------------------------------------------
    say("")
    say("==> 注册到 Chrome（HKCU，无需管理员权限）")
    write_manifest(manifest_dst, host_exe_dst, allowed_ids)
    say(f"    清单 : {manifest_dst}")
    try:
        register_native_host(manifest_dst)
        say(f"    注册表: HKCU\\{NM_REGISTRY_SUBKEY}")
    except Exception as exc:
        die(f"写入注册表失败: {exc}")

    # -- uninstaller --------------------------------------------------------
    uninstall_exe = copy_self_as_uninstaller()
    if uninstall_exe:
        try:
            register_uninstall_entry(uninstall_exe)
            say(f"    卸载 : 已注册到“应用和功能”（{uninstall_exe}）")
        except Exception as exc:
            print(f"  警告: 无法注册卸载项: {exc}", file=sys.stderr)

    # -- self test ----------------------------------------------------------
    say("")
    say("==> 自检")
    try:
        result = subprocess.run(
            [host_exe_dst, "--ping"], capture_output=True, text=True, timeout=60
        )
        payload = json.loads(result.stdout or "{}")
        if payload.get("pywin32") is False:
            say(f"    警告: 主机缺少 pywin32: {payload.get('detail')}")
        chrome = payload.get("chrome")
        if chrome:
            say(f"    找到 Chrome 窗口 hwnd={chrome.get('hwnd')} pid={chrome.get('pid')} "
                f"前台={chrome.get('foreground')}")
        else:
            say("    暂未找到 Chrome 窗口（Chrome 没在运行也可以稍后再试）")
    except Exception as exc:
        print(f"    自检未能完成: {exc}", file=sys.stderr)

    # -- next steps ---------------------------------------------------------
    say("")
    say("=" * 60)
    say("安装完成 —— 还需两步")
    say("")
    say("1. 载入 Chrome 扩展")
    say("   打开 chrome://extensions → 右上角开启“开发者模式”")
    say("   → “加载已解压的扩展程序” → 选择：")
    say(f"     {ext_dst}")
    say("   （也可以把该文件夹直接拖到 chrome://extensions 页面上）")
    say("")
    say("2. 重启 Chrome")
    say("   让它重新读取原生消息主机注册表。")
    say("")
    say("完成。打开 id.tsinghua.edu.cn 登录页并保持 Chrome 在前台即可。")
    say("")
    say("卸载：设置 → 应用 → 已安装的应用 → THU Auto Login")
    say(f"      或运行 \"{self_copy_target()}\" --uninstall")
    say("=" * 60)

    if not silent:
        try:
            os.startfile(ext_dst)  # noqa: S606 - Windows-only convenience
        except Exception:
            pass

    return 0


def do_uninstall(silent: bool = False) -> int:
    if not _is_windows():
        die("此卸载程序仅适用于 Windows")

    say(f"正在卸载 {DISPLAY_NAME}…")
    say("")

    say("==> 删除注册表项")
    if unregister_native_host():
        say(f"    已删除 HKCU\\{NM_REGISTRY_SUBKEY}")
    else:
        say("    原生主机注册表项不存在，跳过")

    if unregister_uninstall_entry():
        say("    已删除“应用和功能”条目")

    say("")
    say("==> 删除文件")
    # Remove everything except the running uninstaller, which Windows will not
    # let us delete while it is executing; it is removed on a best-effort basis
    # and the leftover is harmless (and gets overwritten on reinstall).
    keep = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else ""
    for name in (HOST_EXE_NAME, MANIFEST_NAME):
        path = os.path.join(APPDATA_DIR, name)
        try:
            os.remove(path)
            say(f"    已删除 {path}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"    警告: 无法删除 {path}: {exc}", file=sys.stderr)

    ext_dst = os.path.join(APPDATA_DIR, EXT_DIR_NAME)
    if os.path.isdir(ext_dst):
        shutil.rmtree(ext_dst, ignore_errors=True)
        say(f"    已删除 {ext_dst}")

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
                say(f"    已删除 {full}")
            except OSError as exc:
                print(f"    警告: 无法删除 {full}: {exc}", file=sys.stderr)
                leftovers.append(entry)
        if not leftovers:
            try:
                os.rmdir(APPDATA_DIR)
                say(f"    已删除空目录 {APPDATA_DIR}")
            except OSError:
                pass

    say("")
    say("=" * 60)
    say("卸载完成。还需手动做一件事：")
    say("")
    say("  在 chrome://extensions 里移除 THU Auto Login 扩展。")
    say("")
    say("建议随后重启 Chrome。")
    say("=" * 60)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=f"{DISPLAY_NAME} 安装程序", allow_abbrev=False
    )
    parser.add_argument("--uninstall", action="store_true", help="卸载")
    parser.add_argument("--silent", action="store_true", help="不打开文件夹、不等待回车")
    args = parser.parse_args(argv)

    try:
        if args.uninstall:
            return do_uninstall(silent=args.silent)
        return do_install(silent=args.silent)
    finally:
        if not args.silent:
            pause_if_interactive()


if __name__ == "__main__":
    sys.exit(main())
