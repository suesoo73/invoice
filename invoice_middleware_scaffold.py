from pathlib import Path


FILES = {
    "invoice-middleware/app/main.py": """from fastapi import FastAPI\nfrom app.api.routes.health import router as health_router\nfrom app.api.routes.jobs import router as jobs_router\n\napp = FastAPI(title=\"invoice-middleware\")\napp.include_router(health_router)\napp.include_router(jobs_router, prefix=\"/internal/ocr\")\n""",
    "invoice-middleware/app/api/routes/health.py": """from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get(\"/health\")\ndef health_check() -> dict:\n    return {\"status\": \"ok\"}\n""",
    "invoice-middleware/app/api/routes/jobs.py": """from fastapi import APIRouter\nfrom pydantic import BaseModel\n\nrouter = APIRouter()\n\n\nclass OCRJobCreate(BaseModel):\n    document_id: str\n    company_id: str\n    file_path: str\n    document_type: str\n    requested_by: str | None = None\n\n\n@router.post(\"/jobs\")\ndef create_job(payload: OCRJobCreate) -> dict:\n    return {\n        \"status\": \"queued\",\n        \"document_id\": payload.document_id,\n    }\n""",
    "invoice-middleware/app/core/config.py": """from pydantic_settings import BaseSettings\n\n\nclass Settings(BaseSettings):\n    app_name: str = \"invoice-middleware\"\n    app_env: str = \"production\"\n    app_host: str = \"0.0.0.0\"\n    app_port: int = 8080\n    mysql_host: str\n    mysql_port: int = 3306\n    mysql_database: str\n    mysql_user: str\n    mysql_password: str\n    ollama_base_url: str = \"http://127.0.0.1:11434\"\n    ollama_model: str = \"gemma4:e4b\"\n    storage_root: str = \"/storage\"\n    internal_shared_token: str\n    ocr_max_retries: int = 2\n\n    class Config:\n        env_file = \".env\"\n        case_sensitive = False\n\n\nsettings = Settings()\n""",
    "invoice-middleware/app/workers/ocr_worker.py": """def run_worker() -> None:\n    print(\"ocr worker placeholder\")\n\n\nif __name__ == \"__main__\":\n    run_worker()\n""",
}


def main() -> None:
    for relative_path, content in FILES.items():
        path = Path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"created {path}")


if __name__ == "__main__":
    main()
