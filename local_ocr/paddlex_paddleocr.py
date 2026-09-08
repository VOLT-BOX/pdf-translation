from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import fitz
import requests

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
if PIPELINE_DIR.exists() and str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))


def _load_image_reocr_helper():
    helper_path = PIPELINE_DIR / "services" / "ocr_provider" / "paddle_image_reocr.py"
    spec = importlib.util.spec_from_file_location("_retain_paddle_image_reocr", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Paddle image re-OCR helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.augment_paddle_payload_with_image_reocr


augment_paddle_payload_with_image_reocr = _load_image_reocr_helper()

LOCAL_PADDLE_VL_DEFAULT_OPTIONS: dict[str, Any] = {
    "max_num_input_imgs": 999,
    "mergeLayoutBlocks": False,
    "merge_layout_blocks": False,
    "markdownIgnoreLabels": [
        "header",
        "header_image",
        "footer",
        "footer_image",
        "number",
        "footnote",
        "aside_text",
    ],
    "markdown_ignore_labels": [
        "header",
        "header_image",
        "footer",
        "footer_image",
        "number",
        "footnote",
        "aside_text",
    ],
    "useDocOrientationClassify": False,
    "use_doc_orientation_classify": False,
    "useDocUnwarping": False,
    "use_doc_unwarping": False,
    "useLayoutDetection": True,
    "use_layout_detection": True,
    "useChartRecognition": False,
    "use_chart_recognition": False,
    "useSealRecognition": True,
    "use_seal_recognition": True,
    "useOcrForImageBlock": False,
    "use_ocr_for_image_block": False,
    "mergeTables": True,
    "merge_tables": True,
    "relevelTitles": True,
    "relevel_titles": True,
    "layoutShapeMode": "auto",
    "layout_shape_mode": "auto",
    "promptLabel": "ocr",
    "prompt_label": "ocr",
    "repetitionPenalty": 1,
    "repetition_penalty": 1,
    "temperature": 0,
    "topP": 1,
    "top_p": 1,
    "minPixels": 147384,
    "min_pixels": 147384,
    "maxPixels": 2822400,
    "max_pixels": 2822400,
    "layoutNms": True,
    "layout_nms": True,
    "restructurePages": True,
    "restructure_pages": True,
    "format_block_content": False,
    "visualize": False,
}


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return value if value > 0 else default


def _base_url() -> str:
    return _env("RETAIN_LOCAL_OCR_URL", "http://host.docker.internal:8080").rstrip("/")


def _pdf_page_meta(source_pdf: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    with fitz.open(source_pdf) as doc:
        for page in doc:
            pages.append(
                {
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                }
            )
    return pages


def _raw_page_meta_from_layout(result: dict[str, Any], source_pdf: Path) -> list[dict[str, Any]]:
    fallback_pages = _pdf_page_meta(source_pdf)
    pages: list[dict[str, Any]] = []
    for page_index, page_payload in enumerate(result.get("layoutParsingResults") or []):
        pruned = page_payload.get("prunedResult") if isinstance(page_payload, dict) else {}
        pruned = pruned if isinstance(pruned, dict) else {}
        width = float(pruned.get("width", 0) or 0)
        height = float(pruned.get("height", 0) or 0)
        if width <= 0 or height <= 0:
            fallback = fallback_pages[page_index] if page_index < len(fallback_pages) else {}
            width = float(fallback.get("width", 0) or 0)
            height = float(fallback.get("height", 0) or 0)
        pages.append({"width": width, "height": height})
    return pages


def _request_payload(source_pdf: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "file": base64.b64encode(source_pdf.read_bytes()).decode("ascii"),
        "fileType": 0,
        **LOCAL_PADDLE_VL_DEFAULT_OPTIONS,
    }
    optional_json = _env("RETAIN_LOCAL_OCR_OPTIONAL_PAYLOAD")
    if optional_json:
        extra = json.loads(optional_json)
        if not isinstance(extra, dict):
            raise RuntimeError("RETAIN_LOCAL_OCR_OPTIONAL_PAYLOAD must be a JSON object")
        payload.update(extra)
    return payload


def _request_crop_payload(crop_pdf: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "file": base64.b64encode(crop_pdf.read_bytes()).decode("ascii"),
        "fileType": 0,
        **LOCAL_PADDLE_VL_DEFAULT_OPTIONS,
    }
    optional_json = _env("RETAIN_LOCAL_OCR_OPTIONAL_PAYLOAD")
    if optional_json:
        extra = json.loads(optional_json)
        if not isinstance(extra, dict):
            raise RuntimeError("RETAIN_LOCAL_OCR_OPTIONAL_PAYLOAD must be a JSON object")
        payload.update(extra)
    return payload


def _extract_result(envelope: dict[str, Any]) -> dict[str, Any]:
    if int(envelope.get("errorCode", 0) or 0) != 0:
        raise RuntimeError(
            "PaddleX OCR failed: "
            f"code={envelope.get('errorCode')} msg={envelope.get('errorMsg', '')} "
            f"logId={envelope.get('logId', '')}"
        )
    result = envelope.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("PaddleX OCR response missing result object")
    if not isinstance(result.get("layoutParsingResults"), list):
        raise RuntimeError("PaddleX OCR result missing layoutParsingResults list")
    return result


def _ensure_data_info(result: dict[str, Any], source_pdf: Path) -> dict[str, Any]:
    pages = _raw_page_meta_from_layout(result, source_pdf)
    result = dict(result)
    data_info = dict(result.get("dataInfo") or {})
    data_info["pages"] = pages
    data_info["numPages"] = len(pages)
    result["dataInfo"] = data_info
    return result


def _save_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _ocr_crop_with_local_service(crop_pdf: Path, crop_index: int) -> dict[str, Any]:
    timeout = _env_int("RETAIN_LOCAL_OCR_TIMEOUT_SECONDS", 1800)
    url = f"{_base_url()}/layout-parsing"
    print(f"PaddleX image re-OCR crop {crop_index}: {url}", flush=True)
    response = requests.post(url, json=_request_crop_payload(crop_pdf), timeout=timeout)
    response.raise_for_status()
    return _extract_result(response.json())


def main() -> int:
    source_pdf = Path(_env("RETAIN_OCR_SOURCE_PDF")).resolve()
    output_json = Path(_env("RETAIN_OCR_RAW_PAYLOAD_JSON")).resolve()
    provider_result_json = Path(_env("RETAIN_OCR_PROVIDER_RESULT_JSON")).resolve()
    if not source_pdf.exists():
        raise RuntimeError(f"source PDF not found: {source_pdf}")

    timeout = _env_int("RETAIN_LOCAL_OCR_TIMEOUT_SECONDS", 1800)
    url = f"{_base_url()}/layout-parsing"
    print(f"PaddleX OCR request: {url}", flush=True)
    response = requests.post(url, json=_request_payload(source_pdf), timeout=timeout)
    response.raise_for_status()
    envelope = response.json()
    result = _ensure_data_info(_extract_result(envelope), source_pdf)
    result = augment_paddle_payload_with_image_reocr(
        result,
        source_pdf_path=source_pdf,
        work_dir=output_json.parent / "image_reocr",
        ocr_crop=_ocr_crop_with_local_service,
    )
    result.setdefault("_meta", {})
    if isinstance(result["_meta"], dict):
        result["_meta"].update(
            {
                "provider": "paddlex",
                "endpoint": url,
                "logId": envelope.get("logId", ""),
            }
        )
    _save_payload(output_json, result)
    _save_payload(provider_result_json, result)
    print(f"PaddleX OCR pages: {len(result.get('layoutParsingResults') or [])}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"paddlex paddleocr failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
