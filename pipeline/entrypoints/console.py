"""Console-script adapters for the RetainPDF Python worker entrypoints."""

from __future__ import annotations

from collections.abc import Callable

from foundation.shared.structured_errors import run_with_structured_failure


def _run_structured(
    main_fn: Callable[[], object],
    *,
    default_stage: str,
    provider: str,
) -> int:
    run_with_structured_failure(main_fn, default_stage=default_stage, provider=provider)
    return 0


def run_book() -> int:
    from services.translation.entrypoints.from_ocr_pipeline import main

    return _run_structured(main, default_stage="translation", provider="translation")


def run_provider_ocr() -> int:
    from services.ocr_provider.provider_pipeline import main

    return _run_structured(main, default_stage="provider", provider="ocr")


def run_provider_case() -> int:
    from services.ocr_provider.provider_pipeline import main

    return _run_structured(main, default_stage="provider", provider="ocr")


def run_normalize_ocr() -> int:
    from services.document_schema.normalize_pipeline import main

    return _run_structured(main, default_stage="normalization", provider="ocr")


def run_translate_from_ocr() -> int:
    from services.translation.entrypoints.from_ocr_pipeline import main

    return _run_structured(main, default_stage="translation", provider="translation")


def run_translate_only() -> int:
    from services.translation.entrypoints.translate_only_pipeline import main

    return _run_structured(main, default_stage="translation", provider="translation")


def run_render_only() -> int:
    from services.rendering.workflow.render_only import main

    return _run_structured(main, default_stage="rendering", provider="rendering")
