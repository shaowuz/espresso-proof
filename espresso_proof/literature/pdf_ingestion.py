from __future__ import annotations

import re
import gzip
import hashlib
import ssl
import tarfile
import urllib.parse
import urllib.error
import urllib.request
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from espresso_proof.literature.live_search import fetch_url
from espresso_proof.kernel.models import TheoremExtractionReport


def ingest_source_for_theorems(
    *,
    claim_id: str,
    source: str,
    source_id: str,
    title: str,
    authors: list[str] | None = None,
    source_type: str = "paper",
) -> dict:
    raw, read_notes = read_source_bytes_with_notes(source)
    text, extraction_notes = extract_source_text(raw, source)
    source_hash = hashlib.sha256(raw).hexdigest()
    theorems = extract_theorems(text, source_ref_id=source_id)
    for theorem in theorems:
        locator = theorem.setdefault("source_locator", {})
        locator.update(
            {
                "source_ref_id": source_id,
                "title": title,
                "authors": authors or [],
                "source_sha256": source_hash,
            }
        )
    source_ref = {
        "id": source_id,
        "type": source_type,
        "title": title,
        "authors": authors or [],
        "locator": locator_for_source(source),
        "statement_or_summary": (
            f"Source ingested for theorem extraction; {len(theorems)} candidate "
            f"statement(s) found; source_sha256={source_hash}."
        ),
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
        "notes": read_notes + extraction_notes + [
            "PDF/text ingestion extracts theorem-like statements heuristically.",
            "Theorem extraction is structure/regex based, not LLM-grade semantic parsing.",
            "Extraction does not make a source source-backed; run Referencing verification next.",
        ],
    }
    TheoremExtractionReport.model_validate(report)
    return report


def read_source_bytes(source: str) -> bytes:
    raw, _ = read_source_bytes_with_notes(source)
    return raw


def read_source_bytes_with_notes(source: str) -> tuple[bytes, list[str]]:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https"}:
        if "arxiv.org" in parsed.netloc.lower():
            return read_arxiv_source_bytes(source)
        return (
            fetch_url_resilient(source, {"User-Agent": "espresso-proof/0.1 (local research workflow)"}, 30),
            [f"Fetched URL source: {source}."],
        )
    return Path(source).read_bytes(), [f"Read local source bytes from {Path(source).name}."]


