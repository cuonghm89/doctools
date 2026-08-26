"""OCR cho trang PDF dạng scan (không có lớp chữ), dùng Vision framework của
macOS qua CLI Swift riêng (ocr_cli).

Thiết kế: thay vì nhồi logic OCR vào translator_engine.py / pdf_convert.py,
module này chèn thẳng 1 lớp CHỮ VÔ HÌNH (render_mode=3 — không tô, không
viền, không thấy được khi mở PDF) vào đúng vị trí chữ trên ảnh scan, TRƯỚC
khi đưa tài liệu vào các pipeline đã có. Nhờ vậy `page.get_text("dict")` ở
mọi nơi khác trong dự án tự động "nhìn thấy" chữ đã OCR y hệt như chữ có
sẵn trong PDF số hóa — không cần sửa gì thêm ở translator_engine.py hay
pdf_convert.py, cả 2 chỗ đó vẫn hoạt động y nguyên logic đã test kỹ.

Đây đúng là kỹ thuật các công cụ OCR PDF chuyên nghiệp dùng để tạo
"searchable PDF": ảnh scan gốc giữ nguyên 100% pixel, chỉ thêm 1 lớp text
ẩn bên dưới để tìm kiếm/copy/dịch được.
"""
import json
import os
import subprocess
import tempfile

import fitz  # PyMuPDF
import numpy as np

VN_FONT_FILE = "/System/Library/Fonts/Supplemental/Arial.ttf"
VN_FONT_NAME = "ocrfont"

def _default_ocr_binary():
    """Ưu tiên `ocr_cli` đóng gói cạnh app (`Contents/MacOS/ocr_cli`, xem
    scripts/package_app.sh — PythonEngine nằm ở `Contents/Resources/
    PythonEngine` khi đã đóng gói); nếu không có (chạy dev) thì suy ra
    project root từ vị trí file này và tìm `.build/debug/ocr_cli` (cần
    `swift build` chạy ít nhất 1 lần)."""
    engine_dir = os.path.dirname(os.path.abspath(__file__))
    contents_dir = os.path.dirname(os.path.dirname(engine_dir))
    bundled = os.path.join(contents_dir, "MacOS", "ocr_cli")
    if os.path.exists(bundled):
        return bundled
    project_root = os.path.dirname(engine_dir)
    return os.path.join(project_root, ".build", "debug", "ocr_cli")


DEFAULT_OCR_BINARY = _default_ocr_binary()

# Nếu tổng số ký tự chữ thật trích được từ trang ít hơn ngưỡng này, coi
# trang đó là "dạng scan" (không có lớp chữ đáng kể) và chạy OCR. Không
# dùng ngưỡng = 0 tuyệt đối vì 1 số trang số hóa có thể có 1-2 ký tự lạc
# (số trang, watermark) mà vẫn cần OCR phần nội dung chính là ảnh.
MIN_REAL_TEXT_CHARS = 20

# Cỡ chữ dự phòng ước lượng từ chiều cao bbox do Vision trả về — bbox này
# bao gồm cả phần đệm trên/dưới dòng chữ (line leading), nên nhân với hệ
# số nhỏ hơn 1 để không thổi phồng font_size quá mức so với cỡ chữ thật.
# Chỉ dùng khi KHÔNG thể ước lượng theo chiều rộng (xem _estimate_font_size).
BBOX_TO_FONT_RATIO = 0.75

# Ngưỡng tỉ lệ cao/rộng để nghi ngờ 1 dòng là nhãn XOAY DỌC thay vì chữ
# ngang bình thường — chữ ngang bình thường (kể cả từ rất ngắn) hiếm khi
# cao gấp đôi bề rộng của chính nó, nên ngưỡng này ít khi bắt nhầm.
ROTATED_ASPECT_RATIO = 2.0

_VN_FONT = fitz.Font(fontfile=VN_FONT_FILE)


