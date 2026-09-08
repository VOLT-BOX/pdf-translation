# -*- coding: utf-8 -*-
"""统一服务配置。

合并 v3(babeldoc)/ RetainPDF / router 三方配置。LLM 凭证统一一套,v3 与 RetainPDF 共用。
所有值均可由环境变量覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

_BASE = Path(__file__).resolve().parent


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _default_local_ocr_command() -> str:
    docker_wrapper = Path("/app/local_ocr/paddlex_paddleocr.py")
    if docker_wrapper.exists():
        return f"{os.sys.executable} {docker_wrapper}"
    return f'"{os.sys.executable}" "{_BASE / "local_ocr" / "paddlex_paddleocr.py"}"'


class Settings:
    # ---- LLM(统一一套,v3 与 RetainPDF 共用)----
    # 请求体传的 openai_api_key/model/base_url 优先,缺则这里兜底。
    llm_api_key: str = os.getenv("LLM_API_KEY", os.getenv("BABELDOC_API_KEY", ""))
    llm_model: str = _env("LLM_MODEL", "deepseek-v4-flash")
    llm_base_url: str = _env("LLM_BASE_URL", "https://api.deepseek.com/v1")

    # ---- OCR(RetainPDF 扫描页用)----
    retain_ocr_provider: str = _env("RETAIN_OCR_PROVIDER", "cloud")
    paddle_token: str = os.getenv("RETAIN_PADDLE_TOKEN", "")
    paddle_api_url: str = _env("RETAIN_PADDLE_API_URL", "")
    local_ocr_command: str = _env("RETAIN_LOCAL_OCR_COMMAND", _default_local_ocr_command())
    local_ocr_raw_provider: str = _env("RETAIN_OCR_RAW_PROVIDER", "paddle")

    # ---- v3(babeldoc)资源----
    # 允许重复翻译:禁用 babeldoc 的"已翻译"标记检查。
    allow_retranslate: bool = os.getenv("ALLOW_RETRANSLATE", "true").lower() == "true"

    # ---- RetainPDF 子进程相关(镜像 bake 的 env,这里给默认便于本地跑)----
    # Typst 二进制路径
    typst_bin: str = _env("TYPST_BIN", "typst")
    # Typst 预览包缓存卷
    typst_package_cache_path: str = _env("TYPST_PACKAGE_CACHE_PATH", str(_BASE / "data" / "typst-package-cache"))
    typst_package_path: str = _env("TYPST_PACKAGE_PATH", str(_BASE / "typst-packages"))
    # Typst 渲染字体
    typst_font_family: str = _env("RETAIN_PDF_TYPST_FONT_FAMILY", "Source Han Serif SC")
    # 子进程 Python(默认与主进程同)
    python_bin: str = _env("PYTHON_BIN", os.sys.executable)

    # RetainPDF 默认翻译参数
    retain_mode: str = _env("RETAIN_MODE", "fast")
    retain_render_mode: str = _env("RETAIN_RENDER_MODE", "auto")
    retain_math_mode: str = _env("RETAIN_MATH_MODE", "direct_typst")
    retain_paddle_model: str = _env("RETAIN_PADDLE_MODEL", "PaddleOCR-VL-1.6")
    retain_typst_font_family: str = _env("RETAIN_PDF_TYPST_FONT_FAMILY", "Source Han Serif SC")
    retain_pdf_compress_dpi: int = _env_int("RETAIN_PDF_COMPRESS_DPI", 0)

    # ---- 逐页分类阈值(来自 router)----
    min_text_chars: int = _env_int("MIN_TEXT_CHARS", 10)
    image_cover_threshold: float = _env_float("IMAGE_COVER_THRESHOLD", 0.45)
    image_dominant_text_chars: int = _env_int("IMAGE_DOMINANT_TEXT_CHARS", 300)

    # ---- 超时----
    # RetainPDF 子进程超时(秒)。v3 直调无超时概念(同步跑完)。
    retain_timeout: int = _env_int("RETAIN_TIMEOUT", 3600)

    # ---- 服务----
    host: str = _env("HOST", "0.0.0.0")
    port: int = _env_int("PORT", 8030)

    # 任务工作目录根
    work_root: str = _env("WORK_ROOT", str(_BASE / "data"))

    # 完成任务保留秒,过期清理
    retention_seconds: int = _env_int("RETENTION_SECONDS", 3600)

    @property
    def pipeline_dir(self) -> Path:
        return _BASE / "pipeline"

    @property
    def run_job_path(self) -> Path:
        return _BASE / "run_job.py"

    @property
    def ocr_provider_config(self) -> str:
        return str(self.pipeline_dir / "config" / "ocr_providers.json")

    def ensure_dirs(self) -> None:
        Path(self.work_root).mkdir(parents=True, exist_ok=True)
        Path(self.typst_package_cache_path).mkdir(parents=True, exist_ok=True)


settings = Settings()

# RetainPDF 凭证注入用的 env 名(spec 的 credential_ref 引用这些)
OCR_TOKEN_ENV = "RETAIN_PADDLE_TOKEN"
LLM_KEY_ENV = "RETAIN_TRANSLATION_API_KEY"
