from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .io import read_text, read_yaml
from .live_search import LiveSearchResponse, live_literature_search
from .models import LiteratureReviewReport


MAX_NOTE_CHARS = 180


@dataclass(frozen=True)
class LocalReference:
    title: str
    authors: list[str]
    locator_kind: str
    locator_value: str
    topic: str
    source_path: str
    raw_line: str


def discover_local_literature(
    *,
    project_dir: Path,
    locked_claim_path: Path,
    hypothesis_ledger_path: Path | None = None,
    max_references: int = 8,
    query: str | None = None,
) -> dict:
    """Build a schema-valid Literature Review report from local project files.

    This is intentionally conservative. It discovers candidate references from
    local notes and example banks, but never marks a source as source_backed or
    misapplied.
    """

    claim = read_yaml(locked_claim_path)
    ledger = read_yaml(hypothesis_ledger_path) if hypothesis_ledger_path and hypothesis_ledger_path.exists() else {}
    claim_text = " ".join(
        str(part)
        for part in [
            query or "",
            claim.get("raw_text", ""),
            claim.get("normalized_statement", ""),
            claim.get("conclusion", ""),
            " ".join(claim.get("objects", [])),
            " ".join(item.get("name", "") for item in claim.get("hypotheses", [])),
            " ".join(ledger.get("missing_or_unclear", [])),
        ]
        if part
    )
    terms = query_terms(claim_text)
    references = collect_local_references(project_dir)
    ranked = rank_references(references, terms)
    selected = ranked[:max_references]
    candidate_references = [
        reference_to_literature_item(reference, index=index)
        for index, reference in enumerate(selected, start=1)
    ]
    if not candidate_references:
        candidate_references = [not_found_item(terms)]

    known_examples = discover_known_examples(project_dir, terms)
    known_counterexamples = discover_counterexample_families(project_dir, terms)
    notes = [
        "Local Literature Discovery reads project notes and example banks only.",
        "Discovered references are candidates; Referencing must check exact locators and hypotheses.",
    ]
    if selected:
        topics = sorted({reference.topic for reference in selected if reference.topic})
        if topics:
            notes.append(f"Matched local topic buckets: {', '.join(topics[:5])}.")
    if query:
        notes.append("User query was used as an additional ranking hint.")

    report = {
        "claim_id": claim["id"],
        "query_summary": query_summary(terms, selected),
        "candidate_references": candidate_references,
        "known_examples": known_examples,
        "known_counterexample_families": known_counterexamples,
        "notes": notes,
    }
    LiteratureReviewReport.model_validate(report)
    return report


def discover_literature(
    *,
    project_dir: Path,
    locked_claim_path: Path,
    hypothesis_ledger_path: Path | None = None,
    max_references: int = 8,
    query: str | None = None,
    mode: str = "local",
    providers: list[str] | None = None,
    ads_token_env: str = "ADS_API_TOKEN",
) -> dict:
    local_report = discover_local_literature(
        project_dir=project_dir,
        locked_claim_path=locked_claim_path,
        hypothesis_ledger_path=hypothesis_ledger_path,
        max_references=max_references,
        query=query,
    )
    if mode == "local":
        return local_report
    if mode != "live":
        raise ValueError(f"unsupported literature discovery mode: {mode}")

    live_query = query or local_report["query_summary"]
    live_responses = live_literature_search(
        live_query,
        providers=providers or ["arxiv", "web", "ads"],
        max_results=max_references,
        ads_token_env=ads_token_env,
    )
    live_items = live_responses_to_literature_items(live_responses)
    merged = {
        **local_report,
        "query_summary": (
            f"{local_report['query_summary']} Live providers queried: "
            f"{', '.join(response.provider for response in live_responses)}."
        ),
        "candidate_references": merge_literature_items(
            live_items + local_report["candidate_references"],
            max_references,
        ),
        "notes": local_report["notes"] + live_response_notes(live_responses),
    }
    LiteratureReviewReport.model_validate(merged)
    return merged


def live_responses_to_literature_items(responses: list[LiveSearchResponse]) -> list[dict]:
    items = []
    index = 1
    for response in responses:
        for result in response.results:
            items.append(
                {
                    "id": f"live_{response.provider}_{index:03d}",
                    "title": result.title,
                    "authors": result.authors,
                    "locator": {
                        "kind": result.locator_kind,
                        "value": result.locator_value,
                    },
                    "relevance": result.relevance,
                    "status": result.status,
                    "why_relevant": result.why_relevant,
                    "found_by": "web_search",
                }
            )
            index += 1
    return items


