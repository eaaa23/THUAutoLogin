#!/usr/bin/env python3
"""Install the THU Auto Login native messaging host for Windows Chrome.

Steps performed:

  1. copy the host script into %LOCALAPPDATA%\\THUAutoLogin\\
  2. write a .cmd launcher next to it (a Chrome manifest "path" must be an
     executable, and cmd.exe forwards the binary stdio pipes untouched)
  3. write the native messaging manifest JSON
  4. register  HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\<host>
     pointing at that manifest

Everything is per-user, so no administrator rights are needed.

The extension ID cannot be assumed: Chrome derives an unpacked extension's ID
from the SHA-256 of its absolute path, so the same folder yields a different ID
on Windows than on macOS. This script therefore reads the ID back out of
Chrome's own preferences and only falls back to computing it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

HOST_NAME = "com.thu.autologin.host"
APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "THUAutoLogin"
)
HOST_SCRIPT_NAME = "thu_autologin_host.py"
LAUNCHER_NAME = "thu-autologin-host.cmd"
MANIFEST_NAME = f"{HOST_NAME}.json"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXTENSION_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "THUAutoLogin"))

REGISTRY_SUBKEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"

# Manifest::LOCATION_UNPACKED in Chrome's preferences.
LOCATION_UNPACKED = 4


def say(message: str = "") -> None:
    print(message)


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Extension ID
# ---------------------------------------------------------------------------


def id_from_path(path: str) -> str:
    """Chrome's unpacked-extension ID derivation: first 32 hex digits of the
    SHA-256 of the UTF-8 absolute path, with 0-9a-f mapped onto a-p."""
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def detect_extension_id(extension_dir: str):
    """Read the ID out of Chrome's preferences for the unpacked extension whose
    recorded path matches `extension_dir`.

    More reliable than computing it: this is Chrome's own answer, so it needs no
    assumptions about path normalisation or encoding.
    """
    user_data = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"
    )
    if not os.path.isdir(user_data):
        return None

    target = os.path.normcase(os.path.normpath(os.path.abspath(extension_dir)))

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
# Files
# ---------------------------------------------------------------------------


def write_launcher(launcher_path: str, script_path: str) -> None:
    """Write the .cmd Chrome will execute.

    `@echo off` is mandatory: without it cmd.exe echoes the command line onto
    stdout and corrupts the length-prefixed message stream.
    """
    content = (
        "@echo off\r\n"
        f'"{sys.executable}" "{script_path}" %*\r\n'
    )
    # cmd.exe reads batch files using the ANSI/OEM code page, so encode with
    # mbcs when the paths contain non-ASCII characters.
    for encoding in ("mbcs", "utf-8"):
        try:
            with open(launcher_path, "w", encoding=encoding, newline="") as handle:
                handle.write(content)
            return
        except (LookupError, UnicodeEncodeError):
            continue
    die("could not write the launcher; the install path probably has unsupported characters")


def write_manifest(manifest_path: str, launcher_path: str, extension_id: str) -> None:
    manifest = {
        "name": HOST_NAME,
        "description": "THU Auto Login native messaging host",
        "path": launcher_path,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def register(manifest_path: str) -> None:
    import winreg

    key = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY, 0, winreg.KEY_WRITE
    )
    try:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, manifest_path)
    finally:
        winreg.CloseKey(key)


def unregister() -> bool:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"warning: could not delete the registry key: {exc}", file=sys.stderr)
        return False


def read_registered_path():
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY)
    except FileNotFoundError:
        return None
    try:
        value, _kind = winreg.QueryValueEx(key, None)
        return value
    finally:
        winreg.CloseKey(key)


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def check_pywin32() -> bool:
    try:
        import win32api  # noqa: F401
        import win32gui  # noqa: F401

        return True
    except Exception as exc:
        print(f"pywin32 不可用: {exc}", file=sys.stderr)
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install the THU Auto Login native messaging host")
    parser.add_argument("--extension-dir", default=DEFAULT_EXTENSION_DIR,
                        help="path to the unpacked extension (default: %(default)s)")
    parser.add_argument("--extension-id", default=None,
                        help="override the detected extension ID")
    args = parser.parse_args(argv)

    if os.name != "nt":
        die("this installer is Windows-only; on macOS use ../MacOS/install.sh")

    extension_dir = os.path.abspath(args.extension_dir)
    if not os.path.isdir(extension_dir):
        die(f"extension directory not found: {extension_dir}")

    say(f"扩展目录 : {extension_dir}")
    say(f"Python   : {sys.executable}")

    # -- dependency --------------------------------------------------------
    if not check_pywin32():
        say("")
        say("请先安装依赖:")
        say(f'  "{sys.executable}" -m pip install pywin32')
        return 1

    # Warn about a virtualenv: the launcher pins sys.executable, so deleting the
    # venv later would silently break the host.
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        say("")
        say("注意: 当前 Python 来自虚拟环境。启动器会固定使用该解释器，")
        say("      若之后删除这个虚拟环境，原生主机将失效。建议改用系统 Python 安装。")

    # -- extension id ------------------------------------------------------
    if args.extension_id:
        extension_id = args.extension_id
        source = "命令行指定"
    else:
        extension_id = detect_extension_id(extension_dir)
        source = "从 Chrome 配置中读取"
        if not extension_id:
            extension_id = id_from_path(extension_dir)
            source = "由路径推导（回退方案）"

    say(f"扩展 ID  : {extension_id}  [{source}]")
    if source.startswith("由路径推导"):
        say("")
        say("  警告: 未能从 Chrome 配置中读到该未打包扩展。")
        say("        请先在 chrome://extensions 加载此扩展并重载一次，然后重新运行本脚本；")
        say("        或使用 --extension-id <id> 手动指定。")

    # -- files -------------------------------------------------------------
    say("")
    say("==> 安装原生主机")
    os.makedirs(APP_DIR, exist_ok=True)

    script_dest = os.path.join(APP_DIR, HOST_SCRIPT_NAME)
    launcher_dest = os.path.join(APP_DIR, LAUNCHER_NAME)
    manifest_dest = os.path.join(APP_DIR, MANIFEST_NAME)

    shutil.copyfile(os.path.join(HERE, HOST_SCRIPT_NAME), script_dest)
    write_launcher(launcher_dest, script_dest)
    write_manifest(manifest_dest, launcher_dest, extension_id)

    say(f"    脚本     : {script_dest}")
    say(f"    启动器   : {launcher_dest}")
    say(f"    清单     : {manifest_dest}")

    # -- registry ----------------------------------------------------------
    say("")
    say("==> 注册到 Chrome (HKCU，无需管理员权限)")
    try:
        register(manifest_dest)
    except Exception as exc:
        die(f"写入注册表失败: {exc}")

    registered = read_registered_path()
    if registered == manifest_dest:
        say(f"    已验证   : HKCU\\{REGISTRY_SUBKEY}")
    else:
        say(f"    警告     : 注册表回读不一致 (got {registered!r})")

    # -- quick smoke test --------------------------------------------------
    say("")
    say("==> 自检")
    try:
        result = subprocess.run(
            [sys.executable, script_dest, "--ping"],
            capture_output=True, text=True, timeout=30,
        )
        payload = json.loads(result.stdout or "{}")
        chrome = payload.get("chrome")
        if chrome:
            say(f"    找到 Chrome 窗口: hwnd={chrome.get('hwnd')} pid={chrome.get('pid')} "
                f"前台={chrome.get('foreground')}")
        else:
            say("    未找到 Chrome 窗口（Chrome 没在运行？稍后再试即可）")
    except Exception as exc:
        say(f"    自检未通过: {exc}")

    say("")
    say("-" * 68)
    say("完成。接下来:")
    say("")
    say("1. 重启 Chrome（让它重新读取原生消息主机注册表）")
    say("2. 打开 chrome://extensions ，确认 THU Auto Login 已加载并启用")
    say("3. 打开 id.tsinghua.edu.cn 登录页，保持 Chrome 在前台")
    say("")
    say("排查问题:")
    say(f'  设置环境变量 THU_AUTOLOGIN_DEBUG=1 后重试，日志位于')
    say(f"  {os.path.join(APP_DIR, 'host.log')}")
    say("")
    say("卸载:  python uninstall.py")
    say("-" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
