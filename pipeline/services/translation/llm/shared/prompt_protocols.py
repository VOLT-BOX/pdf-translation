from __future__ import annotations

import json
import re
from typing import Any

from foundation.shared.prompt_loader import load_prompt
from foundation.shared.prompt_loader import render_prompt
from services.pipeline_shared.direct_typst_math import find_mitex_rewrites
from services.pipeline_shared.direct_typst_math import has_balanced_unescaped_dollars
from services.translation.core.context import TranslationItemContext


JSON_ONLY_INSTRUCTION = 'Return only valid JSON with the schema {"translations":[{"item_id":"...","translated_text":"..."}]}.'
LEGACY_JSON_ONLY_INSTRUCTION_ZH = (
    "返回结果时只输出符合以下结构的合法 JSON：\n"
    '{"translations":[{"item_id":"...","translated_text":"..."}]}'
)
DEFAULT_TARGET_LANGUAGE_NAME = "简体中文"
SOURCE_TERMINAL_RE = re.compile(r"[.!?。！？；;:：)\]）】”’\"']\s*$")


def _target_language_name(value: str = "") -> str:
    return (value or DEFAULT_TARGET_LANGUAGE_NAME).strip() or DEFAULT_TARGET_LANGUAGE_NAME


def _prompt_context(*, target_language_name: str = DEFAULT_TARGET_LANGUAGE_NAME) -> dict[str, str]:
    return {"target_language_name": _target_language_name(target_language_name)}


