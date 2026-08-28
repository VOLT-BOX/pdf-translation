from __future__ import annotations

from typing import Pattern


def protected_token_re() -> Pattern[str]:
    from services.translation.public import PROTECTED_TOKEN_RE

    return PROTECTED_TOKEN_RE


def protected_map_from_formula_map(formula_map: list[dict]) -> list[dict]:
    from services.translation.public import protected_map_from_formula_map as _protected_map_from_formula_map

    return _protected_map_from_formula_map(formula_map)


def restore_protected_tokens(text: str, protected_map: list[dict]) -> str:
    from services.translation.public import restore_protected_tokens as _restore_protected_tokens

    return _restore_protected_tokens(text, protected_map)


def re_protect_restored_formulas(text: str, formula_map: list[dict]) -> str:
    from services.translation.public import re_protect_restored_formulas as _re_protect_restored_formulas

    return _re_protect_restored_formulas(text, formula_map)


__all__ = [
    "protected_map_from_formula_map",
    "protected_token_re",
    "re_protect_restored_formulas",
    "restore_protected_tokens",
]
