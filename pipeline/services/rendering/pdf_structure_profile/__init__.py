from __future__ import annotations

from typing import Any


_LAZY_EXPORTS = {
    "PdfObjectBox": ("services.rendering.pdf_structure_profile.contracts", "PdfObjectBox"),
    "PdfStructureDocumentProfile": (
        "services.rendering.pdf_structure_profile.contracts",
        "PdfStructureDocumentProfile",
    ),
    "PdfStructureItemHit": (
        "services.rendering.pdf_structure_profile.contracts",
        "PdfStructureItemHit",
    ),
    "PdfStructurePageProfile": (
        "services.rendering.pdf_structure_profile.contracts",
        "PdfStructurePageProfile",
    ),
    "build_pdf_structure_page_profile": (
        "services.rendering.pdf_structure_profile.sampler",
        "build_pdf_structure_page_profile",
    ),
    "build_pdf_structure_profile": (
        "services.rendering.pdf_structure_profile.sampler",
        "build_pdf_structure_profile",
    ),
    "pdf_structure_profile_from_manifest": (
        "services.rendering.pdf_structure_profile.manifest",
        "pdf_structure_profile_from_manifest",
    ),
    "pdf_structure_profile_path_from_prewarm_manifest": (
        "services.rendering.pdf_structure_profile.io",
        "pdf_structure_profile_path_from_prewarm_manifest",
    ),
    "pdf_structure_profile_to_manifest": (
        "services.rendering.pdf_structure_profile.manifest",
        "pdf_structure_profile_to_manifest",
    ),
    "read_pdf_structure_profile": (
        "services.rendering.pdf_structure_profile.io",
        "read_pdf_structure_profile",
    ),
    "write_pdf_structure_profile": (
        "services.rendering.pdf_structure_profile.io",
        "write_pdf_structure_profile",
    ),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    from importlib import import_module

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


__all__ = sorted(_LAZY_EXPORTS)
