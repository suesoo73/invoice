import base64
from pathlib import Path

import fitz

from app.core.config import settings


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_image_base64(path: Path) -> list[str]:
    return [base64.b64encode(path.read_bytes()).decode("utf-8")]


def _render_pdf_pages(path: Path) -> list[str]:
    encoded_pages: list[str] = []
    document = fitz.open(path)
    try:
        for page in document[: settings.ocr_max_pdf_pages]:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            encoded_pages.append(base64.b64encode(pix.tobytes("png")).decode("utf-8"))
    finally:
        document.close()
    return encoded_pages


def load_document_payload(file_path: str) -> dict:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Document file not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix == ".txt":
        text = _read_text_file(path)
        return {"mode": "text", "text": text, "source_text": text}
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return {"mode": "vision", "images": _read_image_base64(path), "source_text": ""}
    if suffix == ".pdf":
        return {"mode": "vision", "images": _render_pdf_pages(path), "source_text": ""}

    raise ValueError(f"Unsupported file type for OCR worker: {suffix}")
