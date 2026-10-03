// SPDX-License-Identifier: AGPL-3.0-or-later
// ThreemaSimInject.dylib -- SIMULATOR ONLY test hook, injected via SIMCTL_CHILD_DYLD_INSERT_LIBRARIES.
//
// Runs before Threema's main() and prepares the state Threema iOS 7.4 checks at launch
// (AppLaunchSequenceManager.run -> AppSetup.isCompleted):
//   1. Keychain generic-password item exactly as Keychain/KeychainProvider.swift stores `.identity()`:
//        kSecAttrLabel="Threema identity 1", kSecAttrAccount=<ID>, kSecValueData=<32B secret key>,
//        kSecAttrGeneric=<32B public key>, kSecAttrService=<server group>, kSecAttrIsInvisible=YES,
//        kSecAttrAccessible=AfterFirstUnlockThisDeviceOnly  (consumer build: no remote-secret encryption)
//   2. App-group defaults (suite = Info.plist ThreemaAppGroupIdentifier, "group.ch.threema"):
//        AppSetupState=40 (.complete), AppMigratedToVersion=<latest, 36 for 7.4> (skip AppMigration)
//   3. Copies the DB into the group container (THREEMA_SIM_DB_DIR) and removes the "APP_SETUP_NOT_COMPLETED" marker
//      (preLaunchSetup only creates the marker if no DB exists at that point).
//   4. NETWORK KILL-SWITCH (default on): even with -isRunningForScreenshots the app calls the directory server
//      (identity/fetch_priv, identity/set_featuremask). We block all non-loopback IPv4/IPv6 connect()/connectx()
//      via dyld interposing and fail every http(s) URLSession request via an NSURLProtocol. THREEMA_SIM_ALLOW_NET=1 disables.
//
// Config via env (pass with SIMCTL_CHILD_ prefix on `simctl launch`):
//   THREEMA_SIM_IDENTITY_JSON   path to JSON from gen_test_identity.py (host path is readable inside the sim)
//   THREEMA_SIM_MIGRATED_TO     optional int, default 36
//   THREEMA_SIM_SKIP_DEFAULTS   optional "1": only write keychain
//   THREEMA_SIM_DB_DIR          host dir with ThreemaData.sqlite(+-wal/-shm, .ThreemaData_SUPPORT); copied into the
//                               group container on first launch (container is created lazily on the simulator)
//
// UI DRIVER (no system prompts, no taps; see openDriverStart below):
//   THREEMA_SIM_OPEN            'list' | 'contact:<ID or name>' | 'group:<name or 16-hex groupId>' | 'name:<any>'
//                               Opens the chat in-process by posting kNotificationShowConversation
//                               (ThreemaFramework/Constants.h:26, "ThreemaShowConversation") with
//                               userInfo {conversation: ConversationEntity, forceCompose: NO} -- exactly what
//                               RecentTableDataSource.m:133 / NotificationManager.swift:168 do. The observer is
//                               AppCoordinatorNotificationHandler.swift:139-163 -> AppCoordinator.show(conversation:)
//                               (AppCoordinator.swift:640-651, dismisses modals, switches tab) -> ConversationListCoordinator.show.
//                               The ConversationEntity is fetched via BusinessInjector.ui.entityManager.entityFetcher
//                               (all @objc: BusinessInjector.swift:15/139, EntityManager.swift:16).
//   THREEMA_SIM_SCROLL_UP       optional N: scroll the chat table (ChatViewTableView) up N screens before "ready 0"
//   THREEMA_SIM_PAGES           optional M: after "ready 0", wait for host ack file and scroll one more screen, M times
//   THREEMA_SIM_CTRL_DIR        host dir for the handshake: dylib writes 'state' ("<tag> ready <page>" | "<tag> done" |
//                               "<tag> error <reason>"), host creates 'ack-<page>' to request the next page
//   THREEMA_SIM_RUN_TAG         opaque tag echoed in every state line (host distinguishes launches)
//   THREEMA_SIM_SETTLE_SECS     wait after chat is visible / after each scroll (default 3)
//
//   THREEMA_SIM_PROBE_ONLINE    optional "1" (review appsafety M1): the app believes it is LOGGED IN to the chat server
//                               (-[ServerConnector connectionState] -> ConnectionStateLoggedIn) while the kill-switch
//                               stays ON. Every attempt to send / reflect / ack a chat message is intercepted
//                               (-[ServerConnector sendMessage:|reflectMessage:|completedProcessingMessage:]) and
//                               counted, never delivered. Opening chats then runs the connected code paths
//                               (BlobManager auto-sync, read receipts, task spool) instead of stopping at notConnected.
//   THREEMA_SIM_OPEN=probe      with PROBE_ONLINE: after the list is up, drive BlobManager for EVERY FileMessage in the
//                               store (autoSyncBlobs = display path, then syncBlobs = explicit up/download path, via
//                               BlobManagerObjCWrapper), then TaskManager.spool, and report
//                               "PROBE {json}" (counts only) before "ready 0".
//
// Build: tools/sim/build-inject.sh

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <CoreData/CoreData.h>
#import <Security/Security.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <UserNotifications/UserNotifications.h>
#import <sys/socket.h>
#import <netinet/in.h>
#import <errno.h>
#import <stdlib.h>
#import <string.h>

