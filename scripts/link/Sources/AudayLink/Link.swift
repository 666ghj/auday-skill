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

public enum AccessScope: String, CaseIterable {
    case textOnly = "text-only-read-only"
    case textAndAudio = "text-and-audio-read-only"
    public var allowsAudio: Bool { self == .textAndAudio }
}

public func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
public func fileDigest(_ url: URL) throws -> String {
    let file = try FileHandle(forReadingFrom: url); defer { try? file.close() }
    var hash = SHA256()
    while let chunk = try file.read(upToCount: 1024 * 1024), !chunk.isEmpty { hash.update(data: chunk) }
    return hash.finalize().map { String(format: "%02x", $0) }.joined()
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
    sec_protocol_options_append_tls_ciphersuite(tls.securityProtocolOptions,
        tls_ciphersuite_t(rawValue: TLS_ECDHE_PSK_WITH_CHACHA20_POLY1305_SHA256)!)
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
        connection.receive(minimumIncompleteLength: count, maximumLength: count) { data, _, complete, error in
            if let error { completion(.failure(error)) }
            else if let data, data.count == count { completion(.success(data)) }
            else { completion(.failure(LinkError.message(complete ? "connection_closed" : "truncated_frame"))) }
        }
    }
    public func send(_ value: Any, completion: @escaping (Error?) -> Void) {
        do {
            let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
            guard !data.isEmpty, data.count <= Self.maximum else { throw LinkError.message("response_too_large") }
            var size = UInt32(data.count).bigEndian
            var frame = withUnsafeBytes(of: &size) { Data($0) }; frame.append(data)
            connection.send(content: frame, completion: .contentProcessed(completion))
        } catch { completion(error) }
    }
}

/// Immutable public projection. Only allowlisted records/assets in library.json are served.
public final class Library {
    public let root: URL
    public let snapshot: String
    public let sourceDeviceID: String
    public let records: [[String: Any]]
    public let assets: [[String: Any]]
    public let scope: AccessScope
    public init(root: URL, scope: AccessScope = .textAndAudio) throws {
        self.scope = scope
        self.root = root.resolvingSymlinksInPath().standardizedFileURL
        let raw = try Data(contentsOf: root.appendingPathComponent("library.json"))
        guard raw.count <= 32 * 1024 * 1024,
              let value = try JSONSerialization.jsonObject(with: raw) as? [String: Any],
              value["schemaVersion"] as? Int == 1,
              let source = value["sourceDeviceID"] as? String, !source.isEmpty,
              let records = value["records"] as? [[String: Any]],
              let assets = value["assets"] as? [[String: Any]] else { throw LinkError.message("invalid_library") }
        self.snapshot = digest(raw + Data(scope.rawValue.utf8)); self.sourceDeviceID = source
        self.records = records.map { row in
            var projected = row
            if !scope.allowsAudio { projected.removeValue(forKey: "audioAssetID") }
            return projected
        }
        self.assets = scope.allowsAudio ? assets : []
        for rows in [records, assets] {
            let ids = rows.compactMap { $0["id"] as? String }
            guard ids.count == rows.count, Set(ids).count == ids.count, ids.allSatisfy({ !$0.isEmpty }) else {
                throw LinkError.message("duplicate_or_missing_id")
            }
        }
        for row in records {
            guard let kind = row["kind"] as? String,
                  ["transcript", "highlight", "summary", "reflection", "memory"].contains(kind),
                  (try JSONSerialization.data(withJSONObject: row)).count < FrameConnection.maximum / 2 else {
                throw LinkError.message("invalid_record")
            }
        }
        for row in self.assets {
            let url = try assetURL(row)
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? -1
            guard row["size"] as? Int == size, row["sha256"] as? String == (try fileDigest(url)) else {
                throw LinkError.message("asset_integrity_failed")
            }
        }
    }
    public func assetURL(_ row: [String: Any]) throws -> URL {
        guard let path = row["path"] as? String, !path.hasPrefix("/"), !path.split(separator: "/").contains("..") else {
            throw LinkError.message("invalid_asset_path")
        }
        let url = root.appendingPathComponent(path).resolvingSymlinksInPath().standardizedFileURL
        guard url.path.hasPrefix(root.path + "/"), try url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile == true else {
            throw LinkError.message("asset_outside_library")
        }
        return url
    }
    public func respond(_ request: [String: Any]) throws -> [String: Any] {
        if let expected = request["snapshot"] as? String, expected != snapshot { throw LinkError.message("snapshot_changed") }
        var reply: [String: Any] = ["schemaVersion": 1, "snapshot": snapshot, "sourceDeviceID": sourceDeviceID, "ok": true]
        switch request["op"] as? String {
        case "status":
            reply["access"] = scope.rawValue; reply["recordCount"] = records.count; reply["assetCount"] = assets.count
            reply["categories"] = ["transcript", "highlight", "summary", "reflection", "memory"] + (scope.allowsAudio ? ["audio"] : [])
        case "list":
            let offset = request["offset"] as? Int ?? 0
            guard offset >= 0, offset <= records.count else { throw LinkError.message("invalid_offset") }
            var page: [[String: Any]] = []; var bytes = 0
            for row in records.dropFirst(offset).prefix(100) {
                let size = try JSONSerialization.data(withJSONObject: row).count
                if bytes + size > 1024 * 1024, !page.isEmpty { break }
                bytes += size; page.append(row)
            }
            reply["records"] = page; reply["nextOffset"] = offset + page.count < records.count ? offset + page.count : NSNull()
        case "assets":
            let offset = request["offset"] as? Int ?? 0
            guard offset >= 0, offset <= assets.count else { throw LinkError.message("invalid_offset") }
            let page = assets.dropFirst(offset).prefix(100).map { row -> [String: Any] in
                var safe = row; safe.removeValue(forKey: "path"); return safe
            }
            reply["assets"] = page; reply["nextOffset"] = offset + page.count < assets.count ? offset + page.count : NSNull()
        case "read":
            guard let id = request["id"] as? String, let record = records.first(where: { $0["id"] as? String == id }) else {
                throw LinkError.message("record_not_found")
            }
            reply["record"] = record
        case "audio":
            guard scope.allowsAudio else { throw LinkError.message("audio_access_denied") }
            guard let id = request["id"] as? String, let row = assets.first(where: { $0["id"] as? String == id }),
                  let size = row["size"] as? Int else { throw LinkError.message("asset_not_found") }
            let offset = request["offset"] as? Int ?? 0; let length = request["length"] as? Int ?? 262144
            guard offset >= 0, offset <= size, length > 0, length <= 262144 else { throw LinkError.message("invalid_range") }
            let file = try FileHandle(forReadingFrom: assetURL(row)); defer { try? file.close() }
            try file.seek(toOffset: UInt64(offset)); let data = try file.read(upToCount: min(length, size - offset)) ?? Data()
            guard data.count == min(length, size - offset) else { throw LinkError.message("asset_changed") }
            reply["data"] = data.base64EncodedString(); reply["offset"] = offset; reply["sha256"] = row["sha256"]
            reply["chunkSHA256"] = digest(data); reply["size"] = size
        default: throw LinkError.message("read_only_operation_not_supported")
        }
        return reply
    }
}

