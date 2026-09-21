#!/usr/bin/env python3
"""THU Auto Login — Windows native messaging host.

Speaks Chrome's native-messaging protocol on stdin/stdout (4-byte little-endian
length prefix + UTF-8 JSON) and, on request, injects a real keystroke into Chrome.

Why a keystroke at all: Chrome autofills the saved credentials into the login
form but refuses to expose the values to JavaScript until the page has seen a
genuine user event. No extension API can forge one (that is what the old
chrome.debugger/CDP implementation did, and why it raised the debug banner).
``keybd_event`` injects at the OS input layer, so the event is indistinguishable
from real hardware.

Unlike macOS this needs only ONE helper process, because Windows has no
equivalent of the TCC "responsible process" problem: a process spawned by Chrome
can post input without any per-binary Accessibility grant. It does mean the host
must run in the user's interactive session, which it does — Chrome spawns it
there.

Design mirrors the macOS agent deliberately, so the shared extension needs no
platform-specific code:

  * same host name  (com.thu.autologin.host)
  * same request    ({"type": "unlock", "strategy": ...})
  * same response   ({"ok": true, ...})
  * same strategy whitelist (never an arbitrary key or arbitrary text)
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time

HOST_NAME = "com.thu.autologin.host"

# Top-level window class shared by Chrome, Edge and other Chromium browsers.
# Edge is a first-class target: identical autofill, it just needs the Tab
# pressed twice (handled in the extension, not here).
BROWSER_WINDOW_CLASS = "Chrome_WidgetWin_1"

# Virtual key codes. This whitelist is the whole keyboard surface this host can
# ever emit: it can never type a character, so it cannot be abused to inject
# credentials or commands.
STRATEGIES = {
    "shift": 0x10,   # VK_SHIFT
    "tab": 0x09,     # VK_TAB
    "escape": 0x1B,  # VK_ESCAPE
    "enter": 0x0D,   # VK_RETURN
    "f15": 0x7E,     # VK_F15
    "f16": 0x7F,     # VK_F16
}

MAX_MESSAGE_BYTES = 64 * 1024 * 1024
KEY_HOLD_SECONDS = 0.015

# ---------------------------------------------------------------------------
# Optional pywin32 import
#
# Imported defensively so that a missing dependency produces a readable error
# over the native-messaging channel instead of a silent crash the user cannot
# see (Chrome just reports "native host has exited").
# ---------------------------------------------------------------------------

try:
    import win32api
    import win32con
    import win32gui
    import win32process

    HAVE_PYWIN32 = True
    IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - depends on the machine
    HAVE_PYWIN32 = False
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Logging (opt-in; there is no console on Windows, so a file is the only way
# to see what the host did)
# ---------------------------------------------------------------------------

LOG_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "THUAutoLogin",
    "host.log",
)


def _log(message: str) -> None:
    if os.environ.get("THU_AUTOLOGIN_DEBUG") != "1":
        return
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Native messaging framing
# ---------------------------------------------------------------------------


def set_binary_stdio() -> None:
    """Stop the C runtime from translating \\n to \\r\\n in the message stream."""
    if os.name != "nt":
        return
    try:
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    except Exception as exc:
        _log(f"set_binary_stdio failed: {exc!r}")


def read_message(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    if length == 0 or length > MAX_MESSAGE_BYTES:
        return None
    body = stream.read(length)
    if len(body) < length:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception as exc:
        _log(f"bad json: {exc!r}")
        return None


def write_message(stream, payload) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(body)))
    stream.write(body)
    stream.flush()


# ---------------------------------------------------------------------------
# Finding Chrome
# ---------------------------------------------------------------------------


def top_level_browser_windows():
    """Every visible top-level window using Chrome's window class."""
    found = []

    def callback(hwnd, _param):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == BROWSER_WINDOW_CLASS:
                found.append((hwnd, win32gui.GetWindowText(hwnd)))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)
    return found


def window_pid(hwnd):
    try:
        _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None


def process_name(pid):
    """Best-effort image name; never fatal, it is only used for diagnostics."""
    if pid is None:
        return None
    handle = None
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            # QueryFullProcessImageName exists on pywin32 >= 227.
            path = win32process.QueryFullProcessImageName(handle, 0)
        except AttributeError:
            path = win32process.GetModuleFileNameEx(handle, 0)
        return os.path.basename(path)
    except Exception:
        return None
    finally:
        if handle is not None:
            try:
                win32api.CloseHandle(handle)
            except Exception:
                pass


