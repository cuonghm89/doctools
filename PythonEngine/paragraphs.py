"""Gộp các khối chữ PyMuPDF thành đoạn văn trước khi dịch.

Nhiều PDF xuất từ design tool (Figma, Canva, Webflow) đặt mỗi DÒNG hiển thị
thành 1 khối chữ riêng ở tầng PDF, thay vì 1 khối cho cả đoạn văn. Dịch và
vẽ lại từng dòng riêng lẻ sẽ tạo ra 1 chồng ô nhỏ, thường chồng lên nhau,
thay vì 1 đoạn văn gọn gàng. Module này gộp lại các khối cùng cột, cùng cỡ,
nằm sát nhau thành đoạn văn trước khi engine dịch/vẽ lại chúng.
"""
import re

BULLET_PREFIXES = ("•", "-", "*", "‣", "◦", "·")
_ORDERED_LIST_RE = re.compile(r"^\d+[.)]\s")


def is_bullet_text(text):
    stripped = text.strip()
    return stripped.startswith(BULLET_PREFIXES) or bool(_ORDERED_LIST_RE.match(stripped))


def block_font_style(block):
    """(bold, italic) từ span đầu tiên của khối. Cờ flags của PyMuPDF: bit
    1 (2) = nghiêng, bit 4 (16) = đậm; tên font cũng được kiểm tra thêm vì
    không phải PDF nào cũng đặt đúng các cờ này."""
    for line in block["lines"]:
        for span in line["spans"]:
            flags = span.get("flags", 0)
            font_name = span.get("font", "").lower()
            bold = bool(flags & 16) or "bold" in font_name
            italic = bool(flags & 2) or "italic" in font_name or "oblique" in font_name
            return bold, italic
    return False, False


def _avg_font_size(block):
    sizes = [span["size"] for line in block["lines"] for span in line["spans"]]
    return sum(sizes) / len(sizes) if sizes else 11.0


def block_text_color(block):
    """Màu của span đầu tiên, dạng tuple (r, g, b) số nguyên 0-255."""
    for line in block["lines"]:
        for span in line["spans"]:
            c = span.get("color")
            if c is not None:
                return (c >> 16) & 255, (c >> 8) & 255, c & 255
    return (0, 0, 0)


def _marker_prefix_content_x0(block):
    """1 số PDF (thường xuất từ Word/PowerPoint) vẽ bullet bằng font biểu
    tượng (Wingdings-kiểu) NHƯNG mã hoá ToUnicode của ký tự đó lại là dấu
    CÁCH — get_text() trả về 1 span '  ' (trắng, không có ký tự bullet
    thật nào), nằm ở font KHÁC hẳn nội dung thật theo sau. `is_bullet_text`
    không nhận ra được (không có ký tự bullet thật trong chuỗi), và tệ hơn:
    x0 của CẢ BLOCK bị kéo lệch về đúng vị trí span-trắng đó thay vì vị trí
    chữ thật — mọi bullet trong cùng 1 danh sách đều bị lệch x0 y hệt nhau
    theo kiểu này, khiến `merge_paragraph_blocks` tưởng chúng "cùng cột"
    và gộp nhầm các mục danh sách RIÊNG BIỆT thành 1 đoạn văn.

    Nếu block bắt đầu bằng 1 span trắng như vậy, trả về x0 của span chữ
    THẬT đầu tiên theo sau (nếu font khác span trắng) — đây mới là vị trí
    "nội dung" đáng tin để so cột. Không phải kiểu này thì trả về None."""
    lines = block.get("lines") or []
    if not lines:
        return None
    spans = lines[0].get("spans") or []
    if not spans or spans[0]["text"].strip():
        return None
    marker_font = spans[0].get("font")
    for span in spans[1:]:
        if span["text"].strip():
            return span["bbox"][0] if span.get("font") != marker_font else None
    return None


