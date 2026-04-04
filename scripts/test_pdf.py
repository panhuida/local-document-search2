from pathlib import Path

import fitz
from markitdown import MarkItDown

PDF_PATH = Path(r"D:\documents\计算机\编程\Python\爬虫\掘金爬虫讲稿.pdf")
OUTPUT_DIR = PDF_PATH.parent


def extract_with_pymupdf(pdf_path: Path) -> Path:
    output_path = OUTPUT_DIR / f"{pdf_path.stem}_pymupdf.txt"

    doc = fitz.open(pdf_path)
    try:
        all_pages = []
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text")
            all_pages.append(f"===== 第 {page_num} 页 =====\n{text}")
        content = "\n\n".join(all_pages)
    finally:
        doc.close()

    output_path.write_text(content, encoding="utf-8")
    return output_path


def extract_with_markitdown(pdf_path: Path) -> Path:
    output_path = OUTPUT_DIR / f"{pdf_path.stem}_markitdown.md"

    md = MarkItDown()
    result = md.convert(str(pdf_path))
    output_path.write_text(result.text_content, encoding="utf-8")

    return output_path


def main() -> None:
    if not PDF_PATH.exists():
        raise FileNotFoundError(f"文件不存在：{PDF_PATH}")

    pymupdf_output = extract_with_pymupdf(PDF_PATH)
    print(f"PyMuPDF 输出：{pymupdf_output}")

    markitdown_output = extract_with_markitdown(PDF_PATH)
    print(f"MarkItDown 输出：{markitdown_output}")


if __name__ == "__main__":
    main()
