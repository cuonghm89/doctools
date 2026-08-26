import Foundation

struct HistoryEntry: Codable, Identifiable {
    let id: UUID
    let kind: String
    let inputPath: String
    let outputPath: String
    let date: Date
}

/// Lịch sử file đã dịch/xuất, lưu ở UserDefaults (JSON) — chỉ đường dẫn +
/// mốc thời gian, không có dữ liệu nhạy cảm nào nên không cần Keychain.
final class HistoryStore: ObservableObject {
    @Published private(set) var entries: [HistoryEntry] = []

    private let defaultsKey = "translationHistory"
    private let maxEntries = 30

    init() { load() }

    func add(kind: String, input: URL, output: URL) {
        entries.insert(HistoryEntry(id: UUID(), kind: kind, inputPath: input.path,
                                     outputPath: output.path, date: Date()), at: 0)
        if entries.count > maxEntries {
            entries.removeLast(entries.count - maxEntries)
        }
        save()
    }

    func clear() {
        entries = []
        save()
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let decoded = try? JSONDecoder().decode([HistoryEntry].self, from: data) else { return }
        entries = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(entries) else { return }
        UserDefaults.standard.set(data, forKey: defaultsKey)
    }
}
