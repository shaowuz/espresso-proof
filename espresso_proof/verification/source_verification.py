from __future__ import annotations

import hashlib
import re
from pathlib import Path

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.claim.hypothesis_matching import match_hypothesis_semantically
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import SourceGroundingReport, TheoremExtractionReport


def verify_source_grounding_from_theorems(
    *,
    run_dir: Path,
    theorem_extraction_path: Path,
) -> dict:
    claim = read_yaml(run_dir / "locked_claim.yaml")
    ledger = read_yaml(run_dir / "hypothesis_ledger.yaml")
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    extraction = TheoremExtractionReport.model_validate(read_yaml(theorem_extraction_path))
    source_by_id = {source.id: source for source in extraction.sources}
    existing_refs = {ref["id"]: ref for ref in source_grounding.get("source_refs", [])}

    for step in source_grounding.get("steps", []):
        required_statement = str(step.get("needed_theorem") or "")
        theorem = best_theorem_for_step(step, extraction.theorems)
        if theorem is None:
            step["status"] = "candidate"
            step["source_refs"] = []
            step["hypothesis_match"] = {
                "matches": "unclear",
                "mismatches": ["no exact theorem statement matched this proof step"],
                "matched": [],
            }
            step["note"] = (
                "No extracted exact theorem/corollary statement covered this proof "
                "step; source conclusion does not cover the requested theorem, so "
                "leaving it candidate-only."
            )
            continue
        source = source_by_id[theorem.source_ref_id]
        hypothesis_match = check_hypothesis_applicability(
            theorem.statement,
            claim.get("hypotheses", []),
            ledger.get("missing_or_unclear", []),
            required_statement=required_statement,
        )
        exact_locator = theorem.locator.is_exact
        # Anti-plagiarism: source_backed requires attribution (author + title), in
        # addition to an exact locator and a hypothesis match. Fail closed to
        # candidate when attribution is incomplete (the SourceRef model validator
        # is the hard boundary; this is the friendly early downgrade).
        attributed = bool([a for a in source.authors if str(a).strip()]) and bool(
            str(source.title).strip()
        )
        conclusion_match = conclusion_compatible(required_statement, theorem.statement)
        status = (
            "source_backed"
            if exact_locator
            and hypothesis_match["matches"] is True
            and attributed
            and conclusion_match
            else "candidate"
        )
        ref_id = f"verified_{theorem.id}"
        existing_refs[ref_id] = {
            "id": ref_id,
            "type": source.type.value if hasattr(source.type, "value") else source.type,
            "title": source.title,
            "authors": source.authors,
            "locator": theorem.locator.model_dump(mode="json"),
            "statement_or_summary": theorem.statement,
            "hypothesis_match": hypothesis_match,
            "confidence": "high" if status == "source_backed" else "medium",
            "status": status,
            # Bind the quoted statement to the extracted theorem (the
            # validator re-checks both the self-hash and membership in the
            # theorem_extraction artifact).
            "statement_sha256": hashlib.sha256(theorem.statement.encode("utf-8")).hexdigest(),
        }
        step["status"] = status
        step["source_refs"] = [ref_id]
        step["hypothesis_match"] = hypothesis_match
        if status == "source_backed":
            matched_reasons = hypothesis_match.get("matched", [])
            why = ("; ".join(matched_reasons)) if matched_reasons else (
                "required hypotheses were found in the extracted statement"
            )
            step["note"] = (
                f"Exact {theorem.kind} locator {theorem.label} matched; {why}."
            )
        else:
            if not attributed:
                reason = "missing attribution (author/title) for the cited source"
            elif not conclusion_match:
                reason = "source conclusion does not cover the proof step's requested theorem"
            elif not exact_locator:
                reason = "no exact locator"
            else:
                reason = ", ".join(hypothesis_match.get("mismatches", [])) or "unclear hypothesis match"
            step["note"] = f"Candidate theorem found, but reference-support verification is blocked by {reason}."

    source_grounding["source_refs"] = list(existing_refs.values())
    SourceGroundingReport.model_validate(source_grounding)
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)
    return source_grounding


