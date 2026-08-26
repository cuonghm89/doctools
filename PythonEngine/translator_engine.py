"""C-PDF Gear - Python Core Engine.

Bảo tồn layout & hình ảnh: chỉ khối chữ bị xóa trắng và vẽ lại; lớp hình
ảnh/vector không bao giờ bị đụng tới. Báo tiến trình dưới dạng JSON mỗi
dòng trên stdout để frontend Swift đọc.

Cách dùng:
    python3 translator_engine.py --config /path/to/config.json

Các trường trong config.json: input_pdf, output_pdf, deepl_key, gemini_key,
deepl_limit (tùy chọn), max_pages (tùy chọn).
"""
import argparse
import html as html_lib
import json
import os
import sys
from collections import Counter

import fitz  # PyMuPDF

from router import TranslationRouter, is_table_lines
from code_blocks import translate_units_with_code_awareness
from paragraphs import (
    merge_paragraph_blocks, is_bullet_text, block_font_style,
    block_text_color as block_text_color_ints,
)
from ocr_pdf import ensure_text_layer

# Tiếng Việt dài hơn tiếng Anh khoảng 20-30%. Bù trừ sẵn hệ số này ngay từ
# đầu nghĩa là phần lớn đoạn văn sẽ vừa khung ngay lần thử đầu tiên, thay
# vì phải phụ thuộc hoàn toàn vào chuỗi xử lý dự phòng co/giãn/cắt bên
# dưới. Không cho người dùng tùy chỉnh — mục đích chính là app tự xử lý
# việc này mà không ai phải chỉnh thanh trượt nào.
SCALE_FACTOR = 0.88

# Ngưỡng tự-co tối thiểu của chính insert_htmlbox: nếu cỡ đã bù trừ ở trên
# vẫn không vừa, cho phép co thêm xuống tỷ lệ này (tính trên cỡ đã bù trừ)
# trước khi mới tính đến việc giãn khung.
SCALE_LOW = 0.7

VN_FONT_DIR = "/System/Library/Fonts/Supplemental"
VN_ARCHIVE = fitz.Archive(VN_FONT_DIR)
VN_CSS = """
@font-face { font-family: "VN"; src: url(Arial.ttf); }
@font-face { font-family: "VN"; font-weight: bold; src: url("Arial Bold.ttf"); }
@font-face { font-family: "VN"; font-style: italic; src: url("Arial Italic.ttf"); }
@font-face { font-family: "VN"; font-weight: bold; font-style: italic;
             src: url("Arial Bold Italic.ttf"); }
* { font-family: "VN"; margin: 0; padding: 0; }
"""


def emit(**event):
    print(json.dumps(event), flush=True)


def block_text(block):
    """Nối các dòng trong 1 khối. Dòng bullet/đánh số giữ nguyên xuống dòng
    (mỗi mục phải nằm trên dòng riêng); dòng của đoạn văn word-wrap bình
    thường thì nối bằng dấu cách — nối không có ký tự phân cách sẽ dính chữ
    liền nhau ("productsImprove"), còn luôn dùng "\n" sẽ ép xuống dòng giữa
    câu trong khi đáng ra phải tự chảy lại (reflow) bình thường.

    Chuẩn hóa \\xa0 (non-breaking space) về dấu cách thường: chữ do OCR
    hoặc do chính engine này chèn trước đó (qua insert_textbox/insert_htmlbox
    với font Arial) tự động thay dấu cách bằng \\xa0 để canh lề — vô hại khi
    hiển thị, nhưng \\xa0 là ký tự CẤM NGẮT DÒNG trong HTML. Nếu chuỗi này bị
    trích lại làm nguồn để dịch/gộp/vẽ lại (trang đã OCR), insert_htmlbox sẽ
    coi cả cụm dính \\xa0 là 1 "từ" không thể tách, phá vỡ word-wrap."""
    line_texts = [
        "".join(span["text"] for span in line["spans"]).replace("\xa0", " ")
        for line in block["lines"]
    ]
    if len(line_texts) > 1 and any(is_bullet_text(t) for t in line_texts):
        return "\n".join(t.strip() for t in line_texts).strip()
    return " ".join(t.strip() for t in line_texts).strip()


