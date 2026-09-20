// THU Auto Login — native messaging host (host #1).
//
// Chrome spawns this process as a child when the extension calls
// runtime.connectNative(). It speaks the Chrome native-messaging wire format on
// stdin/stdout (4-byte native-endian length prefix + JSON) and forwards each
// request to the key agent app over a Unix domain socket.
//
// It needs no TCC permissions of its own: it cannot post events, and it does not
// try. That separation is deliberate — a helper spawned by Chrome inherits
// Chrome's TCC "responsible process", so granting it Accessibility would mean
// granting Accessibility to Chrome itself.

import Foundation

let maxMessageSize = 64 * 1024 * 1024

/* ------------------------------------------------------------------ *
 * Chrome native messaging framing                                     *
 * ------------------------------------------------------------------ */

func readExactly(_ fd: Int32, _ count: Int) -> Data? {
    var buffer = [UInt8](repeating: 0, count: count)
    var offset = 0
    while offset < count {
        let n = buffer.withUnsafeMutableBytes { raw -> Int in
            guard let base = raw.baseAddress else { return -1 }
            return read(fd, base.advanced(by: offset), count - offset)
        }
        if n < 0 {
            if errno == EINTR { continue }
            return nil
        }
        if n == 0 { return nil } // EOF
        offset += n
    }
    return Data(buffer)
}

func readMessage() -> [String: Any]? {
    guard let header = readExactly(STDIN_FILENO, 4) else { return nil }
    let raw = header.withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }
    let size = Int(UInt32(littleEndian: raw))
    guard size > 0, size <= maxMessageSize else { return nil }
    guard let body = readExactly(STDIN_FILENO, size) else { return nil }
    return (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
}

func writeMessage(_ object: [String: Any]) {
    guard let body = try? JSONSerialization.data(withJSONObject: object) else { return }
    var length = UInt32(body.count).littleEndian
    var out = Data(bytes: &length, count: 4)
    out.append(body)
    _ = writeAll(fd: STDOUT_FILENO, out)
}

/* ------------------------------------------------------------------ *
 * Forwarding to the key agent                                         *
 * ------------------------------------------------------------------ */

enum RoundTrip {
    case response([String: Any])
    case unreachable
    case failed(String)
}

func roundTrip(_ request: [String: Any], timeout: TimeInterval = 3.0) -> RoundTrip {
    guard let fd = connectUnixSocket(path: WirePaths.socketURL.path, timeout: 1.0) else {
        return .unreachable
    }
    defer { close(fd) }
    setReadTimeout(fd, timeout)

    guard var body = try? JSONSerialization.data(withJSONObject: request) else {
        return .failed("encode-failed")
    }
    body.append(0x0A) // newline-delimited JSON

    guard writeAll(fd: fd, body) else { return .failed("write-failed") }
    guard let line = readLine(fd: fd), !line.isEmpty else { return .failed("no-response") }
    guard let object = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else {
        return .failed("bad-response")
    }
    return .response(object)
}

func launchAgent() {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
    // -g: open without bringing the app to the foreground.
    process.arguments = ["-g", "-b", WirePaths.agentBundleID]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
        process.waitUntilExit()
    } catch {
        // Nothing else to do; the caller reports the agent as unreachable.
    }
}

func forwardToAgent(_ request: [String: Any]) -> [String: Any] {
    switch roundTrip(request) {
    case .response(let object):
        return object

    case .failed(let reason):
        // The agent accepted the connection, so re-sending could deliver the
        // keystroke twice. Report the failure instead of retrying.
        return ["ok": false, "error": reason]

    case .unreachable:
        // Not running yet: start it and try exactly once more.
        launchAgent()
        Thread.sleep(forTimeInterval: 1.2)
        switch roundTrip(request) {
        case .response(let object): return object
        case .failed(let reason): return ["ok": false, "error": reason]
        case .unreachable: return ["ok": false, "error": "key-agent-unreachable"]
        }
    }
}

func handle(_ message: [String: Any]) -> [String: Any] {
    let type = message["type"] as? String ?? ""
    let requestId = message["requestId"] as? String

    var response: [String: Any]

    if type == "ping" {
        let reachable: Bool
        if case .response = roundTrip(["type": "ping"], timeout: 2.0) {
            reachable = true
        } else {
            reachable = false
        }
        response = ["ok": true, "pong": true, "agentReachable": reachable]
    } else {
        response = forwardToAgent(message)
    }

    if let requestId { response["requestId"] = requestId }
    return response
}

while let message = readMessage() {
    writeMessage(handle(message))
}
