"""Nhận diện đoạn mã nguồn (Python/C/Java/JS/...) trong tài liệu và tách
riêng phần CHÚ THÍCH (comment) để dịch — phần code còn lại giữ nguyên 100%,
không gửi qua DeepL/Gemini.

ponytail: heuristic dựa trên từ khoá/cú pháp phổ biến, không phải parser
thật cho từng ngôn ngữ — cùng tinh thần với is_table_lines() trong
router.py. Việc tách comment không nhận biết chuỗi ký tự (string literal)
qua nhiều dòng hay comment khối nhiều dòng (`/* ... */` tràn dòng) — 1 dòng
code chứa ký tự comment bên trong string ngắn gọn (vd `print("a # b")`) có
thể bị hiểu nhầm nếu số dấu nháy trước đó không cân; trường hợp phổ biến
(dấu nháy cân đối) đã được lọc qua _quote_parity_ok().
"""
import re

_CODE_KEYWORDS = (
    "def", "class", "elif", "func", "function", "public", "private",
    "protected", "static", "void", "int", "float", "double", "bool",
    "boolean", "var", "let", "const", "package", "namespace", "using",
    "struct", "enum", "switch", "case", "except", "finally", "lambda",
    "async", "await",
)
# `if`/`for`/`while`/`else`/`return`/`import`/... CỐ Ý không nằm trong danh
# sách trên: đây đều là từ tiếng Anh bình thường có thể mở đầu 1 câu văn
# xuôi thật ("If the value exceeds...", "For example...") — dùng riêng làm
# tín hiệu SẼ gây nhận nhầm câu văn thành code. Chỉ những từ khoá không thể
# là câu tiếng Anh tự nhiên mới được đưa vào đây, và ngay cả vậy vẫn cần 1
# tín hiệu khác đi kèm (xem _is_code_line) trừ khi có cú pháp/regex riêng.
_CODE_LINE_KEYWORD_RE = re.compile(r"^\s*(" + "|".join(_CODE_KEYWORDS) + r")\b")
_CODE_LINE_ENDING_RE = re.compile(r"[;{}]\s*$")
# Cú pháp chỉ xuất hiện trong code, gần như không bao giờ trong văn xuôi —
# 1 tín hiệu này là đủ, không cần thêm tín hiệu khác.
_CODE_LINE_SYNTAX_RE = re.compile(r"(=>|->|::|#include|<\?php)")
# `import x`, `import x as y`, `from x import y` — cấu trúc câu lệnh import
# khá đặc trưng (chỉ gồm định danh cách nhau bởi dấu chấm/phẩy), khác hẳn
# 1 câu tiếng Anh thật ("Import your file here to begin.") nên an toàn để
# tự nó là 1 tín hiệu.
_CODE_IMPORT_RE = re.compile(
    r"^\s*(import\s+[\w.]+(\s*,\s*[\w.]+)*(\s+as\s+\w+)?"
    r"|from\s+[\w.]+\s+import\s+[\w., *]+)\s*$"
)
# 1 dòng CHỈ gồm 1 lệnh gọi hàm dạng `tên(...)` — hình dạng khá đặc trưng.
_CODE_CALL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\([^()]*\)\s*;?\s*$")
# 1 dòng gán biến đơn giản `tên = biểu_thức` (không phải `==`).
_CODE_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\s*=\s*[^=]")

CODE_LINE_RATIO_THRESHOLD = 0.5


def _is_code_line(line):
    stripped = line.strip()
    if not stripped:
        return False
    if (
        _CODE_LINE_SYNTAX_RE.search(stripped)
        or _CODE_LINE_ENDING_RE.search(stripped)
        or _CODE_IMPORT_RE.match(stripped)
        or _CODE_CALL_RE.match(stripped)
        or _CODE_ASSIGN_RE.match(stripped)
    ):
        return True
    if _CODE_LINE_KEYWORD_RE.match(stripped):
        # Từ khoá 1 mình dễ trùng ngẫu nhiên — cần thêm 1 tín hiệu phụ
        # (dấu ngoặc, hoặc kết thúc bằng dấu hai chấm kiểu `def foo():`).
        return "(" in stripped or stripped.endswith(":")
    return False


