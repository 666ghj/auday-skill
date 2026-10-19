import Foundation
import FixtureLibrary
import AudayLink

// Test-only fixture process, listening on loopback. No phone APIs or app data.
var retainedPeer: LinkServer?
func emit(_ value: Any) throws {
    var data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    data.append(10)
    FileHandle.standardOutput.write(data)
}
do {
    guard let line = readLine(), let data = line.data(using: .utf8),
          let input = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let password = input["password"] as? String,
          let root = input["root"] as? String else { throw LinkError.message("invalid_fixture_request") }
    let scopeName = input["access"] as? String ?? AccessScope.textAndAudio.rawValue
    guard let scope = AccessScope(rawValue: scopeName) else { throw LinkError.message("invalid_fixture_scope") }
    let library = try Library(root: URL(fileURLWithPath: root), scope: scope)
    let peer = LinkServer(); retainedPeer = peer
    try peer.start(library: library, password: password) { result in
        switch result {
        case .success(let port): try? emit(["ready": true, "port": port, "snapshot": library.snapshot])
        case .failure: try? emit(["ready": false, "error": "fixture_listener_failed"]); exit(1)
        }
    }
    dispatchMain()
} catch { try? emit(["ok": false, "error": String(describing: error)]); exit(1) }
