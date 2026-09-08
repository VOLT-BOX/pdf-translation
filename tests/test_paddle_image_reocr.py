import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_paddle_image_reocr_module():
    sys.modules["fitz"] = ModuleType("fitz")
    source_path = Path(__file__).resolve().parents[1] / "pipeline/services/ocr_provider/paddle_image_reocr.py"
    spec = importlib.util.spec_from_file_location("paddle_image_reocr_under_test", source_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


paddle_image_reocr = _load_paddle_image_reocr_module()


def test_mapped_secondary_blocks_filters_single_character_noise() -> None:
    secondary_payload = {
        "layoutParsingResults": [
            {
                "prunedResult": {
                    "width": 680,
                    "height": 291,
                    "parsing_res_list": [
                        {
                            "block_label": "text",
                            "block_content": "C",
                            "block_bbox": [67, 114, 157, 193],
                        },
                        {
                            "block_label": "text",
                            "block_content": "Opciones:",
                            "block_bbox": [247, 103, 349, 130],
                        },
                        {
                            "block_label": "table",
                            "block_content": "<table><tr><td>A</td></tr></table>",
                            "block_bbox": [10, 200, 620, 260],
                        },
                    ],
                },
            }
        ]
    }

    mapped = paddle_image_reocr._mapped_secondary_blocks(
        secondary_payload,
        parent_bbox=[288.0, 388.0, 968.0, 679.0],
        crop_width=680,
        crop_height=291,
    )

    assert [block["block_content"] for block in mapped] == [
        "Opciones:",
        "<table><tr><td>A</td></tr></table>",
    ]
