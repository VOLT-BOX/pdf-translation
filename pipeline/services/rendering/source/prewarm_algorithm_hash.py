from __future__ import annotations

import hashlib
from pathlib import Path


SOURCE_CLEANUP_HASH_INPUTS = (
    "services/rendering/source_cleanup/contracts.py",
    "services/rendering/source_cleanup/executor.py",
    "services/rendering/source_cleanup/planning/coordinate_resolver.py",
    "services/rendering/source_cleanup/planning/drawing_classifier.py",
    "services/rendering/source_cleanup/planning/evidence.py",
    "services/rendering/source_cleanup/planning/geometry.py",
    "services/rendering/source_cleanup/planning/intent_classifier.py",
    "services/rendering/source_cleanup/planning/item_classifier.py",
    "services/rendering/source_cleanup/planning/mixed_content.py",
    "services/rendering/source_cleanup/planning/items.py",
    "services/rendering/source_cleanup/planning/page_features.py",
    "services/rendering/source_cleanup/planning/page_gate.py",
    "services/rendering/source_cleanup/planning/page_probe.py",
    "services/rendering/source_cleanup/planning/planner.py",
    "services/rendering/source_cleanup/planning/rect_filter.py",
    "services/rendering/source_cleanup/planning/rects.py",
    "services/rendering/source_cleanup/planning/segments.py",
    "services/rendering/source_cleanup/protected_blocks.py",
    "services/rendering/source_cleanup/pdf/document.py",
    "services/rendering/source_cleanup/pdf/hit_test.py",
    "services/rendering/source_cleanup/pdf/stream_engine.py",
    "services/rendering/source_cleanup/pdf/text_removal.py",
    "services/rendering/source_cleanup/pdf/xobject_ops.py",
)


def source_cleanup_implementation_hash() -> str:
    scripts_root = Path(__file__).resolve().parents[3]
    digest = hashlib.sha256()
    for relative_path in SOURCE_CLEANUP_HASH_INPUTS:
        path = scripts_root / relative_path
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


__all__ = [
    "SOURCE_CLEANUP_HASH_INPUTS",
    "source_cleanup_implementation_hash",
]
