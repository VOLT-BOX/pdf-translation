from __future__ import annotations

import os

from services.translation.artifacts import TranslationDiagnosticsCollector
from services.translation.llm.placeholder_transform import item_with_runtime_hard_glossary
from services.translation.llm.shared.cache import split_cached_batch
from services.translation.llm.shared.cache import store_cached_batch
from services.translation.llm.shared.orchestration.batched_plain_cache import split_and_validate_cached_batch
from services.translation.llm.shared.orchestration.batched_plain_cache import store_cacheable_batch_result
from services.translation.llm.shared.orchestration.batched_plain_request import attach_batched_plain_metadata
from services.translation.llm.shared.orchestration.batched_plain_request import emit_batch_transport_single_retry
from services.translation.llm.shared.orchestration.batched_plain_request import should_use_direct_deepseek_batch
from services.translation.llm.shared.orchestration.batched_plain_request import split_batched_plain_result_for_partial_retry
from services.translation.llm.shared.orchestration.batched_plain_single import enqueue_deferred_transport_items
from services.translation.llm.shared.orchestration.batched_plain_single import enqueue_deferred_tail_items
from services.translation.llm.shared.orchestration.batched_plain_single import retry_deferred_transport_items
from services.translation.llm.shared.orchestration.batched_plain_single import translate_uncached_items_single
from services.translation.llm.shared.orchestration.metadata import should_store_translation_result
from services.translation.llm.shared.provider_runtime import is_transport_error
from services.translation.llm.shared.provider_runtime import translate_batch_once


def _try_direct_batched_plain(
    uncached_batch: list[dict],
    *,
    api_key: str,
    model: str,
    base_url: str,
    request_label: str,
    context,
    diagnostics: TranslationDiagnosticsCollector | None,
    store_cached_batch_fn,
    translate_batch_once_fn,
) -> tuple[dict[str, dict[str, str]], list[dict], bool, bool]:
    if not should_use_direct_deepseek_batch(uncached_batch, model=model, base_url=base_url, context=context):
        return {}, uncached_batch, False, False
    try:
        if request_label:
            print(f"{request_label}: batched plain path items={len(uncached_batch)}", flush=True)
        result = translate_batch_once_fn(
            uncached_batch,
            api_key=api_key,
            model=model,
            base_url=base_url,
            request_label=request_label,
            domain_guidance=context.merged_guidance,
            mode=context.mode,
            target_language_name=context.target_language_name,
            diagnostics=diagnostics,
            timeout_s=context.timeout_policy.batch_plain_text_seconds,
            http_retry_attempts=max(1, int(context.fallback_policy.main_http_retry_attempts)),
        )
        result = attach_batched_plain_metadata(uncached_batch, result, context=context)
        store_cacheable_batch_result(
            uncached_batch,
            result,
            model=model,
            base_url=base_url,
            context=context,
            store_cached_batch_fn=store_cached_batch_fn,
            should_store_translation_result_fn=should_store_translation_result,
        )
        return result, [], True, True
    except Exception as exc:
        if is_transport_error(exc):
            emit_batch_transport_single_retry(uncached_batch, diagnostics=diagnostics, exc=exc)
            if request_label:
                print(f"{request_label}: batched plain transport failure, fallback to single-item path: {type(exc).__name__}: {exc}", flush=True)
        if getattr(exc, "item_id", None) and isinstance(getattr(exc, "result", None), dict):
            partial_result = getattr(exc, "result", {}) or {}
            accepted_result, retry_batch = split_batched_plain_result_for_partial_retry(
                uncached_batch,
                partial_result,
                context=context,
                diagnostics=diagnostics,
            )
            if request_label:
                print(f"{request_label}: batched plain partial fallback, keep={len(accepted_result)} retry_items={len(retry_batch)}", flush=True)
            store_cacheable_batch_result(
                [item for item in uncached_batch if item["item_id"] in accepted_result],
                accepted_result,
                model=model,
                base_url=base_url,
                context=context,
                store_cached_batch_fn=store_cached_batch_fn,
                should_store_translation_result_fn=should_store_translation_result,
            )
            return accepted_result, retry_batch, not retry_batch, True
        if request_label:
            print(f"{request_label}: batched plain fallback to single-item path: {type(exc).__name__}: {exc}", flush=True)
        return {}, uncached_batch, False, True


