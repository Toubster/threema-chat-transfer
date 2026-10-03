// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// A mock scenario file `app/Tests/Scenarios/<name>.jsonl` (format: docs/ENGINE-PROTOCOL.md §9).
public struct ScenarioScript: Sendable {
    public struct Header: Decodable, Sendable {
        public let name: String
        public let lang: String?
        public let expectScreen: String?
        public let summary: String?
        enum CodingKeys: String, CodingKey { case name, lang, expectScreen = "expect_screen", summary }
    }

    public struct Block: Sendable {
        public let cmd: String
        public let args: [String]
        public let secrets: [String]
        public var lines: [String]
        public var exitCode: Int32
        public var crashed: Bool
    }

    public enum Item: Sendable {
        case invoke(Block)
        case user(screen: String, answer: String)
        case relaunch
    }

    public let header: Header
    public let items: [Item]

    /// Recorded timestamps are relative to this start (ENGINE-PROTOCOL §9).
    public static let recordedStart = Formatters.parseTimestamp("2026-01-01T09:00:00.000Z")!

    public enum LoadError: Error { case unreadable, noHeader, badDirective(Int) }

    public static func load(_ url: URL) throws -> ScenarioScript {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { throw LoadError.unreadable }
        return try parse(text)
    }

    public static func parse(_ text: String) throws -> ScenarioScript {
        var header: Header?
        var items: [Item] = []
        var open: Block?
        for (no, raw) in text.split(separator: "\n", omittingEmptySubsequences: true).enumerated() {
            let line = String(raw)
            guard let data = line.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw LoadError.badDirective(no + 1)
            }
            guard let mock = obj["mock"] as? String else {
                open?.lines.append(line)          // an event of the open process
                continue
            }
            switch mock {
            case "scenario":
                header = try JSONDecoder().decode(Header.self, from: data)
            case "invoke":
                open = Block(cmd: obj["cmd"] as? String ?? "", args: obj["args"] as? [String] ?? [],
                             secrets: obj["secrets"] as? [String] ?? [], lines: [], exitCode: 0, crashed: false)
            case "exit":
                if var b = open {
                    b.exitCode = Int32((obj["code"] as? Int) ?? 0)
                    b.crashed = (obj["crash"] as? Bool) ?? false
                    items.append(.invoke(b))
                    open = nil
                }
            case "user":
                items.append(.user(screen: obj["screen"] as? String ?? "", answer: obj["answer"] as? String ?? ""))
            case "relaunch":
                items.append(.relaunch)
            default:
                throw LoadError.badDirective(no + 1)
            }
        }
        guard let header else { throw LoadError.noHeader }
        return ScenarioScript(header: header, items: items)
    }

    public var blocks: [Block] { items.compactMap { if case .invoke(let b) = $0 { return b }; return nil } }
}

/// Replays a scenario instead of starting `tmcore` (DESIGN §13.1 level 5, `TM_ENGINE=mock:<name>`).
/// Each `start` takes the next not-yet-used block of the same command (never across a `relaunch` marker),
/// streams its events with scaled timing and rebases recorded timestamps onto the current clock.
public final class MockEngine: EngineClient, @unchecked Sendable {
    public enum Speed: String, Sendable {
        case instant   // unit tests
        case fast      // UI tests
        case demo      // manual runs: compressed but watchable

        func delay(forGap gap: TimeInterval) -> TimeInterval {
            switch self {
            case .instant: return 0
            case .fast: return 0.004
            case .demo: return min(max(gap, 0) * 0.04, 0.7)
            }
        }
    }

    public let kind: EngineKind
    public let requiresMatchingEngineVersion = false
    public let writesEventLog = false
    public let script: ScenarioScript
    public let speed: Speed
    private let offset: TimeInterval
    private let lock = NSLock()
    private var cursor: Int
    private var _log: [EngineInvocation] = []

    /// Every invocation the app made (for ScenarioReplayTests).
    public var log: [EngineInvocation] { lock.lock(); defer { lock.unlock() }; return _log }

    public init(script: ScenarioScript, speed: Speed, startAfterRelaunch: Bool = false, now: Date = Date()) {
        self.script = script
        self.kind = .mock(scenario: script.header.name)
        self.speed = speed
        self.offset = now.timeIntervalSince(ScenarioScript.recordedStart)
        var c = 0
        if startAfterRelaunch, let i = script.items.firstIndex(where: { if case .relaunch = $0 { return true }; return false }) {
            c = i + 1
        }
        self.cursor = c
    }

    public func start(_ invocation: EngineInvocation) -> EngineHandle {
        lock.lock()
        _log.append(invocation)
        var block: ScenarioScript.Block?
        var i = cursor
        while i < script.items.count {
            if case .relaunch = script.items[i] { break }
            if case .invoke(let b) = script.items[i], b.cmd == invocation.command.rawValue {
                block = b
                cursor = i + 1
                break
            }
            i += 1
        }
        lock.unlock()
        let b = block ?? MockEngine.fallbackBlock(for: invocation)
        return MockHandle(block: b, speed: speed, offset: offset)
    }

    /// Number of Android files the scenario's first `android-inspect` expects (`<android-backup-N>` placeholders);
    /// mock runs have no file panel and hand the app that many dummy URLs (never read).
    public var androidFileCount: Int {
        let b = script.blocks.first { $0.cmd == EngineCommand.androidInspect.rawValue }
        return max(1, b?.args.filter { $0.hasPrefix("<android-backup-") }.count ?? 1)
    }

