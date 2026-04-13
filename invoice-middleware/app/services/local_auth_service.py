import base64
import hashlib
import hmac
import os
import re
import uuid
import unicodedata

from app.db.session import db_cursor

_PBKDF2_ITERATIONS = 600000
_REGISTRATION_NO_DIGITS = 10


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def _verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations, salt_b64, hash_b64 = password_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(hash_b64)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(derived, expected)


def _generate_local_wp_user_id() -> int:
    # Preserve legacy schema compatibility while ensuring local-only accounts
    # don't collide on the existing (company_id, wp_user_id) unique key.
    return -int.from_bytes(os.urandom(6), "big")


def _normalize_registration_no(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != _REGISTRATION_NO_DIGITS:
        raise ValueError("company_id must be in 111-11-11111 format")
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def _get_company_by_registration_no(cursor, registration_no: str) -> dict | None:
    cursor.execute(
        """
        SELECT id, name, code, registration_no
        FROM companies
        WHERE registration_no = %s
          AND status = 'active'
        LIMIT 1
        """,
        (registration_no,),
    )
    return cursor.fetchone()


def _slugify_company_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "company"


def _generate_company_code(cursor, name: str, registration_no: str) -> str:
    digits = re.sub(r"\D", "", registration_no)
    base = f"{_slugify_company_name(name)}-{digits}"[:96].strip("-") or f"company-{digits}"
    candidate = base
    suffix = 2
    while True:
        cursor.execute("SELECT id FROM companies WHERE code = %s LIMIT 1", (candidate,))
        if not cursor.fetchone():
            return candidate
        candidate = f"{base[:88]}-{suffix}"
        suffix += 1


def authenticate_local_user(login_id: str, password: str) -> dict | None:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT
                u.id,
                u.company_id,
                u.login_id,
                u.email,
                u.name,
                u.is_operator,
                u.password_hash,
                c.name AS company_name,
                c.code AS company_code,
                c.registration_no AS company_registration_no
            FROM users u
            JOIN companies c ON c.id = u.company_id
            WHERE u.login_id = %s
              AND u.status = 'active'
              AND c.status = 'active'
            LIMIT 1
            """,
            (login_id,),
        )
        user = cursor.fetchone()
        if not user or not _verify_password(password, user.get("password_hash")):
            return None

        cursor.execute(
            """
            UPDATE users
            SET last_login_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (user["id"],),
        )
        user.pop("password_hash", None)
        return user


def list_local_users() -> list[dict]:
    with db_cursor(dictionary=True) as (_, cursor):
        cursor.execute(
            """
            SELECT
                u.id,
                u.company_id,
                u.login_id,
                u.email,
                u.name,
                u.is_operator,
                u.status,
                u.last_login_at,
                c.name AS company_name,
                c.code AS company_code,
                c.registration_no AS company_registration_no
            FROM users u
            JOIN companies c ON c.id = u.company_id
            ORDER BY u.is_operator DESC, c.name ASC, u.login_id ASC
            """
        )
        return cursor.fetchall()


def _get_local_user_by_id(cursor, user_id: str) -> dict | None:
    cursor.execute(
        """
        SELECT
            u.id,
            u.company_id,
            u.login_id,
            u.email,
            u.name,
            u.is_operator,
            u.status,
            u.last_login_at,
            c.name AS company_name,
            c.code AS company_code,
            c.registration_no AS company_registration_no
        FROM users u
        JOIN companies c ON c.id = u.company_id
        WHERE u.id = %s
        LIMIT 1
        """,
        (user_id,),
    )
    return cursor.fetchone()


def resolve_company_by_registration_no(company_id: str) -> dict | None:
    registration_no = _normalize_registration_no(company_id)
    with db_cursor(dictionary=True) as (_, cursor):
        company = _get_company_by_registration_no(cursor, registration_no)
        if not company:
            return None
        return company


