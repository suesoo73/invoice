import os
import re
import subprocess
import tempfile
import base64
from pathlib import Path

import fitz
import httpx
from PIL import Image

from app.core.config import settings
from app.services.parser_service import (
    extract_json_block,
    fallback_parse_from_text,
    merge_with_fallback,
    normalize_ocr_payload,
)
from app.services.runtime_config_service import (
    get_active_llm_config,
    get_external_llm_api_key,
    get_external_llm_chat_completions_url,
)

_PREPROCESS_DPI = 400


def _preprocess_to_grayscale(src_path: str, dst_dir: str) -> str:
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

    return src_path


def _strip_html_for_llm(text: str, max_chars: int = 6000) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


def _run_paddleocr_vl(
    file_path: str,
    use_grayscale: bool = True,
    gpu_id: str | None = None,
) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        prepared_path = _preprocess_to_grayscale(file_path, tmpdir) if use_grayscale else file_path
        cmd = [
            settings.paddleocr_vl_bin,
            "doc_parser",
            "-i", prepared_path,
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
        if gpu_id is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=settings.paddleocr_vl_timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"PaddleOCR-VL failed (exit {result.returncode}): {result.stderr[:1000]}"
            )

        md_files = sorted(Path(tmpdir).glob("*.md"))
        if not md_files:
            raise RuntimeError("PaddleOCR-VL produced no markdown output")

        return "\n\n".join(f.read_text(encoding="utf-8") for f in md_files)


def _encode_image_to_base64(image_path: str) -> str:
    return base64.b64encode(Path(image_path).read_bytes()).decode("ascii")


def _render_pdf_pages_for_glm(file_path: str, dst_dir: str, use_grayscale: bool) -> list[str]:
    image_paths: list[str] = []
    with fitz.open(file_path) as doc:
        page_count = min(doc.page_count, settings.ocr_max_pdf_pages)
        for index in range(page_count):
            page = doc.load_page(index)
            pix = page.get_pixmap(
                colorspace=fitz.csGRAY if use_grayscale else fitz.csRGB,
                dpi=_PREPROCESS_DPI,
                alpha=False,
            )
            page_path = str(Path(dst_dir) / f"glm_page_{index + 1}.jpg")
            pix.save(page_path)
            image_paths.append(page_path)
    return image_paths


def _run_glm_ocr_on_images(image_paths: list[str]) -> tuple[str, dict]:
    responses = []
    page_texts = []
    for index, image_path in enumerate(image_paths, start=1):
        request_payload = {
            "model": get_active_llm_config().get("ocr_model") or settings.glm_ocr_model,
            "prompt": "Read this Korean transaction statement image and return the OCR text in markdown.",
            "stream": False,
            "images": [_encode_image_to_base64(image_path)],
            "options": {"temperature": 0},
        }
        response = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json=request_payload,
            timeout=settings.ollama_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        responses.append(payload)
        text = (payload.get("response") or "").strip()
        if len(image_paths) > 1:
            page_texts.append(f"## Page {index}\n{text}")
        else:
            page_texts.append(text)
    return "\n\n".join(page_texts).strip(), {"pages": responses}


def _run_glm_ocr(file_path: str, use_grayscale: bool = True) -> tuple[str, dict]:
    with tempfile.TemporaryDirectory() as tmpdir:
        suffix = Path(file_path).suffix.lower()
        if suffix == ".pdf":
            image_paths = _render_pdf_pages_for_glm(file_path, tmpdir, use_grayscale)
        elif suffix in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}:
            prepared_path = _preprocess_to_grayscale(file_path, tmpdir) if use_grayscale else file_path
            image_paths = [prepared_path]
        else:
            raise RuntimeError("GLM-OCR supports only PDF or image inputs")
        if not image_paths:
            raise RuntimeError("GLM-OCR produced no page images")
        return _run_glm_ocr_on_images(image_paths)


def _run_external_api_ocr_on_images(image_paths: list[str]) -> tuple[str, dict]:
    headers = {"Content-Type": "application/json"}
    actual_api_key = get_external_llm_api_key()
    if actual_api_key:
        headers["Authorization"] = f"Bearer {actual_api_key}"

    responses = []
    page_texts = []
    model_name = get_active_llm_config().get("default_model") or settings.external_llm_model or ""

    for index, image_path in enumerate(image_paths, start=1):
        image_bytes = Path(image_path).read_bytes()
        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        request_payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Read this Korean transaction statement image and return OCR text only.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 1800,
        }
        response = httpx.post(
            get_external_llm_chat_completions_url(),
            json=request_payload,
            headers=headers,
            timeout=settings.external_llm_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        responses.append(payload)
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )
        text = (content or "").strip()
        if len(image_paths) > 1:
            page_texts.append(f"## Page {index}\n{text}")
        else:
            page_texts.append(text)

    return "\n\n".join(page_texts).strip(), {"pages": responses, "engine": "external_api_ocr"}


