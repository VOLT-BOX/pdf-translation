from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from statistics import median

import fitz

from services.document_schema.semantics import is_caption_like_block
from services.document_schema.semantics import is_footnote_like_block
from services.rendering.layout.font_fit import page_baseline_font_size
from services.rendering.layout.payload.metrics import block_metrics
from services.rendering.layout.payload.metrics import box_capacity_units
from services.rendering.layout.payload.metrics import text_demand_units
from services.rendering.layout.inline_content.mode_router import is_direct_typst_math_mode
from services.rendering.layout.payload.first_line_indent import detect_first_line_indent_pt_with_displaylist
from services.rendering.layout.payload.first_line_indent import is_first_line_indent_candidate
from services.rendering.layout.payload.render_item import clear_render_fields
from services.rendering.layout.payload.render_item import group_render_unit_items
from services.rendering.layout.payload.render_item import group_unit_formula_map
from services.rendering.layout.payload.render_item import group_unit_protected_text
from services.rendering.layout.payload.render_item import group_unit_source_text
from services.rendering.layout.payload.render_item import item_has_group_render_text
from services.rendering.layout.payload.render_item import seed_render_fields
from services.rendering.layout.payload.shared import same_meaningful_render_text
from services.rendering.layout.payload.shared import split_protected_text_for_boxes
from services.rendering.layout.payload.suspicious_ocr import detect_and_drop_suspicious_ocr_glued_blocks
from services.rendering.layout.typography.geometry import inner_bbox
from services.rendering.layout.typography.measurement import bbox_width
from services.document_schema.semantics import block_kind


CONTINUATION_NARROW_BOX_MIN_NEIGHBOR_RATIO = 0.78
CONTINUATION_NARROW_BOX_CAPACITY_RELAX_RATIO = 0.72


def _is_annotation_like(item: dict) -> bool:
    return is_caption_like_block(item) or is_footnote_like_block(item)


def _inner_bbox_width(item: dict) -> float:
    bbox = inner_bbox(item)
    if len(bbox) != 4:
        return 0.0
    return max(0.0, bbox[2] - bbox[0])


def _continuation_adjusted_capacities(items: list[dict], capacities: list[float]) -> list[float]:
    if len(items) != len(capacities) or len(items) < 3:
        return capacities

    widths = [_inner_bbox_width(item) for item in items]
    adjusted = list(capacities)
    for index in range(1, len(items) - 1):
        current_width = widths[index]
        if current_width <= 0:
            continue
        prev_width = widths[index - 1]
        next_width = widths[index + 1]
        if prev_width <= 0 or next_width <= 0:
            continue
        neighbor_avg = (prev_width + next_width) / 2.0
        if neighbor_avg <= 0:
            continue
        if current_width >= neighbor_avg * CONTINUATION_NARROW_BOX_MIN_NEIGHBOR_RATIO:
            continue
        width_ratio = current_width / neighbor_avg
        relax_ratio = max(width_ratio, CONTINUATION_NARROW_BOX_CAPACITY_RELAX_RATIO)
        adjusted[index] = capacities[index] * relax_ratio
    return adjusted


def _attach_first_line_indents(
    prepared: dict[int, list[dict]],
    page_metrics: dict[int, tuple[float, float, float, float, float]],
    source_doc: fitz.Document | None,
    first_line_indent_lookup: dict[str, float] | None = None,
) -> None:
    if source_doc is None and not first_line_indent_lookup:
        return
    for page_idx, items in prepared.items():
        if page_idx not in page_metrics:
            continue
        page_font_size, page_line_pitch, page_line_height, density_baseline, page_text_width_med = page_metrics[page_idx]
        displaylist: fitz.DisplayList | None = None
        for item in items:
            item_id = str(item.get("item_id", "") or "")
            if first_line_indent_lookup is not None and item_id:
                indent_pt = float(first_line_indent_lookup.get(item_id, 0.0) or 0.0)
                if indent_pt > 0:
                    item["_render_first_line_indent_pt"] = indent_pt
                continue
            if source_doc is None:
                continue
            font_size_pt, _leading_em = block_metrics(
                item,
                page_font_size,
                page_line_pitch,
                page_line_height,
                density_baseline,
                page_text_width_med,
            )
            if not is_first_line_indent_candidate(item, page_text_width_med=page_text_width_med):
                continue
            if displaylist is None:
                displaylist = source_doc[page_idx].get_displaylist()
            indent_pt = detect_first_line_indent_pt_with_displaylist(
                source_doc,
                displaylist,
                item,
                page_idx=page_idx,
                font_size_pt=font_size_pt,
                page_text_width_med=page_text_width_med,
            )
            if indent_pt > 0:
                item["_render_first_line_indent_pt"] = indent_pt


