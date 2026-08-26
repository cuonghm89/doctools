"""Tự kiểm tra logic phân nhánh của router. Chạy trực tiếp:
    python3 test_router.py
Không gọi mạng: _deepl/_gemini được stub (giả lập) ra.

Stub trả về text.upper() thay vì chuỗi thêm tiền tố "[deepl]": 1 tiền tố cố
định sẽ làm phồng tỷ lệ dài/ngắn của các cụm test ngắn ở đây (< 4 từ) một
cách vô tình, kích hoạt nhầm cơ chế chặn dịch-quá-dài. .upper() giữ nguyên
độ dài trong khi vẫn phân biệt được với input để assert.
"""
import atexit
import os

from router import (
    TranslationRouter, is_table_block,
    TRACKER_PATH, GEMINI_TRACKER_PATH, load_gemini_tracker, save_gemini_tracker,
)


def _protect_real_file(path):
    """router.translate()/translate_batch() ghi thẳng vào TRACKER_PATH/
    GEMINI_TRACKER_PATH thật (không có bản test riêng) — nếu không backup/
    restore, chạy file test này sẽ ghi đè mất quota thật của người dùng.
    Đăng ký khôi phục qua atexit để chạy dù assert bên dưới có fail giữa
    chừng, không chỉ khi mọi thứ pass."""
    backup = None
    if os.path.exists(path):
        with open(path) as f:
            backup = f.read()

    def restore():
        if backup is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "w") as f:
                f.write(backup)

    atexit.register(restore)


_protect_real_file(TRACKER_PATH)
_protect_real_file(GEMINI_TRACKER_PATH)

router = TranslationRouter(deepl_key="fakekey:fx", gemini_key="fakegeminikey")
router.tracker = {"month": "2026-08", "chars_used": 0}
router._deepl = lambda text: text.upper()
router._gemini = lambda text: text.upper()

# Trong quota, văn bản thường -> DeepL, không có lỗi để báo.
text, engine, deepl_error = router.translate("hello world")
assert engine == "deepl", engine
assert deepl_error is None
assert router.tracker["chars_used"] == len("hello world")

# Khối dạng bảng -> Gemini dù vẫn còn quota, và vì DeepL chưa từng được
# thử nên cũng không có lỗi DeepL nào để báo.
table_block = {
    "lines": [
        {"spans": [{"text": "1 2 3"}]},
        {"spans": [{"text": "4 5 6"}]},
        {"spans": [{"text": "7 8 9"}]},
    ]
}
assert is_table_block(table_block) is True
_, engine, deepl_error = router.translate("look at this table", is_table=True)
assert engine == "gemini", engine
assert deepl_error is None

# Vượt quota -> Gemini kể cả với văn bản thường.
router.tracker["chars_used"] = router.deepl_limit
_, engine, deepl_error = router.translate("more plain text")
assert engine == "gemini", engine
assert deepl_error is None

# DeepL lỗi -> rơi xuống Gemini VÀ báo rõ lý do, thay vì fail âm thầm (đây
# đúng là lỗi từng khiến câu hỏi "sao DeepL không chạy?" không thể trả lời
# được trước khi có trường này).
router.tracker["chars_used"] = 0
def boom(_text):
    raise ConnectionError("network down")
router._deepl = boom
_, engine, deepl_error = router.translate("plain text here")
assert engine == "gemini", engine
assert deepl_error == "network down", deepl_error

# Không có Gemini key và DeepL lỗi/không dùng được -> báo lỗi rõ ràng,
# không crash, và lý do DeepL fail được gộp vào message.
router.gemini_key = ""
try:
    router.translate("plain text here")
    raise AssertionError("expected RuntimeError")
except RuntimeError as e:
    assert "network down" in str(e), e

# Văn bản rỗng/toàn khoảng trắng bị bỏ qua, không gọi API nào cả.
text, engine, deepl_error = router.translate("   ")
assert engine == "skip"
assert deepl_error is None

# --- Từ viết tắt/mã kỹ thuật: không bao giờ dịch, không bao giờ gọi API. ---
router.gemini_key = "fakegeminikey"
router._deepl = lambda text: (_ for _ in ()).throw(AssertionError("không được gọi DeepL cho 1 từ viết tắt"))
router._gemini = lambda text: (_ for _ in ()).throw(AssertionError("không được gọi Gemini cho 1 từ viết tắt"))
for code in ["CJA", "CDP", "SDK", "API", "R-T", "N-R-T", "JDBC"]:
    text, engine, deepl_error = router.translate(code)
    assert text == code and engine == "skip", (code, text, engine)
# Nhưng 1 cụm tiếng Anh bình thường tình cờ viết hoa chữ cái đầu thì KHÔNG
# được nhầm thành từ viết tắt.
router._deepl = lambda text: text.upper()
_, engine, _ = router.translate("Hello World")
assert engine == "deepl", engine

