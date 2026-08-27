"""Dịch ảnh-chụp-bảng nhúng trong PDF (screenshot dán vào tài liệu, KHÔNG
phải chữ thật — get_text() không đọc được gì trong đó, khác hẳn bảng thật
mà translator_engine.py::build_table_cell_units() xử lý qua find_tables()).

OCR bằng Vision (ocr_cli, xem ocr_pdf.py), dựng lại lưới hàng/cột từ VỊ TRÍ
chữ OCR — KHÔNG dò đường kẻ pixel như ocr_pdf.py::_detect_vertical_grid_
lines(): đã kiểm chứng thực tế trên ảnh bảng thật của user (STRIDE table),
ảnh loại này thường KHÔNG có đường viền vẽ thật, chỉ dùng màu nền xen kẽ +
khoảng trắng để phân tách — dò pixel tối không tìm thấy gì. Dịch từng ô,
xoá ảnh gốc, vẽ lại bằng vector THẬT (không phải ảnh) với màu nền LẤY MẪU
từ chính ảnh gốc (giữ đúng bố cục/màu nền bảng). Màu chữ: hàng tiêu đề lấy
mẫu riêng (nền tương phản đổi tuỳ bảng), phần thân dùng 1 màu đồng nhất
thay vì lấy mẫu từng ô — xem `_BODY_INK`. Chữ canh giữa theo chiều dọc
trong từng ô, tiêu đề in đậm.

ponytail: import `fit_and_draw`/`local_bg_color` cục bộ bên trong hàm cần
dùng (không import ở đầu file) — translator_engine.py sẽ gọi module này,
import ở đầu file 2 bên sẽ tạo vòng lặp; giống cách translator_engine.py
đã làm với office_translate.py.
"""
import json
import os
import subprocess
import tempfile

import fitz  # PyMuPDF

from ocr_pdf import DEFAULT_OCR_BINARY, _detect_ink_color, _estimate_font_size

# Dòng OCR rộng hơn ngưỡng này bị nghi là Vision gộp nhầm 2 CỘT liền kề
# thành 1 dòng (biết trước qua ocr_pdf.py) — loại khỏi bước tìm ranh giới
# cột, chỉ tin các dòng "hẹp" (gần chắc chắn thuộc đúng 1 cột). Giá trị
# này lớn hơn hẳn dòng 1-cột dài nhất quan sát được thực tế (~0.25-0.30)
# nhưng nhỏ hơn hẳn dòng bị gộp 2 cột (~0.38+).
NARROW_LINE_MAX_WIDTH = 0.30
MIN_LINES_FOR_TABLE = 6  # ít hơn thì không đáng coi là bảng, bỏ qua an toàn
MIN_COLUMNS_FOR_TABLE = 2
MIN_ROWS_FOR_TABLE = 2
# Khoảng cách dọc giữa 2 dòng OCR LIÊN TIẾP TRONG CÙNG 1 CỘT nhỏ hơn mức
# này (x chiều cao dòng trung bình) coi là cùng 1 Ô (chữ word-wrap 2
# dòng); lớn hơn coi là sang hàng MỚI. Cùng hệ số `gap_factor=1.3` đã
# dùng cho merge_paragraph_blocks() (paragraphs.py).
ROW_GAP_FACTOR = 1.3

# Font đo độ rộng chữ để ước lượng số dòng khi word-wrap (canh giữa theo
# chiều dọc bên dưới) — cùng font Arial mà translator_engine.py dùng để vẽ
# chữ tiếng Việt (VN_FONT_DIR), hardcode lại 1 hằng số string ở đây thay vì
# import translator_engine ở ĐẦU file: import ở đầu 2 bên sẽ tạo vòng lặp
# (xem docstring translate_image_tables() bên dưới).
_VN_FONT = fitz.Font(fontfile="/System/Library/Fonts/Supplemental/Arial.ttf")
_LINE_HEIGHT_FACTOR = 1.25
# Màu chữ đồng nhất cho PHẦN THÂN bảng (không lấy mẫu từng ô) — lấy mẫu
# riêng lẻ từng ô đã gặp thực tế: 1-2 ô ra màu lệch (vd. xanh) dù cả bảng
# gốc chỉ dùng 1 màu đen, do nhiễu ảnh/anti-aliasing tại ô đó. Theo đúng
# yêu cầu: "màu chữ ở bảng đồng nhất 1 màu theo tài liệu" — quy ước phổ
# biến nhất là đen; hàng TIÊU ĐỀ (thường nền đậm, cần tương phản) vẫn lấy
# mẫu riêng vì màu tiêu đề đổi tuỳ thiết kế bảng (trắng/vàng/...).
_BODY_INK = (0, 0, 0)


