# -*- coding: utf-8 -*-
"""统一 PDF 翻译服务 — FastAPI。

把 v3(babeldoc)与 RetainPDF 合成一个进程:整本单引擎路由,默认走 RetainPDF(OCR),
`text_based=true` 则整本走 v3(复用文字层,版面保持好)。对外一套 /tasks 接口。

接口:
  POST   /tasks                 上传 PDF + 超集参数,返回 task_id(202)
  GET    /tasks                 列出所有任务
  GET    /tasks/{id}            查状态/进度/决策
  GET    /tasks/{id}/result     下载合并产物?type=mono|dual|bilingual
  GET    /tasks/{id}/bilingual  实时下载已翻译的原文↔译文对照表 CSV(运行中可用)
  DELETE /tasks/{id}            删任务及工作目录
  POST   /normalize             PDF 页面标准化到 A4(图片转 PDF 场景前置修复)
  POST   /images/translate      图片翻译(异步):提交返回 task_id,后台翻译
  GET    /images/translate/{id}        下载译文图(同尺寸 PNG)
  GET    /health                健康检查
  GET    /docs                  Swagger UI

worker 串行处理任务队列(整本单引擎)。客户端轮询 GET /tasks/{id} 或等回调。
"""
from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

import fitz  # PyMuPDF

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from config import settings
from normalize import normalize_pdf_to_a4
from store import (
    _now_iso,
    _task_queue,
    new_task_id,
    new_work_dir,
    start_background_workers,
    tasks_store,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("unified.service")


def _pdf_page_count(path: Path) -> int:
    doc = fitz.open(str(path))
    try:
        return int(doc.page_count)
    finally:
        doc.close()


# 启动 worker / 清理线程(daemon)。放这里确保只启动一次(本模块加载即启动);
# store.py 是全局单例,worker 拿到的 tasks_store / _task_queue 与本文件用的是同一份。
start_background_workers()

app = FastAPI(
    title="统一 PDF 翻译服务",
    version="1.0.0",
    description="v3(复用文字层,版面保持)+ RetainPDF(OCR)整本单引擎路由与合并",
)


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    try:
        n = tasks_store.cleanup_orphan_dirs()
        if n:
            logger.info(f"startup orphan-dir cleanup removed {n} dir(s)")
    except Exception:  # noqa: BLE001
        logger.exception("startup orphan-dir cleanup error")
    # 预热 babeldoc 资源(首次加载模型/字体),避免第一个任务卡很久
    try:
        from v3_worker import warmup

        warmup()
    except Exception:  # noqa: BLE001
        logger.warning("v3 warmup skipped")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "work_root": settings.work_root}


@app.post("/normalize")
async def normalize(file: UploadFile = File(..., description="待标准化的 PDF")):
    """把 PDF 每页等比缩放到 A4(居中 contain),返回标准化后的 PDF。"""
    data = await file.read()
    try:
        normalized = normalize_pdf_to_a4(data)
    except ValueError:
        raise HTTPException(400, "空文件")
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return Response(
        content=normalized,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="normalized.pdf"'},
    )


def _build_image_inputs(
    lang_in: str,
    lang_out: str,
    openai_api_key: str | None,
    openai_model: str | None,
    openai_base_url: str | None,
    paddle_token: str | None,
    mode: str,
    custom_system_prompt: str | None,
    enable_table_translation: bool,
) -> dict:
    """图片翻译提交参数。"""
    return {
        "lang_in": lang_in,
        "lang_out": lang_out,
        "openai_api_key": openai_api_key,
        "openai_model": openai_model,
        "openai_base_url": openai_base_url,
        "paddle_token": paddle_token,
        "mode": mode,
        "custom_system_prompt": custom_system_prompt,
        "enable_table_translation": enable_table_translation,
    }


