import Foundation

/// Chạy 1 script trong PythonEngine dưới dạng subprocess, ghi 1 file JSON
/// cấu hình tạm, và phát lại từng dòng JSON trên stdout dưới dạng sự kiện.
/// Dùng chung cho TranslationRunner (dịch) và ConversionRunner (xuất
/// Word/PPTX) — cả hai chỉ khác nhau ở chỗ script nào chạy và các sự kiện
/// JSON đó có ý nghĩa gì, không phải ở cách quản lý subprocess.
final class PythonProcess {
    /// Ưu tiên `PythonEngine` đóng gói cạnh app đã build (`.app/Contents/Resources`,
    /// xem scripts/package_app.sh); nếu không có (chạy dev qua `swift run`) thì suy
    /// ra project root từ đường dẫn source file lúc build (`#filePath`) — không còn
    /// hardcode theo máy/tài khoản của 1 người.
    static let engineDir: String = {
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("PythonEngine").path,
           FileManager.default.fileExists(atPath: bundled) {
            return bundled
        }
        let projectRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // Sources/CPDFGear
            .deletingLastPathComponent() // Sources
            .deletingLastPathComponent() // project root
        return projectRoot.appendingPathComponent("PythonEngine").path
    }()
    private static let venvPython = engineDir + "/.venv/bin/python3"

    /// Ưu tiên dùng venv riêng của dự án (đã cài pymupdf/requests/...);
    /// nếu không thấy thì dùng "python3" bất kỳ có trong PATH của người dùng.
    static var resolvedPythonPath: String {
        FileManager.default.fileExists(atPath: venvPython) ? venvPython : "python3"
    }

    private var process: Process?

    /// Chạy `scriptName` (trong PythonEngine) với `config` được ghi ra file
    /// JSON tạm rồi truyền qua `--config`. Gọi `onEvent` (luôn trên main
    /// queue) cho mỗi dòng JSON hợp lệ đọc được từ stdout, và `onFinish`
    /// đúng 1 lần khi tiến trình kết thúc, kèm mã thoát và toàn bộ stderr
    /// (để caller tự quyết định có cần hiển thị nó hay không — ví dụ chỉ
    /// hiển thị stderr thô khi chưa có sự kiện "error"/"done" nào từ JSON).
    func run<Config: Encodable>(
        scriptName: String,
        config: Config,
        onEvent: @escaping ([String: Any]) -> Void,
        onFinish: @escaping (_ exitCode: Int32, _ stderrText: String) -> Void
    ) -> String? {
        let scriptPath = Self.engineDir + "/" + scriptName
        guard FileManager.default.fileExists(atPath: scriptPath) else {
            return "Không tìm thấy \(scriptName) tại \(scriptPath)"
        }

        let configURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".json")
        do {
            try JSONEncoder().encode(config).write(to: configURL)
        } catch {
            return "Không thể tạo file cấu hình: \(error.localizedDescription)"
        }

        let pythonPath = Self.resolvedPythonPath
        let process = Process()
        if pythonPath.contains("/") {
            process.executableURL = URL(fileURLWithPath: pythonPath)
            process.arguments = [scriptPath, "--config", configURL.path]
        } else {
            // Phân giải "python3" trơn qua PATH của người dùng, giống shell.
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = [pythonPath, scriptPath, "--config", configURL.path]
        }

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        stdoutPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            for line in text.split(separator: "\n") {
                if let event = Self.parseEvent(String(line)) {
                    DispatchQueue.main.async { onEvent(event) }
                }
            }
        }

        process.terminationHandler = { proc in
            // Gỡ reader bất đồng bộ trước khi đọc nốt phần còn lại theo
            // kiểu đồng bộ, để dòng JSON cuối (thường là "done"/"error")
            // không bị đua với chính handler này.
            stdoutPipe.fileHandleForReading.readabilityHandler = nil
            let remainingOut = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
            let errData = stderrPipe.fileHandleForReading.readDataToEndOfFile()

            DispatchQueue.main.async {
                if let text = String(data: remainingOut, encoding: .utf8) {
                    for line in text.split(separator: "\n") {
                        if let event = Self.parseEvent(String(line)) {
                            onEvent(event)
                        }
                    }
                }
                try? FileManager.default.removeItem(at: configURL)
                let errText = String(data: errData, encoding: .utf8) ?? ""
                onFinish(proc.terminationStatus, errText)
            }
        }

        self.process = process
        do {
            try process.run()
            return nil
        } catch {
            try? FileManager.default.removeItem(at: configURL)
            return "Không thể chạy Python engine: \(error.localizedDescription)"
        }
    }

    func cancel() {
        process?.terminate()
    }

    private static func parseEvent(_ line: String) -> [String: Any]? {
        guard let data = line.data(using: .utf8) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }
}
