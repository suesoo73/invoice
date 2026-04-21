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
    llm_backend: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_allowed_models: str = "qwen3.5:9B,gemma4:e4b"
    ollama_keep_alive: str = "-1"
    ocr_backend: str = "paddleocr_vl"
    glm_ocr_model: str = "glm-ocr:latest"
    glm_ocr_allowed_models: str = "glm-ocr:latest"
    external_llm_base_url: str | None = None
    external_llm_api_key: str | None = None
    external_llm_model: str | None = None
    external_llm_allowed_models: str = ""
    external_llm_timeout_seconds: int = 180
    runtime_config_path: str = ".runtime-config.json"
    ocr_max_pdf_pages: int = 4
    paddleocr_vl_bin: str = "/home/suesoo/paddleocr-vl-test/.venv/bin/paddleocr"
    paddleocr_vl_model: str = "PaddleOCR-VL-1.5-0.9B"
    paddleocr_vl_device: str = "gpu"
    paddleocr_vl_max_pixels: int = 524288
    paddleocr_vl_min_pixels: int = 196608
    paddleocr_vl_max_new_tokens: int = 768
    paddleocr_vl_timeout_seconds: int = 180
    paddleocr_vl_gpu_ids: str = "0"
    ollama_timeout_seconds: int = 180
    storage_root: str = "/storage"
    web_app_allowed_origin: str = "https://invoice.nusome.co.kr"
    internal_shared_token: str
    ocr_max_retries: int = 1
    worker_stale_after_seconds: int = 240
    ocr_parallel_workers: int = 1
    ocr_min_start_gap_seconds: int = 60

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def allowed_models(self) -> list[str]:
        raw = self.external_llm_allowed_models if self.llm_backend == "external_api" else self.ollama_allowed_models
        values = [item.strip() for item in raw.split(",") if item.strip()]
        if values:
            return values
        default = self.default_llm_model
        return [default] if default else []

    @property
    def default_llm_model(self) -> str:
        if self.llm_backend == "external_api":
            return (self.external_llm_model or "").strip()
        return self.ollama_model

    @property
    def allowed_ocr_models(self) -> list[str]:
        raw = self.glm_ocr_allowed_models if self.ocr_backend == "glm_ocr" else self.paddleocr_vl_model
        values = [item.strip() for item in raw.split(",") if item.strip()]
        if values:
            return values
        default = self.default_ocr_model
        return [default] if default else []

    @property
    def default_ocr_model(self) -> str:
        if self.ocr_backend == "glm_ocr":
            return self.glm_ocr_model.strip()
        return self.paddleocr_vl_model

    @property
    def paddleocr_vl_gpu_id_list(self) -> list[str]:
        return [
            item.strip()
            for item in self.paddleocr_vl_gpu_ids.split(",")
            if item.strip()
        ]

    @property
    def external_llm_chat_completions_url(self) -> str:
        base_url = (self.external_llm_base_url or "").rstrip("/")
        if not base_url:
            raise ValueError("EXTERNAL_LLM_BASE_URL is required when LLM_BACKEND=external_api")
        if base_url.endswith("/chat/completions"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    @property
    def ollama_keep_alive_value(self) -> int | str:
        value = self.ollama_keep_alive.strip()
        if value.lstrip("-").isdigit():
            return int(value)
        return value


settings = Settings()
