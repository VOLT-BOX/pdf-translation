from __future__ import annotations

from services.rendering.policy.cleanup_policy import item_is_marked_non_translated
from services.rendering.policy.cleanup_policy import item_render_output_text
from services.rendering.policy.cleanup_policy import item_render_source_text
from services.rendering.source_cleanup.intents import SourceCleanupEvidence
from services.rendering.source_cleanup.planning.mixed_content import item_has_unresolved_embedded_formula
from services.rendering.source_cleanup.planning.item_classifier import item_allows_forced_text_strip
from services.rendering.source_cleanup.policy.adapter import has_formula_region


def build_source_cleanup_evidence(item: dict) -> SourceCleanupEvidence:
    return SourceCleanupEvidence(
        item=item,
        item_id=str(item.get("item_id") or "").strip(),
        block_kind=item_block_kind(item),
        has_formula_region=has_formula_region(item),
        source_text=item_render_source_text(item),
        output_text=item_render_output_text(item),
        is_marked_non_translated=item_is_marked_non_translated(item),
        has_unresolved_embedded_formula=item_has_unresolved_embedded_formula(item),
        is_force_strip_text=item_allows_forced_text_strip(item),
    )


def item_block_kind(item: dict) -> str:
    return str(item.get("block_kind") or item.get("block_type") or "").strip().lower()
