import Foundation

/// Gọi 1 endpoint NHẸ (không tốn quota dịch thật) để xác nhận API key còn
/// dùng được — DeepL: `/v2/usage` (đã dùng sẵn ở QuotaTracker/router.py).
/// Gemini: `GET /v1beta/models` (liệt kê model, không tính vào quota
/// generateContent RPD như 1 lần dịch thật sẽ tính).
enum ApiKeyValidator {
    enum Outcome {
        case valid
        case invalid(String)
    }

    static func validateDeepL(_ key: String) async -> Outcome {
        let isFreeKey = key.hasSuffix(":fx")
        let url = URL(string: isFreeKey
            ? "https://api-free.deepl.com/v2/usage"
            : "https://api.deepl.com/v2/usage")!
        var request = URLRequest(url: url)
        request.setValue("DeepL-Auth-Key \(key)", forHTTPHeaderField: "Authorization")
        return await perform(request)
    }

    static func validateGemini(_ key: String) async -> Outcome {
        guard let url = URL(string:
            "https://generativelanguage.googleapis.com/v1beta/models?key=\(key)"
        ) else {
            return .invalid("Key có ký tự không hợp lệ")
        }
        return await perform(URLRequest(url: url))
    }

    private static func perform(_ request: URLRequest) async -> Outcome {
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                return .invalid("Không nhận được phản hồi")
            }
            switch http.statusCode {
            case 200: return .valid
            case 401, 403: return .invalid("Key sai hoặc đã bị thu hồi")
            case 400: return .invalid("Key không hợp lệ")
            default: return .invalid("Lỗi HTTP \(http.statusCode)")
            }
        } catch {
            return .invalid(error.localizedDescription)
        }
    }
}
