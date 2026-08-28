from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    """Write JSON without building a giant intermediate string.

    ``path.write_text(json.dumps(...))`` doubles peak memory (object graph +
    full serialized text). For ~100MB+ document.v1 payloads that triggers
    MemoryError on Windows desktop Python. Stream with ``json.dump`` instead.

    compact=True skips pretty-printing; use it for large machine-consumed
    documents (document.v1.json, provider payloads) where indent=2 inflates
    the file by 30-50% and slows every downstream parse.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        dump_kwargs: dict[str, Any] = {
            "ensure_ascii": False,
            "separators": (",", ":"),
        }
    else:
        dump_kwargs = {
            "ensure_ascii": False,
            "indent": 2,
        }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, **dump_kwargs)


def load_json(path: Path) -> Any:
    """Read JSON without ``read_text`` + ``json.loads`` (same memory concern)."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