// ---------- suppress the notification-permission alert (keeps screenshots clean; answers "not granted") ----------
static void suppressNotificationPrompt(void) {
    Class c = [UNUserNotificationCenter class];
    SEL sel = @selector(requestAuthorizationWithOptions:completionHandler:);
    Method m = class_getInstanceMethod(c, sel);
    if (!m) return;
    IMP repl = imp_implementationWithBlock(^(id self_, UNAuthorizationOptions o, void (^done)(BOOL, NSError *)) {
        if (done) done(NO, nil);
    });
    method_setImplementation(m, repl);
}

// ---------- network kill-switch ----------
static int gBlockNet = 1;

static int isBlockedAddr(const struct sockaddr *sa) {
    if (!gBlockNet || sa == NULL) return 0;
    if (sa->sa_family == AF_INET) {
        const struct sockaddr_in *in = (const struct sockaddr_in *)sa;
        return (ntohl(in->sin_addr.s_addr) >> 24) != 127;   // allow 127.0.0.0/8 only
    }
    if (sa->sa_family == AF_INET6) {
        const struct sockaddr_in6 *in6 = (const struct sockaddr_in6 *)sa;
        if (IN6_IS_ADDR_LOOPBACK(&in6->sin6_addr)) return 0;
        if (IN6_IS_ADDR_V4MAPPED(&in6->sin6_addr)) return in6->sin6_addr.s6_addr[12] != 127;
        return 1;
    }
    return 0;   // AF_UNIX etc. (XPC, logging) untouched
}

static int sim_connect(int fd, const struct sockaddr *addr, socklen_t len) {
    if (isBlockedAddr(addr)) { errno = ENETUNREACH; return -1; }
    return connect(fd, addr, len);
}
static int sim_connectx(int fd, const sa_endpoints_t *eps, sae_associd_t aid, unsigned int flags,
                        const struct iovec *iov, unsigned int iovcnt, size_t *len, sae_connid_t *cid) {
    if (eps && isBlockedAddr(eps->sae_dstaddr)) { errno = ENETUNREACH; return -1; }
    return connectx(fd, eps, aid, flags, iov, iovcnt, len, cid);
}
__attribute__((used)) static struct { const void *replacement; const void *replacee; } sim_interposers[]
    __attribute__((section("__DATA,__interpose"))) = {
        {(const void *)sim_connect, (const void *)connect},
        {(const void *)sim_connectx, (const void *)connectx},
};

static volatile long gBlockedTotal = 0, gBlockedBlob = 0;

@interface ThreemaSimNoNetProtocol : NSURLProtocol
@end
@implementation ThreemaSimNoNetProtocol
+ (BOOL)canInitWithRequest:(NSURLRequest *)r {
    NSString *s = r.URL.scheme.lowercaseString;
    BOOL block = gBlockNet && ([s isEqualToString:@"http"] || [s isEqualToString:@"https"] || [s isEqualToString:@"wss"] || [s isEqualToString:@"ws"]);
    if (block) {
        NSLog(@"[ThreemaSimInject] BLOCKED %@ %@%@", r.HTTPMethod, r.URL.host, r.URL.path);  // no bodies/queries
        __sync_fetch_and_add(&gBlockedTotal, 1);
        if ([r.URL.host.lowercaseString containsString:@"blob"]) __sync_fetch_and_add(&gBlockedBlob, 1);
    }
    return block;
}
+ (NSURLRequest *)canonicalRequestForRequest:(NSURLRequest *)r { return r; }
- (void)startLoading {
    [self.client URLProtocol:self didFailWithError:[NSError errorWithDomain:NSURLErrorDomain
                                                                       code:NSURLErrorNotConnectedToInternet userInfo:nil]];
}
- (void)stopLoading {}
@end