def _estimate_line_count(text, font_size, width):
    """Ước lượng số dòng nếu vẽ `text` ở `font_size` trong khung rộng
    `width` (word-wrap đơn giản theo độ rộng ký tự thật của font Arial) —
    không cần khớp tuyệt đối với insert_htmlbox, chỉ cần đủ gần để canh
    giữa theo chiều dọc không lệch rõ mắt; nếu ước lượng thiếu, fit_and_draw
    ở nơi gọi vẫn tự giãn khung xuống như bình thường, không tràn ô."""
    if width <= 0:
        return 1
    space_w = _VN_FONT.text_length(" ", fontsize=font_size)
    lines = 0
    for para in text.split("\n"):
        words = para.split()
        if not words:
            lines += 1
            continue
        line_w = 0.0
        n = 1
        for w in words:
            ww = _VN_FONT.text_length(w, fontsize=font_size)
            if line_w and line_w + space_w + ww > width:
                n += 1
                line_w = ww
            else:
                line_w += (space_w if line_w else 0) + ww
        lines += n
    return max(1, lines)


def _vcentered_rect(rect, text, font_size):
    """Khung con canh giữa theo chiều dọc trong `rect`, làm điểm khởi đầu
    cho fit_and_draw() — chữ ít hơn ô thì bắt đầu thấp xuống 1 chút thay vì
    luôn dính sát mép trên; nếu ước lượng dòng bị thiếu, fit_and_draw tự
    giãn xuống `max_y1` (đáy `rect` gốc) như thường lệ, không bao giờ tràn
    ra ngoài ô."""
    n_lines = _estimate_line_count(text, font_size, rect.width)
    text_h = n_lines * font_size * _LINE_HEIGHT_FACTOR
    if text_h >= rect.height:
        return rect
    top = rect.y0 + (rect.height - text_h) / 2
    return fitz.Rect(rect.x0, top, rect.x1, rect.y1)


def _ocr_region(page, rect, ocr_binary, dpi=300):
    """OCR 1 vùng `rect` (toạ độ tuyệt đối trên trang) của `page`. Trả về
    list các dict {"rect": fitz.Rect (toạ độ tuyệt đối), "text": str}."""
    pix = page.get_pixmap(dpi=dpi, clip=rect)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pix.save(tmp_path)
        proc = subprocess.run([ocr_binary, tmp_path], capture_output=True, text=True, timeout=60)
        data = json.loads(proc.stdout)
    finally:
        os.unlink(tmp_path)
    if "error" in data:
        return []
    lines = []
    for line in data.get("lines", []):
        text = line["text"].strip()
        if not text:
            continue
        x0 = rect.x0 + line["x"] * rect.width
        y0 = rect.y0 + line["y"] * rect.height
        w = line["width"] * rect.width
        h = line["height"] * rect.height
        lines.append({"rect": fitz.Rect(x0, y0, x0 + w, y0 + h), "text": text})
    return lines


def _column_boundaries(lines, region):
    """Ranh giới cột (toạ độ x tuyệt đối) suy từ khoảng TRẮNG THẬT giữa
    các dòng OCR "hẹp" (gần chắc chắn thuộc đúng 1 cột — xem
    NARROW_LINE_MAX_WIDTH). Trả về None nếu tìm được < 2 cột (không đáng
    coi là bảng nhiều cột)."""
    narrow = [l for l in lines if l["rect"].width / region.width < NARROW_LINE_MAX_WIDTH]
    intervals = sorted((l["rect"].x0, l["rect"].x1) for l in narrow)
    bands = []
    for x0, x1 in intervals:
        if bands and x0 <= bands[-1][1]:
            bands[-1] = (bands[-1][0], max(bands[-1][1], x1))
        else:
            bands.append((x0, x1))
    if len(bands) < MIN_COLUMNS_FOR_TABLE:
        return None
    boundaries = [region.x0]
    for i in range(len(bands) - 1):
        boundaries.append((bands[i][1] + bands[i + 1][0]) / 2)
    boundaries.append(region.x1)
    return boundaries


