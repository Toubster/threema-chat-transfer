// SPDX-License-Identifier: AGPL-3.0-or-later
// Importer.swift - writes normalized Android data into a COPY of an iOS ThreemaData.sqlite via Core Data + KVC.
// Every mapping decision is documented (with iOS source citations) in docs/import-mapping.md.
import CoreData
import CryptoKit
import Foundation

struct Options {
    var normalized: URL!
    var workDir: URL!
    var storeIn: URL!
    var storeOut: URL!
    var momd: URL!
    var ownIdentity: String?
    var masterDataOnly = false
    var dryRun = false
    var report: URL?
    var noNonces = false
    var overwrite = false
    var appPrefs: URL?
    var batchSize = 500
    var batchBytes = 256 << 20
    var verifyMediaHash = true
    /// review F3: fill contact/group pictures ONLY where the existing row has none (default off: existing data wins)
    var fillMissingAvatars = false
}

/// SystemMessageEntity.SystemMessageEntityType (SystemMessageEntity.swift:10-47)
enum SysType: Int16 {
    case renameGroup = 1, groupMemberLeave = 2, groupMemberAdd = 3, groupMemberForcedLeave = 4, groupSelfAdded = 5,
         groupSelfRemoved = 6, callMissed = 7, callRejected = 8, callRejectedBusy = 9, callRejectedTimeout = 10,
         callEnded = 11, callRejectedDisabled = 12, callRejectedUnknown = 13, callRejectedOffHours = 15,
         groupSelfLeft = 16, startNoteGroupInfo = 17, endNoteGroupInfo = 18, groupCreatorLeft = 19, vote = 20,
         voteUpdated = 30, groupProfilePictureChanged = 32
    /// excludeTypesAsLastMessage (SystemMessageEntity.swift:49-64): FS types 21-29,36,37 + vote 20/30
    static let excludedAsLastMessage: Set<Int> = [21, 22, 23, 24, 25, 26, 27, 28, 29, 36, 37, 20, 30]
}

/// VoteInfo (SystemMessageEntity+type.swift:8-25), encoded like BallotManager.addVoteSystemMessage (BallotManager.swift:158-175)
struct VoteInfo: Codable {
    let ballotTitle: String
    let voterID: String
    let showIntermediateResults: Bool
    var updatedVote: Bool?
}

private let secondsFormatter: DateComponentsFormatter = {
    // Same configuration as ThreemaFramework DateFormatter.timeFormatted(_ TimeInterval) (DateFormatter.swift:634-655)
    let f = DateComponentsFormatter()
    f.zeroFormattingBehavior = .pad
    f.allowedUnits = [.minute, .second]
    return f
}()

final class Importer {
    let opt: Options
    let norm: Normalized
    let report: Report
    let ctx: NSManagedObjectContext
    let own: String
    let meIDs: Set<String>
    let ownOverridden: Bool
    let tmpDir: URL

    // indices (object IDs survive ctx.reset())
    var contactOID: [String: NSManagedObjectID] = [:]
    var normContacts: [String: NContact] = [:]
    var normGroups: [String: NGroup] = [:]                    // canonical key -> group
    var oneToOneConv: [String: NSManagedObjectID] = [:]       // partner identity -> conversation
    var groupConv: [String: NSManagedObjectID] = [:]          // canonical group key -> conversation
    var groupEnt: [String: NSManagedObjectID] = [:]           // canonical group key -> Group
    var existingMsgIds: [NSManagedObjectID: Set<Data>] = [:]  // conversation -> ids present before this run
    var insertedMsgs: [NSManagedObjectID: [Data: NSManagedObjectID]] = [:]
    var newConvs: Set<NSManagedObjectID> = []
    var touchedConvs: Set<NSManagedObjectID> = []
    var newestImportedDisplayDate: [NSManagedObjectID: Date] = [:]
    var convFallbackDate: [NSManagedObjectID: Date] = [:]
    var ballotByRef: [Int64: NSManagedObjectID] = [:]
    var existingBallotIds: [Data: NSManagedObjectID] = [:]
    var displayNameCache: [String: String] = [:]
    var pending: [(conv: NSManagedObjectID, id: Data, obj: NSManagedObject)] = []
    var pendingBytes = 0
    var importedDates: (min: Date?, max: Date?) = (nil, nil)
    var importedDatesAll: [Date] = []

    init(opt: Options, norm: Normalized, report: Report, psc: NSPersistentStoreCoordinator) throws {
        self.opt = opt
        self.norm = norm
        self.report = report
        ctx = NSManagedObjectContext(concurrencyType: .privateQueueConcurrencyType)
        ctx.persistentStoreCoordinator = psc
        ctx.undoManager = nil
        guard let metaOwn = norm.meta["own_identity"] ?? opt.ownIdentity, metaOwn.count == 8 else {
            throw ImportError("own identity unknown (meta.own_identity missing and no --own-identity)")
        }
        own = opt.ownIdentity ?? metaOwn
        meIDs = Set([own, metaOwn])
        ownOverridden = own != metaOwn
        tmpDir = opt.storeOut.deletingLastPathComponent().appendingPathComponent(".threema-import-tmp-\(getpid())")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    deinit { try? FileManager.default.removeItem(at: tmpDir) }

    func canonGroupKey(idHex: String, creator: String?) -> String {
        guard let creator, !meIDs.contains(creator) else { return idHex.lowercased() + "-ME" }
        return idHex.lowercased() + "-" + creator
    }

    func canonGroupKey(normalizedKey: String) -> String {
        guard let dash = normalizedKey.firstIndex(of: "-") else { return normalizedKey }
        return canonGroupKey(idHex: String(normalizedKey[..<dash]), creator: String(normalizedKey[normalizedKey.index(after: dash)...]))
    }

    // MARK: - driver

    func run() throws {
        var failure: Error?
        ctx.performAndWait {
            do {
                try indexStore()
                try importContacts()
                try importGroups()
                if !opt.masterDataOnly {
                    try importBallots()
                    try importMessages()
                    try importReactions()
                }
                try importNonces()
                try finalizeConversations()
                try flush(force: true)
            } catch { failure = error }
        }
        if let failure { throw failure }
        retentionCheck()
    }

    // MARK: - helpers

    func insert(_ entity: String) -> NSManagedObject {
        NSEntityDescription.insertNewObject(forEntityName: entity, into: ctx)
    }

    func obj(_ oid: NSManagedObjectID) -> NSManagedObject { ctx.object(with: oid) }

    func permanentID(_ o: NSManagedObject) throws -> NSManagedObjectID {
        if o.objectID.isTemporaryID { try ctx.obtainPermanentIDs(for: [o]) }
        return o.objectID
    }

    func flush(force: Bool = false) throws {
        guard ctx.hasChanges else { return }
        guard force || ctx.insertedObjects.count >= opt.batchSize || pendingBytes >= opt.batchBytes else { return }
        let objs = pending.map(\.obj)
        if !objs.isEmpty { try ctx.obtainPermanentIDs(for: objs) }
        for p in pending { insertedMsgs[p.conv, default: [:]][p.id] = p.obj.objectID }
        try ctx.save()
        ctx.reset()
        pending.removeAll(keepingCapacity: true)
        pendingBytes = 0
    }

    func resolveMedia(_ rel: String?) -> URL? {
        guard let rel, !rel.isEmpty else { return nil }
        let url = rel.hasPrefix("/") ? URL(fileURLWithPath: rel) : opt.workDir.appendingPathComponent(rel)
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), !isDir.boolValue else { return nil }
        return url
    }