def _run_external_api_ocr(file_path: str, use_grayscale: bool = True) -> tuple[str, dict]:
    with tempfile.TemporaryDirectory() as tmpdir:
        suffix = Path(file_path).suffix.lower()
        if suffix == ".pdf":
            image_paths = _render_pdf_pages_for_glm(file_path, tmpdir, use_grayscale)
        elif suffix in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}:
            prepared_path = _preprocess_to_grayscale(file_path, tmpdir) if use_grayscale else file_path
            image_paths = [prepared_path]
        else:
            raise RuntimeError("External API OCR supports only PDF or image inputs")
        if not image_paths:
            raise RuntimeError("External API OCR produced no page images")
        return _run_external_api_ocr_on_images(image_paths)


def _build_system_prompt(document_type: str) -> str:
    return (
        f"You are a JSON extraction engine for Korean {document_type} documents. "
        "Output ONLY one JSON object with no explanation, no markdown fences, and no extra keys. "
        "Follow the exact schema the user provides. "
        "This OCR text often contains HTML tables from Korean transaction statements. "
        "Use table structure and left/right column position, not only nearby words. "
        "For the top party-information table, treat the LEFT block as seller/vendor and the RIGHT block as buyer/customer unless the text clearly contradicts that. "
        "When labels like 사업자번호 or 상호 appear twice in the same row, the left value maps to vendor fields and the right value maps to buyer fields. "
        "Map fields as follows: vendor_name=공급하는자/판매자/좌측 상호, vendor_reg_no=좌측 사업자번호, buyer_name=공급받는자/구매자/우측 상호, buyer_reg_no=우측 사업자번호. "
        "Do not confuse 성명 with company name. 성명 is a person name and should not replace vendor_name or buyer_name. "
        "For 거래일자-NO, split the date-like part into issue_date and the trailing number-like part into invoice_number when possible. "
        "For item rows, use the repeating columns 순번, 품목 및 규격, 수량, 단가, 금액/공급가액. "
        "Strip commas, hyphens, spaces, and currency symbols from numeric values, but preserve hyphen format for business registration numbers. "
        "Use null for missing values. Default currency is KRW. "
        "If the OCR text contains enough evidence in the table, prefer that evidence over generic assumptions."
    )


def _build_user_prompt(document_type: str, ocr_text: str) -> str:
    return (
        f"Korean {document_type} OCR text:\n\n"
        f"{ocr_text}\n\n"
        "Extraction rules:\n"
        "- Read the top table as party information first.\n"
        "- Left-side 사업자번호/상호 belongs to vendor fields.\n"
        "- Right-side 사업자번호/상호 belongs to buyer fields.\n"
        "- Ignore 성명 unless no company name exists.\n"
        "- If 공급가액/세액/합계금액 are not explicitly labeled, leave them null rather than guessing from totals unless the document clearly shows a final total row.\n"
        "- Extract every item row that has 순번 and 품목 및 규격.\n"
        "- issue_date must be YYYY-MM-DD.\n"
        "- vendor_reg_no and buyer_reg_no should keep business-number formatting like 000-00-00000 when present.\n\n"
        "Return this JSON and nothing else:\n"
        '{"fields":{"vendor_name":null,"vendor_reg_no":null,"buyer_name":null,"buyer_reg_no":null,'
        '"issue_date":null,"supply_amount":null,'
        '"tax_amount":null,"total_amount":null,"currency":"KRW",'
        '"payment_method":null,"invoice_number":null,"receipt_number":null},'
        '"items":[{"line_no":1,"item_name":"","quantity":null,"unit_price":null,"line_amount":null,"tax_amount":null,"total_amount":null}]}'
    )


def _extract_fields_with_ollama(
    *, model_name: str, document_type: str, ocr_text: str
) -> tuple[dict, dict]:
    clean_text = _strip_html_for_llm(ocr_text)
    request_payload = {
        "model": model_name,
        "system": _build_system_prompt(document_type),
        "prompt": _build_user_prompt(document_type, clean_text),
        "stream": False,
        "keep_alive": settings.ollama_keep_alive_value,
        "think": False,
        "options": {"num_predict": 1500},
    }
    response = httpx.post(
        f"{settings.ollama_base_url}/api/generate",
        json=request_payload,
        timeout=settings.ollama_timeout_seconds,
    )
    response.raise_for_status()
    llm_payload = response.json()
    model_response = llm_payload.get("response", "")
    parsed = normalize_ocr_payload(extract_json_block(model_response), raw_text=ocr_text)
    parsed["raw_text"] = ocr_text
    return parsed, llm_payload


