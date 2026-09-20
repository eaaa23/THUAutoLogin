#!/usr/bin/env python3
"""Remove the THU Auto Login native messaging host from Windows Chrome.

Removes the registry key, the manifest, the launcher and the installed copy of
the host script. Pass --purge to also delete the log file.

The unpacked extension itself is left alone: remove it from chrome://extensions.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

HOST_NAME = "com.thu.autologin.host"
APP_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "THUAutoLogin"
)
REGISTRY_SUBKEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"

FILES = ("thu_autologin_host.py", "thu-autologin-host.cmd", f"{HOST_NAME}.json")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Uninstall the THU Auto Login native messaging host")
    parser.add_argument("--purge", action="store_true", help="also delete host.log")
    args = parser.parse_args(argv)

    if os.name != "nt":
        print("this uninstaller is Windows-only; on macOS use ../MacOS/uninstall.sh", file=sys.stderr)
        return 1

    import winreg

    print("==> 删除注册表项")
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_SUBKEY)
        print(f"    已删除 HKCU\\{REGISTRY_SUBKEY}")
    except FileNotFoundError:
        print("    注册表项不存在，跳过")
    except OSError as exc:
        print(f"    警告: 删除失败: {exc}", file=sys.stderr)

    print("==> 删除已安装的文件")
    for name in FILES:
        path = os.path.join(APP_DIR, name)
        try:
            os.remove(path)
            print(f"    已删除 {path}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"    警告: 无法删除 {path}: {exc}", file=sys.stderr)

    log_path = os.path.join(APP_DIR, "host.log")
    if args.purge:
        try:
            os.remove(log_path)
            print(f"    已删除 {log_path}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"    警告: 无法删除 {log_path}: {exc}", file=sys.stderr)
    elif os.path.exists(log_path):
        print(f"    保留日志 {log_path} (--purge 可一并删除)")

    # Only remove the directory if nothing of ours is left in it.
    try:
        leftovers = os.listdir(APP_DIR)
    except FileNotFoundError:
        leftovers = None
    if leftovers == []:
        try:
            os.rmdir(APP_DIR)
            print(f"    已删除空目录 {APP_DIR}")
        except OSError:
            pass
    elif leftovers:
        print(f"    保留 {APP_DIR}（仍有其它文件）")

    print()
    print("-" * 68)
    print("已卸载。建议重启 Chrome。")
    print("扩展本身请在 chrome://extensions 中手动移除。")
    print("-" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
