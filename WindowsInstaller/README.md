# WindowsInstaller —— Windows 一体化安装器

把 THU Auto Login 打包成**一个单文件 exe**（`THUAutoLogin-Setup.exe`）。安装包里包含**已经编译好的**主机程序，目标机器**不需要 Python、不需要 pywin32、不需要编译器**。

```
WindowsInstaller/
├── build.py        构建两个 exe（必须先构建主机，再把它嵌进安装器）
├── installer.py    安装器本体，被 PyInstaller 冻结成单文件 exe
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
WindowsInstaller\build\dist\THUAutoLogin-Setup.exe    安装器（把主机和扩展都嵌进去了）
```

**只需要把 `THUAutoLogin-Setup.exe` 发给用户**，另一个是它的内部组件。

常用选项：

| 选项 | 作用 |
|---|---|
| `--host-only` | 只重新构建主机 exe |
| `--setup-only` | 只重新构建安装器（复用已有的主机 exe） |

> **PyInstaller 无法交叉编译**：Windows 的 exe 必须在 Windows 上构建。在 macOS 上运行 `build.py` 会直接报错并提示改用 `MacInstaller/build-pkg.sh`。

---

## 用户那边会发生什么

双击 `THUAutoLogin-Setup.exe`，它会：

1. 把主机 exe 和扩展复制到 `%LOCALAPPDATA%\THUAutoLogin\`
2. 计算扩展 ID（见下），写出原生消息清单
3. 写注册表 `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.thu.autologin.host`
4. 复制自身为卸载程序，并注册到"设置 → 应用 → 已安装的应用"
5. 跑一次 `thu-autologin-host.exe --ping` 自检，报告是否找到 Chrome 窗口
6. 打开扩展所在文件夹，并打印后续步骤

全部在 `HKCU` 与 `%LOCALAPPDATA%` 内，**不需要管理员权限**。

安装后的布局：

```
%LOCALAPPDATA%\THUAutoLogin\thu-autologin-host.exe          原生消息主机
%LOCALAPPDATA%\THUAutoLogin\extension\                      Chrome 扩展
%LOCALAPPDATA%\THUAutoLogin\com.thu.autologin.host.json     原生消息清单
%LOCALAPPDATA%\THUAutoLogin\THUAutoLogin-Setup.exe          卸载程序（自身副本）
```

### 用户仍需手动做两步

1. `chrome://extensions` → 开启开发者模式 → 加载已解压的扩展程序 → 选择 `%LOCALAPPDATA%\THUAutoLogin\extension`
   （安装器会打开该文件夹，可以直接把文件夹拖到扩展页面上）
2. **重启 Chrome**，让它重新读取注册表里的原生消息主机

未打包扩展只能由用户手动加载，这一步无法脚本化。

### 卸载

设置 → 应用 → 已安装的应用 → `THU Auto Login`，或者：

```powershell
"%LOCALAPPDATA%\THUAutoLogin\THUAutoLogin-Setup.exe" --uninstall
```

会删除注册表项、清单、主机和扩展。**卸载程序自身那一份 exe 删不掉**（Windows 不允许删除正在运行的 exe），会留在目录里，重装时会被覆盖——这是自删除型卸载程序的常规行为。

---

## 两个设计要点

### 为什么不再需要 `.cmd` 启动器

早期版本的主机是 `thu_autologin_host.py`，Chrome 清单的 `path` 不能直接指向 `.py`，只能生成一个 `.cmd` 去回调 `python.exe`。那带来两个问题：目标机器必须装 Python + pywin32，而且启动器要把 `sys.executable` 路径写死（虚拟环境一删就失效）。

现在主机被 PyInstaller 编译成真正的 exe，清单的 `path` **直接指向 exe**，上面两个问题一起消失。

### 扩展 ID 怎么确定

Chrome 用**未打包扩展目录绝对路径的 SHA-256** 推导 ID，所以：

1. **优先读 Chrome 自己记录的 ID**：`installer.py` 会扫描 `Secure Preferences` / `Preferences`，找出 `location == 4`（未打包）且 `path` 与安装目录匹配的那一项。这是最可靠的方式——不需要任何猜测。
2. **读不到才由路径推导**（首次安装时扩展还没加载过，通常走这条）。

第 2 种情况下，路径里如果含**非 ASCII 字符**（比如中文用户名）就会有歧义：Chrome 到底按 UTF-8 还是按系统 ANSI 代码页哈希这个路径，官方没有说明。与其猜，`installer.py` 把**两种编码各算一个 ID 一起写进 `allowed_origins`**——它们指向同一个文件夹，所以并没有放宽什么。纯 ASCII 路径下两种编码字节相同，会自动去重成**一个** ID。

（不包含 UTF-16：Chromium 哈希的是 wide→UTF-8 转换结果，把 UTF-16 也算进去只会给每次安装都多加一个无用的 origin。）

---

## 构建时的自检

`build.py` 在动手前会检查平台、PyInstaller、pywin32 和源文件是否齐全，缺哪个就直接报错退出。

两个 exe 都必须是 `--console`：主机要走 stdio 说原生消息协议，`--noconsole` 会让它的 stdin/stdout 失效。

`installer.py` 的逻辑在开发机上用桩件（替换 `winreg`、模拟 `sys.frozen`）完整跑过：扩展 ID 推导（含已知向量比对）、安装、清单内容、注册表写入、卸载、重复安装/卸载的幂等性，共 29 项断言。在 Windows 上你可以直接复核：

```powershell
# 装完之后
type "%LOCALAPPDATA%\THUAutoLogin\com.thu.autologin.host.json"
reg query "HKCU\Software\Google\Chrome\NativeMessagingHosts\com.thu.autologin.host"
"%LOCALAPPDATA%\THUAutoLogin\thu-autologin-host.exe" --ping
```