def split_incoherent_block(block, x_tolerance=3, gap_factor=1.3):
    """PyMuPDF đôi khi tự gộp các dòng chữ RỜI RẠC, không liên quan (thường
    gặp với chữ OCR chèn vào sơ đồ: các nhãn như "Publisher"/"Broker"/
    "Subscriber" nằm rải rác quanh 1 hình vẽ, không hề nối tiếp nhau, nhưng
    vô tình gần nhau theo chiều dọc, hoặc thẳng cùng 1 cột dù cách xa nhau
    — vd 2 nhãn cạnh sơ đồ "Applications"/"Communications" thẳng x0 nhưng
    cách nhau hàng chục điểm) thành 1 "block" DUY NHẤT ngay ở tầng
    get_text("dict") — trước khi merge_paragraph_blocks() ở dưới kịp áp
    dụng ngưỡng an toàn nào (nó chỉ so sánh GIỮA các block, không soi vào
    bên trong 1 block). Nếu không tách lại, bbox của "block" này là hợp
    của các dòng cách xa nhau hàng trăm điểm — kéo theo vùng xóa nền khổng
    lồ đè lên mọi thứ nằm giữa chúng khi vẽ lại.

    Tách 1 block gốc thành nhiều block con bất cứ khi nào dòng kế tiếp có
    x0 lệch quá x_tolerance SO VỚI dòng ngay trước, HOẶC khoảng cách dọc
    giữa 2 dòng vượt quá gap_factor lần chiều cao dòng trước — dùng đúng
    2 ngưỡng "cùng cột"/"đủ gần" mà merge_paragraph_blocks() đã dùng giữa
    các block, chỉ áp dụng thêm 1 tầng sâu hơn (giữa các dòng trong cùng 1
    block)."""
    lines = block.get("lines", [])
    if len(lines) <= 1:
        return [block]
    sub_groups = [[lines[0]]]
    for line in lines[1:]:
        prev_line = sub_groups[-1][-1]
        prev_x0 = prev_line["bbox"][0]
        prev_height = max(prev_line["bbox"][3] - prev_line["bbox"][1], 1)
        vertical_gap = line["bbox"][1] - prev_line["bbox"][3]
        same_column = abs(line["bbox"][0] - prev_x0) <= x_tolerance
        close_enough = vertical_gap <= prev_height * gap_factor
        if same_column and close_enough:
            sub_groups[-1].append(line)
        else:
            sub_groups.append([line])
    if len(sub_groups) == 1:
        return [block]
    sub_blocks = []
    for group_lines in sub_groups:
        xs0 = [l["bbox"][0] for l in group_lines]
        ys0 = [l["bbox"][1] for l in group_lines]
        xs1 = [l["bbox"][2] for l in group_lines]
        ys1 = [l["bbox"][3] for l in group_lines]
        sub_blocks.append({
            "type": 0,
            "bbox": (min(xs0), min(ys0), max(xs1), max(ys1)),
            "lines": group_lines,
        })
    return sub_blocks


