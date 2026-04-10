import argparse
import uuid

import mysql.connector

from app.db.session import db_cursor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync WordPress users into invoice_ocr users")
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--company-code", required=True)
    parser.add_argument("--wp-host", required=True)
    parser.add_argument("--wp-port", type=int, default=3306)
    parser.add_argument("--wp-db", required=True)
    parser.add_argument("--wp-user", required=True)
    parser.add_argument("--wp-password", required=True)
    parser.add_argument("--email-domain", default=None)
    parser.add_argument("--user-ids", default=None, help="Comma-separated WordPress user IDs")
    return parser.parse_args()


def fetch_wp_users(args: argparse.Namespace) -> list[dict]:
    connection = mysql.connector.connect(
        host=args.wp_host,
        port=args.wp_port,
        user=args.wp_user,
        password=args.wp_password,
        database=args.wp_db,
        autocommit=True,
    )
    cursor = connection.cursor(dictionary=True)

    conditions: list[str] = []
    params: list[object] = []

    if args.email_domain:
        conditions.append("user_email LIKE %s")
        params.append(f"%@{args.email_domain}")

    if args.user_ids:
        user_ids = [item.strip() for item in args.user_ids.split(",") if item.strip()]
        placeholders = ", ".join(["%s"] * len(user_ids))
        conditions.append(f"ID IN ({placeholders})")
        params.extend(user_ids)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    cursor.execute(
        f"""
        SELECT ID, user_login, user_email, display_name
        FROM wp_users
        {where_clause}
        ORDER BY ID
        """,
        tuple(params),
    )
    rows = cursor.fetchall()
    cursor.close()
    connection.close()
    return rows


def ensure_company(company_id: str, company_name: str, company_code: str) -> None:
    with db_cursor() as (_, cursor):
        cursor.execute(
            """
            INSERT INTO companies (id, name, code, status)
            VALUES (%s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE
                name = VALUES(name),
                code = VALUES(code),
                status = 'active',
                updated_at = CURRENT_TIMESTAMP
            """,
            (company_id, company_name, company_code),
        )


def upsert_user(company_id: str, wp_user: dict) -> tuple[str, bool]:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT id
            FROM users
            WHERE company_id = %s AND wp_user_id = %s
            LIMIT 1
            """,
            (company_id, wp_user["ID"]),
        )
        existing = cursor.fetchone()
        if existing:
            user_id = existing["id"]
            created = False
        else:
            user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"invoice_ocr:{company_id}:{wp_user['ID']}"))
            created = True

        cursor.execute(
            """
            INSERT INTO users (id, company_id, wp_user_id, email, name, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            ON DUPLICATE KEY UPDATE
                email = VALUES(email),
                name = VALUES(name),
                status = 'active',
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                company_id,
                wp_user["ID"],
                wp_user["user_email"],
                wp_user["display_name"] or wp_user["user_login"],
            ),
        )

    return user_id, created


def main() -> None:
    args = parse_args()
    ensure_company(args.company_id, args.company_name, args.company_code)
    wp_users = fetch_wp_users(args)

    print(f"company_id={args.company_id}")
    print(f"company_name={args.company_name}")
    print(f"matched_users={len(wp_users)}")

    for wp_user in wp_users:
        user_id, created = upsert_user(args.company_id, wp_user)
        print(
            f"{'created' if created else 'updated'} "
            f"app_user_id={user_id} wp_user_id={wp_user['ID']} "
            f"login={wp_user['user_login']} email={wp_user['user_email']}"
        )


if __name__ == "__main__":
    main()
