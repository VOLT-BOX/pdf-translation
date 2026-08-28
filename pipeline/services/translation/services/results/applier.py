from __future__ import annotations

from services.translation.services.memory import TranslationMemoryUpdater
from services.translation.services.memory import update_translation_memory
from services.translation.services.memory import update_translation_memory_many
from services.translation.core.payload.parts.apply import apply_group_translated_entry
from services.translation.core.payload.parts.apply import apply_single_translated_entry
from services.translation.core.payload.parts.common import effective_translation_unit_id
from services.translation.core.payload.parts.common import existing_group_unit_id
from services.translation.core.payload.parts.common import is_group_unit_id
from services.translation.core.payload.parts.common import GROUP_ITEM_PREFIX
from services.translation.artifacts.models import FinalStatus
import services.translation.llm.result_payload as result_payload

from services.translation.services.results.flush import TranslationFlushState


def _clone_result_for_item(payload: dict[str, str], *, item: dict) -> dict[str, str]:
    cloned = dict(payload)
    diagnostics = dict(cloned.get("translation_diagnostics") or {})
    if diagnostics:
        diagnostics["item_id"] = item.get("item_id", "")
        diagnostics["page_idx"] = item.get("page_idx")
        cloned["translation_diagnostics"] = diagnostics
    return cloned


def expand_duplicate_results(
    translated: dict[str, dict[str, str]],
    *,
    duplicate_items_by_rep_id: dict[str, list[dict]],
) -> dict[str, dict[str, str]]:
    if not duplicate_items_by_rep_id:
        return translated
    expanded = dict(translated)
    for rep_id, duplicate_items in duplicate_items_by_rep_id.items():
        rep_payload = translated.get(rep_id)
        if not rep_payload:
            continue
        if not _is_duplicate_expandable_result(rep_payload):
            expanded.update(_failed_duplicate_results(rep_payload, duplicate_items))
            continue
        for duplicate_item in duplicate_items:
            expanded[str(duplicate_item.get("item_id", "") or "")] = _clone_result_for_item(
                rep_payload,
                item=duplicate_item,
            )
    return expanded


def _is_duplicate_expandable_result(payload: dict[str, str]) -> bool:
    final_status = str(payload.get("final_status", "") or "").strip()
    if final_status in {FinalStatus.FAILED.value, FinalStatus.KEPT_ORIGIN.value}:
        return False
    decision = str(payload.get("decision", "") or "").strip()
    if decision == "keep_origin":
        return False
    translated_text = str(
        payload.get("translated_text")
        or payload.get("protected_translated_text")
        or ""
    ).strip()
    if not translated_text:
        return False
    return True


def _failed_duplicate_results(rep_payload: dict[str, str], duplicate_items: list[dict]) -> dict[str, dict[str, str]]:
    source_diagnostics = dict(rep_payload.get("translation_diagnostics") or {})
    source_reason = str(source_diagnostics.get("degradation_reason", "") or "").strip()
    error_trace = source_diagnostics.get("error_trace")
    if not isinstance(error_trace, list):
        error_trace = []
    reason = source_reason or "duplicate_representative_not_expandable"
    failed: dict[str, dict[str, str]] = {}
    for duplicate_item in duplicate_items:
        item_id = str(duplicate_item.get("item_id", "") or "")
        payload = result_payload.result_entry("keep_origin", "")
        payload["translation_diagnostics"] = {
            "item_id": item_id,
            "page_idx": duplicate_item.get("page_idx"),
            "route_path": ["block_level", "duplicate_dedupe", "fast_path_keep_origin"],
            "error_trace": [
                *error_trace,
                {
                    "type": "dedupe",
                    "code": "duplicate_representative_not_expandable",
                    "message": "Duplicate item was kept as origin because representative translation did not succeed.",
                },
            ],
            "fallback_to": "keep_origin",
            "degradation_reason": reason,
            "final_status": "kept_origin",
        }
        failed[item_id] = payload
    return failed


def current_payload_page_indexes(flat_payload: list[dict], fallback_item_to_page: dict[str, int]) -> tuple[dict[str, int], dict[str, set[int]]]:
    item_to_page: dict[str, int] = dict(fallback_item_to_page)
    unit_to_pages: dict[str, set[int]] = {}
    for item in flat_payload:
        item_id = str(item.get("item_id", "") or "")
        page_idx = item.get("page_idx")
        if page_idx is None:
            page_idx = fallback_item_to_page.get(item_id)
        if page_idx is None:
            continue
        item_to_page[item_id] = int(page_idx)
        unit_id = str(item.get("translation_unit_id") or item_id or "")
        if unit_id:
            unit_to_pages.setdefault(unit_id, set()).add(int(page_idx))
    return item_to_page, unit_to_pages


