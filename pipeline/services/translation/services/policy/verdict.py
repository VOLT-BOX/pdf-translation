from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from services.translation.core.item_reader import is_forced_table_item
from services.translation.core.item_reader import item_is_caption_like
from services.translation.core.item_reader import item_policy_translate
from services.translation.core.item_reader import item_raw_block_type
from services.translation.llm.validation.placeholder_tokens import strip_placeholders
from services.translation.services.policy.metadata_filter import looks_like_hard_nontranslatable_metadata
from services.translation.services.policy.special_blocks import looks_like_special_long_list_block


BYTE_TOKEN_RE = re.compile(r"^[0-9A-Fa-f]{2}$")
KEEP_ORIGIN_ACTION = "keep_origin"
TRANSLATE_ACTION = "translate"
SKIP_MODEL_LABELS = {
    "code",
    "keep_origin",
    "no_trans",
    "skip_model_keep_origin",
}
NON_TRANSLATABLE_RAW_TYPES = {
    "display_formula",
    "formula",
    "image",
    "table",
    "chart",
}


@dataclass(frozen=True)
class TranslationPolicyView:
    item: dict
    source: str
    compact: str
    policy_translate: bool | None
    labels: frozenset[str]
    raw_block_type: str
    layout_zone: str


@dataclass(frozen=True)
class TranslationPolicyVerdict:
    action: str
    reason: str = ""
    should_call_model: bool = True
    allow_keep_origin: bool = False
    blocks_export: bool = True
    fast_path_keep_origin: bool = False

    @property
    def keeps_origin(self) -> bool:
        return self.action == KEEP_ORIGIN_ACTION


@dataclass(frozen=True)
class _PolicyRule:
    reason: str
    predicate: Callable[[TranslationPolicyView], bool]
    fast_path_keep_origin: bool = True


def source_text_for_policy(item: dict) -> str:
    return str(
        item.get("translation_unit_protected_source_text")
        or item.get("group_protected_source_text")
        or item.get("protected_source_text")
        or item.get("source_text")
        or ""
    ).strip()


def _policy_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _policy_translate(item: dict) -> bool | None:
    explicit = _policy_bool(item.get("should_translate"))
    if explicit is False:
        return False
    policy_value = _policy_bool(item.get("policy_translate"))
    if policy_value is not None:
        return policy_value
    return item_policy_translate(item)


def _labels_for_policy(item: dict) -> frozenset[str]:
    labels = {
        str(item.get("skip_reason", "") or "").strip(),
        str(item.get("classification_label", "") or "").strip(),
    }
    diagnostics = item.get("translation_diagnostics") or {}
    if isinstance(diagnostics, dict):
        labels.update(
            {
                str(diagnostics.get("degradation_reason", "") or "").strip(),
                str(diagnostics.get("fallback_to", "") or "").strip(),
            }
        )
    return frozenset(label for label in labels if label)


def _compact_text(item: dict) -> str:
    return " ".join(strip_placeholders(source_text_for_policy(item)).split())


def _policy_view(item: dict) -> TranslationPolicyView:
    return TranslationPolicyView(
        item=item,
        source=source_text_for_policy(item),
        compact=_compact_text(item),
        policy_translate=_policy_translate(item),
        labels=_labels_for_policy(item),
        raw_block_type=item_raw_block_type(item),
        layout_zone=str(item.get("layout_zone", "") or "").strip().lower(),
    )


def _is_short_alnum_label(view: TranslationPolicyView) -> bool:
    return len(view.compact) <= 4 and view.compact.replace(" ", "").isalnum()