def merge_literature_items(items: list[dict], limit: int) -> list[dict]:
    deduped = []
    seen = set()
    for item in items:
        key = (item.get("title", "").lower(), item.get("locator", {}).get("value", "").lower())
        if key in seen:
            continue
        seen.add(key)
        item = dict(item)
        item["id"] = f"candidate_ref_{len(deduped) + 1:03d}"
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def live_response_notes(responses: list[LiveSearchResponse]) -> list[str]:
    notes = ["Live Literature Discovery queried network providers; returned records remain candidates."]
    for response in responses:
        notes.extend(response.notes)
        notes.append(f"{response.provider} returned {len(response.results)} candidate record(s).")
    return notes


def query_terms(text: str) -> set[str]:
    terms = {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", text.lower())
        if token not in STOPWORDS
    }
    aliases = set()
    if {"cubic", "fourfold", "fourfolds"} & terms:
        aliases.update({"hodge", "period", "torelli", "rationality", "special"})
    if "hodge" in terms:
        aliases.update({"mirror", "atom", "atoms", "kkp", "kkpy"})
    if {"base", "change"} <= terms or "flat" in terms:
        aliases.update({"coherent", "proper", "semicontinuity", "stacks"})
    if "poincare" in terms or "fourier" in terms:
        aliases.update({"pic", "semicontinuity", "cohomology"})
    return terms | aliases


def collect_local_references(project_dir: Path) -> list[LocalReference]:
    references: list[LocalReference] = []
    for path in [project_dir / "core_papers.md", project_dir / "reference_index.md"]:
        if path.exists():
            references.extend(parse_markdown_references(path, project_dir))
    return dedupe_references(references)


def parse_markdown_references(path: Path, project_dir: Path) -> list[LocalReference]:
    current_topic = ""
    current_title = ""
    current_authors: list[str] = []
    current_url = ""
    references: list[LocalReference] = []
    for raw_line in read_text(path).splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            if current_title:
                references.append(
                    LocalReference(
                        title=current_title,
                        authors=current_authors,
                        locator_kind="url" if current_url else "unknown",
                        locator_value=current_url or "to_be_filled",
                        topic=current_topic,
                        source_path=str(path.relative_to(project_dir.parent)),
                        raw_line=current_title,
                    )
                )
            current_topic = clean_inline(line.removeprefix("### "))
            current_title = ""
            current_authors = []
            current_url = ""
            continue
        if line.startswith("- **Title:**"):
            current_title = clean_inline(line.split(":", 1)[1])
            continue
        if line.startswith("- **Authors:**") or line.startswith("- **Author:**"):
            current_authors = split_authors(clean_inline(line.split(":", 1)[1]))
            continue
        if line.startswith("- **URL:**"):
            current_url = clean_inline(line.split(":", 1)[1])
            continue
        if line.startswith("- ") and is_reference_like(line):
            title, authors = parse_reference_bullet(line.removeprefix("- "))
            references.append(
                LocalReference(
                    title=title,
                    authors=authors,
                    locator_kind=infer_locator_kind(title),
                    locator_value=infer_locator_value(title),
                    topic=current_topic,
                    source_path=str(path.relative_to(project_dir.parent)),
                    raw_line=clean_inline(line.removeprefix("- ")),
                )
            )
    if current_title:
        references.append(
            LocalReference(
                title=current_title,
                authors=current_authors,
                locator_kind="url" if current_url else "unknown",
                locator_value=current_url or "to_be_filled",
                topic=current_topic,
                source_path=str(path.relative_to(project_dir.parent)),
                raw_line=current_title,
            )
        )
    return references


def is_reference_like(line: str) -> bool:
    text = line.lower()
    if line.startswith("- `"):
        return False
    if text.startswith("- use ") or text.startswith("- every ") or text.startswith("- a "):
        return False
    return any(marker in text for marker in REFERENCE_MARKERS)


def parse_reference_bullet(text: str) -> tuple[str, list[str]]:
    clean = clean_inline(text).rstrip(".")
    if ", *" in clean:
        author_part, title_part = clean.split(", *", 1)
        return title_part.strip("* "), split_authors(author_part)
    if " on " in clean and clean[0].isupper():
        author_part, title_part = clean.split(" on ", 1)
        return f"{author_part} on {title_part}", split_authors(author_part)
    return clean, []


def split_authors(text: str) -> list[str]:
    parts = re.split(r",| and ", text)
    return [part.strip() for part in parts if part.strip()]


def clean_inline(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "").strip()).strip()


def infer_locator_kind(title: str) -> str:
    lowered = title.lower()
    if "arxiv.org" in lowered or re.search(r"\b\d{4}\.\d{4,5}\b", lowered):
        return "arxiv_id"
    if "stacks project" in lowered or "tag " in lowered:
        return "url"
    return "unknown"


def infer_locator_value(title: str) -> str:
    arxiv_match = re.search(r"\b\d{4}\.\d{4,5}\b", title)
    if arxiv_match:
        return arxiv_match.group(0)
    tag_match = re.search(r"tag\s+([0-9A-Z]+)", title, flags=re.IGNORECASE)
    if tag_match:
        return f"Stacks Project tag {tag_match.group(1)}"
    return "to_be_filled"