static void swizzleConfigGetter(SEL sel) {
    Class meta = object_getClass([NSURLSessionConfiguration class]);
    Method m = class_getClassMethod([NSURLSessionConfiguration class], sel);
    if (!m) return;
    NSURLSessionConfiguration *(*orig)(id, SEL) = (void *)method_getImplementation(m);
    IMP repl = imp_implementationWithBlock(^NSURLSessionConfiguration *(id cls) {
        NSURLSessionConfiguration *c = orig(cls, sel);
        c.protocolClasses = [@[ThreemaSimNoNetProtocol.class] arrayByAddingObjectsFromArray:c.protocolClasses ?: @[]];
        return c;
    });
    class_replaceMethod(meta, sel, repl, method_getTypeEncoding(m));
}

// Background sessions run in nsurlsessiond (another process: neither interposing nor NSURLProtocol reach it).
// Threema 7.4 only creates them for delegate-based HTTPClient calls (URLSessionManager.swift:29-58); redirect any such
// configuration to an in-process ephemeral one that carries the blocking protocol, and log it.
static void redirectBackgroundSessions(void) {
    Class meta = object_getClass([NSURLSessionConfiguration class]);
    SEL sel = @selector(backgroundSessionConfigurationWithIdentifier:);
    Method m = class_getClassMethod([NSURLSessionConfiguration class], sel);
    if (!m) return;
    IMP repl = imp_implementationWithBlock(^NSURLSessionConfiguration *(id cls, NSString *ident) {
        NSLog(@"[ThreemaSimInject] BACKGROUND_SESSION_REDIRECTED (in-process, blocked)");
        NSURLSessionConfiguration *c = [NSURLSessionConfiguration ephemeralSessionConfiguration];  // swizzled: has protocol
        return c;
    });
    class_replaceMethod(meta, sel, repl, method_getTypeEncoding(m));
}

static void installNetworkKillSwitch(void) {
    const char *allow = getenv("THREEMA_SIM_ALLOW_NET");
    gBlockNet = !(allow && strcmp(allow, "1") == 0);
    if (!gBlockNet) { NSLog(@"[ThreemaSimInject] network NOT blocked (THREEMA_SIM_ALLOW_NET=1)"); return; }
    [NSURLProtocol registerClass:ThreemaSimNoNetProtocol.class];
    swizzleConfigGetter(@selector(defaultSessionConfiguration));
    swizzleConfigGetter(@selector(ephemeralSessionConfiguration));
    redirectBackgroundSessions();
    NSLog(@"[ThreemaSimInject] network kill-switch active (non-loopback connect/connectx + http(s)/ws(s) blocked)");
}

static NSData *hexData(NSString *hex) {
    NSMutableData *d = [NSMutableData dataWithCapacity:hex.length / 2];
    for (NSUInteger i = 0; i + 1 < hex.length; i += 2) {
        unsigned int b = 0;
        [[NSScanner scannerWithString:[hex substringWithRange:NSMakeRange(i, 2)]] scanHexInt:&b];
        uint8_t c = (uint8_t)b;
        [d appendBytes:&c length:1];
    }
    return d;
}

// ---------- UI driver: open a conversation in-process, optionally scroll, handshake with the host ----------
static NSString *gOpenTarget, *gCtrlDir, *gRunTag;
static NSInteger gScrollUp = 0, gPages = 0;
static double gSettle = 3.0;
static BOOL gDriverStarted = NO;
static NSInteger gPollCount = 0;

static void driverState(NSString *s) {
    NSString *line = [NSString stringWithFormat:@"%@ %@", gRunTag ?: @"-", s];
    NSLog(@"[ThreemaSimInject] STATE %@", line);
    if (gCtrlDir.length) {
        NSError *e = nil;
        if (![[line stringByAppendingString:@"\n"] writeToFile:[gCtrlDir stringByAppendingPathComponent:@"state"]
                                                     atomically:YES encoding:NSUTF8StringEncoding error:&e]) {
            NSLog(@"[ThreemaSimInject] cannot write state file: %@", e.localizedDescription);
        }
    }
}

static void collectVCs(UIViewController *vc, NSMutableArray *out) {
    if (!vc || [out containsObject:vc]) return;
    [out addObject:vc];
    for (UIViewController *c in vc.childViewControllers) collectVCs(c, out);
    if (vc.presentedViewController) collectVCs(vc.presentedViewController, out);
}

static NSArray<UIViewController *> *allVisibleVCs(void) {
    NSMutableArray *vcs = [NSMutableArray array];
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if (![scene isKindOfClass:UIWindowScene.class]) continue;
        for (UIWindow *w in ((UIWindowScene *)scene).windows) collectVCs(w.rootViewController, vcs);
    }
    NSMutableArray *vis = [NSMutableArray array];
    for (UIViewController *vc in vcs) if (vc.isViewLoaded && vc.view.window) [vis addObject:vc];
    return vis;
}

