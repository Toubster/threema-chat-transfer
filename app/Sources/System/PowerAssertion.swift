// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation
import IOKit.pwr_mgt

/// Keeps the Mac awake while an engine command runs from S12 on and while `critical` is on (DESIGN §8.1).
public protocol PowerAsserting: AnyObject {
    var isHeld: Bool { get }
    func acquire()
    func release()
}

/// Reference-counted `IOPMAssertion` (prevent idle system sleep).
public final class PowerAssertion: PowerAsserting {
    private var id: IOPMAssertionID = 0
    private var count = 0
    private let reason: String

    public init(reason: String = "\(AppInfo.productName): transfer in progress") { self.reason = reason }

    public var isHeld: Bool { count > 0 }

    public func acquire() {
        count += 1
        guard count == 1 else { return }
        let r = IOPMAssertionCreateWithName(kIOPMAssertionTypePreventUserIdleSystemSleep as CFString,
                                            IOPMAssertionLevel(kIOPMAssertionLevelOn), reason as CFString, &id)
        if r != kIOReturnSuccess { id = 0 }
    }

    public func release() {
        guard count > 0 else { return }
        count -= 1
        guard count == 0, id != 0 else { return }
        IOPMAssertionRelease(id)
        id = 0
    }

    deinit { if id != 0 { IOPMAssertionRelease(id) } }
}

/// Counts acquire/release for tests.
public final class FakePowerAssertion: PowerAsserting {
    public private(set) var count = 0
    public private(set) var maxCount = 0
    public init() {}
    public var isHeld: Bool { count > 0 }
    public func acquire() { count += 1; maxCount = max(maxCount, count) }
    public func release() { count = max(0, count - 1) }
}
