"""Smart Hybrid Translation Router: ưu tiên DeepL, dùng Gemini cho bảng biểu/vượt quota/dự phòng lỗi."""
import json
import os
import re
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TRACKER_PATH = os.path.expanduser(
    "~/Library/Application Support/CPDFGear/usage_tracker.json"
)
GEMINI_TRACKER_PATH = os.path.expanduser(
    "~/Library/Application Support/CPDFGear/gemini_usage_tracker.json"
)
DEEPL_FREE_LIMIT = 500_000
DEEPL_TIMEOUT = 20
GEMINI_TIMEOUT = 30
DEEPL_MAX_BATCH = 50  # giới hạn số tham số `text` mỗi request theo tài liệu DeepL


def _session_with_retries():
    """Lỗi 5xx tạm thời từ bất kỳ nhà cung cấp nào (server quá tải, v.v.)
    không nên làm chết cả tiến trình dịch. Thử lại vài lần có backoff trước
    khi bỏ cuộc và để cơ chế dự phòng DeepL->Gemini / báo lỗi bình thường
    tiếp quản."""
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def load_tracker():
    """Cache local của lần đồng bộ gần nhất với DeepL (xem
    `TranslationRouter.refresh_usage()`). Không tự đoán ngày reset ở đây
    nữa — DeepL free tier reset theo ngày đăng ký (vd 29 hàng tháng), không
    phải đầu tháng dương lịch, nên tự đếm+tự reset theo tháng luôn có ngày
    bị lệch. `refresh_usage()` hỏi thẳng DeepL mỗi lần bắt đầu dịch; cache
    này chỉ dùng khi offline/lỗi mạng lúc đó."""
    if os.path.exists(TRACKER_PATH):
        with open(TRACKER_PATH) as f:
            return json.load(f)
    return {"chars_used": 0, "limit": DEEPL_FREE_LIMIT}


def save_tracker(data):
    os.makedirs(os.path.dirname(TRACKER_PATH), exist_ok=True)
    with open(TRACKER_PATH, "w") as f:
        json.dump(data, f)


def _current_day():
    return datetime.now().strftime("%Y-%m-%d")


def load_gemini_tracker():
    """Gemini free tier tính quota theo request/ngày (RPD), không phải ký
    tự/tháng như DeepL — nên tracker này reset theo ngày, không theo tháng."""
    day = _current_day()
    if os.path.exists(GEMINI_TRACKER_PATH):
        with open(GEMINI_TRACKER_PATH) as f:
            data = json.load(f)
        if data.get("day") == day:
            return data
    return {"day": day, "requests_used": 0}


def save_gemini_tracker(data):
    os.makedirs(os.path.dirname(GEMINI_TRACKER_PATH), exist_ok=True)
    with open(GEMINI_TRACKER_PATH, "w") as f:
        json.dump(data, f)


def is_table_lines(lines):
    """Heuristic thô: nhiều dòng ngắn, nhiều chữ số trông giống 1 nhóm hàng
    trong bảng biểu.
    ponytail: heuristic đơn giản dựa trên số dòng/mật độ chữ số, nâng cấp
    lên nhận diện bảng thật (ví dụ pdfplumber) nếu bị nhận nhầm quá nhiều."""
    if len(lines) < 3:
        return False
    digit_lines = sum(
        1 for line in lines
        if re.search(r"\d", "".join(span["text"] for span in line["spans"]))
    )
    return digit_lines / len(lines) > 0.5


def is_table_block(block):
    return is_table_lines(block.get("lines", []))


# Các chuỗi ngắn viết hoa/có gạch nối hầu như luôn là từ viết tắt, tên giao
# thức, hoặc mã sản phẩm ("CJA", "SDK", "API", "R-T", "N-R-T", "JDBC") —
# không bao giờ dịch những cái này, và bỏ qua luôn việc gọi API cho chúng.
_ACRONYM_RE = re.compile(r"^[A-Z][A-Z0-9]*([\-/&][A-Z0-9]+)*$")
_ACRONYM_MAX_LEN = 12

# Một thuật ngữ/nhãn ngắn mà bản dịch trả về dài hơn hẳn so với bản gốc
# ("Jobs-to-be-Done" -> cả 1 cụm mô tả dài bằng tiếng Việt) chính là nguyên
# nhân làm vỡ khung ô chật trong sơ đồ và tiêu đề lớn. Với các cụm ngắn kiểu
# này, nếu bản dịch dài hơn bản gốc chưa tới ~60% thì coi là "phình vừa
# phải" và dùng bình thường; dài hơn nữa thì coi là "phình quá mức" và giữ
# nguyên bản gốc thay vào đó.
# Đoạn văn bình thường được miễn áp dụng quy tắc này — tiếng Việt dài hơn
# tiếng Anh tự nhiên (~20-30%) là chuyện bình thường và đoạn văn có chỗ để
# tự xuống dòng, khác với 1 nhãn hay tiêu đề.
_LENGTH_GUARD_MAX_WORDS = 4
_LENGTH_GUARD_MAX_RATIO = 1.6


