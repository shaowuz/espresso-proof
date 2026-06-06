from __future__ import annotations

import re
import urllib.parse
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from .live_search import fetch_url
from .models import TheoremExtractionReport


def ingest_source_for_theorems(
    *,
    claim_id: str,
    source: str,
    source_id: str,
    title: str,
    authors: list[str] | None = None,
    source_type: str = "paper",
) -> dict:
    raw = read_source_bytes(source)
    text, extraction_notes = extract_source_text(raw, source)
    theorems = extract_theorems(text, source_ref_id=source_id)
    source_ref = {
        "id": source_id,
        "type": source_type,
        "title": title,
        "authors": authors or [],
        "locator": locator_for_source(source),
        "statement_or_summary": f"Source ingested for theorem extraction; {len(theorems)} candidate statement(s) found.",
        "hypothesis_match": {"matches": "unclear", "mismatches": []},
        "confidence": "low",
        "status": "candidate",
    }
    report = {
        "schema_version": 1,
        "claim_id": claim_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": [source_ref],
        "theorems": theorems,
        "notes": extraction_notes + [
            "PDF/text ingestion extracts theorem-like statements heuristically.",
            "Theorem extraction is structure/regex based, not LLM-grade semantic parsing.",
            "Extraction does not make a source source-backed; run Referencing verification next.",
        ],
    }
    TheoremExtractionReport.model_validate(report)
    return report


def read_source_bytes(source: str) -> bytes:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return fetch_url(source, {"User-Agent": "espresso-proof/0.1 (local research workflow)"}, 30)
    return Path(source).read_bytes()


def looks_like_pdf(raw: bytes, source: str) -> bool:
    return raw.startswith(b"%PDF") or source.lower().endswith(".pdf")


def extract_source_text(raw: bytes, source: str) -> tuple[str, list[str]]:
    if looks_like_pdf(raw, source):
        text, method = extract_text_from_pdf_bytes_with_method(raw)
        return text, [method]
    return raw.decode("utf-8", errors="ignore"), ["Read source as UTF-8 text."]


def extract_text_from_pdf_bytes(raw: bytes) -> str:
    text, _ = extract_text_from_pdf_bytes_with_method(raw)
    return text


def extract_text_from_pdf_bytes_with_method(raw: bytes) -> tuple[str, str]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        text, fragments = fallback_pdf_text(raw)
        return text, f"pypdf is not installed; fallback PDF text extraction recovered {fragments} text fragment(s)."
    try:
        reader = PdfReader(BytesIO(raw))
        chunks = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
    except Exception as exc:
        fallback, fragments = fallback_pdf_text(raw)
        return fallback, (
            "pypdf failed to parse the PDF; fallback PDF text extraction recovered "
            f"{fragments} text fragment(s). Error: {exc}"
        )
    text = "\n".join(chunks).strip()
    if text:
        return text, f"Extracted PDF text with pypdf from {len(reader.pages)} page(s)."
    fallback, fragments = fallback_pdf_text(raw)
    return fallback, (
        "pypdf returned no text; fallback PDF text extraction recovered "
        f"{fragments} text fragment(s)."
    )


def fallback_pdf_text(raw: bytes) -> tuple[str, int]:
    text = raw.decode("latin-1", errors="ignore")
    strings = []
    for match in re.finditer(r"\((?:\\.|[^\\)])*\)", text, flags=re.DOTALL):
        value = decode_pdf_literal_string(match.group(0)[1:-1])
        if has_words(value):
            strings.append(value)
    for match in re.finditer(r"(?<!<)<([0-9A-Fa-f\s]{8,})>(?!>)", text):
        value = decode_pdf_hex_string(match.group(1))
        if has_words(value):
            strings.append(value)
    if strings:
        return "\n".join(strings), len(strings)
    readable_runs = re.findall(r"[A-Za-z][A-Za-z0-9 ,.;:()\\[\\]/_-]{20,}", text)
    if readable_runs:
        return "\n".join(readable_runs), len(readable_runs)
    return text, 0


def decode_pdf_literal_string(value: str) -> str:
    def replace_octal(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 8))

    value = re.sub(r"\\([0-7]{1,3})", replace_octal, value)
    replacements = {
        r"\(": "(",
        r"\)": ")",
        r"\\": "\\",
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
        r"\b": "\b",
        r"\f": "\f",
    }
    for escaped, plain in replacements.items():
        value = value.replace(escaped, plain)
    return value


def decode_pdf_hex_string(value: str) -> str:
    cleaned = re.sub(r"\s+", "", value)
    if len(cleaned) % 2:
        cleaned += "0"
    try:
        return bytes.fromhex(cleaned).decode("utf-8", errors="ignore")
    except ValueError:
        return ""


def has_words(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3,}", value))


def fallback_statement_text(text: str) -> str:
    for stop in [" Proof.", " Proof ", " References ", " Bibliography "]:
        position = text.find(stop)
        if position > 40:
            text = text[:position]
            break
    return text