def rank_references(references: list[LocalReference], terms: set[str]) -> list[LocalReference]:
    scored = []
    for index, reference in enumerate(references):
        haystack = " ".join([reference.title, reference.topic, reference.raw_line]).lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((score, -index, reference))
    scored.sort(reverse=True)
    return [reference for _, _, reference in scored]


def reference_to_literature_item(reference: LocalReference, *, index: int) -> dict:
    return {
        "id": f"local_ref_{index:03d}",
        "title": reference.title[:MAX_NOTE_CHARS],
        "authors": reference.authors,
        "locator": {
            "kind": reference.locator_kind,
            "value": reference.locator_value,
        },
        "relevance": infer_relevance(reference),
        "status": "candidate" if reference.locator_kind != "unknown" else "unverified",
        "why_relevant": why_relevant(reference),
        "found_by": "local_notes",
    }


def infer_relevance(reference: LocalReference) -> str:
    text = " ".join([reference.title, reference.topic, reference.raw_line]).lower()
    if "counterexample" in text or "patholog" in text:
        return "counterexample_source"
    if "example" in text or "cubic" in text or "p^1" in text:
        return "example_source"
    if "theorem" in text or "lemma" in text or "stacks project" in text:
        return "possible_theorem_source"
    if "core" in reference.topic.lower() or "kkp" in text or "kkpy" in text:
        return "core"
    return "background"


def why_relevant(reference: LocalReference) -> str:
    topic = reference.topic or "project local notes"
    return (
        f"Matched local reference bucket '{topic}' from {reference.source_path}. "
        "This is a discovery candidate only."
    )[:MAX_NOTE_CHARS]


def not_found_item(terms: set[str]) -> dict:
    hint = ", ".join(sorted(terms)[:8]) or "no usable query terms"
    return {
        "id": "local_ref_not_found",
        "title": "No matching local literature candidate found",
        "authors": [],
        "locator": {"kind": "unknown", "value": "to_be_filled"},
        "relevance": "background",
        "status": "not_found",
        "why_relevant": f"Local notes did not match query terms: {hint}.",
        "found_by": "local_notes",
    }


def discover_known_examples(project_dir: Path, terms: set[str]) -> list[str]:
    bank_path = project_dir / "example_bank.yaml"
    if not bank_path.exists():
        return []
    bank = read_yaml(bank_path)
    matches = []
    for example in bank.get("examples", []):
        text = " ".join(
            [
                example.get("id", ""),
                example.get("name", ""),
                example.get("role", ""),
                " ".join(example.get("what_it_tests", [])),
                example.get("notes", ""),
            ]
        ).lower()
        if any(term in text for term in terms):
            matches.append(example.get("name") or example.get("id"))
    return sorted(dict.fromkeys(matches))[:8]


def discover_counterexample_families(project_dir: Path, terms: set[str]) -> list[str]:
    index_path = project_dir / "reference_index.md"
    if not index_path.exists():
        return []
    families = []
    in_examples = False
    for raw_line in read_text(index_path).splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            in_examples = "example" in line.lower() or "patholog" in line.lower()
            continue
        if in_examples and line.startswith("- "):
            clean = clean_inline(line.removeprefix("- ").rstrip(";"))
            text = clean.lower()
            if any(term in text for term in terms) or not terms:
                families.append(clean)
    return sorted(dict.fromkeys(families))[:8]


def query_summary(terms: set[str], selected: list[LocalReference]) -> str:
    term_text = ", ".join(sorted(terms)[:12]) or "no query terms"
    if selected:
        return f"Local literature discovery matched {len(selected)} candidate reference(s) for: {term_text}."
    return f"Local literature discovery found no matching local reference for: {term_text}."


def dedupe_references(references: list[LocalReference]) -> list[LocalReference]:
    seen: set[tuple[str, str]] = set()
    deduped = []
    for reference in references:
        key = (reference.title.lower(), reference.locator_value.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(reference)
    return deduped


REFERENCE_MARKERS = {
    "arxiv",
    "beauville",
    "bittner",
    "charles",
    "clemens",
    "coates",
    "deligne",
    "gromov",
    "griffiths",
    "hartshorne",
    "hassett",
    "hertling",
    "huybrechts",
    "iritani",
    "kontsevich",
    "kuznetsov",
    "looijenga",
    "sabbah",
    "saito",
    "stacks project",
    "voisin",
}

STOPWORDS = {
    "and",
    "are",
    "but",
    "for",
    "from",
    "into",
    "not",
    "only",
    "probably",
    "should",
    "that",
    "the",
    "this",
    "with",
}