def local_bg_color(page, rect, pad=2):
    """Màu pixel phổ biến nhất ngay quanh khung của khối này, dùng để xóa
    trắng (thực chất là "xóa theo nền") chữ cũ sao cho vùng xóa hòa vào nền
    thay vì hiện ra thành 1 ô lạc quẻ. Lấy mẫu theo từng khối thay vì lấy 1
    lần cho cả trang: 1 trang chia thành cột tối và cột sáng thì không có
    khái niệm "màu nền của cả trang", và dùng 1 màu phẳng cho cả trang sẽ
    tô sai màu ở bất cứ đâu trang không phải màu đó.
    ponytail: trong 1 bbox chữ hẹp, pixel nền vẫn áp đảo hẳn số pixel là nét
    chữ, nên "phổ biến nhất" gần như chắc chắn rơi vào màu nền chứ không
    phải màu chữ — nhưng nền dạng ảnh chụp/gradient hoàn toàn sẽ làm hỏng
    cách này."""
    clip = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad) & page.rect
    pix = page.get_pixmap(clip=clip)
    n = pix.n
    samples = pix.samples
    counts = Counter(samples[i:i + 3] for i in range(0, len(samples), n))
    if not counts:
        return (1, 1, 1)
    r, g, b = counts.most_common(1)[0][0]
    return (r / 255, g / 255, b / 255)


FULL_PAGE_IMAGE_AREA_RATIO = 0.6


def page_image_rects(page):
    """Toạ độ mọi hình ảnh nhỏ, rời rạc trên trang (logo, icon, biểu đồ) —
    dùng cho Image Collision Guard.

    Ảnh chiếm gần hết trang (>60% diện tích) bị loại khỏi danh sách này có
    chủ đích: đó là ảnh nền/trang scan, không phải 1 logo/icon cần bảo vệ.
    Với 1 trang scan, TOÀN BỘ nội dung (kể cả chữ) nằm trên đúng 1 tấm ảnh
    duy nhất — nếu coi ảnh đó là "cần bảo vệ" như 1 logo, Image Collision
    Guard sẽ chặn luôn mọi chữ OCR được (xem ocr_pdf.py), vì bbox của chữ
    nào cũng nằm trọn trong ảnh nền đó. Ảnh nhỏ, rời rạc mới là thứ Guard
    này được thiết kế để bảo vệ (logo công ty, icon sơ đồ, biểu đồ minh
    họa) — những ảnh đó luôn chiếm 1 phần nhỏ của trang."""
    page_area = page.rect.width * page.rect.height
    rects = []
    for img in page.get_images(full=True):
        xref = img[0]
        for r in page.get_image_rects(xref):
            area = r.width * r.height
            if page_area and area / page_area <= FULL_PAGE_IMAGE_AREA_RATIO:
                rects.append(r)
    return rects


def intersects_image(rect, image_rects, threshold=0.15):
    """True nếu rect đè lên 1 hình ảnh thật vượt quá 1 khoảng nhỏ. Chạm nhẹ
    tình cờ vào góc 1 icon là chuyện thường và vô hại; vượt quá ngưỡng này
    nghĩa là việc dịch/xóa ở đây có nguy cơ làm hỏng hoặc che mất hình ảnh,
    nên nơi gọi nên bỏ qua khối đó thay vì xử lý."""
    area = rect.width * rect.height
    if not area:
        return False
    for img_rect in image_rects:
        inter = rect & img_rect
        if not inter.is_empty and (inter.width * inter.height) / area > threshold:
            return True
    return False


def find_real_tables(page):
    """Bảng THẬT trên trang, nhận diện qua đường kẻ lưới thật
    (`page.find_tables()` — PyMuPDF tự phân tích vector graphics), khác
    hẳn `is_table_lines()` chỉ ĐOÁN qua mật độ chữ số để định tuyến DeepL/
    Gemini. Trả về [] nếu trang không có bảng nào hoặc find_tables() lỗi
    (best-effort, không được phép làm hỏng cả trang vì 1 bảng khó phân
    tích)."""
    try:
        return list(page.find_tables().tables)
    except Exception:
        return []


def _block_mostly_in_region(bbox, region, threshold=0.6):
    rect = fitz.Rect(bbox)
    area = rect.width * rect.height
    if not area:
        return False
    inter = rect & region
    return (inter.width * inter.height) / area >= threshold


def _fill_bands(page, region):
    """Mọi hình chữ nhật TÔ MÀU (vector graphics thật, không phải chữ)
    nằm trong `region` — dùng để dò dải màu nền xen kẽ theo hàng của 1
    bảng. Trả về list (rect, fill_color_làm_tròn)."""
    bands = []
    for d in page.get_drawings():
        fill = d.get("fill")
        r = d.get("rect")
        if fill is None or r is None:
            continue
        r = fitz.Rect(r)
        if (r & region).is_empty:
            continue
        bands.append((r, tuple(round(c, 3) for c in fill)))
    return bands