    // MARK: fallbacks for commands a scenario does not script

    static func fallbackBlock(for inv: EngineInvocation) -> ScenarioScript.Block {
        let cmd = inv.command.rawValue
        func line(_ seq: Int, _ body: String) -> String {
            "{\"v\":1,\"seq\":\(seq),\"ts\":\"2026-01-01T09:00:00.000Z\",\"cmd\":\"\(cmd)\",\(body)}"
        }
        let hello = line(1, "\"type\":\"hello\",\"engine_version\":\"0.0.0-mock\",\"protocol\":1,\"pymobiledevice3\":\"0.0.0\",\"importer_version\":\"0.0.0-mock\",\"models\":[\"V56\"],\"compat_digest\":\"h:00000000\"")
        var lines = [hello]
        var exit: Int32 = 0
        switch inv.command {
        case .deviceWatch:
            lines.append(line(2, "\"type\":\"device\",\"state\":\"ready\",\"device\":\"h:5c0ffee1\",\"product_type\":\"iPhone17,1\""))
            lines.append(line(3, "\"type\":\"result\",\"ok\":true,\"code\":\"R_OK\",\"retryable\":false,\"device_modified\":\"no\",\"data\":{\"events\":1}"))
        case .diagReport:
            lines.append(line(2, "\"type\":\"result\",\"ok\":true,\"code\":\"R_OK\",\"retryable\":false,\"device_modified\":\"no\",\"data\":{\"file\":\"diag/report-mock.json\",\"bytes\":0}"))
        case .cleanup:
            let what = inv.args.firstIndex(of: "--what").flatMap { inv.args.indices.contains($0 + 1) ? inv.args[$0 + 1] : nil } ?? "work"
            lines.append(line(2, "\"type\":\"result\",\"ok\":true,\"code\":\"R_OK\",\"retryable\":false,\"device_modified\":\"no\",\"data\":{\"freed_bytes\":0,\"what\":\"\(what)\"}"))
        default:
            lines.append(line(2, "\"type\":\"result\",\"ok\":false,\"code\":\"E_INTERNAL\",\"retryable\":false,\"device_modified\":\"no\",\"data\":{\"sub\":\"mock_no_block\"}"))
            exit = 2
        }
        return ScenarioScript.Block(cmd: cmd, args: [], secrets: [], lines: lines, exitCode: exit, crashed: false)
    }
}

final class MockHandle: EngineHandle, @unchecked Sendable {
    let output: AsyncStream<EngineOutput>
    private let lock = NSLock()
    private var terminated = false
    private var task: Task<Void, Never>?

    private static let tsPattern = try! NSRegularExpression(
        pattern: "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z$")

    init(block: ScenarioScript.Block, speed: MockEngine.Speed, offset: TimeInterval) {
        var cont: AsyncStream<EngineOutput>.Continuation!
        output = AsyncStream { cont = $0 }
        let continuation = cont!
        task = Task.detached { [weak self] in
            var lastTs: Date?
            var critical = false
            var lastSeq = 0
            var command = block.cmd
            for raw in block.lines {
                guard let event = MockHandle.rebased(raw, offset: offset) else {
                    continuation.yield(.invalidLine(reason: "unparseable"))
                    continue
                }
                let ts = Formatters.parseTimestamp(event.ts)
                let gap = (ts != nil && lastTs != nil) ? ts!.timeIntervalSince(lastTs!) : 0
                lastTs = ts ?? lastTs
                let d = speed.delay(forGap: gap)
                if d > 0 { try? await Task.sleep(nanoseconds: UInt64(d * 1_000_000_000)) } else { await Task.yield() }
                if self?.isTerminated == true && !critical {
                    // SIGTERM outside critical → E_CANCELLED at the next safe point (DESIGN §5.6).
                    let cancel = "{\"v\":1,\"seq\":\(lastSeq + 1),\"ts\":\"\(Formatters.timestamp(Date()))\",\"cmd\":\"\(command)\",\"type\":\"result\",\"ok\":false,\"code\":\"E_CANCELLED\",\"retryable\":true,\"device_modified\":\"no\",\"data\":{}}"
                    if lastSeq > 0, let e = EngineEvent.parse(cancel) { continuation.yield(.event(e)) }
                    continuation.yield(.exit(code: 4, crashed: false))
                    continuation.finish()
                    return
                }
                if event.type == EventType.critical.rawValue { critical = event.on ?? false }
                lastSeq = event.seq
                command = event.cmd
                continuation.yield(.event(event))
            }
            continuation.yield(.exit(code: block.exitCode, crashed: block.crashed))
            continuation.finish()
        }
    }

    private var isTerminated: Bool { lock.lock(); defer { lock.unlock() }; return terminated }

    func terminate() {
        lock.lock()
        terminated = true
        lock.unlock()
    }

    static func rebased(_ raw: String, offset: TimeInterval) -> EngineEvent? {
        guard let data = raw.data(using: .utf8), let json = try? JSONDecoder().decode(JSONValue.self, from: data) else {
            return nil
        }
        let shifted = json.mapStrings { s in
            let range = NSRange(s.startIndex..., in: s)
            guard tsPattern.firstMatch(in: s, range: range) != nil, let d = Formatters.parseTimestamp(s) else { return s }
            return Formatters.timestamp(d.addingTimeInterval(offset))
        }
        guard let out = try? JSONEncoder().encode(shifted) else { return nil }
        return try? JSONDecoder().decode(EngineEvent.self, from: out)
    }
}
