"""CLI cho tính năng chuyển đổi PDF -> Word/PPTX, gọi từ Swift qua subprocess.
Dùng cùng giao thức JSON-theo-dòng như translator_engine.py để Swift xử lý
đồng nhất.

Cách dùng:
    python3 convert_cli.py --config /path/to/config.json

Các trường trong config.json: input_pdf (bắt buộc), output_docx (tùy chọn),
output_pptx (tùy chọn) — có trường nào thì chuyển sang định dạng đó.
"""
import argparse
import json
import sys

from pdf_convert import convert_to_docx, convert_to_pptx


def emit(**event):
    print(json.dumps(event), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    input_pdf = config["input_pdf"]
    output_docx = config.get("output_docx")
    output_pptx = config.get("output_pptx")

    try:
        if output_docx:
            emit(type="progress", stage="docx", status="start")
            convert_to_docx(input_pdf, output_docx)
            emit(type="progress", stage="docx", status="done")

        if output_pptx:
            emit(type="progress", stage="pptx", status="start")
            convert_to_pptx(input_pdf, output_pptx)
            emit(type="progress", stage="pptx", status="done")
    except Exception as exc:
        emit(type="error", message=str(exc))
        sys.exit(1)

    emit(type="done", output_docx=output_docx, output_pptx=output_pptx)


if __name__ == "__main__":
    main()
