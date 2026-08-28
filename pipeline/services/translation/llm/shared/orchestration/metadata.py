from __future__ import annotations

from services.translation.llm.result_payload import is_internal_placeholder_degraded
from services.translation.llm.result_payload import result_entry
from services.translation.llm.shared.orchestration.common import formula_placeholder_count
from services.translation.core.payload.formula_protection import restore_tokens_by_type
from services.translation.llm.validation.placeholder_tokens import strip_placeholders
from services.translation.llm.shared.orchestration.segment_routing import build_formula_segment_plan
from services.translation.llm.shared.orchestration.segment_routing import effective_formula_segment_count
from services.translation.llm.shared.orchestration.segment_routing import formula_segment_translation_route
from services.translation.llm.shared.orchestration.segment_routing import formula_segment_window_count
from services.translation.llm.shared.orchestration.segment_routing import small_formula_risk_score


def formula_density(source_text: str, placeholder_count: int) -> float:
    if not source_text or placeholder_count <= 0:
        return 0.0
    return round(placeholder_count / max(1, len(source_text)), 4)


def formula_route_diagnostics(
    item: dict,
    *,
    context=None,
) -> dict[str, object]:
    source_text = str(item.get("translation_unit_protected_source_text") or item.get("protected_source_text") or "")
    placeholder_count = formula_placeholder_count(source_text)
    diagnostics: dict[str, object] = {}
    group_split_reason = str(item.get("group_split_reason", "") or "").strip()
    if group_split_reason:
        diagnostics["group_split_reason"] = group_split_reason
    if placeholder_count <= 0:
        return diagnostics
    policy = context.segmentation_policy if context is not None else None
    _, segments = build_formula_segment_plan(source_text)
    diagnostics.update(
        {
            "formula_placeholder_count": placeholder_count,
            "formula_segment_count": len(segments),
            "effective_formula_segment_count": effective_formula_segment_count(segments),
            "formula_window_count": formula_segment_window_count(item, policy=policy),
            "formula_density": formula_density(source_text, placeholder_count),
            "formula_route_decision": formula_segment_translation_route(item, policy=policy),
            "formula_risk_score": small_formula_risk_score(
                source_text,
                segments=segments,
                policy=policy,
            ),
        }
    )
    if item.get("_heavy_formula_split_applied"):
        diagnostics["heavy_block_split_applied"] = True
    return diagnostics


def term_scope_diagnostics(
    item: dict,
    *,
    context=None,
) -> dict[str, object]:
    if context is None or not hasattr(context, "term_scope_summary_for_item"):
        return {}
    summary = context.term_scope_summary_for_item(item)
    if not summary:
        return {}
    if not int(summary.get("glossary_total_count", 0) or 0) and not int(summary.get("abbreviation_total_count", 0) or 0):
        return {}
    return {"term_scope": summary}


def restore_runtime_term_tokens(
    result: dict[str, dict[str, str]],
    *,
    item: dict,
) -> dict[str, dict[str, str]]:
    protected_map = list(item.get("translation_unit_protected_map") or item.get("protected_map") or [])
    if not protected_map:
        return result
    # glossary_hard 开关下,整段文本就是一个术语时,硬替换把源文本变成单个
    # term 占位符(如 "<t1-d37/>"),无可译内容。LLM 常据此返回
    # decision="keep_origin" + 空 translated_text——此时占位符无处还原,
    # 最终被 apply 判成 keep_origin、译文清空(术语反而漏译)。
    # 纯占位符源(去掉所有占位符后无正文)且含 term 条目时:无论 LLM 返回
    # keep_origin 还是空,译文直接取占位符还原后的目标文本(术语 target)。
    term_entries = [e for e in protected_map if str(e.get("token_type", "") or "") == "term"]
    source_text = str(item.get("translation_unit_protected_source_text") or item.get("protected_source_text") or "")
    pure_term_placeholder_source = bool(term_entries) and not strip_placeholders(source_text).strip()
    restored: dict[str, dict[str, str]] = {}
    for item_id, payload in result.items():
        next_payload = dict(payload)
        translated_text = restore_tokens_by_type(
            str(payload.get("translated_text", "") or ""),
            protected_map,
            {"term"},
        )
        decision = str(payload.get("decision", "translate") or "translate")
        if pure_term_placeholder_source and (not translated_text.strip() or decision == "keep_origin"):
            translated_text = restore_tokens_by_type(source_text, protected_map, {"term"})
            decision = "translate"
            if not str(next_payload.get("final_status", "") or "").strip():
                next_payload["final_status"] = "translated"
        next_payload["translated_text"] = translated_text
        next_payload["decision"] = decision
        restored[item_id] = next_payload
    return restored


def should_store_translation_result(payload: dict[str, str]) -> bool:
    if not payload:
        return False
    if is_internal_placeholder_degraded(payload):
        return False
    final_status = str(payload.get("final_status", "") or "").strip().lower()
    if final_status and final_status != "translated":
        return False
    diagnostics = payload.get("translation_diagnostics")
    if isinstance(diagnostics, dict):
        fallback_to = str(diagnostics.get("fallback_to", "") or "").strip().lower()
        if fallback_to == "sentence_level":
            return False
    return True


def attach_result_metadata(
    result: dict[str, dict[str, str]],
    *,
    item: dict,
    context=None,
    route_path: list[str],
    output_mode_path: list[str] | None = None,
    error_taxonomy: str = "",
    fallback_to: str = "",
    degradation_reason: str = "",
) -> dict[str, dict[str, str]]:
    enriched: dict[str, dict[str, str]] = {}
    for item_id, payload in result.items():
        next_payload = dict(payload)
        diagnostics = dict(next_payload.get("translation_diagnostics") or {})
        diagnostics.setdefault("item_id", item.get("item_id", item_id))
        diagnostics.setdefault("page_idx", item.get("page_idx"))
        diagnostics["route_path"] = route_path
        diagnostics["output_mode_path"] = output_mode_path or []
        diagnostics["fallback_to"] = fallback_to
        diagnostics["degradation_reason"] = degradation_reason
        diagnostics["final_status"] = next_payload.get("final_status", "translated")
        diagnostics.update(formula_route_diagnostics(item, context=context))
        diagnostics.update(term_scope_diagnostics(item, context=context))
        if error_taxonomy:
            diagnostics["error_trace"] = [{"type": error_taxonomy}]
        next_payload["translation_diagnostics"] = diagnostics
        enriched[item_id] = next_payload
    return enriched


def keep_origin_result_with_metadata(
    *,
    item: dict,
    degradation_reason: str,
    error_taxonomy: str,
    route_path: list[str],
    error_trace: list[dict[str, object]],
    final_status: str,
    fallback_to: str,
    context=None,
    dead_letter: bool | None = None,
) -> dict[str, dict[str, str]]:
    payload = result_entry("keep_origin", "")
    payload["final_status"] = final_status
    payload["error_taxonomy"] = error_taxonomy
    diagnostics = {
        "item_id": item.get("item_id", ""),
        "page_idx": item.get("page_idx"),
        "route_path": route_path,
        "error_trace": error_trace,
        "fallback_to": fallback_to,
        "degradation_reason": degradation_reason,
        "final_status": final_status,
        **formula_route_diagnostics(item, context=context),
        **term_scope_diagnostics(item, context=context),
    }
    if dead_letter is not None:
        diagnostics["dead_letter"] = bool(dead_letter)
    payload["translation_diagnostics"] = diagnostics
    return {str(item.get("item_id", "") or ""): payload}