    func readSmall(_ rel: String?) -> Data? {
        resolveMedia(rel).flatMap { try? Data(contentsOf: $0) }
    }

    func imageDataEntity(_ data: Data) -> NSManagedObject? {
        guard let (w, h) = Media.imageSize(data: data) else { return nil }
        let img = insert("ImageData")
        img.setValue(data, forKey: "data")
        img.setValue(NSNumber(value: Int16(clamping: w)), forKey: "width")    // ImageDataEntity width/height Int16
        img.setValue(NSNumber(value: Int16(clamping: h)), forKey: "height")
        return img
    }

    /// ContactEntity+display.swift:13-37 (first+last, else "~nickname", else identity)
    func displayName(_ identity: String) -> String {
        if let c = displayNameCache[identity] { return c }
        var first: String?, last: String?, nick: String?
        if let oid = contactOID[identity] {
            let c = obj(oid)
            first = c.value(forKey: "firstName") as? String
            last = c.value(forKey: "lastName") as? String
            nick = c.value(forKey: "publicNickname") as? String
        } else if let n = normContacts[identity] {
            first = n.firstName; last = n.lastName; nick = n.nickname
        }
        var name = [first, last].compactMap { $0?.isEmpty == false ? $0 : nil }.joined(separator: " ")
        if name.isEmpty, let nick, !nick.isEmpty, nick != identity { name = "~" + nick }
        if name.isEmpty { name = identity }
        displayNameCache[identity] = name
        return name
    }

    // MARK: - phase A: index existing store

    func indexStore() throws {
        let cf = NSFetchRequest<NSManagedObject>(entityName: "Contact")
        for c in try ctx.fetch(cf) {
            if let id = c.value(forKey: "identity") as? String { contactOID[id] = c.objectID }
        }
        let gf = NSFetchRequest<NSManagedObject>(entityName: "Group")
        for g in try ctx.fetch(gf) {
            guard let gid = g.value(forKey: "groupId") as? Data else { continue }
            groupEnt[canonGroupKey(idHex: gid.hex, creator: g.value(forKey: "groupCreator") as? String)] = g.objectID
        }
        let vf = NSFetchRequest<NSManagedObject>(entityName: "Conversation")
        vf.relationshipKeyPathsForPrefetching = ["contact"]
        for v in try ctx.fetch(vf) {
            if v.value(forKey: "distributionList") != nil { continue }
            let contactIdentity = (v.value(forKey: "contact") as? NSManagedObject)?.value(forKey: "identity") as? String
            if let gid = v.value(forKey: "groupId") as? Data {
                groupConv[canonGroupKey(idHex: gid.hex, creator: contactIdentity)] = v.objectID
            } else if let ci = contactIdentity {
                if oneToOneConv[ci] != nil { report.inc("store", "duplicate_1to1_conversations") }
                oneToOneConv[ci] = oneToOneConv[ci] ?? v.objectID
            }
        }
        // review m8: iOS resolves a contact's chat with its own fetch (conversationEntity(for:)), which may pick a
        // different row than ours -> imported history could land in a chat the app never opens. STOP.
        let dups = report.get("store", "duplicate_1to1_conversations")
        // No override (DESIGN §2.3, §6.1): the engine maps this to E_IMPORT_DUPLICATE_CHAT.
        if dups > 0 {
            throw ImportError("target store has \(dups) duplicate 1:1 conversation(s) for the same contact - STOP",
                              code: "duplicate_1to1")
        }
        // (conversation, id) of all existing messages -> dedupe
        let mf = NSFetchRequest<NSDictionary>(entityName: "Message")
        mf.resultType = .dictionaryResultType
        mf.propertiesToFetch = ["id", "conversation"]
        var n = 0
        for d in try ctx.fetch(mf) {
            guard let id = d["id"] as? Data, let conv = d["conversation"] as? NSManagedObjectID else { continue }
            existingMsgIds[conv, default: []].insert(id)
            n += 1
        }
        let bf = NSFetchRequest<NSManagedObject>(entityName: "Ballot")
        for b in try ctx.fetch(bf) { if let id = b.value(forKey: "id") as? Data { existingBallotIds[id] = b.objectID } }
        report.info["store_before"] = [
            "contacts": contactOID.count, "groups": groupEnt.count, "conversations_1to1": oneToOneConv.count,
            "conversations_group": groupConv.count, "messages": n, "ballots": existingBallotIds.count,
        ]
        ctx.reset()
    }

    // MARK: - phase B: contacts

    /// iOS ContactEntity.updateSortInitial + ThreemaLocalizedIndexedCollation (A-Z, "#", "*"; de/en collation)
    func sortInitial(_ identity: String, first: String?, last: String?, nick: String?) -> (String, Int) {
        if identity.hasPrefix("*") { return ("*", 27) }
        var s = identity
        let f = first ?? "", l = last ?? ""
        if !f.isEmpty || !l.isEmpty {
            // UserSettings default SortOrderFirstName = NO -> last name first
            s = !l.isEmpty ? l : f
        } else if let nick, !nick.isEmpty { s = nick }
        if let ch = s.uppercased().first, let a = ch.asciiValue, a >= 65, a <= 90 { return (String(ch), Int(a) - 65) }
        return ("#", 26)
    }

    func importContacts() throws {
        for c in try norm.contacts() { normContacts[c.identity] = c }
        for c in normContacts.values.sorted(by: { $0.identity < $1.identity }) {
            if meIDs.contains(c.identity) { report.skipped("contacts", "own_identity"); continue }
            if let ex = contactOID[c.identity] {
                report.existing("contacts")
                fillExistingContactAvatar(obj(ex), c)
                continue
            }
            guard c.identity.count == 8 else { report.skipped("contacts", "bad_identity"); continue }
            guard c.publicKey.count == 32 else { report.skipped("contacts", "bad_public_key"); continue }
            let o = insert("Contact")
            o.setValue(c.identity, forKey: "identity")
            o.setValue(c.publicKey, forKey: "publicKey")
            o.setValue(NSNumber(value: Int16(max(0, min(2, c.verification)))), forKey: "verificationLevel")
            o.setValue(c.firstName?.isEmpty == false ? c.firstName : nil, forKey: "firstName")
            o.setValue(c.lastName?.isEmpty == false ? c.lastName : nil, forKey: "lastName")
            o.setValue(c.nickname?.isEmpty == false ? c.nickname : nil, forKey: "publicNickname")
            o.setValue(NSNumber(value: Int16(c.hidden ? 1 : 0)), forKey: "hidden")
            o.setValue(NSNumber(value: Int16(0)), forKey: "state")                 // ContactState.active
            o.setValue(NSNumber(value: Int64(0)), forKey: "featureMask")           // refreshed by the app
            o.setValue(NSNumber(value: Int16(0)), forKey: "forwardSecurityState")
            o.setValue(NSNumber(value: Int16(0)), forKey: "readReceipts")          // ReadReceipt.default
            o.setValue(NSNumber(value: Int16(0)), forKey: "typingIndicators")      // TypingIndicator.default
            o.setValue(NSNumber(value: Int16(0)), forKey: "workContact")
            o.setValue(Date(), forKey: "createdAt")
            let (si, idx) = sortInitial(c.identity, first: c.firstName, last: c.lastName, nick: c.nickname)
            o.setValue(si, forKey: "sortInitial")
            o.setValue(NSNumber(value: Int32(idx)), forKey: "sortIndex")
            if let d = readSmall(c.avatarUserPath) {
                o.setValue(d, forKey: "imageData"); report.inc("contacts", "avatar_user")
            }
            if let d = readSmall(c.avatarContactPath), let img = imageDataEntity(d) {
                o.setValue(img, forKey: "contactImage"); report.inc("contacts", "avatar_contact")
            }
            if c.hidden { report.inc("contacts", "hidden") }
            contactOID[c.identity] = try permanentID(o)
            report.inserted("contacts")
        }
        try flush(force: true)
    }

