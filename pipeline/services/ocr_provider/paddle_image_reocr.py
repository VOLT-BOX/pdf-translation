from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from typing import Callable

import fitz


OcrCropFn = Callable[[Path, int], dict[str, Any]]

IMAGE_REOCR_ENV = "RETAIN_PADDLE_IMAGE_REOCR"
IMAGE_REOCR_MAX_BLOCKS_ENV = "RETAIN_PADDLE_IMAGE_REOCR_MAX_BLOCKS"
IMAGE_REOCR_MIN_AREA_RATIO_ENV = "RETAIN_PADDLE_IMAGE_REOCR_MIN_AREA_RATIO"
IMAGE_REOCR_MIN_TEXT_CHARS_ENV = "RETAIN_PADDLE_IMAGE_REOCR_MIN_TEXT_CHARS"

_TABLE_HTML_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_IMAGE_LABELS = {"image", "header_image", "footer_image"}
_SECONDARY_SKIP_LABELS = {"image", "header_image", "footer_image", "chart"}
_SECONDARY_TEXT_LABEL_REMAP = {
    "header": "text",
    "footer": "text",
    "number": "text",
    "footnote": "text",
    "aside_text": "text",
    "vision_footnote": "text",
}


def paddle_image_reocr_enabled() -> bool:
    raw = str(os.environ.get(IMAGE_REOCR_ENV, "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def augment_paddle_payload_with_image_reocr(
    payload: dict[str, Any],
    *,
    source_pdf_path: Path,
    work_dir: Path,
    ocr_crop: OcrCropFn,
    enabled: bool | None = None,
) -> dict[str, Any]:
    if enabled is None:
        enabled = paddle_image_reocr_enabled()
    if not enabled:
        return payload
    if not source_pdf_path.exists():
        return payload

    result = deepcopy(payload)
    layout_results = result.get("layoutParsingResults")
    if not isinstance(layout_results, list) or not layout_results:
        return result

    max_blocks = _env_int(IMAGE_REOCR_MAX_BLOCKS_ENV, 6)
    rescued_count = 0
    replaced_count = 0
    attempted_count = 0
    work_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(source_pdf_path) as pdf:
        for page_index, page_payload in enumerate(layout_results):
            if attempted_count >= max_blocks:
                break
            if not isinstance(page_payload, dict) or page_index >= len(pdf):
                continue
            pruned = page_payload.get("prunedResult")
            if not isinstance(pruned, dict):
                continue
            blocks = pruned.get("parsing_res_list")
            if not isinstance(blocks, list) or not blocks:
                continue
            raw_width = _positive_float(pruned.get("width"))
            raw_height = _positive_float(pruned.get("height"))
            if raw_width <= 0 or raw_height <= 0:
                raw_width, raw_height = _page_dimensions_from_data_info(result, page_index)
            if raw_width <= 0 or raw_height <= 0:
                continue

            patched_blocks: list[dict[str, Any]] = []
            page_replaced = False
            for block_index, block in enumerate(blocks):
                if attempted_count >= max_blocks or not _is_image_reocr_candidate(
                    block,
                    page_width=raw_width,
                    page_height=raw_height,
                ):
                    patched_blocks.append(block)
                    continue
                bbox = _normalized_bbox(block.get("block_bbox"))
                if bbox is None:
                    patched_blocks.append(block)
                    continue
                attempted_count += 1
                crop_pdf_path = work_dir / f"page-{page_index + 1:03d}-image-{block_index + 1:03d}.pdf"
                try:
                    crop_meta = write_pdf_crop_for_reocr(
                        pdf=pdf,
                        page_index=page_index,
                        raw_bbox=bbox,
                        raw_page_width=raw_width,
                        raw_page_height=raw_height,
                        output_pdf_path=crop_pdf_path,
                    )
                    secondary_payload = ocr_crop(crop_pdf_path, attempted_count)
                    rescued_blocks = _mapped_secondary_blocks(
                        secondary_payload,
                        parent_bbox=bbox,
                        crop_width=float(crop_meta["crop_width"]),
                        crop_height=float(crop_meta["crop_height"]),
                    )
                except Exception as exc:  # noqa: BLE001
                    patched = deepcopy(block)
                    patched.setdefault("_image_reocr", {})
                    if isinstance(patched["_image_reocr"], dict):
                        patched["_image_reocr"].update({"attempted": True, "error": str(exc)[:500]})
                    patched_blocks.append(patched)
                    continue
                if _should_replace_parent_image_block(rescued_blocks):
                    for rescued in rescued_blocks:
                        rescued.setdefault("_image_reocr", {})
                        if isinstance(rescued["_image_reocr"], dict):
                            rescued["_image_reocr"].update(
                                {
                                    "attempted": True,
                                    "applied": True,
                                    "secondary_original_block_label": str(
                                        rescued.get("_image_reocr_original_block_label", "") or ""
                                    ),
                                    "parent_block_label": str(block.get("block_label", "") or ""),
                                    "parent_block_bbox": list(bbox),
                                }
                            )
                        patched_blocks.append(rescued)
                    rescued_count += len(rescued_blocks)
                    replaced_count += 1
                    page_replaced = True
                else:
                    patched = deepcopy(block)
                    patched.setdefault("_image_reocr", {})
                    if isinstance(patched["_image_reocr"], dict):
                        patched["_image_reocr"].update(
                            {
                                "attempted": True,
                                "applied": False,
                                "secondary_blocks": len(rescued_blocks),
                            }
                        )
                    patched_blocks.append(patched)
            if page_replaced:
                pruned["parsing_res_list"] = _renumber_blocks(patched_blocks)

    meta = result.setdefault("_meta", {})
    if isinstance(meta, dict):
        meta["imageReocr"] = {
            "enabled": True,
            "attemptedBlocks": attempted_count,
            "replacedBlocks": replaced_count,
            "rescuedBlocks": rescued_count,
            "maxBlocks": max_blocks,
        }
    return result


def write_pdf_crop_for_reocr(
    *,
    pdf: fitz.Document,
    page_index: int,
    raw_bbox: list[float],
    raw_page_width: float,
    raw_page_height: float,
    output_pdf_path: Path,
) -> dict[str, float]:
    page = pdf[page_index]
    pdf_width = float(page.rect.width)
    pdf_height = float(page.rect.height)
    to_pdf_x = pdf_width / raw_page_width
    to_pdf_y = pdf_height / raw_page_height
    clip = fitz.Rect(
        raw_bbox[0] * to_pdf_x,
        raw_bbox[1] * to_pdf_y,
        raw_bbox[2] * to_pdf_x,
        raw_bbox[3] * to_pdf_y,
    ) & page.rect
    if clip.is_empty or clip.is_infinite:
        raise RuntimeError("image re-OCR crop is outside page bounds")

    matrix = fitz.Matrix(raw_page_width / pdf_width, raw_page_height / pdf_height)
    pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    if pixmap.width <= 1 or pixmap.height <= 1:
        raise RuntimeError("image re-OCR crop rendered empty")

    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    crop_pdf = fitz.open()
    try:
        crop_page = crop_pdf.new_page(width=float(pixmap.width), height=float(pixmap.height))
        crop_page.insert_image(fitz.Rect(0, 0, pixmap.width, pixmap.height), pixmap=pixmap)
        crop_pdf.save(output_pdf_path)
    finally:
        crop_pdf.close()
    return {
        "crop_width": float(pixmap.width),
        "crop_height": float(pixmap.height),
    }


def _is_image_reocr_candidate(block: object, *, page_width: float, page_height: float) -> bool:
    if not isinstance(block, dict):
        return False
    label = str(block.get("block_label", "") or "").strip().lower()
    if label not in _IMAGE_LABELS:
        return False
    bbox = _normalized_bbox(block.get("block_bbox"))
    if bbox is None:
        return False
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    if width < 80 or height < 40:
        return False
    area_ratio = (width * height) / max(1.0, page_width * page_height)
    if area_ratio < _env_float(IMAGE_REOCR_MIN_AREA_RATIO_ENV, 0.015):
        return False
    text = str(block.get("block_content", "") or "").strip()
    compact_text_len = len(re.sub(r"\s+", "", text))
    if compact_text_len >= _env_int(IMAGE_REOCR_MIN_TEXT_CHARS_ENV, 12):
        return True
    return area_ratio >= max(0.04, _env_float(IMAGE_REOCR_MIN_AREA_RATIO_ENV, 0.015) * 2.5)


def _mapped_secondary_blocks(
    secondary_payload: dict[str, Any],
    *,
    parent_bbox: list[float],
    crop_width: float,
    crop_height: float,
) -> list[dict[str, Any]]:
    layout_results = secondary_payload.get("layoutParsingResults")
    if not isinstance(layout_results, list) or not layout_results:
        return []
    page_payload = layout_results[0]
    if not isinstance(page_payload, dict):
        return []
    pruned = page_payload.get("prunedResult")
    if not isinstance(pruned, dict):
        return []
    secondary_blocks = pruned.get("parsing_res_list")
    if not isinstance(secondary_blocks, list):
        return []

    secondary_width = _positive_float(pruned.get("width")) or _page_dimensions_from_data_info(secondary_payload, 0)[0]
    secondary_height = _positive_float(pruned.get("height")) or _page_dimensions_from_data_info(secondary_payload, 0)[1]
    if secondary_width <= 0:
        secondary_width = crop_width
    if secondary_height <= 0:
        secondary_height = crop_height
    parent_width = max(1.0, parent_bbox[2] - parent_bbox[0])
    parent_height = max(1.0, parent_bbox[3] - parent_bbox[1])
    scale_x = parent_width / max(1.0, secondary_width)
    scale_y = parent_height / max(1.0, secondary_height)
    table_html = _first_table_html(page_payload)

    mapped: list[dict[str, Any]] = []
    for block in secondary_blocks:
        if not isinstance(block, dict):
            continue
        label = str(block.get("block_label", "") or "").strip().lower()
        if label in _SECONDARY_SKIP_LABELS:
            continue
        text = str(block.get("block_content", "") or "").strip()
        if not text:
            continue
        if _is_secondary_text_noise(label, text):
            continue
        bbox = _normalized_bbox(block.get("block_bbox"))
        if bbox is None:
            continue
        patched = deepcopy(block)
        if label in _SECONDARY_TEXT_LABEL_REMAP:
            patched["_image_reocr_original_block_label"] = str(patched.get("block_label", "") or "")
            patched["block_label"] = _SECONDARY_TEXT_LABEL_REMAP[label]
        if label == "table" and "<table" not in text.lower() and table_html:
            patched["block_content"] = table_html
        patched["block_bbox"] = _map_bbox_to_parent(bbox, parent_bbox, scale_x, scale_y)
        if isinstance(patched.get("block_polygon_points"), list):
            patched["block_polygon_points"] = _map_points_to_parent(
                patched["block_polygon_points"],
                parent_bbox,
                scale_x,
                scale_y,
            )
        mapped.append(patched)
    return mapped


def _is_secondary_text_noise(label: str, text: str) -> bool:
    if label == "table":
        return False
    compact = re.sub(r"\s+", "", text)
    return len(compact) <= 1


def _should_replace_parent_image_block(blocks: list[dict[str, Any]]) -> bool:
    if not blocks:
        return False
    if any(str(block.get("block_label", "") or "").strip().lower() == "table" for block in blocks):
        return True
    return len(blocks) >= 2


def _renumber_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patched_blocks: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        patched = deepcopy(block) if isinstance(block, dict) else {}
        patched["block_order"] = index + 1
        patched["global_block_id"] = index
        if not str(patched.get("block_id", "") or "").strip():
            patched["block_id"] = index
        patched_blocks.append(patched)
    return patched_blocks


def _map_bbox_to_parent(
    bbox: list[float],
    parent_bbox: list[float],
    scale_x: float,
    scale_y: float,
) -> list[float]:
    return [
        round(parent_bbox[0] + bbox[0] * scale_x, 3),
        round(parent_bbox[1] + bbox[1] * scale_y, 3),
        round(parent_bbox[0] + bbox[2] * scale_x, 3),
        round(parent_bbox[1] + bbox[3] * scale_y, 3),
    ]


def _map_points_to_parent(
    points: list,
    parent_bbox: list[float],
    scale_x: float,
    scale_y: float,
) -> list:
    mapped = []
    for point in points:
        if isinstance(point, (list, tuple)) and len(point) == 2:
            mapped.append(
                [
                    round(parent_bbox[0] + float(point[0]) * scale_x, 3),
                    round(parent_bbox[1] + float(point[1]) * scale_y, 3),
                ]
            )
        else:
            mapped.append(point)
    return mapped


def _normalized_bbox(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except Exception:
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _page_dimensions_from_data_info(payload: dict[str, Any], page_index: int) -> tuple[float, float]:
    pages = ((payload.get("dataInfo") or {}).get("pages") or [])
    if page_index >= len(pages) or not isinstance(pages[page_index], dict):
        return 0.0, 0.0
    page = pages[page_index]
    return _positive_float(page.get("width")), _positive_float(page.get("height"))


def _first_table_html(page_payload: dict[str, Any]) -> str:
    markdown = page_payload.get("markdown")
    text = str((markdown or {}).get("text", "") or "") if isinstance(markdown, dict) else ""
    match = _TABLE_HTML_RE.search(text)
    return match.group(0).strip() if match else ""


def _positive_float(value: object) -> float:
    try:
        number = float(value or 0)
    except Exception:
        return 0.0
    return number if number > 0 else 0.0


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return value if value > 0 else default


__all__ = [
    "augment_paddle_payload_with_image_reocr",
    "paddle_image_reocr_enabled",
    "write_pdf_crop_for_reocr",
]