def _looks_like_acronym_or_code(text):
    stripped = text.strip()
    return bool(stripped) and len(stripped) <= _ACRONYM_MAX_LEN and bool(_ACRONYM_RE.match(stripped))


def _translation_expanded_too_much(original, translated):
    if len(original.split()) > _LENGTH_GUARD_MAX_WORDS:
        return False
    return len(translated) > len(original) * _LENGTH_GUARD_MAX_RATIO


class TranslationRouter:
    def __init__(self, deepl_key, gemini_key, deepl_limit=DEEPL_FREE_LIMIT):
        self.deepl_key = deepl_key
        self.gemini_key = gemini_key
        self.deepl_limit = deepl_limit
        self.tracker = load_tracker()
        self.gemini_tracker = load_gemini_tracker()
        self.session = _session_with_retries()

    def refresh_usage(self):
        """Đồng bộ chars_used/limit với số liệu THẬT từ DeepL (GET
        /v2/usage) ngay khi bắt đầu 1 lần dịch, thay vì tin vào cache local
        có thể đã lệch (dùng DeepL ở nơi khác, hoặc app từng tự đếm sai).
        Gọi 1 lần duy nhất mỗi lần dịch — không phải trước mỗi đoạn văn.
        Lỗi mạng/key sai thì im lặng giữ nguyên cache cũ, không chặn dịch."""
        if not self.deepl_key:
            return
        try:
            is_free_key = self.deepl_key.endswith(":fx")
            url = (
                "https://api-free.deepl.com/v2/usage" if is_free_key
                else "https://api.deepl.com/v2/usage"
            )
            resp = self.session.get(
                url,
                headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
                timeout=DEEPL_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            # Tài khoản Pro trả character_limit=0 nghĩa là KHÔNG giới hạn
            # (tính tiền theo ký tự, không có trần cứng) — giữ nguyên 0 để
            # can_use_deepl() hiểu đúng là "không giới hạn", không phải "đã
            # hết hạn mức".
            self.tracker = {
                "chars_used": data["character_count"],
                "limit": data.get("character_limit") or 0,
            }
            if self.tracker["limit"] > 0:
                self.deepl_limit = self.tracker["limit"]
            save_tracker(self.tracker)
        except Exception:
            pass

    def translate(self, text, is_table=False, enforce_length_guard=True):
        """Trả về (bản_dịch, engine_đã_dùng, deepl_error_hoặc_None).
        deepl_error chỉ có giá trị khi đã thử DeepL và thất bại, để nơi gọi
        có thể hiện rõ *lý do thật* vì sao 1 khối rơi xuống Gemini thay vì
        phải đoán.

        enforce_length_guard=False: bỏ qua _translation_expanded_too_much()
        — guard đó được thiết kế cho PDF (khung pixel CỐ ĐỊNH, không co
        giãn được nếu bản dịch phình dài, nên thà giữ bản gốc). office_
        translate.py (.docx/.pptx) tự co cỡ chữ để vừa khung
        (_adjust_font_for_length()) thay vì bỏ dịch, nên không cần — và
        không nên — áp guard này, nếu không sẽ tạo ra tình trạng nửa Anh
        nửa Việt khó hiểu ngay trong cùng 1 tiêu đề/đoạn văn."""
        if not text.strip():
            return text, "skip", None
        if _looks_like_acronym_or_code(text):
            return text, "skip", None

        can_use_deepl = (
            self.deepl_key
            and not is_table
            and (self.deepl_limit <= 0 or self.tracker["chars_used"] + len(text) <= self.deepl_limit)
        )
        deepl_error = None
        if can_use_deepl:
            try:
                result = self._deepl(text)
                # Vẫn tính vào quota dù kết quả này có được giữ lại hay
                # không — server của DeepL đã tính request đó rồi.
                self.tracker["chars_used"] += len(text)
                save_tracker(self.tracker)
                if enforce_length_guard and _translation_expanded_too_much(text, result):
                    return text, "skip", None
                return result, "deepl", None
            except Exception as exc:
                deepl_error = str(exc)  # rơi xuống thử Gemini bên dưới

        if not self.gemini_key:
            reason = f"DeepL lỗi ({deepl_error})" if deepl_error else "Vượt hạn mức DeepL"
            raise RuntimeError(f"{reason}, và chưa có Gemini API key để dự phòng.")

        gemini_result = self._gemini(text)
        if enforce_length_guard and _translation_expanded_too_much(text, gemini_result):
            return text, "skip", deepl_error
        return gemini_result, "gemini", deepl_error

    def translate_batch(self, items, enforce_length_guard=True):
        """items: list các (text, is_table). Trả về list cùng độ dài gồm
        các tuple (bản_dịch, engine, deepl_error, item_error), đúng thứ tự.

        Gửi mọi text đủ điều kiện DeepL trong 1 REQUEST DUY NHẤT thay vì
        1 request/item — DeepL hỗ trợ nhiều tham số `text` mỗi lần gọi, nên
        cả 1 trang gồm nhiều đoạn văn/bullet chỉ tốn 1 lần round-trip thay
        vì hàng chục lần. Các item không đủ điều kiện (bảng biểu, vượt
        quota) vẫn đi qua đường dẫn từng-item-một như bình thường.
        item_error chỉ có giá trị khi CHÍNH item đó không dịch được (tương
        đương raise của translate(), nhưng 1 item lỗi không làm mất kết quả
        đã có của các item khác trong cùng batch).

        enforce_length_guard: xem docstring translate()."""
        results = [None] * len(items)
        deepl_idx, deepl_texts = [], []

        for i, (text, is_table) in enumerate(items):
            if not text.strip() or _looks_like_acronym_or_code(text):
                results[i] = (text, "skip", None, None)
            elif (
                self.deepl_key
                and not is_table
                and (self.deepl_limit <= 0 or self.tracker["chars_used"] + len(text) <= self.deepl_limit)
            ):
                deepl_idx.append(i)
                deepl_texts.append(text)

        if deepl_texts:
            try:
                translations = self._deepl_batch(deepl_texts)
                for idx, translated in zip(deepl_idx, translations):
                    original = items[idx][0]
                    if enforce_length_guard and _translation_expanded_too_much(original, translated):
                        results[idx] = (original, "skip", None, None)
                    else:
                        results[idx] = (translated, "deepl", None, None)
                # Vẫn tính vào quota cho cả batch dù giữ lại kết quả nào —
                # server của DeepL đã tính hết mọi text gửi lên rồi.
                self.tracker["chars_used"] += sum(len(t) for t in deepl_texts)
                save_tracker(self.tracker)
            except Exception as exc:
                # Cả batch fail (mạng chập chờn, v.v.) — rơi về đường dẫn
                # từng-item-một bình thường (thử lại DeepL, rồi Gemini) chỉ
                # cho các item này, thay vì mất luôn cả trang.
                batch_error = str(exc)
                for idx in deepl_idx:
                    text, is_table = items[idx]
                    try:
                        translated, engine, deepl_error = self.translate(
                            text, is_table=is_table, enforce_length_guard=enforce_length_guard)
                        results[idx] = (translated, engine, deepl_error or batch_error, None)
                    except Exception as exc2:
                        results[idx] = (None, None, None, str(exc2))

        for i, (text, is_table) in enumerate(items):
            if results[i] is None:
                try:
                    translated, engine, deepl_error = self.translate(
                        text, is_table=is_table, enforce_length_guard=enforce_length_guard)
                    results[i] = (translated, engine, deepl_error, None)
                except Exception as exc:
                    results[i] = (None, None, None, str(exc))

        return results

    def _deepl_batch(self, texts):
        is_free_key = self.deepl_key.endswith(":fx")
        url = (
            "https://api-free.deepl.com/v2/translate"
            if is_free_key
            else "https://api.deepl.com/v2/translate"
        )
        translated = []
        for i in range(0, len(texts), DEEPL_MAX_BATCH):
            chunk = texts[i:i + DEEPL_MAX_BATCH]
            resp = self.session.post(
                url,
                headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
                data=[("text", t) for t in chunk] + [("target_lang", "VI")],
                timeout=DEEPL_TIMEOUT,
            )
            resp.raise_for_status()
            translated.extend(t["text"] for t in resp.json()["translations"])
        return translated

    def _deepl(self, text):
        is_free_key = self.deepl_key.endswith(":fx")
        url = (
            "https://api-free.deepl.com/v2/translate"
            if is_free_key
            else "https://api.deepl.com/v2/translate"
        )
        resp = self.session.post(
            url,
            headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
            data={"text": text, "target_lang": "VI"},
            timeout=DEEPL_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["translations"][0]["text"]

    def _gemini(self, text):
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-flash-latest:generateContent?key={self.gemini_key}"
        )
        prompt = (
            "Translate the following text to Vietnamese. "
            "Only output the translated text, no notes or explanation:\n\n" + text
        )
        resp = self.session.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=GEMINI_TIMEOUT,
        )
        # Đếm ngay khi request đã thật sự tới được Google — kể cả khi
        # response là lỗi (429/5xx/bị chặn), Google vẫn tính request đó vào
        # quota RPD của key. Chỉ request lỗi mạng (không tới được server,
        # ném exception trước dòng này) mới không bị tính.
        self.gemini_tracker["requests_used"] += 1
        save_gemini_tracker(self.gemini_tracker)
        resp.raise_for_status()
        data = resp.json()

        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "không rõ lý do")
            raise RuntimeError(f"Gemini từ chối dịch đoạn văn bản (blockReason={reason})")

        parts = candidates[0].get("content", {}).get("parts") or []
        if not parts:
            finish_reason = candidates[0].get("finishReason", "không rõ lý do")
            raise RuntimeError(f"Gemini không trả về nội dung (finishReason={finish_reason})")

        return parts[0]["text"].strip()
