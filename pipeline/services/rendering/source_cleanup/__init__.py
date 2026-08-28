from __future__ import annotations

from typing import Any

from services.rendering.source_cleanup.contracts import SourceCleanupOptions
from services.rendering.source_cleanup.contracts import SourceCleanupRequest
from services.rendering.source_cleanup.contracts import SourceCleanupResult
from services.rendering.source_cleanup.types import BBoxTextStripCandidates
from services.rendering.source_cleanup.types import BBoxTextStripPagePlan
from services.rendering.source_cleanup.types import BBoxTextStripResult


_LAZY_EXPORTS = {
    "build_bbox_text_stripped_pdf_copy": (
        "services.rendering.source_cleanup.executor",
        "build_bbox_text_stripped_pdf_copy",
    ),
    "execute_source_cleanup": (
        "services.rendering.source_cleanup.executor",
        "execute_source_cleanup",
    ),
    "plan_source_cleanup": (
        "services.rendering.source_cleanup.planning.planner",
        "plan_source_cleanup",
    ),
    "item_ids_with_uncovered_unsafe_vector_overlap": (
        "services.rendering.source_cleanup.planning.planner",
        "item_ids_with_uncovered_unsafe_vector_overlap",
    ),
    "strip_segments_for_text_rect": (
        "services.rendering.source_cleanup.planning.segments",
        "strip_segments_for_text_rect",
    ),
    "split_rect_around_guards": (
        "services.rendering.source_cleanup.planning.segments",
        "split_rect_around_guards",
    ),
    "strip_bbox_text_rects_from_pdf_copy": (
        "services.rendering.source_cleanup.pdf.document",
        "strip_bbox_text_rects_from_pdf_copy",
    ),
    "strip_bbox_text_from_page": (
        "services.rendering.source_cleanup.pdf.stream_engine",
        "strip_bbox_text_from_page",
    ),
    "strip_bbox_text_from_stream": (
        "services.rendering.source_cleanup.pdf.stream_engine",
        "strip_bbox_text_from_stream",
    ),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    from importlib import import_module

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


__all__ = [
    "SourceCleanupOptions",
    "SourceCleanupRequest",
    "SourceCleanupResult",
    "BBoxTextStripCandidates",
    "BBoxTextStripPagePlan",
    "BBoxTextStripResult",
    *sorted(_LAZY_EXPORTS),
]
