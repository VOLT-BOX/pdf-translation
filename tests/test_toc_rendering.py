import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass
class RenderTocEntry:
    title: str
    page_label: str
    bbox: list[float]
    number: str = ""
    level: int = 1


def _stub_module(name: str, **attrs: object) -> None:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


def _load_toc_structure_module():
    _stub_module("services")
    _stub_module("services.document_schema")
    _stub_module(
        "services.document_schema.toc",
        build_toc_entries=lambda *, lines, line_texts: [],
        order_toc_lines_by_geometry=lambda *, lines, line_texts: [
            (index, text, lines[index] if index < len(lines) else {})
            for index, text in enumerate(line_texts)
        ],
    )
    _stub_module("services.rendering")
    _stub_module("services.rendering.layout")
    _stub_module("services.rendering.layout.model")
    _stub_module("services.rendering.layout.model.models", RenderTocEntry=RenderTocEntry)

    source_path = Path(__file__).resolve().parents[1] / "pipeline/services/rendering/layout/payload/toc_structure.py"
    spec = importlib.util.spec_from_file_location("toc_structure_under_test", source_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TOC_STRUCTURE = _load_toc_structure_module()


def test_toc_entries_use_source_line_index_for_translated_lines() -> None:
    item = {
        "structure_role": "table_of_contents",
        "semantic_role": "table_of_contents",
        "source_line_texts": [
            "Tabla de contenido",
            "Presentacion.....1",
            "CONTENIDO.....2",
            "1. CENTRAL DE ABASTECIMIENTO DEL SISTEMA NACIONAL DE SERVICIOS DE SALUD.....3",
        ],
        "lines": [
            {"bbox": [0.0, 0.0, 100.0, 10.0]},
            {"bbox": [0.0, 10.0, 100.0, 20.0]},
            {"bbox": [0.0, 20.0, 100.0, 30.0]},
            {"bbox": [0.0, 30.0, 100.0, 40.0]},
        ],
        "toc_entries": [
            {
                "title": "Presentacion",
                "page_label": "1",
                "line_index": 1,
                "bbox": [0.0, 10.0, 100.0, 20.0],
            },
            {
                "title": "CONTENIDO",
                "page_label": "2",
                "line_index": 2,
                "bbox": [0.0, 20.0, 100.0, 30.0],
            },
            {
                "number": "1.",
                "title": "CENTRAL DE ABASTECIMIENTO DEL SISTEMA NACIONAL DE SERVICIOS DE SALUD",
                "page_label": "3",
                "line_index": 3,
                "bbox": [0.0, 30.0, 100.0, 40.0],
            },
        ],
    }
    translated_text = (
        "目录\n"
        "介绍.....1\n"
        "目录.....2\n"
        "1. 国家卫生服务系统供应中心.....3"
    )

    rendered = TOC_STRUCTURE.render_toc_entries_for_item(item, translated_text)

    assert [(entry.number, entry.title, entry.page_label) for entry in rendered] == [
        ("", "目录", ""),
        ("", "介绍", "1"),
        ("", "目录", "2"),
        ("1.", "国家卫生服务系统供应中心", "3"),
    ]
