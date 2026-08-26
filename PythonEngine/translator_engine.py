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
        blocks = page.get_text("dict")["blocks"]
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