def ground_run(*, run_dir: Path, theorem_extraction_path: Path) -> dict:
    """Verify source grounding from extracted theorems, run the theorem
    applicability audit, register it, and re-validate the run (fail closed).

    Single reusable entry point shared by the ``verify-sources`` CLI and the
    orchestrated ``--ground-source`` path so there is one grounding pipeline.
    """

    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability
    from espresso_proof.kernel.validators import validate_run

    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    verify_source_grounding_from_theorems(
        run_dir=run_dir, theorem_extraction_path=theorem_extraction_path
    )
    audit_theorem_applicability(run_dir)  # writes theorem_applicability.yaml
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = (
        "theorem_applicability.yaml"
    )
    write_yaml(manifest_path, manifest)
    from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures

    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)
    return read_yaml(run_dir / "source_grounding.yaml")


def conclusion_compatible(required_statement: str, theorem_statement: str) -> bool:
    """Fail closed unless the source statement covers the requested step terms."""

    if _is_subgroup_equality_request(required_statement) and not _states_subgroup_equality(
        theorem_statement
    ):
        return False
    # There used to be a widening shortcut here: four hardcoded theorem shapes
    # (Lagrange, order-divides, prime-order-generator, cyclic-by-definition) that
    # returned True on a keyword pattern, so those four statements could be
    # grounded on evidence no other statement could use. Removed. Every step now
    # meets the same requirement — the cited statement must contain the step's
    # own significant terms — which is stricter, not looser, and identical for
    # every area of mathematics.
    required_terms = significant_terms(required_statement)
    if not required_terms:
        return False
    return all(term_present(term, theorem_statement) for term in required_terms)


def best_theorem_for_step(step: dict, theorems) -> object | None:
    query_terms = significant_terms(step.get("needed_theorem", ""))
    if not query_terms:
        return None
    best = None
    best_score = 0
    for theorem in theorems:
        if not theorem.locator.is_exact:
            continue
        if getattr(theorem, "kind", "") == "remark":
            continue
        if not conclusion_compatible(step.get("needed_theorem", ""), theorem.statement):
            continue
        haystack = normalize_math_text(
            " ".join([theorem.label, theorem.statement, " ".join(theorem.hypothesis_terms)])
        )
        score = sum(1 for term in query_terms if term_present(term, haystack))
        if getattr(theorem, "kind", "") in {"theorem", "corollary"}:
            score += 2
        if score > best_score:
            best = theorem
            best_score = score
    return best if best_score >= 2 else None


def check_hypothesis_applicability(
    statement: str,
    claim_hypotheses: list[dict],
    missing_or_unclear: list[str],
    *,
    required_statement: str | None = None,
) -> dict:
    """Decide whether a cited source statement covers the required claim
    hypotheses.

    A required hypothesis is covered when either:

    1. a conservative, explicit semantic-equivalence rule recognises the same
       statement in different notation (e.g. ``"group of order p^2"`` vs
       ``"|G| = p^2"``); or
    2. every significant whole word of the hypothesis appears in the source
       statement (the original, conservative token coverage, now whole-word
       only so ``finite`` cannot match inside ``infinite``).

    Returns ``matches: True`` with a list of ``matched`` human-readable reasons
    only when ALL required hypotheses are covered; otherwise ``matches:
    "unclear"`` with mismatches. Coverage here gates hypothesis compatibility
    only — it never by itself earns ``source_backed`` (the exact locator and
    attribution gates remain in :func:`verify_source_grounding_from_theorems`
    and the :class:`SourceRef` validator).
    """

    _ = missing_or_unclear
    matched_reasons: list[str] = []
    mismatches: list[str] = []
    unclear = False
    for hypothesis in claim_hypotheses:
        if hypothesis.get("status") not in {"explicit", "inferred"}:
            continue
        name = (hypothesis.get("name") or "").strip()
        if not name:
            continue
        if required_statement and not _hypothesis_relevant_to_step(
            name, required_statement, statement
        ):
            continue
        # 1) Conservative semantic equivalence (explicit, test-backed rules).
        reason = match_hypothesis_semantically(name, statement)
        if reason is not None:
            matched_reasons.append(reason)
            continue
        if _unsupported_symbolic_order_hypothesis(name):
            unclear = True
            mismatches.append(
                f"required hypothesis has no checkable terms or semantic match: {name}"
            )
            continue
        # 2) Fall back to conservative whole-word token coverage.
        terms = list(dict.fromkeys(significant_terms(name) + expanded_hypothesis_terms(name)))
        missing_terms = [term for term in terms if not term_present(term, statement)]
        if missing_terms:
            unclear = True
            mismatches.extend(
                f"required hypothesis term not found in source statement: {term}"
                for term in missing_terms
            )
        elif terms:
            matched_reasons.append(f'"{name}" terms found in the source statement')
        else:
            unclear = True
            mismatches.append(
                f"required hypothesis has no checkable terms or semantic match: {name}"
            )
    if unclear:
        return {"matches": "unclear", "mismatches": mismatches, "matched": matched_reasons}
    return {"matches": True, "mismatches": [], "matched": matched_reasons}


