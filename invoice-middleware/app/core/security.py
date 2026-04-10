from fastapi import Header, HTTPException, status

from app.core.config import settings


def verify_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    if x_internal_token != settings.internal_shared_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )
