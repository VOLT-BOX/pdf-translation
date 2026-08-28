from __future__ import annotations


CAPTION_TAGS = {"caption", "figure_caption", "image_caption", "table_caption", "table_footnote", "image_footnote"}
FOOTNOTE_TAGS = {"footnote", "image_footnote", "table_footnote", "vision_footnote"}
REFERENCE_HEADING_TAGS = {"reference_heading"}
REFERENCE_ENTRY_TAGS = {"reference_entry", "reference_zone"}
ALGORITHM_TAGS = {"algorithm"}
CAPTION_BLOCK_TYPES = {"figure_caption", "image_caption", "table_caption", "table_footnote"}
FOOTNOTE_BLOCK_TYPES = {"footnote", "image_footnote", "table_footnote", "vision_footnote"}
BODYLIKE_LAYOUT_ROLES = {"paragraph", "list_item"}
BODYLIKE_SEMANTIC_ROLES = {"body", "abstract"}
BODYLIKE_STRUCTURE_ROLES = {"", "body", "abstract", "example_line", "option_header", "option_description", "example_intro"}
TITLE_LIKE_LAYOUT_ROLES = {"title", "heading"}
TITLE_LIKE_STRUCTURE_ROLES = {"title", "heading", "section_heading"}
TEXTUAL_LAYOUT_ROLES = {"title", "heading", "paragraph", "list_item", "caption"}


def normalize_tags(tags: list[str] | set[str] | tuple[str, ...] | None) -> set[str]:
    return {str(tag or "").strip().lower() for tag in (tags or []) if str(tag or "").strip()}


def derived_role(payload: dict | None) -> str:
    source = payload or {}
    derived = source.get("derived", {}) or {}
    return str(derived.get("role", "") or "").strip().lower()


def normalized_sub_type(payload: dict | None) -> str:
    source = payload or {}
    if "normalized_sub_type" in source:
        return str(source.get("normalized_sub_type", "") or "").strip().lower()
    return str(source.get("sub_type", "") or "").strip().lower()


def layout_role(payload: dict | None) -> str:
    source = payload or {}
    return str(source.get("layout_role", "") or "").strip().lower()


def semantic_role(payload: dict | None) -> str:
    source = payload or {}
    return str(source.get("semantic_role", "") or "").strip().lower()


def block_kind(payload: dict | None) -> str:
    source = payload or {}
    explicit = str(source.get("block_kind", "") or "").strip()
    if explicit:
        return explicit.lower()
    return str(source.get("block_type", "") or "unknown").strip().lower() or "unknown"


def policy_translate(payload: dict | None) -> bool | None:
    source = payload or {}
    explicit = source.get("policy_translate")
    if isinstance(explicit, bool):
        return explicit
    policy = source.get("policy", {}) or {}
    value = policy.get("translate")
    if isinstance(value, bool):
        return value
    return None


def has_any_tag(payload: dict | None, tags: set[str]) -> bool:
    source = payload or {}
    return bool(normalize_tags(source.get("tags", [])) & tags)


def is_caption_semantic(payload: dict | None) -> bool:
    source = payload or {}
    if structure_role(source) == "figure_caption":
        return True
    if layout_role(source) == "caption":
        return True
    return derived_role(source) in {"caption", "figure_caption"} or has_any_tag(source, CAPTION_TAGS)


def is_caption_like_block(payload: dict | None) -> bool:
    source = payload or {}
    if is_caption_semantic(source):
        return True
    block_type = str(source.get("block_type", source.get("type", "")) or "").strip().lower()
    return block_type in CAPTION_BLOCK_TYPES


def is_footnote_like_block(payload: dict | None) -> bool:
    source = payload or {}
    if layout_role(source) == "footnote":
        return True
    if structure_role(source) in FOOTNOTE_TAGS:
        return True
    if derived_role(source) in FOOTNOTE_TAGS:
        return True
    if has_any_tag(source, FOOTNOTE_TAGS):
        return True
    block_type = str(source.get("block_type", source.get("type", "")) or "").strip().lower()
    return block_type in FOOTNOTE_BLOCK_TYPES


