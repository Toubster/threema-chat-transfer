// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// Static checks that keep the XCUITests and VoiceOver labels honest without running UI automation:
/// every identifier the UI tests use exists in the views, and icon-only controls carry a label.
final class UIContractTests: XCTestCase {
    private func read(_ dir: URL) throws -> String {
        var out = ""
        let e = try XCTUnwrap(FileManager.default.enumerator(at: dir, includingPropertiesForKeys: nil))
        for case let url as URL in e where url.pathExtension == "swift" {
            out += try String(contentsOf: url, encoding: .utf8) + "\n"
        }
        return out
    }

    private func literals(_ pattern: String, _ text: String) throws -> Set<String> {
        let re = try NSRegularExpression(pattern: pattern)
        return Set(re.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap {
            Range($0.range(at: 1), in: text).map { String(text[$0]) }
        })
    }

    func testEveryIdentifierTheUITestsUseExists() throws {
        let src = try read(Repo.sources)
        let ui = try read(Repo.appDir.appendingPathComponent("Tests/UI"))
        let used = try literals(#"(?:click|tick|type|radio|button|element)\("([a-z][a-z0-9_.]*)""#, ui)
            .union(try literals(#"\.(?:buttons|checkBoxes|secureTextFields|textFields|radioGroups)\["([a-z][a-z0-9_.]*)"\]"#, ui))
        XCTAssertGreaterThan(used.count, 15)
        let checklistIds = Set(WizardStore.threemaItems + WizardStore.settingsItems + WizardStore.offlineItems)
        for id in used {
            if src.contains("\"\(id)\"") { continue }
            if id.hasPrefix("check."), case let rest = String(id.dropFirst(6)),
               src.contains("id: \"\(rest)\"") || checklistIds.contains(rest) { continue }
            if id.hasPrefix("screen.") || id.hasPrefix("field.android_password.") { continue }
            if id.hasPrefix("btn.action."), CodeAction(rawValue: String(id.dropFirst(11))) != nil,
               src.contains("\"btn.action.\\(a.rawValue)\"") { continue }
            XCTFail("UI tests use identifier \(id), no view sets it")
        }
    }

    func testIconOnlyButtonsHaveAccessibilityLabels() throws {
        let src = try read(Repo.sources)
        let lines = src.components(separatedBy: "\n")
        for (i, l) in lines.enumerated() where l.contains("} label: { Image(") {
            let window = lines[i..<min(i + 4, lines.count)].joined(separator: "\n")
            XCTAssertTrue(window.contains(".accessibilityLabel("), "icon-only button without label: \(l.trimmingCharacters(in: .whitespaces))")
        }
    }

    func testDecorativeImagesAreHiddenFromVoiceOver() throws {
        // Illustrations and icons next to text are decorative: they must not be read as unlabeled images.
        let src = try read(Repo.sources)
        let lines = src.components(separatedBy: "\n")
        for (i, l) in lines.enumerated() where l.contains("Image(systemName:") && !l.contains("label: {")
            && !l.contains("Label(") && !l.contains("icon:") {
            let window = lines[i..<min(i + 10, lines.count)].joined(separator: "\n")
            let ok = window.contains(".accessibilityHidden(true)") || window.contains(".accessibilityLabel(")
                || window.contains(".accessibilityElement(children: .ignore)") || lines[max(0, i - 6)..<i].joined().contains(".accessibilityElement(children: .ignore)")
                || l.contains("case .")   // CheckRowView icons: the row is one element with label + value
            XCTAssertTrue(ok, "image without label or accessibilityHidden: \(l.trimmingCharacters(in: .whitespaces))")
        }
    }

    func testEveryScreenHasAnIdentifiedContainerAndTitle() throws {
        let scaffold = try String(contentsOf: Repo.sources.appendingPathComponent("Wizard/Views/Components/Scaffold.swift"),
                                  encoding: .utf8)
        XCTAssertTrue(scaffold.contains("accessibilityIdentifier(\"screen.\\(screen.id)\")"))
        XCTAssertTrue(scaffold.contains(".accessibilityAddTraits(.isHeader)"))
        let views = try read(Repo.sources.appendingPathComponent("Wizard/Views"))
        for s in Screen.wizardScreens {
            XCTAssertTrue(views.contains("screen: .\(s.id.lowercased())"), "\(s.id) does not use ScreenScaffold")
        }
    }
}
