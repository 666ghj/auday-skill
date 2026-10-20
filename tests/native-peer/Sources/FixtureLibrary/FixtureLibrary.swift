import Foundation
import Network
import CryptoKit
import AudayLink

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

/// Synthetic test-only peer. Never part of the installed desktop skill.
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
                  (try JSONSerialization.data(withJSONObject: row)).count < FrameConnection.maximum - 4096 else {
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
        let parameters = try linkParameters(password: password, peerToPeer: false)
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
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
                if case .ready = state {
                    framed.receive { result in
                        do {
                            let data = try result.get()
                            guard let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw LinkError.message("invalid_request") }
                            let response: [String: Any]
                            do { response = try library.respond(request) }
                            catch { response = ["ok": false, "error": String(describing: error)] }
                            framed.send(response, final: true) { error in
                                if error != nil { connection.cancel(); self.clients.removeValue(forKey: id); return }
                                connection.receive(minimumIncompleteLength: 1, maximumLength: 1) { _, _, _, _ in
                                    connection.cancel(); self.clients.removeValue(forKey: id)
                                }
                            }
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
