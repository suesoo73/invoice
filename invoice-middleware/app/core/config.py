from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "invoice-middleware"
    app_env: str = "production"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    mysql_host: str
    mysql_port: int = 3306
    mysql_database: str
    mysql_user: str
    mysql_password: str
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9B"
    ollama_allowed_models: str = "qwen3.5:9B,gemma4:e4b"
    ocr_max_pdf_pages: int = 4
    paddleocr_vl_bin: str = "/home/suesoo/paddleocr-vl-test/.venv/bin/paddleocr"
    paddleocr_vl_model: str = "PaddleOCR-VL-1.5-0.9B"
    paddleocr_vl_device: str = "gpu"
    paddleocr_vl_max_pixels: int = 524288
    paddleocr_vl_min_pixels: int = 196608
    paddleocr_vl_max_new_tokens: int = 768
    storage_root: str = "/storage"
    web_app_allowed_origin: str = "https://invoice.nusome.co.kr"
    internal_shared_token: str
    ocr_max_retries: int = 2
    worker_stale_after_seconds: int = 600

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def allowed_models(self) -> list[str]:
        return [item.strip() for item in self.ollama_allowed_models.split(",") if item.strip()]


settings = Settings()