def _band_color_at(bands, x, y):
    """Màu (đã làm tròn) của dải nền NHỎ NHẤT chứa điểm (x, y), None nếu
    không khớp dải nào. Ưu tiên dải nhỏ nhất để tránh khớp nhầm 1 rect nền
    to bao trùm cả bảng thay vì đúng dải của riêng hàng đó."""
    best = None
    for r, color in bands:
        if r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1:
            area = r.width * r.height
            if best is None or area < best[0]:
                best = (area, color)
    return best[1] if best else None


def _merge_wrapped_table_rows(page, table):
    """find_tables() đôi khi tách 1 hàng LOGIC (nhãn/mô tả word-wrap dài,
    vd "DCS (Data Security & Information Lifecycle Management)") thành
    NHIỀU đối tượng "row" RIÊNG BIỆT — gặp thực tế trên file PDF thật của
    user. Hậu quả: (1) dịch mất ngữ cảnh (nửa câu "chains." dịch riêng ra
    "Xiềng xích." thay vì đúng nghĩa "chuỗi cung ứng"), (2) x0 giữa các
    "row" bị tách lệch nhau vài pixel (find_tables() báo cột hơi khác cho
    "row" bị tách so với row bình thường) → thụt lề không đều khi vẽ lại.

    Nhiều bảng dùng dải MÀU NỀN xen kẽ theo hàng LOGIC thật (không theo
    cách find_tables() tách "row") — đây là tín hiệu đáng tin lấy trực
    tiếp từ vector graphics thật (`page.get_drawings()`, KHÁC get_text(),
    không thể bị đánh lừa bởi cách đọc chữ sai thứ tự): 2 "row" liền kề
    thuộc CÙNG 1 dải màu nền chắc chắn là CÙNG 1 hàng thật, gộp lại theo
    từng cột (nối text bằng dấu cách, hợp bbox). Bảng không có dải màu
    xen kẽ (get_drawings() không khớp gì) thì KHÔNG gộp gì cả — an toàn
    hơn là đoán bừa.

    Trả về list các "row" đã gộp, cùng shape (row.cells, row_text) như
    input để build_table_cell_units() dùng lại y nguyên logic bên dưới."""
    bands = _fill_bands(page, fitz.Rect(table.bbox))
    merged = []  # list of {"cells": [...], "texts": [...], "color": ...}
    for row, row_text in zip(table.rows, table.extract()):
        probe = next((fitz.Rect(c) for c in row.cells if c), None)
        color = _band_color_at(bands, probe.x0 + 2, (probe.y0 + probe.y1) / 2) if probe else None

        if merged and color is not None and merged[-1]["color"] == color:
            target = merged[-1]
            for i, (cell_bbox, text) in enumerate(zip(row.cells, row_text)):
                if cell_bbox is None:
                    continue
                rect = fitz.Rect(cell_bbox)
                text = (text or "").strip()
                if target["cells"][i] is None:
                    target["cells"][i] = rect
                    target["texts"][i] = text
                else:
                    target["cells"][i] = target["cells"][i] | rect
                    if text:
                        target["texts"][i] = (target["texts"][i] + " " + text).strip()
        else:
            cells = [fitz.Rect(c) if c else None for c in row.cells]
            texts = [(t or "").strip() if c else None for c, t in zip(row.cells, row_text)]
            merged.append({"cells": cells, "texts": texts, "color": color})

    return _normalize_column_x0([(m["cells"], m["texts"]) for m in merged])


def _normalize_column_x0(merged_rows, tolerance=8):
    """find_tables() đôi khi báo x0 khác nhau cho CÙNG 1 cột hiển thị giữa
    các hàng — không thể canh theo CHỈ SỐ cột (`cells[i]`): độ mịn lưới nó
    tự dò được đổi theo từng hàng, nên "Miền" rơi vào index 0 ở hàng bình
    thường nhưng lại rơi vào index 1 ở hàng vừa bị `_merge_wrapped_table_
    rows()` gộp lại (gặp thực tế trên file PDF thật của user: nhãn "DCS"
    lệch ~5.4pt so với "AAC" dù cùng 1 cột, đã thử canh theo index vẫn
    không sửa được vì 2 nhãn đó ở 2 index KHÁC NHAU).

    Thay vào đó gom CỤM theo giá trị x0 thật gần nhau (trong `tolerance`)
    trên TOÀN BẢNG — chỉ tính từ ô có chữ thật (bỏ ô gutter/đệm rỗng giữa
    các cột, nếu không sẽ bắc cầu nhầm 2 cột hiển thị khác nhau qua chuỗi
    ô đệm liền kề). Mọi ô trong 1 cụm canh về x0 NHỎ NHẤT của cụm (biên
    ngoài thật — ô hẹp hơn chỉ có thể là tập con nằm bên trong)."""
    all_x0 = sorted({
        rect.x0 for cells, texts in merged_rows
        for rect, text in zip(cells, texts)
        if rect is not None and text
    })
    clusters = []
    for x0 in all_x0:
        if clusters and x0 - clusters[-1][-1] <= tolerance:
            clusters[-1].append(x0)
        else:
            clusters.append([x0])
    canonical_x0 = {x0: min(cluster) for cluster in clusters for x0 in cluster}

    for cells, texts in merged_rows:
        for i, (rect, text) in enumerate(zip(cells, texts)):
            if rect is not None and text and rect.x0 in canonical_x0:
                cells[i] = fitz.Rect(canonical_x0[rect.x0], rect.y0, rect.x1, rect.y1)
    return merged_rows


