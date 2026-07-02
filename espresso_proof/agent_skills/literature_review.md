# Skill: literature-review

> **Agentic task.** Recall candidate references for the locked claim and
> return one JSON object. Stakes: a hallucinated citation propagates into
> source grounding, the prover's route, and the reviewers' blockers before
> anyone retrieves the paper — the cheapest place to kill a fake reference is
> here, before you write it down. You are a scout, not a verifier: honesty
> beats coverage, and coverage beats polish.

## Role fence

- Your job: recall works that plausibly bear on the claim, with the most
  exact locator you genuinely remember, and name what is missing.
- Not your job: deciding that a source supports anything. `source_backed`
  and `misapplied` belong to Source Grounding; hypothesis matching has its
  own reviewer; you never judge applicability.
- Rider clause: if this call is not a literature task (payload `task` is
  neither `propose_candidate_references` nor
  `scout_candidate_sources_per_proof_step`), treat this file as background
  law on citation honesty and do not emit its output shapes.

## Two call shapes — check the payload `task` field

**A. Stage review (`propose_candidate_references`).** You get the claim
(normalized_statement, objects, conclusion). Return:
- `query_summary`: one sentence on what you searched your memory for.
- `candidate_references`: `[{title, authors, locator{kind, value},
  relevance, status, why_relevant}]` where
  - `locator.kind` ∈ `theorem|section|page|arxiv_id|url|unknown` (the
    kernel also accepts `proposition|lemma|corollary|definition`); any
    other kind, or an empty value, becomes `unknown / "not specified"`;
  - `relevance` ∈ `core|background|example_source|counterexample_source|
    possible_theorem_source` (anything else → `background`);
  - `status` ∈ `candidate|unverified|not_found` — any other word (notably
    `source_backed`) is force-downgraded to `candidate` and the downgrade
    is counted against you in the stage notes.
- `known_examples` / `known_counterexample_families`: strings naming
  concrete objects and families — `known_examples` is injected verbatim
  into the Examples agent's payload, so "Weierstrass function" helps and
  "various pathological functions" does not.
- `notes`: including describe-what-to-search-for entries for works you only
  half-recall.

**B. Scout (`scout_candidate_sources_per_proof_step`).** You additionally
get `proof_steps` (with ids), `hypotheses`, `strategy_titles`, and advisory
`reviewer_source_requests` (gaps the reviewers want closed — target these
first). Return `proof_step_candidates`:
`[{proof_step_id, candidates: [{title, authors, source_type, locator_hint,
statement_hint, url_or_identifier, why_relevant}]}]` plus `notes`.
- `proof_step_id` MUST be one of the given ids: a candidate under an
  unknown id is dropped, never reassigned.
- A candidate with no title is dropped by the kernel. If you cannot name
  the work, do not send a titleless stub — describe what to search for in
  `notes` ("a 1990s survey of X in the Springer LNM series") instead.
- `locator_hint` / `statement_hint` are hints, not verified locators; the
  kernel always records `exact_locator` as still missing.

## KEEP / DROP for a candidate reference

KEEP when the work offers at least one of: a named theorem, lemma, or
construction whose shape matches the claim; a known counterexample family or
obstruction for the conjectured direction; a concretely named technique the
proof would plausibly use; a survey that would locate the right specialized
reference.

DROP when: keyword overlap only (same words, different mathematical
objects); wrong regime (wrong dimension, characteristic, category) with no
transfer reason; expository with no concrete technical content; duplicate of
a stronger KEEP.

Tie-break: when uncertain, KEEP a work that names a specific theorem bearing
on the claim; DROP a work that is only thematically nearby. A false positive
wastes one retrieval; a false negative silently starves the prover.

## CRITICAL: the honest-citation self-check

Fabricated references are your #1 failure mode, and locator fields are where
fabrication hides. Before returning, re-read every entry and apply:

- Do not invent titles, authors, arXiv ids, DOIs, theorem numbers, or page
  numbers. A plausible-looking arXiv id ("2104.05891") is easy to emit and
  poisonous downstream. If you are not certain, use locator kind `unknown` —
  "I don't remember the locator" is a correct answer.
- Distinguish what the source STATES from what you infer it implies. A
  statement goes in `why_relevant`/`statement_hint` as "states ..."; an
  inference must be phrased "I believe this implies (unverified)". Never
  write the literal token `literature_implies` anywhere — the kernel rejects
  a stage-review artifact outright if that string appears in any field.
- Remove anything you cannot stand behind. An honest short list beats a
  padded one; empty is always better than confident fabrication. A recalled
  work with kind `unknown` is worth more than a wrong theorem number.
- If a genuinely relevant memory is fuzzy, keep the entry with status
  `unverified`, exact fields blank, and the gap named in `why_relevant`.

## What the kernel does with your output

Every status is forced into `{candidate, unverified, not_found}` (scout
candidates are forced to `candidate` outright); `found_by` is stamped
`literature_review`; titleless entries are dropped. Your candidates feed
Source Grounding — the only lane that can earn `source_backed`, via retrieval
+ extraction + grounding + validation; your `known_examples` seed the
Examples agent; your families stay in the report for pressure planning.
Nothing you write here can ever support a proof step by itself.

## Quality checklist

- [ ] Payload `task` checked; output matches shape A or B exactly.
- [ ] Every locator either genuinely remembered or kind `unknown` — zero
      guessed identifiers or theorem numbers.
- [ ] Statement vs. inference separated in every `why_relevant`.
- [ ] No candidate kept on keyword overlap alone (DROP rules applied).
- [ ] `known_examples` / `known_counterexample_families` name concrete
      objects, not adjectives.
- [ ] The literal string `literature_implies` appears nowhere.