def _estimate_font_size(text, width, height):
    """Ước lượng cỡ chữ từ CHIỀU RỘNG thay vì chiều cao khi có thể — chính
    xác hơn hẳn: ta biết chính xác NỘI DUNG chữ (text) và bề rộng thật của
    nó trong ảnh (width, do Vision đo), nên có thể giải ngược ra cỡ chữ mà
    Arial cần dùng để khớp đúng bề rộng đó — trong khi chiều cao bbox của
    Vision còn lẫn cả khoảng đệm dòng (line leading), vốn co giãn thất
    thường và không tỷ lệ thuận đơn giản với cỡ chữ thật.
    Chỉ tin chiều rộng khi có đủ ký tự để phép đo ổn định (chữ quá ngắn,
    vd 1 dấu chấm hoặc 1 số, sai số đo bề rộng quá lớn so với cỡ chữ) và
    khi nó không quá lệch so với ước lượng chiều cao (đề phòng trường hợp
    font gốc trong ảnh khác biệt quá xa so với Arial về độ rộng ký tự)."""
    height_estimate = max(height * BBOX_TO_FONT_RATIO, 4.0)
    stripped = text.strip()
    if len(stripped) < 2:
        return height_estimate
    unit_width = _VN_FONT.text_length(stripped, fontsize=1.0)
    if unit_width <= 0:
        return height_estimate
    width_estimate = width / unit_width
    if 0.5 * height_estimate <= width_estimate <= 1.8 * height_estimate:
        return max(width_estimate, 4.0)
    return height_estimate


def _page_has_real_text(page):
    total_chars = sum(
        len("".join(span["text"] for line in b["lines"] for span in line["spans"]))
        for b in page.get_text("dict")["blocks"] if b.get("type") == 0
    )
    return total_chars >= MIN_REAL_TEXT_CHARS


def _detect_ink_color(page, rect):
    """Đoán màu chữ THẬT trong ảnh scan bằng cách lấy mẫu pixel trong đúng
    vùng bbox của dòng chữ đó. Vision chỉ trả về vị trí/nội dung chữ, không
    trả về màu — nếu không tự dò, chữ vẽ lại sẽ luôn phải hardcode 1 màu cố
    định (vd: đen), sai hoàn toàn với chữ trắng trên nền tối (thường gặp ở
    slide tiêu đề).

    Trả về tuple (r, g, b) số nguyên 0-255 — cùng thang với
    paragraphs.block_text_color(), vì đây là màu CHỮ đưa thẳng vào CSS
    "rgb(...)" khi vẽ lại (khác local_bg_color() ở trên, trả về float 0-1
    vì đó là màu NỀN đưa cho add_redact_annot(fill=...)).

    KHÔNG dùng "màu xuất hiện nhiều nhất = nền" (cách làm tưởng hợp lý
    nhưng SAI với nền dạng GRADIENT, rất phổ biến trong các icon/hộp sơ đồ
    ở đây, vd hộp "Broker"/"Subscriber"): nền tô bóng đổi màu liên tục theo
    từng pixel nên gần như KHÔNG có 1 giá trị màu nền nào lặp lại đủ nhiều,
    trong khi chữ đậm tô 1 màu ĐẶC lại lặp lại chính xác rất nhiều lần —
    kết quả "màu phổ biến nhất" ngược đời lại chính là màu CHỮ, không phải
    nền, khiến thuật toán cũ chọn nhầm màu nền làm màu chữ.

    Thay vào đó, tách 2 nhóm màu bằng 2-means (K=2) trên khoảng cách màu
    thực (không phải tần suất xuất hiện tuyệt đối) rồi coi nhóm CHIẾM ÍT
    PIXEL HƠN là chữ — cách này không quan tâm màu nền có lặp lại y hệt hay
    không (gradient hay phẳng đều xử lý được như nhau), chỉ dựa vào diện
    tích: chữ luôn chiếm ít diện tích hơn nền trong 1 bbox ôm khít dòng chữ
    bình thường."""
    clip = rect & page.rect
    pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2))
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(-1, pix.n)[:, :3].astype(np.float64)
    if len(arr) == 0:
        return (0, 0, 0)
    luminance = arr @ np.array([0.299, 0.587, 0.114])
    c0, c1 = arr[np.argmin(luminance)].copy(), arr[np.argmax(luminance)].copy()
    mask = np.zeros(len(arr), dtype=bool)
    for _ in range(8):
        d0 = np.sum((arr - c0) ** 2, axis=1)
        d1 = np.sum((arr - c1) ** 2, axis=1)
        new_mask = d0 <= d1
        if not new_mask.any() or new_mask.all():
            mask = new_mask
            break
        new_c0 = arr[new_mask].mean(axis=0)
        new_c1 = arr[~new_mask].mean(axis=0)
        converged = np.allclose(new_c0, c0, atol=0.5) and np.allclose(new_c1, c1, atol=0.5)
        c0, c1, mask = new_c0, new_c1, new_mask
        if converged:
            break
    ink = c0 if mask.sum() <= (~mask).sum() else c1
    return tuple(int(round(v)) for v in ink)