_KEEP_ORIGIN_RULES: tuple[_PolicyRule, ...] = (
    _PolicyRule("empty_source_text", lambda view: not view.source.strip()),
    _PolicyRule("placeholder_only", lambda view: not view.compact),
    _PolicyRule("policy_skip", lambda view: view.policy_translate is False),
    _PolicyRule("skip_model_keep_origin", lambda view: bool(view.labels & SKIP_MODEL_LABELS)),
    _PolicyRule("non_textual_raw_block", lambda view: view.raw_block_type in NON_TRANSLATABLE_RAW_TYPES),
    _PolicyRule("hard_metadata_fragment", lambda view: looks_like_hard_nontranslatable_metadata(view.item)),
    _PolicyRule("special_long_list_block", lambda view: looks_like_special_long_list_block(view.source)),
    _PolicyRule("protocol_hex_dump", lambda view: looks_like_protocol_or_hex_dump(view.source)),
    _PolicyRule(
        "short_non_body_label",
        lambda view: _is_short_alnum_label(view) and item_is_caption_like(view.item),
    ),
    _PolicyRule(
        "short_non_body_label",
        lambda view: _is_short_alnum_label(view) and view.policy_translate is not True and view.layout_zone == "non_flow",
    ),
)


def translation_policy_verdict(item: dict) -> TranslationPolicyVerdict:
    view = _policy_view(item)
    # 显式策略优先:OCR 归一化阶段标记 policy_translate=True 的块(如 header/metadata/body)
    # 即使被某处写入了 skip_model_keep_origin 等 label 或把 should_translate 改成 False,
    # 也强制翻译 —— 否则 label/should_translate 与 verdict 会形成自我强化的循环,
    # 显式策略永远无法生效。注意:这里直接读 item 原始 policy_translate 字段,
    # 而非 view.policy_translate(后者会被 should_translate=False 污染成 False)。
    if _policy_bool(item.get("policy_translate")) is True or item_policy_translate(item) is True:
        return TranslationPolicyVerdict(action=TRANSLATE_ACTION)
    # enable_table_translation 强制翻译的表格:直接放行。表格只在开关开启时才会进
    # payload,必须翻译。否则 policy_skip(policy.translate=False,provider_non_text:table)
    # 或 non_textual_raw_block(raw_block_type=table)规则会命中 keep_origin,
    # 进而被 should_fast_path_keep_origin 短路,根本不调用模型。
    if is_forced_table_item(item):
        return TranslationPolicyVerdict(action=TRANSLATE_ACTION)
    matched_rule = next((rule for rule in _KEEP_ORIGIN_RULES if rule.predicate(view)), None)
    if matched_rule is None:
        return TranslationPolicyVerdict(action=TRANSLATE_ACTION)
    return TranslationPolicyVerdict(
        action=KEEP_ORIGIN_ACTION,
        reason=matched_rule.reason,
        should_call_model=False,
        allow_keep_origin=True,
        blocks_export=False,
        fast_path_keep_origin=matched_rule.fast_path_keep_origin,
    )


def should_skip_model_by_policy(item: dict) -> bool:
    return not translation_policy_verdict(item).should_call_model


def should_fast_path_keep_origin(item: dict) -> tuple[bool, str]:
    verdict = translation_policy_verdict(item)
    if verdict.keeps_origin and verdict.fast_path_keep_origin:
        return True, verdict.reason
    return False, ""


def is_keep_origin_allowed_by_policy(item: dict) -> bool:
    return translation_policy_verdict(item).allow_keep_origin


def looks_like_protocol_or_hex_dump(text: str) -> bool:
    normalized = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if not normalized:
        return False
    tokens = normalized.split()
    if len(tokens) < 24:
        return False
    byte_count = sum(1 for token in tokens if BYTE_TOKEN_RE.fullmatch(token))
    byte_ratio = byte_count / max(len(tokens), 1)
    if byte_count >= 32 and byte_ratio >= 0.55:
        return True
    if byte_count >= 128 and byte_ratio >= 0.35:
        return True
    return False


__all__ = [
    "KEEP_ORIGIN_ACTION",
    "TRANSLATE_ACTION",
    "TranslationPolicyVerdict",
    "TranslationPolicyView",
    "is_keep_origin_allowed_by_policy",
    "looks_like_protocol_or_hex_dump",
    "should_fast_path_keep_origin",
    "should_skip_model_by_policy",
    "source_text_for_policy",
    "translation_policy_verdict",
]
