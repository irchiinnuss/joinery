// JoineryNotifier: shows one desktop notification as Joinery, with Joinery's icon.
//
//     JoineryNotifier -- TITLE BODY
//
// The title and body arrive as arguments and are only ever handed to the notification
// APIs as strings: nothing here builds or runs a script. The first run asks macOS for
// permission; macOS answers "not allowed" at once while its prompt is still up, so that
// first message goes out through osascript and later ones through here. Exit status: 0 shown, 1 wrong arguments, 2 not allowed or not delivered
// (the editor then falls back to osascript). Started without arguments (macOS does
// that when the user clicks a notification) it does nothing and exits.

import AppKit
import UserNotifications

/// How long the user may take to answer the first-run permission prompt.
let permissionWait: TimeInterval = 60
/// How long to stay after posting so the banner is handed over before exit.
let settleWait: TimeInterval = 1.0

enum Exit: Int32 {
    case shown = 0
    case usage = 1
    case refused = 2
}

/// One line on stderr for whoever runs it by hand; the editor ignores it.
func say(_ line: String) {
    FileHandle.standardError.write(Data("JoineryNotifier: \(line)\n".utf8))
}

func message(_ arguments: [String]) -> (title: String, body: String)? {
    var rest = Array(arguments.dropFirst())
    if rest.first == "--" {
        rest.removeFirst()
    }
    guard rest.count == 2 else { return nil }
    return (rest[0], rest[1])
}

final class Notifier: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    let title: String
    let body: String

    init(title: String, body: String) {
        self.title = title
        self.body = body
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            DispatchQueue.main.async {
                if granted {
                    self.post(center)
                } else {
                    say("not allowed: \(error?.localizedDescription ?? "the user said no")")
                    self.legacy()
                }
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + permissionWait) {
            say("no answer to the permission prompt in time")
            self.finish(.refused)
        }
    }

    func post(_ center: UNUserNotificationCenter) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(
            identifier: UUID().uuidString, content: content, trigger: nil)
        center.add(request) { error in
            DispatchQueue.main.async {
                if let error {
                    say("not posted: \(error.localizedDescription)")
                    self.legacy()
                } else {
                    self.settle(.shown)
                }
            }
        }
    }

    /// The older API, for when the user-notification framework says no.
    @available(macOS, deprecated: 11.0)
    func legacy() {
        let center = NSUserNotificationCenter.default
        let note = NSUserNotification()
        note.title = title
        note.informativeText = body
        center.deliver(note)
        // Handed over is not shown: only a presented one counts, or osascript takes over.
        DispatchQueue.main.asyncAfter(deadline: .now() + settleWait) {
            if !note.isPresented {
                say("the older API did not show it either")
            }
            self.finish(note.isPresented ? .shown : .refused)
        }
    }

    func settle(_ code: Exit) {
        DispatchQueue.main.asyncAfter(deadline: .now() + settleWait) { self.finish(code) }
    }

    func finish(_ code: Exit) {
        exit(code.rawValue)
    }

    // Shown as a banner even if macOS counts this helper as the active app.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter, willPresent notification: UNNotification,
        withCompletionHandler completionHandler:
            @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list])
    }
}

guard let text = message(CommandLine.arguments) else {
    exit(CommandLine.arguments.count <= 1 ? Exit.shown.rawValue : Exit.usage.rawValue)
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let notifier = Notifier(title: text.title, body: text.body)
app.delegate = notifier
app.run()