def build_table_cell_units(page, tables):
    """1 bảng THẬT (find_real_tables()) → list (rect, text) cho từng Ô có
    chữ, bỏ qua ô bị ô khác merge chiếm (`cells[i] is None`) và ô rỗng.
    Gộp trước các "row" bị find_tables() tách nhầm do word-wrap (xem
    _merge_wrapped_table_rows()).

    Lý do tách riêng khỏi pipeline đoạn văn thường (group_translation_
    units/merge_paragraph_blocks): với bảng nhiều dòng có CHIỀU CAO KHÁC
    NHAU giữa các cột, chính `page.get_text("dict")` (PyMuPDF) đã đọc SAI
    THỨ TỰ đọc ngay từ bước trích xuất thô — nhãn hàng và nội dung ô kế
    tiếp bị nằm chung 1 "block" TRƯỚC KHI code của app chạm vào, khác hẳn
    lỗi bullet-marker (do chính merge_paragraph_blocks() tự gộp nhầm — xem
    paragraphs.py). Không heuristic nào sửa được thứ tự đã sai từ nguồn;
    phải đọc riêng theo đúng lưới ô mà find_tables() đã phân tích được."""
    jobs = []
    for table in tables:
        for cells, texts in _merge_wrapped_table_rows(page, table):
            for cell_bbox, text in zip(cells, texts):
                if cell_bbox is None or not text:
                    continue
                jobs.append((cell_bbox, text))
    return _dedup_cell_jobs(jobs)


def _dedup_cell_jobs(jobs):
    """find_tables() đôi khi trả về 2 Ô CHỒNG LẤN cho cùng 1 nội dung — 1 ô
    lớn bao trọn 1 ô nhỏ hơn nằm ngay bên trong, cùng hệt 1 chuỗi text.
    Gặp thực tế trên file PDF thật của user (dòng nhãn đầu tiên của 1 bảng
    bị nhân đôi, dịch/vẽ 2 lần đè lên nhau). Giữ lại ô LỚN HƠN (khớp khung
    hiển thị thật hơn), bỏ ô nhỏ trùng lặp."""
    keep = []
    for rect, text in jobs:
        area = rect.width * rect.height
        dropped = False
        for i, (kept_rect, kept_text) in enumerate(keep):
            if text != kept_text:
                continue
            kept_area = kept_rect.width * kept_rect.height
            inter = rect & kept_rect
            inter_area = inter.width * inter.height
            if area and kept_area and inter_area / min(area, kept_area) > 0.8:
                if area > kept_area:
                    keep[i] = (rect, text)
                dropped = True
                break
        if not dropped:
            keep.append((rect, text))
    return keep


def cell_style(page, rect):
    """Suy ra (cỡ chữ, màu, style) đại diện cho 1 Ô bảng từ chính chữ thật
    nằm trong đó. find_tables()/extract() chỉ cho text phẳng, không có
    sẵn 1 PyMuPDF 'block' cho từng ô — tự dựng 1 'block' giả từ mọi dòng
    nằm trong `clip` rồi dùng lại đúng các hàm suy luận style/color đã có
    cho block thật (paragraphs.py), tránh viết trùng logic."""
    lines = [
        line for b in page.get_text("dict", clip=rect)["blocks"]
        if "lines" in b for line in b["lines"]
    ]
    if not lines:
        return 9.0, (0, 0, 0), (False, False)
    fake_block = {"lines": lines}
    sizes = [span["size"] for line in lines for span in line["spans"]]
    avg_size = sum(sizes) / len(sizes) if sizes else 9.0
    return avg_size, block_text_color_ints(fake_block), block_font_style(fake_block)


