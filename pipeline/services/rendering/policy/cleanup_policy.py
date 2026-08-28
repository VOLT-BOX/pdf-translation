from __future__ import annotations

import fitz

from foundation.config import layout
from services.rendering.policy.geometry import item_rect
from services.rendering.policy.models import CleanupMode
from services.rendering.policy.models import RenderItemPolicy
from services.rendering.policy.models import RenderPagePolicy
from services.document_schema.semantics import block_kind


NON_TRANSLATED_FINAL_STATUSES = frozenset(
    {
        "empty_translation",
        "failed",
        "ignored",
        "keep_origin",
        "kept_origin",
        "not_translated",
        "preserve_source",
        "source_preserved",
        "skipped",
        "skip_translation",
    }
)
NON_TRANSLATED_DECISIONS = frozenset(
    {
        "ignore",
        "keep_origin",
        "preserve_source",
        "skip",
        "skip_translation",
    }
)
SKIP_TRANSLATION_TAGS = frozenset({"skip_translation", "keep_origin", "preserve_source"})


def item_has_formula_region(item: dict) -> bool:
    normalized_sub_type = str(item.get("normalized_sub_type") or "").strip().lower()
    raw_block_type = str(item.get("raw_block_type") or "").strip().lower()
    block_type = str(item.get("block_type") or "").strip().lower()
    return (
        block_kind(item) == "formula"
        or block_type == "formula"
        or raw_block_type == "display_formula"
        or normalized_sub_type == "display_formula"
    )


def page_has_formula_region(translated_items: list[dict]) -> bool:
    return any(item_has_formula_region(item) and item_rect(item) is not None for item in translated_items)


def page_should_skip_bbox_text_strip(translated_items: list[dict]) -> bool:
    return page_has_formula_region(translated_items)


def formula_neighbor_text_item_ids(translated_items: list[dict]) -> set[str]:
    return set()


def build_render_page_policy(translated_items: list[dict]) -> RenderPagePolicy:
    if layout.use_typst_fill_cleanup():
        return _build_typst_fill_page_policy(translated_items)
    has_formula = page_has_formula_region(translated_items)
    if layout.use_default_text_overlay_cover_fill():
        return _build_default_cover_fill_page_policy(translated_items, cleanup_mode="delete_text")
    return RenderPagePolicy(
        page_has_formula_region=has_formula,
        item_policies={},
    )


def _build_default_cover_fill_page_policy(
    translated_items: list[dict],
    *,
    cleanup_mode: CleanupMode,
) -> RenderPagePolicy:
    policies: dict[str, RenderItemPolicy] = {}
    for item in translated_items:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id or block_kind(item) != "text":
            continue
        if not item_will_render_translated_overlay(item):
            continue
        policies[item_id] = RenderItemPolicy(
            item_id=item_id,
            cleanup_mode=cleanup_mode,
            overlay_fill="sampled",
            reason="default_text_overlay_cover_fill",
        )
    return RenderPagePolicy(
        page_has_formula_region=page_has_formula_region(translated_items),
        item_policies=policies,
    )


def _build_typst_fill_page_policy(translated_items: list[dict]) -> RenderPagePolicy:
    policies: dict[str, RenderItemPolicy] = {}
    for item in translated_items:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id or block_kind(item) != "text":
            continue
        if not item_will_render_translated_overlay(item):
            continue
        policies[item_id] = RenderItemPolicy(
            item_id=item_id,
            cleanup_mode="visual_cover",
            overlay_fill="sampled",
            reason="typst_fill_default",
        )
    return RenderPagePolicy(
        page_has_formula_region=page_has_formula_region(translated_items),
        item_policies=policies,
    )


def apply_render_page_policy_fields(translated_items: list[dict]) -> list[dict]:
    policy = build_render_page_policy(translated_items)
    if not policy.item_policies:
        return translated_items
    patched: list[dict] = []
    for item in translated_items:
        item_id = str(item.get("item_id") or "").strip()
        item_policy = policy.item_policies.get(item_id)
        if item_policy is None:
            patched.append(item)
            continue
        patched.append(apply_render_item_policy_fields(item, item_policy))
    return patched