def is_foreground_window(hwnd) -> bool:
    try:
        foreground = win32gui.GetForegroundWindow()
    except Exception:
        return False
    if not foreground:
        return False
    if foreground == hwnd:
        return True
    # The focused widget is often a child (the render area); compare roots.
    try:
        return win32gui.GetAncestor(foreground, win32con.GA_ROOT) == hwnd
    except Exception:
        return False


def find_browser_window():
    """Return (hwnd, title, is_foreground) for the Chromium browser window to target.

    Prefers the foreground window: the extension only asks while the page really
    has focus, so the window the user is looking at is the correct target. That
    also disambiguates Chrome from other Chromium apps sharing the same window
    class, without needing a process-name lookup.
    """
    candidates = top_level_browser_windows()
    if not candidates:
        return None

    for hwnd, title in candidates:
        if is_foreground_window(hwnd):
            return (hwnd, title, True)

    # Nothing in the foreground: fall back to a titled window, which excludes
    # Chrome's hidden message/parent windows.
    for hwnd, title in candidates:
        if title.strip():
            return (hwnd, title, False)

    hwnd, title = candidates[0]
    return (hwnd, title, False)


def activate_window(hwnd) -> None:
    """Best effort. Windows only lets the foreground process steal focus, so
    this frequently does nothing when called from a background helper."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:
        _log(f"SetForegroundWindow failed: {exc!r}")
    time.sleep(0.15)


# ---------------------------------------------------------------------------
# Key injection
# ---------------------------------------------------------------------------


def send_key(vk: int) -> None:
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(KEY_HOLD_SECONDS)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


# ---------------------------------------------------------------------------
# Request handling
# ---------------------------------------------------------------------------


def browser_summary():
    target = find_browser_window()
    if target is None:
        return None
    hwnd, title, foreground = target
    pid = window_pid(hwnd)
    return {
        "hwnd": hwnd,
        "title": title,
        "pid": pid,
        "process": process_name(pid),
        "foreground": foreground,
    }


def ping_response():
    payload = {
        "ok": True,
        "pong": True,
        "platform": "win32",
        "pywin32": HAVE_PYWIN32,
        "strategies": sorted(STRATEGIES),
        "browser": None,
        "windows": [],
    }
    if not HAVE_PYWIN32:
        payload["detail"] = IMPORT_ERROR
        payload["error"] = "pywin32-missing"
        return payload

    payload["windows"] = [
        {
            "hwnd": hwnd,
            "title": title,
            "pid": window_pid(hwnd),
            "foreground": is_foreground_window(hwnd),
        }
        for hwnd, title in top_level_browser_windows()
    ]
    payload["browser"] = browser_summary()
    return payload


def perform_unlock(request):
    if not HAVE_PYWIN32:
        return {"ok": False, "error": "pywin32-missing", "detail": IMPORT_ERROR}

    strategy = request.get("strategy", "tab")
    vk = STRATEGIES.get(strategy)
    if vk is None:
        return {"ok": False, "error": "unknown-strategy"}

    target = find_browser_window()
    if target is None:
        return {"ok": False, "error": "browser-not-running"}

    hwnd, _title, foreground = target

    # keybd_event injects into whatever has focus, so refuse unless Chrome
    # really is in front. The extension already gates on document.hasFocus();
    # this is the second line of defence against a keystroke landing in another
    # application if focus moved in between.
    if not foreground:
        if not request.get("activate"):
            return {"ok": False, "error": "browser-not-foreground", "hwnd": hwnd}
        activate_window(hwnd)
        if not is_foreground_window(hwnd):
            return {"ok": False, "error": "browser-not-foreground", "hwnd": hwnd}

    send_key(vk)
    _log(f"unlock strategy={strategy} vk=0x{vk:02X} hwnd={hwnd}")
    return {
        "ok": True,
        "strategy": strategy,
        "hwnd": hwnd,
        "platform": "win32",
    }


def handle_request(request):
    if not isinstance(request, dict):
        return {"ok": False, "error": "bad-request"}

    kind = request.get("type", "")
    if kind == "ping":
        return ping_response()
    if kind == "unlock":
        return perform_unlock(request)
    return {"ok": False, "error": "unknown-type"}


# ---------------------------------------------------------------------------
# Command line modes (for manual testing; the extension never uses these)
# ---------------------------------------------------------------------------


def cli_selftest() -> int:
    print("== THU Auto Login Windows host self-test ==")
    if not HAVE_PYWIN32:
        print(f"pywin32 未安装或导入失败: {IMPORT_ERROR}")
        print("请先运行:  pip install pywin32")
        return 3

    windows = top_level_browser_windows()
    print(f"Chrome 顶层窗口数: {len(windows)}")
    for hwnd, title in windows:
        pid = window_pid(hwnd)
        print(f"  hwnd={hwnd} pid={pid} fg={is_foreground_window(hwnd)} title={title!r}")

    target = find_browser_window()
    if target is None:
        print("未找到 Chromium 浏览器窗口")
        return 2

    hwnd, title, foreground = target
    print(f"目标窗口: hwnd={hwnd} title={title!r} 前台={foreground}")
    if not foreground:
        print("注意: 目标窗口不在前台，keybd_event 会打到当前前台窗口。")
        print("请先把 Chrome 切到前台再测试。")
        return 4

    print("3 秒后向 Chrome 发送一次 Tab，请让登录页保持前台…")
    time.sleep(3)
    send_key(STRATEGIES["tab"])
    print("已发送 Tab")
    return 0


def cli_once(strategy: str) -> int:
    if not HAVE_PYWIN32:
        print(f"pywin32 不可用: {IMPORT_ERROR}")
        return 3
    vk = STRATEGIES.get(strategy)
    if vk is None:
        print(f"未知按键策略: {strategy}")
        print("可选: " + ", ".join(sorted(STRATEGIES)))
        return 1
    target = find_browser_window()
    if target is None:
        print("未找到 Chromium 浏览器窗口")
        return 2
    hwnd, _title, foreground = target
    if not foreground:
        print("目标窗口不在前台，已拒绝发送（避免按键落到别的程序）")
        return 4
    send_key(vk)
    print(f"strategy={strategy} vk=0x{vk:02X} hwnd={hwnd} ok")
    return 0


def serve() -> int:
    set_binary_stdio()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    _log("host started")

    while True:
        try:
            request = read_message(stdin)
        except Exception as exc:
            _log(f"read failed: {exc!r}")
            break
        if request is None:
            break  # Chrome closed the port

        try:
            response = handle_request(request)
        except Exception as exc:
            _log(f"handler crashed: {exc!r}")
            response = {"ok": False, "error": "handler-crash", "detail": repr(exc)}

        if isinstance(request, dict) and "requestId" in request:
            response["requestId"] = request["requestId"]

        try:
            write_message(stdout, response)
        except Exception as exc:
            _log(f"write failed: {exc!r}")
            break

    _log("host exiting")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # allow_abbrev=False so no Chrome-supplied argument can ever be mistaken for
    # an abbreviation of one of ours (e.g. "--p" matching --ping/--parent-window).
    parser = argparse.ArgumentParser(
        description="THU Auto Login Windows native messaging host",
        allow_abbrev=False,
    )
    parser.add_argument("--selftest", action="store_true", help="检查环境并向前台 Chrome 发送一次 Tab")
    parser.add_argument("--ping", action="store_true", help="打印诊断信息 (JSON)")
    parser.add_argument("--once", metavar="STRATEGY", help=f"发送一次按键，可选: {', '.join(sorted(STRATEGIES))}")

    # Chrome appends its own arguments when it launches a native messaging host:
    #   <origin>            e.g. chrome-extension://<id>/
    #   --parent-window=0   Windows only: the HWND to parent any dialog to
    # They are declared here purely so they can never trip argument parsing and
    # kill the host before it has served a single request. Previously they hit
    # argparse's "unrecognized arguments" path and the host exited 2, which
    # Chrome reports only as "native host has exited".
    parser.add_argument(
        "origin", nargs="?", default=None,
        help="Chrome passes the calling extension origin here (ignored)",
    )
    parser.add_argument(
        "--parent-window", default=None,
        help="Chrome passes the parent window handle here (ignored)",
    )
    return parser


def parse_arguments(argv=None):
    """Parse argv without ever rejecting an argument.

    parse_known_args -- deliberately not parse_args -- is what keeps the host
    alive: Chrome adds arguments of its own, and an unrecognised one must not be
    fatal. Returns (args, extras).
    """
    return build_parser().parse_known_args(argv)


def main(argv=None) -> int:
    args, extras = parse_arguments(argv)

    _log(
        f"launched argv={sys.argv[1:]!r} origin={args.origin!r} "
        f"parent_window={args.parent_window!r} extras={extras!r}"
    )

    if args.selftest:
        return cli_selftest()
    if args.ping:
        print(json.dumps(ping_response(), ensure_ascii=False, indent=2))
        return 0
    if args.once:
        return cli_once(args.once)
    return serve()


if __name__ == "__main__":
    sys.exit(main())