def growth_ceiling(rect, other_bboxes, page_bottom, margin=2):
    """1 khung được phép giãn xuống dưới tối đa bao nhiêu mà không đè lên
    thứ nằm bên dưới nó. Xét mọi khung khác giao nhau theo chiều ngang với
    khung này và bắt đầu ở vị trí thấp hơn trên trang, rồi dừng lại ngay
    phía trên khung gần nhất.

    So sánh với rect.y0 (đỉnh của chính khung này), không phải rect.y1
    (đáy của nó): các dòng liền kề thường có bbox hơi lấn vào nhau vài điểm
    ảnh (do phần chân/đỉnh chữ theo metric của font), nên nếu bắt buộc
    khung kia phải bắt đầu đúng-tại-hoặc-dưới đáy của TA thì sẽ bỏ sót
    hàng xóm thật, khiến việc giãn chạy không kiểm soát tới tận đáy trang."""
    below = [
        b for b in other_bboxes
        if b[1] > rect.y0 and b[2] > rect.x0 and b[0] < rect.x1
    ]
    if below:
        return min(b[1] for b in below) - margin
    return page_bottom - margin


def _render_html(text, color, style, font_size):
    bold, italic = style
    r, g, b = color
    open_tag = ("<b>" if bold else "") + ("<i>" if italic else "")
    close_tag = ("</i>" if italic else "") + ("</b>" if bold else "")
    body = html_lib.escape(text).replace("\n", "<br/>")
    # margin/padding phải reset ngay inline, không chỉ dựa vào rule
    # "* {...}" trong VN_CSS: engine HTML của MuPDF vẫn áp margin mặc định
    # ~1em của thẻ <p> đè lên trên, margin này tỷ lệ theo font-size và âm
    # thầm ăn mất phần lớn chiều cao của khung ở cỡ chữ lớn (1 tiêu đề 63pt
    # thực chất mất thêm ~63pt vào margin vô hình, khiến nó bị coi là
    # "không vừa" dù nét chữ thật sự chẳng cao đến thế).
    return (
        f'<p style="margin:0; padding:0; color:rgb({r},{g},{b}); '
        f'font-size:{font_size:.1f}pt;">{open_tag}{body}{close_tag}</p>'
    )


def _try_insert(page, rect, text, color, style, font_size):
    html_content = _render_html(text, color, style, font_size)
    spare, _scale = page.insert_htmlbox(rect, html_content, css=VN_CSS,
                                         scale_low=SCALE_LOW, archive=VN_ARCHIVE)
    return spare >= 0


def fit_and_draw(page, rect, text, base_font_size, color, style, max_y1=None):
    """Vẽ chữ đã dịch một cách an toàn, theo 3 tầng leo thang:

    1. Khung gốc, ở cỡ base_font_size * SCALE_FACTOR — mức bù trừ "tiếng
       Việt dài hơn" đã tích hợp sẵn — để insert_htmlbox tự co thêm (xuống
       70% của mức đó) nếu cần. Không có vòng lặp tự co viết tay: engine
       word-wrap + auto-fit của insert_htmlbox làm việc này ngay trong nó,
       và không vẽ gì cả nếu không thể làm vừa (khác với insert_textbox cũ,
       vốn vẫn vẽ chữ tràn ra ngoài bất kể) — nên thử lại nhiều lần ở đây
       không bao giờ để lại "ma chữ".
    2. Nếu vẫn chưa đủ, giãn khung xuống dưới — nhưng giới hạn ở max_y1 để
       không bao giờ giãn đè lên thứ nằm bên dưới nó.
    3. Nếu khung đã giới hạn vẫn không đủ, cắt bớt chữ và thêm dấu "…". Giữ
       nguyên bố cục luôn thắng việc hiển thị đủ từng chữ — 1 nhãn sơ đồ
       thiếu vài ký tự vẫn tốt hơn 1 nhãn đè lên icon hoặc đường kẻ cạnh nó.
    """
    font_size = base_font_size * SCALE_FACTOR

    if _try_insert(page, rect, text, color, style, font_size):
        return

    grown_rect = rect
    if max_y1 is not None:
        grown_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, max(max_y1, rect.y1))
    if grown_rect != rect and _try_insert(page, grown_rect, text, color, style, font_size):
        return

    _truncate_and_draw(page, grown_rect, text, color, style, font_size)