@app.post("/images/translate")
async def translate_image_async(
    file: UploadFile = File(..., description="待翻译图片(png/jpg/webp 等,单张)"),
    lang_in: str = Form("en"),
    lang_out: str = Form("zh"),
    openai_api_key: str | None = Form(None),
    openai_model: str | None = Form(None),
    openai_base_url: str | None = Form(None),
    paddle_token: str | None = Form(None, description="RetainPDF OCR 用;缺则服务端 env 兜底"),
    mode: str = Form("fast", description="RetainPDF 翻译模式:fast/precise/sci"),
    custom_system_prompt: str | None = Form(None),
    enable_table_translation: bool = Form(False),
) -> JSONResponse:
    """图片翻译:先提交、后下载。

    提交即返回 task_id(202),后台 worker 跑 图→PDF→RetainPDF→译文图;
    轮询用 GET /tasks/{id},成功后 GET /images/translate/{id} 下载译文图。
    """
    if not file.filename:
        raise HTTPException(400, "需要上传图片文件")
    data = await file.read()

    # 提交即校验图片合法性 + 记录尺寸,坏图立即 400(而非后台任务失败)
    from image_translate import image_dimensions

    try:
        w, h = image_dimensions(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"图片解析失败: {e}") from e

    task_id = new_task_id()
    work_dir = new_work_dir(task_id)
    (work_dir / "input").write_bytes(data)

    inputs = _build_image_inputs(
        lang_in, lang_out, openai_api_key, openai_model, openai_base_url,
        paddle_token, mode, custom_system_prompt, enable_table_translation,
    )
    task = {
        "task_id": task_id,
        "kind": "image",
        "status": "pending",
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "original_filename": file.filename,
        "work_dir": str(work_dir),
        "inputs": inputs,
        "glossary_path": None,
        "callback_url": None,
        "decision": "retainpdf",
        "manifest": [],
        "sub_tasks": {},
        "progress": 0.0,
        "translated_pages": 0,
        "total_pages": 0,
        "stage": "",
        "image_size": f"{w}x{h}",
        "result_files": [],
        "error": None,
        "traceback": None,
    }
    tasks_store.add(task)
    _task_queue.put(task_id)

    return JSONResponse(tasks_store.public_view(task_id), status_code=202)


@app.get("/images/translate/{task_id}")
def get_image_result(task_id: str) -> FileResponse:
    """下载译文图(与输入同尺寸 PNG)。任务 succeeded 后才能取;轮询状态用 GET /tasks/{id}。"""
    task = tasks_store.get(task_id)
    if task is None or task.get("kind") != "image":
        raise HTTPException(404, "task not found")
    if task.get("status") != "succeeded":
        raise HTTPException(409, f"task status is {task.get('status')}, not succeeded")
    result_files = task.get("result_files") or []
    if not result_files:
        raise HTTPException(404, "no result files")
    name = result_files[0]
    path = Path(task["work_dir"]) / "output" / name
    if not path.exists():
        raise HTTPException(404, f"file not found on disk: {name}")
    return FileResponse(path, media_type="image/png", filename=name)


