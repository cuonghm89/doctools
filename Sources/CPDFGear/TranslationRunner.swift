import Foundation
import Combine

struct TranslationConfig: Encodable {
    let input_pdf: String
    let output_pdf: String
    let deepl_key: String
    let gemini_key: String
    let deepl_limit: Int
    let max_pages: Int
}

/// Điều khiển Python Core Engine (dịch thuật) qua subprocess, xử lý tuần tự
/// 1 hàng đợi file, và phát lại các sự kiện tiến trình JSON để SwiftUI theo
/// dõi. `items[currentIndex]` là file đang xử lý; các item khác giữ trạng
/// thái pending/done/failed để UI vẽ danh sách hàng đợi.
final class TranslationRunner: ObservableObject {
    @Published var items: [QueueItem] = []
    @Published var isRunning = false
    @Published var progress: Double = 0
    @Published var statusText = ""

    /// Gọi mỗi khi 1 file dịch xong (input, output) — ContentView dùng để
    /// ghi vào HistoryStore.
    var onItemDone: ((URL, URL) -> Void)?

    private let runner = PythonProcess()
    private var deeplKey = ""
    private var geminiKey = ""
    private var maxPages = 0
    private var currentIndex = -1

    func start(inputURLs: [URL], deeplKey: String, geminiKey: String, maxPages: Int = 0) {
        guard !inputURLs.isEmpty, !isRunning else { return }
        self.deeplKey = deeplKey
        self.geminiKey = geminiKey
        self.maxPages = maxPages
        items = inputURLs.map { QueueItem(url: $0) }
        currentIndex = -1
        processNext()
    }

    func cancel() {
        runner.cancel()
        isRunning = false
        if items.indices.contains(currentIndex) {
            items[currentIndex].status = .failed("Đã hủy")
        }
    }

    private func processNext() {
        currentIndex += 1
        guard items.indices.contains(currentIndex) else {
            isRunning = false
            statusText = "Hoàn tất \(items.count) file!"
            return
        }
        isRunning = true
        progress = 0
        items[currentIndex].status = .running
        let inputURL = items[currentIndex].url
        let outputPath = inputURL.deletingPathExtension().path + "_VN." + inputURL.pathExtension
        statusText = "Đang dịch \(inputURL.lastPathComponent) (\(currentIndex + 1)/\(items.count))..."

        let config = TranslationConfig(
            input_pdf: inputURL.path,
            output_pdf: outputPath,
            deepl_key: deeplKey,
            gemini_key: geminiKey,
            deepl_limit: 500_000,
            max_pages: maxPages
        )

        let startupError = runner.run(
            scriptName: "translator_engine.py",
            config: config,
            onEvent: { [weak self] event in self?.applyEvent(event) },
            onFinish: { [weak self] exitCode, stderrText in
                guard let self else { return }
                let idx = self.currentIndex
                if self.items.indices.contains(idx) {
                    switch self.items[idx].status {
                    case .done, .failed:
                        break // đã được set bởi sự kiện JSON "done"/"error"
                    default:
                        self.items[idx].status = .failed(
                            stderrText.isEmpty ? "Kết thúc bất thường (mã \(exitCode))." : stderrText
                        )
                    }
                }
                self.processNext()
            }
        )

        if let startupError {
            items[currentIndex].status = .failed(startupError)
            processNext()
        }
    }

    /// Chỉ được gọi trên main queue.
    private func applyEvent(_ obj: [String: Any]) {
        guard let type = obj["type"] as? String, items.indices.contains(currentIndex) else { return }
        switch type {
        case "progress":
            let page = obj["page"] as? Int ?? 0
            let total = obj["total"] as? Int ?? 1
            let engine = obj["engine"] as? String ?? ""
            let detail = obj["detail"] as? String
            progress = total > 0 ? Double(page) / Double(total) : 0
            var engineLabel = engine == "deepl" ? "DeepL router"
                : engine == "gemini" ? "Gemini fallback"
                : engine == "skipped" ? "bỏ qua 1 đoạn, giữ nguyên bản gốc"
                : engine
            if engine == "gemini", let detail {
                // DeepL đã thử và fail trước khi rơi xuống Gemini — hiện lý
                // do thật thay vì để người dùng phải đoán.
                engineLabel += ", DeepL lỗi: \(detail)"
            }
            let name = items[currentIndex].url.lastPathComponent
            statusText = "[\(currentIndex + 1)/\(items.count)] \(name) — trang \(page)/\(total)... (\(engineLabel))"
        case "done":
            if let path = obj["output"] as? String {
                let outputURL = URL(fileURLWithPath: path)
                items[currentIndex].status = .done(outputURL)
                onItemDone?(items[currentIndex].url, outputURL)
            }
        case "error":
            items[currentIndex].status = .failed(obj["message"] as? String ?? "Lỗi không xác định")
        default:
            break
        }
    }
}
