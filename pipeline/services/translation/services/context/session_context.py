from __future__ import annotations

import os

from services.translation.llm.shared.control_context import RetrievalEvidence
from services.translation.llm.shared.control_context import TranslationControlContext
from services.translation.llm.shared.control_context import build_translation_control_context
from services.translation.llm.shared.control_context import resolve_engine_profile
from services.translation.services.policy import TranslationPolicyConfig
from services.translation.services.terms import AbbreviationEntry
from services.translation.services.terms import GlossaryEntry
from services.translation.services.terms import normalize_glossary_entries


# 目标语言默认值可通过环境变量覆盖:
#   RETAIN_TARGET_LANG        如 "zh-CN" / "en" / "ja"
#   RETAIN_TARGET_LANGUAGE_NAME 如 "简体中文" / "English" / "日本語"(注入 prompt 的展示名)
# 留空则回退到 zh-CN / 简体中文。
def _default_target_lang() -> str:
    return os.environ.get("RETAIN_TARGET_LANG", "").strip() or "zh-CN"


def _default_target_language_name() -> str:
    return os.environ.get("RETAIN_TARGET_LANGUAGE_NAME", "").strip() or "简体中文"


def build_translation_context(
    *,
    mode: str = "fast",
    source_lang: str = "auto",
    target_lang: str | None = None,
    target_language_name: str | None = None,
    domain_guidance: str = "",
    rule_guidance: str = "",
    extra_guidance: str = "",
    request_label: str = "",
    glossary_entries: list[GlossaryEntry] | None = None,
    abbreviation_entries: list[AbbreviationEntry] | None = None,
    retrieval_entries: list[RetrievalEvidence] | None = None,
    model: str = "",
    base_url: str = "",
    context_mode: str = "needed",
    glossary_mode: str = "matched",
    glossary_hard: bool = False,
    memory_mode: str = "matched",
) -> TranslationControlContext:
    if target_lang is None:
        target_lang = _default_target_lang()
    if target_language_name is None:
        target_language_name = _default_target_language_name()
    return build_translation_control_context(
        mode=mode,
        source_lang=source_lang,
        target_lang=target_lang,
        target_language_name=target_language_name,
        domain_guidance=domain_guidance,
        rule_guidance=rule_guidance,
        extra_guidance=extra_guidance,
        request_label=request_label,
        glossary_entries=glossary_entries,
        abbreviation_entries=abbreviation_entries,
        retrieval_entries=retrieval_entries,
        context_mode=context_mode,
        glossary_mode=glossary_mode,
        glossary_hard=glossary_hard,
        memory_mode=memory_mode,
        engine_profile=resolve_engine_profile(model=model, base_url=base_url),
    )


def build_translation_context_from_policy(
    policy_config: TranslationPolicyConfig,
    *,
    request_label: str = "",
    extra_guidance: str = "",
    glossary_entries: list[GlossaryEntry] | None = None,
    abbreviation_entries: list[AbbreviationEntry] | None = None,
    retrieval_entries: list[RetrievalEvidence] | None = None,
    model: str = "",
    base_url: str = "",
    context_mode: str = "needed",
    glossary_mode: str = "matched",
    glossary_hard: bool = False,
    memory_mode: str = "matched",
) -> TranslationControlContext:
    extra_guidance_parts: list[str] = []
    if extra_guidance.strip():
        extra_guidance_parts.append(extra_guidance.strip())
    if str(getattr(policy_config, "math_mode", "placeholder") or "placeholder").strip() == "direct_typst":
        extra_guidance_parts.append(
            "Math output mode: direct_typst.\n"
            "When the source contains formulas, output the final translated text directly with inline math preserved "
            "using `$...$` spans when needed.\n"
            "Do not emit placeholder tokens, JSON shells, labels, or explanations."
        )
    return build_translation_context(
        mode=policy_config.mode,
        domain_guidance=policy_config.document_domain_guidance,
        rule_guidance=policy_config.rule_guidance,
        extra_guidance="\n\n".join(extra_guidance_parts).strip(),
        request_label=request_label,
        glossary_entries=normalize_glossary_entries(glossary_entries),
        abbreviation_entries=abbreviation_entries,
        retrieval_entries=retrieval_entries,
        model=model,
        base_url=base_url,
        context_mode=context_mode,
        glossary_mode=glossary_mode,
        glossary_hard=glossary_hard,
        memory_mode=memory_mode,
    )


__all__ = [
    "build_translation_context",
    "build_translation_context_from_policy",
    "RetrievalEvidence",
    "TranslationControlContext",
]