def apply_render_pages_policy_fields(translated_pages: dict[int, list[dict]]) -> dict[int, list[dict]]:
    return {
        page_idx: apply_render_page_policy_fields(items)
        for page_idx, items in translated_pages.items()
    }


def apply_typst_cover_fallback_fields(
    translated_pages: dict[int, list[dict]],
    page_indices: frozenset[int],
    item_ids: frozenset[str] = frozenset(),
) -> dict[int, list[dict]]:
    if not page_indices and not item_ids:
        return translated_pages
    patched_pages: dict[int, list[dict]] = {}
    for page_idx, items in translated_pages.items():
        page_needs_cover = page_idx in page_indices
        if not page_needs_cover and not item_ids:
            patched_pages[page_idx] = items
            continue
        patched_items: list[dict] = []
        for item in items:
            item_id = str(item.get("item_id") or "")
            item_needs_cover = item_id in item_ids
            if block_kind(item) == "text" and (page_needs_cover or item_needs_cover):
                patched_items.append(
                    apply_render_item_policy_fields(
                        item,
                        RenderItemPolicy(
                            item_id=item_id,
                            overlay_fill="sampled",
                            reason="typst_cover_fallback" if page_needs_cover else "typst_item_cover_fallback",
                        ),
                    )
                )
            else:
                patched_items.append(item)
        patched_pages[page_idx] = patched_items
    return patched_pages


def apply_render_item_policy_fields(item: dict, item_policy: RenderItemPolicy) -> dict:
    patched_item = dict(item)
    patched_item["_render_policy"] = item_policy.to_payload()
    return patched_item


def item_has_render_source_or_output_text(item: dict) -> bool:
    return bool(item_render_output_text(item) or item_render_source_text(item))


def item_will_render_translated_overlay(item: dict) -> bool:
    if item_is_marked_non_translated(item):
        return False
    return bool(item_render_output_text(item))


def item_is_marked_non_translated(item: dict) -> bool:
    status = str(item.get("final_status") or item.get("translation_status") or item.get("status") or "").strip().lower()
    if status in NON_TRANSLATED_FINAL_STATUSES:
        return True
    decision = str(item.get("decision") or item.get("translation_decision") or "").strip().lower()
    if decision in NON_TRANSLATED_DECISIONS:
        return True
    tags = item.get("tags")
    if isinstance(tags, list):
        normalized_tags = {str(tag).strip().lower() for tag in tags}
        if normalized_tags & SKIP_TRANSLATION_TAGS:
            return True
    return False


def item_render_output_text(item: dict) -> str:
    if "render_protected_text" in item:
        return str(item.get("render_protected_text") or "").strip()
    if (item.get("continuation_group") or item.get("continuation_group_id")) and (
        item.get("protected_translated_text") or item.get("translated_text")
    ):
        return str(item.get("protected_translated_text") or item.get("translated_text") or "").strip()
    return str(
        item.get("render_translation_overlay_text")
        or item.get("translation_overlay_text")
        or item.get("translation_unit_protected_translated_text")
        or item.get("group_protected_translated_text")
        or item.get("protected_translated_text")
        or item.get("translation_unit_translated_text")
        or item.get("group_translated_text")
        or item.get("translated_text")
        or ""
    ).strip()


def item_render_source_text(item: dict) -> str:
    return str(
        item.get("translation_unit_protected_source_text")
        or item.get("protected_source_text")
        or item.get("source_text")
        or ""
    ).strip()


def item_should_bbox_text_strip(item: dict, *, skip_item_ids: set[str] | None = None) -> bool:
    if skip_item_ids and str(item.get("item_id") or "").strip() in skip_item_ids:
        return False
    return block_kind(item) == "text" and item_will_render_translated_overlay(item)


def _page_formula_rects(items: list[dict]) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for item in items:
        if not item_has_formula_region(item):
            continue
        rect = item_rect(item)
        if rect is not None:
            rects.append(rect)
    return rects
