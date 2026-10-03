// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// Secrets in memory only (DESIGN §9). Never Codable, never logged, never written to the session.
/// The Android passwords are dropped right after `android-normalize`.
public final class SecretsBox {
    public private(set) var backupPassword: String?
    public private(set) var androidPasswords: [Int: String] = [:]

    public init() {}

    public func setBackupPassword(_ p: String?) { backupPassword = (p?.isEmpty ?? true) ? nil : p }
    public func setAndroidPassword(_ p: String, ref: Int) { androidPasswords[ref] = p }
    public func dropAndroidPasswords() { androidPasswords.removeAll() }
    public func wipe() {
        backupPassword = nil
        androidPasswords.removeAll()
    }
}
