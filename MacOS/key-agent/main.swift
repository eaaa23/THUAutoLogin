// THU Auto Login — key agent (host #2).
//
// A menu-bar app, started at login, that owns the Accessibility (TCC) grant and
// posts real Quartz keyboard events into the Google Chrome process.
//
// Why a separate app instead of doing this in the native messaging host: Chrome
// spawns that host as its child, and macOS attributes TCC requests to the
// "responsible process" — which would be Chrome. Granting the helper
// Accessibility would therefore mean granting Chrome Accessibility. Running as
// an independent bundle launched by launchd gives this agent its own identity.
//
// It listens on a Unix domain socket and speaks newline-delimited JSON.

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

/* ------------------------------------------------------------------ *
 * Accessibility                                                       *
 * ------------------------------------------------------------------ */

func isAccessibilityTrusted(prompt: Bool) -> Bool {
    // The literal key avoids the Unmanaged<CFString> import dance for
    // kAXTrustedCheckOptionPrompt, whose value is exactly this string.
    let options = ["AXTrustedCheckOptionPrompt": prompt] as NSDictionary
    return AXIsProcessTrustedWithOptions(options as CFDictionary)
}

/// Set THU_AUTOLOGIN_NO_PROMPT=1 to check trust without ever showing the TCC
/// dialog. Useful for smoke tests and for the extension's health probe.
let suppressPrompt = ProcessInfo.processInfo.environment["THU_AUTOLOGIN_NO_PROMPT"] == "1"

/// Checks trust, and raises the "grant Accessibility" dialog when it is missing.
func requestAccessibility() -> Bool {
    isAccessibilityTrusted(prompt: !suppressPrompt)
}

/* ------------------------------------------------------------------ *
 * Chrome discovery                                                    *
 * ------------------------------------------------------------------ */

// Chromium browsers this agent can drive, ordered by preference so a stable
// build wins over beta/canary. Edge is a first-class target, not a special
// case: it uses the same Chromium autofill, it merely needs the Tab pressed
// twice, which the extension handles.
let browserBundleIDs: [String] = [
    "com.google.Chrome",
    "com.google.Chrome.beta",
    "com.google.Chrome.dev",
    "com.google.Chrome.canary",
    "com.microsoft.edgemac",
    "com.microsoft.edgemac.Beta",
    "com.microsoft.edgemac.Dev",
    "com.microsoft.edgemac.Canary",
    "org.chromium.Chromium",
]

struct BrowserInstance {
    let app: NSRunningApplication

    var pid: pid_t { app.processIdentifier }
    var name: String { app.localizedName ?? "Chromium browser" }
    var active: Bool { app.isActive }
}

/// Finds the Chrome instance the user is actually looking at.
///
/// This deliberately uses the bundle-id query instead of filtering
/// `NSWorkspace.runningApplications`. Reading `bundleIdentifier` off every
/// running app costs a *synchronous XPC round-trip to LaunchServices each*
/// (`_LSCopyApplicationInformation` -> `xpc_connection_send_message_with_reply_sync`),
/// which is what kept the idle CPU above zero. Asking by bundle id does one
/// lookup per candidate instead of one per running process.
func findBrowser() -> BrowserInstance? {
    var candidates: [NSRunningApplication] = []
    for bundleID in browserBundleIDs {
        candidates = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
        if !candidates.isEmpty { break }
    }
    guard !candidates.isEmpty else { return nil }

    let chosen = candidates.first { $0.isActive }
        ?? candidates.first { $0.activationPolicy == .regular }
        ?? candidates[0]
    return BrowserInstance(app: chosen)
}

/* ------------------------------------------------------------------ *
 * Extension ID verification / repair                                  *
 *
 * The bundle id / manifest are guessed at build time from the extension's
 * install path. This reads back the ID the browser ACTUALLY computed and, if
 * the manifest disagrees, rewrites the per-user manifest — which is both the
 * location Chrome consults first and the one this agent can write without root.
 * ------------------------------------------------------------------ */

let HOST_NAME = "com.thu.autologin.host"
let EXTENSION_UNPACKED_LOCATION = 4

