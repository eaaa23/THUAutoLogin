#!/usr/bin/env python3
"""THU Auto Login — Windows installer (graphical).

Frozen by PyInstaller into a single ``THUAutoLogin-Setup.exe`` that carries the
compiled native host and the Chrome extension. The target machine needs no
Python, no pywin32 and no compiler.

The wizard exists to fix the recurring "wrong extension id in
com.thu.autologin.host.json" problem. The extension ID is never guessed: the
user loads the extension first, and the installer then reads the ID straight out
of the browser's own configuration (with manual entry as a fallback). See
``installer_core`` for the two-phase install that makes this ordering possible.

  Step 1  load the extension   — extract it, show the folder, wait
  Step 2  confirm the ID       — auto-read from Chrome/Edge, or type it
  Step 3  done                 — summary and next steps

Command line (used by the "Apps & features" entry and for automation):

  THUAutoLogin-Setup.exe                      graphical install
  THUAutoLogin-Setup.exe --silent             unattended install
  THUAutoLogin-Setup.exe --uninstall          graphical uninstall
  THUAutoLogin-Setup.exe --uninstall --silent unattended uninstall
"""

from __future__ import annotations

import argparse
import os
import sys

import installer_core as core

CHROME_URL = "chrome://extensions"
EDGE_URL = "edge://extensions"


# ---------------------------------------------------------------------------
# Graphical installer
# ---------------------------------------------------------------------------


def build_gui():
    """Import tkinter lazily so the module stays importable without a display."""
    import tkinter as tk
    from tkinter import ttk

    return tk, ttk


