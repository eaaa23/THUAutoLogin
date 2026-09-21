#!/usr/bin/env python3
"""Build the Windows all-in-one installer.

Produces two artifacts, both single files:

  build/thu-autologin-host.exe     the native messaging host
  build/THUAutoLogin-Setup.exe     the installer, with the host and the Chrome
                                   extension embedded

The end user runs only the second one, and needs **no Python, no pywin32 and no
compiler** — PyInstaller bundles the interpreter and pywin32 into the exes.

Run this on Windows (PyInstaller cannot cross-compile):

    python -m pip install pywin32 pyinstaller
    python WindowsInstaller\\build.py

Options:
    --host-only    only rebuild the host exe
    --setup-only   only rebuild the setup exe (reuses the existing host exe)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))

HOST_SOURCE = os.path.join(REPO, "Windows", "native-host", "thu_autologin_host.py")
EXT_SOURCE = os.path.join(REPO, "THUAutoLogin")
INSTALLER_SOURCE = os.path.join(HERE, "installer.py")

BUILD_DIR = os.path.join(HERE, "build")
DIST_DIR = os.path.join(BUILD_DIR, "dist")
WORK_DIR = os.path.join(BUILD_DIR, "work")
SPEC_DIR = os.path.join(BUILD_DIR, "spec")

HOST_EXE = "thu-autologin-host"
SETUP_EXE = "THUAutoLogin-Setup"


def say(message: str = "") -> None:
    print(message, flush=True)


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def check_environment() -> None:
    if os.name != "nt":
        die(
            "PyInstaller 无法交叉编译：请在 Windows 上运行本脚本。\n"
            "       macOS 安装包请改用 MacInstaller/build-pkg.sh。"
        )

    try:
        import PyInstaller  # noqa: F401
    except Exception:
        die("未安装 PyInstaller。请先运行:  python -m pip install pyinstaller")

    try:
        import win32api  # noqa: F401
        import win32gui  # noqa: F401
    except Exception as exc:
        die(f"未安装 pywin32（{exc}）。请先运行:  python -m pip install pywin32")

    for path in (HOST_SOURCE, INSTALLER_SOURCE):
        if not os.path.isfile(path):
            die(f"缺少源文件: {path}")
    if not os.path.isdir(EXT_SOURCE):
        die(f"缺少扩展目录: {EXT_SOURCE}")


def run_pyinstaller(name: str, source: str, extra_args, console: bool = True) -> str:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        # The host must be --console: it speaks the native messaging protocol over
        # stdio, and --noconsole would leave it with no usable stdin/stdout.
        # The installer is the opposite: a GUI build, so no console window flashes
        # up when it is double-clicked.
        "--console" if console else "--noconsole",
        "--noconfirm",
        "--clean",
        "--name", name,
        "--distpath", DIST_DIR,
        "--workpath", WORK_DIR,
        "--specpath", SPEC_DIR,
        *extra_args,
        source,
    ]
    say("    " + " ".join(args[3:]))
    result = subprocess.run(args)
    if result.returncode != 0:
        die(f"PyInstaller 构建失败 ({name})")

    suffix = ".exe" if os.name == "nt" else ""
    out = os.path.join(DIST_DIR, name + suffix)
    if not os.path.isfile(out):
        die(f"未生成预期产物: {out}")
    return out


def build_host() -> str:
    say("==> 构建原生主机 (thu-autologin-host.exe)")
    # The pywin32 imports sit inside a try/except so a missing dependency yields
    # a readable error instead of a silent crash; PyInstaller still needs to be
    # told about them explicitly.
    return run_pyinstaller(
        HOST_EXE, HOST_SOURCE,
        [
            "--hidden-import", "win32api",
            "--hidden-import", "win32gui",
            "--hidden-import", "win32con",
            "--hidden-import", "win32process",
        ],
    )


def build_setup(host_exe: str) -> str:
    say("")
    say("==> 构建安装程序 (THUAutoLogin-Setup.exe)")
    # On Windows the --add-data separator is ';'. The extension is embedded whole
    # so the installer can lay it down for the user to load unpacked.
    sep = ";" if os.name == "nt" else ":"
    return run_pyinstaller(
        SETUP_EXE, INSTALLER_SOURCE,
        [
            # installer.py imports installer_core, so the directory must be on
            # the analysis path.
            "--paths", HERE,
            "--add-data", f"{host_exe}{sep}.",
            "--add-data", f"{EXT_SOURCE}{sep}extension",
        ],
        console=False,
    )


def human(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024.0
    return f"{size:.1f} MB"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the Windows installer", allow_abbrev=False)
    parser.add_argument("--host-only", action="store_true")
    parser.add_argument("--setup-only", action="store_true")
    args = parser.parse_args(argv)

    check_environment()

    for path in (DIST_DIR, WORK_DIR, SPEC_DIR):
        os.makedirs(path, exist_ok=True)

    host_exe = os.path.join(DIST_DIR, HOST_EXE + ".exe")
    setup_exe = os.path.join(DIST_DIR, SETUP_EXE + ".exe")

    if not args.setup_only:
        host_exe = build_host()
    elif not os.path.isfile(host_exe):
        die(f"--setup-only 需要已存在的 {host_exe}")

    if not args.host_only:
        setup_exe = build_setup(host_exe)

    say("")
    say("==> 产物")
    for path in (host_exe, setup_exe):
        if os.path.isfile(path):
            say(f"    {human(os.path.getsize(path)):>10}  {path}")

    say("")
    say("把 THUAutoLogin-Setup.exe 单独发给用户即可，目标机器无需 Python / pywin32 / 编译器。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
