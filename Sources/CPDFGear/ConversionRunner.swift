import Foundation

struct ConversionConfig: Encodable {
    let input_pdf: String
    let output_docx: String?
    let output_pptx: String?
}

/// Điều khiển convert_cli.py (chuyển PDF -> Word/PPTX) qua subprocess, xử lý
/// tuần tự 1 hàng đợi file. Độc lập hoàn toàn với việc dịch thuật — dùng
/// được cho bất kỳ PDF nào, không cần API key.
final class ConversionRunner: ObservableObject {
    @Published var items: [QueueItem] = []
    @Published var isRunning = false
    @Published var statusText = ""

    /// Gọi mỗi khi 1 file xuất xong (input, [output docx/pptx]).
    var onItemDone: ((URL, [URL]) -> Void)?

    private let runner = PythonProcess()
    private var toDocx = false
    private var toPptx = false
    private var currentIndex = -1

    func start(inputURLs: [URL], toDocx: Bool, toPptx: Bool) {
        guard toDocx || toPptx, !inputURLs.isEmpty, !isRunning else { return }
        self.toDocx = toDocx
        self.toPptx = toPptx
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
        items[currentIndex].status = .running
        let inputURL = items[currentIndex].url
        let base = inputURL.deletingPathExtension().path
        statusText = "Đang chuyển đổi \(inputURL.lastPathComponent) (\(currentIndex + 1)/\(items.count))..."

        let config = ConversionConfig(
            input_pdf: inputURL.path,
            output_docx: toDocx ? base + ".docx" : nil,
            output_pptx: toPptx ? base + ".pptx" : nil
        )

        var outputs: [URL] = []
        let startupError = runner.run(
            scriptName: "convert_cli.py",
            config: config,
            onEvent: { [weak self] event in
                guard let self, let type = event["type"] as? String else { return }
                switch type {
                case "progress":
                    let stage = event["stage"] as? String ?? ""
                    let status = event["status"] as? String ?? ""
                    let label = stage == "docx" ? "Word (.docx)" : stage == "pptx" ? "PowerPoint (.pptx)" : stage
                    let name = self.items[self.currentIndex].url.lastPathComponent
                    self.statusText = "[\(self.currentIndex + 1)/\(self.items.count)] \(name) — "
                        + (status == "start" ? "đang xuất ra \(label)..." : "đã xong \(label)")
                case "done":
                    if let path = event["output_docx"] as? String { outputs.append(URL(fileURLWithPath: path)) }
                    if let path = event["output_pptx"] as? String { outputs.append(URL(fileURLWithPath: path)) }
                case "error":
                    self.items[self.currentIndex].status = .failed(event["message"] as? String ?? "Lỗi không xác định")
                default:
                    break
                }
            },
            onFinish: { [weak self] exitCode, stderrText in
                guard let self else { return }
                let idx = self.currentIndex
                if case .failed = self.items[idx].status {
                    // giữ nguyên lỗi đã set từ sự kiện "error"
                } else if !outputs.isEmpty {
                    self.items[idx].status = .done(outputs[0])
                    self.onItemDone?(inputURL, outputs)
                } else {
                    self.items[idx].status = .failed(
                        stderrText.isEmpty ? "Kết thúc bất thường (mã \(exitCode))." : stderrText
                    )
                }
                self.processNext()
            }
        )

        if let startupError {
            items[currentIndex].status = .failed(startupError)
            processNext()
        }
    }
}
