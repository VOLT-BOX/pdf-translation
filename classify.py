# -*- coding: utf-8 -*-
"""整本单引擎路由 + PDF 按页切片(留作混合件兜底)+ manifest 生成。

路由决策(整本二选一):
  - text_based=False(默认)→ 整本走 RetainPDF(OCR),原生页也统一 OCR
  - text_based=True      → 整本走 v3(复用文字层,版面保持好)

不再逐页做图片覆盖率/文字层判定:逐页切分会造成 v3↔OCR 引擎边界处的跨页断句
无法续接(连续段检测是引擎内全局做的,跨引擎边界接不上)。整本单引擎后,
manifest 只有单一引擎,split_pdf 不物理切片,merge 阶段实际是直通。

manifest 记录"原页号 → 引擎 → 子任务内页号"的映射,合并阶段据此按原页序拼回。
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

# 引擎名(与 manifest / orchestrate / merge 中保持一致)
ENGINE_V3 = "v3"
ENGINE_RETAIN = "retainpdf"


def classify_pages(pdf_path: str | Path, *, text_based: bool = False) -> list[bool]:
    """返回每页是否原生页(True=原生→v3,False=OCR→RetainPDF)。

    text_based=False(默认)整本走 OCR;text_based=True 整本走 v3。整本单引擎,
    不做逐页判定——跨引擎边界的跨页断句无法续接,整本统一才是正确解。
    """
    doc = fitz.open(str(pdf_path))
    try:
        return [text_based] * doc.page_count
    finally:
        doc.close()


def decide(native_flags: list[bool]) -> str:
    """根据逐页标记给出路由决策:v3 / retainpdf / mixed。"""
    any_native = any(native_flags)
    any_scanned = any(not f for f in native_flags)
    if any_native and any_scanned:
        return "mixed"
    if any_scanned:
        return "retainpdf"
    return "v3"  # 全原生(含空 PDF 退化情况)


def build_manifest(native_flags: list[bool]) -> list[tuple[int, str, int]]:
    """生成 manifest:[(original_page_idx, engine, sub_page_idx), ...]

    sub_page_idx 为该页在切片后子 PDF(native.pdf / scanned.pdf)中的页序。
    全原生/全扫描时子 PDF 即原 PDF,sub_page_idx == original_page_idx。
    """
    native_sub = 0
    scanned_sub = 0
    manifest: list[tuple[int, str, int]] = []
    for orig_idx, is_native in enumerate(native_flags):
        if is_native:
            manifest.append((orig_idx, ENGINE_V3, native_sub))
            native_sub += 1
        else:
            manifest.append((orig_idx, ENGINE_RETAIN, scanned_sub))
            scanned_sub += 1
    return manifest


def split_pdf(
    pdf_path: str | Path,
    native_flags: list[bool],
    out_dir: str | Path,
) -> dict[str, str | None]:
    """按页切片出 native.pdf / scanned.pdf。

    全原生或全扫描时不切片(返回对应键指向原文件,另一键为 None),省一份 IO。
    混合件才物理切:用 insert_pdf(from_page,to_page) 逐页插入对应子文档
    (扫描页可能是非连续的 1/3/5 页,必须物理切,start_page/end_page 不适用)。

    返回 {"v3": path|None, "retainpdf": path|None}。
    """
    pdf_path = str(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    any_native = any(native_flags)
    any_scanned = any(not f for f in native_flags)

    if any_native and not any_scanned:
        return {"v3": pdf_path, "retainpdf": None}
    if any_scanned and not any_native:
        return {"v3": None, "retainpdf": pdf_path}

    # 混合:物理切
    native_path = out_dir / "native.pdf"
    scanned_path = out_dir / "scanned.pdf"

    src = fitz.open(pdf_path)
    native_doc = fitz.open()
    scanned_doc = fitz.open()
    try:
        for orig_idx, is_native in enumerate(native_flags):
            target = native_doc if is_native else scanned_doc
            target.insert_pdf(src, from_page=orig_idx, to_page=orig_idx)
        native_doc.save(str(native_path))
        scanned_doc.save(str(scanned_path))
    finally:
        src.close()
        native_doc.close()
        scanned_doc.close()

    return {"v3": str(native_path), "retainpdf": str(scanned_path)}


def classify_and_split(
    pdf_path: str | Path, out_dir: str | Path, *, text_based: bool = False
) -> tuple[str, list[tuple[int, str, int]], dict[str, str | None]]:
    """一步完成:分类 → 决策 → manifest → (必要时)切片。

    返回 (decision, manifest, engine_pdf_map)。
    """
    flags = classify_pages(pdf_path, text_based=text_based)
    decision = decide(flags)
    manifest = build_manifest(flags)
    engine_map = split_pdf(pdf_path, flags, out_dir)
    return decision, manifest, engine_map