def _column_row_bands(lines):
    """1 cột (list dòng OCR CÙNG cột, đã sort theo y0) → list [y0, y1] mỗi
    hàng logic — dòng cách dòng TRƯỚC nó (trong CÙNG cột này) dưới
    `ROW_GAP_FACTOR` lần chiều cao dòng trung bình CỦA CẢ BẢNG thì cùng 1
    Ô (chữ word-wrap nhiều dòng), xa hơn thì hàng MỚI.

    ponytail: gom theo TỪNG CỘT riêng (không gộp chung mọi cột rồi sort
    theo y0) — đã thử gộp chung và THẤT BẠI trên dữ liệu thật: các cột
    xen kẽ nhau theo Y khiến khoảng cách "dòng kế tiếp theo y0" đo được
    vô nghĩa (có thể âm, nhảy lung tung giữa các cột khác nhau)."""
    if not lines:
        return []
    avg_h = sum(l["rect"].height for l in lines) / len(lines)
    bands = [[lines[0]["rect"].y0, lines[0]["rect"].y1]]
    for l in lines[1:]:
        if l["rect"].y0 - bands[-1][1] < avg_h * ROW_GAP_FACTOR:
            bands[-1][1] = max(bands[-1][1], l["rect"].y1)
        else:
            bands.append([l["rect"].y0, l["rect"].y1])
    return bands


def _ocr_table_grid(page, region, ocr_binary):
    """OCR `region` (ảnh nhúng, toạ độ tuyệt đối) và dựng lại thành lưới
    hàng x cột. Trả về None nếu KHÔNG đủ tin cậy để coi là bảng (quá ít
    chữ, hoặc không tìm được ≥2 cột/≥2 hàng) — an toàn hơn là đoán bừa,
    nơi gọi giữ nguyên ảnh gốc trong trường hợp đó.

    Thuật toán hàng (đã kiểm chứng trên ảnh bảng thật của user — STRIDE
    table, khớp 100% với bảng gốc sau khi hiệu chỉnh): mỗi cột tự gom
    hàng riêng qua `_column_row_bands()` (không phải cột nào cũng đúng —
    cột có Ô word-wrap phức tạp dễ gom sai số hàng), lấy CỘT CHO NHIỀU
    HÀNG NHẤT làm "hàng tham chiếu" (dưới-đếm luôn nguy hiểm hơn — gộp
    nhầm 2 hàng thật thành 1 — nên tin cột đếm được NHIỀU nhất). Mọi dòng
    OCR (kể cả của chính cột tham chiếu) sau đó gán lại vào hàng tham
    chiếu có TÂM (không phải RÌA — rìa test sai 1 dòng ở biên mơ hồ) gần
    nhất, không dùng lại kết quả gom-theo-cột ban đầu của các cột khác.

    Trả về (dict): {"col_bounds": [...], "cells": {(row_idx, col_idx):
    {"rect": fitz.Rect hợp nhất mọi dòng con, "text": str đã nối các dòng
    con}}, "n_rows": int, "n_cols": int}."""
    pass1 = _ocr_region(page, region, ocr_binary)
    if len(pass1) < MIN_LINES_FOR_TABLE:
        return None
    col_bounds = _column_boundaries(pass1, region)
    if col_bounds is None:
        return None
    n_cols = len(col_bounds) - 1

    # Lượt 2: OCR RIÊNG từng dải cột theo ranh giới vừa tìm — tránh Vision
    # gộp nhầm 2 cột liền kề thành 1 dòng (xem docstring module).
    lines_by_col = []
    for c in range(n_cols):
        strip_rect = fitz.Rect(col_bounds[c], region.y0, col_bounds[c + 1], region.y1)
        strip_lines = sorted(_ocr_region(page, strip_rect, ocr_binary), key=lambda l: l["rect"].y0)
        lines_by_col.append(strip_lines)

    band_candidates = [_column_row_bands(lines) for lines in lines_by_col]
    ref_bands = max(band_candidates, key=len, default=[])
    if len(ref_bands) < MIN_ROWS_FOR_TABLE:
        return None
    ref_centers = [(y0 + y1) / 2 for y0, y1 in ref_bands]

    def nearest_row(y0, y1):
        yc = (y0 + y1) / 2
        return min(range(len(ref_centers)), key=lambda i: abs(yc - ref_centers[i]))

    # Ranh giới hàng ĐẦY ĐỦ (không chỉ khít riêng vùng chữ như ref_bands) —
    # mở rộng ra tới ĐIỂM GIỮA với hàng liền kề (hoặc mép region cho hàng
    # đầu/cuối). Cần cho full_rect bên dưới: ô CHỈ khít theo bbox chữ OCR
    # sẽ để hở khoảng trắng quanh mỗi ô khi vẽ nền — nhìn như nhiều thẻ
    # màu rời rạc thay vì 1 lưới liền mạch (đã gặp thực tế, user phát
    # hiện qua ảnh phóng to: "nền màu tùm lum", không giống dạng bảng).
    row_slots = []
    for i, (y0, y1) in enumerate(ref_bands):
        top = region.y0 if i == 0 else (ref_bands[i - 1][1] + y0) / 2
        bottom = region.y1 if i == len(ref_bands) - 1 else (y1 + ref_bands[i + 1][0]) / 2
        row_slots.append((top, bottom))

    # `sample_rect`/`sample_text`: LUÔN giữ dòng OCR GỐC ĐẦU TIÊN của ô này
    # (chưa nối với dòng nào khác) — dùng riêng cho ước lượng cỡ chữ. Nối
    # nhiều dòng lại ("text") mà vẫn đưa cho _estimate_font_size() cùng
    # bbox đã HỢP (rộng hơn/cao hơn nhiều so với 1 dòng gốc) làm sai lệch
    # hẳn phép đo width-based của nó (giả định 1-dòng-khớp-1-bbox) — gặp
    # thực tế: cỡ chữ ước lượng sai làm fit_and_draw() phải cắt bớt còn
    # "Tuyên bố…" thay vì câu đầy đủ.
    cells = {}
    for c, lines in enumerate(lines_by_col):
        for l in lines:
            r = nearest_row(l["rect"].y0, l["rect"].y1)
            key = (r, c)
            if key not in cells:
                full_rect = fitz.Rect(col_bounds[c], row_slots[r][0], col_bounds[c + 1], row_slots[r][1])
                cells[key] = {
                    "rect": full_rect, "text": l["text"],
                    "sample_rect": l["rect"], "sample_text": l["text"],
                }
            else:
                cells[key]["text"] += " " + l["text"]

    return {"col_bounds": col_bounds, "cells": cells, "n_rows": len(ref_bands), "n_cols": n_cols}