struct LoadedExtension {
    let browser: String
    let profile: String
    let id: String
}

/// Chrome extension IDs are 32 characters drawn from a-p. Anything else in a
/// preferences file is not an ID we should ever write into a manifest.
func isValidExtensionId(_ value: String) -> Bool {
    guard value.count == 32 else { return false }
    return value.allSatisfy { $0 >= "a" && $0 <= "p" }
}

/// Home directory used to locate browser profiles and manifests.
///
/// THU_AUTOLOGIN_HOME overrides it, so this logic can be exercised without
/// reading or writing the real Chrome/Edge profile (same idea as the existing
/// THU_AUTOLOGIN_SUPPORT_DIR / THU_AUTOLOGIN_NO_PROMPT knobs).
func browserHome() -> URL {
    let environment = ProcessInfo.processInfo.environment
    if let override = environment["THU_AUTOLOGIN_HOME"], !override.isEmpty {
        return URL(fileURLWithPath: override, isDirectory: true)
    }
    return FileManager.default.homeDirectoryForCurrentUser
}

/// Where this install put things. The system (.pkg) layout wins when present.
func installedLayout() -> (extensionDir: URL, hostPath: String)? {
    let home = browserHome()
    let bases = [
        URL(fileURLWithPath: "/Library/Application Support/THUAutoLogin"),
        home.appendingPathComponent("Library/Application Support/THUAutoLogin"),
    ]
    for base in bases {
        let extensionDir = base.appendingPathComponent("extension")
        if FileManager.default.fileExists(atPath: extensionDir.path) {
            return (extensionDir, base.appendingPathComponent("bin/thu-autologin-host").path)
        }
    }
    return nil
}

func browserUserDataRoots() -> [(String, URL)] {
    let home = browserHome()
    let support = home.appendingPathComponent("Library/Application Support")
    let all: [(String, URL)] = [
        ("Chrome", support.appendingPathComponent("Google/Chrome")),
        ("Chrome Beta", support.appendingPathComponent("Google/Chrome Beta")),
        ("Chrome Canary", support.appendingPathComponent("Google/Chrome Canary")),
        ("Edge", support.appendingPathComponent("Microsoft Edge")),
        ("Edge Beta", support.appendingPathComponent("Microsoft Edge Beta")),
        ("Chromium", support.appendingPathComponent("Chromium")),
    ]
    return all.filter { FileManager.default.fileExists(atPath: $0.1.path) }
}

/// The IDs the browsers themselves recorded for the unpacked extension at
/// `extensionDir`. Authoritative: no path-hashing assumptions involved.
func loadedExtensions(at extensionDir: URL) -> [LoadedExtension] {
    let target = (extensionDir.path as NSString).standardizingPath
    var found: [LoadedExtension] = []

    for (browserName, root) in browserUserDataRoots() {
        let profiles = (try? FileManager.default.contentsOfDirectory(
            at: root, includingPropertiesForKeys: [.isDirectoryKey]
        )) ?? []

        for profileDir in profiles {
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: profileDir.path, isDirectory: &isDirectory),
                  isDirectory.boolValue else { continue }

            for fileName in ["Secure Preferences", "Preferences"] {
                let fileURL = profileDir.appendingPathComponent(fileName)
                guard let data = try? Data(contentsOf: fileURL),
                      let parsed = try? JSONSerialization.jsonObject(with: data),
                      let top = parsed as? [String: Any],
                      let extensions = top["extensions"] as? [String: Any],
                      let settings = extensions["settings"] as? [String: Any]
                else { continue }

                for (identifier, value) in settings {
                    guard let entry = value as? [String: Any],
                          (entry["location"] as? Int) == EXTENSION_UNPACKED_LOCATION,
                          let recorded = entry["path"] as? String,
                          isValidExtensionId(identifier)
                    else { continue }

                    let candidate = recorded.hasPrefix("/")
                        ? recorded
                        : profileDir.appendingPathComponent(recorded).path
                    if (candidate as NSString).standardizingPath == target {
                        found.append(LoadedExtension(
                            browser: browserName,
                            profile: profileDir.lastPathComponent,
                            id: identifier
                        ))
                    }
                }
            }
        }
    }
    return found
}

