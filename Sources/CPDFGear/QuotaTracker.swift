import Foundation

/// Đọc lại file usage_tracker.json mà router.py (PythonEngine) đã ghi, để
/// hiện quota DeepL còn lại trên UI mà không cần đổi gì bên Python.
enum QuotaTracker {
    private static let path = NSString(string:
        "~/Library/Application Support/CPDFGear/usage_tracker.json"
    ).expandingTildeInPath

    static let deeplFreeLimit = 500_000

    /// (đã dùng, giới hạn) — đọc lại số liệu router.py đồng bộ từ chính API
    /// DeepL (`refresh_usage()`, GET /v2/usage) lúc bắt đầu lần dịch gần
    /// nhất, không phải số tự đếm nội bộ — nên luôn khớp dashboard DeepL,
    /// không lệch theo ngày reset. `limit` <= 0 nghĩa là tài khoản Pro
    /// không giới hạn cứng. Trả về nil nếu chưa dịch lần nào.
    static func deeplUsage() -> (used: Int, limit: Int)? {
        guard let data = FileManager.default.contents(atPath: path),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let used = obj["chars_used"] as? Int else { return nil }
        return (used, obj["limit"] as? Int ?? deeplFreeLimit)
    }

    private static let geminiPath = NSString(string:
        "~/Library/Application Support/CPDFGear/gemini_usage_tracker.json"
    ).expandingTildeInPath

    /// Số request Gemini đã gọi trong ngày hôm nay — Gemini tính quota theo
    /// request/ngày (RPD), không phải ký tự/tháng như DeepL, nên đây chỉ là
    /// con số đếm hòm hòm để tham khảo, không phải % quota thật (mỗi model/
    /// tier có hạn mức khác nhau, Google không cho tra qua API key suông).
    static func geminiRequestsToday() -> Int? {
        guard let data = FileManager.default.contents(atPath: geminiPath),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard obj["day"] as? String == formatter.string(from: Date()) else { return 0 }
        return obj["requests_used"] as? Int
    }
}