public final class LinkServer {
    private let queue = DispatchQueue(label: "auday.link.server")
    private var listener: NWListener?
    private var clients: [UUID: NWConnection] = [:]
    public init() {}
    public func start(library: Library, password: String, port: UInt16 = 0, peerToPeer: Bool = false,
                      advertise: Bool = false, ready: @escaping (Result<UInt16, Error>) -> Void) throws {
        let parameters = try linkParameters(password: password, peerToPeer: peerToPeer)
        let listener = try NWListener(using: parameters, on: NWEndpoint.Port(rawValue: port)!)
        self.listener = listener
        if advertise { listener.service = .init(name: "Auday-" + String(UUID().uuidString.prefix(6)), type: "_auday-link._tcp") }
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready: ready(.success(listener.port!.rawValue))
            case .failed(let error): ready(.failure(error))
            default: break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self, self.clients.count < 8 else { connection.cancel(); return }
            let id = UUID(); self.clients[id] = connection
            let framed = FrameConnection(connection)
            // One request per connection; bounded handshake and response time.
            self.queue.asyncAfter(deadline: .now() + 12) { connection.cancel(); self.clients.removeValue(forKey: id) }
            connection.stateUpdateHandler = { state in
                trace("server state: \(state)")
                if case .ready = state {
                    framed.receive { result in
                        do {
                            let data = try result.get()
                            guard let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw LinkError.message("invalid_request") }
                            let response: [String: Any]
                            do { response = try library.respond(request) }
                            catch { response = ["ok": false, "error": String(describing: error)] }
                            framed.send(response) { _ in connection.cancel(); self.clients.removeValue(forKey: id) }
                        } catch { connection.cancel(); self.clients.removeValue(forKey: id) }
                    }
                } else if case .failed = state { connection.cancel(); self.clients.removeValue(forKey: id) }
            }
            connection.start(queue: self.queue)
        }
        listener.start(queue: queue)
    }
    public func stop() { queue.sync { listener?.cancel(); listener = nil; clients.values.forEach { $0.cancel() }; clients.removeAll() } }
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
