import Vision
import AppKit
import Foundation

// CLI OCR dùng Vision framework (gốc macOS, chạy trên Neural Engine).
// Cách dùng: ocr_cli <đường_dẫn_ảnh>
// In ra stdout 1 JSON object: {"lines": [{"text","confidence","x","y","width","height"}, ...]}
// x/y/width/height chuẩn hóa 0-1, GỐC TRÊN-TRÁI (đã tự quy đổi từ gốc
// dưới-trái mặc định của Vision) để phía Python không phải lo quy đổi hệ
// tọa độ khi ghép vào không gian trang PDF.

func emitError(_ message: String) -> Never {
    let obj: [String: Any] = ["error": message]
    if let data = try? JSONSerialization.data(withJSONObject: obj),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    }
    exit(1)
}

guard CommandLine.arguments.count > 1 else {
    emitError("Thiếu đường dẫn ảnh. Cách dùng: ocr_cli <đường_dẫn_ảnh>")
}

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    emitError("Không đọc được ảnh tại \(path)")
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

// Mã ngôn ngữ tiếng Việt do Vision báo hỗ trợ có thể khác nhau giữa các
// phiên bản macOS (đã thấy "vi-VT" thay vì "vi-VN" chuẩn) — dò động thay
// vì hardcode, và luôn kèm thêm tiếng Anh cho tài liệu lai (tiêu đề/thuật
// ngữ tiếng Anh xen trong văn bản tiếng Việt).
if let supported = try? request.supportedRecognitionLanguages() {
    var languages = supported.filter { $0.hasPrefix("vi") }
    if supported.contains("en-US") { languages.append("en-US") }
    if !languages.isEmpty {
        request.recognitionLanguages = languages
    }
}

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    emitError("Vision OCR thất bại: \(error.localizedDescription)")
}

guard let observations = request.results else {
    emitError("Không có kết quả OCR")
}

// Ảnh scan (đặc biệt bảng biểu) không có ranh giới cột THẬT nào để Vision
// biết dừng — nó tự nhận diện "1 dòng" theo heuristic riêng của nó, dựa
// trên khoảng trắng bình thường giữa các từ, và có thể gộp nhầm chữ ở 2 CỘT
// khác nhau (cùng hàng ngang) thành 1 "dòng" liên tục nếu khoảng trống giữa
// 2 cột không đủ để nó coi là ranh giới. Lấy thêm bbox của TỪNG TỪ (qua
// boundingBox(for:)) để tự phát hiện khoảng cách ngang bất thường (lớn hơn
// hẳn khoảng cách 1-từ-cách-1-từ bình thường) và tách dòng tại đó — cùng
// nguyên lý gap-based split đã dùng ở paragraphs.py::split_incoherent_block.
let gapSplitFactor: CGFloat = 1.5  // khoảng cách > 1.5x chiều cao dòng coi là ranh giới cột, không phải khoảng trắng thường

// KHÔNG dùng enumerateSubstrings(options: .byWords) của Foundation: nó
// tách cả ở DẤU GẠCH NỐI ("on-demand" -> "on" + "demand"), và
// candidate.boundingBox(for:) đã kiểm chứng THỰC TẾ trả về bbox SAI/TRÙNG
// LẶP cho các nửa từ ghép kiểu đó (đo được: "on" và "demand" nhận cùng 1
// bbox y hệt nhau) — đúng ngay điểm ranh giới cột quan trọng nhất, làm
// khoảng cách tính ra nhỏ giả tạo, bỏ lỡ đúng chỗ cần tách. Tự tách CHỈ
// theo KÝ TỰ DẤU CÁCH thật, giữ nguyên "on-demand" làm 1 khối — tránh
// hẳn API lỗi ở trên vì mỗi khối giờ chỉ hỏi bbox 1 lần, không tách đôi.
func wordRanges(in text: String) -> [Range<String.Index>] {
    var ranges: [Range<String.Index>] = []
    var idx = text.startIndex
    while idx < text.endIndex {
        while idx < text.endIndex && text[idx] == " " {
            idx = text.index(after: idx)
        }
        guard idx < text.endIndex else { break }
        let start = idx
        while idx < text.endIndex && text[idx] != " " {
            idx = text.index(after: idx)
        }
        ranges.append(start..<idx)
    }
    return ranges
}

var lines: [[String: Any]] = []
for obs in observations {
    guard let candidate = obs.topCandidates(1).first else { continue }
    let text = candidate.string
    var boxedWords: [(range: Range<String.Index>, box: CGRect)] = []
    for range in wordRanges(in: text) {
        if let wordObs = try? candidate.boundingBox(for: range) {
            boxedWords.append((range, wordObs.boundingBox))
        }
    }
    guard let firstWord = boxedWords.first else {
        // Không lấy được bbox từng từ (hiếm) — dùng nguyên bbox cả dòng
        // như trước, còn hơn mất trắng dòng này.
        let box = obs.boundingBox
        lines.append([
            "text": text,
            "confidence": candidate.confidence,
            "x": box.origin.x,
            "y": 1.0 - box.origin.y - box.height,
            "width": box.width,
            "height": box.height,
        ])
        continue
    }

    var groups: [[(range: Range<String.Index>, box: CGRect)]] = [[firstWord]]
    let lineHeight = obs.boundingBox.height
    for word in boxedWords.dropFirst() {
        let prevBox = groups[groups.count - 1].last!.box
        let gap = word.box.origin.x - (prevBox.origin.x + prevBox.width)
        if gap > lineHeight * gapSplitFactor {
            groups.append([word])
        } else {
            groups[groups.count - 1].append(word)
        }
    }

    for group in groups {
        // Cắt nguyên văn từ chuỗi GỐC (không ghép lại từ các từ đã tách) —
        // giữ đúng dấu câu/khoảng trắng gốc bên trong 1 nhóm, chỉ mất phần
        // khoảng trắng NGOÀI nhóm (đúng ý: đó chính là ranh giới bị cắt).
        let groupText = String(text[group.first!.range.lowerBound..<group.last!.range.upperBound])
        let xs0 = group.map { $0.box.origin.x }
        let xs1 = group.map { $0.box.origin.x + $0.box.width }
        let ys0 = group.map { $0.box.origin.y }
        let ys1 = group.map { $0.box.origin.y + $0.box.height }
        let x0 = xs0.min()!, y0 = ys0.min()!, x1 = xs1.max()!, y1 = ys1.max()!
        lines.append([
            "text": groupText,
            "confidence": candidate.confidence,
            "x": x0,
            "y": 1.0 - y0 - (y1 - y0),
            "width": x1 - x0,
            "height": y1 - y0,
        ])
    }
}

let result: [String: Any] = ["lines": lines]
if let data = try? JSONSerialization.data(withJSONObject: result),
   let text = String(data: data, encoding: .utf8) {
    print(text)
} else {
    emitError("Không thể mã hóa kết quả OCR thành JSON")
}
