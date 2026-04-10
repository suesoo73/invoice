import os
import re
import subprocess
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
import httpx
from PIL import Image

from app.core.config import settings
from app.services.parser_service import (
    extract_json_block,
    fallback_parse_from_text,
    merge_with_fallback,
    normalize_ocr_payload,
)

# PaddleOCR-VL에 넘기기 전 렌더링 해상도 (DPI)
_PREPROCESS_DPI = 200


def _preprocess_to_grayscale(src_path: str, dst_dir: str) -> str:
    """파일을 회색조로 변환해 dst_dir에 저장하고 경로를 반환한다.

    PDF  → 각 페이지를 회색조 픽스맵으로 렌더링 후 단일 PDF로 재조합
    이미지 → PIL로 'L' 모드 변환 후 PNG 저장
    """
    src = Path(src_path)
    suffix = src.suffix.lower()

    if suffix == ".pdf":
        dst_path = str(Path(dst_dir) / "gray_input.pdf")
        doc = fitz.open(src_path)
        out = fitz.open()
        for page in doc:
            pix = page.get_pixmap(colorspace=fitz.csGRAY, dpi=_PREPROCESS_DPI)
            img_page = out.new_page(width=pix.width, height=pix.height)
            img_page.insert_image(img_page.rect, pixmap=pix)
        out.save(dst_path, garbage=4, deflate=True)
        doc.close()
        out.close()
        return dst_path

    if suffix in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}:
        dst_path = str(Path(dst_dir) / ("gray_input" + suffix))
        img = Image.open(src_path).convert("L")
        img.save(dst_path)
        return dst_path

    # 지원하지 않는 형식은 원본 그대로 사용
    return src_path


def _strip_html_for_llm(text: str, max_chars: int = 6000) -> str:
    """HTML 태그 제거 후 Ollama에 넘길 플레인 텍스트로 변환한다."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


def _run_paddleocr_vl(file_path: str) -> str:
    """PaddleOCR-VL subprocess를 실행해 마크다운 텍스트를 반환한다.
    GPU 0 전용 (CUDA_VISIBLE_DEVICES=0).
    전처리: 입력 파일을 회색조로 변환 후 PaddleOCR-VL에 전달한다.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # 회색조 전처리
        gray_path = _preprocess_to_grayscale(file_path, tmpdir)
        cmd = [
            settings.paddleocr_vl_bin,
            "doc_parser",
            "-i", gray_path,
            "--save_path", tmpdir,
            "--device", settings.paddleocr_vl_device,
            "--vl_rec_model_name", settings.paddleocr_vl_model,
            "--use_chart_recognition", "False",
            "--use_seal_recognition", "False",
            "--max_pixels", str(settings.paddleocr_vl_max_pixels),
            "--min_pixels", str(settings.paddleocr_vl_min_pixels),
            "--max_new_tokens", str(settings.paddleocr_vl_max_new_tokens),
        ]
        env = os.environ.copy()
        env["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        env["CUDA_VISIBLE_DEVICES"] = "0"  # GPU 0 전용

        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"PaddleOCR-VL failed (exit {result.returncode}): {result.stderr[:1000]}"
            )

        md_files = sorted(Path(tmpdir).glob("*.md"))
        if not md_files:
            raise RuntimeError("PaddleOCR-VL produced no markdown output")

        return "\n\n".join(f.read_text(encoding="utf-8") for f in md_files)


def _build_system_prompt(document_type: str) -> str:
    return (
        f'You are a JSON extraction engine for Korean {document_type} documents. '
        'Output ONLY a JSON object — no explanation, no markdown fences, no extra keys. '
        'Follow the exact schema the user provides. '
        'Korean field mappings: '
        '공급자/공급하는자/판매자/상호(왼쪽)→vendor_name, 공급자사업자번호→vendor_reg_no, '
        '공급받는자/구매자/상호(오른쪽)→buyer_name, 공급받는자사업자번호→buyer_reg_no, '
        '작성일/거래일자/발행일→issue_date, '
        '공급가액→supply_amount, 세액→tax_amount, 합계금액/합계→total_amount, '
        '품목/상품명→item_name, 수량→quantity, 단가→unit_price, 금액→line_amount. '
        'Strip commas/hyphens/currency symbols from numbers. Use null for missing values. Currency default KRW.'
    )


def _build_user_prompt(document_type: str, ocr_text: str) -> str:
    return (
        f"Korean {document_type} OCR text:\n\n"
        f"{ocr_text}\n\n"
        "Return this JSON and nothing else:\n"
        '{"fields":{"vendor_name":null,"vendor_reg_no":null,"buyer_name":null,"buyer_reg_no":null,'
        '"issue_date":null,"supply_amount":null,'
        '"tax_amount":null,"total_amount":null,"currency":"KRW",'
        '"payment_method":null,"invoice_number":null,"receipt_number":null},'
        '"items":[{"line_no":1,"item_name":"","quantity":null,"unit_price":null,"line_amount":null}]}'
    )


def _extract_fields_with_ollama(
    *, model_name: str, document_type: str, ocr_text: str
) -> tuple[dict, dict]:
    """OCR 텍스트를 Ollama에 보내 구조화된 필드를 추출한다.
    Ollama는 GPU 1 전용 (systemd CUDA_VISIBLE_DEVICES=1).
    """
    clean_text = _strip_html_for_llm(ocr_text)
    request_payload = {
        "model": model_name,
        "system": _build_system_prompt(document_type),
        "prompt": _build_user_prompt(document_type, clean_text),
        "stream": False,
        "think": False,
        "options": {"num_predict": 1500},
    }
    response = httpx.post(
        f"{settings.ollama_base_url}/api/generate",
        json=request_payload,
        timeout=300,
    )
    response.raise_for_status()
    llm_payload = response.json()
    model_response = llm_payload.get("response", "")
    parsed = normalize_ocr_payload(extract_json_block(model_response), raw_text=ocr_text)
    parsed["raw_text"] = ocr_text  # 항상 PaddleOCR-VL 원문 사용
    return parsed, llm_payload


def run_ocr_with_model(*, model_name: str, file_path: str, document_type: str) -> dict:
    """
    2단계 병렬 가능 OCR 파이프라인:
      1. PaddleOCR-VL (GPU 0) → 고정밀 마크다운 텍스트
      2. Ollama qwen3.5:9B (GPU 1) → 구조화된 필드 추출
    두 모델이 별도 GPU를 사용하므로 충돌 없음.
    """
    # 1단계: PaddleOCR-VL (GPU 0)
    ocr_text = _run_paddleocr_vl(file_path)

    # 2단계: Ollama 필드 추출 (GPU 1)
    parsed, llm_payload = _extract_fields_with_ollama(
        model_name=model_name,
        document_type=document_type,
        ocr_text=ocr_text,
    )

    # 폴백 파서로 보완
    parsed = merge_with_fallback(parsed, fallback_parse_from_text(ocr_text))

    return {
        "model_name": model_name,
        "raw_text": parsed["raw_text"],
        "fields": parsed["fields"],
        "items": parsed["items"],
        "llm_response_json": llm_payload,
    }
