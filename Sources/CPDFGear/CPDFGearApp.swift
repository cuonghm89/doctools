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
        // App đã đóng gói (scripts/package_app.sh) đã có icon qua
        // Info.plist/CFBundleIconFile + AppIcon.icns — macOS tự hiển thị,
        // không cần set runtime. Chỉ còn thiếu icon khi chạy dev (`swift
        // run`/Xcode, chưa có Info.plist thật).
        //
        // KHÔNG dùng `Bundle.module` ở đây: code SwiftPM tự sinh cho nó tìm
        // bundle resource ngay tại `Bundle.main.bundleURL`, tức NGOÀI
        // `Contents/` trong 1 .app thật — `codesign` từ chối ký hẳn nếu có
        // gì đó nằm ở đó ("unsealed contents present in the bundle root",
        // đã tái hiện + xác nhận bằng crash log thật khi thử). Tự tìm
        // đường dẫn dev qua #filePath thay vì phụ thuộc bundle đó.
        if Bundle.main.object(forInfoDictionaryKey: "CFBundleIconFile") == nil {
            let devIconURL = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent() // Sources/CPDFGear
                .appendingPathComponent("Resources/AppIcon.png")
            if let icon = NSImage(contentsOf: devIconURL) {
                NSApp.applicationIconImage = icon
            }
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