def _run_ocr(image_path, ocr_binary):
    proc = subprocess.run(
        [ocr_binary, image_path],
        capture_output=True, text=True, timeout=60,
    )
    data = json.loads(proc.stdout)
    if "error" in data:
        raise RuntimeError(f"OCR lỗi: {data['error']}")
    return data.get("lines", [])


def _ocr_pixmap(pix, ocr_binary):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pix.save(tmp_path)
        return _run_ocr(tmp_path, ocr_binary)
    finally:
        os.unlink(tmp_path)


# Ngưỡng cho _detect_vertical_grid_lines(): 1 đường kẻ lưới thật phải tối
# TUYỆT ĐỐI (không chỉ tối hơn 2 bên — 1 cột toàn nền trắng đi ngang qua 1
# đoạn chữ thưa vẫn có thể "tối hơn 2 bên" 1 chút dù chẳng phải đường kẻ
# nào) VÀ tối hơn HẲN 2 bên (loại nhiễu/1 nét chữ tình cờ đi qua). Đã kiểm
# chứng trên dữ liệu thật: đường kẻ lưới thật có mean rất thấp (~26-140,
# tối hẳn), trong khi cột nền trắng có/không có chữ đi qua luôn có mean
# cao (~200+) dù độ lệch chuẩn (std) của nó dao động thất thường — std
# KHÔNG phải tín hiệu đáng tin, mean tuyệt đối mới là tín hiệu chính.
GRID_LINE_WINDOW_PX = 15
GRID_LINE_MIN_CONTRAST = 20.0
GRID_LINE_MAX_MEAN = 150.0
GRID_LINE_MERGE_DIST_PX = 3
GRID_LINE_EDGE_MARGIN_PX = 30


