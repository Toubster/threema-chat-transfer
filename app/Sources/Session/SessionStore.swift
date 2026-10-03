// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// The session folder (DESIGN §5.8): `~/Library/Application Support/Chat Transfer for Threema/sessions/<uuid>/`, 0700, excluded
/// from Time Machine, not indexed by Spotlight. The app writes `session.json` and `logs/`; everything else belongs
/// to the engine. A new session is only possible when the old one is finished or explicitly discarded.
public final class SessionStore {
    public let defaultRoot: URL
    private let fm = FileManager.default
    private let excludeFromBackups: Bool
    public private(set) var current: URL?
    public private(set) var state: SessionState?

    /// `root` = the sessions folder. `excludeFromBackups` runs `tmutil addexclusion` (off in tests and mock runs).
    public init(root: URL, excludeFromBackups: Bool) {
        self.defaultRoot = root
        self.excludeFromBackups = excludeFromBackups
    }

    public static var standardRoot: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
        return base.appendingPathComponent(AppInfo.productName).appendingPathComponent("sessions")
    }

    /// Extra roots chosen by the user (external APFS SSD); remembered so the app finds the session after a restart.
    private var rootsFile: URL { defaultRoot.deletingLastPathComponent().appendingPathComponent("session-roots.json") }

    public var knownRoots: [URL] {
        var roots = [defaultRoot]
        if let data = try? Data(contentsOf: rootsFile),
           let paths = try? JSONDecoder().decode([String].self, from: data) {
            roots += paths.map { URL(fileURLWithPath: $0) }
        }
        return roots
    }

    public func remember(root: URL) {
        guard root.standardizedFileURL != defaultRoot.standardizedFileURL else { return }
        var paths = knownRoots.dropFirst().map(\.path)
        if !paths.contains(root.path) { paths.append(root.path) }
        try? ensureDir(rootsFile.deletingLastPathComponent())
        try? JSONEncoder().encode(paths).write(to: rootsFile, options: .atomic)
    }

    // MARK: lifecycle

    /// The most recently updated session that is not discarded (the engine says whether it is closed).
    public func findLatest() -> (URL, SessionState)? {
        var best: (URL, SessionState)?
        for root in knownRoots {
            guard let items = try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil) else { continue }
            for dir in items where dir.hasDirectoryPath {
                guard let s = load(dir) else { continue }
                if best == nil || s.updatedAt > best!.1.updatedAt { best = (dir, s) }
            }
        }
        return best
    }

    public func load(_ dir: URL) -> SessionState? {
        guard let data = try? Data(contentsOf: dir.appendingPathComponent("session.json")) else { return nil }
        return try? JSONDecoder().decode(SessionState.self, from: data)
    }

    public func open(_ dir: URL, state: SessionState) {
        current = dir
        self.state = state
    }

    /// Creates `<root>/<uuid>/` with mode 0700, `.metadata_never_index`, a Time Machine exclusion and `session.json`.
    @discardableResult
    public func create(root: URL? = nil, state make: (String) -> SessionState) throws -> URL {
        let root = root ?? defaultRoot
        try ensureDir(root)
        let id = UUID().uuidString.lowercased()
        let dir = root.appendingPathComponent(id, isDirectory: true)
        try ensureDir(dir)
        for sub in ["logs", "work/tmp"] { try ensureDir(dir.appendingPathComponent(sub)) }
        fm.createFile(atPath: dir.appendingPathComponent(".metadata_never_index").path, contents: Data(),
                      attributes: [.posixPermissions: 0o600])
        if excludeFromBackups { SessionStore.addTimeMachineExclusion(dir) }
        var s = make(id)
        s.sessionId = id
        current = dir
        state = s
        try save()
        remember(root: root)
        return dir
    }

    public func update(_ change: (inout SessionState) -> Void, now: Date = Date()) {
        guard var s = state else { return }
        change(&s)
        s.updatedAt = Formatters.timestamp(now)
        state = s
        try? save()
    }

    /// Atomic write, mode 0600.
    public func save() throws {
        guard let dir = current, let s = state else { return }
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try enc.encode(s)
        let url = dir.appendingPathComponent("session.json")
        try data.write(to: url, options: [.atomic])
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    /// Deletes the whole session folder ("Sitzung verwerfen"; only before anything was sent, or after a postcheck).
    public func discard() throws {
        guard let dir = current else { return }
        try fm.removeItem(at: dir)
        current = nil
        state = nil
    }

    public func close() {
        current = nil
        state = nil
    }

    /// A file the engine wrote into the session (`android/missing-senders.json`, `diag/…`). Keys are validated so a
    /// key can never leave the session folder.
    public func file(forKey key: String) -> URL? {
        guard let dir = current,
              key.range(of: "^(android|ios|work|reports|logs|diag)/[a-z0-9_.-]{1,60}(/[a-z0-9_.-]{1,60}){0,3}$",
                        options: .regularExpression) != nil, !key.contains("..") else { return nil }
        return dir.appendingPathComponent(key)
    }

    private func ensureDir(_ url: URL) throws {
        if !fm.fileExists(atPath: url.path) {
            try fm.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        }
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
    }

    static func addTimeMachineExclusion(_ url: URL) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/tmutil")
        p.arguments = ["addexclusion", url.path]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
    }
}
