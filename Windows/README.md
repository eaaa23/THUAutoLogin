# THU Auto Login —— Windows 实现

> 扩展本身在仓库根目录的 `THUAutoLogin/`，**两端共享、不含平台代码**。
> 跨平台总览见 [`../README.md`](../README.md)，macOS 见 [`../MacOS/README.md`](../MacOS/README.md)。

---

## 1. 与 macOS 的差异：只需要一个原生主机

macOS 上不得不拆成两个原生组件，因为 TCC（隐私权限）按 **responsible process** 归属：native messaging host 是 Chrome 的子进程，给它"辅助功能"权限等于给 Chrome 本身。

**Windows 没有这个问题。** 由 Chrome 启动的进程可以直接调用 `keybd_event` 注入输入，不需要任何针对单个二进制的授权。所以这里只有一个原生主机：

```
  id.tsinghua.edu.cn 登录页
        │
        │  content.js（隔离世界）—— document.hasFocus() 把关
        ▼
  background.js（service worker）
        │  chrome.runtime.connectNative('com.thu.autologin.host')
        │  4 字节小端长度前缀 + UTF-8 JSON
        ▼
  thu_autologin_host.py（唯一原生主机）
        │  win32gui    —— 找到 Chrome 主窗口、确认它在前台
        │  win32api.keybd_event —— 注入真实按键
        ▼
  Google Chrome → 渲染器认定发生了真实用户输入 → 自动填充值对 JS 可见
        │
        └──► background.js 轮询到有值 → 调用页面自身的 doLogin()
```

主机名、请求格式、响应格式、按键白名单都与 macOS 完全一致，所以**扩展一行都不用改**。

### 为什么用 win32gui 找窗口

`keybd_event` 是**注入到当前前台窗口**的，它本身不知道目标是谁。所以：

- 用 `win32gui.EnumWindows` 枚举类名为 `Chrome_WidgetWin_1` 的可见顶层窗口；
- 优先选择**当前前台**的那一个（扩展只在页面确实有焦点时才发请求，所以前台窗口就是正确目标）；
- 这个判断顺带解决了"`Chrome_WidgetWin_1` 也被其它 Chromium 浏览器使用"的歧义，不需要再查进程名。

如果目标窗口不在前台，主机**拒绝发送**并返回 `browser-not-foreground`。这是继扩展 `document.hasFocus()` 之后的第二道防线：即使焦点在两次检查之间漂移，按键也不会落到别的程序里。

---

## 2. 目录结构

```
Windows/
└── native-host/
    ├── thu_autologin_host.py   原生主机（单文件，唯一需要部署的组件）
    ├── install.py              安装：复制文件 + 写清单 + 写注册表
    ├── uninstall.py            卸载
    ├── requirements.txt        pywin32
    └── README.md               本文件
```

安装后落到（全部在用户目录，**无需管理员权限**）：

| 内容 | 位置 |
|---|---|
| 主机脚本 | `%LOCALAPPDATA%\THUAutoLogin\thu_autologin_host.py` |
| 启动器 | `%LOCALAPPDATA%\THUAutoLogin\thu-autologin-host.cmd` |
| 清单 | `%LOCALAPPDATA%\THUAutoLogin\com.thu.autologin.host.json` |
| 注册表 | `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.thu.autologin.host` |
| 日志（可选） | `%LOCALAPPDATA%\THUAutoLogin\host.log` |

---

## 3. 安装

```powershell
# 1. 依赖
python -m pip install pywin32

# 2. 安装（会复制文件、写清单、写注册表，并做一次自检）
python Windows\native-host\install.py
```

装完后：

1. **重启 Chrome**（让它重新读取原生消息主机注册表）；
2. 打开 `chrome://extensions`，确认 `THU Auto Login` 已加载并启用；
3. 打开 `id.tsinghua.edu.cn` 登录页，**保持 Chrome 在前台**。

### 关于扩展 ID

Chrome 用**未打包扩展目录绝对路径的 SHA-256** 推导扩展 ID，所以同一个扩展在 Windows 上和在 macOS 上 ID **不同**。

`install.py` 不会去猜：它直接读 Chrome 自己的 `Secure Preferences` / `Preferences`，找出 `location == 4`（未打包）且 `path` 与扩展目录匹配的那一项，取它记录的 ID。只有读不到时才回退到按路径计算，并打印警告。

如果自动检测失败（比如扩展还没在 Chrome 里加载过），可以手动指定：

```powershell
python Windows\native-host\install.py --extension-dir "D:\path\to\THUAutoLogin" --extension-id <你的ID>
```