struct ManifestSummary {
    let origins: [String]
    let hostPath: String
}

func manifestSummary(at url: URL) -> ManifestSummary? {
    guard let data = try? Data(contentsOf: url),
          let parsed = try? JSONSerialization.jsonObject(with: data),
          let top = parsed as? [String: Any],
          let origins = top["allowed_origins"] as? [String],
          let hostPath = top["path"] as? String
    else { return nil }
    return ManifestSummary(origins: origins, hostPath: hostPath)
}

func userManifestURL() -> URL {
    browserHome()
        .appendingPathComponent("Library/Application Support/Google/Chrome/NativeMessagingHosts", isDirectory: true)
        .appendingPathComponent("\(HOST_NAME).json")
}

func systemManifestURL() -> URL {
    URL(fileURLWithPath: "/Library/Google/Chrome/NativeMessagingHosts/\(HOST_NAME).json")
}

func writeUserManifest(hostPath: String, extensionId: String) throws -> URL {
    let url = userManifestURL()
    try FileManager.default.createDirectory(
        at: url.deletingLastPathComponent(), withIntermediateDirectories: true
    )
    let manifest: [String: Any] = [
        "name": HOST_NAME,
        "description": "THU Auto Login native messaging host",
        "path": hostPath,
        "type": "stdio",
        "allowed_origins": ["chrome-extension://\(extensionId)/"],
    ]
    let data = try JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted])
    try data.write(to: url)
    return url
}

/// Check the installed manifest against what the browser actually loaded, and
/// repair it when they disagree. Returns a human-readable report.
func verifyAndRepairExtensionId() -> String {
    guard let layout = installedLayout() else {
        return """
        没有找到已安装的扩展目录。

        请先安装（MacInstaller 的 .pkg，或 MacOS/install.sh）。
        """
    }

    let loaded = loadedExtensions(at: layout.extensionDir)
    guard let authoritative = loaded.first else {
        return """
        未在 Chrome / Edge 的配置里找到该扩展。

        请确认已经加载了这个文件夹：
            \(layout.extensionDir.path)

        chrome://extensions → 开发者模式 → 加载已解压的扩展程序。
        加载完成后保持浏览器运行，再点一次本项。
        """
    }

    let expected = "chrome-extension://\(authoritative.id)/"
    let user = manifestSummary(at: userManifestURL())
    let system = manifestSummary(at: systemManifestURL())

    // Chrome consults the per-user manifest first, so that is the one that
    // decides. (A stale per-user manifest shadowing a correct system one is
    // exactly the failure this exists to clear.)
    let effectiveName = user != nil ? "用户级" : (system != nil ? "系统级" : nil)
    let effective = user ?? system

    let source = "\(authoritative.browser) · \(authoritative.profile)"
    let others = loaded.dropFirst().map { "\($0.browser) · \($0.profile)" }.joined(separator: "、")
    var report = "浏览器实际使用的扩展 ID：\(authoritative.id)\n来源：\(source)"
    if !others.isEmpty { report += "\n其它位置也加载了同一扩展：\(others)" }

    // Both halves matter: a manifest can name the right extension but point at
    // a host binary that is not there (a leftover from the other install
    // layout), and the extension still will not connect.
    if let effective, effective.origins.contains(expected), effective.hostPath == layout.hostPath {
        return "扩展 ID 与主机路径都正确，无需修复。\n\n" + report
    }

    var reason = ""
    if let effective {
        let originsOK = effective.origins.contains(expected)
        let pathOK = effective.hostPath == layout.hostPath
        if !originsOK && !pathOK {
            reason = "\(effectiveName ?? "")清单里的扩展 ID 和主机路径都不对，已覆盖。"
        } else if !originsOK {
            reason = "\(effectiveName ?? "")清单里的扩展 ID 与浏览器不一致，已覆盖。"
        } else {
            reason = "\(effectiveName ?? "")清单指向的主机路径不存在：\(effective.hostPath)，已覆盖。"
        }
    } else {
        reason = "没有找到原生消息清单，已写入用户级清单。"
    }

    do {
        let url = try writeUserManifest(hostPath: layout.hostPath, extensionId: authoritative.id)
        let out = "已修复。\n\n" + report + "\n\n" + reason
            + "\n\n已写入：\n\(url.path)\n\n请重启浏览器使其生效。"
        return out
    } catch {
        return "修复失败：\(error.localizedDescription)\n\n请手动检查：\n\(userManifestURL().path)"
    }
}

