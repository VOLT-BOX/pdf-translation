# -*- coding: utf-8 -*-
"""图片翻译:图 → PDF → RetainPDF(OCR)→ 译文图,译文图与原图同尺寸。

尺寸还原的关键:图转 PDF 时**等比缩放、不留白**(长边压到 MAX_PAGE_PT 内,宽高比不变),
这样既避免"像素当 point"的巨纸(夹死翻译字号体系),又让译文 PDF 页面与源图成同一比例;
输出时按 `原宽 / 译文页宽` 的缩放因子光栅化,即可精确还原回原始 W×H 像素。
"""
from __future__ import annotations

import io

import fitz  # PyMuPDF
from PIL import Image, ImageOps

# 页面长边上限(pt)。A4 长边 842pt:把任意尺寸的图片等比压到 ≤842pt,
# 避免 3000+px 的图变成 3000+pt 的巨纸。等比(不 letterbox)是为了宽高比不变,
# 输出光栅化时按同一比例即可 1:1 还原原始像素尺寸。
MAX_PAGE_PT = 842.0


def image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """返回图片 (宽px, 高px),EXIF 摆正后。空/损坏图片抛异常。

    供提交即校验用(坏图立即 400,而非拖到后台任务失败)。
    """
    img = Image.open(io.BytesIO(image_bytes))
    img.load()
    img = ImageOps.exif_transpose(img)
    w, h = img.size
    if w <= 0 or h <= 0:
        raise ValueError("无效图片尺寸")
    return w, h


def image_to_pdf(image_bytes: bytes) -> tuple[bytes, int, int]:
    """图片字节 → 单页 PDF 字节。返回 (pdf_bytes, 原宽px, 原高px)。

    图片等比例缩放到长边 ≤ MAX_PAGE_PT,整页铺满(无留白)。空/损坏图片抛 ValueError。
    """
    img = Image.open(io.BytesIO(image_bytes))
    img.load()
    img = ImageOps.exif_transpose(img)  # 手机照片 EXIF 方向摆正,避免横竖颠倒
    w, h = img.size
    if w <= 0 or h <= 0:
        raise ValueError("无效图片尺寸")

    # 转 RGB(去 alpha)+ PNG 供 PyMuPDF 插入
    png_buf = io.BytesIO()
    img.convert("RGB").save(png_buf, format="PNG")
    png = png_buf.getvalue()

    scale = min(1.0, MAX_PAGE_PT / max(w, h))
    pw, ph = w * scale, h * scale  # 页面尺寸(pt),与像素等比

    doc = fitz.open()
    try:
        page = doc.new_page(width=pw, height=ph)
        page.insert_image(fitz.Rect(0, 0, pw, ph), stream=png)
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue(), w, h
    finally:
        doc.close()


def pdf_page_to_image(pdf_bytes: bytes, orig_w: int, orig_h: int) -> bytes:
    """译文 PDF 首页 → 与原图同尺寸(orig_w × orig_h px)的 PNG 字节。

    按 `orig_w / 译文页宽` 光栅化,再用 Pillow 精确对齐到 (orig_w, orig_h),
    规避浮点缩放取整带来的 ±1px 偏差。空页/损坏抛 RuntimeError。
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]
        pw, ph = float(page.rect.width), float(page.rect.height)
        if pw <= 0 or ph <= 0:
            raise RuntimeError("译文 PDF 页面尺寸无效")

        zoom = orig_w / pw
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if img.size != (orig_w, orig_h):
            img = img.resize((orig_w, orig_h), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()