def prepare_render_payloads_by_page(
    translated_pages: dict[int, list[dict]],
    *,
    source_pdf_path: Path | None = None,
    first_line_indent_lookup: dict[str, float] | None = None,
    effective_inner_bbox_lookup: dict[str, list[float]] | None = None,
) -> dict[int, list[dict]]:
    prepared = {page_idx: deepcopy(items) for page_idx, items in translated_pages.items()}
    if not prepared:
        return prepared

    source_doc = fitz.open(source_pdf_path) if source_pdf_path and first_line_indent_lookup is None else None
    page_metrics: dict[int, tuple[float, float, float, float, float]] = {}
    flat_items: list[dict] = []
    try:
        for page_idx in sorted(prepared):
            items = prepared[page_idx]
            page_font_size, page_line_pitch, page_line_height, density_baseline = page_baseline_font_size(items)
            text_widths = [bbox_width(item) for item in items if block_kind(item) == "text" and not _is_annotation_like(item)]
            page_text_width_med = median(text_widths) if text_widths else 0.0
            page_metrics[page_idx] = (
                page_font_size,
                page_line_pitch,
                page_line_height,
                density_baseline,
                page_text_width_med,
            )
            for item in items:
                seed_render_fields(item)
                item_id = str(item.get("item_id", "") or "")
                if effective_inner_bbox_lookup is not None and item_id:
                    effective_bbox = effective_inner_bbox_lookup.get(item_id)
                    if isinstance(effective_bbox, list) and len(effective_bbox) == 4:
                        item["_render_inner_bbox"] = [float(value) for value in effective_bbox]
                flat_items.append(item)

        _attach_first_line_indents(prepared, page_metrics, source_doc, first_line_indent_lookup)

        units = group_render_unit_items(flat_items)

        for _, items in units.items():
            items = [item for item in items if item_has_group_render_text(item)]
            if not items:
                continue
            direct_math_mode = any(is_direct_typst_math_mode(item) for item in items)
            unit_formula_map = group_unit_formula_map(items)
            protected_unit_text = group_unit_protected_text(items)
            protected_unit_source_text = group_unit_source_text(items)
            if same_meaningful_render_text(protected_unit_source_text, protected_unit_text):
                for item in items:
                    clear_render_fields(item)
                continue
            capacities: list[float] = []
            source_weights: list[float] = []
            for item in items:
                page_font_size, page_line_pitch, page_line_height, density_baseline, page_text_width_med = page_metrics[
                    item.get("page_idx", 0)
                ]
                font_size_pt, leading_em = block_metrics(
                    item,
                    page_font_size,
                    page_line_pitch,
                    page_line_height,
                    density_baseline,
                    page_text_width_med,
                )
                capacities.append(box_capacity_units(inner_bbox(item), font_size_pt, leading_em))
                source_weights.append(
                    text_demand_units(
                        item.get("protected_source_text") or "",
                        item.get("formula_map", []),
                    )
                )
            capacities = _continuation_adjusted_capacities(items, capacities)

            chunks = split_protected_text_for_boxes(
                protected_unit_text,
                unit_formula_map,
                capacities,
                preferred_weights=source_weights if any(weight > 0 for weight in source_weights) else None,
                direct_math_mode=direct_math_mode,
            )
            source_chunks = split_protected_text_for_boxes(
                protected_unit_source_text,
                unit_formula_map,
                capacities,
                preferred_weights=source_weights if any(weight > 0 for weight in source_weights) else None,
            )
            for item, chunk, source_chunk in zip(items, chunks, source_chunks):
                item["render_protected_text"] = chunk
                item["render_source_text"] = source_chunk
                item["render_formula_map"] = unit_formula_map

        suspicious_total = 0
        for page_idx, items in prepared.items():
            page_font_size, page_line_pitch, page_line_height, density_baseline, page_text_width_med = page_metrics[page_idx]
            summary = detect_and_drop_suspicious_ocr_glued_blocks(
                items,
                page_idx=page_idx,
                page_font_size=page_font_size,
                page_line_pitch=page_line_pitch,
                page_line_height=page_line_height,
                density_baseline=density_baseline,
                page_text_width_med=page_text_width_med,
            )
            suspicious_total += summary["count"]
            for hit in summary["hits"]:
                print(
                    "render skip suspicious OCR-glued block "
                    f"page={hit['page_idx'] + 1} item={hit['item_id']} next={hit['next_item_id']} "
                    f"chars={hit['source_chars']} chars_per_pt={hit['char_height_ratio']:.2f} "
                    f"gap={hit['source_gap_pt']:.1f} overlap={hit['width_overlap_ratio']:.3f} "
                    f"est={hit['estimated_height_pt']:.1f} allowed={hit['allowed_height_pt']:.1f} "
                    f"overflow={hit['overflow_ratio']:.2f}",
                    flush=True,
                )
        if suspicious_total:
            print(f"render skip suspicious OCR-glued blocks total={suspicious_total}", flush=True)
    finally:
        if source_doc is not None:
            source_doc.close()

    return prepared