/* ------------------------------------------------------------------ *
 * Key posting                                                         *
 * ------------------------------------------------------------------ */

enum KeyStrategy: String, CaseIterable {
    // Every entry except `enter` is deliberately side-effect free: no text is
    // inserted and no default action fires. They exist purely to make Chrome
    // treat the page as user-driven, which is what reveals autofilled values.
    case shift, f15, f16, tab, escape, enter

    var keyCode: CGKeyCode {
        switch self {
        case .shift: return 0x38  // kVK_Shift
        case .f15: return 0x71    // kVK_F15
        case .f16: return 0x6A    // kVK_F16
        case .tab: return 0x30    // kVK_Tab
        case .escape: return 0x35 // kVK_Escape
        case .enter: return 0x24  // kVK_Return
        }
    }

    func flags(keyDown: Bool) -> CGEventFlags {
        switch self {
        case .shift: return keyDown ? .maskShift : []
        default: return []
        }
    }
}

enum Delivery: String {
    /// Post straight into the Chrome process's event queue. Cannot leak into
    /// another app if focus moves between our check and the keystroke.
    case pid
    /// Post into the system-wide HID event stream, exactly like real hardware.
    /// Highest fidelity to a genuine keypress, but goes to whatever has focus.
    case hid
}

@discardableResult
func postKey(pid: pid_t, strategy: KeyStrategy, delivery: Delivery) -> Bool {
    guard let source = CGEventSource(stateID: .hidSystemState) else { return false }
    let code = strategy.keyCode

    for keyDown in [true, false] {
        guard let event = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: keyDown) else {
            return false
        }
        event.flags = strategy.flags(keyDown: keyDown)
        switch delivery {
        case .pid: event.postToPid(pid)
        case .hid: event.post(tap: .cghidEventTap)
        }
        usleep(15_000)
    }
    return true
}

/* ------------------------------------------------------------------ *
 * Request handling                                                    *
 * ------------------------------------------------------------------ */

func browserDescription() -> Any {
    guard let browser = findBrowser() else { return NSNull() }
    return ["pid": Int(browser.pid), "name": browser.name, "active": browser.active]
}

func pingResponse() -> [String: Any] {
    [
        "ok": true,
        "pong": true,
        "accessibility": isAccessibilityTrusted(prompt: false),
        "strategies": KeyStrategy.allCases.map { $0.rawValue },
        "browser": browserDescription(),
    ]
}

func performUnlock(_ request: [String: Any]) -> [String: Any] {
    guard requestAccessibility() else {
        return ["ok": false, "error": "no-accessibility"]
    }
    guard let strategy = KeyStrategy(rawValue: (request["strategy"] as? String) ?? "shift") else {
        return ["ok": false, "error": "unknown-strategy"]
    }
    guard let browser = findBrowser() else {
        return ["ok": false, "error": "browser-not-running"]
    }

    let delivery = Delivery(rawValue: (request["delivery"] as? String) ?? "pid") ?? .pid

    // Escape hatch only: the extension normally refuses to ask while the page is
    // unfocused, so we do not steal focus by default.
    if (request["activate"] as? Bool) == true, !browser.active {
        browser.app.activate()
        usleep(150_000)
    }

    guard postKey(pid: browser.pid, strategy: strategy, delivery: delivery) else {
        return ["ok": false, "error": "post-failed"]
    }

    return [
        "ok": true,
        "strategy": strategy.rawValue,
        "delivery": delivery.rawValue,
        "pid": Int(browser.pid),
    ]
}

func handleRequest(_ request: [String: Any]) -> [String: Any] {
    switch request["type"] as? String ?? "" {
    case "ping":
        return pingResponse()
    case "unlock":
        return performUnlock(request)
    default:
        return ["ok": false, "error": "unknown-type"]
    }
}

