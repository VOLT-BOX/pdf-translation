# -*- coding: utf-8 -*-
"""后台编排 worker:v3 进程内直调 + RetainPDF subprocess,无 httpx 轮询。

对每个涉及的引擎:
  - v3:     进程内直调 v3_worker.run_translate(同步,跑完即有产物,进度回调)
  - retain: subprocess 跑 run_job.py(隔离 apply_layout_tuning 全局态,跑完取产物)
整本单引擎路由(默认 OCR,text_based=True 走 v3)。(混合件兜底分支仍在,但不再被触发。)
跑完按 manifest 合并(mono/dual 按原页序拼,bilingual CSV concat),终态触发回调。
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from pathlib import Path

from classify import ENGINE_RETAIN, ENGINE_V3, classify_and_split
from config import settings
from merge import check_dual_compatible, concat_csv, merge_pdf_by_manifest
from v3_models import TranslateParams, V3Record

logger = logging.getLogger("unified.orchestrate")


def _build_v3_params(inputs: dict) -> TranslateParams:
    return TranslateParams(
        lang_in=inputs.get("lang_in", "en"),
        lang_out=inputs.get("lang_out", "zh"),
        qps=min(int(inputs.get("concurrency") or 4), 20),  # v3 qps 上限 20
        openai_model=inputs.get("openai_model"),
        openai_base_url=inputs.get("openai_base_url"),
        openai_api_key=inputs.get("openai_api_key"),
        no_mono=bool(inputs.get("no_mono", False)),
        no_watermark=bool(inputs.get("no_watermark", True)),
        enable_json_mode_if_requested=True,
        custom_system_prompt=inputs.get("custom_system_prompt"),
        glossary_hard=bool(inputs.get("glossary_hard", False)),
    )


def _run_v3(
    pdf_path: Path,
    inputs: dict,
    work_dir: Path,
    glossary_path: Path | None,
    progress_cb,
) -> dict[str, Path]:
    """v3 进程内直调。返回 {"mono": path, "dual": path?, "bilingual": path?}。"""
    from v3_worker import run_translate

    v3_work = work_dir / "v3_work"
    v3_work.mkdir(parents=True, exist_ok=True)
    record = V3Record(
        input_pdf=pdf_path,
        work_dir=v3_work,
        params=_build_v3_params(inputs),
        glossary_path=glossary_path,
    )
    run_translate(record, progress_cb=progress_cb)

    out_dir = v3_work / "output"
    products: dict[str, Path] = {}
    for name in record.result_files:
        p = out_dir / name
        if not p.exists():
            continue
        low = name.lower()
        if low.endswith(".mono.pdf") or (low.endswith(".pdf") and ".dual." not in low and ".bilingual." not in low):
            if "mono" not in products:
                products["mono"] = p
        elif low.endswith(".dual.pdf"):
            products["dual"] = p
        elif low.endswith(".bilingual.csv"):
            products["bilingual"] = p
    return products


def _run_retain(
    pdf_path: Path,
    inputs: dict,
    work_dir: Path,
    glossary_path: Path | None,
    progress_cb,
) -> dict[str, Path]:
    """RetainPDF subprocess。返回 {"mono": path, "dual": path?, "bilingual": path?}。"""
    from retain import run_retain

    retain_work = work_dir / "retain_work"
    retain_work.mkdir(parents=True, exist_ok=True)
    result = run_retain(
        pdf_path=pdf_path,
        inputs=inputs,
        work_dir=retain_work,
        glossary_path=glossary_path,
        progress_cb=progress_cb,
    )
    if result.error and not result.mono_pdf:
        raise RuntimeError(f"retain failed: {result.error}; stderr_tail={result.stderr[-2000:]}")

    products: dict[str, Path] = {}
    if result.mono_pdf:
        products["mono"] = result.mono_pdf
    if result.dual_pdf:
        products["dual"] = result.dual_pdf
    if result.bilingual_csv:
        products["bilingual"] = result.bilingual_csv
    return products


def _fire_callback(task_id: str) -> None:
    """终态后,若存了 callback_url,POST 当前 task 状态。best-effort。"""
    from store import tasks_store  # 全局单例(lazy 规避循环依赖)

    task = tasks_store.get(task_id)
    if not task:
        return
    url = task.get("callback_url") or ""
    if not url:
        return

    def _post() -> None:
        try:
            payload = json.dumps(tasks_store.public_view(task_id), ensure_ascii=False)
            req = urllib.request.Request(
                url,
                data=payload.encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"callback task {task_id} -> {url} status={resp.status}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"callback task {task_id} -> {url} failed: {e}")

    threading.Thread(target=_post, daemon=True).start()


def run_image_task(task_id: str) -> None:
    """图片翻译任务主流程(后台 worker 线程):图 → PDF → RetainPDF → 译文图。"""
    from store import tasks_store  # 全局单例(lazy 规避循环依赖)

    task = tasks_store.get(task_id)
    if task is None:
        return
    tasks_store.mark_running(task_id)
    work_dir = Path(task["work_dir"])
    inputs: dict = task.get("inputs", {})

    try:
        from image_translate import image_to_pdf, pdf_page_to_image
        from retain import run_retain

        img_path = work_dir / "input"
        if not img_path.exists():
            raise RuntimeError("图片源文件缺失")

        pdf_bytes, w, h = image_to_pdf(img_path.read_bytes())
        tasks_store.update(task_id, image_size=f"{w}x{h}", stage="image_to_pdf")
        pdf_path = work_dir / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)

        retain_work = work_dir / "retain_work"
        retain_work.mkdir(parents=True, exist_ok=True)

        def pcb(p, s):
            tasks_store.update(task_id, retain_progress=p)

        result = run_retain(
            pdf_path=pdf_path,
            inputs=inputs,
            work_dir=retain_work,
            progress_cb=pcb,
        )
        if result.error or not result.mono_pdf:
            raise RuntimeError(
                f"RetainPDF 翻译失败: {result.error or '无产物'}; stderr_tail={result.stderr[-2000:]}"
            )

        tasks_store.update(task_id, stage="pdf_to_image")
        out_img = pdf_page_to_image(result.mono_pdf.read_bytes(), w, h)

        out_dir = work_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(task.get("original_filename", "image.png")).stem or "image"
        out_path = out_dir / f"{stem}.translated.png"
        out_path.write_bytes(out_img)

        tasks_store.mark_done(task_id, result_files=[out_path.name])
    except Exception as e:  # noqa: BLE001
        import traceback

        logger.exception(f"image task {task_id} failed")
        tasks_store.mark_failed(task_id, error=str(e), traceback=traceback.format_exc())
    finally:
        _fire_callback(task_id)


def run_router_task(task_id: str) -> None:
    """路由任务主流程(后台 worker 线程)。"""
    from store import tasks_store  # 全局单例(lazy 规避循环依赖)

    task = tasks_store.get(task_id)
    if task is None:
        return
    tasks_store.mark_running(task_id)
    work_dir = Path(task["work_dir"])
    inputs: dict = task.get("inputs", {})
    glossary_path_str = task.get("glossary_path")
    glossary_path = Path(glossary_path_str) if glossary_path_str else None
    input_pdf = work_dir / "input.pdf"

    try:
        # 1. 整本单引擎路由(默认 OCR,text_based=True 走 v3)+ (必要时)切片
        text_based = bool(inputs.get("text_based", False))
        decision, manifest, engine_map = classify_and_split(
            input_pdf, work_dir, text_based=text_based
        )
        tasks_store.update(task_id, decision=decision, manifest=manifest, stage="classified")
        engines_needed = [
            e for e in (ENGINE_V3, ENGINE_RETAIN) if engine_map.get(e)
        ]

        # 2. 跑引擎(混合件并行)
        v3_products: dict[str, Path] = {}
        retain_products: dict[str, Path] = {}

        def _do_v3():
            nonlocal v3_products
            def pcb(p, s):
                tasks_store.update(task_id, v3_progress=p)
            v3_products = _run_v3(Path(engine_map[ENGINE_V3]), inputs, work_dir, glossary_path, pcb)

        def _do_retain():
            nonlocal retain_products
            def pcb(p, s):
                tasks_store.update(task_id, retain_progress=p)
            retain_products = _run_retain(Path(engine_map[ENGINE_RETAIN]), inputs, work_dir, glossary_path, pcb)

        if len(engines_needed) == 1:
            # 单引擎,直接跑
            if ENGINE_V3 in engines_needed:
                _do_v3()
            else:
                _do_retain()
        else:
            # 混合件:两线程并行
            tv = threading.Thread(target=_do_v3)
            tr = threading.Thread(target=_do_retain)
            tv.start()
            tr.start()
            tv.join()
            tr.join()
            tasks_store.update(task_id, stage="translating")

        # 进度合并:已跑完的引擎记 100,未涉及的记 0
        v3_p = 100.0 if v3_products else 0.0
        retain_p = 100.0 if retain_products else 0.0
        if len(engines_needed) == 2:
            tasks_store.update(task_id, progress=(v3_p + retain_p) / 2)
        elif ENGINE_V3 in engines_needed:
            tasks_store.update(task_id, progress=v3_p)
        else:
            tasks_store.update(task_id, progress=retain_p)

        # 3. 收集各引擎产物路径(用于合并)
        engine_mono: dict[str, str] = {}
        engine_dual: dict[str, str | None] = {ENGINE_V3: None, ENGINE_RETAIN: None}
        bilingual_paths: list[Path] = []

        if v3_products:
            if "mono" in v3_products:
                engine_mono[ENGINE_V3] = str(v3_products["mono"])
            if "dual" in v3_products:
                engine_dual[ENGINE_V3] = str(v3_products["dual"])
            if "bilingual" in v3_products:
                bilingual_paths.append(v3_products["bilingual"])
        if retain_products:
            if "mono" in retain_products:
                engine_mono[ENGINE_RETAIN] = str(retain_products["mono"])
            if "dual" in retain_products:
                engine_dual[ENGINE_RETAIN] = str(retain_products["dual"])
            if "bilingual" in retain_products:
                bilingual_paths.append(retain_products["bilingual"])

        # mono 必有(任一所需引擎缺 mono 即失败)
        missing_mono = [e for e in engines_needed if e not in engine_mono]
        if missing_mono:
            tasks_store.mark_failed(
                task_id,
                error=f"mono missing for engine(s): {missing_mono}",
            )
            return

        # 4. 合并
        out_dir = work_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(task.get("original_filename", "output.pdf")).stem or "output"
        result_files: list[str] = []

        # mono
        mono_out = out_dir / f"{stem}.mono.pdf"
        if merge_pdf_by_manifest(manifest, engine_mono, mono_out):
            result_files.append(mono_out.name)
        else:
            tasks_store.mark_failed(task_id, error="mono merge produced no pages")
            return

        # dual(best-effort)
        if any(engine_dual.values()) and check_dual_compatible(manifest, engine_dual):
            dual_out = out_dir / f"{stem}.dual.pdf"
            if merge_pdf_by_manifest(manifest, engine_dual, dual_out):
                result_files.append(dual_out.name)

        # bilingual concat(v3 在前、retain 在后)
        ordered_bi: list[Path] = []
        if v3_products and "bilingual" in v3_products:
            ordered_bi.append(v3_products["bilingual"])
        if retain_products and "bilingual" in retain_products:
            ordered_bi.append(retain_products["bilingual"])
        bi_out = out_dir / f"{stem}.bilingual.csv"
        if concat_csv(ordered_bi, bi_out):
            result_files.append(bi_out.name)

        tasks_store.mark_done(task_id, result_files=result_files)
    except Exception as e:  # noqa: BLE001
        import traceback

        logger.exception(f"router task {task_id} failed")
        tasks_store.mark_failed(task_id, error=str(e), traceback=traceback.format_exc())
    finally:
        _fire_callback(task_id)
