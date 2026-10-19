import XCTest
@testable import AudayLink

final class BoundaryTests: XCTestCase {
    func testPasswordRoundTripAndWeakRejection() throws {
        let one = try CommunicationPassword.generate(), two = try CommunicationPassword.generate()
        XCTAssertEqual(one.count, 16); XCTAssertEqual(CommunicationPassword.alphabet.count, 32)
        XCTAssertNotEqual(one, two); XCTAssertEqual(try CommunicationPassword.key(one).count, 32)
        XCTAssertEqual(try CommunicationPassword.key(one), try CommunicationPassword.key(CommunicationPassword.formatted(one).lowercased()))
        XCTAssertThrowsError(try CommunicationPassword.key("password123"))
        XCTAssertThrowsError(try CommunicationPassword.key(String(repeating: "A", count: 16)))
    }
}