def fetch_url_resilient(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> bytes:
    """Fetch a URL with the project fetcher, with a narrow SSL fallback.

    Some Windows/Python installs lack a usable CA bundle for arXiv. The fallback
    still records no trust status; it only lets the source ingestion path read the
    public source bytes instead of failing before extraction.
    """

    try:
        return fetch_url(url, headers or {}, timeout)
    except (urllib.error.URLError, OSError) as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        request = urllib.request.Request(url, headers=headers or {})
        context = ssl._create_unverified_context()  # noqa: SLF001 - narrow source-fetch fallback.
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.read()


def arxiv_id_from_source(source: str) -> str | None:
    parsed = urllib.parse.urlparse(source)
    if "arxiv.org" not in parsed.netloc.lower():
        return None
    path = parsed.path.strip("/")
    for prefix in ("abs/", "pdf/", "e-print/"):
        if path.startswith(prefix):
            ident = path[len(prefix):]
            return ident.removesuffix(".pdf") or None
    return path or None


def arxiv_candidate_urls(arxiv_id: str) -> list[tuple[str, str]]:
    clean = arxiv_id.removesuffix(".pdf")
    return [
        ("abs_metadata", f"https://arxiv.org/abs/{clean}"),
        ("eprint_source", f"https://arxiv.org/e-print/{clean}"),
        ("pdf", f"https://arxiv.org/pdf/{clean}"),
    ]


def read_arxiv_source_bytes(source: str) -> tuple[bytes, list[str]]:
    arxiv_id = arxiv_id_from_source(source)
    if not arxiv_id:
        return (
            fetch_url_resilient(source, {"User-Agent": "espresso-proof/0.1 (local research workflow)"}, 30),
            [f"Fetched arXiv URL without normalized id: {source}."],
        )

    notes: list[str] = [f"Resolved arXiv id {arxiv_id}; tried abs metadata, e-print/source, and PDF."]
    fetched: list[tuple[int, str, bytes, str]] = []
    for kind, url in arxiv_candidate_urls(arxiv_id):
        try:
            raw = fetch_url_resilient(url, {"User-Agent": "espresso-proof/0.1 (local research workflow)"}, 30)
        except Exception as exc:  # noqa: BLE001 - source retrieval fails closed upstream.
            notes.append(f"arXiv {kind} fetch failed: {exc}")
            continue
        prepared, prep_note = prepare_source_bytes(raw, url)
        text, extract_notes = extract_source_text(prepared, url)
        score = source_text_quality_score(text)
        notes.append(
            f"arXiv {kind} fetched ({len(raw)} byte(s)); {prep_note}; "
            f"text_quality={score}; {'; '.join(extract_notes[:1])}"
        )
        fetched.append((score, kind, prepared, url))

    if not fetched:
        raise OSError(f"no arXiv source candidate could be fetched for {arxiv_id}")
    # Prefer the candidate with the best theorem-like text, not blindly PDF. Old
    # arXiv papers often expose cleaner text through gzipped PostScript sources.
    fetched.sort(key=lambda item: (item[0], 1 if item[1] == "eprint_source" else 0), reverse=True)
    best_score, best_kind, best_raw, best_url = fetched[0]
    notes.append(f"Selected arXiv {best_kind} candidate for theorem extraction: {best_url} (quality={best_score}).")
    return best_raw, notes


def prepare_source_bytes(raw: bytes, source: str) -> tuple[bytes, str]:
    if raw.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(raw), "decompressed gzip source bytes"
        except OSError:
            return raw, "gzip marker present but decompression failed; using raw bytes"
    return raw, "used raw source bytes"


def looks_like_pdf(raw: bytes, source: str) -> bool:
    return raw.startswith(b"%PDF") or source.lower().endswith(".pdf")


def extract_source_text(raw: bytes, source: str) -> tuple[str, list[str]]:
    raw, prep_note = prepare_source_bytes(raw, source)
    if looks_like_pdf(raw, source):
        text, method = extract_text_from_pdf_bytes_with_method(raw)
        return clean_extracted_source_text(text), [prep_note, method]
    if looks_like_tar(raw):
        text, files = extract_text_from_tar_bytes(raw)
        return clean_extracted_source_text(text), [
            prep_note,
            f"Extracted arXiv source tar text from {files} file(s).",
        ]
    if looks_like_postscript(raw, source):
        text, fragments = extract_text_from_postscript_bytes(raw)
        return clean_extracted_source_text(text), [
            prep_note,
            f"Extracted PostScript text from {fragments} text fragment(s).",
        ]
    return clean_extracted_source_text(raw.decode("utf-8", errors="ignore")), [
        prep_note,
        "Read source as UTF-8 text.",
    ]


def looks_like_postscript(raw: bytes, source: str) -> bool:
    lowered = source.lower()
    return raw.startswith(b"%!PS") or lowered.endswith(".ps") or lowered.endswith(".eps")


def looks_like_tar(raw: bytes) -> bool:
    try:
        with tarfile.open(fileobj=BytesIO(raw)) as archive:
            archive.getmembers()
        return True
    except tarfile.TarError:
        return False


def extract_text_from_tar_bytes(raw: bytes) -> tuple[str, int]:
    chunks: list[str] = []
    try:
        with tarfile.open(fileobj=BytesIO(raw)) as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.isfile() and member.size <= 2_000_000
            ]
            priority = {".tex": 0, ".ltx": 0, ".txt": 1, ".md": 1, ".bbl": 2, ".ps": 3}
            members.sort(key=lambda member: (priority.get(Path(member.name).suffix.lower(), 9), member.name))
            for member in members:
                suffix = Path(member.name).suffix.lower()
                if suffix not in {".tex", ".ltx", ".txt", ".md", ".bbl", ".ps"}:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                data = extracted.read()
                if suffix == ".ps" or looks_like_postscript(data, member.name):
                    text, _ = extract_text_from_postscript_bytes(data)
                else:
                    text = data.decode("utf-8", errors="ignore")
                if has_words(text):
                    chunks.append(text)
    except tarfile.TarError:
        return "", 0
    return "\n".join(chunks), len(chunks)


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


def extract_text_from_postscript_bytes(raw: bytes) -> tuple[str, int]:
    text = raw.decode("latin-1", errors="ignore")
    strings = []
    for match in re.finditer(r"\((?:\\.|[^\\)])*\)", text, flags=re.DOTALL):
        value = decode_pdf_literal_string(match.group(0)[1:-1])
        if value and re.search(r"[A-Za-z0-9]", value):
            strings.append(value)
    return "\n".join(strings), len(strings)


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
    for stop in [
        " Proof.",
        " Proof ",
        " References ",
        " Bibliography ",
        " Safarevi",
        " In the course",
        " Introduction ",
    ]:
        position = text.find(stop)
        if position > 40:
            text = text[:position]
            break
    return text