static UIViewController *visibleVC(NSString *suffix) {
    for (UIViewController *vc in allVisibleVCs()) {
        NSString *n = NSStringFromClass(vc.class);
        if ([n isEqualToString:suffix] || [n hasSuffix:[@"." stringByAppendingString:suffix]]) return vc;
    }
    return nil;
}

static UIScrollView *findTable(UIView *v, NSString *classSuffix) {
    if ([v isKindOfClass:UIScrollView.class] && [NSStringFromClass(v.class) hasSuffix:classSuffix]) return (UIScrollView *)v;
    for (UIView *s in v.subviews) { UIScrollView *t = findTable(s, classSuffix); if (t) return t; }
    return nil;
}

static id safeKV(id obj, NSString *key) {
    @try { return [obj valueForKey:key]; } @catch (NSException *e) { return nil; }
}

static NSString *hexOf(NSData *d) {
    NSMutableString *s = [NSMutableString string];
    const uint8_t *b = d.bytes;
    for (NSUInteger i = 0; i < d.length; i++) [s appendFormat:@"%02x", b[i]];
    return s;
}

// Returns a ConversationEntity (main-context object) or nil. Sets *why on failure.
static id resolveConversation(NSString *target, NSString **why) {
    Class biClass = NSClassFromString(@"ThreemaFramework.BusinessInjector");
    SEL uiSel = NSSelectorFromString(@"ui");
    if (!biClass || ![biClass respondsToSelector:uiSel]) { *why = @"BusinessInjector.ui not found"; return nil; }
    id bi = ((id (*)(id, SEL))objc_msgSend)(biClass, uiSel);
    id em = safeKV(bi, @"entityManager");
    id fetcher = safeKV(em, @"entityFetcher");
    SEL frcSel = NSSelectorFromString(@"fetchedResultsControllerForGroupConversationEntities");
    if (!fetcher || ![fetcher respondsToSelector:frcSel]) { *why = @"entityFetcher not available"; return nil; }
    NSFetchedResultsController *frc = ((id (*)(id, SEL))objc_msgSend)(fetcher, frcSel);
    NSManagedObjectContext *moc = frc.managedObjectContext;
    if (!moc) { *why = @"no managed object context"; return nil; }

    NSString *kind = @"name", *value = target;
    NSRange colon = [target rangeOfString:@":"];
    if (colon.location != NSNotFound) {
        kind = [target substringToIndex:colon.location];
        value = [target substringFromIndex:colon.location + 1];
    }
    __block id found = nil;
    __block NSUInteger scanned = 0;
    __block NSError *ferr = nil;
    [moc performBlockAndWait:^{
        NSFetchRequest *fr = [NSFetchRequest fetchRequestWithEntityName:@"Conversation"];
        NSArray *convs = [moc executeFetchRequest:fr error:&ferr];
        scanned = convs.count;
        for (id conv in convs) {
            NSData *gid = safeKV(conv, @"groupId");
            BOOL isGroup = gid != nil;
            BOOL isDL = safeKV(conv, @"distributionList") != nil;
            NSMutableArray<NSString *> *keys = [NSMutableArray array];
            if (isGroup) {
                NSString *gn = safeKV(conv, @"groupName");
                if (gn) [keys addObject:gn];
                [keys addObject:hexOf(gid)];
            } else if (!isDL) {
                id c = safeKV(conv, @"contact");
                NSString *ident = safeKV(c, @"identity");
                NSString *fn = safeKV(c, @"firstName"), *ln = safeKV(c, @"lastName"), *nick = safeKV(c, @"publicNickname");
                if (ident) [keys addObject:ident];
                NSString *full = [[NSString stringWithFormat:@"%@ %@", fn ?: @"", ln ?: @""]
                                  stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
                if (full.length) [keys addObject:full];
                if (fn.length) [keys addObject:fn];
                if (nick.length) { [keys addObject:nick]; [keys addObject:[@"~" stringByAppendingString:nick]]; }
            }
            if ([kind isEqualToString:@"group"] && !isGroup) continue;
            if ([kind isEqualToString:@"contact"] && (isGroup || isDL)) continue;
            for (NSString *k in keys) {
                if ([k isEqualToString:value] || [k.lowercaseString isEqualToString:value.lowercaseString]) { found = conv; break; }
            }
            if (found) break;
        }
    }];
    if (!found) *why = [NSString stringWithFormat:@"no conversation matches (scanned %lu%@)", (unsigned long)scanned,
                        ferr ? [@", fetch error " stringByAppendingString:ferr.localizedDescription] : @""];
    return found;
}

// Scroll the chat table one screen up. Returns NO if already at the top.
static BOOL scrollOneScreenUp(void) {
    UIViewController *chat = visibleVC(@"ChatViewController");
    UIScrollView *tv = chat ? findTable(chat.view, @"ChatViewTableView") : nil;
    if (!tv) tv = chat ? findTable(chat.view, @"TableView") : nil;
    if (!tv) { NSLog(@"[ThreemaSimInject] scroll: no chat table visible"); return NO; }
    UIEdgeInsets in = tv.adjustedContentInset;
    CGFloat minY = -in.top;
    CGFloat step = (tv.bounds.size.height - in.top - in.bottom) * 0.85;
    if (tv.contentOffset.y <= minY + 1) return NO;
    CGFloat y = MAX(minY, tv.contentOffset.y - step);
    [tv setContentOffset:CGPointMake(tv.contentOffset.x, y) animated:NO];
    NSLog(@"[ThreemaSimInject] scrolled chat table to y=%.0f (contentHeight=%.0f)", y, tv.contentSize.height);
    return YES;
}

static void afterSecs(double s, dispatch_block_t b) {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(s * NSEC_PER_SEC)), dispatch_get_main_queue(), b);
}