def extract_candidate_locators(text: str, *, source_ref_id: str = "src_fulltext") -> list[dict]:
    """Autonomous Retrieval V1 primitive: pull candidate theorem/lemma/
    proposition/corollary blocks WITH precise locators from full text.

    Returns CANDIDATE-only records: ``{id, kind, label, locator, is_exact,
    statement, status="candidate"}``. ``status`` is pinned to ``"candidate"`` —
    extraction never sets ``source_backed`` or any earned status; only the
    existing source-grounding gate (exact locator + hypothesis match +
    attribution) can. ``is_exact`` reports whether the recorded locator is precise
    enough to be groundable (a block with an unclear locator stays candidate /
    "needs a source"). Deterministic, read-only, fail-closed (no network).
    """

    from .models import Locator

    out: list[dict] = []
    for theorem in extract_theorems(text, source_ref_id=source_ref_id):
        locator = theorem["locator"]
        out.append(
            {
                "id": theorem["id"],
                "kind": theorem["kind"],
                "label": theorem["label"],
                "locator": locator,
                "is_exact": Locator.model_validate(locator).is_exact,
                "statement": theorem["statement"],
                "status": "candidate",
            }
        )
    return out


# A locator-like label: numeric (1, 1.2, 2.1.3) or letter+dotted-number (A.3),
# NOT an arbitrary word ("about", "Smoothness") that merely follows the keyword.
_THEOREM_KINDS = "Theorem|Proposition|Lemma|Corollary|Definition|Remark"
_LABEL_TOKEN = r"[0-9]+(?:\.[0-9A-Za-z]+)*|[A-Z]\.[0-9]+(?:\.[0-9]+)*"
# A theorem header starts at text start or after a sentence boundary, so a
# mid-sentence reference like "by Theorem 5" does not split a statement.
_HEADER_RE = re.compile(
    rf"(?:(?<=[.:;]\s)|^)(?P<kind>{_THEOREM_KINDS})"
    rf"(?:\s+(?P<label>{_LABEL_TOKEN}))?"
    rf"(?:\s*\((?P<name>[^)]+)\))?"
    rf"\s*[.:]?\s",
    flags=re.IGNORECASE,
)


def _theorem_locator(kind: str, label: str, name: str) -> dict:
    """Build a locator. EXACT only when there is a concrete label or a
    parenthetical name a reviewer can find; otherwise the value is empty so
    ``Locator.is_exact`` is False (an unlabeled/malformed header stays candidate)."""
    locator_kind = locator_kind_for_statement(kind)
    if label:
        value = f"{kind.title()} {label}"
    elif name:
        value = f"{kind.title()} ({name})"
    else:
        value = ""  # no concrete locator -> not exact
    return {"kind": locator_kind, "value": value}


def extract_theorems(text: str, *, source_ref_id: str) -> list[dict]:
    normalized = normalize_text(text)
    # 1) Find every theorem-like HEADER and its position. 2) Each statement runs
    # from the end of its header to the start of the next header (or end of text).
    # This splits consecutive blocks (Theorem 1 ... Proposition 2 ... Lemma 3 ...)
    # instead of concatenating them into one statement.
    headers = list(_HEADER_RE.finditer(normalized))
    theorems = []
    for index, match in enumerate(headers, start=1):
        kind = match.group("kind").lower()
        raw_label = (match.group("label") or "").strip()
        name = (match.group("name") or "").strip()
        end = headers[index].start() if index < len(headers) else len(normalized)
        body = normalized[match.end():end]
        statement = clean_statement(fallback_statement_text(body))
        if not statement:
            continue
        # Display label: the locator-like label, else the parenthetical name,
        # else the running index (display only; does not affect exactness).
        label = clean_label(raw_label or name or str(index))
        theorems.append(
            {
                "id": f"thm_{index:03d}",
                "source_ref_id": source_ref_id,
                "kind": kind,
                "label": label,
                "locator": _theorem_locator(kind, raw_label, name),
                "statement": statement[:2500],
                "context": "",
                "hypothesis_terms": extract_hypothesis_terms(statement),
                "confidence": "medium" if (raw_label or name) else "low",
            }
        )
    return theorems


def locator_kind_for_statement(kind: str) -> str:
    return {
        "theorem": "theorem",
        "proposition": "proposition",
        "lemma": "lemma",
        "corollary": "corollary",
        "definition": "definition",
        "remark": "section",
    }.get(kind, "section")


def locator_for_source(source: str) -> dict:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return {"kind": "url", "value": source}
    return {"kind": "unknown", "value": Path(source).name}


def extract_hypothesis_terms(statement: str) -> list[str]:
    lowered = statement.lower()
    terms = [
        phrase
        for phrase in HYPOTHESIS_PHRASES
        if phrase in lowered
    ]
    return sorted(dict.fromkeys(terms))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_statement(text: str) -> str:
    text = normalize_text(text)
    return text.strip(" ;")


def clean_label(text: str) -> str:
    return normalize_text(text).strip(" .:;()") or "unlabeled"


HYPOTHESIS_PHRASES = [
    "base change",
    "base field",
    "coherent",
    "cubic fourfold",
    "flat",
    "noetherian",
    "proper",
    "projective",
    "smooth",
    "special",
    "very general",
]