# --- Chặn dịch phình quá dài: 1 nhãn/thuật ngữ ngắn mà bản dịch trả về dài
# hơn hẳn thì giữ nguyên bản gốc thay vào đó. ---
router.tracker["chars_used"] = 0
router._deepl = lambda text: "một cụm từ tiếng việt dịch ra dài hơn nhiều so với bản gốc"
text, engine, deepl_error = router.translate("Core Banking")
assert engine == "skip" and text == "Core Banking", (text, engine)
# Vẫn tính vào quota dù kết quả bị bỏ -- server của DeepL đã tính request
# đó rồi bất kể mình có dùng kết quả hay không.
assert router.tracker["chars_used"] == len("Core Banking")

# 1 đoạn văn dài được miễn áp dụng quy tắc trên dù nở ra khá nhiều khi dịch
# Anh->Việt, vì đoạn văn có chỗ để xuống dòng và không nên bị âm thầm bỏ.
router.tracker["chars_used"] = 0
long_original = "This is a longer paragraph with several words in it that should not trigger the short-label guard"
router._deepl = lambda text: text + " " + ("dài " * 20)
_, engine, _ = router.translate(long_original)
assert engine == "deepl", engine

print("All router self-checks passed.")


# --- translate_batch: cùng quy tắc định tuyến, nhưng các item đủ điều
# kiện DeepL gộp thành 1 lần gọi thay vì từng item riêng. ---
batch_router = TranslationRouter(deepl_key="fakekey:fx", gemini_key="fakegeminikey")
batch_router.tracker = {"month": "2026-08", "chars_used": 0}
batch_calls = []
def fake_deepl_batch(texts):
    batch_calls.append(list(texts))
    return [t.upper() for t in texts]
batch_router._deepl_batch = fake_deepl_batch
batch_router._gemini = lambda text: text.upper()

results = batch_router.translate_batch([
    ("hello there", False),
    ("world of code", False),
    ("look at this table", True),  # bảng -> KHÔNG được vào batch DeepL
    ("   ", False),                # rỗng -> skip, không gọi API nào cả
    ("SDK", False),                # từ viết tắt -> skip, không gọi API nào cả
])
assert len(batch_calls) == 1, "mọi text đủ điều kiện DeepL phải đi trong ĐÚNG 1 lần gọi batch"
assert batch_calls[0] == ["hello there", "world of code"], batch_calls[0]
assert results[0] == ("HELLO THERE", "deepl", None, None)
assert results[1] == ("WORLD OF CODE", "deepl", None, None)
assert results[2] == ("LOOK AT THIS TABLE", "gemini", None, None)
assert results[3] == ("   ", "skip", None, None)
assert results[4] == ("SDK", "skip", None, None)
assert batch_router.tracker["chars_used"] == len("hello there") + len("world of code")

# 1 nhãn ngắn trong batch mà bản dịch DeepL phình quá dài thì bị revert về
# bản gốc, giống hệt đường dẫn từng-item-một.
batch_router.tracker["chars_used"] = 0
def fake_deepl_batch_expand(texts):
    batch_calls.append(list(texts))
    return ["một cụm từ tiếng việt dịch ra dài hơn nhiều so với bản gốc" for _ in texts]
batch_router._deepl_batch = fake_deepl_batch_expand
results = batch_router.translate_batch([("Core Banking", False)])
assert results[0] == ("Core Banking", "skip", None, None), results[0]
assert batch_router.tracker["chars_used"] == len("Core Banking")

# Cả batch fail -> rơi về đường dẫn từng-item-một (thử lại DeepL, rồi
# Gemini), không vứt bỏ hết mọi thứ.
def boom_batch(_texts):
    raise ConnectionError("batch network down")
batch_router._deepl_batch = boom_batch
batch_router._deepl = lambda text: (_ for _ in ()).throw(ConnectionError("still down"))
results = batch_router.translate_batch([("plain text here", False)])
assert results[0][1] == "gemini", results[0]
assert results[0][0] == "PLAIN TEXT HERE"

# 1 item thực sự không cứu được (không có Gemini key) -> chỉ item ĐÓ fail,
# không kéo sập cả batch theo.
batch_router.gemini_key = ""
results = batch_router.translate_batch([("plain text here", False)])
assert results[0][0] is None and results[0][3] is not None, results[0]

print("All translate_batch self-checks passed.")


# --- gemini_tracker: đếm request/ngày (RPD), reset theo ngày chứ không
# theo tháng như tracker DeepL. (File thật đã được bảo vệ bởi
# _protect_real_file() ở đầu file, không cần backup/restore lại ở đây.) ---
save_gemini_tracker({"day": "2000-01-01", "requests_used": 5})
fresh = load_gemini_tracker()
assert fresh["requests_used"] == 0, "ngày cũ phải bị reset về 0, không cộng dồn"
assert fresh["day"] != "2000-01-01"

fresh["requests_used"] += 1
save_gemini_tracker(fresh)
reloaded = load_gemini_tracker()
assert reloaded["requests_used"] == 1, "cùng ngày phải cộng dồn, không reset"

print("All gemini_tracker self-checks passed.")

# --- deepl_limit <= 0 (tài khoản Pro, không giới hạn cứng) -> vẫn luôn
# dùng được DeepL dù chars_used đã rất lớn. ---
router.tracker = {"chars_used": 10_000_000, "limit": 0}
router.deepl_limit = 0
router._deepl = lambda text: text.upper()
_, engine, _ = router.translate("plain text here")
assert engine == "deepl", engine

print("All deepl_limit self-checks passed.")