### 关于 .cmd 启动器

Chrome 清单里的 `path` 必须指向一个可执行文件，不能直接是 `.py`。安装器因此生成一个 `.cmd` 启动器：

```bat
@echo off
"C:\...\python.exe" "C:\...\thu_autologin_host.py" %*
```

- `@echo off` 是**必须的**：否则 cmd.exe 会把命令行回显到 stdout，直接破坏长度前缀的消息流。
- cmd.exe 只负责启动子进程、原样继承 stdin/stdout 管道，不会碰二进制数据，所以消息流是安全的。
- Chrome 启动原生主机时是隐藏窗口，不会闪黑框。

启动器会把 `sys.executable` 固定写死。**如果你用虚拟环境安装，之后删掉那个 venv 就会失效** —— 安装器检测到虚拟环境时会提示。想要更干净的话，用 PyInstaller 打成一个 exe 并把清单的 `path` 指向它：

```powershell
python -m pip install pyinstaller
pyinstaller --onefile --console --name thu-autologin-host Windows\native-host\thu_autologin_host.py
# 然后把 com.thu.autologin.host.json 里的 path 改成生成的 exe 的绝对路径
```

注意必须用 `--console`：`--noconsole` 会让 stdio 失效，原生消息直接没法通信。

---

## 4. 验证

主机自带三个命令行模式（扩展不会用到，仅供手动排查）：

```powershell
# 诊断：pywin32 是否可用、枚举到哪些 Chrome 窗口、选中的是哪个
python "%LOCALAPPDATA%\THUAutoLogin\thu_autologin_host.py" --ping

# 自检：打印窗口信息，3 秒后向前台 Chrome 发一次 Tab
python "%LOCALAPPDATA%\THUAutoLogin\thu_autologin_host.py" --selftest

# 发送一次指定按键
python "%LOCALAPPDATA%\THUAutoLogin\thu_autologin_host.py" --once tab
```

`--ping` 输出示例：

```json
{
  "ok": true,
  "pong": true,
  "platform": "win32",
  "pywin32": true,
  "strategies": ["enter", "escape", "f15", "f16", "shift", "tab"],
  "browser": {
    "hwnd": 1234567,
    "title": "登录 - Google Chrome",
    "pid": 4321,
    "process": "chrome.exe",
    "foreground": true
  },
  "windows": [ ... ]
}
```

打开日志：

```powershell
$env:THU_AUTOLOGIN_DEBUG=1     # 设置后重启 Chrome 生效
Get-Content "$env:LOCALAPPDATA\THUAutoLogin\host.log" -Wait
```

---

## 5. 按键策略与解锁阶梯

主机支持的按键**白名单**（与 macOS 完全一致）：

| strategy | 虚拟键码 | 副作用 |
|---|---|---|
| `tab` | `0x09` | 移动焦点 |
| `f15` | `0x7E` | 无 |
| `f16` | `0x7F` | 无 |
| `escape` | `0x1B` | 可能关闭自动填充下拉 |
| `enter` | `0x0D` | **会触发表单提交 / 页面 `keyLogin()`** |
| `shift` | `0x10` | 无（但实测**无法**解锁自动填充） |

这个白名单是主机能发出的**全部**键盘能力——它无法输入任何字符，因此不可能被用来注入密码或命令。

扩展侧的逐级重试（`THUAutoLogin/background.js`）：

```js
const UNLOCK_LADDER = [
  { strategy: 'tab',   pollMs: 1000 },
  { strategy: 'f15',   pollMs: 1000 },
  { strategy: 'enter', pollMs: 4000, submitsPage: true },
];
```

逐级尝试，每级之后轮询确认自动填充值是否已对 JS 可见。走到 `enter` 就不再多调用一次 `doLogin()`，避免重复提交。

### Edge 的差异：Tab 要连按两次

扩展到 Edge 也能直接用，但 Edge 的密码填充**需要连按两次 Tab** 才提交。扩展会自行检测浏览器类型（`userAgentData.brands` 优先，UA 兜底），只在 Edge 上把 Tab 这一级连发两次，间隔由 `UNLOCK_REPEAT_GAP_MS` 控制（默认 60 ms）。

两次按键是两次独立的 `unlock` 请求，因此**主机侧不需要任何改动**。

如果 Windows 上默认顺序不生效，把有效的那个挪到最前面即可。

---

