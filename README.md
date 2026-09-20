# THU Auto Login

自动登录清华大学信息门户 / 网络学堂的 Chrome 扩展，**不依赖 `chrome.debugger`，因此不会出现"正在调试此浏览器"横幅**。

---

## 1. 背景：横幅是怎么来的，又是怎么去掉的

登录 `id.tsinghua.edu.cn/do/off/ui/auth/login/form/*` 时，Chrome 已经把保存的账号密码自动填充进了 `#i_user` / `#i_pass`，但在**页面收到一次真实的用户交互之前，不会把这些值暴露给 JavaScript**。所以脚本看到的是两个空输入框。

旧方案用 `chrome.debugger.attach()` + CDP `Input.dispatchKeyEvent` 伪造一个可信按键来"解锁"。CDP 注入的事件确实是可信的，但代价是：**只要 attach 了 debugger，Chrome 就会在窗口顶部常驻一条调试横幅，且没有任何 API 可以隐藏它。**

新方案把"可信按键"的来源从 CDP 换成操作系统本身：

> 由原生组件通过 Quartz 向 Chrome 进程投递**真实的 CGEvent 键盘事件**。操作系统级事件天然就是可信事件，渲染器无法区分它和用户手上的键盘——于是不需要 debugger，也就没有横幅。

---

## 2. 架构

```
  id.tsinghua.edu.cn 登录页
        │
        │  content.js（隔离世界）
        │  仅当 document.hasFocus() && visibilityState === 'visible' 才继续
        ▼
  background.js（service worker）
        │  chrome.runtime.connectNative()
        │  Chrome native messaging 协议：4 字节长度前缀 + JSON
        ▼
  主机 ①  thu-autologin-host            ← 无任何 TCC 权限，只做转发
        │  Unix domain socket，换行分隔 JSON
        │  ~/Library/Application Support/THUAutoLogin/keyagent.sock (0600)
        ▼
  主机 ②  THUAutoLoginKeyAgent.app      ← 菜单栏 App，持有"辅助功能"权限
        │  CGEvent.postToPid(chromePid, keyDown/keyUp)
        ▼
  Google Chrome 进程
        │  渲染器认定为真实用户输入
        ▼
  自动填充的账号密码对 JS 可见
        │
        └──► background.js 轮询到有值 → 调用页面自身的 doLogin()
```

### 为什么必须拆成两个原生组件

macOS 的 TCC（隐私权限）按 **responsible process** 归属。主机 ① 是 Chrome 的子进程，如果直接给主机 ① 授予"辅助功能"权限，**实际被授权的是 Chrome 本身**——既不安全，身份也不稳定。

所以主机 ② 必须是由 launchd 独立拉起的 `.app` bundle，拥有自己的 bundle id 和代码签名身份，权限才归属于它自己。主机 ① 只需要 Unix socket，不需要任何权限。

### 焦点处理

真实按键只会送到**当前焦点**。因此：

- `content.js` 用 `document.hasFocus() && document.visibilityState === 'visible'` 把关，页面在后台时**完全不发消息**；
- 监听了 `focus` / `visibilitychange`，用户切回该标签页的瞬间立即触发，而不是傻等下一个轮询周期；
- `background.js` 在每次真正按键前**再查一次焦点**，防止"检查通过 → 用户切走 → 按键落到别的窗口"的竞态；
- 默认投递方式是 `postToPid`，直接投进 Chrome 的进程事件队列，即使焦点在毫秒级漂移也不会打到别的 App。

---

## 3. 目录结构

```
AutoLogin/
├── THUAutoLogin/               Chrome 扩展
│   ├── manifest.json           nativeMessaging 权限；已移除 debugger
│   ├── background.js           原生消息通道 + 解锁阶梯 + 调用 doLogin()
│   └── content.js              页面规则 + 焦点把关
├── shared/
│   └── SocketWire.swift        Unix socket / 分帧工具（两个主机共用）
├── native-host/                主机 ①：Chrome native messaging host
│   ├── main.swift
│   └── build.sh
├── key-agent/                  主机 ②：菜单栏 App
│   ├── main.swift
│   ├── Info.plist              LSUIElement = true（无 Dock 图标）
│   └── build.sh
├── build.sh                    一次构建两个组件 → ./build
├── install.sh                  安装 + 注册（无需 sudo）
└── uninstall.sh                卸载
```