    /// review F3: a real Threema Safe restore carries no pictures, so every Android contact already exists on the
    /// iPhone without one. Default: count only (existing iPhone data wins). --fill-missing-avatars: set a picture
    /// only where the existing contact has none (imageData = user-set, contactImage = contact's own profile picture).
    func fillExistingContactAvatar(_ o: NSManagedObject, _ c: NContact) {
        let user = readSmall(c.avatarUserPath), prof = readSmall(c.avatarContactPath)
        guard user != nil || prof != nil else { return }
        let hasUser = o.value(forKey: "imageData") != nil, hasProf = o.value(forKey: "contactImage") != nil
        guard opt.fillMissingAvatars else {
            if (user != nil && !hasUser) || (prof != nil && !hasProf) { report.inc("contacts", "existing_avatar_available_not_filled") }
            return
        }
        if let d = user, !hasUser { o.setValue(d, forKey: "imageData"); report.inc("contacts", "existing_avatar_user_filled") }
        if let d = prof, !hasProf, let img = imageDataEntity(d) {
            o.setValue(img, forKey: "contactImage"); report.inc("contacts", "existing_avatar_contact_filled")
        }
    }

    // MARK: - phase C: groups + group conversations

    func importGroups() throws {
        for g in try norm.groups() {
            guard g.groupId.count == 8 else { report.skipped("groups", "bad_group_id"); continue }
            let key = canonGroupKey(idHex: g.groupId.hex, creator: g.creator)
            normGroups[key] = g
            let mine = key.hasSuffix("-ME")
            let needConv = groupConv[key] == nil
            // review m2: resolve the creator contact BEFORE inserting anything, so a missing creator never leaves a
            // GroupEntity without a ConversationEntity behind (iOS resolves groups through the conversation,
            // EntityFetcher+GroupEntity.swift:7-15).
            var creatorContact: NSManagedObject?
            if needConv && !mine {
                guard let oid = contactOID[g.creator] else {
                    report.skipped("conversations_group", "creator_contact_missing")
                    if groupEnt[key] == nil { report.skipped("groups", "creator_contact_missing") }
                    continue
                }
                creatorContact = obj(oid)
            }
            // Group entity (EntityFetcher+GroupEntity.swift:7-15: creator nil for own groups)
            if groupEnt[key] == nil {
                let ge = insert("Group")
                ge.setValue(g.groupId, forKey: "groupId")
                ge.setValue(mine ? nil : g.creator, forKey: "groupCreator")
                // GroupEntity.GroupState active=0, requestedSync=1, left=2, forcedLeft=3; Android user_state 0/1/2
                let state: Int16 = g.userState == 2 ? 2 : (g.userState == 1 ? 3 : 0)
                ge.setValue(NSNumber(value: state), forKey: "state")
                groupEnt[key] = try permanentID(ge)
                report.inserted("groups")
                report.inc("groups", "state_\(state)")
            } else {
                report.existing("groups")
            }
            if !needConv {
                report.existing("conversations_group")
                if let d = readSmall(g.avatarPath), let oid = groupConv[key] {
                    let conv = obj(oid)
                    if conv.value(forKey: "groupImage") == nil {
                        if opt.fillMissingAvatars, let img = imageDataEntity(d) {
                            conv.setValue(img, forKey: "groupImage"); report.inc("conversations_group", "existing_avatar_filled")
                        } else if !opt.fillMissingAvatars {
                            report.inc("conversations_group", "existing_avatar_available_not_filled")
                        }
                    }
                }
                continue
            }
            let conv = insert("Conversation")
            conv.setValue(g.groupId, forKey: "groupId")
            conv.setValue(g.name, forKey: "groupName")
            conv.setValue(own, forKey: "groupMyIdentity")
            conv.setValue(creatorContact, forKey: "contact")
            conv.setValue(NSNumber(value: Int16(0)), forKey: "category")
            conv.setValue(NSNumber(value: Int16(g.archived ? 1 : 0)), forKey: "visibility")
            conv.setValue(NSNumber(value: false), forKey: "marked")
            conv.setValue(NSNumber(value: Int32(0)), forKey: "unreadMessageCount")
            let members = conv.mutableSetValue(forKey: "members")
            // review m3: members_json is authoritative (NORMALIZED CONTRACT: excluding me, including the creator when
            // Android had them as member). No extra creator insert: iOS derives "creator left" from membership
            // (Group.swift:150-153).
            for m in Set(g.members).sorted() where !meIDs.contains(m) {
                if let oid = contactOID[m] { members.add(obj(oid)) } else { report.inc("conversations_group", "member_contact_missing") }
            }
            if let d = readSmall(g.avatarPath), let img = imageDataEntity(d) {
                conv.setValue(img, forKey: "groupImage"); report.inc("conversations_group", "avatar")
            }
            if g.archived { report.inc("conversations_group", "archived") }
            let oid = try permanentID(conv)
            groupConv[key] = oid
            newConvs.insert(oid)
            if let d = dateFromMs(g.lastUpdateMs ?? g.createdMs) { convFallbackDate[oid] = d }
            report.inserted("conversations_group")
        }
        try flush(force: true)
    }

    func oneToOneConversation(_ identity: String) throws -> NSManagedObjectID? {
        if let c = oneToOneConv[identity] { return c }
        guard let coid = contactOID[identity] else { return nil }
        let conv = insert("Conversation")
        conv.setValue(obj(coid), forKey: "contact")
        conv.setValue(NSNumber(value: Int16(0)), forKey: "category")
        let archived = normContacts[identity]?.archived ?? false
        conv.setValue(NSNumber(value: Int16(archived ? 1 : 0)), forKey: "visibility")
        conv.setValue(NSNumber(value: false), forKey: "marked")
        conv.setValue(NSNumber(value: Int32(0)), forKey: "unreadMessageCount")
        let oid = try permanentID(conv)
        oneToOneConv[identity] = oid
        newConvs.insert(oid)
        if archived { report.inc("conversations_1to1", "archived") }
        if let d = dateFromMs(normContacts[identity]?.lastUpdateMs) { convFallbackDate[oid] = d }
        report.inserted("conversations_1to1")
        return oid
    }

