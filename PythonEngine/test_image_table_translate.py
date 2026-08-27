"""Self-check các hàm canh-giữa-theo-chiều-dọc trong image_table_translate.py
— chạy trực tiếp: python3 test_image_table_translate.py"""
import fitz
from image_table_translate import _drop_nested_regions, _estimate_line_count, _vcentered_rect


# --- _drop_nested_regions: case THẬT gặp trên bảng STRIDE của user —
# page.get_images() trả về 2 xref cho ĐÚNG 1 bảng nhìn thấy (ảnh khung viền
# lớn hơn + ảnh nội dung nằm lồng bên trong nó vài pt mỗi cạnh). Không lọc
# thì cả 2 bị OCR/dịch/vẽ chồng nhau 2 lần, để sót viền chữ cũ ở mép. ---
outer = fitz.Rect(108.12, 356.52, 504.12, 535.20)
inner = fitz.Rect(113.25, 361.65, 495.15, 526.20)  # nằm gọn trong `outer`
unrelated_icon = fitz.Rect(50, 50, 70, 70)  # ảnh khác hẳn, không lồng gì cả
kept = _drop_nested_regions([outer, inner, unrelated_icon])
assert set(kept) == {outer, unrelated_icon}, f"phải bỏ `inner` (lồng trong outer), giữ outer + icon, got {kept}"

# 2 rect không lồng nhau (chỉ chồng lấn 1 phần) -> giữ cả 2, không phải
# quan hệ chứa trọn.
partial_a = fitz.Rect(0, 0, 100, 100)
partial_b = fitz.Rect(50, 50, 150, 150)
assert set(_drop_nested_regions([partial_a, partial_b])) == {partial_a, partial_b}

print("Tất cả self-check lọc-ảnh-lồng-nhau đều pass.")


# --- _estimate_line_count: chữ ngắn vừa 1 dòng, chữ dài phải word-wrap ---
assert _estimate_line_count("OK", 10, 200) == 1
short_lines = _estimate_line_count("Một hàng ngắn", 10, 200)
long_lines = _estimate_line_count("Một đoạn văn rất dài cần phải xuống dòng nhiều lần", 10, 60)
assert long_lines > short_lines, f"đoạn dài hơn khung hẹp phải ra nhiều dòng hơn, got {long_lines} vs {short_lines}"

# Khung rộng = 0 -> không chia 0, trả về ít nhất 1 dòng.
assert _estimate_line_count("bất kỳ", 10, 0) == 1

print("Tất cả self-check ước-lượng-số-dòng đều pass.")


# --- _vcentered_rect: chữ ngắn trong ô cao -> đẩy xuống giữa, không tràn ---
tall_rect = fitz.Rect(0, 0, 100, 100)
centered = _vcentered_rect(tall_rect, "OK", 10)
assert centered.y0 > tall_rect.y0, "chữ ngắn trong ô cao phải bắt đầu thấp hơn mép trên"
assert centered.y1 == tall_rect.y1, "đáy khung không đổi (fit_and_draw tự giãn xuống nếu cần)"
assert centered.x0 == tall_rect.x0 and centered.x1 == tall_rect.x1, "không đổi canh ngang"

# Chữ dài gần lấp đầy ô -> gần như không đẩy xuống (hoặc giữ nguyên khung gốc).
long_text = "Một đoạn văn rất dài " * 10
full_rect = fitz.Rect(0, 0, 100, 30)
almost_full = _vcentered_rect(full_rect, long_text, 10)
assert almost_full.y0 - full_rect.y0 < full_rect.height, "không được đẩy quá đà ra ngoài chiều cao ô"

print("Tất cả self-check canh-giữa-theo-chiều-dọc đều pass.")
