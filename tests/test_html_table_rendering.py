import importlib.util
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
import sys


def _stub_module(name: str, **attrs: object) -> None:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


def _load_renderer_module():
    _stub_module("services")
    _stub_module("services.rendering")
    _stub_module("services.rendering.layout")
    _stub_module("services.rendering.layout.model")
    _stub_module("services.rendering.layout.model.models", RenderBlock=object)
    _stub_module("services.rendering.layout.inline_content")
    _stub_module("services.rendering.layout.inline_content.core")
    _stub_module(
        "services.rendering.layout.inline_content.core.markdown",
        build_direct_typst_passthrough_text=lambda *args, **kwargs: "",
    )
    _stub_module("services.rendering.layout.payload")
    _stub_module(
        "services.rendering.layout.payload.formula_safety",
        formula_safety_insets_pt=lambda *args, **kwargs: SimpleNamespace(total_pt=0.0, top_pt=0.0, bottom_pt=0.0),
        has_long_inline_math_layout_risk=lambda *args, **kwargs: False,
    )
    _stub_module("services.rendering.output")
    _stub_module("services.rendering.output.typst")
    _stub_module("services.rendering.output.typst.block_fit", fit_dimensions=lambda **kwargs: {})
    _stub_module("services.rendering.output.typst.block_config")
    _stub_module(
        "services.rendering.output.typst.block_fields",
        typst_block_fields=lambda *args, **kwargs: None,
        typst_rgb=lambda *args, **kwargs: "black",
    )
    _stub_module(
        "services.rendering.output.typst.block_markup",
        typst_markdown_block=lambda *args, **kwargs: "",
        typst_markdown_fit_call=lambda *args, **kwargs: "",
        typst_place_context=lambda *args, **kwargs: "",
        typst_plain_markdown_expr=lambda *args, **kwargs: "",
        typst_plain_text_expr=lambda *args, **kwargs: "",
        typst_preserved_lines_expr=lambda *args, **kwargs: "",
        typst_single_line_fit_call=lambda *args, **kwargs: "",
    )
    _stub_module("services.rendering.output.typst.shared", escape_typst_string=lambda text: text)

    source_path = Path(__file__).resolve().parents[1] / "pipeline/services/rendering/output/typst/block_renderer.py"
    spec = importlib.util.spec_from_file_location("table_renderer_under_test", source_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RENDERER = _load_renderer_module()


def _fields() -> SimpleNamespace:
    return SimpleNamespace(
        var_prefix="table_test",
        width=300.0,
        height=100.0,
        font_size=10.0,
        font_weight="regular",
        x0=0.0,
        y0=0.0,
    )


def test_html_table_parser_preserves_empty_rows() -> None:
    rows = RENDERER._parse_html_table("<table><tr><td>Title</td></tr><tr></tr><tr><td>Value</td></tr></table>")

    assert [len(row) for row in rows] == [1, 0, 1]


def test_html_table_renderer_adds_cell_clearance_and_empty_row_track() -> None:
    source = "<table><tr><td>Title</td></tr><tr></tr><tr><td>Value</td></tr></table>"

    typst = RENDERER._build_html_table_typst_block(
        "table-test",
        _fields(),
        source,
        text_fill="black",
        block_fill="",
    )

    assert typst is not None
    assert "inset: (x: 1.8pt, y: 1.6pt)" in typst
    assert "set par(leading: 0.88em, justify: false);" in typst
    assert "rows: (auto, 10.00pt, auto)," in typst
