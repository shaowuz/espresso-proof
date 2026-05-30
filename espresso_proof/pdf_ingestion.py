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


def extract_theorems(text: str, *, source_ref_id: str) -> list[dict]:
    normalized = normalize_text(text)
    pattern = re.compile(
        r"\b(Theorem|Proposition|Lemma|Corollary|Definition|Remark)\s*"
        r"(?:\(([^)]+)\)|([A-Za-z0-9_.-]+))?\s*[:.]?\s+"
        r"(.+?)(?=\b(?:Theorem|Proposition|Lemma|Corollary|Definition|Remark)\s*(?:\([^)]+\)|[A-Za-z0-9_.-]+)?\s*[:.]|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    theorems = []
    for index, match in enumerate(pattern.finditer(normalized), start=1):
        kind = match.group(1).lower()
        label = clean_label(match.group(2) or match.group(3) or str(index))
        statement = clean_statement(fallback_statement_text(match.group(4)))
        if not statement:
            continue
        locator_kind = locator_kind_for_statement(kind)
        theorem_id = f"thm_{index:03d}"
        theorems.append(
            {
                "id": theorem_id,
                "source_ref_id": source_ref_id,
                "kind": kind,
                "label": label,
                "locator": {
                    "kind": locator_kind,
                    "value": f"{kind.title()} {label}",
                },
                "statement": statement[:2500],
                "context": "",
                "hypothesis_terms": extract_hypothesis_terms(statement),
                "confidence": "medium" if label else "low",
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
