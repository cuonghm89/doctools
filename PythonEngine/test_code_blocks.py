"""Self-check code_blocks.py — chạy trực tiếp: python3 test_code_blocks.py"""
from code_blocks import (
    is_code_snippet, reassemble_code_comments, split_code_comments,
    translate_units_with_code_awareness,
)

# --- is_code_snippet: nhận diện đúng ---
PYTHON_SNIPPET = """def greet(name):
    # in lời chào
    print(f"Hello, {name}")
    return None"""
assert is_code_snippet(PYTHON_SNIPPET)

C_SNIPPET = """int add(int a, int b) {
    return a + b;
}"""
assert is_code_snippet(C_SNIPPET)

assert is_code_snippet("import numpy as np")
assert is_code_snippet("for (int i = 0; i < n; i++) {")

print("Tất cả self-check nhận-diện-code đều pass.")

# --- is_code_snippet: không nhận nhầm văn xuôi ---
assert not is_code_snippet("Đây là một câu bình thường trong tài liệu.")
assert not is_code_snippet("Các bước thực hiện như sau:")
assert not is_code_snippet("Kết quả: 42")
assert not is_code_snippet("")
assert not is_code_snippet("   \n  \n")

# Câu tiếng Anh thật mở đầu bằng từ trùng với từ khoá code (import/if/for/
# return/class/switch/static/...) — dễ nhận nhầm nhất nếu heuristic quá lỏng.
ENGLISH_PROSE_TRAPS = [
    "Import your file here to begin.",
    "If the value exceeds the limit, retry.",
    "For example, this works well in practice.",
    "Return to the previous menu.",
    "Class attendance is mandatory.",
    "The switch is located near the entrance.",
    "Static analysis showed no issues.",
]
for sentence in ENGLISH_PROSE_TRAPS:
    assert not is_code_snippet(sentence), sentence

print("Tất cả self-check không-nhận-nhầm-văn-xuôi đều pass.")

# --- split_code_comments: tách đúng comment, giữ nguyên code ---
template, comments = split_code_comments(PYTHON_SNIPPET)
assert comments == [" in lời chào"]
assert "\x00" in template
assert "print(f\"Hello, {name}\")" in template  # dòng không có comment giữ y nguyên

# round-trip: ghép lại với đúng comment gốc phải ra lại y hệt bản gốc
assert reassemble_code_comments(template, comments) == PYTHON_SNIPPET

print("Tất cả self-check tách-comment đều pass.")

# --- comment nằm trong chuỗi ký tự (dấu nháy cân đối) không bị tách nhầm ---
STRING_WITH_HASH = 'print("giá trị # không phải comment")'
template2, comments2 = split_code_comments(STRING_WITH_HASH)
assert comments2 == []
assert template2 == STRING_WITH_HASH

print("Tất cả self-check bỏ-qua-marker-trong-chuỗi đều pass.")

# --- code không có comment nào: template y hệt bản gốc, comments rỗng ---
NO_COMMENT = "x = 1\ny = 2\nreturn x + y"
template3, comments3 = split_code_comments(NO_COMMENT)
assert comments3 == []
assert template3 == NO_COMMENT

print("Tất cả self-check code-không-comment đều pass.")

# --- nhiều comment trên nhiều dòng, thứ tự placeholder đúng ---
MULTI = """x = 1  # gán x
y = 2  # gán y
return x + y"""
template4, comments4 = split_code_comments(MULTI)
assert comments4 == [" gán x", " gán y"]
translated = ["gán x (đã dịch)", "gán y (đã dịch)"]
result = reassemble_code_comments(template4, translated)
assert "gán x (đã dịch)" in result and "gán y (đã dịch)" in result
assert "x = 1  #" in result and "y = 2  #" in result

print("Tất cả self-check nhiều-comment đều pass.")


# --- translate_units_with_code_awareness: tích hợp với router (giả) ---
class _StubRouter:
    """Router giả: "dịch" = viết HOA, không gọi mạng thật. Ghi lại mọi text
    đã thấy để test được là code KHÔNG bị gửi lọt qua đây."""

    def __init__(self):
        self.seen_texts = []

    def translate_batch(self, items, enforce_length_guard=True):
        results = []
        for text, _is_table in items:
            self.seen_texts.append(text)
            results.append((text.upper(), "deepl", None, None))
        return results


router = _StubRouter()
NO_COMMENT_CODE = "x = 1\ny = 2\nreturn x + y"
units = [
    (PYTHON_SNIPPET, False),
    ("Đây là 1 đoạn văn thường.", False),
    (NO_COMMENT_CODE, False),
]
results = translate_units_with_code_awareness(units, router)
assert len(results) == 3

code_text, code_engine, _, _ = results[0]
assert code_engine == "code"
assert 'print(f"Hello, {name}")' in code_text  # dòng code giữ nguyên 100%
assert "IN LỜI CHÀO" in code_text  # phần chú thích ĐÃ qua router (viết hoa)
assert "def greet(name):" in code_text

plain_text, plain_engine, _, _ = results[1]
assert plain_text == "ĐÂY LÀ 1 ĐOẠN VĂN THƯỜNG."
assert plain_engine == "deepl"

no_comment_result = results[2]
assert no_comment_result == (NO_COMMENT_CODE, "skip", None, None)
assert NO_COMMENT_CODE not in router.seen_texts  # không tốn quota cho code không có chú thích

print("Tất cả self-check tích-hợp-với-router đều pass.")
