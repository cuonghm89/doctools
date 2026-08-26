import Foundation

/// Trạng thái 1 file trong hàng đợi xử lý (dịch hoặc xuất Word/PPTX).
/// Dùng chung bởi TranslationRunner và ConversionRunner.
enum QueueItemStatus: Equatable {
    case pending
    case running
    case done(URL)
    case failed(String)
}

struct QueueItem: Identifiable {
    let id = UUID()
    let url: URL
    var status: QueueItemStatus = .pending
}
