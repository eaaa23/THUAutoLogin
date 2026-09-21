# WindowsInstaller —— Windows 一体化安装器（图形界面）

把 THU Auto Login 打包成**一个单文件 exe**（`THUAutoLogin-Setup.exe`）。安装包里包含**已经编译好的**主机程序，目标机器**不需要 Python、不需要 pywin32、不需要编译器**。

```
WindowsInstaller/
├── build.py            构建两个 exe（先构建主机，再把它嵌进安装器）
├── installer.py        图形界面安装器（tkinter），被冻结成单文件 exe
├── installer_core.py   安装逻辑，不含界面 —— 便于脱开显示器测试
└── README.md
```

---

## 快速开始（在 Windows 上执行）

```powershell
python -m pip install pywin32 pyinstaller
python WindowsInstaller\build.py
```

产物：

```
WindowsInstaller\build\dist\thu-autologin-host.exe    原生消息主机
WindowsInstaller\build\dist\THUAutoLogin-Setup.exe    安装器（图形界面，已嵌入主机和扩展）
```

**只需要把 `THUAutoLogin-Setup.exe` 发给用户**，另一个是它的内部组件。

| 构建选项 | 作用 |
|---|---|
| `--host-only` | 只重新构建主机 exe |
| `--setup-only` | 只重新构建安装器（复用已有的主机 exe） |

> **PyInstaller 无法交叉编译**：Windows 的 exe 必须在 Windows 上构建。在 macOS 上运行 `build.py` 会直接报错并提示改用 `MacInstaller/build-pkg.sh`。

---

## 安装流程（三步向导）

双击 `THUAutoLogin-Setup.exe` 后是一个向导窗口：

### 步骤 1 / 3 — 加载浏览器扩展

窗口会先把扩展解压到 `%LOCALAPPDATA%\THUAutoLogin\extension`，并显示：

- 图文步骤：打开 `chrome://extensions`（Edge 用 `edge://extensions`）→ 开启开发者模式 → 加载已解压的扩展程序
- 扩展所在文件夹的完整路径（只读输入框）
- **打开扩展文件夹** 按钮
- **复制 chrome://extensions** / **复制 edge://extensions** 按钮（免去手打地址）

用户加载完成后点"下一步"。

### 步骤 2 / 3 — 确认扩展 ID

这是这次改动的重点。**扩展 ID 不再靠猜**，两种获取方式：

| 方式 | 说明 |
|---|---|
| **读取浏览器配置**（推荐） | 直接扫描 Chrome 和 Edge 的 `Secure Preferences` / `Preferences`，找出 `location == 4`（未打包）且 `path` 指向我们安装目录的那一项。**ID 是浏览器自己算出来的，不会有歧义。** |
| **手动输入** | 从 `chrome://extensions` 的扩展卡片复制 ID 粘贴进来 |

输入框会**实时校验**：必须是 32 位、只含 `a`–`p` 的字符，格式不对时"安装"按钮保持禁用并给出提示。也接受直接粘贴 `chrome-extension://<id>/` 这种整串，会自动提取出 ID。

进入这一步时会**自动尝试读取一次**，读到就直接填好；读不到会说明可能的原因（还没加载 / 浏览器没开 / 需要手动复制）。

### 步骤 3 / 3 — 完成

显示写入的扩展 ID、主机路径、清单路径，并跑一次 `thu-autologin-host.exe --ping` 自检，报告是否找到了浏览器窗口。最后提示重启浏览器。

---

## 为什么改成"先加载扩展，再确认 ID"

之前是安装时**由安装路径推导**扩展 ID，这正是 `com.thu.autologin.host.json` 里 ID 经常出错的原因：

Chrome 用**未打包扩展目录绝对路径的 SHA-256** 推导 ID。路径里一旦含**非 ASCII 字符**（比如中文用户名），到底按 UTF-8 还是按系统 ANSI 代码页哈希，官方没有说明——推导就未必和浏览器一致，清单里的 `allowed_origins` 对不上，浏览器就会拒绝连接原生主机。

改成先让浏览器加载扩展、再从浏览器自己的配置里读回 ID，就完全没有猜测成分了。手动输入作为兜底。

因此安装被拆成两个阶段（`installer_core.py`）：

```
阶段 A  extract_extension()      先把扩展落盘，否则用户没法加载
阶段 B  install_host(ext_id)     复制主机、按确认的 ID 写清单、写注册表
```

