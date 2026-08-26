"""Self-check các hàm xử lý bảng thật trong translator_engine.py — chạy
trực tiếp: python3 test_translator_engine.py"""
import fitz
from translator_engine import (
    _block_mostly_in_region, _dedup_cell_jobs, _merge_wrapped_table_rows,
    build_table_cell_units,
)


# --- _dedup_cell_jobs: bỏ ô nhỏ trùng lặp NẰM TRỌN trong ô lớn, cùng text ---
big = fitz.Rect(72, 412, 258, 438)
small_dup = fitz.Rect(78, 412, 253, 425)  # nằm trọn trong `big`, cùng text
jobs = [(big, "AIS (Application & Interface Security)"),
        (small_dup, "AIS (Application & Interface Security)")]
result = _dedup_cell_jobs(jobs)
assert len(result) == 1, f"phải bỏ ô trùng lặp, got {len(result)}"
assert result[0][0] == big, "phải giữ ô LỚN hơn (khớp khung hiển thị thật)"

# 2 ô cùng text nhưng KHÔNG chồng lấn nhau -> giữ cả 2 (không phải trùng lặp).
far_apart = [(big, "Same label"), (fitz.Rect(72, 500, 258, 526), "Same label")]
assert len(_dedup_cell_jobs(far_apart)) == 2

print("Tất cả self-check khử-ô-trùng-lặp đều pass.")


# --- _block_mostly_in_region ---
region = fitz.Rect(70, 60, 540, 330)
inside_bbox = (80, 100, 500, 120)  # nằm trọn trong region
outside_bbox = (80, 700, 500, 720)  # ngoài region hẳn
straddling_bbox = (80, 300, 500, 400)  # 1 phần trong, phần lớn ngoài
assert _block_mostly_in_region(inside_bbox, region)
assert not _block_mostly_in_region(outside_bbox, region)
assert not _block_mostly_in_region(straddling_bbox, region)

print("Tất cả self-check block-nằm-trong-vùng-bảng đều pass.")


# --- Table/Row/Page giả (duck-typing, không cần PyMuPDF Table thật) ---
class _FakeRow:
    def __init__(self, cells):
        self.cells = cells


class _FakeTable:
    def __init__(self, bbox, rows_cells, rows_text):
        self.bbox = bbox
        self.rows = [_FakeRow(cells) for cells in rows_cells]
        self._rows_text = rows_text

    def extract(self):
        return self._rows_text


class _FakePage:
    """`get_drawings()` giả: mỗi item là 1 dải màu nền (rect tô màu) —
    dùng để test _merge_wrapped_table_rows() mà không cần PDF thật."""
    def __init__(self, fill_rects):
        self._fill_rects = fill_rects

    def get_drawings(self):
        return [{"fill": color, "rect": rect} for rect, color in self._fill_rects]


table = _FakeTable(
    bbox=(72, 63, 500, 120),
    rows_cells=[
        [(72, 63, 200, 80), (200, 63, 500, 80)],
        [(72, 80, 200, 100), None],  # cell 1 None (bị ô khác merge chiếm) -> bỏ qua
        [(72, 100, 200, 120), (200, 100, 500, 120)],
    ],
    rows_text=[
        ["Domain", "Description"],
        ["AIS", None],
        ["AAC", ""],  # ô rỗng ("") -> bỏ qua
    ],
)
jobs = build_table_cell_units(_FakePage([]), [table])
assert len(jobs) == 4, f"phải có 4 job (bỏ ô None-bbox và ô text rỗng), got {len(jobs)}"
texts = {t for _r, t in jobs}
assert texts == {"Domain", "Description", "AIS", "AAC"}

print("Tất cả self-check build-table-cell-units đều pass.")


# --- _merge_wrapped_table_rows: gộp 2 "row" find_tables() tách nhầm do
# word-wrap, dựa trên cùng 1 dải màu nền thật (khác row kế tiếp — đổi màu). ---
LIGHT = (0.851, 0.886, 0.953)
DARK = (0.557, 0.667, 0.859)

