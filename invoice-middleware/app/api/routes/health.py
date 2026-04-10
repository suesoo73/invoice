from fastapi import APIRouter

from app.db.session import test_connection

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    return {
        "status": "ok",
        "database": "ok" if test_connection() else "error",
    }