## 6. 疑难排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `Specified native messaging host not found` | 注册表项/清单路径不对，或 Chrome 未重启 | 重跑 `install.py`，然后重启 Chrome |
| `Access to the specified native messaging host is forbidden` | 清单里 `allowed_origins` 的 ID 与实际扩展 ID 不一致 | 重跑 `install.py`（它会读 Chrome 配置重新检测） |
| `Native host has exited` | 主机启动即崩 | 见下方「Chrome 会附加自己的命令行参数」；再用 `--ping` 手动跑一遍看报错 |
| 主机报 `unrecognized arguments: chrome-extension://… --parent-window=0` | 用 `argparse.parse_args()` 解析了 Chrome 附加的参数 | **已修复**（改用 `parse_known_args`）；若你本地改过，见下方说明 |
| 返回 `browser-not-foreground` | 浏览器不在前台 | 保持登录页在前台；或让扩展在切回标签页时重试（已内置） |
| 返回 `browser-not-running` | 没枚举到 Chromium 浏览器窗口 | 确认 Chrome / Edge 正在运行；两者共用 `Chrome_WidgetWin_1` 窗口类，都能被识别 |
| 按键发了但输入框仍为空 | Chrome 没有自动填充（没保存凭据），或按键不足以解锁 | 确认 Chrome 能弹出该站点的密码建议；调整解锁阶梯 |
| 中文/空格路径导致启动失败 | `.cmd` 以 ANSI 代码页编码 | 安装器已优先用 `mbcs` 写入；若仍失败，改用 PyInstaller 打 exe |
| 虚拟环境被删除后失效 | 启动器固定了 `sys.executable` | 用系统 Python 重装，或改用 exe |

### Chrome 会附加自己的命令行参数

Chrome 启动原生主机时**不是**只运行你的程序，它会追加两个参数：

```
thu-autologin-host.exe chrome-extension://<扩展ID>/ --parent-window=0
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^^^^^
                       调用方扩展的 origin            Windows 专有：对话框父窗口句柄
```

所以主机**必须容忍不认识的参数**。用 `argparse.parse_args()` 会直接抛 `SystemExit(2)`：

```
usage: thu-autologin-host.exe [-h] [--selftest] [--ping] [--once STRATEGY]
thu-autologin-host.exe: error: unrecognized arguments: chrome-extension://…/ --parent-window=0
```

而 Chrome 只会笼统地报 `Native host has exited`，看不到这段 stderr，非常难查。

本主机用 `parse_known_args()` 而不是 `parse_args()`，并且把 `<origin>` 和 `--parent-window` **显式声明**出来（声明即文档，同时避免歧义），另外关掉了 `allow_abbrev`，防止 Chrome 的参数被当成我们自己选项的缩写。将来 Chrome 再加参数也只会被丢进 extras，不会让主机在开始服务前就退出。

### 看不到主机报错怎么办

Chrome 默认不显示原生主机的 stderr。用调试参数启动 Chrome 才能看到：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --enable-logging=stderr --v=1
```

或者让主机自己记日志（更省事）：

```powershell
$env:THU_AUTOLOGIN_DEBUG=1
Get-Content "$env:LOCALAPPDATA\THUAutoLogin\host.log" -Wait
```

主机启动时会把收到的完整命令行、origin、parent-window 都记进日志，排查参数问题很直接。

完全没有任何反应时，最有效的顺序是：

1. `--ping` 看主机本身是否能跑、能否找到 Chrome 窗口；
2. 设 `THU_AUTOLOGIN_DEBUG=1` 看 `host.log` 里有没有收到请求；
3. 在 `chrome://extensions` 打开扩展的 service worker 控制台，看 `connectNative` 是否报错。

---

## 7. 卸载

```powershell
python Windows\native-host\uninstall.py           # 保留 host.log
python Windows\native-host\uninstall.py --purge   # 一并删除日志
```

会删掉注册表项、清单、启动器和已安装的主机脚本，并在目录为空时删除目录。扩展本身请在 `chrome://extensions` 里手动移除，之后建议重启 Chrome。

---

## 8. 安全说明

- 主机**只接受白名单按键**（上表 6 个），不能发送任意按键或任意文本，因此无法被用来注入密码或命令。
- 主机**不读取、不记录、不传输任何凭据**；凭据只存在于 Chrome 的自动填充与页面 DOM 中。
- 只接受 `ping` / `unlock` 两种消息类型。
- 清单的 `allowed_origins` 限定为扩展 ID，其它扩展无法调用。
- 目标窗口不在前台时**拒绝发送**，避免按键落到别的应用程序。
- 全部安装在 `HKCU` 与 `%LOCALAPPDATA%`，不触碰系统级位置，也不需要管理员权限。