def _truncate_and_draw(page, rect, text, color, style, font_size):
    """Bỏ dần từ ở cuối, rồi tới từng ký tự, cho đến khi có thứ thực sự vừa
    khung và vẽ được. insert_htmlbox không vẽ gì khi thất bại, nên thử theo
    cách này an toàn — không có chữ trùng/"ma chữ" từ các lần thử hỏng."""
    words = text.split()
    for n in range(len(words), 0, -1):
        candidate = " ".join(words[:n])
        if n < len(words):
            candidate += "…"
        if _try_insert(page, rect, candidate, color, style, font_size):
            return

    chars = text.strip()
    while len(chars) > 1:
        chars = chars[:-1]
        if _try_insert(page, rect, chars + "…", color, style, font_size):
            return
    # Khung nhỏ đến mức không vừa nổi dù chỉ 1 ký tự ở scale_low — cực kỳ
    # hiếm gặp. Không còn gì để làm; để vùng đã xóa trống thay vì vẽ ra thứ
    # vẫn không vừa.


def group_translation_units(group):
    """1 nhóm trở thành 1 đơn vị dịch (cả đoạn văn gộp thành 1 chuỗi) trừ
    khi nó là dạng danh sách, khi đó mỗi dòng gốc trở thành 1 đơn vị riêng
    để các mục không bị nhét chung thành 1 khối."""
    texts = [block_text(b) for b in group["sub_blocks"]]
    all_lines = [line for b in group["sub_blocks"] for line in b["lines"]]
    is_table = is_table_lines(all_lines)

    if len(texts) > 1 and any(is_bullet_text(t) for t in texts):
        return [(t, is_table) for t in texts]
    return [(" ".join(texts), is_table)]


def build_translation_units(groups):
    """Gom đơn vị dịch của mọi nhóm thành 1 list chung cho cả trang, kèm
    theo (vị_trí_bắt_đầu, số_lượng) vào list đó cho từng nhóm, để cả trang
    có thể dịch trong 1 lần gọi theo batch rồi ráp lại sau."""
    units = []
    spans = []
    for group in groups:
        group_units = group_translation_units(group)
        start = len(units)
        units.extend(group_units)
        spans.append((start, len(group_units)))
    return units, spans


