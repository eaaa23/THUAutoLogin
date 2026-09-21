# MacInstaller —— macOS 一体化安装器

把 THU Auto Login 打包成**一个 `.pkg`**（可选再套一层 `.dmg`）。安装包里包含**已经编译好的**主机程序，最终用户不需要 Xcode、编译器或 Python。

```
MacInstaller/
├── build-pkg.sh              构建 .pkg（可选 .dmg）
├── distribution.xml          安装器界面定义（版本/架构由脚本注入）
├── launchagent.plist.in      LaunchAgent 模板（@HOME@ 由 postinstall 替换）
├── uninstall.sh              系统级卸载脚本，随安装包一起装进去
├── scripts/postinstall       安装后脚本（装 LaunchAgent、触发授权提示）
└── resources/                欢迎页 / 完成页 / DMG 里的说明
```

---

## 快速开始

```bash
cd MacInstaller
./build-pkg.sh --dmg
```

产物：

```
MacInstaller/build/THUAutoLogin-1.0.0.pkg      安装器
MacInstaller/build/THUAutoLogin-1.0.0.dmg      上面那个 pkg 加说明文件
```

常用选项：

| 选项 | 作用 |
|---|---|
| `--skip-build` | 复用 `MacOS/build` 里已有的产物，不重新编译 |
| `--dmg` | 额外生成 `.dmg` |
| `--sign "Developer ID Installer: …"` | 对 `.pkg` 签名 |

`build-pkg.sh` 默认会先调用 `../MacOS/build.sh` 编译原生组件——**编译只发生在构建机上**，装进包里的是编译产物。

---

## 安装后的布局

安装器是**系统级**的（需要管理员密码）：

```
/Applications/THUAutoLoginKeyAgent.app                              菜单栏 App
/Library/Application Support/THUAutoLogin/bin/thu-autologin-host   原生消息主机
/Library/Application Support/THUAutoLogin/extension/                Chrome 扩展
/Library/Application Support/THUAutoLogin/uninstall.sh              卸载脚本
/Library/Google/Chrome/NativeMessagingHosts/com.thu.autologin.host.json
```

此外 `postinstall` 会以**当前登录用户**的身份：
1. 把 LaunchAgent 装到 `~/Library/LaunchAgents/`（模板里的 `@HOME@` 替换成该用户的家目录），并 `launchctl bootstrap` 到其 GUI 会话；
2. 启动一次按键代理——这是让 macOS 弹出"辅助功能"授权对话框的必要动作。

### 为什么是系统级安装

因为 Chrome 用**未打包扩展目录的绝对路径**推导扩展 ID。固定成系统路径后，**所有用户拿到同一个扩展 ID**，于是原生消息清单可以在构建时就写死、只需要放一份，不必给每个用户分别处理。安装路径变了 ID 就会变，所以这个路径不能随意改动。

---

## 安装后仍需两步手动操作

安装器（完成页面 + DMG 里的说明）会提示：

1. **系统设置 → 隐私与安全性 → 辅助功能** → 勾选 `THUAutoLoginKeyAgent`
2. 打开 `chrome://extensions` → 开启开发者模式 → 加载已解压的扩展程序 → 选择
   `/Library/Application Support/THUAutoLogin/extension`
   （在该文件窗口里按 ⌘⇧G 可直接粘贴路径；或把文件夹拖到页面上）

这两步**无法脚本化**：辅助功能授权只能由用户在系统设置里确认，未打包扩展也只能由用户手动加载。

---

## 卸载

```bash
sudo "/Library/Application Support/THUAutoLogin/uninstall.sh"
```

它会停掉并删除 LaunchAgent、应用、主机、扩展、原生消息清单，并 `pkgutil --forget` 注销收据。另有两处需要手动处理（脚本会提示）：辅助功能列表里的残留条目、`chrome://extensions` 里已加载的扩展。

---

## 与开发安装（MacOS/install.sh）的冲突

`postinstall` 会做一件容易被忽略但很关键的事：**把用户级目录下同名的原生消息清单改名备份**。

原因是两者共用同一个主机名 `com.thu.autologin.host`，但扩展路径不同（因而扩展 ID 不同），清单位置也不同：

| 安装方式 | 清单位置 | 扩展路径 |
|---|---|---|
| 本 `.pkg` | `/Library/Google/Chrome/NativeMessagingHosts/`（系统级） | `/Library/Application Support/THUAutoLogin/extension` |
| `MacOS/install.sh` | `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`（用户级） | 仓库里的 `THUAutoLogin/` |

Chrome **优先读取用户级清单**，所以如果机器上留着旧的开发安装，它会把系统级清单整个遮蔽掉，而两者扩展 ID 不同，浏览器就会报：

```
Access to the specified native messaging host is forbidden
```

`postinstall` 的备份动作会把这个隐患清掉（备份文件名形如 `com.thu.autologin.host.json.pre-pkg-20260921163000.bak`）。安装日志在 `/var/log/thu-autologin-install.log`。

反过来，`MacOS/install.sh` 检测到系统级安装时会拒绝继续并提示二选一，避免重新制造这个冲突。

---

## 关于签名（要分发给别人时务必看）

### 代码签名

构建脚本在没有找到证书时会退回 **ad-hoc 签名**。这对本机自用没问题，但如果你想发给别人：

- **`.pkg` 未签名**：对方下载后 Gatekeeper 会拦截，需要右键→打开，或手动 `xattr -dr com.apple.quarantine`。
- **`.app` 是 ad-hoc 签名**：辅助功能授权绑定在代码签名（cdhash）上，**每次重新构建都会变化**，于是每次升级用户都要重新授权一次。用稳定的证书签名可以避免。

要正式分发，请准备 Apple Developer 证书后：

```bash
./build-pkg.sh --sign "Developer ID Installer: 你的名字 (TEAMID)"
```

并考虑对产物做 notarization（`xcrun notarytool submit`）。

### 打包的是哪一份二进制

`distribution.xml` 里的 `hostArchitectures` 由构建脚本从**实际二进制**读出（`lipo -archs`）。在 Apple Silicon 上构建出来的包只允许装在 Apple Silicon 上——这比装上去跑不起来要好。**要给 Intel Mac 用，需要在 Intel 机器上（或做 universal binary 后）重新构建。**

---

## 构建时的自检

构建脚本不会静默产出坏包，下面这些在开发时都已验证过：

- `pkgutil --payload-files` 列出的文件树与上面"安装后的布局"一致
- 原生消息清单里的 `path` 与 `allowed_origins`（扩展 ID 由安装路径推导）正确
- 主机二进制是真正的 Mach-O 且保留了可执行位
- `.app` 的代码签名在打包后仍然有效（`codesign --verify`）
- `postinstall` / `uninstall.sh` 通过 `bash -n`
- LaunchAgent 模板里仍带 `@HOME@`（由 postinstall 替换，不是构建时）

自己复核：

```bash
pkgutil --expand-full build/THUAutoLogin-1.0.0.pkg /tmp/pkgcheck
pkgutil --payload-files build/THUAutoLogin-1.0.0.pkg
```