def _extract_fields_with_external_api(
    *, model_name: str, document_type: str, ocr_text: str
) -> tuple[dict, dict]:
    clean_text = _strip_html_for_llm(ocr_text)
    headers = {"Content-Type": "application/json"}
    actual_api_key = get_external_llm_api_key()
    if actual_api_key:
        headers["Authorization"] = f"Bearer {actual_api_key}"

    request_payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": _build_system_prompt(document_type)},
            {"role": "user", "content": _build_user_prompt(document_type, clean_text)},
        ],
        "temperature": 0,
        "max_tokens": 1800,
    }
    response = httpx.post(
        get_external_llm_chat_completions_url(),
        json=request_payload,
        headers=headers,
        timeout=settings.external_llm_timeout_seconds,
    )
    response.raise_for_status()
    llm_payload = response.json()
    model_response = (
        llm_payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if isinstance(model_response, list):
        model_response = "\n".join(
            part.get("text", "")
            for part in model_response
            if isinstance(part, dict)
        )
    parsed = normalize_ocr_payload(extract_json_block(model_response), raw_text=ocr_text)
    parsed["raw_text"] = ocr_text
    return parsed, llm_payload


def extract_fields_with_llm(
    *, model_name: str, document_type: str, ocr_text: str
) -> tuple[dict, dict]:
    active_config = get_active_llm_config()
    if active_config["llm_backend"] == "external_api":
        return _extract_fields_with_external_api(
            model_name=model_name,
            document_type=document_type,
            ocr_text=ocr_text,
        )
    return _extract_fields_with_ollama(
        model_name=model_name,
        document_type=document_type,
        ocr_text=ocr_text,
    )


def run_ocr_with_model(
    *,
    model_name: str,
    file_path: str,
    document_type: str,
    use_grayscale: bool = True,
    gpu_id: str | None = None,
) -> dict:
    active_config = get_active_llm_config()
    ocr_backend = active_config.get("ocr_backend") or settings.ocr_backend
    ocr_model = active_config.get("ocr_model") or settings.default_ocr_model
    if ocr_backend == "glm_ocr":
        ocr_text, ocr_payload = _run_glm_ocr(file_path, use_grayscale=use_grayscale)
    else:
        ocr_text = _run_paddleocr_vl(
            file_path,
            use_grayscale=use_grayscale,
            gpu_id=gpu_id,
        )
        ocr_payload = {
            "engine": "paddleocr_vl",
            "model": settings.paddleocr_vl_model,
            "gpu_id": gpu_id,
        }

    parsed, llm_payload = extract_fields_with_llm(
        model_name=model_name,
        document_type=document_type,
        ocr_text=ocr_text,
    )

    parsed = merge_with_fallback(parsed, fallback_parse_from_text(ocr_text))

    return {
        "model_name": model_name,
        "raw_text": parsed["raw_text"],
        "fields": parsed["fields"],
        "items": parsed["items"],
        "llm_response_json": llm_payload,
        "ocr_response_json": ocr_payload,
        "ocr_backend": ocr_backend,
        "ocr_model": ocr_model,
        "use_grayscale": use_grayscale,
        "gpu_id": gpu_id,
    }


def _summarize_comparison_result(result: dict, label: str, backend: str) -> dict:
    fields = result.get("fields") or {}
    items = result.get("items") or []
    return {
        "backend": backend,
        "label": label,
        "raw_text": result.get("raw_text") or "",
        "fields": fields,
        "items": items,
        "item_count": len(items),
        "summary": {
            "vendor_name": fields.get("vendor_name"),
            "vendor_reg_no": fields.get("vendor_reg_no"),
            "buyer_name": fields.get("buyer_name"),
            "buyer_reg_no": fields.get("buyer_reg_no"),
            "issue_date": fields.get("issue_date"),
            "invoice_number": fields.get("invoice_number"),
            "supply_amount": fields.get("supply_amount"),
            "tax_amount": fields.get("tax_amount"),
            "total_amount": fields.get("total_amount"),
        },
    }


def compare_ocr_engines(
    *,
    model_name: str,
    file_path: str,
    document_type: str,
    use_grayscale: bool = True,
) -> dict:
    comparisons: list[dict] = []

    paddle_text = _run_paddleocr_vl(file_path, use_grayscale=use_grayscale)
    paddle_parsed, _ = extract_fields_with_llm(
        model_name=model_name,
        document_type=document_type,
        ocr_text=paddle_text,
    )
    paddle_result = merge_with_fallback(paddle_parsed, fallback_parse_from_text(paddle_text))
    comparisons.append(
        _summarize_comparison_result(
            paddle_result,
            label="PaddleOCR-VL",
            backend="paddleocr_vl",
        )
    )

    active_config = get_active_llm_config()
    if active_config.get("external_api_configured"):
        external_text, _ = _run_external_api_ocr(file_path, use_grayscale=use_grayscale)
        external_parsed, _ = extract_fields_with_llm(
            model_name=model_name,
            document_type=document_type,
            ocr_text=external_text,
        )
        external_result = merge_with_fallback(external_parsed, fallback_parse_from_text(external_text))
        comparisons.append(
            _summarize_comparison_result(
                external_result,
                label="Gemini OCR",
                backend="external_api_ocr",
            )
        )

    return {
        "field_model_name": model_name,
        "use_grayscale": use_grayscale,
        "comparisons": comparisons,
    }