def process_pdf(input_pdf, output_pdf, router, max_pages=0):
    doc = fitz.open(input_pdf)
    # Trang nào không có lớp chữ thật (PDF dạng scan) được OCR và chèn 1
    # lớp chữ vô hình ngay tại đây — trang có chữ sẵn thì bỏ qua bước này
    # gần như tức thì. Sau bước này, mọi trang trong `doc` đều có
    # get_text("dict") trả về chữ để dùng, nên phần logic bên dưới không
    # cần biết chữ đó là OCR hay có sẵn trong PDF.
    ocred_pages = ensure_text_layer(doc)
    # max_pages > 0: chỉ dịch N trang đầu (test nhanh chất lượng mà không
    # cần chờ cả tài liệu), các trang còn lại lưu nguyên không đụng tới.
    total = min(max_pages, len(doc)) if max_pages else len(doc)

    for page_index, page in enumerate(doc):
        if page_index >= total:
            break

        # Bảng THẬT (đường kẻ lưới thật, không phải đoán): dịch/vẽ riêng
        # theo từng Ô TRƯỚC khi đọc block cho pipeline đoạn văn thường bên
        # dưới — xem docstring build_table_cell_units() lý do phải tách
        # riêng. Redact+vẽ xong thì đọc lại get_text("dict") sẽ thấy đúng
        # chữ Việt vừa vẽ (không phải chữ Anh gốc) trong vùng bảng.
        real_tables = find_real_tables(page)
        table_regions = [fitz.Rect(t.bbox) for t in real_tables]
        cell_jobs = build_table_cell_units(page, real_tables)
        if cell_jobs:
            cell_units = [(text, True) for _rect, text in cell_jobs]
            cell_results = translate_units_with_code_awareness(cell_units, router)
            cell_pending = []
            for (rect, orig_text), result in zip(cell_jobs, cell_results):
                translated, engine, deepl_error, item_error = result
                if item_error is not None or not translated.strip():
                    continue
                if orig_text.isupper():
                    translated = translated.upper()
                size, color, style = cell_style(page, rect)
                bg_color = local_bg_color(page, rect)
                page.add_redact_annot(rect, fill=bg_color)
                cell_pending.append((rect, translated, size, color, style))
                emit(type="progress", page=page_index + 1, total=total, engine=engine, detail=deepl_error)
            if cell_pending:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=0)
                for rect, translated, size, color, style in cell_pending:
                    # max_y1=rect.y1: không cho giãn khung qua khỏi chính ô
                    # đó — giãn xuống sẽ đè lên hàng dưới, khác đoạn văn
                    # thường có chỗ trống để giãn.
                    fit_and_draw(page, rect, translated, size, color=color, style=style, max_y1=rect.y1)

        blocks = page.get_text("dict")["blocks"]
        if table_regions:
            # Bỏ block nằm trong vùng bảng khỏi pipeline đoạn văn thường —
            # nội dung đó đã dịch/vẽ ở trên rồi (hoặc cố ý bỏ qua nếu lỗi),
            # không được để merge_paragraph_blocks() động vào lần nữa.
            blocks = [
                b for b in blocks
                if not any(_block_mostly_in_region(b["bbox"], r) for r in table_regions)
            ]
        groups = merge_paragraph_blocks(blocks)
        all_bboxes = [g["bbox"] for g in groups]
        image_rects = page_image_rects(page)
        pending = []  # (rect, chữ_đã_dịch, cỡ_chữ_gốc, màu, style, max_y1)

        # Dịch cả trang trong 1 lần gọi theo batch thay vì 1 lần gọi mạng
        # cho mỗi đoạn văn/bullet — đây là đòn bẩy chính để giảm thời gian
        # xử lý với tài liệu dài.
        units, spans = build_translation_units(groups)
        batch_results = translate_units_with_code_awareness(units, router) if units else []

        for group, (start, count) in zip(groups, spans):
            first_block = group["sub_blocks"][0]
            # `rect` (vị trí VẼ chữ dịch) giữ nguyên bbox của nhóm — bbox
            # này lấy từ chữ ẩn OCR đã chèn, dùng để vẽ là hợp lý (đúng cột/
            # kích thước bố cục). `erase_rect` (vùng XÓA nền) có thể rộng
            # hơn — không được dùng để vẽ, nếu không chữ dịch sẽ tràn sang
            # ô/cột bên cạnh (bảng biểu dày đặc).
            rect = fitz.Rect(group["bbox"])
            erase_rect = fitz.Rect(rect)
            ocr_ink_colors = []

            if page_index in ocred_pages:
                # Trang scan: bbox đọc lại từ chữ ẩn vừa OCR-chèn hẹp hơn
                # vùng chữ THẬT trong ảnh (chữ ẩn dùng font/cỡ chữ ước
                # lượng, khác font gốc trong ảnh) — nếu dùng bbox này để
                # xóa nền sẽ sót rìa chữ gốc lộ ra sau khi vẽ chữ dịch đè
                # lên. Nới erase_rect ra bằng rect GỐC do Vision nhận diện
                # (mọi rect chồng lấn khối này) để đảm bảo xóa hết pixel
                # chữ cũ; đồng thời lấy màu mực Vision đã dò được cho từng
                # dòng gốc (OCR không tự cho biết màu chữ).
                for orig_rect, orig_color in ocred_pages[page_index]:
                    if orig_rect.intersects(rect):
                        erase_rect |= orig_rect
                        ocr_ink_colors.append(orig_color)

            if intersects_image(erase_rect, image_rects):
                # Image Collision Guard: không xóa hay vẽ đè lên 1 hình
                # ảnh/logo thật — giữ nguyên chữ gốc của khối này.
                emit(type="progress", page=page_index + 1, total=total,
                     engine="skipped", detail="Đè lên hình ảnh, giữ nguyên bản gốc")
                continue

            base_font_size = group["size"]
            # Chữ OCR (vô hình) luôn mang màu đen hardcode lúc chèn — màu
            # đó vô nghĩa, phải dùng màu mực THẬT dò được từ ảnh scan thay
            # vì đọc lại màu của chính chữ ẩn.
            text_color = ocr_ink_colors[0] if ocr_ink_colors else block_text_color_ints(first_block)
            style = block_font_style(first_block)
            other_bboxes = [b for b in all_bboxes if b != group["bbox"]]
            max_y1 = growth_ceiling(rect, other_bboxes, page.rect.y1)

            unit_results = batch_results[start:start + count]
            failed = next((r[3] for r in unit_results if r[3] is not None), None)
            if failed is not None:
                # 1 đơn vị lỗi (bị chặn vì an toàn, thiếu key dự phòng,...)
                # không được phép vứt bỏ mọi trang/nhóm khác đã dịch xong.
                # Giữ nguyên chữ gốc của nhóm này và tiếp tục.
                emit(type="progress", page=page_index + 1, total=total,
                     engine="skipped", detail=failed)
                continue

            # DeepL/Gemini không giữ nguyên chữ HOA gốc — 1 tiêu đề viết
            # hoa toàn bộ ("MESSAGE QUERY...") dịch xong thường trả về dạng
            # câu thường ("Thông điệp truy vấn..."), làm mất kiểu chữ hoa
            # đặc trưng của tiêu đề. Nếu văn bản GỐC toàn chữ hoa, ép chữ
            # dịch cũng thành chữ hoa để giữ đúng kiểu trình bày ban đầu.
            original_units = units[start:start + count]
            translated_parts = []
            for (orig_text, _is_table), result in zip(original_units, unit_results):
                part = result[0]
                if orig_text.strip() and orig_text.isupper():
                    part = part.upper()
                translated_parts.append(part)
            translated = "\n".join(translated_parts)
            engine = unit_results[0][1]
            deepl_error = next((r[2] for r in unit_results if r[2]), None)

            if not translated.strip():
                continue

            # Lấy mẫu màu nền ngay quanh khối NÀY trước khi xóa nó (trang
            # vẫn còn nguyên vẹn — việc xóa chỉ thật sự có hiệu lực lúc gọi
            # apply_redactions() bên dưới), để màu tô khớp với đúng thứ
            # thật sự nằm phía sau thay vì đoán 1 màu chung cho cả trang.
            bg_color = local_bg_color(page, erase_rect)

            # Xếp hàng 1 lệnh xóa (redaction) thật (không chỉ vẽ đè hình
            # chữ nhật) để chữ cũ thực sự bị xóa khỏi tầng chữ của trang,
            # chứ không chỉ bị che dưới chữ mới. Xóa theo erase_rect (có
            # thể rộng hơn rect) nhưng VẼ chữ dịch theo rect gốc — nếu vẽ
            # theo erase_rect, chữ dịch sẽ tràn sang ô/cột bên cạnh.
            page.add_redact_annot(erase_rect, fill=bg_color)
            pending.append((rect, translated, base_font_size, text_color, style, max_y1))

            emit(type="progress", page=page_index + 1, total=total, engine=engine, detail=deepl_error)

        if pending:
            # Trang số hóa bình thường: images=NONE / graphics=0, việc xóa
            # chỉ bao giờ xóa chữ, lớp hình ảnh và vector không bao giờ bị
            # đụng tới. Trang scan (đã OCR ở trên) thì khác hẳn: TOÀN BỘ
            # trang chính là 1 tấm ảnh, "chữ" chỉ là pixel bên trong nó —
            # phải cho phép xóa đúng vùng pixel nhỏ đó (PDF_REDACT_IMAGE_
            # PIXELS chỉ xóa trắng phần bị khoanh vùng, không đụng phần
            # còn lại của ảnh) thì mới thay được chữ dịch vào, nếu không
            # chữ scan gốc sẽ vẫn hiện xuyên qua bên dưới chữ mới.
            images_mode = (
                fitz.PDF_REDACT_IMAGE_PIXELS if page_index in ocred_pages
                else fitz.PDF_REDACT_IMAGE_NONE
            )
            page.apply_redactions(images=images_mode, graphics=0)
            for rect, translated, base_font_size, text_color, style, max_y1 in pending:
                fit_and_draw(page, rect, translated, base_font_size,
                             color=text_color, style=style, max_y1=max_y1)

    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()


