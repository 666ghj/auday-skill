import Foundation
import Network
import Security
import CryptoKit

public enum LinkError: Error, CustomStringConvertible {
    case message(String)
    public var description: String { switch self { case .message(let value): return value } }
}

public enum CommunicationPassword {
    // 16 random Base32 symbols = 80 bits. Group separators are presentation only.
    // This remains a generated secret, NOT an arbitrary short human password/PAKE.
    public static let alphabet = Array("23456789ABCDEFGHJKLMNPQRSTUVWXYZ")
    public static func generate() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 16)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw LinkError.message("secure_random_failed")
        }
        return String(bytes.map { alphabet[Int($0 & 31)] })
    }
    public static func normalized(_ password: String) -> String {
        password.uppercased().filter { !$0.isWhitespace && $0 != "-" }
    }
    public static func formatted(_ password: String) -> String {
        let characters = Array(normalized(password))
        return stride(from: 0, to: characters.count, by: 4).map {
            String(characters[$0..<min($0 + 4, characters.count)])
        }.joined(separator: "-")
    }
    public static func key(_ password: String) throws -> Data {
        let text = normalized(password)
        guard text.count == 16, text.allSatisfy({ alphabet.contains($0) }), Set(text).count >= 4 else {
            throw LinkError.message("use_generated_16_character_communication_password")
        }
        let key = HKDF<SHA256>.deriveKey(inputKeyMaterial: SymmetricKey(data: Data(text.utf8)),
            salt: Data("auday-workflow-link-v2".utf8), info: Data("tls-psk".utf8), outputByteCount: 32)
        return key.withUnsafeBytes { Data($0) }
    }
}

private func trace(_ text: String) {
    if ProcessInfo.processInfo.environment["AUDAY_LINK_DEBUG"] == "1" {
        FileHandle.standardError.write(Data((text + "\n").utf8))
    }
}

public func linkParameters(password: String, peerToPeer: Bool) throws -> NWParameters {
    let key = try CommunicationPassword.key(password)
    let tls = NWProtocolTLS.Options()
    sec_protocol_options_set_min_tls_protocol_version(tls.securityProtocolOptions, .TLSv12)
    sec_protocol_options_set_max_tls_protocol_version(tls.securityProtocolOptions, .TLSv12)
    // Some SDKs import SSLCipherSuite constants as UInt32; the TLS enum uses UInt16.
    sec_protocol_options_append_tls_ciphersuite(tls.securityProtocolOptions,
        tls_ciphersuite_t(rawValue: UInt16(TLS_ECDHE_PSK_WITH_CHACHA20_POLY1305_SHA256))!)
    let identity = Data("auday-workflow-link-v2".utf8)
    key.withUnsafeBytes { raw in identity.withUnsafeBytes { name in
        sec_protocol_options_add_pre_shared_key(tls.securityProtocolOptions,
            DispatchData(bytes: raw) as __DispatchData, DispatchData(bytes: name) as __DispatchData)
    }}
    // There is no certificate bypass, plaintext fallback, or TLS 0-RTT.
    let parameters = NWParameters(tls: tls, tcp: NWProtocolTCP.Options())
    parameters.includePeerToPeer = peerToPeer
    return parameters
}

public final class FrameConnection {
    private final class Buffer: @unchecked Sendable {
        var bytes = Data()
    }
    public static let maximum = 2 * 1024 * 1024
    public let connection: NWConnection
    public init(_ connection: NWConnection) { self.connection = connection }
    public func receive(_ completion: @escaping (Result<Data, Error>) -> Void) {
        exact(4) { result in
            switch result {
            case .failure(let error): completion(.failure(error))
            case .success(let header):
                let size = header.reduce(0) { ($0 << 8) | Int($1) }
                guard size > 0, size <= Self.maximum else { completion(.failure(LinkError.message("invalid_frame_size"))); return }
                self.exact(size, completion)
            }
        }
    }
    private func exact(_ count: Int, _ completion: @escaping (Result<Data, Error>) -> Void) {
        read(count, into: Buffer(), completion)
    }
    private func read(_ count: Int, into buffer: Buffer, _ completion: @escaping (Result<Data, Error>) -> Void) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: min(64 * 1024, count - buffer.bytes.count)) { data, _, complete, error in
            if let data { buffer.bytes.append(data) }
            if let error { completion(.failure(error)) }
            else if buffer.bytes.count == count { completion(.success(buffer.bytes)) }
            else if complete || data?.isEmpty != false {
                completion(.failure(LinkError.message(complete ? "connection_closed" : "truncated_frame")))
            } else { self.read(count, into: buffer, completion) }
        }
    }
    public func send(_ value: Any, final: Bool = false, completion: @escaping (Error?) -> Void) {
        do {
            let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
            guard !data.isEmpty, data.count <= Self.maximum else { throw LinkError.message("response_too_large") }
            var size = UInt32(data.count).bigEndian
            var frame = withUnsafeBytes(of: &size) { Data($0) }; frame.append(data)
            connection.send(content: frame, contentContext: final ? .finalMessage : .defaultMessage,
                            isComplete: true, completion: .contentProcessed(completion))
        } catch { completion(error) }
    }
}

public func call(endpoint: NWEndpoint, password: String, request: [String: Any], peerToPeer: Bool = false,
                 timeout: TimeInterval = 10) throws -> [String: Any] {
    let connection = NWConnection(to: endpoint, using: try linkParameters(password: password, peerToPeer: peerToPeer))
    let queue = DispatchQueue(label: "auday.link.client"); let done = DispatchSemaphore(value: 0)
    let lock = NSLock(); var outcome: Result<[String: Any], Error>?
    func finish(_ result: Result<[String: Any], Error>) {
        lock.lock(); defer { lock.unlock() }; if outcome == nil { outcome = result; done.signal() }
    }
    let framed = FrameConnection(connection)
    connection.stateUpdateHandler = { state in
        trace("client state: \(state)")
        switch state {
        case .ready:
            framed.send(request) { error in
                if let error { finish(.failure(error)); return }
                framed.receive { result in
                    do {
                        guard var json = try JSONSerialization.jsonObject(with: result.get()) as? [String: Any],
                              let metadata = connection.metadata(definition: NWProtocolTLS.definition) as? NWProtocolTLS.Metadata else { throw LinkError.message("invalid_response") }
                        json["transport"] = [
                            "tlsVersion": String(format: "0x%04x", sec_protocol_metadata_get_negotiated_tls_protocol_version(metadata.securityProtocolMetadata).rawValue),
                            "cipherSuite": String(format: "0x%04x", sec_protocol_metadata_get_negotiated_tls_ciphersuite(metadata.securityProtocolMetadata).rawValue)]
                        finish(.success(json))
                    } catch { finish(.failure(error)) }
                }
            }
        case .failed: finish(.failure(LinkError.message("authentication_or_connection_failed")))
        default: break
        }
    }
    connection.start(queue: queue)
    if done.wait(timeout: .now() + timeout) == .timedOut { finish(.failure(LinkError.message("awaiting_phone_or_wrong_password"))) }
    connection.cancel(); lock.lock(); defer { lock.unlock() }; return try outcome!.get()
}