    func conversation(kind: String, key: String) throws -> (NSManagedObjectID, Bool)? {
        switch kind {
        case "contact":
            guard !meIDs.contains(key), let oid = try oneToOneConversation(key) else { return nil }
            return (oid, false)
        case "group":
            guard let oid = groupConv[canonGroupKey(normalizedKey: key)] else { return nil }
            return (oid, true)
        default: return nil
        }
    }

    // MARK: - phase D: ballots

    func importBallots() throws {
        let choices = Dictionary(grouping: try norm.ballotChoices(), by: \.ballotRef)
        let votes = Dictionary(grouping: try norm.ballotVotes(), by: \.ballotRef)
        for b in try norm.ballots() {
            guard let api = b.apiId, api.count == 8 else { report.skipped("ballots", "bad_ballot_id"); continue }
            if let ex = existingBallotIds[api] { ballotByRef[b.refId] = ex; report.existing("ballots"); continue }
            guard let ck = b.chatKind, let key = b.chatKey, let (convOID, _) = try conversation(kind: ck, key: key) else {
                report.skipped("ballots", "conversation_missing"); continue
            }
            let o = insert("Ballot")
            o.setValue(api, forKey: "id")
            // review m1: own polls must carry the identity the store runs with (BallotEntity+Extension.swift:82 isOwn)
            o.setValue(b.creator.map { meIDs.contains($0) ? own : $0 }, forKey: "creatorId")
            o.setValue(b.title, forKey: "title")
            // BallotEntity+Extension.swift:4-18
            o.setValue(NSNumber(value: Int16(b.state == "CLOSED" ? 1 : 0)), forKey: "state")
            o.setValue(NSNumber(value: Int16(b.assessment == "MULTIPLE_CHOICE" ? 1 : 0)), forKey: "assessmentType")
            o.setValue(NSNumber(value: Int16(b.btype == "INTERMEDIATE" ? 1 : 0)), forKey: "type")
            o.setValue(NSNumber(value: Int16(0)), forKey: "choicesType")     // TEXT = 0 (only type in protocol)
            o.setValue(NSNumber(value: Int16(0)), forKey: "displayMode")     // list (results per participant)
            o.setValue(dateFromMs(b.createdMs), forKey: "createDate")
            o.setValue(dateFromMs(b.modifiedMs ?? b.createdMs), forKey: "modifyDate")
            o.setValue(obj(convOID), forKey: "conversation")
            touchedConvs.insert(convOID)
            let bchoices = choices[b.refId] ?? []
            var choiceObjs: [Int64: NSManagedObject] = [:]
            for c in bchoices where choiceObjs[c.choiceId] == nil {
                let co = insert("BallotChoice")
                co.setValue(NSNumber(value: Int32(clamping: c.choiceId)), forKey: "id")
                co.setValue(c.name, forKey: "name")
                co.setValue(NSNumber(value: Int16(clamping: c.orderPos ?? 0)), forKey: "orderPosition")
                co.setValue(dateFromMs(c.createdMs), forKey: "createDate")
                co.setValue(dateFromMs(c.modifiedMs ?? c.createdMs), forKey: "modifyDate")
                co.setValue(o, forKey: "ballot")
                choiceObjs[c.choiceId] = co
                report.inserted("ballot_choices")
            }
            var seenVote = Set<String>()
            for v in votes[b.refId] ?? [] {
                guard let co = choiceObjs[v.choiceId] else { report.skipped("ballot_results", "choice_missing"); continue }
                guard seenVote.insert("\(v.choiceId)|\(v.identity)").inserted else {
                    report.skipped("ballot_results", "duplicate"); continue
                }
                let r = insert("BallotResult")
                // participantId = voter identity (own identity for own votes, BallotChoiceEntity+Extension.swift:64-73)
                r.setValue(meIDs.contains(v.identity) ? own : v.identity, forKey: "participantId")
                r.setValue(NSNumber(value: Int16(v.choice != 0 ? 1 : 0)), forKey: "value")
                r.setValue(dateFromMs(v.createdMs), forKey: "createDate")
                r.setValue(dateFromMs(v.modifiedMs ?? v.createdMs), forKey: "modifyDate")
                r.setValue(co, forKey: "ballotChoice")
                report.inserted("ballot_results")
            }
            ballotByRef[b.refId] = try permanentID(o)
            report.inserted("ballots")
        }
        try flush(force: true)
    }

    // MARK: - phase E: messages

    func importMessages() throws {
        report.info["messages_in_normalized"] = try norm.messageCount()
        var curKey = ""
        var cur: (NSManagedObjectID, Bool)?
        var curMissingReason = ""
        var seenInRun: [NSManagedObjectID: Set<Data>] = [:]
        try norm.forEachMessage { m in
            let key = m.chatKind + "|" + m.chatKey
            if key != curKey {
                curKey = key
                cur = try conversation(kind: m.chatKind, key: m.chatKey)
                if cur == nil {
                    curMissingReason = m.chatKind == "contact" ? (meIDs.contains(m.chatKey) ? "chat_with_self" : "contact_missing")
                        : (m.chatKind == "group" ? "group_missing" : "unsupported_chat_kind")
                }
            }
            guard let (convOID, isGroup) = cur else { report.skipped("messages", curMissingReason); return }
            guard m.msgId.count == 8 else { report.skipped("messages", "bad_msg_id"); return }
            if existingMsgIds[convOID]?.contains(m.msgId) == true { report.skipped("messages", "duplicate_existing"); return }
            if seenInRun[convOID]?.contains(m.msgId) == true { report.skipped("messages", "duplicate_in_input"); return }
            guard let msg = try buildMessage(m, convOID: convOID, isGroup: isGroup) else { return }
            seenInRun[convOID, default: []].insert(m.msgId)
            touchedConvs.insert(convOID)
            pending.append((convOID, m.msgId, msg))
            report.inserted("messages")
            report.inc("messages", "kind_\(msg.entity.name ?? "?")")
            if let d = msg.value(forKey: "date") as? Date {
                importedDates.min = min(importedDates.min ?? d, d)
                importedDates.max = max(importedDates.max ?? d, d)
                importedDatesAll.append(d)
                let excluded = msg.entity.name == "SystemMessage"
                    && SysType.excludedAsLastMessage.contains(Int((msg.value(forKey: "type") as? NSNumber)?.int16Value ?? 0))
                if !excluded, d > (newestImportedDisplayDate[convOID] ?? .distantPast) { newestImportedDisplayDate[convOID] = d }
            }
            try flush()
        }
        try flush(force: true)
    }

