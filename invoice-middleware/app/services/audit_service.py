import json
import uuid


def insert_audit_log(cursor, *, company_id: str, document_id: str, user_id: str | None, action: str, payload: dict | None) -> None:
    cursor.execute(
        """
        INSERT INTO document_audit_logs (
            id, company_id, document_id, user_id, action, payload_json
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            company_id,
            document_id,
            user_id,
            action,
            json.dumps(payload or {}),
        ),
    )
