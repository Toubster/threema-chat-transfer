// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// Code → screen, texts and actions (DESIGN §5.5, §8.4). The catalog comes from `codes.v1.json` via gen_codes.py.
final class ErrorCatalogTests: XCTestCase {
    func testEveryCodeHasADestinationThatExists() {
        for code in EngineCode.allCases {
            let d = ErrorCatalog.destination(for: code.rawValue)
            switch code.info.kind {
            case .error:
                XCTAssertNotEqual(d, .none, code.rawValue)
                if case .failure(.internalError) = d {
                    XCTAssertEqual(code.info.screen, "F-INTERNAL", "\(code.rawValue) must not fall back to F-INTERNAL")
                }
            case .result:
                if code == .R_RESTORE_SENT_LINK_LOST { XCTAssertEqual(d, .screen(.s16)) } else { XCTAssertEqual(d, .none) }
            case .warning, .note:
                if case .inline = d {} else { XCTFail("\(code.rawValue) should be an inline hint") }
            }
        }
    }

    func testEveryFailureScreenIsReachableFromACode() {
        let targets = Set(EngineCode.allCases.map { $0.info.screen })
        for f in FailureScreen.allCases {
            XCTAssertTrue(targets.contains(f.rawValue), "\(f.rawValue) has no code")
        }
    }

    func testDesignScreenMapping() {
        let expect: [(EngineCode, CodeDestination)] = [
            (.E_GUARD_FINDMY, .failure(.findMy)), (.E_GUARD_AIRPLANE, .failure(.airplane)),
            (.E_IOS_UNKNOWN, .failure(.iosUnknown)), (.E_IOS_BLOCKED, .failure(.iosUnknown)),
            (.E_THREEMA_ID_MISMATCH, .failure(.threemaId)), (.E_GUARD_FRESHNESS, .failure(.freshness)),
            (.E_GUARD_DCIM_CHANGED, .failure(.dcim)), (.E_BACKUP_PASSWORD, .failure(.passwordWrong)),
            (.E_ANDROID_PASSWORD, .inline(.s05)), (.E_ANDROID_INCOMPLETE, .inline(.s05)),
            (.E_DEV_LOCKED, .inline(.s03)), (.E_POST_TOO_EARLY, .inline(.s18)),
            (.E_GUARD_ROLLBACK_WINDOW, .inline(.s21)), (.E_BACKUP_ENCRYPTION_OFF, .screen(.s10a)),
            (.E_RESTORE_DEVICE_ERROR, .screen(.s16)), (.E_RESTORE_INTERRUPTED, .failure(.restoreMid)),
            (.E_GUARD_SET_INTEGRITY, .failure(.internalError)), (.E_THREEMA_ID_UNREADABLE, .failure(.threemaId)),
        ]
        for (c, d) in expect { XCTAssertEqual(ErrorCatalog.destination(for: c.rawValue), d, c.rawValue) }
    }

    func testUnknownCodeIsInternal() {
        XCTAssertEqual(ErrorCatalog.destination(for: "E_NOT_IN_PROTOCOL_1"), .failure(.internalError))
        XCTAssertEqual(ErrorCatalog.actions(for: "E_NOT_IN_PROTOCOL_1"), [.diagReport])
        XCTAssertEqual(ErrorCatalog.titleKey("E_NOT_IN_PROTOCOL_1"), EngineCode.E_INTERNAL.titleKey)
    }

    func testEveryCodeAndActionHasTextsInBothLanguages() {
        for lang in AppLanguage.allCases {
            let loc = Localizer(lang)
            for c in EngineCode.allCases {
                XCTAssertTrue(loc.has(c.titleKey), "\(lang) \(c.titleKey)")
                XCTAssertTrue(loc.has(c.bodyKey), "\(lang) \(c.bodyKey)")
            }
            for a in CodeAction.allCases where a != .none {
                XCTAssertTrue(loc.has("action.\(a.rawValue)"), "\(lang) action.\(a.rawValue)")
            }
        }
    }

    func testPlaceholdersFormatBytesCountsAndEnums() {
        let loc = Localizer(.de)
        let data = JSONValue.object(["need_bytes": .number(9_600_000_000), "free_bytes": .number(4_200_000_000),
                                     "reason": .string("connection"), "files": .number(12345)])
        let p = ErrorCatalog.placeholders(codeRaw: "E_RESTORE_NOT_STARTED", data: data, loc: loc)
        XCTAssertEqual(p["need_gb"], "9,6")
        XCTAssertEqual(p["free_gb"], "4,2")
        XCTAssertEqual(p["reason"], loc.t("code.E_RESTORE_NOT_STARTED.reason.connection"))
        XCTAssertNotEqual(p["reason"], "connection")
        let body = loc.t(ErrorCatalog.bodyKey("E_GUARD_IPHONE_SPACE"),
                         ErrorCatalog.placeholders(codeRaw: "E_GUARD_IPHONE_SPACE", data: data, loc: loc))
        XCTAssertFalse(body.contains("{"), body)
        XCTAssertTrue(body.contains("9,6"), body)
    }

    func testNeedsNewBackupDropsTheBackupResults() async throws {
        let h = try await StoreHarness(scenario: "happy")
        await MainActor.run {
            h.store.go(.s12)
            let r = ResultEvent(command: "backup", ok: false, codeRaw: "E_GUARD_AIRPLANE", retryable: true,
                                deviceModified: "no", data: .object([:]))
            h.store.handle(.failure(r), origin: .s12)
            XCTAssertEqual(h.store.screen, .failure(.airplane))
            XCTAssertNil(h.store.preBackup)
            XCTAssertEqual(ErrorCatalog.actions(for: "E_GUARD_AIRPLANE"), [.newBackup])
        }
    }
}
