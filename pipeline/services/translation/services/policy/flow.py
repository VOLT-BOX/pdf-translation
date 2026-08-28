from __future__ import annotations

from typing import Callable

from services.translation.core.item_reader import item_block_kind
from services.translation.core.item_reader import item_raw_block_type
from services.translation.core.payload.parts.policy_state import mark_translation_required
from services.translation.services.context import TranslationDocumentContext
from services.translation.services.policy.config import TranslationPolicyConfig
from services.translation.services.policy.config import build_translation_policy_config
from services.translation.services.policy.hints import apply_policy_hints
from services.translation.services.policy.payload_rules import apply_classification_labels
from services.translation.services.policy.payload_rules import apply_reference_tail_skip
from services.translation.services.policy.payload_rules import apply_title_skip
from services.translation.services.policy.payload_rules import reset_policy_state
from services.translation.services.policy.planner import TranslationPlanner
from services.translation.services.policy.structured_technical_blocks import collect_structured_technical_hints
from services.translation.services.policy.title_rules import apply_title_translation_rule


def _build_skip_summary(
    *,
    title_skipped: int,
    reference_tail_skipped: int,
) -> dict[str, int]:
    return {
        "title_skipped": title_skipped,
        "reference_tail_skipped": reference_tail_skipped,
        "tail_skipped": reference_tail_skipped,
        "ref_text_skipped": 0,
        "reference_zone_skipped": 0,
        # Deprecated compatibility field. Narrow-body skip logic is disabled.
        "narrow_body_skipped": 0,
        "metadata_fragment_skipped": 0,
        "shared_literal_code_skipped": 0,
        "shared_literal_code_region_skipped": 0,
        "shared_literal_image_region_skipped": 0,
        "shared_literal_translate_forced": 0,
        "mixed_keep_all": 0,
        "mixed_translate_all": 0,
        "mixed_translate_tail": 0,
        "structured_technical_blocks": 0,
        "title_translation_candidates": 0,
    }


def _force_table_translation(payload: list[dict]) -> int:
    """enable_table_translation 时:把表格项从"跳过"强制翻回"翻译"。

    表格跳过硬编码在两处(记录构建 default_translation_flags 与 policy 重置
    foundational_skip_defaults),但本函数是所有 policy 的最终闸门——在这里统一
    覆盖即可同时抵消两处来源。翻译阶段以 should_translate 为准,翻回来即会真译。
    """
    forced = 0
    for item in payload:
        if item_block_kind(item) != "table" and item_raw_block_type(item) not in (
            "table",
            "table_body",
        ):
            continue
        mark_translation_required(item, label="translate_table_body")
        item["final_status"] = ""
        forced += 1
    return forced


def apply_translation_policies(
    *,
    payload: list[dict],
    mode: str,
    classify_batch_size: int,
    workers: int,
    api_key: str,
    model: str,
    base_url: str,
    skip_title_translation: bool,
    page_idx: int,
    sci_cutoff_page_idx: int | None,
    sci_cutoff_block_idx: int | None,
    policy_config: TranslationPolicyConfig | None = None,
    request_chat_content_fn: Callable[..., str] | None = None,
) -> tuple[int, dict[str, int]]:
    if policy_config is None:
        policy_config = build_translation_policy_config(
            mode=mode,
            skip_title_translation=skip_title_translation,
            sci_cutoff_page_idx=sci_cutoff_page_idx,
            sci_cutoff_block_idx=sci_cutoff_block_idx,
        )

    reset_policy_state(payload)
    title_translation_candidates = apply_title_translation_rule(payload)
    structured_technical_blocks = apply_policy_hints(payload, collect_structured_technical_hints(payload))
    classified_items = 0
    skip_summary = _build_skip_summary(
        title_skipped=0,
        reference_tail_skipped=0,
    )

    if policy_config.enable_page_no_trans_classification:
        try:
            planner = TranslationPlanner(
                TranslationDocumentContext(
                    mode=mode,
                    rule_guidance=policy_config.rule_guidance,
                )
            )
            labels = planner.classify_no_trans(
                payload,
                api_key=api_key,
                model=model,
                base_url=base_url,
                batch_size=classify_batch_size,
                request_label=f"classification page {page_idx + 1}",
                request_chat_content_fn=request_chat_content_fn,
            )
        except Exception as exc:
            print(
                f"classification page {page_idx + 1}: skipped after {type(exc).__name__}: {exc}",
                flush=True,
            )
            labels = {}
        classified_items = apply_classification_labels(payload, labels)

    if policy_config.enable_reference_tail_skip:
        title_skipped = apply_title_skip(payload)
        reference_tail_skipped = apply_reference_tail_skip(
            payload,
            page_idx=page_idx,
            cutoff_page_idx=policy_config.sci_cutoff_page_idx,
            cutoff_block_idx=policy_config.sci_cutoff_block_idx,
        )
        skip_summary = _build_skip_summary(
            title_skipped=title_skipped,
            reference_tail_skipped=reference_tail_skipped,
        )
    elif policy_config.enable_title_skip:
        skip_summary = _build_skip_summary(
            title_skipped=apply_title_skip(payload),
            reference_tail_skipped=0,
        )

    if policy_config.enable_table_translation:
        skip_summary["table_translation_forced"] = _force_table_translation(payload)

    skip_summary["structured_technical_blocks"] = structured_technical_blocks
    skip_summary["title_translation_candidates"] = title_translation_candidates
    return classified_items, skip_summary


__all__ = ["apply_translation_policies"]