def run_gui() -> int:
    tk, ttk = build_gui()
    from tkinter import messagebox

    class InstallerApp(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"THU Auto Login {core.VERSION} 安装程序")
            self.resizable(False, False)
            # Windows ships this font; Tk substitutes a sane default elsewhere.
            self.option_add("*Font", "{Microsoft YaHei UI} 9")

            self.ext_dir = ""
            self.install_result = None
            self.ping_result = None

            self._prepare_payload()
            self._build_widgets()
            self._center(700, 500)
            self.show_step(0)

        # -- helpers -------------------------------------------------------

        def _center(self, width, height):
            self.update_idletasks()
            x = max(0, (self.winfo_screenwidth() - width) // 2)
            y = max(0, (self.winfo_screenheight() - height) // 3)
            self.geometry(f"{width}x{height}+{x}+{y}")

        def _prepare_payload(self):
            """Phase A: the extension must be on disk before it can be loaded."""
            try:
                self.ext_dir = core.extract_extension()
            except core.InstallError as exc:
                messagebox.showerror("无法解压扩展", str(exc))
                raise SystemExit(1)

        def _copy(self, text, label):
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_var.set(f"已复制 {label}，请粘贴到浏览器地址栏")

        def _labelled(self, parent, text, wraplength=650, **kw):
            return ttk.Label(parent, text=text, wraplength=wraplength, justify="left", **kw)

        # -- layout --------------------------------------------------------

        def _build_widgets(self):
            self.step_var = tk.StringVar()
            ttk.Label(self, textvariable=self.step_var, font=("", 14, "bold")).pack(
                anchor="w", padx=16, pady=(14, 0)
            )

            self.status_var = tk.StringVar(value="")
            self.body = ttk.Frame(self)
            self.body.pack(fill="both", expand=True, padx=16, pady=10)

            self.frames = [self._build_step1(), self._build_step2(), self._build_step3()]

            ttk.Label(self, textvariable=self.status_var, foreground="#555").pack(
                anchor="w", padx=16, pady=(0, 12)
            )

        def _build_step1(self):
            frame = ttk.Frame(self.body)

            self._labelled(
                frame,
                "请先在浏览器里加载扩展，然后回到本窗口继续。\n\n"
                "1. 在浏览器地址栏打开  chrome://extensions  （Edge 用 edge://extensions）\n"
                "2. 打开右上角的“开发者模式”\n"
                "3. 点击“加载已解压的扩展程序”，选择下面这个文件夹：",
            ).pack(anchor="w")

            path_row = ttk.Frame(frame)
            path_row.pack(fill="x", pady=(10, 4))
            # The StringVar must be kept on self. An anonymous one gets garbage
            # collected, and StringVar.__del__ unsets the underlying Tcl
            # variable, which silently blanks the Entry.
            self.path_var = tk.StringVar(value=self.ext_dir)
            self.path_entry = ttk.Entry(path_row, textvariable=self.path_var, state="readonly")
            self.path_entry.pack(side="left", fill="x", expand=True)

            buttons = ttk.Frame(frame)
            buttons.pack(fill="x", pady=(2, 8))
            ttk.Button(buttons, text="打开扩展文件夹",
                       command=lambda: os.startfile(self.ext_dir)).pack(side="left")
            ttk.Button(buttons, text="复制路径",
                       command=lambda: self._copy(self.ext_dir, "扩展路径")).pack(side="left", padx=6)
            ttk.Button(buttons, text=f"复制 {CHROME_URL}",
                       command=lambda: self._copy(CHROME_URL, CHROME_URL)).pack(side="left")
            ttk.Button(buttons, text=f"复制 {EDGE_URL}",
                       command=lambda: self._copy(EDGE_URL, EDGE_URL)).pack(side="left", padx=6)

            self._labelled(
                frame,
                "也可以直接把上面这个文件夹拖到扩展管理页面上。\n\n"
                "加载完成后点击“下一步”——安装程序会从浏览器配置里读取扩展 ID，"
                "这样就不需要手动猜了。",
                foreground="#555",
            ).pack(anchor="w", pady=(6, 0))

            nav = ttk.Frame(frame)
            nav.pack(side="bottom", fill="x", pady=(18, 0))
            ttk.Button(nav, text="退出", command=self.destroy).pack(side="left")
            ttk.Button(nav, text="下一步 →", command=lambda: self.show_step(1)).pack(side="right")
            return frame

        def _build_step2(self):
            frame = ttk.Frame(self.body)

            self._labelled(
                frame, "扩展 ID 决定浏览器能否调用原生主机，写错就会安装失败。请用下面任一方式获取："
            ).pack(anchor="w")

            self._labelled(
                frame,
                "· 点击“读取浏览器配置”自动获取（推荐，最准确——ID 由浏览器自己算出）\n"
                "· 或者从 chrome://extensions 的扩展卡片上复制 ID，粘贴到下面输入框",
                foreground="#555",
            ).pack(anchor="w", pady=(2, 10))

            row = ttk.Frame(frame)
            row.pack(fill="x")
            self.id_var = tk.StringVar()
            self.id_var.trace_add("write", self._on_id_changed)
            ttk.Entry(row, textvariable=self.id_var, font=("Consolas", 10)).pack(
                side="left", fill="x", expand=True
            )
            self.read_btn = ttk.Button(row, text="读取浏览器配置", command=self._auto_detect)
            self.read_btn.pack(side="left", padx=(6, 0))

            self.id_status_var = tk.StringVar(value="")
            self.id_status = ttk.Label(frame, textvariable=self.id_status_var,
                                       wraplength=650, justify="left")
            self.id_status.pack(anchor="w", pady=(8, 0))

            self.detect_detail_var = tk.StringVar(value="")
            ttk.Label(frame, textvariable=self.detect_detail_var, foreground="#555",
                      wraplength=650, justify="left").pack(anchor="w", pady=(6, 0))

            nav = ttk.Frame(frame)
            nav.pack(side="bottom", fill="x", pady=(18, 0))
            ttk.Button(nav, text="← 上一步", command=lambda: self.show_step(0)).pack(side="left")
            self.install_btn = ttk.Button(nav, text="安装", command=self._do_install)
            self.install_btn.pack(side="right")
            self.install_btn.state(["disabled"])
            return frame

        def _build_step3(self):
            frame = ttk.Frame(self.body)
            self.done_var = tk.StringVar(value="")
            ttk.Label(frame, textvariable=self.done_var, wraplength=650,
                      justify="left").pack(anchor="w")

            nav = ttk.Frame(frame)
            nav.pack(side="bottom", fill="x", pady=(18, 0))
            ttk.Button(nav, text="完成", command=self.destroy).pack(side="right")
            return frame

        # -- behaviour -----------------------------------------------------

        def show_step(self, index):
            titles = [
                "步骤 1 / 3 — 加载浏览器扩展",
                "步骤 2 / 3 — 确认扩展 ID",
                "安装完成",
            ]
            self.step_var.set(titles[index])
            for i, frame in enumerate(self.frames):
                frame.pack_forget()
                if i == index:
                    frame.pack(fill="both", expand=True)
            self.status_var.set("")
            if index == 1:
                self._auto_detect(announce=False)

        def _on_id_changed(self, *_args):
            text = self.id_var.get().strip()
            if core.normalize_extension_id(text):
                self.install_btn.state(["!disabled"])
                self.id_status_var.set("ID 格式有效，可以安装。")
                self.id_status.configure(foreground="#0a7d28")
            else:
                self.install_btn.state(["disabled"])
                if text:
                    self.id_status_var.set("ID 格式不正确：应为 32 位、只含 a-p 的字符。")
                    self.id_status.configure(foreground="#b00020")
                else:
                    self.id_status_var.set("")
                    self.id_status.configure(foreground="#555")

        def _auto_detect(self, announce=True):
            matches = core.detect_extension_ids(self.ext_dir)
            if matches:
                self.id_var.set(matches[0].extension_id)
                where = "、".join(m.describe() for m in matches)
                self.detect_detail_var.set(
                    f"已从浏览器配置读取到扩展 ID（{where}）。"
                    + ("\n多个位置读到同一个 ID，说明加载正确。" if len(matches) > 1 else "")
                )
                self.status_var.set("已自动读取扩展 ID")
            else:
                self.detect_detail_var.set(
                    "未在 Chrome / Edge 的配置里找到这个扩展。\n"
                    "请确认：已经在浏览器里加载了上面那个文件夹，并且加载后浏览器一直开着。\n"
                    "仍然读不到时，可以在 chrome://extensions 打开扩展的“详细信息”，"
                    "复制其中的 ID，粘贴到上面的输入框。"
                )
                if announce:
                    self.status_var.set("未找到扩展，请手动填写，或重新加载后再点一次")

        def _do_install(self):
            extension_id = core.normalize_extension_id(self.id_var.get())
            if not extension_id:
                return

            self.install_btn.state(["disabled"])
            self.read_btn.state(["disabled"])
            self.status_var.set("正在安装…")
            self.update_idletasks()

            try:
                self.install_result = core.install_host([extension_id])
            except Exception as exc:  # noqa: BLE001 - surface anything to the user
                messagebox.showerror("安装失败", f"{type(exc).__name__}: {exc}")
                self.install_btn.state(["!disabled"])
                self.read_btn.state(["!disabled"])
                self.status_var.set("安装失败")
                return

            self.status_var.set("正在自检…")
            self.update_idletasks()
            self.ping_result = core.verify_host()

            self._render_done(extension_id)
            self.show_step(2)

        def _render_done(self, extension_id):
            browser = (self.ping_result or {}).get("browser") or {}
            if browser:
                probe = (
                    "自检找到浏览器窗口："
                    f"{browser.get('name') or browser.get('process') or 'Chromium'}"
                    f"（前台={browser.get('foreground')}）"
                )
            else:
                probe = "自检时没找到浏览器窗口（浏览器没开着也没关系，之后打开即可）。"

            self.done_var.set(
                "安装成功。\n\n"
                f"写入的扩展 ID：\n    {extension_id}\n\n"
                f"原生主机：\n    {os.path.join(core.APPDATA_DIR, core.HOST_EXE_NAME)}\n\n"
                f"原生消息清单：\n    {os.path.join(core.APPDATA_DIR, core.MANIFEST_NAME)}\n\n"
                f"{probe}\n\n"
                "最后一步：重启浏览器，让它重新读取原生消息主机。\n\n"
                f"卸载：设置 → 应用 → 已安装的应用 → {core.DISPLAY_NAME}"
            )

    app = InstallerApp()
    app.mainloop()
    return 0


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def run_uninstall_gui() -> int:
    _tk, _ttk = build_gui()
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()

    if not messagebox.askyesno(
        "卸载 THU Auto Login",
        "确定要卸载吗？\n\n"
        "会删除原生主机、原生消息清单、扩展文件和注册表项。\n"
        "浏览器里已加载的扩展需要在扩展管理页手动移除。",
    ):
        return 0

    try:
        core.uninstall()
    except Exception as exc:  # noqa: BLE001
        messagebox.showerror("卸载失败", f"{type(exc).__name__}: {exc}")
        return 1

    messagebox.showinfo(
        "卸载完成",
        "已删除原生主机和相关文件。\n\n"
        "还剩一件事需要手动做：\n"
        "在 chrome://extensions 里移除 THU Auto Login 扩展。\n\n"
        "建议随后重启浏览器。",
    )
    return 0


# ---------------------------------------------------------------------------
# Unattended install
# ---------------------------------------------------------------------------


def run_silent(extension_id=None) -> int:
    """No GUI. Prefers the browser configuration, falling back to path-derived
    IDs only when the extension has not been loaded yet."""
    try:
        ext_dir = core.extract_extension()
    except core.InstallError as exc:
        core.say(f"error: {exc}")
        return 1

    if extension_id:
        resolved = core.normalize_extension_id(extension_id)
        if not resolved:
            core.say(f"error: 扩展 ID 格式不正确: {extension_id}")
            return 1
        ids = [resolved]
        core.say(f"使用命令行指定的扩展 ID: {resolved}")
    else:
        matches = core.detect_extension_ids(ext_dir)
        if matches:
            ids = [matches[0].extension_id]
            core.say(f"已从 {matches[0].describe()} 读取扩展 ID: {ids[0]}")
        else:
            ids = core.extension_id_candidates(ext_dir)
            core.say("警告: 未在浏览器配置中找到该扩展，改用由安装路径推导的 ID。")
            core.say("      请先加载扩展再重新运行，否则浏览器可能拒绝连接原生主机。")
            core.say(f"      {ids[0]}")

    try:
        core.install_host(ids)
    except core.InstallError as exc:
        core.say(f"error: {exc}")
        return 1

    if extension_id is None:
        core.say("")
        core.say("请加载扩展：")
        core.say(f"  {ext_dir}")
    return 0


def run_silent_uninstall() -> int:
    try:
        core.uninstall()
    except core.InstallError as exc:
        core.say(f"error: {exc}")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=f"{core.DISPLAY_NAME} 安装程序", allow_abbrev=False
    )
    parser.add_argument("--uninstall", action="store_true", help="卸载")
    parser.add_argument("--silent", action="store_true", help="不显示图形界面")
    parser.add_argument("--extension-id", default=None, help="直接指定扩展 ID（静默安装用）")
    args = parser.parse_args(argv)

    if args.uninstall:
        return run_silent_uninstall() if args.silent else run_uninstall_gui()
    if args.silent:
        return run_silent(args.extension_id)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
