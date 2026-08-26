import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct ContentView: View {
    @AppStorage("deeplApiKey") private var deeplApiKey: String = ""
    @AppStorage("geminiApiKey") private var geminiApiKey: String = ""
    @AppStorage("maxPages") private var maxPages: Int = 0

    @State private var queue: [URL] = []
    @State private var isSettingsExpanded = true
    @State private var isHistoryExpanded = false
    @State private var isDropTargeted = false

    @StateObject private var runner = TranslationRunner()
    @StateObject private var converter = ConversionRunner()
    @StateObject private var history = HistoryStore()

    private var canStart: Bool {
        !queue.isEmpty && !deeplApiKey.isEmpty && !runner.isRunning && !converter.isRunning
    }

    private var canConvert: Bool {
        !pdfsInQueue.isEmpty && !runner.isRunning && !converter.isRunning
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                header
                dropZone
                queueList
                settingsCard
                statusArea
                startButton
                convertButtons
                conversionStatusArea
                historyCard
            }
            .padding(28)
        }
        .frame(width: 480, height: 700)
        .background(.background)
        .onAppear {
            runner.onItemDone = { input, output in
                history.add(kind: "Dịch", input: input, output: output)
            }
            converter.onItemDone = { input, outputs in
                for output in outputs {
                    let kind = output.pathExtension.lowercased() == "docx" ? "Word" : "PowerPoint"
                    history.add(kind: kind, input: input, output: output)
                }
            }
        }
    }

    // MARK: - Phần đầu (header)

    private var header: some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(LinearGradient(colors: [.accentColor, .accentColor.opacity(0.6)],
                                          startPoint: .topLeading, endPoint: .bottomTrailing))
                    .frame(width: 44, height: 44)
                Image(systemName: "character.book.closed.fill")
                    .font(.system(size: 20))
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("C-PDF Gear")
                    .font(.title3).bold()
                Text("Dịch PDF sang tiếng Việt, giữ nguyên bố cục")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
    }

    // MARK: - Khu vực kéo thả file (hỗ trợ nhiều file cùng lúc)

    private var dropZone: some View {
        RoundedRectangle(cornerRadius: 14)
            .strokeBorder(style: StrokeStyle(lineWidth: 2, dash: [7]))
            .foregroundStyle(isDropTargeted ? Color.accentColor : Color.secondary.opacity(0.4))
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(isDropTargeted ? Color.accentColor.opacity(0.08) : Color.secondary.opacity(0.06))
            )
            .frame(height: 110)
            .overlay {
                VStack(spacing: 8) {
                    Image(systemName: "arrow.down.doc.fill")
                        .font(.system(size: 26))
                        .foregroundStyle(Color.secondary)
                    Text(queue.isEmpty ? "Kéo thả PDF/Word/PowerPoint vào đây (có thể chọn nhiều file)" : "Thả thêm file để thêm vào hàng đợi")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                    Button("Chọn file...") { pickFiles() }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
            }
            .animation(.easeOut(duration: 0.15), value: isDropTargeted)
            .onDrop(of: [.fileURL], isTargeted: $isDropTargeted) { providers in
                for provider in providers {
                    _ = provider.loadObject(ofClass: URL.self) { url, _ in
                        guard let url, Self.supportedExtensions.contains(url.pathExtension.lowercased()) else { return }
                        DispatchQueue.main.async { addToQueue(url) }
                    }
                }
                return true
            }
    }

    /// Dịch hỗ trợ cả PDF/Word/PowerPoint (xem office_translate.py); xuất
    /// Word/PowerPoint (convertButtons bên dưới) chỉ nhận PDF làm input.
    private static let supportedExtensions: Set<String> = ["pdf", "docx", "pptx"]

    private func pickFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.pdf]
            + (UTType(filenameExtension: "docx").map { [$0] } ?? [])
            + (UTType(filenameExtension: "pptx").map { [$0] } ?? [])
        if panel.runModal() == .OK {
            panel.urls.forEach(addToQueue)
        }
    }

    private func addToQueue(_ url: URL) {
        guard !queue.contains(url) else { return }
        // Chọn/thả file mới khi đang đứng yên (không chạy dở) nghĩa là bắt
        // đầu 1 đợt mới — xóa kết quả (đã .done/.failed) của đợt TRƯỚC để
        // quay lại danh sách hàng đợi chỉnh sửa được (queueList ưu tiên
        // hiện runner.items/converter.items nếu còn, nên nếu không xóa,
        // file mới vẫn được thêm vào `queue` nhưng KHÔNG hiện ra ở đâu cả
        // — trông y hệt như chọn file không có tác dụng). Không đụng gì
        // khi đang chạy dở, để không phá màn hình tiến trình đang hiện.
        if !runner.isRunning && !converter.isRunning {
            runner.items = []
            converter.items = []
        }
        queue.append(url)
    }

    // MARK: - Hàng đợi file (queue)

    /// Khi runner/converter đang chạy hoặc vừa chạy xong, hiện danh sách có
    /// trạng thái của chính nó (pending/running/done/failed); lúc rảnh thì
    /// hiện danh sách `queue` thô, cho phép xóa từng file.
    @ViewBuilder
    private var queueList: some View {
        if runner.isRunning || (!runner.items.isEmpty && !converter.isRunning && converter.items.isEmpty) {
            queueItemsView(runner.items, showCompare: true)
        } else if converter.isRunning || !converter.items.isEmpty {
            queueItemsView(converter.items, showCompare: false, showReveal: true)
        } else if !queue.isEmpty {
            VStack(spacing: 6) {
                ForEach(queue, id: \.self) { url in
                    HStack {
                        Image(systemName: "doc.fill").foregroundStyle(.secondary)
                        Text(url.lastPathComponent).font(.callout).lineLimit(1)
                        Spacer()
                        Button {
                            queue.removeAll { $0 == url }
                        } label: {
                            Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                }
                HStack {
                    Spacer()
                    Button("Xóa tất cả") { queue.removeAll() }
                        .buttonStyle(.plain)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color.secondary.opacity(0.06)))
        }
    }

    private func queueItemsView(_ items: [QueueItem], showCompare: Bool, showReveal: Bool = false) -> some View {
        VStack(spacing: 6) {
            ForEach(items, id: \.id) { item in
                queueRow(item, showCompare: showCompare, showReveal: showReveal)
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.secondary.opacity(0.06)))
    }

    @ViewBuilder
    private func queueRow(_ item: QueueItem, showCompare: Bool, showReveal: Bool = false) -> some View {
        HStack(spacing: 8) {
            statusIcon(item.status)
            Text(item.url.lastPathComponent).font(.callout).lineLimit(1)
            Spacer()
            if showCompare, case .done(let outputURL) = item.status {
                Button("So sánh") {
                    NSWorkspace.shared.open(item.url)
                    NSWorkspace.shared.open(outputURL)
                }
                .buttonStyle(.plain)
                .font(.caption)
                .foregroundStyle(Color.accentColor)
            }
            if showReveal, case .done(let outputURL) = item.status {
                Button("Hiện trong Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([outputURL])
                }
                .buttonStyle(.plain)
                .font(.caption)
                .foregroundStyle(Color.accentColor)
            }
            if case .failed(let message) = item.status {
                Text(message).font(.caption2).foregroundStyle(.red).lineLimit(1)
            }
        }
    }

    @ViewBuilder
    private func statusIcon(_ status: QueueItemStatus) -> some View {
        switch status {
        case .pending:
            Image(systemName: "circle.dashed").foregroundStyle(.secondary)
        case .running:
            ProgressView().controlSize(.small)
        case .done:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed:
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.red)
        }
    }

    // MARK: - Cài đặt

    private var settingsCard: some View {
        DisclosureGroup(isExpanded: $isSettingsExpanded) {
            VStack(spacing: 12) {
                settingsField(icon: "key.fill", placeholder: "DeepL API Key", text: $deeplApiKey, secure: true)
                settingsField(icon: "sparkles", placeholder: "Gemini API Key (fallback)", text: $geminiApiKey, secure: true)

                HStack {
                    Image(systemName: "doc.on.doc")
                        .foregroundStyle(.secondary)
                        .frame(width: 16)
                    Text("Giới hạn số trang")
                        .font(.callout)
                    Spacer()
                    Stepper(value: $maxPages, in: 0...200) {
                        Text(maxPages == 0 ? "Tất cả" : "\(maxPages) trang")
                            .font(.callout.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    .fixedSize()
                }

                if let usage = QuotaTracker.deeplUsage() {
                    HStack {
                        Image(systemName: "gauge.with.dots.needle.50percent")
                            .foregroundStyle(.secondary)
                            .frame(width: 16)
                        Text("Quota DeepL")
                            .font(.callout)
                        Spacer()
                        Text(usage.limit > 0
                             ? "\(usage.used.formatted()) / \(usage.limit.formatted())"
                             : "\(usage.used.formatted()) (không giới hạn)")
                            .font(.callout.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }

                if !geminiApiKey.isEmpty {
                    // Khác DeepL (chạy mỗi lần dịch), Gemini chỉ là fallback
                    // nên file tracker có thể chưa từng được tạo dù đã điền
                    // key — ẩn hẳn dòng này theo kiểu "chỉ hiện khi có dữ
                    // liệu" (như DeepL) sẽ khiến nó gần như không bao giờ
                    // hiện. Hiện ngay khi có key, mặc định 0 request.
                    HStack {
                        Image(systemName: "sparkles")
                            .foregroundStyle(.secondary)
                            .frame(width: 16)
                        Text("Gemini request hôm nay")
                            .font(.callout)
                        Spacer()
                        Text("~\((QuotaTracker.geminiRequestsToday() ?? 0).formatted())")
                            .font(.callout.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.top, 12)
        } label: {
            Label("Cài đặt", systemImage: "gearshape.fill")
                .font(.callout.weight(.medium))
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.secondary.opacity(0.06)))
    }

    private func settingsField(icon: String, placeholder: String, text: Binding<String>, secure: Bool) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .foregroundStyle(.secondary)
                .frame(width: 16)
            if secure {
                SecureField(placeholder, text: text)
            } else {
                TextField(placeholder, text: text)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.08)))
        .textFieldStyle(.plain)
    }

    // MARK: - Trạng thái

    @ViewBuilder
    private var statusArea: some View {
        if runner.isRunning {
            VStack(spacing: 8) {
                ProgressView(value: runner.progress)
                Text(runner.statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color.secondary.opacity(0.06)))
        } else if !runner.items.isEmpty {
            HStack(spacing: 10) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text(runner.statusText)
                    .font(.callout.weight(.medium))
                Spacer()
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color.green.opacity(0.1)))
        }
    }

    // MARK: - Nút Start Translation

    private var startButton: some View {
        Button {
            runner.start(inputURLs: queue, deeplKey: deeplApiKey, geminiKey: geminiApiKey, maxPages: maxPages)
        } label: {
            HStack {
                if runner.isRunning {
                    ProgressView().controlSize(.small).tint(.white)
                } else {
                    Image(systemName: "arrow.right.circle.fill")
                }
                Text(runner.isRunning ? "Đang dịch..." : "Start Translation (\(queue.count) file)")
                    .fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .disabled(!canStart)
    }

    // MARK: - Chuyển đổi sang Word/PowerPoint

    /// Chuyển đổi định dạng, độc lập với việc dịch — dùng được cho PDF gốc
    /// lẫn PDF đã dịch (_VN.pdf), không cần API key. Chỉ nhận input PDF
    /// (pdf_convert.py mở file bằng PyMuPDF) — lọc bỏ .docx/.pptx nếu có
    /// trong hàng đợi (đã thêm cho tính năng dịch trực tiếp office).
    private var pdfsInQueue: [URL] { queue.filter { $0.pathExtension.lowercased() == "pdf" } }

    private var convertButtons: some View {
        HStack(spacing: 10) {
            Button {
                converter.start(inputURLs: pdfsInQueue, toDocx: true, toPptx: false)
            } label: {
                Label("Xuất Word", systemImage: "doc.richtext")
                    .frame(maxWidth: .infinity)
            }
            Button {
                converter.start(inputURLs: pdfsInQueue, toDocx: false, toPptx: true)
            } label: {
                Label("Xuất PowerPoint", systemImage: "rectangle.on.rectangle")
                    .frame(maxWidth: .infinity)
            }
        }
        .buttonStyle(.bordered)
        .disabled(!canConvert)
    }

    @ViewBuilder
    private var conversionStatusArea: some View {
        if converter.isRunning {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(converter.statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        } else if !converter.items.isEmpty {
            HStack(spacing: 10) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text(converter.statusText)
                    .font(.callout.weight(.medium))
                Spacer()
            }
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 12).fill(Color.green.opacity(0.1)))
        }
    }

    // MARK: - Lịch sử

    private var historyCard: some View {
        DisclosureGroup(isExpanded: $isHistoryExpanded) {
            VStack(spacing: 6) {
                if history.entries.isEmpty {
                    Text("Chưa có file nào.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.top, 8)
                } else {
                    ForEach(history.entries) { entry in
                        HStack(spacing: 8) {
                            Text(entry.kind)
                                .font(.caption2.weight(.semibold))
                                .padding(.horizontal, 6).padding(.vertical, 2)
                                .background(Capsule().fill(Color.accentColor.opacity(0.15)))
                            Text(URL(fileURLWithPath: entry.outputPath).lastPathComponent)
                                .font(.callout)
                                .lineLimit(1)
                            Spacer()
                            Text(entry.date, style: .time)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Button {
                                NSWorkspace.shared.activateFileViewerSelecting(
                                    [URL(fileURLWithPath: entry.outputPath)])
                            } label: {
                                Image(systemName: "folder")
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    HStack {
                        Spacer()
                        Button("Xóa lịch sử") { history.clear() }
                            .buttonStyle(.plain)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.top, 12)
        } label: {
            Label("Lịch sử", systemImage: "clock.arrow.circlepath")
                .font(.callout.weight(.medium))
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.secondary.opacity(0.06)))
    }
}
