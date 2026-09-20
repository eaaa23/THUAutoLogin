# THU Auto Login

自动点击清华大学几个站点上登录相关按钮 / 链接的 Chrome 扩展，支持 **macOS** 与 **Windows**。

| 站点 | 触发条件 | 动作 |
|---|---|---|
| `learn.tsinghua.edu.cn/f/login` | 始终 | 点击 `#loginButtonId` |
| `learn.tsinghua.edu.cn/f/wlxt/index/course/student` | 始终 | 点击 `.chongxin`（"重新登录"） |
| `id.tsinghua.edu.cn/do/off/ui/auth/login/form/*` | 页面有焦点 | 通过原生主机注入**真实按键**解锁 Chrome 自动填充，再调用页面 `doLogin()` |
| `info.tsinghua.edu.cn/*` | 存在**可见**的 `span.dehmil`（即未登录） | 点击该登录按钮 |

---

## 为什么需要原生主机

登录 `id.tsinghua.edu.cn` 时，Chrome 已经把保存的账号密码自动填充进了 `#i_user` / `#i_pass`，但在**页面收到一次真实的用户交互之前，不会把这些值暴露给 JavaScript**——所以脚本看到的是两个空输入框。

没有任何扩展 API 能伪造这种事件。旧实现用 `chrome.debugger.attach()` + CDP 注入按键，代价是 Chrome 会常驻一条"正在调试此浏览器"横幅，且无法隐藏。

现在的做法是把"可信按键"的来源换成**操作系统本身**：由原生主机注入 OS 级键盘事件，渲染器无法区分它与真实硬件。于是不需要 debugger，也就没有横幅。

---

## 架构

```
                    ┌──────────────────────────────────────────┐
                    │  THUAutoLogin/   扩展（两端共享，零平台代码）│
                    │                                          │
   登录页 ──────────►  content.js   规则匹配 + document.hasFocus() 把关
                    │      │                                   │
                    │      ▼                                   │
                    │  background.js  解锁阶梯 + 调用 doLogin() │
                    └──────┬───────────────────────────────────┘
                           │ chrome.runtime.connectNative('com.thu.autologin.host')
                           │ 4 字节长度前缀 + UTF-8 JSON（Chrome native messaging）
                           ▼
        ┌──────────────────────────────┬──────────────────────────────┐
        │  macOS（两个原生组件）        │  Windows（一个原生组件）      │
        ├──────────────────────────────┼──────────────────────────────┤
        │ 主机① thu-autologin-host     │ thu_autologin_host.py        │
        │   无任何权限，纯转发          │   win32gui 找 Chrome 主窗口   │
        │        │ Unix domain socket  │   win32api.keybd_event 注入   │
        │        ▼                     │                              │
        │ 主机② THUAutoLoginKeyAgent   │                              │
        │   持有"辅助功能"权限          │                              │
        │   CGEvent.postToPid          │                              │
        └──────────────────────────────┴──────────────────────────────┘
                           │
                           ▼
        Chrome 渲染器认定为真实用户输入 → 自动填充值对 JS 可见
                           │
                           └──► background.js 轮询到有值 → 调用 doLogin()
```

### 为什么 macOS 要两个组件、Windows 只要一个

macOS 的 TCC（隐私权限）按 **responsible process** 归属。原生主机是 Chrome 的子进程，直接给它"辅助功能"权限等于**给 Chrome 本身授权**——既不安全、身份也不稳定。所以 macOS 必须另起一个由 launchd 拉起的 `.app` 来持有权限。

Windows 没有这个机制：Chrome 启动的进程可以直接调用 `keybd_event` 注入输入，不需要针对二进制的授权。因此 Windows 只需一个原生主机。

### 两端保持一致的部分

正是为了让扩展**一行都不用改**，Windows 主机刻意对齐了 macOS 的对外契约：

| 项目 | 值 |
|---|---|
| 主机名 | `com.thu.autologin.host` |
| 请求 | `{"type": "unlock", "strategy": "tab", "requestId": "..."}` |
| 响应 | `{"ok": true, ...}` / `{"ok": false, "error": "..."}` |
| 按键白名单 | `tab` / `f15` / `f16` / `escape` / `enter` / `shift` |
| 焦点策略 | 页面无焦点则不发送；主机侧再校验一次 |

**按键白名单是主机能发出的全部键盘能力**——它无法输入任何字符，所以不可能被用来注入密码或命令。两端都不读取、不记录、不传输任何凭据。

---

## 目录结构

```
AutoLogin/
├── THUAutoLogin/          Chrome 扩展（两端共享）
│   ├── manifest.json      nativeMessaging 权限；已移除 debugger
│   ├── background.js      原生消息通道 + 解锁阶梯 + 调用 doLogin()
│   └── content.js         页面规则 + 焦点把关
├── MacOS/                 macOS 实现
│   ├── shared/            两个主机共用的 socket / 分帧工具
│   ├── native-host/       主机①：native messaging host
│   ├── key-agent/         主机②：菜单栏 App（持有辅助功能权限）
│   ├── build.sh  install.sh  uninstall.sh
│   └── README.md          详细的 macOS 文档
└── Windows/               Windows 实现
    ├── native-host/
    │   ├── thu_autologin_host.py   唯一原生主机
    │   ├── install.py  uninstall.py
    │   └── requirements.txt
    └── README.md          详细的 Windows 文档
```

---

## 快速开始

### macOS

```bash
cd MacOS
./build.sh      # 需要 Xcode Command Line Tools
./install.sh    # 安装到用户目录，无需 sudo
```

随后：**系统设置 → 隐私与安全性 → 辅助功能** 勾选 `THUAutoLoginKeyAgent`；在 `chrome://extensions` 重新加载扩展。详见 [`MacOS/README.md`](MacOS/README.md)。

### Windows

```powershell
python -m pip install pywin32
python Windows\native-host\install.py
```

随后**重启 Chrome**，并在 `chrome://extensions` 确认扩展已加载。详见 [`Windows/README.md`](Windows/README.md)。

### 扩展 ID 会因平台而异

Chrome 用**未打包扩展目录绝对路径的 SHA-256** 推导扩展 ID，所以同一份扩展在 macOS 与 Windows 上的 ID **不同**，两端的安装脚本各自负责把它写进原生消息清单：

- macOS `install.sh`：由路径计算；
- Windows `install.py`：**直接读 Chrome 配置**取出 ID（更可靠），读不到才回退到计算。

移动扩展目录会改变 ID，需要重跑对应的安装脚本。

---

## 解锁阶梯（两端共用）

```js
const UNLOCK_LADDER = [
  { strategy: 'tab',   pollMs: 1000 },
  { strategy: 'f15',   pollMs: 1000 },
  { strategy: 'enter', pollMs: 4000, submitsPage: true },
];
```

逐级尝试，每级之后轮询确认自动填充值是否已对 JS 可见；走到 `enter` 时它会**同时触发页面自身的 `keyLogin()`**，因此那一步之后不再调用 `doLogin()`，避免重复提交。

> `shift` 已实测排除：单独按修饰键只产生 `keydown`/`keyup`、不改变输入框内容，Chrome 不认为发生了"编辑"，**无法**解锁自动填充。

如果默认顺序在你的环境里不生效，把有效的那个挪到最前面即可。
