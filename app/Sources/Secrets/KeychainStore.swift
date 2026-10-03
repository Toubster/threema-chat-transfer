// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation
import Security

/// Storage for the iPhone backup password when the app generated it (DESIGN §9). Nothing else is ever stored;
/// Android passwords and an existing backup password stay in memory only.
public protocol KeychainStoring: AnyObject {
    func save(_ password: String) throws
    func load() throws -> String?
    func delete() throws
}

public enum KeychainError: Error, Equatable {
    case status(OSStatus)
}

/// Login keychain item "{App} – Backup-Passwort". With an ad-hoc signature macOS asks once per app update.
public final class KeychainStore: KeychainStoring {
    public let service: String
    public let account: String

    public init(service: String = "\(AppInfo.productName) – Backup-Passwort", account: String = "iphone-backup") {
        self.service = service
        self.account = account
    }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    public func save(_ password: String) throws {
        let data = Data(password.utf8)
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrLabel as String] = service
        add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
        let status = SecItemAdd(add as CFDictionary, nil)
        if status == errSecDuplicateItem {
            let upd = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
            guard upd == errSecSuccess else { throw KeychainError.status(upd) }
            return
        }
        guard status == errSecSuccess else { throw KeychainError.status(status) }
    }

    public func load() throws -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &out)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = out as? Data else { throw KeychainError.status(status) }
        return String(data: data, encoding: .utf8)
    }

    public func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError.status(status) }
    }
}

/// In-memory keychain for tests and mock runs (never touches the login keychain).
public final class InMemoryKeychain: KeychainStoring {
    private var value: String?
    public init() {}
    public func save(_ password: String) throws { value = password }
    public func load() throws -> String? { value }
    public func delete() throws { value = nil }
}