def _drop_nested_regions(regions):
    """Loại các rect NẰM GỌN bên trong 1 rect KHÁC lớn hơn trong cùng danh
    sách. Nhiều PDF ghép 1 bảng-ảnh từ NHIỀU lớp ảnh chồng nhau (ảnh khung
    viền trang trí + ảnh nội dung nằm lồng bên trong nó — kiểu ghép ảnh
    thường gặp khi export bảng có viền từ PowerPoint/Word) — thấy thực tế
    trên bảng STRIDE của user (`page.get_images()` trả về 2 xref cho đúng
    1 bảng nhìn thấy, 1 ảnh nằm lồng trong ảnh kia). Không lọc thì cả 2 lớp
    đều bị coi là 1 "bảng" riêng, OCR/dịch/vẽ 2 LẦN CHỒNG NHAU với biên
    khác nhau (lớp trong hẹp hơn lớp ngoài) — lần 2 thực chất OCR lại chính
    chữ tiếng Việt lớp 1 vừa vẽ (không còn là ảnh gốc nữa) rồi vẽ đè lệch
    bên trong, để sót 1 dải viền chữ cũ ở mép không bị lớp 2 xoá tới. Giữ
    lại rect LỚN NHẤT của mỗi cụm lồng nhau — xử lý 1 lần trên toàn bộ
    vùng nhìn thấy là đủ."""
    ordered = sorted(regions, key=lambda r: r.width * r.height, reverse=True)
    kept = []
    for r in ordered:
        if any(bigger.contains(r) for bigger in kept):
            continue
        kept.append(r)
    return kept


