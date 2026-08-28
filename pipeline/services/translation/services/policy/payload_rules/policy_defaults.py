from __future__ import annotations

from services.document_schema.semantics import build_role_profile
from services.translation.core.item_reader import item_is_algorithm_like
from services.translation.core.item_reader import item_block_kind
from services.translation.core.item_reader import item_is_bodylike
from services.translation.core.item_reader import item_is_reference_like
from services.translation.core.item_reader import item_normalized_sub_type
from services.translation.core.item_reader import item_policy_translate
from services.translation.core.item_reader import item_raw_block_type

_FOUNDATIONAL_SKIP_BY_BLOCK_TYPE = {
    "image_body": ("skip_image_body", "skip_image_body"),
    "table_body": ("skip_table_body", "skip_table_body"),
    "code_body": ("code", "code"),
}
_DEFAULT_TRANSLATABLE_TEXT_STRUCTURE_ROLES = {
    "",
    "body",
    "abstract",
    "heading",
    "title",
    "footnote",
    "image_footnote",
    "table_footnote",
}


def is_ref_text_like(item: dict) -> bool:
    if item_is_reference_like(item) or item_raw_block_type(item) == "ref_text":
        return True
    return item_normalized_sub_type(item) == "ref_text"


def is_default_translatable_text_item(item: dict) -> bool:
    explicit_policy = item_policy_translate(item)
    if explicit_policy is not None:
        return explicit_policy
    if item_block_kind(item) != "text":
        return False
    role = str(build_role_profile(item).get("structure_role") or "")
    if item_is_bodylike(item):
        return True
    return role in _DEFAULT_TRANSLATABLE_TEXT_STRUCTURE_ROLES


def foundational_skip_defaults(item: dict) -> tuple[str, str] | None:
    if item_is_algorithm_like(item):
        return "skip_algorithm", "skip_algorithm"
    block_type = item_raw_block_type(item)
    normalized_block_type = block_type.strip().lower()
    if normalized_block_type in _FOUNDATIONAL_SKIP_BY_BLOCK_TYPE:
        return _FOUNDATIONAL_SKIP_BY_BLOCK_TYPE[normalized_block_type]
    if is_ref_text_like(item):
        return None
    if is_default_translatable_text_item(item):
        return None
    if normalized_block_type:
        return f"skip_{normalized_block_type}", f"skip_{normalized_block_type}"
    return "skip_non_body_text", "skip_non_body_text"


__all__ = [
    "foundational_skip_defaults",
    "is_default_translatable_text_item",
    "is_ref_text_like",
]