顺序不能颠倒：扩展必须先能被加载，ID 才可能被读回来。

清单里现在只写**一个**确认过的 origin（不再像以前那样把两种编码变体都列上）。

---

## 安装后的布局

```
%LOCALAPPDATA%\THUAutoLogin\thu-autologin-host.exe          原生消息主机
%LOCALAPPDATA%\THUAutoLogin\extension\                      扩展
%LOCALAPPDATA%\THUAutoLogin\com.thu.autologin.host.json     原生消息清单
%LOCALAPPDATA%\THUAutoLogin\THUAutoLogin-Setup.exe          卸载程序（自身副本）
%LOCALAPPDATA%\THUAutoLogin\install.log                     仅当无控制台时（静默安装）写入
```

全部在 `HKCU` 与 `%LOCALAPPDATA%` 内，**不需要管理员权限**。

---

## 卸载

设置 → 应用 → 已安装的应用 → `THU Auto Login`（图形确认对话框），或者：

```powershell
"%LOCALAPPDATA%\THUAutoLogin\THUAutoLogin-Setup.exe" --uninstall
```

**卸载程序自身那一份 exe 删不掉**（Windows 不允许删除正在运行的 exe），会留在目录里，重装时会被覆盖——这是自删除型卸载程序的常规行为。

---

## 命令行

| 命令 | 作用 |
|---|---|
| `THUAutoLogin-Setup.exe` | 图形界面安装 |
| `THUAutoLogin-Setup.exe --silent` | 静默安装（优先读浏览器配置，读不到才回退到路径推导并警告） |
| `THUAutoLogin-Setup.exe --silent --extension-id <id>` | 静默安装并指定 ID |
| `THUAutoLogin-Setup.exe --uninstall` | 图形界面卸载 |
| `THUAutoLogin-Setup.exe --uninstall --silent` | 静默卸载（注册表里 `QuietUninstallString` 用的就是这个） |

静默安装**不显示界面**，所以进度会写进 `%LOCALAPPDATA%\THUAutoLogin\install.log`。

---

## 两个实现要点

### 安装器是 `--noconsole`，主机是 `--console`

主机必须走 stdio 说原生消息协议，`--noconsole` 会让它的 stdin/stdout 失效，所以它保持控制台程序。

安装器正相反：它是图形界面，用 `--noconsole` 构建，双击时不会闪出黑色控制台窗口。代价是 `sys.stdout` 为 `None`，因此 `installer_core.say()` 在无控制台时会改写 `install.log`，静默安装仍然可追查。

### 为什么不再需要 `.cmd` 启动器

早期版本的主机是 `thu_autologin_host.py`，Chrome 清单的 `path` 不能直接指向 `.py`，只能生成一个 `.cmd` 去回调 `python.exe`。那带来两个问题：目标机器必须装 Python + pywin32，而且启动器要把 `sys.executable` 路径写死（虚拟环境一删就失效）。

现在主机被 PyInstaller 编译成真正的 exe，清单的 `path` **直接指向 exe**，上面两个问题一起消失。

---

## 构建时的自检

`build.py` 在动手前会检查平台、PyInstaller、pywin32 和源文件是否齐全，缺哪个就直接报错退出。

逻辑层（`installer_core.py`）与界面层（`installer.py`）是分开的，因此前者可以脱开显示器、用桩件（替换 `winreg`、模拟 `sys.frozen`、伪造 Chrome/Edge 配置）完整测试：

- 扩展 ID 推导（含已知向量比对）
- **ID 归一化**：裸 ID、前后空白、大写、粘贴 `chrome-extension://…` URL、以及 9 种非法输入
- **Chrome + Edge 双浏览器探测**：同时存在时都能找到且给出同一个 ID；`location != 4` 和其它路径被正确忽略
- 两阶段安装、清单内容（**只含一个确认的 origin**）、注册表、卸载、幂等性

界面层也用桩件接管了 `mainloop`，把三步向导完整构建一遍、走完所有步骤切换，并逐一验证 6 种输入对应的"安装"按钮启用/禁用状态。

在 Windows 上你可以直接复核：

```powershell
# 装完之后
type "%LOCALAPPDATA%\THUAutoLogin\com.thu.autologin.host.json"
reg query "HKCU\Software\Google\Chrome\NativeMessagingHosts\com.thu.autologin.host"
"%LOCALAPPDATA%\THUAutoLogin\thu-autologin-host.exe" --ping
```
