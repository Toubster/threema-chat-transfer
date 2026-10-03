// SPDX-License-Identifier: AGPL-3.0-or-later
import AppKit

/// Quit and close rules of DESIGN §8.1: ⌘Q, the window's close button and ⌘W ask from S11 on and are refused
/// while `critical` is on (the restore is being sent). The decision itself lives in `WizardStore.quitDecision`.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    static weak var store: WizardStore?
    /// Set once the user confirmed "Beenden" in the dialog; the next terminate request passes.
    private static var quitApproved = false
    private weak var observedWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        AppHooks.terminate = {
            AppDelegate.quitApproved = true
            NSApp.terminate(nil)
        }
        // The wizard window is created by SwiftUI; intercept its close button with the same rules as ⌘Q.
        DispatchQueue.main.async { [weak self] in self?.attachToMainWindow() }
        NotificationCenter.default.addObserver(forName: NSWindow.didBecomeKeyNotification, object: nil,
                                               queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.attachToMainWindow() }
        }
    }

    private func attachToMainWindow() {
        guard let w = NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }), w !== observedWindow else {
            return
        }
        observedWindow = w
        w.tabbingMode = .disallowed
        if let close = w.standardWindowButton(.closeButton) {
            close.target = self
            close.action = #selector(closeButtonClicked(_:))
        }
    }

    @objc private func closeButtonClicked(_ sender: Any?) { AppDelegate.closeRequested() }

    /// Window close = quit (one-window app).
    static func closeRequested() {
        NSApp.terminate(nil)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let store = AppDelegate.store else { return .terminateNow }
        if store.critical {
            AppDelegate.quitApproved = false
            _ = store.requestQuit()
            return .terminateCancel
        }
        if AppDelegate.quitApproved { return .terminateNow }
        return store.requestQuit() ? .terminateNow : .terminateCancel
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        AppDelegate.store?.prepareForTermination()
    }
}