    /// Returns the inserted message or nil (skip already counted).
    func buildMessage(_ m: NMessage, convOID: NSManagedObjectID, isGroup: Bool) throws -> NSManagedObject? {
        var isOwn = m.isOwn
        var sender: NSManagedObject?
        let isSystem = m.kind == "call" || m.kind == "group_status"
        switch m.kind {
        case "text", "file", "location", "ballot", "call", "group_status": break
        case "legacy_status": report.skipped("messages", "legacy_status_no_ios_equivalent"); return nil
        default: report.skipped("messages", "unsupported_kind"); return nil
        }
        if isGroup && !isOwn && !isSystem {
            guard let s = m.sender, !s.isEmpty else { report.skipped("messages", "sender_unknown"); return nil }
            if meIDs.contains(s) {
                isOwn = true; report.inc("messages", "incoming_from_me_treated_as_own")
            } else {
                guard let oid = contactOID[s] else { report.skipped("messages", "sender_unknown"); return nil }
                sender = obj(oid)
            }
        }
        guard let date = dateFromMs(m.createdMs ?? m.postedMs ?? m.modifiedMs) else {
            report.skipped("messages", "no_date"); return nil
        }
        let o: NSManagedObject
        switch m.kind {
        case "text":
            o = insert("TextMessage")
            o.setValue(m.deletedMs != nil ? "" : (m.text ?? ""), forKey: "text")
            if let q = m.quotedApiId, q.count == 8 { o.setValue(q, forKey: "quotedMessageId"); report.inc("messages", "quote_v2") }
        case "file":
            o = insert("FileMessage")
            try fillFile(o, m, isOwn: isOwn)
        case "location":
            if m.deletedMs == nil, m.locLat == nil || m.locLon == nil {
                report.skipped("messages", "location_without_coordinates"); return nil
            }
            o = insert("LocationMessage")
            let del = m.deletedMs != nil   // EntityDestroyer.deleteMessageContent: coordinates 0, poi nil
            o.setValue(NSNumber(value: del ? 0 : m.locLat!), forKey: "latitude")
            o.setValue(NSNumber(value: del ? 0 : m.locLon!), forKey: "longitude")
            o.setValue(NSNumber(value: del ? 0 : (m.locAcc ?? 0)), forKey: "accuracy")
            o.setValue(del ? nil : m.locName, forKey: "poiName")
            o.setValue(del ? nil : m.locAddress, forKey: "poiAddress")
        case "ballot":
            guard let ref = m.ballotRef, let boid = ballotByRef[ref] else { report.skipped("messages", "ballot_missing"); return nil }
            o = insert("BallotMessage")
            o.setValue(obj(boid), forKey: "ballot")
            // BallotMessageEntity.ballotState = ballot state at message time (BallotMessageEntity+Extension.swift:21-27)
            o.setValue(NSNumber(value: Int16(m.ballotDataType == 3 ? 1 : 0)), forKey: "ballotState")
        case "call":
            guard !isGroup else { report.skipped("messages", "call_in_group"); return nil }
            guard let (t, arg) = callSystemMessage(m, isOwn: isOwn, date: date) else { return nil }
            o = insert("SystemMessage")
            o.setValue(NSNumber(value: t.rawValue), forKey: "type")
            o.setValue(arg, forKey: "arg")
            report.inc("system_messages", "type_\(t.rawValue)")
        case "group_status":
            guard isGroup else { report.skipped("messages", "group_status_outside_group"); return nil }
            guard let (t, arg) = groupStatusSystemMessage(m, convOID: convOID) else { return nil }
            o = insert("SystemMessage")
            o.setValue(NSNumber(value: t.rawValue), forKey: "type")
            o.setValue(arg, forKey: "arg")
            isOwn = true   // EntityCreator.systemMessageEntity: group status messages are created with isOwn=true
            report.inc("system_messages", "type_\(t.rawValue)")
        default:
            report.skipped("messages", "unsupported_kind"); return nil
        }
        setBase(o, m, date: date, isOwn: isOwn, sender: sender, convOID: convOID, isSystem: isSystem)
        return o
    }

    /// Same label as tools/verify_import.py chat_hash / android_normalize.chat_ref: sha256("<kind>:<key>")[:10]
    func chatHash(_ kind: String, _ key: String) -> String {
        String(SHA256.hash(data: Data("\(kind):\(key)".utf8)).map { String(format: "%02x", $0) }.joined().prefix(10))
    }

    func setBase(_ o: NSManagedObject, _ m: NMessage, date: Date, isOwn: Bool, sender: NSManagedObject?,
                 convOID: NSManagedObjectID, isSystem: Bool) {
        o.setValue(m.msgId, forKey: "id")
        o.setValue(date, forKey: "date")
        o.setValue(dateFromMs(m.postedMs) ?? date, forKey: "remoteSentDate")
        o.setValue(NSNumber(value: isOwn), forKey: "isOwn")
        o.setValue(NSNumber(value: true), forKey: "sent")                 // never "sending" -> nothing is (re)sent
        o.setValue(NSNumber(value: false), forKey: "sendFailed")          // never "failed" -> no retry button
        o.setValue(NSNumber(value: false), forKey: "userack")             // legacy ack -> MessageReaction
        o.setValue(NSNumber(value: false), forKey: "isCreatedFromWeb")
        o.setValue(NSNumber(value: Int16(0)), forKey: "forwardSecurityMode")
        if isOwn {
            let st = (m.state ?? "").uppercased()
            let delivered = ["DELIVERED", "READ", "CONSUMED", "USERACK", "USERDEC"].contains(st) || m.deliveredMs != nil || m.readMs != nil
            let read = ["READ", "CONSUMED"].contains(st) || m.readMs != nil
            o.setValue(NSNumber(value: delivered), forKey: "delivered")
            o.setValue(delivered ? (dateFromMs(m.deliveredMs ?? m.readMs) ?? dateFromMs(m.postedMs) ?? date) : nil, forKey: "deliveryDate")
            o.setValue(NSNumber(value: read || isSystem), forKey: "read")
            o.setValue(read ? (dateFromMs(m.readMs ?? m.deliveredMs) ?? date) : (isSystem ? date : nil), forKey: "readDate")
            if ["SENDFAILED", "PENDING", "SENDING", "UPLOADING", "TRANSCODING", "FS_KEY_MISMATCH"].contains(st) {
                report.inc("messages", "own_unsent_state_normalized_to_sent")
                // review F5/m4: list them (Android uid + hashed chat, no content) so the maintainer knows which
                // messages the recipient never got although iOS shows them as sent.
                var l = report.info["own_unsent_normalized_to_sent"] as? [[String: String]] ?? []
                l.append(["uid": m.uid, "chat": chatHash(m.chatKind, m.chatKey), "android_state": st])
                report.info["own_unsent_normalized_to_sent"] = l
            }
        } else {
            // incoming: delivered=processed (EntityManager+Extension.swift:837-838), read=1 -> no read receipt ever
            o.setValue(NSNumber(value: true), forKey: "delivered")
            o.setValue(date, forKey: "deliveryDate")
            o.setValue(NSNumber(value: true), forKey: "read")
            o.setValue(dateFromMs(m.readMs) ?? date, forKey: "readDate")
        }
        if let e = dateFromMs(m.editedMs) { o.setValue(e, forKey: "lastEditedAt"); report.inc("messages", "edited") }
        if let d = dateFromMs(m.deletedMs) { o.setValue(d, forKey: "deletedAt"); report.inc("messages", "deleted") }
        o.setValue(obj(convOID), forKey: "conversation")
        if let sender { o.setValue(sender, forKey: "sender") }
        if m.starred {
            let mk = insert("MessageMarkers")
            mk.setValue(NSNumber(value: true), forKey: "star")
            o.setValue(mk, forKey: "messageMarkers")
            report.inc("messages", "starred")
        }
    }