---

## 4. 安装

```bash
./build.sh      # 编译两个组件到 ./build
./install.sh    # 安装并注册
```

`install.sh` 会做四件事（**全部在用户目录内，不需要 sudo**）：

| 目标 | 位置 |
|---|---|
| 主机 ① 二进制 | `~/Library/Application Support/THUAutoLogin/bin/thu-autologin-host` |
| Chrome 原生消息清单 | `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.thu.autologin.host.json` |
| 主机 ② App | `~/Applications/THUAutoLoginKeyAgent.app` |
| LaunchAgent（开机自启） | `~/Library/LaunchAgents/com.thu.autologin.keyagent.plist` |

装到 `/Applications` 的话：`APP_INSTALL_DIR=/Applications ./install.sh`。

### 安装后还需要两步手动操作

1. **授予"辅助功能"（控制）权限**
   `系统设置 → 隐私与安全性 → 辅助功能` → 勾选 `THUAutoLoginKeyAgent`

   菜单栏会出现一个 **THU** 图标，点它可以直接打开该设置页。

2. **重新加载扩展**
   `chrome://extensions` → 重新加载 "THU Auto Login"。
   因为权限变了（移除了 `debugger`，新增了 `nativeMessaging`），Chrome 会要求确认。

> 扩展 ID 由**文件夹绝对路径**的 SHA-256 推导（这是 Chrome 对未打包扩展的规则），当前为 `gedgmoihnnbcodgmemcbgchojjgbihlk`。
> **移动 `AutoLogin` 文件夹会导致 ID 变化**，需要重新执行 `install.sh`。也可以用 `EXTENSION_ID=<id> ./install.sh` 手动指定。

---

## 5. 验证

优先用**菜单栏图标 → "自检（向 Chrome 发送一次 Shift）"**，它会报告：
辅助功能是否已授权、是否找到 Chrome 及其 pid、按键是否发送成功。

也可以在终端验证，但请注意 TCC 归属问题：

```bash
# 推荐：让 launchd/LaunchServices 以 App 身份运行
open -a THUAutoLoginKeyAgent --args --selftest

# 直接跑 bundle 内的二进制（可能让 TCC 把权限归给终端）
./build/THUAutoLoginKeyAgent.app/Contents/MacOS/THUAutoLoginKeyAgent --ping
```

`--ping` 输出示例：

```json
{
  "ok": true,
  "pong": true,
  "accessibility": true,
  "strategies": ["shift", "f15", "f16", "tab", "escape", "enter"],
  "chrome": { "pid": 86946, "name": "Google Chrome", "active": true }
}
```

---

## 6. 调参

### 解锁按键阶梯 — `THUAutoLogin/background.js`

```js
const UNLOCK_LADDER = [
  { strategy: 'shift', pollMs: 1000 },
  { strategy: 'tab',   pollMs: 1000 },
  { strategy: 'f15',   pollMs: 1000 },
  { strategy: 'enter', pollMs: 4000, submitsPage: true },
];
```

逐级尝试，每级之后轮询最多 `pollMs` 确认自动填充值是否已经可见。

- `shift` / `f15` / `f16` / `tab` / `escape` 都是**无副作用**的：不输入字符、不触发表单默认行为，纯粹用于让 Chrome 认定"发生了真实用户交互"。
- `enter` 是最后手段：它会**同时触发页面自己的 `keyLogin()`**，所以走到这一步之后就不再调用 `doLogin()`，避免重复提交。

如果默认顺序在你的环境里不生效，把有效的那个挪到最前面即可减少延迟。

### 投递方式 — 请求里的 `delivery`

| 值 | 行为 | 取舍 |
|---|---|---|
| `pid`（默认） | `CGEvent.postToPid(chromePid)` | 直接进 Chrome 进程事件队列，焦点漂移也不会误伤别的 App |
| `hid` | `CGEvent.post(tap: .cghidEventTap)` | 进入系统 HID 事件流，与真实硬件完全一致；但会发给当前焦点窗口 |

