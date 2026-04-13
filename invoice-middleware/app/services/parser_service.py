import json
import re
from datetime import datetime


def _coerce_number(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _find_labeled_value(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}\s*:\s*(.+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _parse_issue_date(text: str) -> str | None:
    labeled = _find_labeled_value(text, "Issue Date")
    if labeled:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", labeled)
        if match:
            return match.group(1)
    return None


def coerce_issue_date(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()

    text = str(value).strip()
    if not text:
        return None

    normalized = text
    digit_match = re.search(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})", text)
    if digit_match:
        normalized = f"{digit_match.group(1)}-{int(digit_match.group(2)):02d}-{int(digit_match.group(3)):02d}"

    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return datetime.now().date().isoformat()


def _parse_items_from_text(text: str) -> list[dict]:
    items: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not re.match(r"^\d+\.\s+", line):
            continue

        line_no_match = re.match(r"^(\d+)\.\s+", line)
        line_no = int(line_no_match.group(1)) if line_no_match else len(items) + 1
        parts = [part.strip() for part in line.split("|")]
        item_name = re.sub(r"^\d+\.\s*", "", parts[0]).strip() if parts else ""

        quantity = None
        unit_price = None
        line_amount = None
        for part in parts[1:]:
            lower = part.lower()
            if "qty" in lower:
                quantity = _coerce_number(part)
            elif "unit price" in lower:
                unit_price = _coerce_number(part)
            elif "line amount" in lower:
                line_amount = _coerce_number(part)

        items.append(
            {
                "line_no": line_no,
                "item_name": item_name,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_amount": line_amount,
                "tax_amount": None,
                "total_amount": line_amount,
            }
        )

    return items


def fallback_parse_from_text(text: str) -> dict:
    return {
        "raw_text": text,
        "fields": {
            "vendor_name": _find_labeled_value(text, "Vendor"),
            "vendor_reg_no": None,
            "buyer_name": None,
            "buyer_reg_no": None,
            "issue_date": coerce_issue_date(_parse_issue_date(text)),
            "supply_amount": _coerce_number(_find_labeled_value(text, "Supply Amount")),
            "tax_amount": _coerce_number(_find_labeled_value(text, "Tax Amount")),
            "total_amount": _coerce_number(_find_labeled_value(text, "Total Amount")),
            "currency": _find_labeled_value(text, "Currency") or "KRW",
            "payment_method": _find_labeled_value(text, "Payment Method"),
            "invoice_number": _find_labeled_value(text, "Invoice Number"),
            "receipt_number": _find_labeled_value(text, "Receipt Number"),
        },
        "items": _parse_items_from_text(text),
    }


def merge_with_fallback(primary: dict, fallback: dict) -> dict:
    merged_fields = {}
    for key, fallback_value in fallback["fields"].items():
        primary_value = primary["fields"].get(key)
        merged_fields[key] = primary_value if primary_value not in (None, "", []) else fallback_value

    merged_items = primary["items"] if primary["items"] else fallback["items"]

    return {
        "raw_text": primary["raw_text"] if primary["raw_text"] not in (None, "") else fallback["raw_text"],
        "fields": merged_fields,
        "items": merged_items,
    }


def extract_json_block(text: str) -> dict:
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    object_match = re.search(r"(\{.*\})", text, re.DOTALL)
    if object_match:
        return json.loads(object_match.group(1))

    raise ValueError("No JSON object found in model response")


def normalize_ocr_payload(payload: dict, raw_text: str) -> dict:
    fields = payload.get("fields") or {}
    items = payload.get("items") or []

    normalized_items = []
    for index, item in enumerate(items, start=1):
        normalized_items.append(
            {
                "line_no": item.get("line_no") or index,
                "item_name": item.get("item_name") or "",
                "quantity": _coerce_number(item.get("quantity")),
                "unit_price": _coerce_number(item.get("unit_price")),
                "line_amount": _coerce_number(item.get("line_amount")),
                "tax_amount": _coerce_number(item.get("tax_amount") or item.get("vat_amount")),
                "total_amount": _coerce_number(item.get("total_amount") or item.get("line_total_amount")),
            }
        )

    return {
        "raw_text": payload.get("raw_text") or raw_text,
        "fields": {
            "vendor_name": fields.get("vendor_name"),
            "vendor_reg_no": fields.get("vendor_reg_no"),
            "buyer_name": fields.get("buyer_name"),
            "buyer_reg_no": fields.get("buyer_reg_no"),
            "issue_date": coerce_issue_date(fields.get("issue_date")),
            "supply_amount": _coerce_number(fields.get("supply_amount")),
            "tax_amount": _coerce_number(fields.get("tax_amount")),
            "total_amount": _coerce_number(fields.get("total_amount")),
            "currency": fields.get("currency") or "KRW",
            "payment_method": fields.get("payment_method"),
            "invoice_number": fields.get("invoice_number"),
            "receipt_number": fields.get("receipt_number"),
        },
        "items": normalized_items,
    }
