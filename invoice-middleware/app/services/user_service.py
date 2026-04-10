from app.db.session import db_cursor


def resolve_user_mapping(*, wp_user_id: int, email: str | None = None) -> dict | None:
    with db_cursor(dictionary=True) as (_, cursor):
        if email:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.company_id,
                    u.wp_user_id,
                    u.email,
                    u.name,
                    c.name AS company_name,
                    c.code AS company_code
                FROM users u
                JOIN companies c ON c.id = u.company_id
                WHERE u.wp_user_id = %s
                  AND u.email = %s
                  AND u.status = 'active'
                  AND c.status = 'active'
                LIMIT 1
                """,
                (wp_user_id, email),
            )
            user = cursor.fetchone()
            if user:
                return user

        cursor.execute(
            """
            SELECT
                u.id,
                u.company_id,
                u.wp_user_id,
                u.email,
                u.name,
                c.name AS company_name,
                c.code AS company_code
            FROM users u
            JOIN companies c ON c.id = u.company_id
            WHERE u.wp_user_id = %s
              AND u.status = 'active'
              AND c.status = 'active'
            LIMIT 1
            """,
            (wp_user_id,),
        )
        return cursor.fetchone()
