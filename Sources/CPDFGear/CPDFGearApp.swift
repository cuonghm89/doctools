import SwiftUI
import AppKit

// 1 executable chạy trực tiếp bằng `swift run` (không có .app bundle /
// Info.plist) không phải lúc nào cũng được kích hoạt thành ứng dụng ở
// foreground, nên cửa sổ của nó có thể mở phía sau mọi thứ khác mà không
// có icon Dock để bấm vào. Ép nó lên phía trước.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        if let iconURL = Bundle.module.url(forResource: "AppIcon", withExtension: "png"),
           let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }
    }
}

@main
struct CPDFGearApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .windowResizability(.contentSize)
    }
}
