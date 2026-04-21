from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import verify_internal_token
from app.schemas.auth import LocalCompanyCreateRequest, LocalLoginRequest, LocalUserCreateRequest, LocalUserUpdateRequest
from app.services.local_auth_service import (
    authenticate_local_user,
    create_company,
    create_local_user,
    delete_local_user,
    list_local_users,
    resolve_company_by_registration_no,
    search_companies,
    update_local_user,
)

router = APIRouter()


@router.post("/login")
def local_login(
    payload: LocalLoginRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    user = authenticate_local_user(payload.login_id, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login credentials")
    return {"user": user}


@router.get("/users")
def get_local_users(
    _: None = Depends(verify_internal_token),
) -> dict:
    return {"items": list_local_users()}


@router.get("/companies/resolve")
def resolve_company(
    company_id: str,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        company = resolve_company_by_registration_no(company_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return {"company": company}


@router.get("/companies")
def get_companies(
    query: str,
    limit: int = 10,
    _: None = Depends(verify_internal_token),
) -> dict:
    return {"items": search_companies(query, limit=min(max(limit, 1), 30))}


@router.post("/companies")
def create_company_route(
    payload: LocalCompanyCreateRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return {"company": create_company(**payload.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/users")
def create_local_user_route(
    payload: LocalUserCreateRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return {"user": create_local_user(**payload.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/users/{user_id}")
def update_local_user_route(
    user_id: str,
    payload: LocalUserUpdateRequest,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return {"user": update_local_user(user_id, **payload.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/users/{user_id}")
def delete_local_user_route(
    user_id: str,
    _: None = Depends(verify_internal_token),
) -> dict:
    try:
        return {"user": delete_local_user(user_id)}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