def term_present(term: str, statement: str) -> bool:
    """Whole-word coverage of ``term`` in ``statement`` (case-insensitive).

    Whole-word matching is deliberate: it keeps ``finite`` from matching inside
    ``infinite`` and ``singular`` from matching inside other words. A NEGATED
    occurrence ("non-proper", "non proper", "not proper") never counts as
    Coverage: a theorem about non-proper varieties cannot support a
    properness hypothesis. Falls back to the explicit :func:`term_covered`
    notation aliases. A token that fails to match is treated as missing, which
    downgrades to a candidate — the safe direction.
    """

    lowered = normalize_math_text(statement)
    if _unnegated_word_present(term, lowered):
        return True
    for variant in _inflections(term):
        if _unnegated_word_present(variant, lowered):
            return True
    return term_covered(term, lowered)


def _inflections(term: str) -> tuple[str, ...]:
    """English inflections of one token: "divides" also reads as "divide".

    The gate used to bridge this with four hardcoded theorem patterns — a step
    saying "|H| divides |G|" was matched to a source saying "must divide" by a
    rule that named Lagrange. The mismatch was never mathematical, it was
    morphological, and every area of mathematics writes the same way. Handling
    the inflection instead is subject-independent and keeps the requirement
    itself unchanged: the source must still contain the step's own terms.
    """

    if len(term) < 4:
        return ()
    variants = {term + "s", term + "es"}
    if term.endswith("es"):
        variants.update({term[:-2], term[:-1]})
    elif term.endswith("s"):
        variants.add(term[:-1])
    if term.endswith("y"):
        variants.add(term[:-1] + "ies")
    elif term.endswith("ies"):
        variants.add(term[:-3] + "y")
    return tuple(v for v in variants if len(v) >= 3 and v != term)


def _unnegated_word_present(term: str, lowered_statement: str) -> bool:
    """True iff ``term`` occurs as a whole word NOT immediately negated."""

    for match in re.finditer(r"\b" + re.escape(term) + r"\b", lowered_statement):
        before = lowered_statement[: match.start()]
        # whitespace/hyphen-tolerant immediate negators: non-/non/not/no
        if re.search(r"(?:\bnon[-\u2011]?|\bnot|\bno)\s*$", before):
            continue
        return True
    return False


def significant_terms(text: str) -> list[str]:
    text = normalize_math_text(text)
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", text.lower())
        if token not in STOPWORDS
    ]


def term_covered(term: str, statement: str) -> bool:
    # Aliases and rule tokens match as whole words, never substrings —
    # "ample" must not fire inside "example"/"sample", and the proper+ample
    # projectivity rule must not be satisfied by negated occurrences.
    for alias in TERM_ALIASES.get(term, []):
        if _unnegated_word_present(alias, statement):
            return True
    if term == "field":
        return _unnegated_word_present("over", statement) or _unnegated_word_present(
            "base field", statement
        )
    if term == "order":
        return "|g|" in statement or "order" in statement or "number of elements" in statement
    if term == "finite":
        return (
            _unnegated_word_present("finite", statement)
            or bool(re.search(r"\border\s+of\s+[a-z]\s+(?:is|=)\s+p\b", statement))
            or bool(re.search(r"\|\s*[a-z]\s*\|\s*=\s*p\b", statement))
        )
    if term == "projective":
        return _unnegated_word_present("proper", statement) and _unnegated_word_present(
            "ample", statement
        )
    return False