/* ------------------------------------------------------------------ *
 * Socket server                                                       *
 * ------------------------------------------------------------------ */

final class SocketServer {
    static let shared = SocketServer()

    private var listenFD: Int32 = -1
    /// Serializes request handling so keystrokes from concurrent clients cannot interleave.
    private let handlerQueue = DispatchQueue(label: "com.thu.autologin.keyagent.handler")

    func start() {
        let thread = Thread { [weak self] in self?.serve() }
        thread.name = "keyagent-socket"
        thread.stackSize = 1 << 20
        thread.start()
    }

    private func serve() {
        do {
            try FileManager.default.createDirectory(
                at: WirePaths.supportDir, withIntermediateDirectories: true
            )
        } catch {
            NSLog("[keyagent] cannot create \(WirePaths.supportDir.path): \(error)")
            exit(1)
        }

        // If somebody is already listening, another instance owns the socket.
        if let live = connectUnixSocket(path: WirePaths.socketURL.path, timeout: 0.3) {
            close(live)
            NSLog("[keyagent] another instance is already listening; exiting")
            exit(0)
        }

        unlink(WirePaths.socketURL.path)

        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            NSLog("[keyagent] socket() failed: errno \(errno)")
            exit(1)
        }

        guard var addr = makeSockaddr(WirePaths.socketURL.path) else {
            NSLog("[keyagent] socket path too long: \(WirePaths.socketURL.path)")
            exit(1)
        }

        let bindResult = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bindResult == 0 else {
            NSLog("[keyagent] bind() failed for \(WirePaths.socketURL.path): errno \(errno)")
            close(fd)
            exit(1)
        }

        // Only this user's processes may drive the agent.
        chmod(WirePaths.socketURL.path, 0o600)

        guard listen(fd, 16) == 0 else {
            NSLog("[keyagent] listen() failed: errno \(errno)")
            close(fd)
            exit(1)
        }

        listenFD = fd
        NSLog("[keyagent] listening on \(WirePaths.socketURL.path)")

        while true {
            let client = accept(fd, nil, nil)
            if client < 0 {
                if errno == EINTR { continue }
                usleep(50_000)
                continue
            }
            // One thread per connection so a client that lingers can never block
            // another request behind it.
            let thread = Thread { [weak self] in self?.handleClient(client) }
            thread.stackSize = 1 << 19
            thread.start()
        }
    }

    private func handleClient(_ client: Int32) {
        defer { close(client) }
        setReadTimeout(client, 60.0)

        while true {
            guard let line = readLine(fd: client), !line.isEmpty else { return }

            guard let request = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else {
                _ = writeLine(fd: client, ["ok": false, "error": "bad-request"])
                return
            }

            // Serialized: two concurrent unlocks must never interleave keys.
            var response = handlerQueue.sync { handleRequest(request) }
            if let requestId = request["requestId"] { response["requestId"] = requestId }

            guard writeLine(fd: client, response) else { return }
        }
    }

    func shutdown() {
        if listenFD >= 0 { close(listenFD) }
        unlink(WirePaths.socketURL.path)
    }
}

/* ------------------------------------------------------------------ *
 * Menu-bar app                                                        *
 * ------------------------------------------------------------------ */