def translate_items_plain_text(
    batch: list[dict],
    *,
    api_key: str,
    model: str,
    base_url: str,
    request_label: str,
    context,
    diagnostics: TranslationDiagnosticsCollector | None,
    single_item_translator,
    split_cached_batch_fn=split_cached_batch,
    store_cached_batch_fn=store_cached_batch,
    translate_batch_once_fn=translate_batch_once,
) -> dict[str, dict[str, str]]:
    # 给每个 item 设逐条匹配的术语指引(对标 single_item_flow 的 item["_scoped_terms_guidance"])。
    # 批处理主路径此前不设此字段,direct_typst_batch_user_prompt 即使读也读不到,
    # 导致批处理翻译时 user 消息不带术语约束。用 scope 前的全量 entries 按每个 item
    # 自身文本算 guidance(scoped_to_batch 之后 entries 已缩到批级命中集合,会漏掉
    # 只在单条出现的术语)。
    from services.translation.core.terms.injection import build_terms_guidance
    from services.translation.core.terms.glossary import matched_glossary_entries
    from services.translation.core.terms.glossary import matched_glossary_spans
    from services.translation.llm.validation.english_residue import unit_source_text
    for item in batch:
        source_text = str(item.get("translation_unit_protected_source_text") or item.get("protected_source_text") or item.get("source_text") or "")
        matched = matched_glossary_entries(context.glossary_entries, source_text)
        if matched:
            item["_scoped_terms_guidance"] = build_terms_guidance(glossary_entries=matched)
    context = context.scoped_to_batch(batch)
    if context.glossary_hard:
        # 开关开启:所有命中术语(含 preferred)走硬替换,位置直接复用 hyperscan
        # 已算出的 (entry,start,end),不走 _collect_term_spans 的逐条 finditer。
        # spans 必须对 unit_source_text(item) 算,与 protect_glossary_terms 操作的文本一致。
        batch = [
            item_with_runtime_hard_glossary(
                item,
                context.glossary_entries,
                precomputed_spans=matched_glossary_spans(context.glossary_entries, unit_source_text(item)),
            )
            for item in batch
        ]
    else:
        batch = [item_with_runtime_hard_glossary(item, context.glossary_entries) for item in batch]
    merged, uncached_batch = split_and_validate_cached_batch(
        batch,
        model=model,
        base_url=base_url,
        context=context,
        diagnostics=diagnostics,
        request_label=request_label,
        split_cached_batch_fn=split_cached_batch_fn,
    )
    if not uncached_batch:
        return merged

    batched_result, uncached_batch, complete, direct_batch_attempted = _try_direct_batched_plain(
        uncached_batch,
        api_key=api_key,
        model=model,
        base_url=base_url,
        request_label=request_label,
        context=context,
        diagnostics=diagnostics,
        store_cached_batch_fn=store_cached_batch_fn,
        translate_batch_once_fn=translate_batch_once_fn,
    )
    merged.update(batched_result)
    if complete or not uncached_batch:
        return merged

    if direct_batch_attempted and _defer_batched_plain_fallback_to_tail():
        queued = enqueue_deferred_tail_items(
            uncached_batch,
            api_key=api_key,
            model=model,
            base_url=base_url,
            request_label=request_label,
            context=context,
            diagnostics=diagnostics,
            single_item_translator=single_item_translator,
            store_cached_batch_fn=store_cached_batch_fn,
            reason="batched_plain_fallback",
        )
        if queued:
            return merged

    single_result, deferred_transport_items = translate_uncached_items_single(
        uncached_batch,
        api_key=api_key,
        model=model,
        base_url=base_url,
        request_label=request_label,
        context=context,
        diagnostics=diagnostics,
        single_item_translator=single_item_translator,
        store_cached_batch_fn=store_cached_batch_fn,
    )
    merged.update(single_result)
    if not enqueue_deferred_transport_items(
        deferred_transport_items,
        api_key=api_key,
        model=model,
        base_url=base_url,
        request_label=request_label,
        context=context,
        diagnostics=diagnostics,
        single_item_translator=single_item_translator,
        store_cached_batch_fn=store_cached_batch_fn,
    ):
        merged.update(
            retry_deferred_transport_items(
                deferred_transport_items,
                api_key=api_key,
                model=model,
                base_url=base_url,
                request_label=request_label,
                context=context,
                diagnostics=diagnostics,
                single_item_translator=single_item_translator,
                store_cached_batch_fn=store_cached_batch_fn,
            )
        )
    return merged


def _defer_batched_plain_fallback_to_tail() -> bool:
    value = str(os.environ.get("RETAIN_TRANSLATION_INLINE_BATCH_FALLBACK", "") or "").strip().lower()
    return value not in {"1", "true", "yes", "on"}
