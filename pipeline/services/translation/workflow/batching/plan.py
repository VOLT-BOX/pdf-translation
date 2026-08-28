from __future__ import annotations

from services.translation.workflow.batching.batching import _build_translation_batches as _build_translation_batches_impl
from services.translation.workflow.batching.batching import _classify_translation_batches
from services.translation.workflow.batching.batching import _effective_translation_batch_size
from services.translation.workflow.batching.batching import _save_flush_interval
from services.translation.workflow.batching.batching import chunked
from services.translation.workflow.batching.dedupe import _dedupe_pending_items
from services.translation.workflow.batching.dedupe import _dedupe_signature
from services.translation.workflow.batching.dedupe import _source_text
from services.translation.services.fast_path.keep_origin import _fast_path_keep_origin_result
from services.translation.services.fast_path.keep_origin import _is_fast_path_keep_origin_item
from services.translation.services.fast_path.keep_origin import _normalized_text_without_placeholders
from services.translation.services.fast_path.keep_origin import _plan_item_view
from services.translation.workflow.scheduling.allocation import _adaptive_floor_limit
from services.translation.workflow.scheduling.allocation import _adaptive_initial_limit
from services.translation.workflow.scheduling.allocation import _allocate_translation_queue_workers
from services.translation.workflow.scheduling.allocation import _provider_adaptive_initial_limit
from services.translation.workflow.scheduling.allocation import _slow_worker_cap
from services.translation.workflow.scheduling.stats import TranslationBatchRunStats


def _build_translation_batches(
    pending: list[dict],
    *,
    effective_batch_size: int,
    translation_context,
) -> tuple[list[list[dict]], list[dict[str, dict[str, str]]]]:
    return _build_translation_batches_impl(
        pending,
        effective_batch_size=effective_batch_size,
        translation_context=translation_context,
        is_fast_path_keep_origin_item_fn=_is_fast_path_keep_origin_item,
        fast_path_keep_origin_result_fn=_fast_path_keep_origin_result,
        plan_item_view_fn=_plan_item_view,
    )


def effective_translation_batch_size(
    *,
    batch_size: int,
    model: str,
    base_url: str,
    translation_context,
) -> int:
    return _effective_translation_batch_size(
        batch_size=batch_size,
        model=model,
        base_url=base_url,
        translation_context=translation_context,
    )


__all__ = [
    "chunked",
    "TranslationBatchRunStats",
    "_adaptive_floor_limit",
    "_adaptive_initial_limit",
    "_provider_adaptive_initial_limit",
    "_allocate_translation_queue_workers",
    "_build_translation_batches",
    "_classify_translation_batches",
    "_dedupe_pending_items",
    "_dedupe_signature",
    "effective_translation_batch_size",
    "_effective_translation_batch_size",
    "_fast_path_keep_origin_result",
    "_is_fast_path_keep_origin_item",
    "_normalized_text_without_placeholders",
    "_plan_item_view",
    "_save_flush_interval",
    "_slow_worker_cap",
    "_source_text",
]
