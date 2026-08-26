"""Tự kiểm tra OCR cho trang PDF dạng scan. Chạy trực tiếp:
    python3 test_ocr_pdf.py
Dùng binary ocr_cli thật (Vision framework, chạy local, không cần mạng) —
cần `swift build` đã chạy ít nhất 1 lần trong thư mục dự án.
"""
import os

import fitz

from ocr_pdf import DEFAULT_OCR_BINARY, _page_has_real_text, ensure_text_layer, _detect_vertical_grid_lines

assert os.path.exists(DEFAULT_OCR_BINARY), (
    f"Chưa build ocr_cli tại {DEFAULT_OCR_BINARY} — chạy `swift build` trong thư mục dự án trước."
)

# Trang có chữ thật (PDF số hóa bình thường) -> không OCR, không đụng gì.
doc1 = fitz.open()
page1 = doc1.new_page()
page1.insert_text((72, 100), "This page already has real text, twenty chars.", fontsize=12)
assert _page_has_real_text(page1) is True
ocred = ensure_text_layer(doc1)
assert ocred == {}, f"trang đã có chữ thật không được OCR lại, ocred={ocred}"

# Trang trắng hoàn toàn (không ảnh, không chữ) -> _page_has_real_text False,
# nhưng OCR trên trang trắng sẽ không tìm thấy dòng nào (không lỗi, không
# chèn gì) — vẫn phải chạy được mà không crash.
doc2 = fitz.open()
doc2.new_page()
ocred2 = ensure_text_layer(doc2)
assert set(ocred2.keys()) == {0}, f"trang trắng vẫn được coi là 'không có chữ thật', kỳ vọng OCR chạy, ocred={ocred2}"
assert ocred2[0] == [], f"trang trắng không có dòng chữ nào để OCR, kỳ vọng rect gốc rỗng, ocred={ocred2}"

# Trang dạng scan thật: nhúng ảnh có chữ tiếng Việt rõ ràng, không có lớp
# chữ nào -> OCR phải nhận diện được và chèn lớp chữ ẩn đúng vị trí.
doc3 = fitz.open()
page3 = doc3.new_page(width=400, height=200)
img_doc = fitz.open()
img_page = img_doc.new_page(width=400, height=200)
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
img_page.insert_text((30, 60), "Xin chào Việt Nam, đây là bài kiểm tra OCR.", fontsize=18, fontfile=FONT, fontname="F1")
pix = img_page.get_pixmap(matrix=fitz.Matrix(3, 3))
page3.insert_image(page3.rect, pixmap=pix)

assert _page_has_real_text(page3) is False
ocred3 = ensure_text_layer(doc3)
assert set(ocred3.keys()) == {0}, f"trang scan phải được OCR, ocred={ocred3}"
assert len(ocred3[0]) >= 1, f"phải trả về ít nhất 1 rect gốc do Vision nhận diện, ocred={ocred3}"
assert _page_has_real_text(page3) is True, "sau OCR trang phải có lớp chữ"

extracted = page3.get_text()
assert "Việt" in extracted and "Nam" in extracted, f"OCR phải nhận đúng dấu tiếng Việt, extracted={extracted!r}"

print("Tất cả self-check ocr_pdf đều pass.")


# --- _detect_vertical_grid_lines: dò đúng đường kẻ dọc THẬT (viền cột
# trong bảng) để cắt riêng từng dải cột trước khi OCR — bug thật đã gặp:
# Vision gộp nhầm chữ 2 cột liền kề thành 1 "dòng" khi ô bên trái có chữ
# dài gần chạm cột bên phải, không có khoảng trắng nào để phát hiện bằng
# cách đo khoảng cách chữ; phải dò đúng đường kẻ lưới thay thế. ---
def _make_page_pixmap(draw_line_x=None, width=400, height=100, zoom=2):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if draw_line_x is not None:
        page.draw_line(fitz.Point(draw_line_x, 0), fitz.Point(draw_line_x, height), color=(0, 0, 0), width=2)
    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))


line_pix = _make_page_pixmap(draw_line_x=200)
detected = _detect_vertical_grid_lines(line_pix)
assert len(detected) == 1, f"phải dò đúng 1 đường kẻ, ra {detected}"
assert abs(detected[0] - 400) <= 3, f"vị trí đường kẻ (px) phải khớp x=200pt * zoom 2x = 400px, ra {detected[0]}"

blank_pix = _make_page_pixmap(draw_line_x=None)
assert _detect_vertical_grid_lines(blank_pix) == [], "trang trắng không đường kẻ nào không được báo động giả"

print("Tất cả self-check _detect_vertical_grid_lines đều pass.")