# Hàng 1 (LIGHT, y 412-438): bị find_tables() tách thành 2 "row" (412-425,
# 425-438) — case thật từ file PDF user: "DCS (Data Security & Information"
# + "Lifecycle Management)" phải gộp lại thành 1 câu.
# Hàng 2 (DARK, y 438-463): 1 "row" bình thường, không bị tách.
wrap_table = _FakeTable(
    bbox=(72, 412, 528, 463),
    rows_cells=[
        [(72, 412, 258, 425), (263, 412, 528, 425)],
        [(72, 425, 258, 438), (263, 425, 528, 438)],
        [(72, 438, 258, 463), (263, 438, 528, 463)],
    ],
    rows_text=[
        ["DCS (Data Security & Information", "Ensures secure data handling, encryption"],
        ["Lifecycle Management)", "management."],
        ["ECS (Encryption & Key Management)", "Defines standards for encryption."],
    ],
)
wrap_page = _FakePage([
    (fitz.Rect(72, 412, 528, 438), LIGHT),   # 1 dải LIGHT phủ trọn cả 2 "row" bị tách
    (fitz.Rect(72, 438, 528, 463), DARK),    # dải DARK riêng cho hàng thứ 2 (không gộp)
])

merged_rows = _merge_wrapped_table_rows(wrap_page, wrap_table)
assert len(merged_rows) == 2, f"phải còn 2 hàng LOGIC (gộp 2 row đầu), got {len(merged_rows)}"

cells0, texts0 = merged_rows[0]
assert texts0[0] == "DCS (Data Security & Information Lifecycle Management)", texts0[0]
assert texts0[1] == "Ensures secure data handling, encryption management."
assert cells0[0] == fitz.Rect(72, 412, 258, 438), "bbox phải hợp cả 2 row con"

cells1, texts1 = merged_rows[1]
assert texts1[0] == "ECS (Encryption & Key Management)"

# Bảng KHÔNG có dải màu nền nào (get_drawings() rỗng) -> không gộp gì cả,
# an toàn hơn đoán bừa (giữ nguyên hành vi cũ).
no_band_rows = _merge_wrapped_table_rows(_FakePage([]), wrap_table)
assert len(no_band_rows) == 3, "không có tín hiệu màu nền thì KHÔNG được gộp"

print("Tất cả self-check gộp-hàng-bị-word-wrap đều pass.")


# --- _normalize_column_x0: case THẬT gặp trên file PDF user — nhãn
# "DCS..." (hàng bị find_tables() tách do word-wrap) rơi vào CHỈ SỐ CỘT
# KHÁC (index 1, lưới 6 cột kể cả ô đệm rỗng) so với "AAC"/"ECS" (hàng
# bình thường, index 0, lưới chỉ 2 cột) — dù cùng 1 cột hiển thị. Canh
# theo index (bản cũ) KHÔNG sửa được case này; phải canh theo CỤM x0. ---
x0_table = _FakeTable(
    bbox=(72, 412, 528, 488),
    rows_cells=[
        [(72.3, 412, 258.2, 438), None, None, (263.6, 412, 528.1, 438), None, None],
        [(72.3, 438, 77.7, 451), (77.7, 438, 252.8, 451), (252.8, 438, 258.2, 451),
         (258.2, 438, 263.6, 451), (263.6, 438, 528.1, 451), None],
        [None, (77.7, 451, 252.8, 464), None, None, (263.6, 451, 528.1, 464), None],
        [(72.3, 464, 258.2, 488), None, None, (263.6, 464, 528.1, 488), None, None],
    ],
    rows_text=[
        ["AAC (Audit)", None, None, "Ensures compliance.", None, None],
        ["", "DCS (Data Security", "", "", "Ensures secure data", None],
        [None, "Lifecycle Management)", None, None, "handling.", None],
        ["ECS (Encryption)", None, None, "Defines standards.", None, None],
    ],
)
x0_page = _FakePage([
    (fitz.Rect(72, 412, 528, 438), DARK),
    (fitz.Rect(72, 438, 528, 464), LIGHT),  # 1 dải phủ cả 2 hàng bị tách
    (fitz.Rect(72, 464, 528, 488), DARK),
])
x0_jobs = build_table_cell_units(x0_page, [x0_table])
label_x0s = {round(rect.x0, 1) for rect, text in x0_jobs if "AAC" in text or "DCS" in text or "ECS" in text}
assert label_x0s == {72.3}, f"mọi nhãn cột 'Miền' phải cùng 1 x0 sau khi canh lại, got {label_x0s}"
desc_x0s = {round(rect.x0, 1) for rect, text in x0_jobs
            if "Ensures" in text or "handling" in text or "Defines" in text}
assert desc_x0s == {263.6}, f"cột 'Mô tả' không được bị ảnh hưởng, got {desc_x0s}"

print("Tất cả self-check canh-lại-x0-theo-cột đều pass.")