static void driverPage(NSInteger page);
static void probeRun(void);

static void driverWaitAck(NSInteger page, NSInteger tries) {
    NSString *ack = [gCtrlDir stringByAppendingPathComponent:[NSString stringWithFormat:@"ack-%ld", (long)page]];
    if ([NSFileManager.defaultManager fileExistsAtPath:ack]) {
        if (page >= gPages) { driverState(@"done"); return; }
        BOOL moved = scrollOneScreenUp();
        afterSecs(gSettle, ^{ driverState([NSString stringWithFormat:@"ready %ld%@", (long)(page + 1), moved ? @"" : @" top"]);
                              driverPage(page + 1); });
        return;
    }
    if (tries > 600) { driverState(@"error ack timeout"); return; }   // 5 min
    afterSecs(0.5, ^{ driverWaitAck(page, tries + 1); });
}

static void driverPage(NSInteger page) {
    if (gCtrlDir.length == 0) { if (page >= gPages) driverState(@"done"); return; }
    driverWaitAck(page, 0);
}

static void driverScrollInitial(NSInteger remaining) {
    if (remaining <= 0) {
        afterSecs(gSettle, ^{ driverState(@"ready 0"); driverPage(0); });
        return;
    }
    scrollOneScreenUp();
    afterSecs(1.5, ^{ driverScrollInitial(remaining - 1); });
}

static void driverWaitChat(NSInteger tries) {
    if (visibleVC(@"ChatViewController")) {
        NSLog(@"[ThreemaSimInject] chat view visible");
        afterSecs(gSettle, ^{ driverScrollInitial(gScrollUp); });
        return;
    }
    if (tries > 60) { driverState(@"error chat view did not appear"); return; }
    afterSecs(0.5, ^{ driverWaitChat(tries + 1); });
}

static void driverPoll(void) {
    gPollCount++;
    if (!visibleVC(@"ConversationListViewController")) {
        if (gPollCount > 120) { driverState(@"error conversation list never appeared"); return; }
        afterSecs(1.0, ^{ driverPoll(); });
        return;
    }
    if ([gOpenTarget isEqualToString:@"list"]) {
        afterSecs(gSettle, ^{ driverState(@"ready 0"); driverPage(0); });
        return;
    }
    if ([gOpenTarget isEqualToString:@"probe"]) {
        afterSecs(gSettle, ^{ probeRun(); });
        return;
    }
    NSString *why = nil;
    id conv = resolveConversation(gOpenTarget, &why);
    if (!conv) { driverState([@"error " stringByAppendingString:why ?: @"unknown"]); return; }
    NSLog(@"[ThreemaSimInject] posting ThreemaShowConversation for target %@", gOpenTarget);
    [NSNotificationCenter.defaultCenter postNotificationName:@"ThreemaShowConversation" object:nil
                                                    userInfo:@{@"conversation": conv, @"forceCompose": @NO}];
    driverWaitChat(0);
}

// ---------- ONLINE PROBE (review appsafety M1) ----------
static BOOL gProbeOnline = NO, gProbeSwizzled = NO;
static volatile long gProbeSend = 0, gProbeReflect = 0, gProbeCompleted = 0, gProbeStateReads = 0;

