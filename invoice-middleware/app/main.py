from app.api.routes.auth import router as auth_router
from app.api.routes.local_auth import router as local_auth_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.reports import router as reports_router
from app.core.config import settings

app = FastAPI(title="invoice-middleware")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_app_allowed_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router, prefix="/internal/auth", tags=["auth"])
app.include_router(local_auth_router, prefix="/internal/local-auth", tags=["local-auth"])
app.include_router(jobs_router, prefix="/internal/ocr", tags=["ocr"])
app.include_router(reports_router, prefix="/internal/reports", tags=["reports"])