def is_code_snippet(text):
    """1 dòng/khối được coi là code nếu phần lớn các dòng không rỗng của
    nó trông giống code. Với input 1 dòng, dòng đó phải TỰ đủ mạnh (khớp
    keyword lẫn ký tự kết thúc, hoặc có cú pháp code đặc trưng)."""
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return False
    code_lines = sum(1 for line in lines if _is_code_line(line))
    return code_lines / len(lines) >= CODE_LINE_RATIO_THRESHOLD


_COMMENT_MARKERS = ("#", "//", "--")
_PLACEHOLDER = "\x00{}\x00"


def _quote_parity_ok(prefix):
    return prefix.count('"') % 2 == 0 and prefix.count("'") % 2 == 0


def _find_comment_marker(line):
    """Vị trí marker comment ĐẦU TIÊN trên dòng, bỏ qua marker nằm trong 1
    chuỗi ký tự (theo _quote_parity_ok). Trả về (idx, marker) hoặc None."""
    best = None
    for marker in _COMMENT_MARKERS:
        idx = line.find(marker)
        while idx != -1:
            if _quote_parity_ok(line[:idx]):
                if best is None or idx < best[0]:
                    best = (idx, marker)
                break
            idx = line.find(marker, idx + 1)
    return best


def split_code_comments(text):
    """Tách text thành (template, [comment_texts]). template giữ nguyên
    100% phần code, chỉ thay phần chú thích bằng placeholder — ghép lại
    bằng reassemble_code_comments(). Dòng không có chú thích giữ y nguyên,
    kể cả khoảng trắng."""
    out_lines = []
    comments = []
    for line in text.split("\n"):
        found = _find_comment_marker(line)
        if found is None:
            out_lines.append(line)
            continue
        idx, marker = found
        comment_text = line[idx + len(marker):]
        if not comment_text.strip():
            out_lines.append(line)
            continue
        placeholder_idx = len(comments)
        comments.append(comment_text)
        out_lines.append(f"{line[:idx]}{marker} {_PLACEHOLDER.format(placeholder_idx)}")
    return "\n".join(out_lines), comments


def reassemble_code_comments(template, translated_comments):
    result = template
    for i, translated in enumerate(translated_comments):
        result = result.replace(_PLACEHOLDER.format(i), translated.strip())
    return result


def translate_units_with_code_awareness(units, router, **translate_kwargs):
    """Bọc quanh router.translate_batch(): unit nào là code (is_code_snippet)
    được tách riêng — chỉ phần CHÚ THÍCH đi dịch (gộp thành 1 batch riêng),
    phần code giữ nguyên 100%; unit code không có chú thích nào thì giữ
    nguyên bản gốc, không gọi API. Cùng dùng cho cả PDF (translator_engine.py)
    và docx/pptx (office_translate.py) nên đặt chung ở đây thay vì lặp lại
    logic tách/ghép ở 2 nơi.

    `units`/kwargs/kết quả trả về CÙNG SHAPE với router.translate_batch():
    items list các (text, is_table) -> list các (bản_dịch, engine,
    deepl_error, item_error) cùng thứ tự, cùng độ dài."""
    code_templates = {}   # unit_index -> template
    comment_units = []    # gộp comment của MỌI code unit thành 1 batch
    comment_owner = []    # song song với comment_units: (unit_index, placeholder_idx)
    plain_indices = []

    for i, (text, is_table) in enumerate(units):
        if not is_table and is_code_snippet(text):
            template, comments = split_code_comments(text)
            code_templates[i] = template
            for j, comment in enumerate(comments):
                comment_units.append((comment, False))
                comment_owner.append((i, j))
        else:
            plain_indices.append(i)

    plain_units = [units[i] for i in plain_indices]
    plain_results = router.translate_batch(plain_units, **translate_kwargs) if plain_units else []
    comment_results = (
        router.translate_batch(comment_units, **translate_kwargs) if comment_units else []
    )

    per_unit_comments = {}  # unit_index -> {placeholder_idx: bản_dịch}
    for (unit_idx, placeholder_idx), result in zip(comment_owner, comment_results):
        per_unit_comments.setdefault(unit_idx, {})[placeholder_idx] = result[0]

    results = [None] * len(units)
    for idx, result in zip(plain_indices, plain_results):
        results[idx] = result
    for unit_idx, template in code_templates.items():
        translated_map = per_unit_comments.get(unit_idx)
        if translated_map:
            ordered = [translated_map[j] for j in sorted(translated_map)]
            results[unit_idx] = (reassemble_code_comments(template, ordered), "code", None, None)
        else:
            results[unit_idx] = (units[unit_idx][0], "skip", None, None)

    return results
