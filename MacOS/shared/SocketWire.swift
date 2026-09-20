// Shared plumbing for the two native components.
//
// Compiled into both binaries: the native messaging host (client) and the key
// agent app (server) talk to each other over a Unix domain socket using
// newline-delimited JSON.

import Foundation

enum WirePaths {
    static let agentBundleID = "com.thu.autologin.keyagent"
    static let agentAppName = "THUAutoLoginKeyAgent"

    /// Where the socket lives. THU_AUTOLOGIN_SUPPORT_DIR overrides it, which is
    /// handy for smoke tests that must not touch the real support directory.
    static var supportDir: URL = {
        let environment = ProcessInfo.processInfo.environment
        if let override = environment["THU_AUTOLOGIN_SUPPORT_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/THUAutoLogin", isDirectory: true)
    }()

    static var socketURL: URL { supportDir.appendingPathComponent("keyagent.sock") }
}

func makeSockaddr(_ path: String) -> sockaddr_un? {
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    addr.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)

    let bytes = Array(path.utf8)
    let capacity = MemoryLayout.size(ofValue: addr.sun_path)
    guard bytes.count < capacity else { return nil }

    withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
        ptr.withMemoryRebound(to: CChar.self, capacity: capacity) { dst in
            for (i, b) in bytes.enumerated() { dst[i] = CChar(bitPattern: b) }
            dst[bytes.count] = 0
        }
    }
    return addr
}

/// Connects to a Unix domain socket, honouring a connect timeout.
/// Returns a blocking file descriptor, or nil if the socket is unreachable.
func connectUnixSocket(path: String, timeout: TimeInterval) -> Int32? {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { return nil }

    guard var addr = makeSockaddr(path) else {
        close(fd)
        return nil
    }

    let originalFlags = fcntl(fd, F_GETFL, 0)
    _ = fcntl(fd, F_SETFL, originalFlags | O_NONBLOCK)

    let rc = withUnsafePointer(to: &addr) { ptr in
        ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }

    if rc != 0 {
        guard errno == EINPROGRESS else {
            close(fd)
            return nil
        }
        var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
        guard poll(&pfd, 1, Int32(timeout * 1000)) > 0 else {
            close(fd)
            return nil
        }
        var soError: Int32 = 0
        var len = socklen_t(MemoryLayout<Int32>.size)
        guard getsockopt(fd, SOL_SOCKET, SO_ERROR, &soError, &len) == 0, soError == 0 else {
            close(fd)
            return nil
        }
    }

    _ = fcntl(fd, F_SETFL, originalFlags)
    return fd
}

func setReadTimeout(_ fd: Int32, _ seconds: TimeInterval) {
    var tv = timeval(
        tv_sec: Int(seconds),
        tv_usec: Int32((seconds - Double(Int(seconds))) * 1_000_000)
    )
    _ = withUnsafePointer(to: &tv) {
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size))
    }
}

/// Reads up to and including the next newline. Returns nil on EOF with no data.
func readLine(fd: Int32, maxBytes: Int = 1_048_576) -> Data? {
    var data = Data()
    var byte: UInt8 = 0
    while true {
        let n = read(fd, &byte, 1)
        if n < 0 {
            if errno == EINTR { continue }
            return data.isEmpty ? nil : data
        }
        if n == 0 { return data.isEmpty ? nil : data }
        if byte == 0x0A { return data }
        data.append(byte)
        if data.count > maxBytes { return nil }
    }
}

func writeAll(fd: Int32, _ data: Data) -> Bool {
    var ok = true
    data.withUnsafeBytes { raw in
        guard let base = raw.baseAddress else { return }
        var offset = 0
        while offset < raw.count {
            let n = write(fd, base.advanced(by: offset), raw.count - offset)
            if n < 0 {
                if errno == EINTR { continue }
                ok = false
                return
            }
            if n == 0 { ok = false; return }
            offset += n
        }
    }
    return ok
}

func writeLine(fd: Int32, _ object: [String: Any]) -> Bool {
    guard var body = try? JSONSerialization.data(withJSONObject: object) else { return false }
    body.append(0x0A)
    return writeAll(fd: fd, body)
}
