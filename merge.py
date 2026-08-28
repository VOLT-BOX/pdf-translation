# -*- coding: utf-8 -*-
"""结果合并:按原页序拼 mono/dual PDF + concat bilingual CSV。

manifest 来自 classify.build_manifest,记录 [(orig_page_idx, engine, sub_idx), ...]。
合并时按 orig_page_idx 升序逐页从对应引擎产物中取第 sub_idx 页插入。

engine_paths: {"v3": mono_pdf_path|None, "retainpdf": mono_pdf_path|None}。
"""
from __future__ import annotations

import csv
from pathlib import Path

import fitz  # PyMuPDF

from classify import ENGINE_RETAIN, ENGINE_V3


def _ordered_manifest(manifest: list[tuple[int, str, int]]):
    return sorted(manifest, key=lambda x: x[0])


def merge_pdf_by_manifest(
    manifest: list[tuple[int, str, int]],
    engine_paths: dict[str, str | None],
    out_path: str | Path,
) -> bool:
    """按原页序合并 PDF(mono 或 dual 通用)。

    返回是否成功生成(out_path 存在且页数 > 0)。
    任一引擎产物缺失对应页时跳过该页(尽力而为,不报错)。
    """
    ordered = _ordered_manifest(manifest)
    out = fitz.open()
    try:
        # 预开各引擎源文档(避免逐页重复打开)
        src_docs: dict[str, fitz.Document | None] = {}
        for eng in (ENGINE_V3, ENGINE_RETAIN):
            p = engine_paths.get(eng)
            if p and Path(p).exists():
                src_docs[eng] = fitz.open(p)
            else:
                src_docs[eng] = None

        for _orig_idx, engine, sub_idx in ordered:
            src = src_docs.get(engine)
            if src is None or sub_idx >= src.page_count:
                continue
            out.insert_pdf(src, from_page=sub_idx, to_page=sub_idx)

        if out.page_count == 0:
            return False
        out.save(str(out_path))
        return True
    finally:
        out.close()
        for d in (src_docs.get(ENGINE_V3), src_docs.get(ENGINE_RETAIN)):
            if d is not None:
                d.close()


def concat_csv(paths: list[str | Path | None], out_path: str | Path) -> bool:
    """拼接多份 bilingual CSV(原文↔译文对照表)。

    两引擎 CSV 无页号列,无法严格按原页排序——直接 concat(行序:传入顺序)。
    表头取首份,后续份跳过表头。统一写 UTF-8 BOM(Excel 友好)。
    返回是否生成(至少有一份非空 CSV)。
    """
    writer = None
    wrote_any = False
    out_path = Path(out_path)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        for p in paths:
            if not p:
                continue
            p = Path(p)
            if not p.exists():
                continue
            # 兼容 BOM 与无 BOM:utf-8-sig 能读两者
            try:
                with open(p, "r", encoding="utf-8-sig", errors="replace", newline="") as inp:
                    reader = csv.reader(inp)
                    header = next(reader, None)
                    if header is None:
                        continue
                    if writer is None:
                        writer = csv.writer(f)
                        writer.writerow(header)
                    for row in reader:
                        writer.writerow(row)
                        wrote_any = True
            except Exception:  # noqa: BLE001
                # 单份 CSV 读失败不影响其余拼接
                continue
    if not wrote_any:
        try:
            out_path.unlink()
        except OSError:
            pass
        return False
    return True


def check_dual_compatible(
    manifest: list[tuple[int, str, int]],
    engine_paths: dict[str, str | None],
) -> bool:
    """混合件 dual 合并前置检查:各引擎 dual 页数须覆盖其 manifest 中的 sub_idx。

    均匀路由(单引擎)只要该引擎 dual 存在且页数足够即可。
    返回 False 时调用方应跳过 dual(下载返回 404 + 原因)。
    """
    ordered = _ordered_manifest(manifest)
    # 各引擎所需的最大 sub_idx + 1
    need: dict[str, int] = {ENGINE_V3: 0, ENGINE_RETAIN: 0}
    for _orig, engine, sub_idx in ordered:
        need[engine] = max(need[engine], sub_idx + 1)

    for eng in (ENGINE_V3, ENGINE_RETAIN):
        if need[eng] == 0:
            continue
        p = engine_paths.get(eng)
        if not p or not Path(p).exists():
            return False
        try:
            doc = fitz.open(p)
            if doc.page_count < need[eng]:
                doc.close()
                return False
            doc.close()
        except Exception:  # noqa: BLE001
            return False
    return True
