# Scoring calibration

Numeric metrics are reproducible only relative to explicit units and annotations.
Freeze the source revision, intended reader, unit IDs, locations, and counting
rules before comparing rewrites. The following synthetic anchors specify how
to count; they are not a performance evaluation of the skill.

## Anchors for the seven metrics

| Metric | Frozen units and judgments | Expected result |
| --- | --- | --- |
| Notation coverage | In “Let V and W be real vector spaces, let f:V -> W be linear, and consider the invariant Q,” register V, W, f, Q. The first three are introduced; Q has no definition. | 3/4 coverage; all four assessed |
| Gap ratio | For linear f:V -> W and g:W -> U with gf=0, a claim im(f)=ker(g) has two registered inclusions. im(f) subset ker(g) follows; the reverse inclusion does not follow. | 1/2 unsupported essential inferences; also flag the equality claim as potentially refutable, not a routine omission |
| Terminology consistency | Register the established concepts kernel, image, direct sum. Kernel and direct sum use standard names; image is unnecessarily renamed “active sector” with the same explicit definition. | 2/3; genuinely new concepts are listed separately, not penalized merely for being new |
| Citation misuse | Register three supporting uses: one supported, one demonstrably misapplied, one unavailable. | Misuse 1/2, assessed coverage 2/3, one unresolved; never 1/3 or 0% |
| Casual chatting | Register three proof paragraphs: a valid deduction, an unsupported “obvious” replacing a needed step, and intuition followed by the actual deduction. An introductory motivation paragraph is outside this denominator. | 1/3; useful intuition alone is not a failure |
| Circular dependency | Register A,B,C,D with edges A->B, A->C, B->D, C->D. | 0/4 cycle participants; this does not certify the proof's mathematical validity |
| Statement clarity | Register a clean hypothesis/conclusion statement, a statement containing its proof argument, and a longer statement whose constructions specify the claim. | 2/3; length or an if-then condition alone is not a failure |

In the gap anchor, V=0, W=R, U=0, with both maps zero, refutes the equality.
Keep the failed inclusion in the registered comparison and record the refutation;
do not invent a proof or silently assume exactness at W.

If C->A is added to the dependency anchor, A and C participate in a directed
cycle, giving 2/4. B and D are downstream of the unresolved cycle but are not
themselves cycle participants. Report their affected status separately.

## Annotation decisions

- Use pass, fail, or unresolved for each registered unit. A missing assessment
  remains unresolved. Do not remove a difficult unit from the denominator.
- Bindings count as introductions. Decide which audience-standard symbols and
  bound variables are excluded before counting; record the exclusions.
- Split an essential inference only when it has independently checkable premises
  and conclusion. Freeze that segmentation before seeing the rewritten text.
  If reasonable reviewers choose different segmentations, report the disagreement
  and a sensitivity analysis instead of presenting a unique objective gap ratio.
- Keep paragraph boundaries fixed for the casual-chatting comparison. Record new
  paragraphs separately; do not improve the rate by fragmenting the same argument.
- A changed or removed claim must keep its original ID in the comparison, with
  its new status and reason. New claims and replacement claims get new IDs.
- For reviewer calibration, annotate the same fixed units independently, compare
  classifications and reasons, and retain the disagreements. Agreement on arithmetic
  does not establish agreement on mathematical or editorial judgments.

## Optional deterministic scoring helper

Run from this skill directory:

```text
python3 scripts/score_review.py REGISTRY.json ASSESSMENT.json --source SOURCE.txt
python3 scripts/score_review.py REGISTRY.json AFTER.json --baseline BEFORE.json
```

REGISTRY.json contains scope.source_sha256, scope.reader, scope.inventory_complete
(a declared boolean), and a units list. Each unit has id, metric, location, and
description. The metric IDs are notation_coverage, gap_ratio,
terminology_consistency, citation_misuse, casual_chatting, circular_dependency,
and statement_clarity. A metric with no registered applicable units is N/A.

ASSESSMENT.json contains registry_sha256 (the SHA-256 of the registry file's
exact bytes) and judgments. Each judgment has unit_id, status, and reason.
Statuses are pass, fail, and unresolved. Missing judgments are unresolved;
unknown or duplicate IDs are errors. Reasons are required for pass and fail.
Use separate files for independent reviewers; do not overwrite disagreements.

The helper checks record consistency and computes counts and assessed coverage.
It does not identify units, determine whether a proof or citation is valid, or
establish that an inventory is complete. Without --source it does not verify the
source bytes. It reports a before/after change only when the exact assessed unit
sets agree; a change in coverage is reported without an improvement score.

For a full rewrite, new arguments may require an expanded registry. Keep the
original registry's comparison and an additional inventory of new units separate;
do not retroactively change the registry to make the baseline look worse.
