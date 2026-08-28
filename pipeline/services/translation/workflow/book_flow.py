from __future__ import annotations

from pathlib import Path

from services.translation.workflow.stages import run_continuation_review
from services.translation.workflow.stages import run_agent_repair_stage
from services.translation.workflow.stages import run_final_untranslated_recovery_stage
from services.translation.workflow.stages import run_garbled_reconstruction_stage
from services.translation.workflow.stages import run_initial_continuation_pass
from services.translation.workflow.stages import run_page_policy_stage
from services.translation.workflow.stages import run_translation_batch_stage
from services.translation.workflow.pages import load_page_payloads
from services.translation.workflow.pages import save_pages
from services.translation.workflow.page_policies import build_page_summaries
from services.translation.artifacts import TranslationRunDiagnostics
from services.translation.services.context.windows import annotate_translation_context_windows
from services.translation.services.continuation.orchestrator import finalize_orchestration_metadata_by_page
from services.translation.llm.shared.control_context import TranslationControlContext
from services.translation.services.policy import TranslationPolicyConfig
from services.translation.core.payload import load_translations


def translate_book_with_global_continuations(
    *,
    data: dict,
    output_dir: Path,
    page_indices: range,
    api_key: str,
    batch_size: int,
    workers: int,
    model: str,
    base_url: str,
    mode: str,
    classify_batch_size: int,
    skip_title_translation: bool,
    sci_cutoff_page_idx: int | None,
    sci_cutoff_block_idx: int | None,
    policy_config: TranslationPolicyConfig | None = None,
    domain_guidance: str = "",
    translation_context: TranslationControlContext | None = None,
    run_diagnostics: TranslationRunDiagnostics | None = None,
) -> tuple[dict[int, list[dict]], list[dict]]:
    if translation_context is not None:
        domain_guidance = translation_context.merged_guidance
    elif not domain_guidance and policy_config is not None:
        domain_guidance = policy_config.domain_guidance

    translation_paths, page_payloads = load_page_payloads(
        data=data,
        output_dir=output_dir,
        page_indices=page_indices,
        math_mode=(policy_config.math_mode if policy_config is not None else "placeholder"),
        enable_table_translation=(policy_config.enable_table_translation if policy_config is not None else False),
    )
    run_initial_continuation_pass(
        page_payloads=page_payloads,
        translation_paths=translation_paths,
    )
    if policy_config is None or policy_config.enable_candidate_continuation_review:
        run_continuation_review(
            page_payloads=page_payloads,
            translation_paths=translation_paths,
            api_key=api_key,
            model=model,
            base_url=base_url,
            workers=workers,
            run_diagnostics=run_diagnostics,
        )

    run_page_policy_stage(
        page_payloads=page_payloads,
        mode=mode,
        classify_batch_size=max(1, classify_batch_size),
        workers=max(1, workers),
        api_key=api_key,
        model=model,
        base_url=base_url,
        skip_title_translation=skip_title_translation,
        sci_cutoff_page_idx=sci_cutoff_page_idx,
        sci_cutoff_block_idx=sci_cutoff_block_idx,
        policy_config=policy_config,
        run_diagnostics=run_diagnostics,
    )
    finalize_orchestration_metadata_by_page(page_payloads)
    context_window_updates = annotate_translation_context_windows(
        page_payloads,
        mode=str(getattr(translation_context, "context_mode", "needed") if translation_context is not None else "needed"),
    )
    if context_window_updates:
        print(f"book: translation context windows updated={context_window_updates}", flush=True)
    save_pages(page_payloads, translation_paths)
    run_translation_batch_stage(
        page_payloads=page_payloads,
        translation_paths=translation_paths,
        batch_size=batch_size,
        workers=max(1, workers),
        api_key=api_key,
        model=model,
        base_url=base_url,
        domain_guidance=domain_guidance,
        mode=mode,
        translation_context=translation_context,
        run_diagnostics=run_diagnostics,
        flush_callback=None,
    )

    run_garbled_reconstruction_stage(
        page_payloads=page_payloads,
        translation_paths=translation_paths,
        api_key=api_key,
        model=model,
        base_url=base_url,
        workers=workers,
        run_diagnostics=run_diagnostics,
    )

    run_agent_repair_stage(
        page_payloads=page_payloads,
        translation_paths=translation_paths,
        api_key=api_key,
        model=model,
        base_url=base_url,
        translation_context=translation_context,
        run_diagnostics=run_diagnostics,
    )

    run_final_untranslated_recovery_stage(
        page_payloads=page_payloads,
        translation_paths=translation_paths,
        api_key=api_key,
        model=model,
        base_url=base_url,
        translation_context=translation_context,
        workers=workers,
    )
    translated_pages_map = {page_idx: load_translations(translation_paths[page_idx]) for page_idx in sorted(page_payloads)}
    summaries = build_page_summaries(
        translated_pages_map=translated_pages_map,
        translation_paths=translation_paths,
    )
    return translated_pages_map, summaries