static void probeSwizzleServerConnector(void) {
    if (gProbeSwizzled) return;
    Class sc = NSClassFromString(@"ServerConnector");
    if (!sc) { NSLog(@"[ThreemaSimInject] PROBE ServerConnector class not loaded yet"); return; }
    Method m = class_getInstanceMethod(sc, @selector(connectionState));
    if (!m) { NSLog(@"[ThreemaSimInject] PROBE connectionState getter missing"); return; }
    method_setImplementation(m, imp_implementationWithBlock(^NSInteger(id self_) {
        __sync_fetch_and_add(&gProbeStateReads, 1);
        return 4;   // ConnectionStateLoggedIn (ConnectionStateDelegate.h: Disconnected=1 ... LoggedIn=4)
    }));
    Method ms = class_getInstanceMethod(sc, NSSelectorFromString(@"sendMessage:"));
    if (ms) method_setImplementation(ms, imp_implementationWithBlock(^BOOL(id self_, id boxed) {
        long n = __sync_add_and_fetch(&gProbeSend, 1);
        NSLog(@"[ThreemaSimInject] PROBE WOULD_SEND chat message #%ld (dropped)", n);
        return YES;
    }));
    Method mr = class_getInstanceMethod(sc, NSSelectorFromString(@"reflectMessage:"));
    if (mr) method_setImplementation(mr, imp_implementationWithBlock(^id(id self_, NSData *msg) {
        long n = __sync_add_and_fetch(&gProbeReflect, 1);
        NSLog(@"[ThreemaSimInject] PROBE WOULD_REFLECT #%ld (dropped)", n);
        return nil;
    }));
    Method mc = class_getInstanceMethod(sc, NSSelectorFromString(@"completedProcessingMessage:"));
    if (mc) method_setImplementation(mc, imp_implementationWithBlock(^BOOL(id self_, id boxed) {
        long n = __sync_add_and_fetch(&gProbeCompleted, 1);
        NSLog(@"[ThreemaSimInject] PROBE WOULD_ACK incoming #%ld (dropped)", n);
        return YES;
    }));
    gProbeSwizzled = YES;
    NSLog(@"[ThreemaSimInject] PROBE online simulation active: connectionState=LoggedIn, send/reflect/ack intercepted");
}

static NSManagedObjectContext *appMainContext(NSString **why) {
    Class biClass = NSClassFromString(@"ThreemaFramework.BusinessInjector");
    SEL uiSel = NSSelectorFromString(@"ui");
    if (!biClass || ![biClass respondsToSelector:uiSel]) { *why = @"BusinessInjector.ui not found"; return nil; }
    id bi = ((id (*)(id, SEL))objc_msgSend)(biClass, uiSel);
    id fetcher = safeKV(safeKV(bi, @"entityManager"), @"entityFetcher");
    SEL frcSel = NSSelectorFromString(@"fetchedResultsControllerForGroupConversationEntities");
    if (!fetcher || ![fetcher respondsToSelector:frcSel]) { *why = @"entityFetcher not available"; return nil; }
    NSFetchedResultsController *frc = ((id (*)(id, SEL))objc_msgSend)(fetcher, frcSel);
    return frc.managedObjectContext;
}

static void probeFinish(NSDictionary *res) {
    NSMutableDictionary *d = [res mutableCopy];
    d[@"would_send"] = @(gProbeSend); d[@"would_reflect"] = @(gProbeReflect); d[@"would_ack"] = @(gProbeCompleted);
    d[@"blocked_http_total"] = @(gBlockedTotal); d[@"blocked_http_blob"] = @(gBlockedBlob);
    d[@"connection_state_reads"] = @(gProbeStateReads);
    NSData *j = [NSJSONSerialization dataWithJSONObject:d options:NSJSONWritingSortedKeys error:nil];
    NSString *js = [[NSString alloc] initWithData:j encoding:NSUTF8StringEncoding];
    NSLog(@"[ThreemaSimInject] PROBE %@", js);
    if (gCtrlDir.length) [js writeToFile:[gCtrlDir stringByAppendingPathComponent:@"probe.json"] atomically:YES
                                encoding:NSUTF8StringEncoding error:nil];
    afterSecs(gSettle, ^{ driverState(@"ready 0"); driverPage(0); });
}

