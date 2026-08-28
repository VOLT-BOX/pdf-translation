# -*- coding: utf-8 -*-
"""babeldoc 翻译 worker(解耦版)。

从 no_ocr/v3/service/worker.py 重构:把 run_task(task_id)(依赖 tasks_store 全局)
改为 run_translate(record, progress_cb),直接操作传入的 V3Record 对象。

保留原 worker 的全部核心逻辑:
  - _build_translator(OpenAITranslator 构造)
  - DocLayoutModel.load_onnx()
  - _GLOSSARY_CACHE LRU(进程全局,跨任务复用编译好的 hyperscan DB)
  - check_metadata monkeypatch(允许重复翻译,进程全局一次)
  - TranslationConfig 构造 + do_translate
  - _build_bilingual_csv(从 translate_tracking.json 生成对照表)
  - 产物收集(output_dir glob *.pdf / *.csv)
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from babeldoc.format.pdf.high_level import do_translate
from babeldoc.format.pdf.high_level import get_translation_stage
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.translation_config import WatermarkOutputMode
from babeldoc.glossary import Glossary
from babeldoc.progress_monitor import ProgressMonitor
from babeldoc.translator.translator import OpenAITranslator

from config import settings
from v3_models import TranslateParams, V3Record

logger = logging.getLogger("unified.v3_worker")

# 允许重复翻译:禁用 babeldoc 的"已翻译"标记检查(进程全局,import 时生效一次)。
if settings.allow_retranslate:
    import babeldoc.format.pdf.high_level as _hl

    _hl.check_metadata = lambda _pdf: None
    logger.info("retranslate check disabled (ALLOW_RETRANSLATE=true)")


# 富文本样式占位符 / 公式占位符:对照表 CSV 清洗用。
_STYLE_TAG_RE = re.compile(
    r"<style\s+id\s*=\s*'\s*\d+\s*'\s*>|</style>", flags=re.IGNORECASE
)
_FORMULA_PLACEHOLDER_RE = re.compile(r"\{\s*v\s*\d+\s*\}|\{\{\s*\d+\s*\}\}")


def _clean_text(text: str) -> str:
    """剥掉引擎内部的 <style> 样式标签,删掉公式占位符 {vN}。仅用于对照表展示清洗。"""
    text = _STYLE_TAG_RE.sub("", text)
    text = _FORMULA_PLACEHOLDER_RE.sub("", text)
    return text


def collect_bilingual_rows(working_dir: Path) -> list[tuple[str, str]]:
    """把 working_dir 下所有 translate_tracking.json 转成 (原文, 译文) 行,不落盘。

    递归搜(单卷 + 分卷)合并。结构:{"page":[{"paragraph":[{"pdf_unicode":"原文",
    "output":"译文"}, ...]}],"cross_page":[...],"cross_column":[...]}。
    供实时对照表接口复用:translate_tracking.json 随翻译进度落盘,运行中即可拉到已翻部分。
    """
    tracking_files = sorted(working_dir.rglob("translate_tracking.json"))
    rows: list[tuple[str, str]] = []
    for tf in tracking_files:
        try:
            with tf.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            logger.exception(f"failed to parse {tf}")
            continue
        for section_key in ("page", "cross_page", "cross_column"):
            for page in data.get(section_key, []) or []:
                for para in page.get("paragraph", []) or []:
                    original = _clean_text((para.get("pdf_unicode") or "").strip())
                    translated = _clean_text((para.get("output") or "").strip())
                    if not original or not translated:
                        continue
                    if original == translated:
                        continue
                    rows.append((original, translated))
    return rows


def _build_bilingual_csv(
    working_dir: Path,
    output_csv_path: Path,
    *,
    lang_in: str,
    lang_out: str,
) -> Path | None:
    """把引擎的 translate_tracking.json 转成「原文↔译文段落对照表」CSV。"""
    rows = collect_bilingual_rows(working_dir)
    if not rows:
        logger.info("translate_tracking.json has no bilingual paragraph pairs")
        return None

    with output_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([f"original ({lang_in})", f"translated ({lang_out})"])
        writer.writerows(rows)
    logger.info(f"bilingual csv from {len(rows)} paragraph pair(s)")
    return output_csv_path


# 术语表进程级缓存(LRU max 1)。同一文件内容不变就复用编译好的 hyperscan DB。
_GLOSSARY_CACHE: "OrderedDict[tuple[str, str], Glossary]" = OrderedDict()
_GLOSSARY_CACHE_MAX = 1


def _load_glossary_cached(path: Path, lang_out: str) -> Glossary:
    """带 LRU 缓存的术语表加载:按 (文件内容哈希, 目标语言) 复用编译好的 Glossary。"""
    content_hash = hashlib.md5(path.read_bytes()).hexdigest()
    key = (content_hash, lang_out)
    cached = _GLOSSARY_CACHE.get(key)
    if cached is not None:
        _GLOSSARY_CACHE.move_to_end(key)
        logger.info(f"glossary cache hit: {path.name} ({len(cached.entries)} entries)")
        return cached
    g = Glossary.from_file(path, lang_out)
    _GLOSSARY_CACHE[key] = g
    while len(_GLOSSARY_CACHE) > _GLOSSARY_CACHE_MAX:
        evicted_key, evicted_g = _GLOSSARY_CACHE.popitem(last=False)  # noqa: F841
        logger.info(
            f"glossary cache evicted LRU entry ({len(evicted_g.entries)} entries) "
            f"to keep max {_GLOSSARY_CACHE_MAX}"
        )
    logger.info(f"glossary cache miss, compiled: {path.name} ({len(g.entries)} entries)")
    return g


def _build_translator(params: TranslateParams) -> OpenAITranslator:
    model = params.resolved_model(settings.llm_model)
    base_url = params.resolved_base_url(settings.llm_base_url)
    api_key = params.resolved_api_key(settings.llm_api_key)

    # 部分模型(如 Moonshot/Kimi)只接受 temperature=1,拒绝 temperature=0,
    # 否则每次翻译请求都 400(且 400 不被 tenacity 重试)→ 整段保持原文,静默丢翻译。
    # 检测 base_url/model 命中即不发送 temperature;用户也可用 no_send_temperature 显式关闭。
    base_url_low = (base_url or "").lower()
    model_low = (model or "").lower()
    no_temperature = params.no_send_temperature or (
        "moonshot" in base_url_low or "kimi" in model_low
    )

    kwargs: dict = {}
    if params.openai_reasoning is not None:
        kwargs["reasoning"] = params.openai_reasoning
    # deepseek 默认开思考模式:reasoning_content 吃掉大量输出 token(实测 941/977,
    # 占 96%),长段译文的 content 会被挤空 → 空响应/空白段。未显式指定时默认关闭
    # 思考;用户传 openai_thinking 可覆盖(如想保留思考)。
    if "deepseek" in base_url_low or "deepseek" in model_low:
        kwargs.setdefault("thinking", "disabled")
    if params.openai_thinking is not None:
        kwargs["thinking"] = params.openai_thinking
    return OpenAITranslator(
        lang_in=params.lang_in,
        lang_out=params.lang_out,
        model=model,
        base_url=base_url,
        api_key=api_key,
        enable_json_mode_if_requested=params.enable_json_mode_if_requested,
        send_dashscope_header=params.send_dashscope_header,
        send_temperature=not no_temperature,
        **kwargs,
    )


def run_translate(
    record: V3Record,
    *,
    progress_cb: Callable[[float, str], None] | None = None,
) -> None:
    """同步执行 v3 翻译(在工作线程里调用)。

    输入:record.input_pdf + record.glossary_path
    输出:产物写 record.work_dir/output/,回填 record.result_files
    进度:通过 progress_cb(percent, stage) 回调;同时回填 record.progress/stage
    失败:抛异常(调用方捕获标 failed);回填 record.error
    """
    params = record.params
    input_pdf = record.input_pdf
    output_dir = record.work_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        translator = _build_translator(params)

        # doc_layout_model:用本地 onnx(warmup 已下好)
        from babeldoc.docvision.doclayout import DocLayoutModel

        doc_layout_model = DocLayoutModel.load_onnx()

        # 加载用户术语表(进程级缓存,失败降级为无术语表)
        user_glossaries: list[Glossary] = []
        if record.glossary_path is not None and record.glossary_path.exists():
            try:
                g = _load_glossary_cached(record.glossary_path, params.lang_out)
                user_glossaries.append(g)
                logger.info(
                    f"loaded glossary {record.glossary_path.name} "
                    f"({len(g.entries)} entries)"
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    f"failed to load glossary {record.glossary_path.name}, "
                    f"translating without it"
                )

        working_dir = record.work_dir / "working"
        working_dir.mkdir(parents=True, exist_ok=True)

        config = TranslationConfig(
            input_file=str(input_pdf),
            font=None,
            pages=None,
            output_dir=str(output_dir),
            translator=translator,
            term_extraction_translator=translator,
            debug=False,
            lang_in=params.lang_in,
            lang_out=params.lang_out,
            no_dual=False,
            no_mono=params.no_mono,
            qps=params.qps,
            doc_layout_model=doc_layout_model,
            glossaries=user_glossaries or None,
            glossary_hard=params.glossary_hard,
            working_dir=str(working_dir),
            custom_system_prompt=params.custom_system_prompt,
            remove_non_formula_lines=True,
            watermark_output_mode=(
                WatermarkOutputMode.NoWatermark
                if params.no_watermark
                else WatermarkOutputMode.Watermarked
            ),
        )

        def _on_progress(**kwargs):
            overall = kwargs.get("overall_progress")
            if overall is not None:
                record.progress = float(overall)
                if progress_cb:
                    progress_cb(float(overall), record.stage)
            stage = kwargs.get("stage")
            if stage:
                record.stage = str(stage)
                if progress_cb:
                    progress_cb(record.progress, record.stage)

        pm = ProgressMonitor(
            get_translation_stage(config),
            progress_change_callback=_on_progress,
        )
        do_translate(pm, config)

        # 翻译完生成 bilingual CSV(失败不阻断,仅 warning)
        try:
            tracking_csv = _build_bilingual_csv(
                working_dir,
                output_dir / "input.bilingual.csv",
                lang_in=params.lang_in,
                lang_out=params.lang_out,
            )
            if tracking_csv:
                logger.info(f"bilingual csv: {tracking_csv.name}")
        except Exception:  # noqa: BLE001
            logger.exception("failed to build bilingual csv")

        # 收集产物:PDF(mono/dual)+ CSV(bilingual/自动术语表)
        results = list(output_dir.glob("*.pdf"))
        record.result_files = [p.name for p in results]
        for extra in output_dir.glob("*.csv"):
            if extra.name not in record.result_files:
                record.result_files.append(extra.name)
        if not record.result_files:
            raise RuntimeError("翻译完成但输出目录没有任何产物")
        record.progress = 100.0
        if progress_cb:
            progress_cb(100.0, "done")
        logger.info(f"v3 done, files={record.result_files}")
    except Exception as e:  # noqa: BLE001
        record.error = f"{type(e).__name__}: {e}"
        logger.exception("v3 translate failed")
        raise


def warmup() -> None:
    """预热 babeldoc 资源(首次加载模型/字体/CMap),避免第一个任务卡很久。"""
    try:
        import babeldoc.assets.assets as assets

        assets.warmup()
        logger.info("babeldoc warmup done")
    except Exception:  # noqa: BLE001
        logger.warning("babeldoc warmup failed (assets may download on first task)")