final class AgentDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem?
    private var statusLine: NSMenuItem?

    // Last rendered values, so AppKit is only touched when something changed.
    private var lastIconTitle: String?
    private var lastStatusText: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        SocketServer.shared.start()
        buildMenuBar()
        observeWorkspace()
        // Ask once at first launch so the app shows up in the Accessibility list.
        _ = requestAccessibility()
    }

    func applicationWillTerminate(_ notification: Notification) {
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        NotificationCenter.default.removeObserver(self)
        SocketServer.shared.shutdown()
    }

    /// Refresh on events instead of on a timer. A 3-second poll meant a
    /// synchronous LaunchServices XPC round-trip plus a menu-bar relayout every
    /// tick, which is exactly the idle CPU that showed up in the profile.
    private func observeWorkspace() {
        let workspaceCenter = NSWorkspace.shared.notificationCenter
        for name in [
            NSWorkspace.didLaunchApplicationNotification,
            NSWorkspace.didTerminateApplicationNotification,
            NSWorkspace.didActivateApplicationNotification,
        ] {
            workspaceCenter.addObserver(
                self, selector: #selector(workspaceChanged), name: name, object: nil
            )
        }
        // Catches the user coming back from System Settings after granting
        // Accessibility.
        NotificationCenter.default.addObserver(
            self, selector: #selector(workspaceChanged),
            name: NSApplication.didBecomeActiveNotification, object: nil
        )
    }

    @objc private func workspaceChanged(_ notification: Notification) {
        refreshStatus()
    }

    func menuWillOpen(_ menu: NSMenu) {
        // The menu is only ever read while it is open, so this is the one place
        // the status text needs to be accurate.
        refreshStatus()
    }

    private func buildMenuBar() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "THU"
        item.button?.toolTip = "THU Auto Login 按键代理"

        let menu = NSMenu()

        let header = NSMenuItem(title: "THU Auto Login 按键代理", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)

        let status = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        status.isEnabled = false
        menu.addItem(status)
        statusLine = status

        menu.addItem(.separator())

        let selfTest = NSMenuItem(
            title: "自检（向 Chrome 发送一次 Tab）",
            action: #selector(runSelfTestFromMenu), keyEquivalent: ""
        )
        selfTest.target = self
        menu.addItem(selfTest)

        let repair = NSMenuItem(
            title: "校验 / 修复扩展 ID",
            action: #selector(verifyExtensionIdFromMenu), keyEquivalent: ""
        )
        repair.target = self
        menu.addItem(repair)

        let axSettings = NSMenuItem(
            title: "打开“辅助功能”设置…",
            action: #selector(openAccessibilitySettings), keyEquivalent: ""
        )
        axSettings.target = self
        menu.addItem(axSettings)

        let loginSettings = NSMenuItem(
            title: "打开“登录项”设置…",
            action: #selector(openLoginItemsSettings), keyEquivalent: ""
        )
        loginSettings.target = self
        menu.addItem(loginSettings)

        menu.addItem(.separator())

        let quit = NSMenuItem(title: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quit)

        menu.delegate = self
        item.menu = menu
        statusItem = item

        refreshStatus()
    }

    private func refreshStatus() {
        let trusted = isAccessibilityTrusted(prompt: false)
        var text = trusted ? "辅助功能权限：已授权" : "辅助功能权限：未授权 ⚠︎"

        if let browser = findBrowser() {
            text += "\nChrome：运行中 (pid \(browser.pid))"
        } else {
            text += "\nChrome：未运行"
        }

        let icon = trusted ? "THU" : "THU ⚠︎"

        // Assigning a status-item title triggers a menu-bar relayout and a
        // CoreAnimation commit even when the string is identical, so only write
        // when the rendered result actually differs.
        if text != lastStatusText {
            statusLine?.title = text
            lastStatusText = text
        }
        if icon != lastIconTitle {
            statusItem?.button?.title = icon
            lastIconTitle = icon
        }
    }

    @objc private func runSelfTestFromMenu() {
        let trusted = isAccessibilityTrusted(prompt: true)

        let alert = NSAlert()
        alert.messageText = "自检结果"

        guard trusted else {
            alert.informativeText = """
            辅助功能权限未授予。

            请在 系统设置 → 隐私与安全性 → 辅助功能 中勾选 “\(WirePaths.agentAppName)”，然后重试。
            """
            alert.alertStyle = .warning
            alert.addButton(withTitle: "打开系统设置")
            alert.addButton(withTitle: "关闭")
            NSApp.activate(ignoringOtherApps: true)
            if alert.runModal() == .alertFirstButtonReturn { openAccessibilitySettings() }
            return
        }

        guard let browser = findBrowser() else {
            alert.informativeText = "未找到正在运行的 Google Chrome 进程。"
            alert.alertStyle = .warning
            alert.addButton(withTitle: "好")
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
            return
        }

        let posted = postKey(pid: browser.pid, strategy: .tab, delivery: .pid)
        alert.informativeText = """
        辅助功能权限：已授权
        Chrome：\(browser.name) (pid \(browser.pid))，\(browser.active ? "前台" : "后台")
        发送 Tab：\(posted ? "成功" : "失败")

        若登录页已打开，页面应已获得“用户交互”，自动填充的账号密码随即对脚本可见。
        """
        alert.alertStyle = posted ? .informational : .warning
        alert.addButton(withTitle: "好")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    @objc private func verifyExtensionIdFromMenu() {
        let report = verifyAndRepairExtensionId()
        let alert = NSAlert()
        alert.messageText = "扩展 ID 校验"
        alert.informativeText = report
        alert.alertStyle = report.hasPrefix("已修复") || report.hasPrefix("扩展 ID 正确")
            ? .informational : .warning
        alert.addButton(withTitle: "好")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    @objc private func openAccessibilitySettings() {
        openSettings("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
    }

    @objc private func openLoginItemsSettings() {
        openSettings("x-apple.systempreferences:com.apple.LoginItems-Settings.extension")
    }

    private func openSettings(_ urlString: String) {
        guard let url = URL(string: urlString) else { return }
        NSWorkspace.shared.open(url)
    }
}

/* ------------------------------------------------------------------ *
 * Command line modes                                                  *
 * ------------------------------------------------------------------ */

func runSelfTestCLI() -> Int32 {
    print("== THU Auto Login key agent self-test ==")

    let trusted = isAccessibilityTrusted(prompt: true)
    print("辅助功能权限: \(trusted ? "已授权" : "未授权")")

    guard let browser = findBrowser() else {
        print("未找到正在运行的 Chrome 进程")
        return 2
    }
    print("Chrome: \(browser.name) pid=\(browser.pid) 前台=\(browser.active)")

    guard trusted else {
        print("请先在 系统设置 → 隐私与安全性 → 辅助功能 中勾选 \(WirePaths.agentAppName)")
        print("提示：直接在本终端运行可能让 TCC 把权限归给终端。")
        print("优先使用菜单栏图标的“自检”，或 `open -a \(WirePaths.agentAppName) --args --selftest`。")
        return 3
    }

    print("3 秒后向 Chrome 发送一次 Tab，请让登录页保持前台…")
    Thread.sleep(forTimeInterval: 3)

    let ok = postKey(pid: browser.pid, strategy: .tab, delivery: .pid)
    print(ok ? "已发送 Tab" : "发送失败")
    return ok ? 0 : 4
}

func runOnce(_ strategy: KeyStrategy, delivery: Delivery) -> Int32 {
    guard isAccessibilityTrusted(prompt: true) else {
        print("辅助功能权限未授予")
        return 3
    }
    guard let browser = findBrowser() else {
        print("未找到正在运行的 Chrome 进程")
        return 2
    }
    let ok = postKey(pid: browser.pid, strategy: strategy, delivery: delivery)
    print("strategy=\(strategy.rawValue) delivery=\(delivery.rawValue) pid=\(browser.pid) ok=\(ok)")
    return ok ? 0 : 4
}

let arguments = CommandLine.arguments

if arguments.contains("--selftest") {
    exit(runSelfTestCLI())
}

if arguments.contains("--verify-id") {
    print(verifyAndRepairExtensionId())
    exit(0)
}

if arguments.contains("--ping") {
    let payload = pingResponse()
    let data = (try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])) ?? Data()
    print(String(data: data, encoding: .utf8) ?? "{}")
    exit(0)
}

if let index = arguments.firstIndex(of: "--once"), index + 1 < arguments.count {
    guard let strategy = KeyStrategy(rawValue: arguments[index + 1]) else {
        print("未知按键策略：\(arguments[index + 1])")
        print("可选：\(KeyStrategy.allCases.map { $0.rawValue }.joined(separator: ", "))")
        exit(1)
    }
    let delivery: Delivery = arguments.contains("--hid") ? .hid : .pid
    exit(runOnce(strategy, delivery: delivery))
}

// Default: run as the menu-bar agent.
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let delegate = AgentDelegate()
application.delegate = delegate
application.run()