def _detect_vertical_grid_lines(pix):
    """Dò các đường kẻ dọc THẬT (viền cột trong bảng) trong ảnh cả trang,
    để cắt riêng từng dải cột trước khi OCR — nếu OCR cả trang 1 lần,
    Vision có thể gộp nhầm chữ ở 2 CỘT liền kề thành 1 "dòng" duy nhất khi
    khoảng trống giữa chúng không đủ rõ (gặp thật: 1 ô "Advantages" có chữ
    dài gần chạm cột "Remarks" bên cạnh — không có khoảng trắng nào để
    phát hiện, phải dò đúng đường kẻ lưới thay vì dò khoảng cách chữ).

    Cắt theo đúng đường kẻ lưới loại bỏ hẳn nguy cơ này TẬN GỐC: Vision
    không bao giờ nhìn thấy 2 cột cùng lúc trong 1 lần OCR nữa.

    Trả về list vị trí x (pixel, đã sort, không gồm 2 viền ngoài cùng của
    trang — không có gì để tách ở đó) — rỗng nếu trang không phải dạng
    bảng có viền rõ (xử lý như cũ, OCR nguyên trang 1 lần)."""
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    gray = arr.mean(axis=2).astype(np.float64)  # độ sáng từng pixel
    col_mean = gray.mean(axis=0)
    w = len(col_mean)

    candidates = []
    for x in range(GRID_LINE_WINDOW_PX, w - GRID_LINE_WINDOW_PX):
        local = col_mean[x - GRID_LINE_WINDOW_PX:x + GRID_LINE_WINDOW_PX + 1]
        neighbor_avg = (local.sum() - col_mean[x]) / (len(local) - 1)
        if (
            col_mean[x] == local.min()
            and col_mean[x] < GRID_LINE_MAX_MEAN
            and neighbor_avg - col_mean[x] > GRID_LINE_MIN_CONTRAST
        ):
            candidates.append(x)

    lines = []
    for x in candidates:
        if lines and x - lines[-1] <= GRID_LINE_MERGE_DIST_PX:
            continue
        lines.append(x)

    return [x for x in lines if GRID_LINE_EDGE_MARGIN_PX < x < w - GRID_LINE_EDGE_MARGIN_PX]


