import Foundation
import Network
import Security
import AudayLink
import Darwin

func emit(_ value: Any) throws {
    var data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]); data.append(10); FileHandle.standardOutput.write(data)
}
let args = Array(CommandLine.arguments.dropFirst())
func option(_ key: String) -> String? { guard let i = args.firstIndex(of: key), i + 1 < args.count else { return nil }; return args[i + 1] }
func stdinObject() throws -> [String: Any] {
    guard let line = readLine(), let data = line.data(using: .utf8), data.count <= FrameConnection.maximum,
          let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw LinkError.message("invalid_stdin") }
    return object
}
let service = "ai.auday.workflow-link.experimental"
func localPassword() throws -> String {
    // getpass has terminal/signal restoration; disallow its insecure no-TTY fallback.
    let descriptor = open("/dev/tty", O_RDWR | O_CLOEXEC)
    guard descriptor >= 0 else { throw LinkError.message("pair_requires_controlling_local_terminal") }
    var attributes = termios()
    let readable = tcgetattr(descriptor, &attributes) == 0
    close(descriptor)
    guard readable else { throw LinkError.message("terminal_attributes_failed") }
    guard let pointer = getpass("Auday 通信密码（不回显）：") else { throw LinkError.message("password_input_cancelled") }
    let password = String(cString: pointer)
    memset(pointer, 0, strlen(pointer))
    _ = try CommunicationPassword.key(password)
    return password
}

func saveSecret(_ password: String, profile: String) throws {
    let key: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: profile]
    let status = SecItemUpdate(key as CFDictionary, [kSecValueData as String: Data(password.utf8)] as CFDictionary)
    if status == errSecItemNotFound {
        var item = key; item[kSecValueData as String] = Data(password.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw LinkError.message("keychain_save_failed") }
    } else if status != errSecSuccess { throw LinkError.message("keychain_save_failed") }
}
func readSecret(_ profile: String) throws -> String {
    let key: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
        kSecAttrAccount as String: profile, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
    var result: CFTypeRef?
    guard SecItemCopyMatching(key as CFDictionary, &result) == errSecSuccess, let data = result as? Data,
          let value = String(data: data, encoding: .utf8) else { throw LinkError.message("pair_on_local_terminal_first") }
    return value
}
func endpoint(_ input: [String: Any]) throws -> NWEndpoint {
    if let name = input["service"] as? String { return .service(name: name, type: "_auday-link._tcp", domain: "local.", interface: nil) }
    guard let host = input["host"] as? String, let port = input["port"] as? UInt16, port != 0 else { throw LinkError.message("missing_endpoint") }
    return .hostPort(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: port)!)
}

do {
    switch args.first {
    case "generate":
        // Do not invoke from an AI chat: this command is for a user's terminal only.
        print(try CommunicationPassword.generate())
    case "request":
        let input = try stdinObject()
        let password: String
        if let supplied = input["password"] as? String { password = supplied }
        else { password = try readSecret(input["profile"] as? String ?? "default") }
        guard let request = input["request"] as? [String: Any] else { throw LinkError.message("missing_request") }
        try emit(call(endpoint: try endpoint(input), password: password, request: request,
                      peerToPeer: input["peerToPeer"] as? Bool ?? false, timeout: input["timeout"] as? Double ?? 10))
    case "pair":
        guard isatty(STDIN_FILENO) == 1, let profile = option("--profile") else { throw LinkError.message("pair_requires_local_terminal_and_profile") }
        var destination: [String: Any] = [:]
        if let name = option("--service") { destination["service"] = name }
        else { destination["host"] = option("--host"); destination["port"] = UInt16(option("--port") ?? "") }
        let password = try localPassword()
        let response = try call(endpoint: endpoint(destination), password: password, request: ["op": "status"], peerToPeer: args.contains("--peer-to-peer"))
        guard response["ok"] as? Bool == true else { throw LinkError.message("pair_failed") }
        try saveSecret(password, profile: profile); try emit(response)
    case "discover":
        let parameters = NWParameters.tcp; parameters.includePeerToPeer = args.contains("--peer-to-peer")
        let browser = NWBrowser(for: .bonjour(type: "_auday-link._tcp", domain: "local."), using: parameters)
        let queue = DispatchQueue(label: "auday.discover"); var services: [String] = []
        browser.browseResultsChangedHandler = { results, _ in
            services = results.compactMap { if case .service(let name, _, _, _) = $0.endpoint { return name }; return nil }.sorted()
        }
        browser.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 3) { browser.cancel(); try? emit(["services": services, "authenticated": false]); exit(0) }
        dispatchMain()
    case nil, "--help", "help": print("auday-link discover [--peer-to-peer] | pair --profile NAME --service NAME | request (JSON stdin)")
    default: throw LinkError.message("unsupported_client_command")
    }
} catch { try? emit(["ok": false, "error": String(describing: error)]); exit(1) }
