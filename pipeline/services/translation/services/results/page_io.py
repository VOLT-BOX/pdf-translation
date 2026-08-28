from __future__ import annotations

from pathlib import Path

from services.translation.core.payload import save_translations
from services.translation.core.payload.parts.translation_units import refresh_payload_translation_units


def save_pages(
    page_payloads: dict[int, list[dict]],
    translation_paths: dict[int, Path],
    page_indices: set[int] | None = None,
    *,
    refresh_units: bool = True,
) -> None:
    if refresh_units:
        flat_payload = [item for page_idx in sorted(page_payloads) for item in page_payloads[page_idx]]
        refresh_payload_translation_units(flat_payload)
    targets = sorted(page_payloads) if page_indices is None else sorted(page_indices)
    for page_idx in targets:
        save_translations(translation_paths[page_idx], page_payloads[page_idx])


__all__ = ["save_pages"]
