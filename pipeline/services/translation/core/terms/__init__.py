from services.translation.core.terms.abbreviations import AbbreviationEntry
from services.translation.core.terms.abbreviations import build_abbreviation_guidance
from services.translation.core.terms.abbreviations import matched_abbreviation_entries
from services.translation.core.terms.glossary import GlossaryEntry
from services.translation.core.terms.glossary import build_glossary_guidance
from services.translation.core.terms.glossary import context_matches
from services.translation.core.terms.glossary import glossary_hard_entries
from services.translation.core.terms.glossary import matched_glossary_entries
from services.translation.core.terms.glossary import normalize_glossary_entries
from services.translation.core.terms.glossary import parse_glossary_json
from services.translation.core.terms.glossary import term_pattern
from services.translation.core.terms.injection import build_terms_guidance
from services.translation.core.terms.matcher import GlossaryMatcher

__all__ = [
    "AbbreviationEntry",
    "GlossaryEntry",
    "GlossaryMatcher",
    "build_abbreviation_guidance",
    "build_glossary_guidance",
    "build_terms_guidance",
    "context_matches",
    "glossary_hard_entries",
    "matched_abbreviation_entries",
    "matched_glossary_entries",
    "normalize_glossary_entries",
    "parse_glossary_json",
    "term_pattern",
]
