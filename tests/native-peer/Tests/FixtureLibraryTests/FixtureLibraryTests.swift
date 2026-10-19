import XCTest
import FixtureLibrary

final class FixtureLibraryTests: XCTestCase {
    func testTraversalAndDuplicateIdentityRejected() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let invalid: [String: Any] = ["schemaVersion": 1, "sourceDeviceID": "synthetic", "records": [],
            "assets": [["id": "bad", "path": "../outside", "size": 0, "sha256": ""]]]
        try JSONSerialization.data(withJSONObject: invalid).write(to: root.appendingPathComponent("library.json"))
        XCTAssertThrowsError(try Library(root: root))
        let duplicate: [String: Any] = ["schemaVersion": 1, "sourceDeviceID": "synthetic", "assets": [],
            "records": [["id": "same", "kind": "memory"], ["id": "same", "kind": "memory"]]]
        try JSONSerialization.data(withJSONObject: duplicate).write(to: root.appendingPathComponent("library.json"))
        XCTAssertThrowsError(try Library(root: root))
    }
    func testTextScopeHidesAudioAndChangesSnapshot() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let content = Data("synthetic asset".utf8)
        try content.write(to: root.appendingPathComponent("audio.dat"))
        let fixture: [String: Any] = ["schemaVersion": 1, "sourceDeviceID": "synthetic",
            "records": [["id": "record-1", "kind": "transcript", "text": "example", "audioAssetID": "audio-1"]],
            "assets": [["id": "audio-1", "path": "audio.dat", "size": content.count, "sha256": digest(content)]]]
        try JSONSerialization.data(withJSONObject: fixture).write(to: root.appendingPathComponent("library.json"))
        let text = try Library(root: root, scope: .textOnly), all = try Library(root: root, scope: .textAndAudio)
        XCTAssertNotEqual(text.snapshot, all.snapshot)
        XCTAssertEqual(try text.respond(["op": "status"])["assetCount"] as? Int, 0)
        XCTAssertNil(text.records[0]["audioAssetID"])
        XCTAssertThrowsError(try text.respond(["op": "audio", "id": "audio-1", "access": AccessScope.textAndAudio.rawValue]))
        XCTAssertThrowsError(try text.respond(["op": "write"]))
        XCTAssertEqual(try all.respond(["op": "audio", "id": "audio-1"])["size"] as? Int, content.count)
    }
}