static void probeRun(void) {
    if (!gProbeOnline || !gProbeSwizzled) { driverState(@"error probe needs THREEMA_SIM_PROBE_ONLINE=1"); return; }
    NSString *why = nil;
    NSManagedObjectContext *moc = appMainContext(&why);
    if (!moc) { driverState([@"error " stringByAppendingString:why ?: @"no context"]); return; }
    __block NSArray *oids = nil;
    __block NSUInteger nOwn = 0, nIn = 0;
    [moc performBlockAndWait:^{
        NSFetchRequest *fr = [NSFetchRequest fetchRequestWithEntityName:@"FileMessage"];
        fr.resultType = NSManagedObjectIDResultType;
        oids = [moc executeFetchRequest:fr error:nil];
        NSFetchRequest *fo = [NSFetchRequest fetchRequestWithEntityName:@"FileMessage"];
        fo.predicate = [NSPredicate predicateWithFormat:@"isOwn == YES"];
        nOwn = [moc countForFetchRequest:fo error:nil];
        nIn = oids.count - nOwn;
    }];
    Class wc = NSClassFromString(@"ThreemaFramework.BlobManagerObjCWrapper");
    id wrapper = wc ? [[wc alloc] init] : nil;
    SEL autoSel = NSSelectorFromString(@"autoSyncBlobsFor:");
    SEL syncSel = NSSelectorFromString(@"syncBlobsFor:onCompletion:");
    if (!wrapper || ![wrapper respondsToSelector:autoSel] || ![wrapper respondsToSelector:syncSel]) {
        driverState(@"error BlobManagerObjCWrapper not usable"); return;
    }
    NSLog(@"[ThreemaSimInject] PROBE start: %lu FileMessages (%lu own, %lu incoming)", (unsigned long)oids.count,
          (unsigned long)nOwn, (unsigned long)nIn);
    // phase 1: display path (autoSyncBlobs) for every file message
    for (NSManagedObjectID *oid in oids) ((void (*)(id, SEL, id))objc_msgSend)(wrapper, autoSel, oid);
    NSArray *all = oids;
    afterSecs(20, ^{
        // phase 2: explicit sync (up- AND download path) for every file message
        __block long done = 0; NSMutableDictionary<NSString *, NSNumber *> *res = [NSMutableDictionary dictionary];
        NSArray *names = @[@"uploaded", @"downloaded", @"inProgress", @"failed"];
        for (NSManagedObjectID *oid in all) {
            void (^cb)(NSInteger) = ^(NSInteger r) {
                NSString *k = (r >= 0 && r < 4) ? names[r] : @"other";
                res[k] = @(res[k].longValue + 1); done++;
            };
            ((void (*)(id, SEL, id, id))objc_msgSend)(wrapper, syncSel, oid, cb);
        }
        __block int waited = 0;
        __block void (^poll)(void);
        poll = ^{
            if (done >= (long)all.count || waited > 600) {
                // phase 3: spool the task queue while "logged in" (would send anything queued, e.g. receipts)
                Class tmc = NSClassFromString(@"ThreemaFramework.TaskManager");
                id tm = tmc ? [[tmc alloc] init] : nil;
                if (tm && [tm respondsToSelector:NSSelectorFromString(@"spool")]) {
                    ((void (*)(id, SEL))objc_msgSend)(tm, NSSelectorFromString(@"spool"));
                }
                afterSecs(10, ^{
                    probeFinish(@{@"file_messages": @(all.count), @"own": @(nOwn), @"incoming": @(nIn),
                                  @"sync_completed": @(done), @"sync_results": res, @"task_spool_called": @(tm != nil)});
                });
                poll = nil;
                return;
            }
            waited++;
            afterSecs(0.5, poll);
        };
        afterSecs(0.5, poll);
    });
}

static void openDriverInstall(NSDictionary *env) {
    gOpenTarget = env[@"THREEMA_SIM_OPEN"];
    if (gOpenTarget.length == 0) return;
    gCtrlDir = env[@"THREEMA_SIM_CTRL_DIR"];
    gRunTag = env[@"THREEMA_SIM_RUN_TAG"];
    gScrollUp = [env[@"THREEMA_SIM_SCROLL_UP"] integerValue];
    gPages = [env[@"THREEMA_SIM_PAGES"] integerValue];
    if (env[@"THREEMA_SIM_SETTLE_SECS"]) gSettle = [env[@"THREEMA_SIM_SETTLE_SECS"] doubleValue];
    NSLog(@"[ThreemaSimInject] UI driver armed: open=%@ scrollUp=%ld pages=%ld", gOpenTarget, (long)gScrollUp, (long)gPages);
    [NSNotificationCenter.defaultCenter addObserverForName:UIApplicationDidBecomeActiveNotification object:nil
                                                     queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *n) {
        if (gDriverStarted) return;
        gDriverStarted = YES;
        driverState(@"started");
        afterSecs(2.0, ^{ driverPoll(); });
    }];
}