def touched_pages_for_batch(
    translated: dict[str, str],
    flat_payload: list[dict],
    fallback_item_to_page: dict[str, int],
) -> set[int]:
    item_to_page, unit_to_pages = current_payload_page_indexes(flat_payload, fallback_item_to_page)
    touched_pages: set[int] = set()
    for item_id in translated:
        if item_id.startswith(GROUP_ITEM_PREFIX):
            touched_pages.update(unit_to_pages.get(item_id, set()))
        elif item_id in item_to_page:
            touched_pages.add(item_to_page[item_id])
    return touched_pages


class TranslationResultApplier:
    def __init__(
        self,
        *,
        flat_payload: list[dict],
        item_to_page: dict[str, int],
        duplicate_items_by_rep_id: dict[str, list[dict]],
        flush_state: TranslationFlushState,
        memory_store: TranslationMemoryUpdater | None,
    ) -> None:
        self.flat_payload = flat_payload
        self.item_to_page = item_to_page
        self.duplicate_items_by_rep_id = duplicate_items_by_rep_id
        self.flush_state = flush_state
        self.memory_store = memory_store
        self.item_by_id: dict[str, dict] = {
            str(item.get("item_id", "") or ""): item
            for item in flat_payload
            if str(item.get("item_id", "") or "")
        }
        self.next_item_by_id: dict[str, dict | None] = {
            str(item.get("item_id", "") or ""): flat_payload[index + 1] if index + 1 < len(flat_payload) else None
            for index, item in enumerate(flat_payload)
            if str(item.get("item_id", "") or "")
        }
        self.group_items_by_unit_id: dict[str, list[dict]] = {}
        self._rebuild_group_index()

    def _rebuild_group_index(self) -> None:
        grouped: dict[str, list[dict]] = {}
        for item in self.flat_payload:
            unit_id = effective_translation_unit_id(item)
            if is_group_unit_id(unit_id):
                grouped.setdefault(unit_id, []).append(item)
        self.group_items_by_unit_id = grouped

    def apply_immediate(self, translated: dict[str, dict[str, str]]) -> set[int]:
        return self.apply_batch([], translated, update_memory=False)

    def apply_batch(
        self,
        batch: list[dict],
        translated: dict[str, dict[str, str]],
        *,
        update_memory: bool = True,
    ) -> set[int]:
        expanded = expand_duplicate_results(
            translated,
            duplicate_items_by_rep_id=self.duplicate_items_by_rep_id,
        )
        self._apply_translated_results(expanded)
        if update_memory:
            update_translation_memory(self.memory_store, batch=batch, translated=expanded)
        touched_pages = self._touched_pages_for_expanded(expanded)
        self.flush_state.mark_dirty(touched_pages)
        return touched_pages

    def apply_batches(
        self,
        results: list[tuple[list[dict], dict[str, dict[str, str]]]],
        *,
        update_memory: bool = True,
    ) -> set[int]:
        touched_pages: set[int] = set()
        if not results:
            return touched_pages
        memory_updates: list[tuple[list[dict], dict[str, dict[str, str]]]] = []
        for batch, translated in results:
            expanded = expand_duplicate_results(
                translated,
                duplicate_items_by_rep_id=self.duplicate_items_by_rep_id,
            )
            self._apply_translated_results(expanded)
            touched_pages.update(self._touched_pages_for_expanded(expanded))
            if update_memory:
                memory_updates.append((batch, expanded))
        if update_memory:
            update_translation_memory_many(self.memory_store, memory_updates)
        self.flush_state.mark_dirty(touched_pages)
        return touched_pages

    def _apply_translated_results(self, translated: dict[str, dict[str, str]]) -> None:
        if not translated:
            return
        for item_id, raw_result in translated.items():
            if is_group_unit_id(str(item_id)):
                apply_group_translated_entry(self.group_items_by_unit_id.get(str(item_id), []), raw_result)
                continue
            item = self.item_by_id.get(str(item_id))
            if item is None:
                continue
            preserved_group_unit_id = existing_group_unit_id(item)
            apply_single_translated_entry(
                item,
                raw_result,
                next_item=self.next_item_by_id.get(str(item_id)),
                preserved_group_unit_id=preserved_group_unit_id,
            )
        if any(is_group_unit_id(str(item_id)) or existing_group_unit_id(self.item_by_id.get(str(item_id), {})) for item_id in translated):
            self._rebuild_group_index()

    def _touched_pages_for_expanded(self, translated: dict[str, dict[str, str]]) -> set[int]:
        touched_pages: set[int] = set()
        for item_id in translated:
            item_id = str(item_id)
            if item_id.startswith(GROUP_ITEM_PREFIX):
                for item in self.group_items_by_unit_id.get(item_id, []):
                    page_idx = item.get("page_idx")
                    if page_idx is None:
                        page_idx = self.item_to_page.get(str(item.get("item_id", "") or ""))
                    if page_idx is not None:
                        touched_pages.add(int(page_idx))
                continue
            page_idx = self.item_to_page.get(item_id)
            if page_idx is not None:
                touched_pages.add(int(page_idx))
        return touched_pages


__all__ = [
    "TranslationResultApplier",
    "current_payload_page_indexes",
    "expand_duplicate_results",
    "touched_pages_for_batch",
]
