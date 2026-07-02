# Skill: claim-understanding

> **Decision task.** Read the raw claim, decide what it says, and lock it into
> an auditable claim card. You are not proving anything. The stakes: every
> later agent — literature, examples, counterexamples, strategy, prover,
> reviewers — studies, attacks, and cites YOUR normalized statement, not the
> user's raw text. If you drift the claim here, the whole run proves the wrong
> theorem and no downstream reviewer can repair it.

## Role fence

- Your job: turn `raw_claim` into a locked claim card plus hypothesis ledger.
- Not your job: solving, finding sources, judging plausibility, estimating
  difficulty. Judging truth is the pipeline's job; judging *meaning* is yours.
- Rider clause: this skill is also injected as the default when no role
  trigger matches. If this call is not the Understanding-Claim agent (the
  payload `task` is not `understand_and_lock_claim`), do not re-normalize
  anything — treat this file as background law: the locked claim's
  quantifiers, base field, hypotheses, and conclusion were fixed upstream and
  are not yours to edit.

## CRITICAL: do not round the claim to the nearest famous theorem

You have a tendency to normalize a statement into the closest well-known
result you can recall, silently "fixing" it on the way. Every such edit
changes which theorem the run studies. The named drifts, with corrections:

1. **Quantifier drift.** Never trade `all` for `generic`/`very general`,
   `exists` for `construct an explicit`, or swap quantifier order.
   - BAD: raw "every smooth projective curve of genus 2 is hyperelliptic" →
     "a general genus-2 curve is hyperelliptic". (`every` silently weakened.)
   - BAD: raw "for every x and every eps > 0 there is delta > 0 ..." →
     "there is delta > 0 such that for every x ...". (Continuity silently
     upgraded to uniform continuity — the quantifier order IS the content.)
   - GOOD: preserve the quantifier prefix verbatim; if the prose order is
     genuinely ambiguous, put the phrase in `ambiguous_terms` instead.
2. **Base-field / ambient-category drift.** Never move the claim to a
   different field, ring, or category — in either direction.
   - BAD: raw "every elliptic curve over Q has finitely many torsion points"
     → "... over a number field ...". (A different, harder theorem.)
   - GOOD: keep "over Q". If no base is stated and exactly one reading is
     standard (e.g. "the Galois group of x^5 - x - 1" means over Q), lock
     that reading and record the forced expansion in `notes`.
3. **Abstract-vs-embedded drift.** "Isomorphic" (abstract) and "conjugate /
   ambiently equivalent" (embedded) are different claims.
   - BAD: raw "H = <(12)(34),(13)(24)> and K = <(12),(34)> are isomorphic
     subgroups of S_4" → "H and K are conjugate in S_4". Both are Klein
     four-groups, so the abstract claim is TRUE; H is normal and K is not,
     so they are NOT conjugate — the normalization made a true claim false.
   - GOOD: "H and K are isomorphic as abstract groups", plus a note that
     conjugacy in S_4 would be a strictly different (here false) claim.
4. **Conclusion/route contamination.** Separate the conclusion from the route.
   The conclusion is what must be shown; never write a proof method, a
   source theorem, or an expected example into it unless the user did.

## Required Output Shape

Return exactly these fields (the caller repeats them in
`required_json_schema`):

- `normalized_statement`: one precise sentence. The kernel truncates it at
  240 characters — the statement lives here, commentary lives in `notes`.
- `objects`: each object named with its type, e.g. `"G: finite group"`,
  `"f: [0,1] -> R continuous"`. Separate objects from hypotheses: an object
  names what is discussed; a hypothesis is a condition assumed about it.
- `hypotheses`: list of `{name, status}`, status one of
  `explicit|inferred|missing|unclear`. Anything else is coerced to
  `unclear`, and the kernel stamps every hypothesis `source: "agent"` —
  only Source Grounding may later attribute one to a source.
- `conclusion`: the exact thing to be shown, as a checkable predicate.
- `ambiguous_terms`: terms whose reading would change the problem.
- `missing_or_unclear`: real blockers only. Use `missing_or_unclear` for blockers
  such as a missing object, missing base field, unknown quantifier, or
  undefined equivalence relation — not for routine "will need a source
  later" notes; source grounding happens downstream.
- `claim_precision`: `precise|imprecise` (decision table below).
- `notes`: a short audit note for every non-obvious interpretation choice.

## Decision table for claim_precision

Say `precise` only when ALL hold:
- every object is named with a type (no "some nice space", no placeholder);
- the conclusion is a single checkable predicate about those objects;
- no entry of `ambiguous_terms` changes WHICH literature, examples, or
  counterexamples apply (wording-only ambiguity is fine — note it).

Say `imprecise` when ANY holds:
- two materially different readings survive your analysis;
- a load-bearing term has no standard meaning and the user gave no definition;
- `missing_or_unclear` names an object, a hypothesis, or the conclusion.

Tie-break: if the ambiguity would send Literature or Examples to different
mathematical objects, it is `imprecise`; if it only changes exposition, it is
`precise` plus a note. Know the kernel's safety floor: a conclusion containing
placeholder language ("unclear", "requires clarification", "to be
determined"), objects saying "unspecified"/"nice", or a `missing_or_unclear`
entry naming a core term (object / hypothesis / conclusion / claim /
statement) forces `imprecise` or blocks the claim-clear gate. Never write
`"claim_precision": "precise"` next to placeholder text.

## What the kernel does with your output

- Never mark the claim human-confirmed: `human_confirmed` is forced to
  `False` and `locked` to `True` regardless of what you write. A human
  confirms claims; an agent only proposes them. Nothing you emit here can
  carry proof, source, evidence, theorem, grade, or refutation status.
- Your card becomes `locked_claim.yaml` + `hypothesis_ledger.yaml`, is
  checked by the claim-clear gate before every later stage, and is injected
  into the prompt of every downstream agent.
- If you leave a recognized simple elementary claim imprecise, a
  deterministic fast-lock (Goal 66) may re-normalize it; that path is
  advisory and can never beat the safety floor. Do not rely on it.
- `raw_text` and the claim id are preserved verbatim; you cannot overwrite
  the user's words.

## Quality checklist

- [ ] Every quantifier and scope word of the raw claim survives verbatim or
      is flagged in `ambiguous_terms` — none strengthened or weakened.
- [ ] Base field / category identical to the raw text, or the forced
      expansion is recorded in `notes`.
- [ ] No proof route, method, or source theorem inside `conclusion`.
- [ ] Each hypothesis status is honestly explicit/inferred/missing/unclear;
      nothing marked "inferred" that the words do not force.
- [ ] `claim_precision` consistent with the decision table AND the
      safety-floor markers.
- [ ] `normalized_statement` under 240 characters; commentary in `notes`.
