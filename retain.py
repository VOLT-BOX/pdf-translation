# -*- coding: utf-8 -*-
"""RetainPDF 翻译引擎封装(subprocess 调用)。

从 RetainPDF/retain-pdf-translate-service/app.py 提取核心逻辑:
  - 术语表解析(CSV/XLSX/JSON → spec glossary_entries)
  - spec(provider.stage.v1)构造
  - 子进程跑 run_job.py(隔离 apply_layout_tuning 进程全局态)
  - rendered/ 产物分类(mono/dual/bilingual)

保留 subprocess 形态:RetainPDF 的 apply_layout_tuning 是进程全局态,
并发任务会互相踩,必须每任务一进程隔离(不能像 v3 那样塞进主进程)。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

from fastapi import HTTPException

# pipeline 源码目录(复用 job_dirs 工具 + side_by_side_pdf + translation_artifact_text)
_PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from foundation.shared.job_dirs import resolve_job_dirs, ensure_job_dirs  # noqa: E402
from services.rendering.tools.side_by_side_pdf import build_side_by_side_pdf  # noqa: E402
from services.translation.artifacts.status import translation_artifact_text  # noqa: E402

from config import settings, OCR_TOKEN_ENV, LLM_KEY_ENV

logger = logging.getLogger("unified.retain")

# rendered/ 下产物命名后缀
_DUAL_PDF_SUFFIX = "-dual.pdf"
_BILINGUAL_CSV_SUFFIX = ".bilingual.csv"

# target_lang 代码 → 展示名(注入 prompt)
_TARGET_LANGUAGE_NAMES = {
    "zh-CN": "简体中文", "zh": "简体中文", "zh-TW": "繁體中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "fr": "Français", "de": "Deutsch", "es": "Español",
    "it": "Italiano", "pt": "Português", "ru": "Русский",
    "ar": "العربية", "th": "ภาษาไทย", "vi": "Tiếng Việt",
}


def _resolve_target_language_name(target_lang: str, explicit_name: str = "") -> str:
    name = (explicit_name or "").strip()
    if name:
        return name
    lang = (target_lang or "").strip()
    if not lang:
        return ""
    return _TARGET_LANGUAGE_NAMES.get(lang, lang)


# ---------- 术语表解析(父进程 LRU 缓存) ----------

_GLOSSARY_PARSE_CACHE: "OrderedDict[tuple[str, str, str], list[dict]]" = OrderedDict()
_GLOSSARY_PARSE_CACHE_MAX = 8

_VALID_LEVELS = {"preferred", "canonical", "preserve"}


def _normalize_level(value: str) -> str:
    lv = (value or "").strip().lower()
    return lv if lv in _VALID_LEVELS else "preferred"



def _normalize_lang_main(value: str | None) -> str:
    lang = (value or "").strip().lower().replace("_", "-")
    if not lang or lang == "auto":
        return ""
    return lang.split("-", 1)[0]


def _lang_matches(row_lang: str | None, requested_lang: str | None) -> bool:
    row_main = _normalize_lang_main(row_lang)
    if not row_main:
        return True
    requested_main = _normalize_lang_main(requested_lang)
    if not requested_main:
        return True
    return row_main == requested_main


def _first_value(mapping: dict, *names: str) -> str:
    for name in names:
        value = mapping.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _optional_column(col: dict[str, int], *names: str) -> int | None:
    for name in names:
        idx = col.get(name)
        if idx is not None:
            return idx
    return None


def _row_cell(row: tuple, idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    value = row[idx]
    return str(value).strip() if value is not None else ""


def _decode_csv_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_glossary_csv(data: bytes, source_lang: str, target_lang: str) -> list[dict]:
    """CSV: source,target[,src_lng,tgt_lng,level]。语言列为空表示通用术语。"""
    if not data:
        return []
    text = _decode_csv_bytes(data)
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []
    if "source" not in fieldnames or "target" not in fieldnames:
        raise HTTPException(400, "glossary CSV must have 'source' and 'target' columns")

    entries: list[dict] = []
    for row in reader:
        source = (row.get("source") or "").strip()
        target = (row.get("target") or "").strip()
        if not source or not target:
            continue
        row_src_lang = _first_value(row, "src_lng", "src_lang", "source_lang", "source_language")
        row_tgt_lang = _first_value(row, "tgt_lng", "tgt_lang", "target_lang", "target_language")
        if not _lang_matches(row_src_lang, source_lang):
            continue
        if not _lang_matches(row_tgt_lang, target_lang):
            continue
        level = _normalize_level(row.get("level") or "")
        entry = {"source": source, "target": target, "level": level}
        if row_src_lang:
            entry["src_lng"] = row_src_lang
        if row_tgt_lang:
            entry["tgt_lng"] = row_tgt_lang
        entries.append(entry)
    return entries


def _parse_glossary_xlsx(data: bytes, source_lang: str, target_lang: str) -> list[dict]:
    if not data:
        return []
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError as exc:
        raise HTTPException(500, "加载 xlsx 术语表需要 openpyxl") from exc

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise HTTPException(400, "xlsx 术语表为空") from None
        header = [str(c).strip().lower() if c is not None else "" for c in header]
        if "source" not in header or "target" not in header:
            raise HTTPException(400, f"xlsx 术语表表头须含 source、target,实际: {header}")
        col = {name: i for i, name in enumerate(header)}
        src_lng_idx = _optional_column(col, "src_lng", "src_lang", "source_lang", "source_language")
        tgt_lng_idx = _optional_column(col, "tgt_lng", "tgt_lang", "target_lang", "target_language")
        level_idx = col.get("level")

        entries: list[dict] = []
        for row in rows:
            source = row[col["source"]] if col["source"] < len(row) else None
            target = row[col["target"]] if col["target"] < len(row) else None
            if source is None or target is None:
                continue
            source = str(source).strip()
            target = str(target).strip()
            if not source or not target:
                continue
            row_src_lang = _row_cell(row, src_lng_idx)
            row_tgt_lang = _row_cell(row, tgt_lng_idx)
            if not _lang_matches(row_src_lang, source_lang):
                continue
            if not _lang_matches(row_tgt_lang, target_lang):
                continue
            level = "preferred"
            if level_idx is not None:
                level = _normalize_level(_row_cell(row, level_idx))
            entry = {"source": source, "target": target, "level": level}
            if row_src_lang:
                entry["src_lng"] = row_src_lang
            if row_tgt_lang:
                entry["tgt_lng"] = row_tgt_lang
            entries.append(entry)
        return entries
    finally:
        wb.close()


def _parse_glossary_json_str(text: str, source_lang: str, target_lang: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"glossary_json is not valid JSON: {exc}")
    if not isinstance(payload, list):
        raise HTTPException(400, "glossary_json must be a JSON array")
    entries: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "") or "").strip()
        target = str(item.get("target", "") or "").strip()
        if not source or not target:
            continue
        row_src_lang = _first_value(item, "src_lng", "src_lang", "source_lang", "source_language")
        row_tgt_lang = _first_value(item, "tgt_lng", "tgt_lang", "target_lang", "target_language")
        if not _lang_matches(row_src_lang, source_lang):
            continue
        if not _lang_matches(row_tgt_lang, target_lang):
            continue
        level = _normalize_level(str(item.get("level", "") or ""))
        entry = {"source": source, "target": target, "level": level}
        if row_src_lang:
            entry["src_lng"] = row_src_lang
        if row_tgt_lang:
            entry["tgt_lng"] = row_tgt_lang
        entries.append(entry)
    return entries


def resolve_glossary_entries(
    glossary_path: Path | None,
    glossary_json: str,
    source_lang: str,
    target_lang: str,
) -> list[dict]:
    """合并术语表文件(.csv/.xlsx)与 JSON 字符串,按 src_lng/tgt_lng 过滤并去重。"""
    entries: list[dict] = []
    if glossary_path is not None and glossary_path.exists():
        data = glossary_path.read_bytes()
        suffix = glossary_path.suffix.lower()
        source_main = _normalize_lang_main(source_lang)
        target_main = _normalize_lang_main(target_lang)
        cache_key = (hashlib.md5(data).hexdigest(), source_main, target_main)
        cached = _GLOSSARY_PARSE_CACHE.get(cache_key)
        if cached is not None:
            _GLOSSARY_PARSE_CACHE.move_to_end(cache_key)
            logger.info("glossary parse cache hit: %s entries", len(cached))
            file_entries = cached
        else:
            if suffix == ".xlsx":
                file_entries = _parse_glossary_xlsx(data, source_lang, target_lang)
            else:
                file_entries = _parse_glossary_csv(data, source_lang, target_lang)
            _GLOSSARY_PARSE_CACHE[cache_key] = file_entries
            while len(_GLOSSARY_PARSE_CACHE) > _GLOSSARY_PARSE_CACHE_MAX:
                _GLOSSARY_PARSE_CACHE.popitem(last=False)
            logger.info(
                "glossary parsed: %s entries from %s for %s->%s",
                len(file_entries), suffix.lstrip("."), source_main or "auto", target_main or "auto",
            )
        entries.extend(file_entries)
    entries.extend(_parse_glossary_json_str(glossary_json, source_lang, target_lang))

    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for e in entries:
        key = (e["source"].casefold(), e["target"].casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return deduped

# ---------- spec 构造 ----------

def _normalize_ocr_provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    if provider in {"", "cloud", "remote", "paddle"}:
        return "paddle"
    if provider == "local":
        return "local"
    return provider


def _build_spec(
    *,
    job_id: str,
    job_root: Path,
    source_pdf_path: Path,
    provider: str,
    model: str,
    base_url: str,
    mode: str,
    render_mode: str,
    workers: int,
    batch_size: int,
    math_mode: str,
    skip_title_translation: bool,
    typst_font_family: str,
    pdf_compress_dpi: int,
    paddle_model: str,
    paddle_api_url: str = "",
    local_ocr_command: str = "",
    local_ocr_raw_provider: str = "",
    glossary_entries: list[dict],
    custom_prompt: str = "",
    glossary_hard: bool = False,
    enable_table_translation: bool = False,
    target_lang: str = "",
    target_language_name: str = "",
) -> dict:
    """构造 provider.stage.v1 spec。"""
    translation_block: dict = {
        "credential_ref": f"env:{LLM_KEY_ENV}",
        "model": model,
        "base_url": base_url,
        "mode": mode,
        "math_mode": math_mode,
        "skip_title_translation": skip_title_translation,
        "enable_table_translation": bool(enable_table_translation),
        "start_page": 0,
        "end_page": -1,
        "workers": workers,
        "batch_size": batch_size,
        "glossary_entries": glossary_entries,
        "glossary_mode": "matched",
        "glossary_hard": bool(glossary_hard),
        "target_lang": target_lang,
        "target_language_name": target_language_name,
    }
    if custom_prompt.strip():
        translation_block["custom_rules_text"] = custom_prompt.strip()
    ocr_block: dict = {
        "provider": provider,
        "credential_ref": f"env:{OCR_TOKEN_ENV}",
        "paddle_model": paddle_model,
    }
    if paddle_api_url.strip():
        ocr_block["paddle_api_url"] = paddle_api_url.strip()
    local_options: dict = {}
    if local_ocr_command.strip():
        local_options["command"] = local_ocr_command.strip()
    if local_ocr_raw_provider.strip():
        local_options["raw_provider"] = local_ocr_raw_provider.strip()
    if local_options:
        ocr_block["options"] = local_options

    return {
        "schema_version": "provider.stage.v1",
        "stage": "provider",
        "job": {"job_id": job_id, "job_root": str(job_root), "workflow": "book"},
        "source": {"file_url": "", "file_path": str(source_pdf_path)},
        "ocr": ocr_block,
        "translation": translation_block,
        "render": {
            "render_mode": render_mode,
            "typst_font_family": typst_font_family,
            "pdf_compress_dpi": pdf_compress_dpi,
        },
    }


# ---------- 产物分类 ----------

def classify_rendered_pdfs(rendered_dir: Path) -> dict[str, Path]:
    """rendered/ 下的 PDF 按 mono/dual 分类(各取最新一个)。"""
    out: dict[str, Path] = {}
    if not rendered_dir.exists():
        return out
    candidates = sorted(rendered_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in candidates:
        if p.name.lower().endswith(_DUAL_PDF_SUFFIX.lower()):
            out.setdefault("dual", p)
        else:
            out.setdefault("mono", p)
    return out


def find_bilingual_csv(rendered_dir: Path) -> Path | None:
    if not rendered_dir.exists():
        return None
    candidates = sorted(
        rendered_dir.glob(f"*{_BILINGUAL_CSV_SUFFIX}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _page_index_from_name(name: str) -> int:
    import re

    m = re.search(r"page-(\d+)-", name)
    return int(m.group(1)) - 1 if m else 10**9


def _flatten_cell(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def collect_bilingual_rows(translated_dir: Path) -> list[tuple[str, str]]:
    """从 translated/page-NNN-*.json 聚合 (原文, 译文) 行,不落盘。

    供实时对照表接口复用:translated 目录是逐页 flush 的,任务运行中即可
    拉到"已翻完的部分"。
    """
    if not translated_dir.exists():
        return []
    page_files = sorted(
        translated_dir.glob("page-*.json"),
        key=lambda p: _page_index_from_name(p.name),
    )
    rows: list[tuple[str, str]] = []
    for pf in page_files:
        try:
            payload = json.loads(pf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            original = _flatten_cell(str(item.get("source_text") or ""))
            translated = _flatten_cell(translation_artifact_text(item) or "")
            if not original or not translated:
                continue
            if original == translated:
                continue
            rows.append((original, translated))
    return rows


def collect_live_bilingual_rows(retain_work: Path) -> list[tuple[str, str]]:
    """实时对照表:从 retain_work 下已 flush 的 translated 页聚合 (原文, 译文)。"""
    return collect_bilingual_rows(resolve_job_dirs(retain_work).translated_dir)


def build_bilingual_csv(
    job_root: Path,
    source_pdf_path: Path,
    *,
    lang_in: str = "",
    lang_out: str = "",
) -> Path | None:
    """从 translated/page-NNN-*.json 聚合原文↔译文对照表 CSV。"""
    job_dirs = resolve_job_dirs(job_root)
    rows = collect_bilingual_rows(job_dirs.translated_dir)
    if not rows:
        return None

    csv_path = job_dirs.rendered_dir / f"{source_pdf_path.stem}{_BILINGUAL_CSV_SUFFIX}"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            f"original ({lang_in})" if lang_in else "original",
            f"translated ({lang_out})" if lang_out else "translated",
        ])
        writer.writerows(rows)
    return csv_path


# ---------- 源页范围抽取(dual 对齐用) ----------

def _extract_source_page_range(
    src_pdf: Path,
    start_page: int,
    end_page: int,
    dest: Path,
) -> Path:
    """抽取原文 PDF 的 [start_page, end_page] 子集,保证 dual 左右按页索引对齐。"""
    import fitz

    if end_page < 0:
        end_page = None  # 到末页
    src = fitz.open(str(src_pdf))
    try:
        out = fitz.open()
        if end_page is None:
            out.insert_pdf(src, from_page=start_page)
        else:
            out.insert_pdf(src, from_page=start_page, to_page=end_page)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(dest)
        out.close()
        return dest
    finally:
        src.close()


def _run_dual_render(
    *,
    job_root: Path,
    source_pdf_path: Path,
    mono_pdf_path: Path,
    start_page: int,
    end_page: int,
) -> tuple[Path | None, str]:
    """原文 PDF 与译文 mono PDF 每页左右并排拼成 dual PDF。best-effort。"""
    if not source_pdf_path.exists() or not mono_pdf_path.exists():
        return None, "source pdf or mono pdf missing, skip dual"

    job_dirs = resolve_job_dirs(job_root)
    dual_pdf_path = job_dirs.rendered_dir / f"{source_pdf_path.stem}{_DUAL_PDF_SUFFIX}"
    aligned_source = _extract_source_page_range(
        source_pdf_path,
        start_page,
        end_page,
        job_dirs.rendered_dir / f".{source_pdf_path.stem}-source-range.pdf",
    )
    try:
        build_side_by_side_pdf(aligned_source, mono_pdf_path, dual_pdf_path)
        if dual_pdf_path.exists():
            return dual_pdf_path, ""
        return None, "dual render produced no pdf"
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"dual render failed: {exc}")
        return None, str(exc)


def _translation_failure_summary(work_dir: Path) -> str:
    diagnostics_path = work_dir / "artifacts" / "translation_diagnostics.json"
    if not diagnostics_path.exists():
        return ""
    try:
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to read translation diagnostics: %s", exc)
        return ""
    status_summary = diagnostics.get("status_summary") or {}
    failed = int(status_summary.get("failed") or 0)
    partially_translated = int(status_summary.get("partially_translated") or 0)
    if failed <= 0 and partially_translated <= 0:
        return ""
    error_summary = diagnostics.get("error_summary") or {}
    samples = diagnostics.get("slow_request_samples") or []
    sample_errors = [str(sample.get("error_class") or sample.get("status_code") or "") for sample in samples if not sample.get("success")]
    details = [f"failed={failed}", f"partially_translated={partially_translated}"]
    if error_summary:
        details.append(f"error_summary={error_summary}")
    if sample_errors:
        details.append(f"sample_errors={sample_errors[:3]}")
    return "; ".join(details)

# ---------- 子进程跑流水线 ----------

class RetainResult:
    """RetainPDF 子任务产物。"""

    def __init__(self) -> None:
        self.mono_pdf: Path | None = None
        self.dual_pdf: Path | None = None
        self.bilingual_csv: Path | None = None
        self.returncode: int = -1
        self.stdout: str = ""
        self.stderr: str = ""
        self.error: str | None = None


def run_retain(
    *,
    pdf_path: Path,
    inputs: dict,
    work_dir: Path,
    glossary_path: Path | None = None,
    progress_cb=None,
) -> RetainResult:
    """跑 RetainPDF 流水线(subprocess),返回产物。

    inputs 字段:lang_in/lang_out/concurrency/openai_api_key/openai_model/openai_base_url/
                 paddle_token/mode/glossary_hard/custom_system_prompt/enable_table_translation
    work_dir:RetainPDF job 目录(产物写 rendered/)
    """
    import fitz  # noqa: F401  (确保 PyMuPDF 可用)

    result = RetainResult()

    # 凭证:请求体优先,缺则 config 兜底
    requested_ocr_provider = (
        (inputs.get("ocr_provider") or "").strip()
        or settings.retain_ocr_provider.strip()
        or "cloud"
    )
    ocr_provider = _normalize_ocr_provider(requested_ocr_provider)
    paddle_token = (inputs.get("paddle_token") or "").strip() or settings.paddle_token
    paddle_api_url = (inputs.get("paddle_api_url") or "").strip() or settings.paddle_api_url
    local_ocr_command = (inputs.get("local_ocr_command") or "").strip() or settings.local_ocr_command
    local_ocr_raw_provider = (
        (inputs.get("local_ocr_raw_provider") or "").strip()
        or settings.local_ocr_raw_provider
        or "paddle"
    )
    api_key = (inputs.get("openai_api_key") or "").strip() or settings.llm_api_key
    model = (inputs.get("openai_model") or "").strip() or settings.llm_model
    base_url = (inputs.get("openai_base_url") or "").strip() or settings.llm_base_url

    if ocr_provider == "paddle" and not paddle_token:
        result.error = "paddle_token is required (or set RETAIN_PADDLE_TOKEN)"
        return result
    if ocr_provider == "local" and not local_ocr_command:
        result.error = "local_ocr_command is required when ocr_provider is local (or set RETAIN_LOCAL_OCR_COMMAND)"
        return result
    if not api_key:
        result.error = "api_key is required (or set LLM_API_KEY)"
        return result

    target_lang = inputs.get("lang_out") or "zh"
    target_language_name = _resolve_target_language_name(target_lang)

    # job 目录结构(对齐 pipeline 的 job_dirs)
    job_id = work_dir.name
    ensure_job_dirs(resolve_job_dirs(work_dir))

    # 源 PDF 放 source/ 下(pipeline 期望)
    original_name = pdf_path.name
    if not original_name.lower().endswith(".pdf"):
        original_name = f"{original_name}.pdf"
    source_pdf_path = work_dir / "source" / original_name
    source_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.resolve() != source_pdf_path.resolve():
        import shutil

        shutil.copy2(pdf_path, source_pdf_path)

    # 术语表
    glossary_entries = resolve_glossary_entries(
        glossary_path, "", inputs.get("lang_in") or "", target_lang
    )

    mode = inputs.get("mode") or settings.retain_mode
    spec = _build_spec(
        job_id=job_id, job_root=work_dir, source_pdf_path=source_pdf_path,
        provider=ocr_provider, model=model, base_url=base_url, mode=mode,
        render_mode=settings.retain_render_mode,
        workers=int(inputs.get("concurrency") or 100),
        batch_size=1, math_mode=settings.retain_math_mode,
        skip_title_translation=False,
        typst_font_family=settings.retain_typst_font_family,
        pdf_compress_dpi=settings.retain_pdf_compress_dpi,
        paddle_model=settings.retain_paddle_model,
        paddle_api_url=paddle_api_url,
        local_ocr_command=local_ocr_command,
        local_ocr_raw_provider=local_ocr_raw_provider,
        glossary_entries=glossary_entries,
        custom_prompt=inputs.get("custom_system_prompt") or "",
        glossary_hard=bool(inputs.get("glossary_hard", False)),
        enable_table_translation=bool(inputs.get("enable_table_translation", False)),
        target_lang=target_lang,
        target_language_name=target_language_name,
    )
    spec_path = work_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    # 子进程 env
    child_env = os.environ.copy()
    if paddle_token:
        child_env[OCR_TOKEN_ENV] = paddle_token
    child_env[LLM_KEY_ENV] = api_key
    child_env["RETAIN_OCR_PROVIDER_CONFIG"] = settings.ocr_provider_config
    if local_ocr_command:
        child_env["RETAIN_LOCAL_OCR_COMMAND"] = local_ocr_command
    if local_ocr_raw_provider:
        child_env["RETAIN_OCR_RAW_PROVIDER"] = local_ocr_raw_provider
    child_env["OUTPUT_ROOT"] = str(settings.work_root)
    child_env["PYTHONUNBUFFERED"] = "1"
    if target_lang.strip():
        child_env["RETAIN_TARGET_LANG"] = target_lang.strip()
    if target_language_name.strip():
        child_env["RETAIN_TARGET_LANGUAGE_NAME"] = target_language_name.strip()
    # Typst 相关(镜像 bake,本地跑时给默认)
    child_env.setdefault("TYPST_BIN", settings.typst_bin)
    child_env.setdefault("TYPST_PACKAGE_CACHE_PATH", settings.typst_package_cache_path)
    child_env.setdefault("TYPST_PACKAGE_PATH", settings.typst_package_path)
    child_env.setdefault("RETAIN_PDF_TYPST_FONT_FAMILY", settings.retain_typst_font_family)

    if progress_cb:
        progress_cb(5.0, "retain_submitted")

    try:
        proc = subprocess.run(
            [settings.python_bin, "-u", str(settings.run_job_path), str(spec_path)],
            env=child_env,
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            timeout=settings.retain_timeout,
        )
        result.returncode = proc.returncode
        result.stdout = proc.stdout[-6000:]
        result.stderr = proc.stderr[-6000:]

        rendered_dir = work_dir / "rendered"
        classified = classify_rendered_pdfs(rendered_dir)
        result.mono_pdf = classified.get("mono")
        failure_summary = _translation_failure_summary(work_dir) if proc.returncode == 0 else ""

        if proc.returncode == 0 and result.mono_pdf and not failure_summary:
            # dual(best-effort)
            dual_pdf, _ = _run_dual_render(
                job_root=work_dir,
                source_pdf_path=source_pdf_path,
                mono_pdf_path=result.mono_pdf,
                start_page=0,
                end_page=-1,
            )
            result.dual_pdf = dual_pdf
            # bilingual CSV
            try:
                result.bilingual_csv = build_bilingual_csv(
                    work_dir, source_pdf_path,
                    lang_in=inputs.get("lang_in", "en"),
                    lang_out=target_lang,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"retain bilingual csv failed: {exc}")
        else:
            result.error = (
                f"pipeline produced output but translation has failed items: {failure_summary}"
                if failure_summary else
                "no output pdf produced" if proc.returncode == 0
                else f"pipeline failed (rc={proc.returncode})"
            )
        if progress_cb:
            progress_cb(100.0 if result.mono_pdf else 0.0, "retain_done")
    except subprocess.TimeoutExpired:
        result.error = f"retain subprocess timeout ({settings.retain_timeout}s)"
        result.returncode = -1
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
        result.returncode = -1

    return result
