import Foundation

/// Kiểm tra bản mới trên GitHub Releases khi app khởi động — không tự tải
/// hay cài đè gì cả (đó là việc của Sparkle/tương tự, quá tay cho 1 tool
/// nội bộ), chỉ báo cho người dùng biết để họ tự tải zip mới nếu muốn.
@MainActor
final class UpdateChecker: ObservableObject {
    @Published var latestVersion: String?
    @Published var releaseURL: URL?

    private static let releasesAPI =
        URL(string: "https://api.github.com/repos/cuonghm89/doctools/releases/latest")!

    func check() {
        // Chỉ có ý nghĩa với app đã đóng gói (Info.plist thật, xem
        // scripts/package_app.sh) — chạy dev qua `swift run` không có
        // CFBundleShortVersionString nên bỏ qua, không có gì để so sánh.
        guard let currentVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String else {
            return
        }
        Task {
            guard let (data, response) = try? await URLSession.shared.data(from: Self.releasesAPI),
                  (response as? HTTPURLResponse)?.statusCode == 200,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag = json["tag_name"] as? String else {
                return
            }
            let latest = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
            guard Self.isNewer(latest, than: currentVersion) else { return }
            latestVersion = latest
            if let urlString = json["html_url"] as? String {
                releaseURL = URL(string: urlString)
            }
        }
    }

    /// So sánh 2 chuỗi version dạng "x.y.z" theo từng phần số — thiếu phần
    /// nào coi như 0 (vd "0.2" so với "0.2.0" bằng nhau).
    private static func isNewer(_ a: String, than b: String) -> Bool {
        let partsA = a.split(separator: ".").compactMap { Int($0) }
        let partsB = b.split(separator: ".").compactMap { Int($0) }
        for i in 0..<max(partsA.count, partsB.count) {
            let x = i < partsA.count ? partsA[i] : 0
            let y = i < partsB.count ? partsB[i] : 0
            if x != y { return x > y }
        }
        return false
    }
}