def _source_looks_incomplete(text: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return False
    return SOURCE_TERMINAL_RE.search(source) is None


def _append_context_lines(lines: list[str], item: TranslationItemContext) -> None:
    context_before = item.context_before_for_prompt()
    if context_before:
        lines.append(f"前文上下文（仅供理解，禁止翻译进输出）：{context_before}")
    context_after = item.context_after_for_prompt()
    if context_after:
        if _source_looks_incomplete(item.source_for_prompt()):
            lines.append("当前原文是不完整片段；译文必须保持同等不完整，不要用后文上下文补全。")
        lines.append(f"后文上下文（仅供理解，禁止翻译进输出）：{context_after}")


MATH_DELIMITER_DAMAGE_HINT = (
    "注意：本段原文的数学定界符 `$` 数量为奇数，说明 OCR 丢失了配对的 `$`。"
    "请按语义判断公式的真实边界，在译文中修复补全，确保每个公式的 `$...$` 成对闭合。"
)


def _append_math_delimiter_damage_hint(lines: list[str], item: TranslationItemContext) -> None:
    # 源文本 $ 不平衡时直接交给模型必然产出不平衡译文,触发整条验证/
    # 修复链(实测一个条目烧 ~10 次 LLM 调用)。先明确提示模型按语义修复。
    if not has_balanced_unescaped_dollars(item.source_for_prompt()):
        lines.append(MATH_DELIMITER_DAMAGE_HINT)
    _append_mitex_rewrite_hint(lines, item)


def _append_mitex_rewrite_hint(lines: list[str], item: TranslationItemContext) -> None:
    # 数据驱动的按需提示:只有源文本里真的出现了渲染器不支持的命令,
    # 才把对应替换规则告诉模型,由模型在语义层完成替换(复杂公式里
    # 正则改写不可靠);渲染期的正则改写保留作兜底。
    rewrites = find_mitex_rewrites(item.source_for_prompt())
    if not rewrites:
        return
    pairs = "；".join(f"`{command}` 改用 `{preferred}`" for command, preferred in rewrites)
    lines.append(f"注意：渲染器不支持本段公式中的部分 LaTeX 写法，请在译文公式中替换：{pairs}。")


def _scoped_terms_guidance(item: TranslationItemContext) -> str:
    return str((item.raw_item or {}).get("_scoped_terms_guidance", "") or "").strip()


def _append_scoped_terms_guidance(lines: list[str], item: TranslationItemContext) -> None:
    # 逐条匹配的术语指引放 user 消息:放 system 会让每条请求前缀不同,
    # 打掉 provider 前缀缓存。
    guidance = _scoped_terms_guidance(item)
    if guidance:
        lines.append(f"术语要求：\n{guidance}")


def _append_text_flow_guidance(lines: list[str], item: TranslationItemContext) -> None:
    structure_role = str((item.metadata or {}).get("structure_role", "") or "").strip().lower()
    if item.toc_entries or structure_role == "table_of_contents" or str(item.semantic_role or "").strip().lower() == "table_of_contents":
        lines.append(
            "结构提示：当前原文是目录/图表清单。必须逐行翻译，每个原文行输出一个译文行；"
            "翻译行首标签和标题，保留行尾页码且不要改动页码；不要合并行，不要输出解释。"
        )
        return
    if not item.preserve_line_structure_for_prompt or not item.line_texts:
        return
    lines.append("结构提示：当前原文是多行结构块；译文应尽量保持相同换行数量和行序，不要合并成普通段落。")


def _append_table_structure_hint(lines: list[str], item: TranslationItemContext) -> None:
    if item.block_kind != "table":
        return
    lines.append(
        "结构提示：当前原文是表格的 HTML 表示。必须保留完整的 <table>…</table> 结构、"
        "所有标签、属性与行列顺序，只翻译单元格内的英文文字内容；数字、单位、符号与公式原样保留。"
        "虽然系统提示要求不输出标签，表格例外——HTML 标签是表格结构的一部分，必须逐字保留。"
    )


def direct_math_guidance(*, target_language_name: str = DEFAULT_TARGET_LANGUAGE_NAME) -> str:
    return render_prompt("translation_direct_typst_guidance.txt", **_prompt_context(target_language_name=target_language_name))


def build_translation_system_prompt(
    *,
    domain_guidance: str = "",
    mode: str = "fast",
    response_style: str = "tagged",
    include_sci_decision: bool = False,
    target_language_name: str = DEFAULT_TARGET_LANGUAGE_NAME,
) -> str:
    system_prompt = render_prompt(
        "translation_system_plain_text.txt"
        if response_style == "plain_text"
        else "translation_system.txt",
        **_prompt_context(target_language_name=target_language_name),
    )
    if response_style != "json":
        system_prompt = system_prompt.replace(JSON_ONLY_INSTRUCTION, "")
        system_prompt = system_prompt.replace(LEGACY_JSON_ONLY_INSTRUCTION_ZH, "").strip()
    if domain_guidance.strip():
        system_prompt = f"{system_prompt}\n\nDocument-specific translation guidance:\n{domain_guidance.strip()}"
    if mode == "sci" and include_sci_decision:
        system_prompt = f"{system_prompt}\n\n{load_prompt('translation_sci_decision.txt')}"
    return system_prompt


def direct_typst_batch_user_prompt(
    batch: list[TranslationItemContext],
    *,
    mode: str,
    target_language_name: str = DEFAULT_TARGET_LANGUAGE_NAME,
) -> str:
    lines: list[str] = [
        render_prompt("translation_task_plain_text.txt", **_prompt_context(target_language_name=target_language_name)),
        "",
        "下面是若干段待翻译正文。",
        "请为每段输出一个 tagged block，除此之外不要输出结构化数据、代码块、解释或额外文字。",
        "严格格式：",
        "<<<ITEM item_id=对应的原文 ID>>>",
        "译文",
        "<<<END>>>",
    ]
    for item in batch:
        lines.append("")
        lines.append(f"原文 {item.item_id}:")
        lines.append(item.source_for_prompt())
        _append_math_delimiter_damage_hint(lines, item)
        _append_scoped_terms_guidance(lines, item)
        _append_text_flow_guidance(lines, item)
        _append_table_structure_hint(lines, item)
        if item.style_hint:
            lines.append(f"风格提示：{item.style_hint}")
        if item.continuation_group:
            lines.append("这是跨栏或跨页续接正文的一部分，请结合上下文理解后直接输出这一整段的译文。")
        _append_context_lines(lines, item)
    return "\n".join(lines).strip()


def direct_typst_single_user_prompt(
    item: TranslationItemContext,
    *,
    mode: str,
    target_language_name: str = DEFAULT_TARGET_LANGUAGE_NAME,
) -> str:
    lines: list[str] = [
        render_prompt("translation_task_plain_text.txt", **_prompt_context(target_language_name=target_language_name)),
        "",
        "下面是一段待翻译正文。",
        f"你只输出最终{_target_language_name(target_language_name)}译文正文，不要输出编号、决策字段、结构化数据、标签、代码块或解释。",
        "",
        "【当前原文开始】",
        item.source_for_prompt(),
        "【当前原文结束】",
    ]
    _append_math_delimiter_damage_hint(lines, item)
    _append_scoped_terms_guidance(lines, item)
    _append_text_flow_guidance(lines, item)
    _append_table_structure_hint(lines, item)
    if item.style_hint:
        lines.append(f"风格提示：{item.style_hint}")
    if item.continuation_group:
        lines.append("这是跨栏或跨页续接正文的一部分，请结合上下文理解后直接输出这一整段的译文。")
    _append_context_lines(lines, item)
    return "\n".join(lines).strip()


def plain_text_single_user_prompt(
    item: TranslationItemContext,
    *,
    mode: str,
    target_language_name: str = DEFAULT_TARGET_LANGUAGE_NAME,
) -> str:
    lines: list[str] = [
        render_prompt("translation_task_plain_text.txt", **_prompt_context(target_language_name=target_language_name)),
        "",
        "下面是一段待翻译正文。",
        f"只输出这一段的最终{_target_language_name(target_language_name)}译文正文，不要输出编号、决策字段、结构化数据、标签、代码块或解释。",
        "",
        "【当前原文开始】",
        item.source_for_prompt(),
        "【当前原文结束】",
    ]
    # 逐条匹配的术语指引:与 direct_typst_single_user_prompt 对齐。
    # 此前 plain_text 分支漏了这一步,导致无公式的纯文本 item 翻译时
    # user 消息里不带术语约束,模型用自己的知识翻译专名而偏离术语表。
    _append_scoped_terms_guidance(lines, item)
    _append_text_flow_guidance(lines, item)
    _append_table_structure_hint(lines, item)
    if item.style_hint:
        lines.append(f"风格提示：{item.style_hint}")
    if item.continuation_group:
        lines.append("这是跨栏或跨页续接正文的一部分，请结合上下文理解后直接输出这一整段的译文。")
    _append_context_lines(lines, item)
    return "\n".join(lines).strip()


def batch_json_user_prompt(
    batch: list[TranslationItemContext],
    *,
    target_language_name: str = DEFAULT_TARGET_LANGUAGE_NAME,
) -> str:
    groups: dict[str, dict[str, Any]] = {}
    items_payload = []
    for item in batch:
        group_id = item.continuation_group
        item_payload = item.as_batch_payload()
        if group_id:
            group = groups.setdefault(group_id, {"group_id": group_id, "item_ids": [], "combined_source_text": []})
            group["item_ids"].append(item.item_id)
            group["combined_source_text"].append(item.source_for_context())
        items_payload.append(item_payload)
    user_payload = {
        "task": render_prompt("translation_task.txt", **_prompt_context(target_language_name=target_language_name)),
        "items": items_payload,
    }
    if groups:
        user_payload["continuation_groups"] = [
            {
                "group_id": group["group_id"],
                "item_ids": group["item_ids"],
                "combined_source_text": " ".join(group["combined_source_text"]),
            }
            for group in groups.values()
        ]
    return json.dumps(user_payload, ensure_ascii=False)


def group_member_json_user_prompt(
    item: TranslationItemContext,
    *,
    target_language_name: str = DEFAULT_TARGET_LANGUAGE_NAME,
) -> str:
    raw_item = item.raw_item or {}
    member_ids = [
        str(member_id or "").strip()
        for member_id in raw_item.get("translation_unit_member_ids", [])
        if str(member_id or "").strip()
    ]
    if not member_ids:
        member_ids = [item.item_id]
    user_payload: dict[str, Any] = {
        "task": (
            f"Translate the continuation group into {_target_language_name(target_language_name)}. "
            "Return one translated fragment per member_id. Do not add text from neighboring context."
        ),
        "group": {
            "item_id": item.item_id,
            "continuation_group": item.continuation_group,
            "member_ids": member_ids,
            "combined_source_text": item.source_for_prompt(),
        },
        "output_schema": {
            "translated_text": "full translated continuation group",
            "member_translations": [
                {"item_id": "member id from member_ids", "translated_text": "translation for this member only"}
            ],
        },
    }
    if item.style_hint:
        user_payload["group"]["style_hint"] = item.style_hint
    terms_guidance = _scoped_terms_guidance(item)
    if terms_guidance:
        user_payload["group"]["terms_note"] = terms_guidance
    if str(raw_item.get("math_mode", "") or "").strip() == "direct_typst":
        if not has_balanced_unescaped_dollars(item.source_for_prompt()):
            user_payload["group"]["math_delimiter_note"] = MATH_DELIMITER_DAMAGE_HINT
        rewrites = find_mitex_rewrites(item.source_for_prompt())
        if rewrites:
            pairs = "；".join(f"`{command}` 改用 `{preferred}`" for command, preferred in rewrites)
            user_payload["group"]["math_rewrite_note"] = (
                f"渲染器不支持本段公式中的部分 LaTeX 写法，请在译文公式中替换：{pairs}。"
            )
    context_before = item.context_before_for_prompt()
    context_after = item.context_after_for_prompt()
    if context_before:
        user_payload["context_before"] = f"仅供理解，禁止翻译进输出：{context_before}"
    if context_after:
        user_payload["context_after"] = f"仅供理解，禁止翻译进输出：{context_after}"
    return json.dumps(user_payload, ensure_ascii=False)


__all__ = [
    "batch_json_user_prompt",
    "build_translation_system_prompt",
    "direct_math_guidance",
    "direct_typst_batch_user_prompt",
    "direct_typst_single_user_prompt",
    "group_member_json_user_prompt",
    "plain_text_single_user_prompt",
]
