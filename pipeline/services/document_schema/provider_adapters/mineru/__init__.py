from __future__ import annotations


def build_mineru_document(*args, **kwargs):
    from services.mineru.document_v1 import build_normalized_document_from_layout_payload

    return build_normalized_document_from_layout_payload(
        layout_payload=kwargs["payload"],
        document_id=kwargs["document_id"],
        layout_json_path=kwargs["source_json_path"],
        provider_version=kwargs["provider_version"],
    )


def looks_like_mineru_layout(payload: dict) -> bool:
    pdf_info = payload.get("pdf_info")
    if not isinstance(pdf_info, list):
        return False
    if not pdf_info:
        return True
    first_page = pdf_info[0]
    return isinstance(first_page, dict) and "para_blocks" in first_page


__all__ = [
    "build_mineru_document",
    "looks_like_mineru_layout",
]