def is_reference_heading_semantic(payload: dict | None) -> bool:
    source = payload or {}
    if structure_role(source) == "reference_heading":
        return True
    return derived_role(source) == "reference_heading" or has_any_tag(source, REFERENCE_HEADING_TAGS)


def is_reference_entry_semantic(payload: dict | None) -> bool:
    source = payload or {}
    if semantic_role(source) == "reference":
        return True
    if structure_role(source) == "reference_entry":
        return True
    return derived_role(source) == "reference_entry" or has_any_tag(source, REFERENCE_ENTRY_TAGS)


def is_algorithm_semantic(payload: dict | None) -> bool:
    source = payload or {}
    block_type = str(source.get("raw_block_type", source.get("block_type", source.get("type", ""))) or "").strip().lower()
    return (
        normalized_sub_type(source) == "algorithm"
        or block_type == "algorithm"
        or derived_role(source) == "algorithm"
        or has_any_tag(source, ALGORITHM_TAGS)
    )


def is_metadata_semantic(payload: dict | None) -> bool:
    return normalized_sub_type(payload) == "metadata" or semantic_role(payload) == "metadata"


def structure_role(payload: dict | None) -> str:
    source = payload or {}
    return str(source.get("structure_role", "") or "").strip().lower()


def is_title_like_block(payload: dict | None) -> bool:
    if layout_role(payload) in TITLE_LIKE_LAYOUT_ROLES:
        return True
    return structure_role(payload) in TITLE_LIKE_STRUCTURE_ROLES


def is_body_structure_role(payload: dict | None) -> bool:
    role = structure_role(payload)
    return role in {"", "body"}


def is_body_like_structure_role(payload: dict | None) -> bool:
    role = structure_role(payload)
    return role in {"", "body", "example_line"}


def is_bodylike_block(payload: dict | None) -> bool:
    return (
        semantic_role(payload) in BODYLIKE_SEMANTIC_ROLES
        or structure_role(payload) in BODYLIKE_STRUCTURE_ROLES
        or layout_role(payload) in BODYLIKE_LAYOUT_ROLES
    )


def is_textual_block(payload: dict | None) -> bool:
    if block_kind(payload) == "text":
        return True
    return layout_role(payload) in TEXTUAL_LAYOUT_ROLES


def is_plain_text_block(payload: dict | None) -> bool:
    return block_kind(payload) == "text" and not (
        is_caption_like_block(payload)
        or is_footnote_like_block(payload)
        or is_reference_entry_semantic(payload)
        or is_title_like_block(payload)
    )


def is_plain_bodylike_block(payload: dict | None) -> bool:
    return is_plain_text_block(payload) and is_bodylike_block(payload)


def build_role_profile(payload: dict | None) -> dict[str, object]:
    source = payload or {}
    return {
        "layout_role": layout_role(source),
        "semantic_role": semantic_role(source),
        "structure_role": structure_role(source),
        "normalized_sub_type": normalized_sub_type(source),
        "policy_translate": policy_translate(source),
        "block_kind": block_kind(source),
        "is_caption_like": is_caption_like_block(source),
        "is_footnote_like": is_footnote_like_block(source),
        "is_reference_heading": is_reference_heading_semantic(source),
        "is_reference_entry": is_reference_entry_semantic(source),
        "is_algorithm": is_algorithm_semantic(source),
        "is_metadata": is_metadata_semantic(source),
        "is_title_like": is_title_like_block(source),
        "is_bodylike": is_bodylike_block(source),
        "is_textual": is_textual_block(source),
        "is_plain_text": is_plain_text_block(source),
        "is_plain_bodylike": is_plain_bodylike_block(source),
    }


def body_repair_applied(payload: dict | None) -> bool:
    source = payload or {}
    return bool(source.get("body_repair_applied") or source.get("provider_body_repair_applied"))


def body_repair_role(payload: dict | None) -> str:
    source = payload or {}
    return str(source.get("body_repair_role", source.get("provider_body_repair_role", "")) or "").strip().lower()


def body_repair_peer_block_id(payload: dict | None) -> str:
    source = payload or {}
    return str(source.get("body_repair_peer_block_id", source.get("provider_suspected_peer_block_id", "")) or "").strip()