若怀疑 Chrome 对 `postToPid` 的事件信任度不足，可在 `background.js` 的 `sendNative({...})` 里加上 `delivery: 'hid'` 对比验证。

### 环境变量

| 变量 | 作用 |
|---|---|
| `THU_AUTOLOGIN_SUPPORT_DIR` | 覆盖 socket / 二进制所在目录（测试用） |
| `THU_AUTOLOGIN_NO_PROMPT=1` | 只查询权限、不弹 TCC 授权对话框（测试用） |

---

## 7. 疑难排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 菜单栏图标显示 `THU ⚠︎` | 未授予辅助功能权限 | 点图标 → 打开"辅助功能"设置并勾选 |
| 菜单栏没有 THU 图标 | LaunchAgent 没起来 | `launchctl print gui/$UID/com.thu.autologin.keyagent`；或 `open -a THUAutoLoginKeyAgent` |
| 扩展报 `Specified native messaging host not found` | 清单路径或扩展 ID 不匹配 | 重跑 `install.sh`；确认 JSON 里的 `allowed_origins` 是当前 ID |
| 扩展报 `Access to the specified native messaging host is forbidden` | `allowed_origins` 里的 ID 和实际扩展 ID 不一致 | 同上；**改动文件夹后必须重跑** |
| 按键发了但输入框仍是空的 | Chrome 未自动填充（没有保存的凭据），或焦点判断为假 | 先手动确认 Chrome 能弹出该站点的密码建议；再看阶梯是否走到 `enter` |
| 页面在后台时完全不动作 | **这是设计行为** | 切到该标签页即会立刻触发 |
| 日志 | — | `~/Library/Logs/THUAutoLogin/keyagent.log` |

查看主机 ① 是否收到了请求、以及主机 ② 是否在线：

```bash
ls -l ~/Library/Application\ Support/THUAutoLogin/keyagent.sock   # 应存在，权限 0600
```

---

## 8. 让"辅助功能"授权在重新编译后依然有效

`build.sh` 在没有找到代码签名证书时会退回 **ad-hoc 签名**。ad-hoc 签名由二进制哈希推导，所以**每次重新编译，macOS 都认为这是一个全新的 App**，之前的辅助功能授权会失效，需要重新勾选。

解决办法是用一张稳定的证书签名，一次性配置：

1. 打开 **钥匙串访问** → 菜单 **钥匙串访问 → 证书助理 → 创建证书…**
2. 名称：`THUAutoLogin Dev`
   身份类型：**自签名根证书**
   证书类型：**代码签名**
3. 创建后重新构建：

```bash
CODESIGN_IDENTITY="THUAutoLogin Dev" ./build.sh
```

此后 designated requirement 只依赖 bundle id 和证书，重新编译不会再重置授权。

---

## 9. 卸载

```bash
./uninstall.sh            # 保留 ~/Library/Application Support/THUAutoLogin
./uninstall.sh --purge    # 一并删除 socket、二进制与日志
```

有一件事脚本做不了：`系统设置 → 隐私与安全性 → 辅助功能` 里可能残留 `THUAutoLoginKeyAgent` 条目，需要手动选中并按减号删除。另外建议重启 Chrome，让它忘掉已卸载的原生消息主机。

---

## 10. 安全说明

- 主机 ② 的 socket 权限为 `0600`，只有当前用户能连接，且只接受 `ping` / `unlock` 两种消息。
- 主机 ② 的 `unlock` **只接受白名单按键策略**（`shift` / `f15` / `f16` / `tab` / `escape` / `enter`），不能发送任意按键或任意文本，因此无法被用来注入密码或命令。
- 它不读取、不记录、不传输任何凭据；凭据始终只存在于 Chrome 的自动填充与页面 DOM 中。
- 扩展只在 `id.tsinghua.edu.cn` / `learn.tsinghua.edu.cn` 上运行，且仅在登录表单页触发原生调用。
