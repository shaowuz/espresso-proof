"""The forbidden-proof-language primitive shared by every text gate."""

from __future__ import annotations

from espresso_proof.kernel.proof_language import contains_proof_language
import re


def _first_forbidden_proof_language(text: str) -> str | None:
    canonical_phrase = contains_proof_language(text)
    if canonical_phrase:
        return canonical_phrase
    forbidden_patterns = {
        # Spelled in parts: repository text must not contain the banned closing
        # abbreviation.
        r"\bq" r"\.?e" r"\.?d" r"\.?\b": "Q" "ED",
        r"\bverified\b": "verified",
        r"\b(?:the\s+)?(?:theorem|claim|result)\s+(?:is|was|has\s+been)\s+settled\b": "settled result",
        r"\bsettled\s+the\s+(?:theorem|claim|result)\b": "settled result",
        r"\bsettles?\s+the\s+(?:theorem|claim|result)\b": "settles result",
        r"\bproof\s+complete\b": "proof complete",
        r"\bwe\s+proved\b": "we proved",
        r"\bproved\s+the\s+theorem\b": "proved the theorem",
        r"\bproven\b": "proven",
        r"\bproves\s+the\s+claim\b": "proves the claim",
        r"\bproves\s+the\s+theorem\b": "proves the theorem",
        r"\bhas\s+been\s+proven\b": "has been proven",
        r"\bthis\s+proof\b": "this proof",
        r"\bcomplete\s+proof\b": "complete proof",
        r"\bfull\s+proof\b": "full proof",
        r"\b(?:route|strategy)\s+(?:is|are|was|were)\s+(?:a\s+)?(?:proof|proof\s+status|source\s+support|evidence)\b": "route/strategy proof status",
        r"\broute\s+(?:is\s+)?(?:a\s+)?proof\b": "route is a proof",
        r"\broute\s+proves\s+the\s+(?:result|claim|theorem)\b": "route proves the result",
        r"\broute\s+establishes\s+the\s+(?:result|claim|theorem)\b": "route establishes the result",
        r"\b(?:route|strategy)\s+(?:show(?:s|ed|ing)?|demonstrat(?:e|es|ed|ing)|verif(?:y|ies|ied|ying)|validat(?:e|es|ed|ing)|support(?:s|ed|ing)?|certif(?:y|ies|ied|ying)|prove(?:s|d|n|ing)?)\s+(?:the\s+)?(?:theorem|claim|result|proof|proof\s+status|source\s+support|evidence)\b": "route/strategy status projection",
        r"\btheorem\s+applicability\s+(?:proves|establishes|confirms)\b": "theorem applicability proves",
        r"\bapplicability\s+(?:proves|establishes)\s+the\s+(?:route|claim|theorem|result)\b": "applicability proves",
        r"\bestablishes\s+the\s+theorem\b": "establishes the theorem",
        r"\bestablishes\s+the\s+claim\b": "establishes the claim",
        r"\bproof\s+establishes\b": "proof establishes",
    }
    for pattern, phrase in forbidden_patterns.items():
        if re.search(pattern, text):
            return phrase
    return None
