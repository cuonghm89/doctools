# C-PDF Gear

**Trước khi đọc bất kỳ file code nào trong dự án này, hãy đọc 2 file sau:**

1. [`SKELETON.md`](SKELETON.md) — cấu trúc thư mục, sơ đồ module, 3 luồng
   chạy chính (dịch PDF / xuất Word-PPTX / OCR).
2. [`CODEMAP.md`](CODEMAP.md) — từng hàm trong từng file, kèm "gọi hàm nào /
   được hàm nào gọi".

Hai file này đủ để trả lời phần lớn câu hỏi về cấu trúc/luồng chạy mà không
cần mở lại toàn bộ source — chỉ đọc file `.swift`/`.py` cụ thể khi cần sửa
đúng chỗ đó hoặc khi 2 file trên không đủ chi tiết.

**Khi thêm/sửa hàm quan trọng, cập nhật `CODEMAP.md` (và `SKELETON.md` nếu
đổi cấu trúc/luồng chạy) trong cùng lần sửa** — đừng để 2 file này lạc hậu so
với code thật.
