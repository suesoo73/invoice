from io import BytesIO

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

from app.db.session import db_cursor


def _filters(company_id: str, date_from: str | None, date_to: str | None) -> tuple[str, list[object]]:
    filters = ["company_id = %s", "status = 'completed'", "deleted_at IS NULL"]
    params: list[object] = [company_id]
    if date_from:
        filters.append("issue_date >= %s")
        params.append(date_from)
    if date_to:
        filters.append("issue_date <= %s")
        params.append(date_to)
    return " AND ".join(filters), params


def _query_report_summary(
    *,
    company_id: str,
    period_type: str = "monthly",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    if period_type not in {"monthly", "quarterly"}:
        raise ValueError("Unsupported period_type")

    where_clause, params = _filters(company_id, date_from, date_to)
    period_expr = (
        "DATE_FORMAT(issue_date, '%Y-%m-01')"
        if period_type == "monthly"
        else "CONCAT(YEAR(issue_date), '-Q', QUARTER(issue_date))"
    )

    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            f"""
            SELECT
                COUNT(*) AS document_count,
                IFNULL(SUM(supply_amount), 0) AS supply_amount_sum,
                IFNULL(SUM(tax_amount), 0) AS tax_amount_sum,
                IFNULL(SUM(total_amount), 0) AS total_amount_sum
            FROM documents
            WHERE {where_clause}
            """,
            tuple(params),
        )
        summary = cursor.fetchone()

        cursor.execute(
            f"""
            SELECT
                {period_expr} AS period,
                COUNT(*) AS document_count,
                IFNULL(SUM(total_amount), 0) AS total_amount_sum
            FROM documents
            WHERE {where_clause}
              AND issue_date IS NOT NULL
            GROUP BY {period_expr}
            ORDER BY MIN(issue_date) DESC
            LIMIT 12
            """,
            tuple(params),
        )
        periods = cursor.fetchall()

        cursor.execute(
            f"""
            SELECT
                vendor_name,
                COUNT(*) AS document_count,
                IFNULL(SUM(total_amount), 0) AS total_amount_sum
            FROM documents
            WHERE {where_clause}
              AND vendor_name IS NOT NULL
            GROUP BY vendor_name
            ORDER BY total_amount_sum DESC, vendor_name ASC
            LIMIT 10
            """,
            tuple(params),
        )
        vendors = cursor.fetchall()

        cursor.execute(
            f"""
            SELECT
                i.item_name,
                COUNT(*) AS line_count,
                IFNULL(SUM(i.line_amount), 0) AS line_amount_sum
            FROM document_items i
            JOIN documents d ON d.id = i.document_id
            WHERE {where_clause.replace('company_id', 'd.company_id').replace('status', 'd.status').replace('deleted_at', 'd.deleted_at')}
              AND i.item_name IS NOT NULL
            GROUP BY i.item_name
            ORDER BY line_amount_sum DESC, i.item_name ASC
            LIMIT 10
            """,
            tuple(params),
        )
        items = cursor.fetchall()

    return {
        "summary": summary,
        "periods": periods,
        "vendors": vendors,
        "items": items,
        "filters": {
            "company_id": company_id,
            "period_type": period_type,
            "date_from": date_from,
            "date_to": date_to,
        },
    }


def get_report_summary(
    *,
    company_id: str,
    period_type: str = "monthly",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    return _query_report_summary(
        company_id=company_id,
        period_type=period_type,
        date_from=date_from,
        date_to=date_to,
    )


def build_report_xlsx(
    *,
    company_id: str,
    period_type: str = "monthly",
    date_from: str | None = None,
    date_to: str | None = None,
) -> BytesIO:
    report = _query_report_summary(
        company_id=company_id,
        period_type=period_type,
        date_from=date_from,
        date_to=date_to,
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Metric", "Value"])
    for key, value in report["summary"].items():
        ws.append([key, value])

    ws2 = wb.create_sheet("Periods")
    ws2.append(["Period", "Document Count", "Total Amount"])
    for row in report["periods"]:
        ws2.append([row["period"], row["document_count"], row["total_amount_sum"]])

    ws3 = wb.create_sheet("Vendors")
    ws3.append(["Vendor", "Document Count", "Total Amount"])
    for row in report["vendors"]:
        ws3.append([row["vendor_name"], row["document_count"], row["total_amount_sum"]])

    ws4 = wb.create_sheet("Items")
    ws4.append(["Item", "Line Count", "Line Amount"])
    for row in report["items"]:
        ws4.append([row["item_name"], row["line_count"], row["line_amount_sum"]])

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def build_report_pdf(
    *,
    company_id: str,
    period_type: str = "monthly",
    date_from: str | None = None,
    date_to: str | None = None,
) -> BytesIO:
    report = _query_report_summary(
        company_id=company_id,
        period_type=period_type,
        date_from=date_from,
        date_to=date_to,
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Invoice OCR Report", styles["Title"]),
        Paragraph(f"Period type: {period_type}", styles["Normal"]),
        Paragraph(f"Date range: {date_from or '-'} ~ {date_to or '-'}", styles["Normal"]),
        Spacer(1, 10),
    ]

    def add_table(title: str, rows: list[list[object]]) -> None:
        story.append(Paragraph(title, styles["Heading2"]))
        table = Table(rows, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ead9c6")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d7c8b5")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 10))

    add_table("Summary", [["Metric", "Value"]] + [[k, v] for k, v in report["summary"].items()])
    add_table("Periods", [["Period", "Document Count", "Total Amount"]] + [[r["period"], r["document_count"], r["total_amount_sum"]] for r in report["periods"]])
    add_table("Vendors", [["Vendor", "Document Count", "Total Amount"]] + [[r["vendor_name"], r["document_count"], r["total_amount_sum"]] for r in report["vendors"]])
    add_table("Items", [["Item", "Line Count", "Line Amount"]] + [[r["item_name"], r["line_count"], r["line_amount_sum"]] for r in report["items"]])

    doc.build(story)
    buffer.seek(0)
    return buffer
