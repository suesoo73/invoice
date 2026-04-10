from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import verify_internal_token
from app.services.user_service import resolve_user_mapping

router = APIRouter()


@router.get("/resolve-user")
def resolve_user(
    wp_user_id: int,
    email: str | None = None,
    _: None = Depends(verify_internal_token),
) -> dict:
    user = resolve_user_mapping(wp_user_id=wp_user_id, email=email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mapped app user not found",
        )
    return {"user": user}