def search_companies(query: str, limit: int = 10) -> list[dict]:
    keyword = (query or "").strip()
    if not keyword:
        return []
    like = f"%{keyword}%"
    digits = re.sub(r"\D", "", keyword)
    reg_like = None
    if digits:
        formatted = format_registration_no_loose(digits)
        reg_like = f"%{formatted}%"

    with db_cursor(dictionary=True) as (_, cursor):
        if reg_like:
            cursor.execute(
                """
                SELECT id, name, code, registration_no
                FROM companies
                WHERE status = 'active'
                  AND (
                    registration_no LIKE %s
                    OR REPLACE(registration_no, '-', '') LIKE %s
                    OR name LIKE %s
                    OR code LIKE %s
                  )
                ORDER BY name ASC
                LIMIT %s
                """,
                (reg_like, f"%{digits}%", like, like, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, name, code, registration_no
                FROM companies
                WHERE status = 'active'
                  AND (name LIKE %s OR code LIKE %s)
                ORDER BY name ASC
                LIMIT %s
                """,
                (like, like, limit),
            )
        return cursor.fetchall()


def format_registration_no_loose(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")[:_REGISTRATION_NO_DIGITS]
    if len(digits) <= 3:
        return digits
    if len(digits) <= 5:
        return f"{digits[:3]}-{digits[3:]}"
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def create_company(*, registration_no: str, name: str) -> dict:
    normalized_registration_no = _normalize_registration_no(registration_no)
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValueError("company name is required")

    with db_cursor(dictionary=True) as (_, cursor):
        existing = _get_company_by_registration_no(cursor, normalized_registration_no)
        if existing:
            return existing

        company_id = str(uuid.uuid4())
        company_code = _generate_company_code(cursor, normalized_name, normalized_registration_no)
        cursor.execute(
            """
            INSERT INTO companies (id, name, code, registration_no, status)
            VALUES (%s, %s, %s, %s, 'active')
            """,
            (company_id, normalized_name, company_code, normalized_registration_no),
        )
        cursor.execute(
            """
            SELECT id, name, code, registration_no
            FROM companies
            WHERE id = %s
            LIMIT 1
            """,
            (company_id,),
        )
        created = cursor.fetchone()
        if not created:
            raise ValueError("Company creation failed")
        return created


def create_local_user(
    *,
    company_id: str,
    login_id: str,
    password: str,
    name: str,
    email: str,
    is_operator: bool,
) -> dict:
    registration_no = _normalize_registration_no(company_id)
    normalized_login = login_id.strip().lower()
    normalized_email = email.strip().lower()
    if not normalized_login:
        raise ValueError("login_id is required")
    if len(password or "") < 4:
        raise ValueError("password must be at least 4 characters")

    with db_cursor(dictionary=True) as (_, cursor):
        company = _get_company_by_registration_no(cursor, registration_no)
        if not company:
            raise ValueError("Company not found")

        cursor.execute("SELECT id FROM users WHERE login_id = %s LIMIT 1", (normalized_login,))
        if cursor.fetchone():
            raise ValueError("login_id already exists")

        user_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO users (
                id, company_id, wp_user_id, login_id, password_hash, email, name, is_operator, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active')
            """,
            (
                user_id,
                company["id"],
                _generate_local_wp_user_id(),
                normalized_login,
                _hash_password(password),
                normalized_email,
                name.strip(),
                1 if is_operator else 0,
            ),
        )

        cursor.execute(
            """
            SELECT
                u.id,
                u.company_id,
                u.login_id,
                u.email,
                u.name,
                u.is_operator,
                u.status,
                u.last_login_at,
                c.name AS company_name,
                c.code AS company_code,
                c.registration_no AS company_registration_no
            FROM users u
            JOIN companies c ON c.id = u.company_id
            WHERE u.id = %s
            LIMIT 1
            """,
            (user_id,),
        )
        created = cursor.fetchone()
        if not created:
            raise ValueError("User creation failed")
        return created


def update_local_user(
    user_id: str,
    *,
    company_id: str,
    login_id: str,
    password: str | None,
    name: str,
    email: str,
    is_operator: bool,
    status: str,
) -> dict:
    registration_no = _normalize_registration_no(company_id)
    normalized_login = login_id.strip().lower()
    normalized_email = email.strip().lower()
    normalized_name = name.strip()
    normalized_status = (status or "active").strip().lower()

    if not normalized_login:
        raise ValueError("login_id is required")
    if not normalized_name:
        raise ValueError("name is required")
    if normalized_status not in {"active", "inactive"}:
        raise ValueError("status must be active or inactive")
    if password and len(password) < 4:
        raise ValueError("password must be at least 4 characters")

    with db_cursor(dictionary=True) as (_, cursor):
        existing_user = _get_local_user_by_id(cursor, user_id)
        if not existing_user:
            raise ValueError("User not found")

        company = _get_company_by_registration_no(cursor, registration_no)
        if not company:
            raise ValueError("Company not found")

        cursor.execute(
            "SELECT id FROM users WHERE login_id = %s AND id <> %s LIMIT 1",
            (normalized_login, user_id),
        )
        if cursor.fetchone():
            raise ValueError("login_id already exists")

        if password:
            cursor.execute(
                """
                UPDATE users
                SET company_id = %s,
                    login_id = %s,
                    password_hash = %s,
                    email = %s,
                    name = %s,
                    is_operator = %s,
                    status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    company["id"],
                    normalized_login,
                    _hash_password(password),
                    normalized_email,
                    normalized_name,
                    1 if is_operator else 0,
                    normalized_status,
                    user_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE users
                SET company_id = %s,
                    login_id = %s,
                    email = %s,
                    name = %s,
                    is_operator = %s,
                    status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    company["id"],
                    normalized_login,
                    normalized_email,
                    normalized_name,
                    1 if is_operator else 0,
                    normalized_status,
                    user_id,
                ),
            )

        updated = _get_local_user_by_id(cursor, user_id)
        if not updated:
            raise ValueError("User update failed")
        return updated