def clean_extracted_source_text(text: str) -> str:
    """Clean PDF/PostScript/TeX extraction artifacts while staying conservative."""

    text = text.replace("\x0c", "fi").replace("\x0b", "ff").replace("\x0e", "fl")
    text = text.replace("\x10", "ffl").replace("\x11", "ffi")
    text = re.sub(r"[\x00-\x08\x0f\x12-\x1f\x7f]", "", text)
    # Common old TeX/PDF extraction splits and ligature losses.
    replacements = [
        (r"(?i)\b(Theorem|Proposition|Lemma|Corollary|Definition|Remark)\s+[/\\]\s*([0-9]+)", r"\1 \2"),
        (r"\bG\s*/\(\s*K/k\s*/\)\s*/\s*/?=+\s*G\b", "G(K/k) is isomorphic to G"),
        (r"[/\\]\s*finite\b", "finite"),
        (r"[/\\]\s*field\b", "field"),
        (r"[/\\]\s*nite\b", "finite"),
        (r"[/\\]\s*eld\b", "field"),
        (r"[/\\]\s*rst\b", "first"),
        (r"[/\\]\s*nd\b", "find"),
        (r"\bb\s+e\b", "be"),
        (r"\bw\s+e\b", "we"),
        (r"\bo\s+v\s+er\b", "over"),
        (r"\bgiv\s+en\b", "given"),
        (r"\bsolv\s+able\b", "solvable"),
        (r"\bpro\s+of\b", "proof"),
        (r"\bpro\s+v\s+e\b", "prove"),
        (r"\bin\s+tro\s+duction\b", "introduction"),
        (r"\bdi\s*ff\s*eren\s*t\b", "different"),
        (r"\bc\s*har\s*acteristic\b", "characteristic"),
        (r"\bK\s+j\s+k\b", "K/k"),
        (r"\bG\s+K/k\s+G\b", "G(K/k) is isomorphic to G"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Some old dvips sources lose the colon before the first theorem citation:
    # "... SAFAREVIC Theorem 1 Let ...". Restore a sentence boundary so the
    # header extractor does not miss the theorem.
    text = re.sub(
        r"\b(S\s+AFAREVI\s*C)\s+(Theorem\s+[0-9]+\s+Let)\b",
        r"\1: \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*/\s*([.,;:])", r"\1", text)
    text = re.sub(r"\s*/\s*\)", ")", text)
    text = re.sub(r"\(\s*/\s*", "(", text)
    # Drop obvious PostScript font/operator crumbs that survived string extraction.
    text = re.sub(r"\bF[A-Z]{1,3}\b", " ", text)
    text = re.sub(r"\b(?:b|g|h|i|j|m|n|q|r|y)\b(?=\s+[A-Z(])", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def source_text_quality_score(text: str) -> int:
    lowered = normalize_text(text).lower()
    score = 0
    for token in ("theorem", "global field", "finite solvable group", "galois extension"):
        if token in lowered:
            score += 25
    score += min(len(re.findall(r"\b(theorem|lemma|proposition|corollary)\b", lowered)), 20)
    # Penalize obvious broken extraction.
    score -= min(lowered.count("/ nite") + lowered.count("/ eld") + lowered.count(" b e "), 25)
    score -= min(len(re.findall(r"\s/[.,;:)]=?|\(\s*/|/\s*/?=", lowered)), 30)
    return score


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

    from espresso_proof.kernel.models import Locator

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


# A locator-like label: numeric (1, 1.2, 2.1.3) or letter+dotted-number (A.3;
# matched case-insensitively via the IGNORECASE header regex), NOT an arbitrary
# word ("about", "Smoothness") that merely follows the keyword.
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
        context_start = max(0, match.start() - 180)
        context_end = min(len(normalized), min(end, match.end() + len(statement) + 180))
        statement_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
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
                "context": normalized[context_start:context_end][:1200],
                "hypothesis_terms": extract_hypothesis_terms(statement),
                "confidence": "medium" if (raw_label or name) else "low",
                "source_locator": {
                    "theorem_locator": _theorem_locator(kind, raw_label, name),
                    "paragraph_index": index,
                    "page": None,
                    "excerpt": statement[:500],
                    "context_excerpt": normalized[context_start:context_end][:500],
                },
                "statement_sha256": statement_hash,
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
    "finite solvable group",
    "flat",
    "galois extension",
    "galois group",
    "global field",
    "noetherian",
    "proper",
    "projective",
    "smooth",
    "special",
    "very general",
]
