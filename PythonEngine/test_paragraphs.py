"""Tự kiểm tra logic gộp đoạn văn. Chạy trực tiếp:
    python3 test_paragraphs.py
"""
from paragraphs import (
    merge_paragraph_blocks, is_bullet_text, block_font_style, block_text_color,
)


def make_block(x0, y0, x1, y1, text, size=11, bold=False, italic=False, color=0):
    flags = (16 if bold else 0) | (2 if italic else 0)
    return {
        "type": 0,
        "bbox": (x0, y0, x1, y1),
        "lines": [{"spans": [{"text": text, "size": size, "flags": flags, "font": "Arial", "color": color}]}],
    }


# Hai dòng xếp chồng trong cùng 1 cột, khoảng cách nhỏ -> gộp thành 1 nhóm.
para = [
    make_block(72, 100, 300, 112, "The reason so many innovators fail"),
    make_block(72, 114, 300, 126, "and so many products flop is that"),
]
groups = merge_paragraph_blocks(para)
assert len(groups) == 1, f"expected 1 merged group, got {len(groups)}"
assert len(groups[0]["sub_blocks"]) == 2

# Hai cột nằm cạnh nhau, cùng y -> KHÔNG được gộp với nhau.
columns = [
    make_block(72, 100, 300, 112, "Left column text"),
    make_block(340, 100, 560, 112, "Right column text"),
]
groups = merge_paragraph_blocks(columns)
assert len(groups) == 2, f"columns must stay separate, got {len(groups)} groups"

# Khoảng cách dọc lớn trong cùng 1 cột -> đoạn văn tách biệt, không gộp.
gapped = [
    make_block(72, 100, 300, 112, "First paragraph line"),
    make_block(72, 300, 300, 312, "Unrelated paragraph far below"),
]
groups = merge_paragraph_blocks(gapped)
assert len(groups) == 2, f"large gap should not merge, got {len(groups)} groups"

# Cỡ chữ khác nhau nhiều trong cùng 1 cột (tiêu đề vs thân bài) -> không gộp.
mixed_size = [
    make_block(72, 100, 300, 120, "Big Heading", size=24),
    make_block(72, 124, 300, 136, "small body text right after", size=11),
]
groups = merge_paragraph_blocks(mixed_size)
assert len(groups) == 2, f"different sizes should not merge, got {len(groups)} groups"

# Cùng cỡ/kiểu chữ nhưng khác màu nhấn (ví dụ "CHAPTER 03" màu tím ngay
# trên "CHAPTER 04" màu đen, 1 layout mục lục thật) -> KHÔNG được gộp.
# Gộp sẽ bọc chúng thành 1 khung kết hợp bị trôi lệch khỏi bất cứ thứ gì
# mỗi cái đáng lẽ phải thẳng hàng (hàng mô tả chương tương ứng của nó).
chapter_labels = [
    make_block(72, 100, 160, 112, "CHAPTER 03", color=0x8A2BE2),
    make_block(72, 116, 160, 128, "CHAPTER 04", color=0x000000),
]
groups = merge_paragraph_blocks(chapter_labels)
assert len(groups) == 2, f"different colors should not merge, got {len(groups)} groups"

# Chú thích icon kiểu sơ đồ với x0 lệch nhau chút ít (ví dụ "CRM" canh giữa
# dưới icon riêng của nó, lệch vài điểm so với chú thích "Core Banking"
# dưới 1 icon khác) -> KHÔNG được gộp thành 1 nhãn dính chữ lộn xộn.
icon_captions = [
    make_block(72, 300, 130, 312, "Core Banking"),
    make_block(160, 300, 190, 312, "CRM"),  # khác cột, x0 lệch ~5pt
]
groups = merge_paragraph_blocks(icon_captions)
assert len(groups) == 2, f"different-column icon captions should not merge, got {len(groups)} groups"

# Các mục tính năng kiểu sơ đồ với khoảng đệm rộng hơn giữa các dòng như
# danh sách UI thật hay dùng (khác với giãn dòng sát của văn bản đoạn) ->
# KHÔNG được gộp thành 1 tên tính năng kết hợp.
feature_list = [
    make_block(72, 300, 300, 312, "Data Visualization", size=11),
    make_block(72, 328, 300, 340, "Intelligent Insight", size=11),  # khoảng cách ~1.5x
]
groups = merge_paragraph_blocks(feature_list)
assert len(groups) == 2, f"loosely-spaced list items should not merge, got {len(groups)} groups"

# Trích màu: 0xRRGGBB -> tuple số nguyên (r, g, b).
purple_block = make_block(72, 100, 160, 112, "CHAPTER 03", color=0x8A2BE2)
assert block_text_color(purple_block) == (0x8A, 0x2B, 0xE2)

# Nhận diện bullet.
assert is_bullet_text("• Conceptualize new products")
assert is_bullet_text("1. First step")
assert is_bullet_text("2) Second step")
assert not is_bullet_text("A regular sentence.")

# Nhận diện kiểu chữ (đậm/nghiêng).
bold_block = make_block(72, 100, 300, 112, "Bold text", bold=True)
assert block_font_style(bold_block) == (True, False)

italic_block = make_block(72, 100, 300, 112, "Italic text", italic=True)
assert block_font_style(italic_block) == (False, True)

plain_block = make_block(72, 100, 300, 112, "Plain text")
assert block_font_style(plain_block) == (False, False)

print("All paragraph-merging self-checks passed.")