    // MARK: file messages

    func fillFile(_ o: NSManagedObject, _ m: NMessage, isOwn: Bool) throws {
        if m.deletedMs != nil {
            // EntityDestroyer.deleteMessageContent (EntityDestroyer.swift:216-285)
            o.setValue("", forKey: "mimeType"); o.setValue("", forKey: "fileName"); o.setValue("", forKey: "caption")
            o.setValue("", forKey: "json"); o.setValue(nil, forKey: "type"); o.setValue(nil, forKey: "fileSize")
            o.setValue(nil, forKey: "blobId"); o.setValue(nil, forKey: "blobThumbnailId"); o.setValue(nil, forKey: "encryptionKey")
            o.setValue(NSNumber(value: false), forKey: "dataAvailable")
            report.inc("files", "deleted_content")
            return
        }
        let mime = (m.fileMime?.isEmpty == false ? m.fileMime! : "application/octet-stream").lowercased()
        let render = Int16(max(0, min(2, m.fileRender ?? 0)))
        let isImage = mime.hasPrefix("image/"), isVideo = mime.hasPrefix("video/"), isAudio = mime.hasPrefix("audio/")
        var meta: [String: Any] = [:]
        if let j = m.fileMetaJson, let d = j.data(using: .utf8), let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
            meta = obj
        }
        var width = (meta["w"] as? NSNumber).map { Int($0.doubleValue.rounded()) }
        var height = (meta["h"] as? NSNumber).map { Int($0.doubleValue.rounded()) }
        var duration = (meta["d"] as? NSNumber)?.doubleValue

        // media
        var mediaURL = resolveMedia(m.mediaPath)
        if m.mediaPath != nil && mediaURL == nil { report.inc("files", "media_path_not_found") }
        if let url = mediaURL, opt.verifyMediaHash, !opt.dryRun, let want = m.mediaSha256?.lowercased(), !want.isEmpty {
            if Media.sha256Hex(of: url) != want { report.skipped("media", "sha256_mismatch"); mediaURL = nil }
        }
        var size = m.fileSize
        var hasData = false
        if let url = mediaURL {
            if opt.dryRun {
                size = ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.int64Value ?? size
                hasData = true
            } else {
                let data = try Data(contentsOf: url, options: .alwaysMapped)
                let fd = insert("FileData")
                fd.setValue(data, forKey: "data")
                o.setValue(fd, forKey: "data")
                size = Int64(data.count)
                pendingBytes += data.count
                hasData = true
            }
            report.inserted("media")
            report.inc("media", "bytes", by: Int(size ?? 0))
        } else {
            report.inc("files", "placeholder_without_data")
        }
        o.setValue(NSNumber(value: hasData), forKey: "dataAvailable")

        // thumbnail: Android thumbnail, else generate for image/video
        var thumb: (data: Data, mime: String)?
        if let d = readSmall(m.thumbPath) { thumb = (d, m.fileThumbMime ?? "image/jpeg"); report.inc("thumbnails", "from_android") }
        else if hasData, !opt.dryRun, let url = mediaURL {
            if isImage {
                thumb = Media.imageThumbnail(url: url, keepAlpha: render == 2 || mime == "image/png")
            } else if isVideo {
                thumb = Media.videoThumbnail(url: url, mime: mime, tmpDir: tmpDir)
            }
            if thumb != nil { report.inc("thumbnails", "generated") } else if isImage || isVideo { report.inc("thumbnails", "generation_failed") }
        } else if hasData && opt.dryRun && (isImage || isVideo) {
            report.inc("thumbnails", "would_generate")
        }
        var thumbEntity: NSManagedObject?
        if let t = thumb {
            thumbEntity = imageDataEntity(t.data)
            if thumbEntity == nil { report.inc("thumbnails", "undecodable_dropped"); thumb = nil }
            else { o.setValue(thumbEntity, forKey: "thumbnail"); report.inserted("thumbnails") }
        }

        // metadata the Android JSON did not carry
        if hasData, !opt.dryRun, let url = mediaURL {
            if (width == nil || height == nil), isImage, let (w, h) = Media.imageSize(url: url) {
                width = w; height = h; report.inc("files", "dimensions_computed")
            }
            if (width == nil || height == nil), isVideo, let (w, h) = Media.videoSize(url: url, mime: mime, tmpDir: tmpDir) {
                width = w; height = h; report.inc("files", "dimensions_computed")
            }
            if duration == nil, isAudio || isVideo, let d = Media.duration(url: url, mime: mime, tmpDir: tmpDir) {
                duration = d; report.inc("files", "duration_computed")
            }
        }

        // blob ids / key (BlobData+state.swift:22-195): see docs/import-mapping.md "FileMessage blob state"
        let key: Data = (m.fileKey?.count == 32 ? m.fileKey! : Media.deterministicBytes("key", m.uid, count: 32))
        let blobId: Data? = hasData ? (m.fileBlobId?.count == 16 ? m.fileBlobId! : Media.deterministicBytes("blob", m.uid, count: 16)) : nil
        let thumbBlobId: Data? = (isOwn && thumbEntity != nil) ? Media.deterministicBytes("thumb", m.uid, count: 16) : nil
        o.setValue(key, forKey: "encryptionKey")
        o.setValue(blobId, forKey: "blobId")
        o.setValue(thumbBlobId, forKey: "blobThumbnailId")
        o.setValue(mime, forKey: "mimeType")
        o.setValue(NSNumber(value: render), forKey: "type")
        o.setValue(m.fileName, forKey: "fileName")
        o.setValue(size.map { NSNumber(value: Int32(clamping: $0)) }, forKey: "fileSize")
        o.setValue(m.fileCaption?.isEmpty == false ? m.fileCaption : nil, forKey: "caption")
        o.setValue(NSNumber(value: Int16(0)), forKey: "origin")   // BlobOrigin.public
        if !isOwn, isAudio, (m.state ?? "").uppercased() == "CONSUMED" {
            o.setValue(dateFromMs(m.modifiedMs ?? m.readMs ?? m.createdMs), forKey: "consumed")
            report.inc("files", "voice_consumed")
        }