def merge_paragraph_blocks(blocks, x_tolerance=3, gap_factor=1.3, size_tolerance=0.25):
    """Gộp các khối chữ xếp chồng theo chiều dọc, canh trái, cùng cỡ chữ,
    nằm trong cùng 1 cột thành đoạn văn. Trả về 1 list các nhóm, mỗi nhóm:
    {"sub_blocks": [...], "bbox": (x0, y0, x1, y1), "size": cỡ_chữ_TB}.

    Việc canh theo x0 là thứ giữ cho các cột nằm cạnh nhau không bị gộp lẫn
    (cột khác nhau có x0 khác nhau), và giới hạn khoảng cách dọc giữ cho các
    đoạn văn thực sự tách biệt trong cùng 1 cột không bị gộp thành 1 khối
    khổng lồ. Đậm/nghiêng và màu sắc cũng phải khớp: 1 chuỗi nhãn cùng cỡ,
    cùng độ đậm nhưng phân biệt nhau bằng màu nhấn (số chương, thẻ phân
    loại) vốn được thiết kế để là các mục riêng biệt, không phải 1 đoạn văn
    — gộp chúng lại sẽ bọc thành 1 khung kết hợp bị trôi lệch khỏi bất cứ
    thứ gì nằm cạnh mỗi mục.

    Cả 2 ngưỡng đều cố tình đặt chặt: văn bản word-wrap nằm trên các dòng
    liên tiếp với độ giãn dòng tự nhiên (nhỏ) của font và lề trái giống hệt
    nhau từng pixel, trong khi PDF dạng sơ đồ/infographic (lưới icon, thẻ
    danh sách tính năng) đầy rẫy các nhãn ngắn, không liên quan nhau nhưng
    vô tình rơi vào cùng 1 x0 (chú thích canh giữa) hoặc có khoảng đệm rộng
    giữa các dòng (khoảng cách cố ý giữa các mục danh sách). Ngưỡng lỏng
    hơn bắt đúng đoạn văn thật nhưng cũng dán dính các nhãn sơ đồ không
    liên quan lại với nhau — ví dụ "Core Banking" + "CRM" dưới 2 icon khác
    nhau, hoặc "Data Visualization" + "Intelligent Insight" là 2 dòng tính
    năng riêng biệt — tạo ra chữ gộp lộn xộn trong 1 khung đặt sai chỗ.
    ponytail: chỉ dùng heuristic canh trái — chữ canh phải hoặc canh giữa
    sẽ không có x0 ổn định, nên các cột đó sẽ không bị gộp (vẫn giữ nguyên
    từng khối riêng lẻ, giống như trước khi có thay đổi này).

    Khối bắt đầu bằng marker-trắng (`_marker_prefix_content_x0` — bullet
    hoán đổi glyph, xem docstring hàm đó) LUÔN mở 1 nhóm MỚI, không bao
    giờ gộp vào nhóm đang có — bản thân nó chính là điểm bắt đầu 1 mục
    danh sách mới, dù x0/style/color RAW của nó (bị marker kéo lệch) có
    tình cờ khớp nhóm trước hay không. Việc so cột (`same_column`) cho MỌI
    khối khác dùng `content_x0` (bỏ qua phần marker nếu có) thay vì x0 thô
    của block — nhờ vậy dòng word-wrap tiếp theo của bullet đó (căn theo
    lề chữ thật, không phải theo marker) vẫn gộp đúng vào cùng nhóm.
    """
    text_blocks = [b for b in blocks if b.get("type") == 0 and b.get("lines")]
    text_blocks = [sub for b in text_blocks for sub in split_incoherent_block(b, x_tolerance)]
    text_blocks.sort(key=lambda b: round(b["bbox"][1], 1))

    groups = []
    for block in text_blocks:
        bbox = block["bbox"]
        marker_x0 = _marker_prefix_content_x0(block)
        content_x0 = bbox[0] if marker_x0 is None else marker_x0
        size = _avg_font_size(block)
        style = block_font_style(block)
        color = block_text_color(block)
        placed = False
        if marker_x0 is None:
            for group in groups:
                gx0, gy0, gx1, gy1 = group["bbox"]
                same_column = abs(content_x0 - group["anchor_x0"]) <= x_tolerance
                similar_size = abs(size - group["size"]) <= group["size"] * size_tolerance
                same_style = style == group["style"]
                same_color = color == group["color"]
                vertical_gap = bbox[1] - gy1
                close_enough = 0 <= vertical_gap <= size * gap_factor
                if same_column and similar_size and same_style and same_color and close_enough:
                    group["sub_blocks"].append(block)
                    group["bbox"] = (
                        min(gx0, bbox[0]), min(gy0, bbox[1]),
                        max(gx1, bbox[2]), max(gy1, bbox[3]),
                    )
                    placed = True
                    break
        if not placed:
            groups.append({
                "sub_blocks": [block], "bbox": bbox, "size": size,
                "style": style, "color": color, "anchor_x0": content_x0,
            })

    return groups