__attribute__((constructor)) static void ThreemaSimInjectInit(void) {
    @autoreleasepool {
        installNetworkKillSwitch();
        suppressNotificationPrompt();
        NSDictionary *penv = NSProcessInfo.processInfo.environment;
        gProbeOnline = [penv[@"THREEMA_SIM_PROBE_ONLINE"] isEqualToString:@"1"] && gBlockNet;   // never with network
        if (gProbeOnline) {
            probeSwizzleServerConnector();
            if (!gProbeSwizzled) {
                [NSNotificationCenter.defaultCenter addObserverForName:UIApplicationDidFinishLaunchingNotification object:nil
                                                                 queue:nil usingBlock:^(NSNotification *n) {
                    probeSwizzleServerConnector();
                }];
            }
        }
        openDriverInstall(NSProcessInfo.processInfo.environment);
        NSDictionary *env = NSProcessInfo.processInfo.environment;
        NSString *path = env[@"THREEMA_SIM_IDENTITY_JSON"];
        if (path.length == 0) {
            NSLog(@"[ThreemaSimInject] THREEMA_SIM_IDENTITY_JSON not set, doing nothing");
            return;
        }
        NSData *json = [NSData dataWithContentsOfFile:path];
        NSDictionary *cfg = json ? [NSJSONSerialization JSONObjectWithData:json options:0 error:nil] : nil;
        NSString *identity = cfg[@"identity"];
        NSData *sk = hexData(cfg[@"secretKey"] ?: @"");
        NSData *pk = hexData(cfg[@"publicKey"] ?: @"");
        NSString *serverGroup = cfg[@"serverGroup"] ?: @"a";
        if (identity.length != 8 || sk.length != 32 || pk.length != 32) {
            NSLog(@"[ThreemaSimInject] invalid identity JSON at %@", path);
            return;
        }

        // 1. keychain (replace any previous identity item)
        NSDictionary *del = @{(__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
                              (__bridge id)kSecAttrLabel: @"Threema identity 1"};
        SecItemDelete((__bridge CFDictionaryRef)del);
        NSDictionary *add = @{(__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
                              (__bridge id)kSecAttrAccessible: (__bridge id)kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
                              (__bridge id)kSecAttrIsInvisible: @YES,
                              (__bridge id)kSecAttrLabel: @"Threema identity 1",
                              (__bridge id)kSecAttrAccount: identity,
                              (__bridge id)kSecValueData: sk,
                              (__bridge id)kSecAttrGeneric: pk,
                              (__bridge id)kSecAttrService: serverGroup};
        OSStatus st = SecItemAdd((__bridge CFDictionaryRef)add, NULL);
        NSLog(@"[ThreemaSimInject] keychain identity %@ -> OSStatus %d", identity, (int)st);

        if ([env[@"THREEMA_SIM_SKIP_DEFAULTS"] isEqualToString:@"1"]) {
            return;
        }
        // 2. app group defaults
        NSString *group = [NSBundle.mainBundle objectForInfoDictionaryKey:@"ThreemaAppGroupIdentifier"] ?: @"group.ch.threema";
        NSUserDefaults *ud = [[NSUserDefaults alloc] initWithSuiteName:group];
        NSInteger migratedTo = env[@"THREEMA_SIM_MIGRATED_TO"] ? [env[@"THREEMA_SIM_MIGRATED_TO"] integerValue] : 36;
        [ud setInteger:40 forKey:@"AppSetupState"];
        [ud setInteger:migratedTo forKey:@"AppMigratedToVersion"];
        [ud synchronize];
        NSFileManager *fm = NSFileManager.defaultManager;
        // On the simulator the app-group container only comes into existence when the entitled process asks for it,
        // so the DB copy has to happen here (before AppLaunchManager.preLaunchSetup -> registerIfADatabaseFileExists).
        NSURL *container = [fm containerURLForSecurityApplicationGroupIdentifier:group];
        NSLog(@"[ThreemaSimInject] defaults suite=%@ AppSetupState=40 AppMigratedToVersion=%ld container=%@",
              group, (long)migratedTo, container.path);
        NSString *dbDir = env[@"THREEMA_SIM_DB_DIR"];
        if (container && dbDir.length > 0) {
            NSString *dst = container.path;
            if (![fm fileExistsAtPath:[dst stringByAppendingPathComponent:@"ThreemaData.sqlite"]]) {
                for (NSString *name in @[@"ThreemaData.sqlite", @"ThreemaData.sqlite-wal", @"ThreemaData.sqlite-shm",
                                         @".ThreemaData_SUPPORT"]) {
                    NSString *src = [dbDir stringByAppendingPathComponent:name];
                    if (![fm fileExistsAtPath:src]) continue;
                    NSError *err = nil;
                    BOOL ok = [fm copyItemAtPath:src toPath:[dst stringByAppendingPathComponent:name] error:&err];
                    NSLog(@"[ThreemaSimInject] copy %@ -> %@ (%@)", name, ok ? @"ok" : @"FAILED", err.localizedDescription ?: @"");
                }
            } else {
                NSLog(@"[ThreemaSimInject] DB already present in group container, not copying");
            }
            [fm removeItemAtPath:[dst stringByAppendingPathComponent:@"APP_SETUP_NOT_COMPLETED"] error:nil];
        }
    }
}
