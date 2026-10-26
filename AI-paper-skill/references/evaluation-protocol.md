# Evaluating whether the skill helps

Use this protocol for a behavioral pilot, not for an ordinary paper rewrite.
Format validation, compilation, and Espresso's application tests do not measure
the writing quality produced by a language model following this skill.
The [2026-10-25 pilot](../evaluations/2026-10-25-pilot.md) records an initial
three-excerpt comparison and its limitations; it is not a general effectiveness claim.

## Freeze the comparison

1. Choose short real manuscript excerpts with sufficient surrounding definitions.
   Include a passage already clear enough to test unnecessary rewriting. Record
   selection criteria, original source URL or local provenance, version, page or
   line range, source hash, extraction corrections, and availability of references.
   Treat a paper's asserted theorem as a source claim, not as verified mathematics.
2. Freeze the skill version, reader, task, case boundaries, semantic invariants,
   scoring questions, and condition-label assignment before generating outputs.
   Keep the main task and source context identical in both conditions.
3. Generate one rewrite without the skill and one with it, using fresh contexts
   and the same model/settings where available. Record settings that are unknown
   or unavailable; do not claim exact reproducibility or controlled randomness.
   Restrict both writers to the same source material and tool access.
4. Keep manuscript text separate from notes/audits. Give a reviewer the source
   and manuscripts labeled A/B, with assignment varied across cases and withheld
   from the reviewer. Do not reveal the intended winner, the generation prompts,
   or the authors' issue inventories. Preserve the assignment key separately.

Use independent writers/reviewers only when available and authorized. Label AI
review, human review, and self-review accurately. A model may infer the condition
from style despite masked labels; record that limitation.

## Review questions, in order

- **Claim preservation:** identify lost or changed hypotheses, quantifiers,
  coefficients, formulas, conclusions, and unrequested new mathematical assertions.
  Give exact evidence. Do not substitute a general impression of plausibility.
- **Introduced error or false certainty:** identify new invalid steps, invented
  citations, hidden candidate repairs, or omitted uncertainty. Mark uncertainty
  when the reviewer cannot assess a specialized argument.
- **Readability:** assess definition placement, referents, statement/proof
  boundaries, proof orientation, and useful explanation. Prefer a specific
  improvement or degradation over a length-based judgment.
- **Preference:** choose A, B, tie, or both unacceptable for the stated reader,
  with reasons. An unsafe change cannot be offset by more fluent prose.

Keep the seven metrics as located diagnostics. Report numerical rates only for
frozen, actually annotated units using the calibration reference. Do not invent
full seven-metric percentages from a global preference judgment.

## Report and limits

After the review is recorded, reveal the assignment and report every case,
including ties, failures, and unchanged passages. Preserve prompts, outputs,
source/skill hashes, reviewer judgments, and the assignment key locally. Share
source excerpts or private manuscripts only with explicit authorization and
appropriate attribution; a public report may contain provenance and findings
without redistributing the source text.

A small purposive AI-reviewed pilot establishes only the observed outcomes in
those runs. It does not establish human comprehension gains, mathematical
correctness, general superiority, or performance on full papers. Report the
same-family writer/reviewer bias and absence of repeated runs where applicable.

Use a demonstrated failure to make a narrow correction. Retest on a fresh passage
when possible and keep development cases separate from later evaluation cases.
Do not keep changing the rubric, cases, or labels until the skill appears to win.
