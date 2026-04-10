from pathlib import Path

from app.db.session import db_cursor


COMPANY_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
DOCUMENT_ID = "33333333-3333-3333-3333-333333333333"
DEMO_FILE_PATH = "/home/suesoo/invoice-storage/documents/demo-invoice.txt"


def main() -> None:
    demo_file = Path(DEMO_FILE_PATH)
    demo_file.parent.mkdir(parents=True, exist_ok=True)
    demo_file.write_text(
        "\n".join(
            [
                "Vendor: Demo Supplier Co.",
                "Issue Date: 2026-04-08",
                "Currency: KRW",
                "Supply Amount: 100000",
                "Tax Amount: 10000",
                "Total Amount: 110000",
                "Invoice Number: INV-2026-0001",
                "Items:",
                "1. Thermal Paper | qty 10 | unit price 5000 | line amount 50000",
                "2. Label Sticker | qty 20 | unit price 2500 | line amount 50000",
            ]
        ),
        encoding="utf-8",
    )

    with db_cursor() as (_, cursor):
        cursor.execute(
            "DELETE FROM document_audit_logs WHERE document_id = %s",
            (DOCUMENT_ID,),
        )
        cursor.execute(
            "DELETE FROM document_items WHERE document_id = %s",
            (DOCUMENT_ID,),
        )
        cursor.execute(
            "DELETE FROM document_ocr_raw WHERE document_id = %s",
            (DOCUMENT_ID,),
        )
        cursor.execute(
            "DELETE FROM document_jobs WHERE document_id = %s",
            (DOCUMENT_ID,),
        )

        cursor.execute(
            """
            INSERT INTO companies (id, name, code, status)
            VALUES (%s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE name = VALUES(name), updated_at = CURRENT_TIMESTAMP
            """,
            (COMPANY_ID, "Demo Company", "demo-company"),
        )

        cursor.execute(
            """
            INSERT INTO users (id, company_id, wp_user_id, email, name, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE email = VALUES(email), name = VALUES(name), updated_at = CURRENT_TIMESTAMP
            """,
            (USER_ID, COMPANY_ID, 1, "suesoo@nusome.co.kr", "suesoo"),
        )

        cursor.execute(
            """
            INSERT INTO documents (
                id, company_id, created_by, type, status, original_filename,
                file_path, file_size, mime_type, currency
            )
            VALUES (%s, %s, %s, 'invoice', 'uploaded', %s, %s, %s, %s, 'KRW')
            ON DUPLICATE KEY UPDATE
                file_path = VALUES(file_path),
                status = 'uploaded',
                vendor_name = NULL,
                issue_date = NULL,
                supply_amount = NULL,
                tax_amount = NULL,
                total_amount = NULL,
                payment_method = NULL,
                invoice_number = NULL,
                receipt_number = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                DOCUMENT_ID,
                COMPANY_ID,
                USER_ID,
                "demo-invoice.txt",
                DEMO_FILE_PATH,
                1024,
                "text/plain",
            ),
        )

    print("seeded")
    print(f"company_id={COMPANY_ID}")
    print(f"user_id={USER_ID}")
    print(f"document_id={DOCUMENT_ID}")


if __name__ == "__main__":
    main()