@app.post("/tasks", response_model_exclude_none=True)
async def create_task(
    file: UploadFile = File(..., description="待翻译的 PDF(原生/扫描/混合均可)"),
    lang_in: str = Form("en"),
    lang_out: str = Form("zh"),
    concurrency: int = Form(4),
    openai_api_key: str | None = Form(None),
    openai_model: str | None = Form(None),
    openai_base_url: str | None = Form(None),
    paddle_token: str | None = Form(None, description="RetainPDF 用(扫描页);缺则服务端 env 兜底"),
    mode: str = Form("fast", description="RetainPDF 翻译模式:fast/precise/sci"),
    no_mono: bool = Form(False, description="v3 用:不输出单语 PDF"),
    no_watermark: bool = Form(True, description="v3 用:去水印"),
    glossary: UploadFile | None = File(None, description="可选术语表 CSV/XLSX，表头支持 source,target,src_lng,tgt_lng,level"),
    glossary_hard: bool = Form(False, description="对命中的术语启用占位符硬约束；只作用于成功匹配的 source"),
    custom_system_prompt: str | None = Form(None),
    callback_url: str | None = Form(None),
    text_based: bool = Form(False, description="文本型 PDF(有文字层)填 true 复用文字层直译;扫描件/图片型 PDF 填 false 走 OCR"),
    enable_table_translation: bool = Form(False, description="RetainPDF 扫描页:是否翻译表格(默认 False=跳过表格)"),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只接受 .pdf 文件")

    # 校验 LLM 凭证:客户端没传且服务端也没配 → 报错
    if not (openai_api_key or settings.llm_api_key):
        raise HTTPException(500, "服务端未配置 LLM_API_KEY,请在请求里传 openai_api_key")

    task_id = new_task_id()
    work_dir = new_work_dir(task_id)

    # 保存上传 PDF
    input_path = work_dir / "input.pdf"
    input_path.write_bytes(await file.read())
    try:
        total_pages = _pdf_page_count(input_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"无法读取 PDF 页数: {exc}") from exc

    # 保存可选术语表(支持 .csv/.xlsx)
    glossary_path: str | None = None
    if glossary is not None and glossary.filename:
        suffix = Path(glossary.filename).suffix.lower()
        if suffix not in (".csv", ".xlsx"):
            raise HTTPException(400, "术语表只接受 .csv / .xlsx 文件")
        gp = work_dir / f"glossary{suffix}"
        gp.write_bytes(await glossary.read())
        glossary_path = str(gp)

    inputs = {
        "lang_in": lang_in,
        "lang_out": lang_out,
        "concurrency": concurrency,
        "openai_api_key": openai_api_key,
        "openai_model": openai_model,
        "openai_base_url": openai_base_url,
        "paddle_token": paddle_token,
        "mode": mode,
        "no_mono": no_mono,
        "no_watermark": no_watermark,
        "glossary_hard": glossary_hard,
        "custom_system_prompt": custom_system_prompt,
        "text_based": text_based,
        "enable_table_translation": enable_table_translation,
    }

    task = {
        "task_id": task_id,
        "status": "pending",
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "original_filename": file.filename,
        "work_dir": str(work_dir),
        "inputs": inputs,
        "glossary_path": glossary_path,
        "callback_url": callback_url,
        "decision": "",
        "manifest": [],
        "sub_tasks": {},
        "progress": 0.0,
        "translated_pages": 0,
        "total_pages": total_pages,
        "stage": "",
        "result_files": [],
        "error": None,
        "traceback": None,
    }
    tasks_store.add(task)
    _task_queue.put(task_id)

    return JSONResponse(tasks_store.public_view(task_id), status_code=202)


@app.get("/tasks")
def list_tasks() -> list[dict]:
    return [tasks_store.public_view(t["task_id"]) for t in tasks_store.list()]


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = tasks_store.get(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return tasks_store.public_view(task_id)


@app.get("/tasks/{task_id}/result")
def get_result(
    task_id: str,
    file: str | None = None,
    type: str | None = None,
):
    """下载合并产物。file 精确指定 / type=mono|dual|bilingual / 都不传返回首个。"""
    task = tasks_store.get(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.get("status") != "succeeded":
        raise HTTPException(409, f"task status is {task.get('status')}, not succeeded")
    result_files = task.get("result_files") or []
    if not result_files:
        raise HTTPException(404, "no result files")

    if file:
        name = file
    elif type:
        if type in ("mono", "dual"):
            matched = [f for f in result_files if f.endswith(f".{type}.pdf")]
        elif type == "bilingual":
            matched = [f for f in result_files if f.endswith(".bilingual.csv")]
        else:
            raise HTTPException(400, "type must be 'mono' | 'dual' | 'bilingual'")
        if not matched:
            raise HTTPException(404, f"no {type} file in results: {result_files}")
        name = matched[0]
    else:
        name = result_files[0]

    if name not in result_files:
        raise HTTPException(404, f"file {name} not in results: {result_files}")

    path = Path(task["work_dir"]) / "output" / name
    if not path.exists():
        raise HTTPException(404, f"file not found on disk: {name}")

    media_type = "text/csv; charset=utf-8" if name.lower().endswith(".csv") else "application/pdf"
    return FileResponse(path, media_type=media_type, filename=name)


@app.get("/tasks/{task_id}/bilingual")
def get_bilingual_live(task_id: str):
    """实时下载当前已翻译的原文↔译文对照表 CSV(运行中也能拉,只含已翻完的部分)。

    与 /result?type=bilingual(要求任务 succeeded)不同,此接口不 gate 状态,
    从两引擎的中间产物即时聚合:
      - v3:      v3_work/**/translate_tracking.json(随进度落盘)
      - retain:  retain_work/translated/page-NNN-*.json(逐页 flush)
    返回 CSV 两列 + 响应头 X-Translated-Rows(当前已翻行数)。
    """
    from retain import collect_live_bilingual_rows as retain_rows
    from v3_worker import collect_bilingual_rows as v3_rows

    task = tasks_store.get(task_id)
    if task is None:
        raise HTTPException(404, "task not found")

    work_dir = Path(task["work_dir"])
    inputs = task.get("inputs", {}) or {}
    lang_in = inputs.get("lang_in", "en")
    lang_out = inputs.get("lang_out", "zh")

    rows: list[tuple[str, str]] = []
    rows.extend(v3_rows(work_dir / "v3_work"))
    rows.extend(retain_rows(work_dir / "retain_work"))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"original ({lang_in})", f"translated ({lang_out})"])
    writer.writerows(rows)

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{task_id}.bilingual.csv"',
            "X-Translated-Rows": str(len(rows)),
        },
    )


@app.delete("/tasks/{task_id}")
def delete_task(task_id: str) -> dict:
    task = tasks_store.get(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.get("status") in ("pending", "running"):
        raise HTTPException(409, "task not finished, cannot delete")
    tasks_store.delete(task_id)
    return {"deleted": task_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)



