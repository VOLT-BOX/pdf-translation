import sys
from pathlib import Path


sys.path.append(str(Path(__file__).resolve().parents[1] / "pipeline"))

from services.translation.llm.result_payload import KEEP_ORIGIN_LABEL
from services.translation.llm.validation.english_residue import should_force_translate_body_text
from services.translation.llm.validation.quality import should_reject_keep_origin
from services.translation.services.policy.verdict import should_fast_path_keep_origin
from services.translation.services.policy.verdict import should_skip_model_by_policy


def _toc_item() -> dict:
    return {
        "item_id": "p001-b002",
        "block_kind": "text",
        "raw_block_type": "content",
        "semantic_role": "table_of_contents",
        "structure_role": "table_of_contents",
        "normalized_sub_type": "table_of_contents",
        "source_text": (
            "Tabla de contenido\n"
            "Presentacion.....1\n"
            "1. CENTRAL DE ABASTECIMIENTO DEL SISTEMA NACIONAL DE SERVICIOS DE SALUD.....3"
        ),
        "protected_source_text": (
            "Tabla de contenido\n"
            "Presentacion.....1\n"
            "1. CENTRAL DE ABASTECIMIENTO DEL SISTEMA NACIONAL DE SERVICIOS DE SALUD.....3"
        ),
        "translation_unit_protected_source_text": (
            "Tabla de contenido\n"
            "Presentacion.....1\n"
            "1. CENTRAL DE ABASTECIMIENTO DEL SISTEMA NACIONAL DE SERVICIOS DE SALUD.....3"
        ),
        "toc_entries": [
            {"title": "Presentacion", "page_label": "1"},
            {
                "number": "1.",
                "title": "CENTRAL DE ABASTECIMIENTO DEL SISTEMA NACIONAL DE SERVICIOS DE SALUD",
                "page_label": "3",
            },
        ],
    }


def test_table_of_contents_is_not_fast_path_kept_origin() -> None:
    item = _toc_item()

    assert should_skip_model_by_policy(item) is False
    assert should_fast_path_keep_origin(item) == (False, "")


def test_table_of_contents_rejects_keep_origin_degradation() -> None:
    item = _toc_item()

    assert should_force_translate_body_text(item) is True
    assert should_reject_keep_origin(item, KEEP_ORIGIN_LABEL, {"decision": KEEP_ORIGIN_LABEL}) is True