def main():
    # Import cục bộ (không phải ở đầu file): office_translate.py tự import
    # ngược `emit` từ chính module này — import ở đầu file sẽ tạo vòng lặp
    # import (translator_engine chưa định nghĩa xong `emit` khi
    # office_translate cố đọc nó). Trì hoãn tới lúc main() thật sự chạy thì
    # translator_engine đã load xong hoàn toàn, không còn vòng lặp.
    from office_translate import translate_docx, translate_pptx

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    input_path = config["input_pdf"]
    output_path = config["output_pdf"]
    max_pages = config.get("max_pages", 0)
    ext = os.path.splitext(input_path)[1].lower()

    router = TranslationRouter(
        deepl_key=config.get("deepl_key", ""),
        gemini_key=config.get("gemini_key", ""),
        deepl_limit=config.get("deepl_limit", 500_000),
    )
    router.refresh_usage()

    try:
        if ext == ".pdf":
            process_pdf(input_path, output_path, router, max_pages=max_pages)
        elif ext == ".docx":
            translate_docx(input_path, output_path, router, max_pages=max_pages)
        elif ext == ".pptx":
            translate_pptx(input_path, output_path, router, max_pages=max_pages)
        else:
            raise ValueError(f"Định dạng không được hỗ trợ: {ext}")
    except Exception as exc:
        emit(type="error", message=str(exc))
        sys.exit(1)

    emit(type="done", output=output_path)


if __name__ == "__main__":
    main()
