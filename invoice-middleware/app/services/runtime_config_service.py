import json
from pathlib import Path

from app.core.config import settings

_SUPPORTED_LLM_BACKENDS = {"ollama", "external_api"}
_SUPPORTED_OCR_BACKENDS = {"paddleocr_vl", "glm_ocr"}


def _config_path() -> Path:
    return Path(settings.runtime_config_path)


def _env_allowed_models(backend: str) -> list[str]:
    raw = settings.external_llm_allowed_models if backend == "external_api" else settings.ollama_allowed_models
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if values:
        return values
    default_model = settings.external_llm_model if backend == "external_api" else settings.ollama_model
    return [default_model] if default_model else []


def _env_default_model(backend: str) -> str:
    return (settings.external_llm_model if backend == "external_api" else settings.ollama_model).strip()


def _env_allowed_ocr_models(backend: str) -> list[str]:
    raw = settings.glm_ocr_allowed_models if backend == "glm_ocr" else settings.paddleocr_vl_model
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if values:
        return values
    default_model = settings.glm_ocr_model if backend == "glm_ocr" else settings.paddleocr_vl_model
    return [default_model] if default_model else []


def _env_default_ocr_model(backend: str) -> str:
    return (settings.glm_ocr_model if backend == "glm_ocr" else settings.paddleocr_vl_model).strip()


def _all_env_ocr_models() -> dict[str, list[str]]:
    return {
        "paddleocr_vl": _env_allowed_ocr_models("paddleocr_vl"),
        "glm_ocr": _env_allowed_ocr_models("glm_ocr"),
    }


def _load_runtime_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _active_external_api_key(override: dict | None = None) -> str:
    runtime = override if override is not None else _load_runtime_config()
    value = runtime.get("external_llm_api_key")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return (settings.external_llm_api_key or "").strip()


def get_external_llm_api_key() -> str:
    return _active_external_api_key()


def _mask_secret(value: str) -> str:
    secret = (value or "").strip()
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"


def get_external_llm_chat_completions_url() -> str:
    base_url = (settings.external_llm_base_url or "").rstrip("/")
    if not base_url:
        raise ValueError("EXTERNAL_LLM_BASE_URL is required when LLM_BACKEND=external_api")
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def get_active_llm_config() -> dict:
    override = _load_runtime_config()
    backend = override.get("llm_backend") or settings.llm_backend
    if backend not in _SUPPORTED_LLM_BACKENDS:
        backend = settings.llm_backend
    ocr_backend = override.get("ocr_backend") or settings.ocr_backend
    if ocr_backend not in _SUPPORTED_OCR_BACKENDS:
        ocr_backend = settings.ocr_backend
    default_model = _env_default_model(backend)
    active_model = override.get("default_model") or default_model
    default_ocr_model = _env_default_ocr_model(ocr_backend)
    active_ocr_model = override.get("ocr_model") or default_ocr_model
    external_api_key = _active_external_api_key(override)
    return {
        "llm_backend": backend,
        "default_model": active_model,
        "allowed_models": _env_allowed_models(backend),
        "ocr_backend": ocr_backend,
        "ocr_model": active_ocr_model,
        "ocr_allowed_models": _env_allowed_ocr_models(ocr_backend),
        "ocr_models_by_backend": _all_env_ocr_models(),
        "ocr_backend_ready": bool(settings.ollama_base_url and active_ocr_model) if ocr_backend == "glm_ocr" else True,
        "backend_ready": (
            bool(settings.external_llm_base_url and settings.external_llm_model and external_api_key)
            if backend == "external_api"
            else bool(settings.ollama_base_url and settings.ollama_model)
        ),
        "external_api_configured": bool(settings.external_llm_base_url and settings.external_llm_model and external_api_key),
        "external_api_key_configured": bool(external_api_key),
        "external_api_key_masked": _mask_secret(external_api_key),
        "ollama_configured": bool(settings.ollama_base_url and settings.ollama_model),
        "runtime_config_path": str(_config_path()),
    }


def update_llm_backend(
    llm_backend: str,
    default_model: str | None = None,
    ocr_backend: str | None = None,
    ocr_model: str | None = None,
    external_llm_api_key: str | None = None,
) -> dict:
    override = _load_runtime_config()
    backend = (llm_backend or "").strip().lower()
    if backend not in _SUPPORTED_LLM_BACKENDS:
        raise ValueError("llm_backend must be 'ollama' or 'external_api'")
    next_external_key = (external_llm_api_key or "").strip() or _active_external_api_key(override)
    if backend == "external_api" and not (settings.external_llm_base_url and settings.external_llm_model and next_external_key):
        raise ValueError("External API backend is not configured on the server")
    chosen_ocr_backend = (ocr_backend or "").strip().lower() or settings.ocr_backend
    if chosen_ocr_backend not in _SUPPORTED_OCR_BACKENDS:
        raise ValueError("ocr_backend must be 'paddleocr_vl' or 'glm_ocr'")
    if chosen_ocr_backend == "glm_ocr" and not settings.ollama_base_url:
        raise ValueError("Ollama is not configured on the server for GLM-OCR")

    allowed_models = _env_allowed_models(backend)
    chosen_model = (default_model or "").strip() or _env_default_model(backend)
    if chosen_model and chosen_model not in allowed_models:
        raise ValueError(
            f"Unsupported model '{chosen_model}'. Allowed models: {', '.join(allowed_models)}"
        )
    allowed_ocr_models = _env_allowed_ocr_models(chosen_ocr_backend)
    chosen_ocr_model = (ocr_model or "").strip() or _env_default_ocr_model(chosen_ocr_backend)
    if chosen_ocr_model and chosen_ocr_model not in allowed_ocr_models:
        raise ValueError(
            f"Unsupported OCR model '{chosen_ocr_model}'. Allowed models: {', '.join(allowed_ocr_models)}"
        )

    payload = {
        "llm_backend": backend,
        "default_model": chosen_model,
        "ocr_backend": chosen_ocr_backend,
        "ocr_model": chosen_ocr_model,
    }
    if (external_llm_api_key or "").strip():
        payload["external_llm_api_key"] = external_llm_api_key.strip()
    elif override.get("external_llm_api_key"):
        payload["external_llm_api_key"] = override["external_llm_api_key"]
    path = _config_path()
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return get_active_llm_config()