def expanded_hypothesis_terms(text: str) -> list[str]:
    lowered = normalize_math_text(text)
    expanded = []
    for term, aliases in TERM_ALIASES.items():
        term_present_in_text = len(term) >= 3 and _unnegated_word_present(term, lowered)
        if term_present_in_text or any(alias in lowered for alias in aliases):
            expanded.append(term)
    return expanded


TERM_ALIASES = {
    "field": ["over", "base field"],
    "q": ["global field", "number field", "rational field"],
    "galois": ["galois group", "galois extension"],
    "order": ["|g|", "order", "cardinality", "number of elements"],
    "proper": ["complete"],
    "smooth": ["nonsingular", "non-singular"],
    "solvable": ["solvable group"],
}


STOPWORDS = {
    "and",
    "are",
    "apply",
    "for",
    "from",
    "hypothesis",
    "particular",
    "source",
    "the",
    "theorem",
    "this",
    "its",
    "some",
    "via",
    "over",
    "every",
    "occurs",
    "with",
}


def normalize_math_text(text: str) -> str:
    """Normalize common TeX/prose variants used by theorem webpages."""

    lowered = str(text).lower()
    lowered = re.sub(r"\\text\{([^}]*)\}", r"\1", lowered)
    lowered = lowered.replace("\\langle", "<").replace("\\rangle", ">")
    lowered = lowered.replace("\\neq", "!=").replace("\\ne", "!=")
    lowered = lowered.replace("\\in", " in ")
    lowered = lowered.replace("\\gt", ">").replace("\\lt", "<")
    lowered = re.sub(r"\\[a-zA-Z]+", " ", lowered)
    lowered = re.sub(r"\|\s*([a-z])\s*\|\s*=\s*p\b", r" order of \1 is p ", lowered)
    lowered = re.sub(r"\|\s*([a-z])\s*\|", r" order of \1 ", lowered)
    lowered = re.sub(
        r"number of elements in\s+(?:\\?\(?\s*)?([a-z])",
        r"order of \1",
        lowered,
    )
    lowered = re.sub(r"prime\s+number", "prime", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _hypothesis_relevant_to_step(
    hypothesis_name: str,
    required_statement: str,
    theorem_statement: str,
) -> bool:
    """Only require claim hypotheses that the proof step/theorem actually uses.

    One rule for every hypothesis: a hypothesis is relevant when the step or the
    cited theorem actually mentions one of its terms. There used to be three
    branches ahead of it — "group", "finite", "prime"/"order" — each with its own
    hand-written marker list, so a hypothesis about a group was judged by
    different evidence than a hypothesis about a sheaf. That is the same
    answer-key mechanism as the deleted theorem shortcuts, and it made the gate's
    strictness depend on which area of mathematics the claim came from.
    """

    combined = normalize_math_text(f"{required_statement} {theorem_statement}")
    hyp_terms = list(
        dict.fromkeys(
            significant_terms(hypothesis_name) + expanded_hypothesis_terms(hypothesis_name)
        )
    )
    # ANY term, deliberately: a hypothesis counts as used as soon as the step or
    # the theorem touches something it is about. Requiring ALL of its terms was
    # tried and rejected — it let a source whose hypotheses genuinely mismatch
    # the step earn source_backed, because the mismatching hypothesis stopped
    # being "relevant" and so stopped being checked. Between the two errors, the
    # one that grounds a step on the wrong theorem is the one that matters, so
    # this stays on the strict side and a citation the gate cannot line up stays
    # a candidate.
    return any(term_present(term, combined) for term in hyp_terms)




def _is_subgroup_equality_request(required_statement: str) -> bool:
    required = normalize_math_text(required_statement)
    return (
        "subgroup" in required
        and (
            "h = g" in required
            or "same order" in required
            or ("order of h" in required and "order of g" in required and "then h" in required)
        )
    )


def _states_subgroup_equality(theorem_statement: str) -> bool:
    theorem = normalize_math_text(theorem_statement)
    return (
        "subgroup" in theorem
        and (
            "h = g" in theorem
            or "same order" in theorem
            or "same cardinality" in theorem
        )
    )


def _unsupported_symbolic_order_hypothesis(hypothesis_name: str) -> bool:
    """Pure symbolic cardinality hypotheses need an explicit semantic rule."""

    lowered = hypothesis_name.lower()
    return bool(re.search(r"\|\s*[a-z]\s*\|\s*=\s*[a-oq-z]\b", lowered))