def translate_image_tables(page, image_rects, router, ocr_binary=None):
    """Với mỗi ảnh trong `image_rects` (rect tuyệt đối trên trang) trông
    giống 1 bảng chữ (OCR ra đủ chữ VÀ dựng được lưới ≥2 cột x ≥2 hàng —
    xem `_ocr_table_grid()`): dịch từng ô, xoá hẳn ảnh gốc, vẽ lại bằng
    vector thật — nền LẤY MẪU từ ảnh gốc, chữ hàng tiêu đề (row 0) in đậm +
    màu lấy mẫu riêng, chữ phần thân dùng 1 màu đồng nhất (`_BODY_INK`),
    mọi ô canh giữa theo chiều dọc. Ảnh không đủ tin cậy là bảng (ít chữ,
    không thấy lưới rõ — nghi là logo/sơ đồ/ảnh chụp thường) GIỮ NGUYÊN,
    không đụng tới.

    Trả về list các rect ĐÃ thay thế thành công — nơi gọi (process_pdf)
    cần loại các rect này khỏi `page_image_rects()`/Image Collision Guard
    sau đó, vì ảnh gốc không còn tồn tại nữa."""
    # Import cục bộ: translator_engine.py gọi module này, import ở đầu
    # file 2 bên sẽ tạo vòng lặp (translator_engine.py chưa định nghĩa
    # xong fit_and_draw/local_bg_color khi module này cố đọc chúng lúc
    # import) — cùng lý do translator_engine.py::main() import cục bộ
    # office_translate.py.
    from translator_engine import fit_and_draw, local_bg_color, SCALE_FACTOR
    from code_blocks import translate_units_with_code_awareness

    ocr_binary = ocr_binary or DEFAULT_OCR_BINARY
    replaced = []

    for region in _drop_nested_regions(image_rects):
        grid = _ocr_table_grid(page, region, ocr_binary)
        if grid is None:
            continue

        # Lấy mẫu màu nền/cỡ chữ TỪNG Ô, nhưng màu chữ chỉ lấy mẫu 1 lần
        # cho HÀNG TIÊU ĐỀ (row 0) — xem _BODY_INK ở đầu file. Vẫn phải
        # lấy mẫu TRƯỚC khi xoá ảnh gốc (ảnh còn nguyên pixel lúc này).
        cell_info = []
        for (row, _col), cell in grid["cells"].items():
            rect = cell["rect"]
            cell_info.append({
                "row": row,
                "rect": rect,
                "original": cell["text"],
                "bg": local_bg_color(page, rect, pad=0),
                "size": _estimate_font_size(
                    cell["sample_text"], cell["sample_rect"].width, cell["sample_rect"].height,
                ),
            })

        header_ink = _BODY_INK
        for info in cell_info:
            if info["row"] == 0:
                header_ink = _detect_ink_color(page, info["rect"])
                break
        for info in cell_info:
            info["ink"] = header_ink if info["row"] == 0 else _BODY_INK

        units = [(info["original"], True) for info in cell_info]
        # enforce_length_guard=False: guard đó thiết kế cho khung PDF CỐ
        # ĐỊNH (translator_engine.py::process_pdf) — không có chỗ giãn nên
        # thà giữ bản gốc còn hơn tràn khung. Ở đây fit_and_draw() bên
        # dưới đã TỰ co cỡ chữ để vừa ô rồi (giống lý do office_translate.py
        # cũng tắt guard này) — bật guard sẽ làm rơi các chữ NGẮN (dễ vượt
        # tỉ lệ phình 1.6x dù tuyệt đối chỉ dài thêm vài ký tự) về nguyên
        # bản tiếng Anh. Đã tái hiện thực tế: "Threat"/"Tampering" bị bỏ
        # qua không dịch vì lý do này trước khi thêm tham số này.
        results = translate_units_with_code_awareness(units, router, enforce_length_guard=False)
        for info, result in zip(cell_info, results):
            translated, _engine, _deepl_error, item_error = result
            info["translated"] = info["original"] if item_error is not None else translated

        # text=1 (ignore text): vùng này chỉ nên có ảnh, không đụng tới
        # bất kỳ chữ thật nào lỡ chồng lấn rìa vùng ảnh.
        page.add_redact_annot(region, fill=None)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE, graphics=0, text=1)

        for info in cell_info:
            page.draw_rect(info["rect"], fill=info["bg"], color=None)
        for info in cell_info:
            translated = info["translated"]
            if not translated.strip():
                continue
            bold = info["row"] == 0
            # Khung khởi điểm canh giữa theo chiều dọc (căn trái vẫn giữ
            # nguyên — mặc định của insert_htmlbox cho khối <p>).
            start_rect = _vcentered_rect(info["rect"], translated, info["size"] * SCALE_FACTOR)
            fit_and_draw(page, start_rect, translated, info["size"],
                         color=info["ink"], style=(bold, False), max_y1=info["rect"].y1)

        replaced.append(region)

    return replaced