        // json like FileMessageEncoder.jsonDataForMessage (FileMessageEncoder.m:51-110), keys FileMessageKeys.h
        var json: [String: Any] = ["m": mime, "j": Int(render), "i": 0, "k": key.hex]
        if let n = m.fileName { json["n"] = n }
        if let s = size { json["s"] = s }
        if let b = blobId { json["b"] = b.hex }
        if let t = thumbBlobId { json["t"] = t.hex }
        if let c = m.fileCaption, !c.isEmpty { json["d"] = c }
        if let t = thumb { json["p"] = t.mime }
        var x: [String: Any] = [:]
        if let d = duration, d > 0 { x["d"] = d }            // FileMessageMetadataJSON.duration: Double
        if let h = height, h > 0 { x["h"] = h }              // Int (a float here would break JSONDecoder)
        if let w = width, w > 0 { x["w"] = w }
        if !x.isEmpty { json["x"] = x }
        let jd = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])
        o.setValue(String(data: jd, encoding: .utf8), forKey: "json")
        report.inc("files", "render_\(render)_\(mime.split(separator: "/").first ?? "?")")
    }

    // MARK: system messages

    func callSystemMessage(_ m: NMessage, isOwn: Bool, date: Date) -> (SysType, Data)? {
        // Android VoipStatusDataModel: 1 MISSED, 2 FINISHED, 3 REJECTED, 4 ABORTED; reason 0 UNKNOWN 1 BUSY 2 TIMEOUT
        // 3 REJECTED 4 DISABLED 5 OFF_HOURS. iOS rendering: SystemMessageEntity+type.swift:438-492.
        var callTime: String?
        let t: SysType
        switch m.callStatus {
        case 2:
            t = .callEnded
            if let d = m.callDurationS, d > 0 {
                secondsFormatter.allowedUnits = d > 3600 ? [.hour, .minute, .second] : [.minute, .second]
                callTime = secondsFormatter.string(from: TimeInterval(d))
            }
        case 1: t = .callMissed
        case 4: t = .callEnded            // aborted by caller -> "call_canceled" (own, no CallTime)
        case 3:
            switch m.callReason {
            case 1: t = .callRejectedBusy
            case 2: t = isOwn ? .callRejectedTimeout : .callMissed      // VoIPCallService.swift:2844-2845
            case 3: t = .callRejected
            case 4: t = .callRejectedDisabled
            case 5: t = .callRejectedOffHours
            default: t = isOwn ? .callRejectedUnknown : .callMissed    // VoIPCallService.swift:2855-2856
            }
        default:
            report.skipped("messages", "call_status_unknown"); return nil
        }
        // arg JSON as VoIPCallService.swift:2786-2802 / CallSystemMessageHelper.swift:98-109
        let df = DateFormatter()
        df.locale = Locale(identifier: "de_CH")
        df.dateStyle = .none
        df.timeStyle = .short
        var info: [String: Any] = ["DateString": df.string(from: date), "CallInitiator": NSNumber(value: isOwn)]
        if let callTime { info["CallTime"] = callTime }
        let arg = (try? JSONSerialization.data(withJSONObject: info, options: [.prettyPrinted, .sortedKeys])) ?? Data()
        return (t, arg)
    }

    func groupStatusSystemMessage(_ m: NMessage, convOID: NSManagedObjectID) -> (SysType, Data?)? {
        let ident = m.gstatusIdentity.flatMap { $0.isEmpty ? nil : $0 }
        let isMe = ident.map { meIDs.contains($0) } ?? false
        let name = m.gstatusName ?? ""
        func nameArg() -> Data? { ident.map { Data(displayName($0).utf8) } }   // GroupManager.swift:1327-1334
        func vote(_ show: Bool, _ updated: Bool?) -> Data? {
            try? JSONEncoder().encode(VoteInfo(ballotTitle: name, voterID: show ? (ident ?? "") : "", showIntermediateResults: show,
                                               updatedVote: updated))
        }
        let creatorIsMe: Bool = {
            if let g = normGroups.first(where: { groupConv[$0.key] == convOID }) { return g.key.hasSuffix("-ME") }
            return false
        }()
        switch m.gstatusType {
        case 0: // CREATED: iOS shows "you were added" for foreign groups, nothing for own groups
            if creatorIsMe { report.skipped("messages", "group_status_no_ios_equivalent"); return nil }
            return (.groupSelfAdded, nil)
        case 1: return (.renameGroup, Data(name.utf8))
        case 2: return (.groupProfilePictureChanged, nil)
        case 3:
            if isMe { return (.groupSelfAdded, nil) }
            guard let a = nameArg() else { report.skipped("messages", "group_status_missing_identity"); return nil }
            return (.groupMemberAdd, a)
        case 4:
            if isMe { return (.groupSelfLeft, nil) }
            guard let a = nameArg() else { report.skipped("messages", "group_status_missing_identity"); return nil }
            return (.groupMemberLeave, a)
        case 5:
            if isMe { return (.groupSelfRemoved, nil) }
            guard let a = nameArg() else { report.skipped("messages", "group_status_missing_identity"); return nil }
            return (.groupMemberForcedLeave, a)
        case 6: return (.startNoteGroupInfo, nil)
        case 7: return (.endNoteGroupInfo, nil)
        case 8: return (.vote, vote(true, false))
        case 9: return (.voteUpdated, vote(true, true))
        case 10: return (.vote, vote(false, false))
        case 13: return (.groupCreatorLeft, nil)
        case 11, 12:
            report.skipped("messages", "group_status_no_ios_equivalent"); return nil
        default:
            report.skipped("messages", "group_status_unknown_type"); return nil
        }
    }

    // MARK: - phase F: reactions

    func importReactions() throws {
        var seen = Set<String>()
        for r in try norm.reactions() {
            guard let (convOID, isGroup) = try conversation(kind: r.chatKind, key: r.chatKey) else {
                report.skipped("reactions", "conversation_missing"); continue
            }
            guard let msgOID = insertedMsgs[convOID]?[r.targetMsgId] else {
                report.skipped("reactions", existingMsgIds[convOID]?.contains(r.targetMsgId) == true ? "target_preexisting" : "target_missing")
                continue
            }
            let msg = obj(msgOID)
            if msg.entity.name == "SystemMessage" { report.skipped("reactions", "target_not_reactable"); continue }
            if msg.value(forKey: "deletedAt") != nil { report.skipped("reactions", "target_deleted"); continue }
            var creator: NSManagedObject?
            var creatorKey = ""
            if let s = r.sender, !s.isEmpty, !meIDs.contains(s) {
                guard let coid = contactOID[s] else { report.skipped("reactions", "reactor_unknown"); continue }
                if !isGroup, s != r.chatKey { report.skipped("reactions", "reactor_not_in_chat"); continue }
                creator = obj(coid)
                creatorKey = s
            }
            guard r.emoji.isEmpty == false else { report.skipped("reactions", "empty_emoji"); continue }
            // uniqueness constraint (creator, reaction, message) (MessageReaction entity / ios-schema.sql)
            guard seen.insert("\(msgOID.uriRepresentation().absoluteString)|\(creatorKey)|\(r.emoji)").inserted else {
                report.skipped("reactions", "duplicate"); continue
            }
            let o = insert("MessageReaction")
            o.setValue(r.emoji, forKey: "reaction")
            o.setValue(dateFromMs(r.reactedMs) ?? (msg.value(forKey: "date") as? Date) ?? Date(), forKey: "date")
            o.setValue(msg, forKey: "message")
            o.setValue(creator, forKey: "creator")   // nil = own reaction (MessageReactionEntity.swift init doc)
            report.inserted("reactions")
            report.inc("reactions", "source_\(r.source ?? "?")")
            try flush()
        }
        try flush(force: true)
    }

    // MARK: - phase G: nonces

    func importNonces() throws {
        if opt.noNonces { report.inc("nonces", "disabled_by_flag"); return }
        if ownOverridden {
            // hashes are HMAC-SHA256(key = own identity) (NonceHasher.m:12-24) - useless for another identity
            try norm.forEachNonce { _, _ in report.skipped("nonces", "own_identity_overridden") }
            return
        }
        let nf = NSFetchRequest<NSDictionary>(entityName: "Nonce")
        nf.resultType = .dictionaryResultType
        nf.propertiesToFetch = ["nonce"]
        var existing = Set<Data>()
        for d in try ctx.fetch(nf) { if let n = d["nonce"] as? Data { existing.insert(n) } }
        var count = 0
        try norm.forEachNonce { kind, h in
            guard h.count == 32 else { report.skipped("nonces", "bad_hash"); return }
            guard existing.insert(h).inserted else { report.skipped("nonces", "duplicate"); return }
            let o = insert("Nonce")
            o.setValue(h, forKey: "nonce")
            report.inserted("nonces")
            report.inc("nonces", "kind_\(kind)")
            count += 1
            if count % 5000 == 0 { try flush(force: true) }
        }
        try flush(force: true)
    }

    // MARK: - phase H: conversations

    /// MessageFetcher.lastDisplayMessage (MessageFetcher.swift:158-184, 392-400, 486-491)
    func lastDisplayMessage(_ conv: NSManagedObject) throws -> NSManagedObject? {
        let f = NSFetchRequest<NSManagedObject>(entityName: "Message")
        f.predicate = NSPredicate(format: "conversation == %@", conv)
        f.sortDescriptors = [NSSortDescriptor(key: "date", ascending: false), NSSortDescriptor(key: "remoteSentDate", ascending: false)]
        f.fetchBatchSize = 50
        var offset = 0
        while true {
            f.fetchOffset = offset
            f.fetchLimit = 50
            let rows = try ctx.fetch(f)
            if rows.isEmpty { return nil }
            for r in rows {
                if r.entity.name == "SystemMessage",
                   SysType.excludedAsLastMessage.contains(Int((r.value(forKey: "type") as? NSNumber)?.int16Value ?? 0)) { continue }
                return r
            }
            offset += rows.count
        }
    }

    func finalizeConversations() throws {
        for oid in newConvs.union(touchedConvs).sorted(by: { $0.uriRepresentation().absoluteString < $1.uriRepresentation().absoluteString }) {
            let conv = obj(oid)
            let last = try lastDisplayMessage(conv)
            if newConvs.contains(oid), last == nil, conv.value(forKey: "groupId") == nil,
               (conv.value(forKey: "ballots") as? NSSet)?.count ?? 0 == 0 {
                let mf = NSFetchRequest<NSManagedObject>(entityName: "Message")
                mf.predicate = NSPredicate(format: "conversation == %@", conv)
                if try ctx.count(for: mf) == 0 {
                    // created lazily but every message of that chat was skipped -> do not leave an empty chat behind
                    ctx.delete(conv)
                    oneToOneConv = oneToOneConv.filter { $0.value != oid }
                    report.inc("conversations_1to1", "created_then_removed_empty")
                    report.inc("conversations_1to1", "inserted", by: -1)
                    continue
                }
            }
            if newConvs.contains(oid) {
                conv.setValue(last, forKey: "lastMessage")
                let lu = (last?.value(forKey: "date") as? Date) ?? convFallbackDate[oid]
                conv.setValue(lu, forKey: "lastUpdate")
                // unreadMessageCount = count(isOwn == NO AND read == NO) (EntityFetcher+BaseMessage.swift:643-645)
                let uf = NSFetchRequest<NSManagedObject>(entityName: "Message")
                uf.predicate = NSPredicate(format: "conversation == %@ AND isOwn == NO AND read == NO", conv)
                conv.setValue(NSNumber(value: Int32(try ctx.count(for: uf))), forKey: "unreadMessageCount")
                report.inc("conversations_finalized", lu == nil ? "new_unlisted_no_lastUpdate" : "new")
            } else if let newest = newestImportedDisplayDate[oid] {
                let curLast = conv.value(forKey: "lastMessage") as? NSManagedObject
                let curLastDate = curLast?.value(forKey: "date") as? Date
                if let last, curLast == nil || (curLastDate ?? .distantPast) < newest, last != curLast {
                    conv.setValue(last, forKey: "lastMessage")
                    report.inc("conversations_finalized", "existing_lastMessage_updated")
                }
                let curUpdate = conv.value(forKey: "lastUpdate") as? Date
                if curUpdate == nil || curUpdate! < newest {
                    conv.setValue(newest, forKey: "lastUpdate")
                    report.inc("conversations_finalized", "existing_lastUpdate_updated")
                } else {
                    report.inc("conversations_finalized", "existing_unchanged")
                }
            }
            try flush()
        }
        try flush(force: true)
    }

    // MARK: - retention

    func retentionCheck() {
        var ret: [String: Any] = [:]
        ret["imported_oldest"] = importedDates.min.map { ISO8601DateFormatter().string(from: $0) } ?? NSNull()
        ret["imported_newest"] = importedDates.max.map { ISO8601DateFormatter().string(from: $0) } ?? NSNull()
        // UserSettings KeepMessagesDays (UserSettings.m:259,374; default -1 = forever) is in the app-group defaults
        // Library/Preferences/group.ch.threema.plist - not in the store. MDM th_keep_messages_days only for Work.
        if let p = opt.appPrefs {
            if let d = try? Data(contentsOf: p),
               let plist = try? PropertyListSerialization.propertyList(from: d, format: nil) as? [String: Any] {
                let days = (plist["KeepMessagesDays"] as? NSNumber)?.intValue
                ret["KeepMessagesDays"] = days ?? NSNull()
                if let days, days > 0 {
                    // MessageRetentionManagerModel.keepMessagesDays clamps 1..<7 -> 7, >=3650 -> 3650
                    let eff = days < 7 ? 7 : min(days, 3650)
                    let cutoff = Date().addingTimeInterval(-Double(eff) * 86400)
                    let n = importedDatesAll.filter { $0 < cutoff }.count
                    ret["effective_days"] = eff
                    ret["imported_messages_that_would_be_deleted"] = n
                    report.warn("RETENTION: KeepMessagesDays=\(days) would delete \(n) imported messages on next app launch - set 'Keep messages' to forever first")
                } else {
                    ret["status"] = "forever (no deletion)"
                }
            } else {
                ret["status"] = "app prefs plist unreadable"
                report.warn("app prefs plist given but unreadable")
            }
        } else {
            ret["status"] = "unknown - pass --app-prefs <group.ch.threema.plist> from the iPhone backup to check KeepMessagesDays"
            report.warn("retention setting not checked (no --app-prefs)")
        }
        report.info["retention"] = ret
    }
}
