// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// `session.json` (schema `session.v1#/$defs/session`), written by the app only. It never contains a password;
/// the only personal value is the path of the Android files the user chose (stays in the 0700 session folder).
public struct SessionState: Codable, Equatable, Sendable {
    public var schema = "session.v1"
    public var sessionId: String
    public var createdAt: String
    public var updatedAt: String
    public var appVersion: String
    public var locale: AppLanguage
    public var sidebarPhase: SidebarPhase
    public var screen: String
    public var workdirCustom: Bool?
    public var answers: SessionAnswers
    public var lastErrorCode: String?

    enum CodingKeys: String, CodingKey {
        case schema, sessionId = "session_id", createdAt = "created_at", updatedAt = "updated_at"
        case appVersion = "app_version", locale, sidebarPhase = "sidebar_phase", screen
        case workdirCustom = "workdir_custom", answers, lastErrorCode = "last_error_code"
    }

    public init(sessionId: String, now: Date, appVersion: String, locale: AppLanguage) {
        self.sessionId = sessionId
        self.createdAt = Formatters.timestamp(now)
        self.updatedAt = createdAt
        self.appVersion = appVersion
        self.locale = locale
        self.sidebarPhase = .start
        self.screen = Screen.s02.id
        self.answers = SessionAnswers()
    }
}

public struct AndroidFileRef: Codable, Equatable, Sendable {
    public var ref: Int
    public var path: String
}

public struct CleanupAnswers: Codable, Equatable, Sendable {
    public var chatCopiesDeletedAt: String?
    public var safetyCopyKeepUntil: String?
    enum CodingKeys: String, CodingKey {
        case chatCopiesDeletedAt = "chat_copies_deleted_at", safetyCopyKeepUntil = "safety_copy_keep_until"
    }
}

/// `session.v1` `answers`: what the user answered/ticked. Keys are fixed by the schema (additionalProperties: false).
public struct SessionAnswers: Codable, Equatable, Sendable {
    public var disclaimerAcceptedAt: String?
    public var androidFiles: [AndroidFileRef]?
    public var textOnlyConfirmed: Bool?
    public var threemaChecklist: [String: Bool]?
    public var settingsChecklist: [String: Bool]?
    public var safetyNet: String?             // icloud | finder
    public var safetyNetAt: String?
    public var encryptionWas: String?         // on | off
    public var passwordSource: String?        // generated | own | existing
    public var passwordInKeychain: Bool?
    public var offlineChecklist: [String: Bool]?
    public var transferConfirmedAt: String?
    public var buddyAnswer: String?           // account_only | full_setup | none
    public var threemaCheck: String?          // ok | problem
    public var cleanup: CleanupAnswers?

    enum CodingKeys: String, CodingKey {
        case disclaimerAcceptedAt = "disclaimer_accepted_at", androidFiles = "android_files"
        case textOnlyConfirmed = "text_only_confirmed", threemaChecklist = "threema_checklist"
        case settingsChecklist = "settings_checklist", safetyNet = "safety_net", safetyNetAt = "safety_net_at"
        case encryptionWas = "encryption_was", passwordSource = "password_source"
        case passwordInKeychain = "password_in_keychain", offlineChecklist = "offline_checklist"
        case transferConfirmedAt = "transfer_confirmed_at", buddyAnswer = "buddy_answer"
        case threemaCheck = "threema_check", cleanup
    }

    public init() {}
}