def ocr_page(page, ocr_binary, dpi_scale=2.0, visible=False):
    """OCR 1 trang (dạng scan) và chèn lớp chữ đúng vị trí. Mặc định chèn
    CHỮ VÔ HÌNH (render_mode=3) — dùng cho searchable PDF, không đổi giao
    diện trang khi mở ra xem. `visible=True` chèn chữ hiện bình thường —
    chỉ dùng cho file PDF TRUNG GIAN, dùng-rồi-bỏ (ví dụ file tạm đưa cho
    pdf2docx, xem convert_to_docx trong pdf_convert.py): pdf2docx tự lọc bỏ
    hoàn toàn chữ render_mode=3 khi trích xuất (đã kiểm chứng thực tế), nên
    với riêng đường dẫn đó bắt buộc phải dùng chữ hiện — người dùng cuối
    không bao giờ nhìn thấy file PDF trung gian này, chỉ nhận file .docx
    kết quả, nên việc chữ "hiện" ở đây không gây trùng lặp hình ảnh gì cả.

    Không làm gì nếu trang đã có lớp chữ thật (tránh OCR lại tài liệu số
    hóa, vừa tốn thời gian vừa có nguy cơ chèn chữ trùng lặp sai vị trí).
    Trả về None nếu bỏ qua vì đã có chữ thật; nếu trang này thực sự được
    OCR (trang scan), trả về danh sách (rect GỐC do Vision nhận diện, màu
    mực dò được) - có thể rỗng nếu trang trắng.

    Vì sao phải trả về rect gốc thay vì để nơi gọi tự đọc lại bbox qua
    get_text("dict") sau khi chèn: chữ vô hình chèn vào dùng font Arial +
    cỡ chữ ƯỚC LƯỢNG từ chiều cao box, khác hẳn font/kiểu chữ thật trong
    ảnh — nên bbox thực tế của chữ vừa chèn (đo lại qua get_text) gần như
    luôn HẸP HƠN vùng chữ thật trong ảnh scan. Nơi gọi (process_pdf) cần
    rect GỐC này để xóa (redact) đúng hết vùng pixel chứa chữ cũ, nếu
    không sẽ để sót rìa chữ gốc lộ ra sau khi vẽ chữ dịch đè lên."""
    if _page_has_real_text(page):
        return None

    page_w, page_h = page.rect.width, page.rect.height
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi_scale, dpi_scale))
    grid_lines_px = _detect_vertical_grid_lines(pix)

    if grid_lines_px:
        # Trang dạng bảng có viền rõ: cắt riêng từng dải cột theo đúng
        # đường kẻ lưới rồi OCR RIÊNG TỪNG DẢI (xem
        # _detect_vertical_grid_lines()) — thay vì OCR nguyên trang 1 lần,
        # nơi Vision có thể gộp nhầm 2 cột liền kề. Chỉ cắt theo TRỤC X:
        # dải vẫn giữ NGUYÊN chiều cao cả trang, nên y/height của mỗi dòng
        # OCR ra đã đúng thang page_h sẵn, không cần quy đổi lại — chỉ x/
        # width cần cộng offset + quy đổi theo bề rộng dải.
        boundaries_pt = [0.0] + [x / dpi_scale for x in grid_lines_px] + [page_w]
        lines = []
        for i in range(len(boundaries_pt) - 1):
            x0_pt, x1_pt = boundaries_pt[i], boundaries_pt[i + 1]
            strip_pix = page.get_pixmap(
                matrix=fitz.Matrix(dpi_scale, dpi_scale),
                clip=fitz.Rect(x0_pt, 0, x1_pt, page_h),
            )
            strip_w_pt = x1_pt - x0_pt
            for line in _ocr_pixmap(strip_pix, ocr_binary):
                lines.append({
                    "text": line["text"],
                    "x": (x0_pt + line["x"] * strip_w_pt) / page_w,
                    "y": line["y"],
                    "width": line["width"] * strip_w_pt / page_w,
                    "height": line["height"],
                })
    else:
        lines = _ocr_pixmap(pix, ocr_binary)

    original_rects = []
    for line in lines:
        text = line["text"].strip()
        if not text:
            continue
        x0 = line["x"] * page_w
        y0 = line["y"] * page_h
        width = line["width"] * page_w
        height = line["height"] * page_h

        rect = fitz.Rect(x0, y0, x0 + width, y0 + height)
        ink_color = _detect_ink_color(page, rect)
        original_rects.append((rect, ink_color))
        # render_mode=3: không tô màu, không viền -> vô hình khi hiển thị,
        # nhưng vẫn là văn bản thật trong content stream nên get_text() và
        # mọi công cụ tìm kiếm/copy đều đọc được bình thường. Với
        # visible=True (chỉ dùng cho file PDF trung gian đưa cho pdf2docx —
        # xem convert_to_docx trong pdf_convert.py), màu CHỮ THẬT cũng
        # phải đúng — nếu không, pdf2docx sẽ trích màu đen mặc định vào
        # Word bất kể ảnh gốc có chữ trắng/màu gì. insert_textbox() cần
        # color ở thang 0-1, khác thang 0-255 của ink_color.
        kwargs = dict(fontname=VN_FONT_NAME, fontfile=VN_FONT_FILE,
                       color=tuple(c / 255 for c in ink_color),
                       render_mode=0 if visible else 3)

        drawn = False
        if height > width * ROTATED_ASPECT_RATIO and len(text) >= 2:
            # Nhãn xoay dọc (thường gặp ở cạnh bên sơ đồ, vd "Applications"/
            # "Communications" trong mô hình OSI): Vision vẫn ĐỌC ĐÚNG toàn
            # bộ chữ, chỉ trả về bbox cao-hẹp đúng theo hướng thật trong
            # ảnh. Nếu chèn NGANG vào bbox này, PyMuPDF buộc phải xuống dòng
            # từng 2-3 ký tự một để vừa bề rộng quá hẹp, ra chữ vô nghĩa
            # ("Ap"/"pli"/"ca"/"tio"/"ns") dù nội dung nhận diện ĐÚNG ngay
            # từ đầu — lỗi nằm ở bước chèn, không phải ở OCR. Thử chèn DỌC
            # (rotate=90, đọc từ dưới lên — quy ước phổ biến cho nhãn cạnh
            # dọc trong biểu đồ/infographic) trước: hoán đổi rộng/cao khi
            # ước lượng cỡ chữ vì chiều "dài dòng chữ" bây giờ là height.
            rotated_size = _estimate_font_size(text, height, width)
            overflow = page.insert_textbox(rect, text, fontsize=rotated_size, rotate=90, **kwargs)
            while overflow < 0 and rotated_size > 3.0:
                rotated_size -= 0.5
                overflow = page.insert_textbox(rect, text, fontsize=rotated_size, rotate=90, **kwargs)
            drawn = overflow >= 0

        if not drawn:
            font_size = _estimate_font_size(text, width, height)
            # Chèn vào khung CAO HƠN rect gốc (không phải rect gốc trực
            # tiếp): chiều cao bbox Vision đo được luôn hụt so với
            # line-height thật mà Arial cần ở đúng font_size đã ước lượng
            # (thiếu phần đệm trên/dưới dòng chữ), nên nếu chèn thẳng vào
            # rect gốc, insert_textbox sẽ báo tràn NGAY CẢ KHI cỡ chữ ước
            # lượng hoàn toàn chính xác — kích hoạt vòng co nhỏ dần bên
            # dưới một cách KHÔNG CẦN THIẾT, và mức co lại khác nhau thất
            # thường giữa các dòng (tùy sai số đo bbox từng dòng), phá vỡ
            # tính nhất quán cỡ chữ giữa các dòng wrap của CÙNG 1 đoạn văn
            # — rect gốc (chật, đúng vùng mực) vẫn được giữ riêng ở trên
            # cho mục đích xóa nền/dò màu, không bị ảnh hưởng bởi việc nới
            # khung chèn này. Lấy max với rect.height gốc (không chỉ dựa
            # thuần vào font_size): 1 số dòng có rect gốc CAO SẴN (ví dụ
            # nhãn xoay dọc thử rotate=90 không vừa, rơi về đây) — nếu chỉ
            # dùng font_size*1.4, khung sẽ bị co lại NHỎ HƠN cả rect gốc mỗi
            # khi vòng lặp bên dưới giảm font_size, làm mất luôn khoảng
            # trống dọc vốn đã có sẵn, khiến chữ không bao giờ chèn vừa.
            insert_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + max(rect.height, font_size * 1.4))
            overflow = page.insert_textbox(insert_rect, text, fontsize=font_size, **kwargs)
            # insert_textbox không vẽ GÌ CẢ nếu chữ không vừa khung — vẫn
            # giữ vòng co nhỏ dần này làm lưới an toàn cuối cùng cho trường
            # hợp thật sự không vừa (dòng quá dài/nhiều ký tự đặc biệt), để
            # không bao giờ mất trắng 1 dòng OCR thay vì báo lỗi.
            while overflow < 0 and font_size > 3.0:
                font_size -= 0.5
                insert_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + max(rect.height, font_size * 1.4))
                overflow = page.insert_textbox(insert_rect, text, fontsize=font_size, **kwargs)
    return original_rects


def ensure_text_layer(doc, ocr_binary=DEFAULT_OCR_BINARY, visible=False):
    """Với mọi trang trong `doc` không có lớp chữ thật, OCR và chèn lớp
    chữ ngay tại chỗ (sửa trực tiếp `doc`, không trả về bản sao). Trả về
    dict {chỉ số trang đã OCR: [rect gốc do Vision nhận diện]} — dùng `in`
    để kiểm tra 1 trang có được OCR hay không y hệt cách dùng set trước
    đây, đồng thời cho nơi gọi (process_pdf) truy cập được rect gốc để
    redact đúng vùng pixel chữ cũ (xem docstring của ocr_page()).
    `visible`: xem docstring của ocr_page()."""
    ocred_pages = {}
    for i, page in enumerate(doc):
        rects = ocr_page(page, ocr_binary, visible=visible)
        if rects is not None:
            ocred_pages[i] = rects
    return ocred_pages
