# -*- coding: utf-8 -*-
"""PDF 页面尺寸标准化(折成函数,供 app.py 的 /normalize 路由调用)。

把任意页面尺寸的 PDF 等比缩放到标准 A4(595×842 pt),内容 contain 居中,
不裁剪不拉伸。任意比例的图片都安全:长边撑满、短边留白居中。

场景:图片转 PDF 工具常把"像素数"直接当成"point"(默认 72DPI),
导致 3429px 的图变成 3429pt 宽的"巨纸",翻译字号体系被夹死。
标准化到 A4 后字号体系恢复正常。
"""
from __future__ import annotations

import io

import fitz  # PyMuPDF

A4_W, A4_H = 595.0, 842.0  # point,标准 A4


def normalize_pdf_to_a4(data: bytes) -> bytes:
    """把 PDF 每页等比缩放到 A4(居中 contain),返回标准化后的 PDF 字节。

    空数据抛 ValueError;失败抛 RuntimeError。全程内存,不落盘。
    """
    if not data:
        raise ValueError("空文件")

    src = fitz.open(stream=data, filetype="pdf")
    try:
        dst = fitz.open()
        for src_page in src:
            sw, sh = float(src_page.rect.width), float(src_page.rect.height)
            if sw <= 0 or sh <= 0:
                continue
            scale = min(A4_W / sw, A4_H / sh)
            rw, rh = sw * scale, sh * scale
            x0 = (A4_W - rw) / 2.0
            y0 = (A4_H - rh) / 2.0
            target_rect = fitz.Rect(x0, y0, x0 + rw, y0 + rh)

            dst_page = dst.new_page(width=A4_W, height=A4_H)
            dst_page.show_pdf_page(target_rect, src, src_page.number)

        buf = io.BytesIO()
        dst.save(buf)
        dst.close()
        return buf.getvalue()
    except Exception as e:
        raise RuntimeError(f"标准化失败: {e}") from e
    finally:
        src.close()
