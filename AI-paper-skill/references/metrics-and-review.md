# Metrics and review

Read this file when establishing a baseline, checking the revision, or recording
findings. Use these seven metrics consistently; the definition-locality and
repetition checks below supplement them rather than introducing competing scores.
These are proposed, manually assessed diagnostics, not calibrated benchmarks or
certificates of mathematical correctness.

## Counting and reporting

For each metric, report the numerator and denominator as `n/K`, optionally with
a percentage, plus the review coverage `K/T`: `T` is the total number of relevant
units in scope and `K` is the number assessed. State the counting unit and keep
unresolved units separate. An inaccessible source is unresolved, not automatically
a correct or incorrect citation. Use `N/A` when there are no assessable units.

If the total units cannot be established, report coverage as unknown. If reliable
counting is not practical, give located findings and say `not measured`; never
invent a percentage. For before/after comparisons, preserve the scope and counting
rules, map any changed units, and explain denominators that are not comparable.
Do not improve a score merely by splitting paragraphs or deleting difficult claims.
The metrics overlap; do not combine them into an overall score without an explicit
aggregation rule.

## 1. Notation coverage rate

**Measure:** assessed notation units defined at or before first use / assessed
notation units. Higher is better.

Count a symbol with a particular meaning in a particular scope as one unit, rather
than counting every occurrence. Explicit variable binding counts as a definition.
Treat standard notation relative to the stated audience; require unfamiliar or
ambiguous conventions to be stated or referenced.

- Check objects, parameters, indices, maps, domains, codomains, and hypotheses.
- Flag symbols with missing definitions, conflicting meanings, or silent changes
  of scope. Do not infer the author's intended meaning when several are possible.
- Move existing definitions before first use. Add a missing definition only when
  its intended meaning is unambiguous; otherwise flag it for clarification.

## 2. Gap ratio

**Measure:** assessed essential inferences lacking a sufficient argument or
applicable supporting result / assessed essential inferences. Lower is better.

Count mathematical inferences, not sentences or displayed equations. An essential
inference is one whose failure could invalidate the stated conclusion or its
applicability. Routine steps may remain concise when they are reconstructible by
the intended reader without new assumptions or a new substantive result.

- Check whether insightful observations receive actual arguments, rather than
  heuristic explanations presented as proofs.
- Track hypotheses and quantifiers through each essential inference. Look for
  silent shifts such as generic to all, existence to universality, and local to
  global without the needed compatibility or gluing argument.
- Remove redundant explanations of elementary steps when rewriting, but do not
  hide gaps by shortening the proof. Report unnecessary repetition separately
  from the gap ratio so verbosity cannot compensate for a missing argument.

## 3. Terminology consistency

**Measure:** assessed established-concept introductions using appropriate standard
names and notation / assessed established-concept introductions. Higher is better.

- Identify concepts given new names or symbols despite an established equivalent.
  Verify the equivalence before recommending a replacement; similar names need
  not denote the same construction.
- Prefer standard terminology and remove unnecessary naming for one-off steps.
  For a rename, record the old name, proposed name, and affected occurrences.
- Preserve genuinely new concepts, useful local notation, and deliberate author
  choices. Do not penalize a legitimate standard convention merely because
  another convention is more familiar. List uncertain classifications separately.

## 4. Citation misuse rate

**Measure:** assessed supporting citation uses that fail to support the claim as
invoked / assessed supporting citation uses. Lower is better. Count each pairing
of a claim with its supporting source as a use, even when a source is reused.

- Consult the original source for the exact statement, theorem or section locator,
  and hypotheses. Correct bibliographic metadata alone does not establish support.
- Compare the source's conclusion and assumptions with the manuscript's actual
  application. Separate a wrong citation, an inapplicable theorem, and a suspected
  gap in the cited argument; give evidence for the distinction.
- At analytic/algebraic boundaries, identify the comparison result being used
  and check its applicability. Do not treat the word GAGA as a blanket license
  for transferring results. At local/global boundaries, check the required
  compatibility and gluing conditions.
- If a reference is unavailable or a suspected source gap cannot be resolved,
  mark that use unresolved and disclose the coverage limit. Do not fabricate a
  locator, silently substitute a different theorem, or call a suspicion a known
  error. Checking citation support does not certify the cited proof as correct.

## 5. Casual chatting rate

**Measure:** assessed proof paragraphs substituting informal persuasion, intuition,
or heuristic plausibility for a needed deductive step / assessed proof paragraphs.
Lower is better. Use fixed paragraph segmentation for a comparison.

- Separate rigorous deduction from explanations of intuition, motivation, and
  strategy. Informal exposition is useful; do not count it as a defect unless it
  takes the place of a necessary argument or obscures the claim's evidence status.
- Replace unsupported phrases such as an unexplained appeal to obviousness with
  the actual argument, an applicable reference, or a visible unresolved gap.
- Label heuristics as heuristics. For a proof-relevant folklore claim, seek a
  supporting argument or verifiable source; folklore is not automatically false
  or heuristic, but its reputation alone does not justify an essential step.
- Keep transitions readable and flexible while making logical dependencies
  explicit. A gap also counted under gap ratio remains the same underlying defect.

## 6. Circular dependency rate

**Measure:** assessed claim nodes participating in unjustified circular
dependencies / assessed claim nodes. Lower is better; zero observed defects does
not establish their absence when the dependency reconstruction is incomplete.

Represent lemmas, propositions, theorems, and needed intermediate claims as nodes.
Orient an edge `A -> B` when B's justification invokes A. Include dependencies in
the arguments, not only explicit citations between numbered statements.

- For an ordinary finite proof, resolve these dependencies into a directed
  acyclic graph (DAG). Check for direct self-dependence and longer directed cycles.
  Give the actual dependency chain for a reported cycle.
- Shared lemmas and undirected cycles are allowed. Do not require a tree,
  planarity, embedding genus zero, or an underlying graph with no holes.
- Resolve scoped assumptions, assumption discharge, and induction hypotheses
  before diagnosing circularity. Legitimate induction, well-founded recursion,
  and proof by contradiction must not be mistaken for self-justification. If a
  specialized cyclic proof system is intended, check its validity conditions.
- Break an unjustified cycle only with an independently justified argument,
  revised proof structure, or an explicitly proposed change to the claim.
  Rearranging the written order alone does not fix circular reasoning.

## 7. Statement clarity metric

**Measure:** assessed theorem, proposition, and lemma statements that specify
their objects, hypotheses, quantifiers, and conclusions without mixing in proof
instructions / assessed theorem, proposition, and lemma statements. Higher is better.

- Prefer a clean hypothesis/conclusion structure when it fits the mathematics.
  Do not force every statement into an if-then form or treat length alone as a defect.
- Keep necessary definitions and qualifications. Move reasoning such as applying
  another theorem, performing a reduction, or explaining how to handle a case into
  the proof when it describes the method rather than the conclusion.
- Where helpful, separate a core statement, its proof, and subsequent applications
  or corollaries. Preserve every mathematical assertion, assumption, case, and
  intended relationship when doing so; show the correspondence in the change log.

## Definition locality and useful explanation

Notation coverage alone does not measure how much context the reader must
reconstruct. Place specialized definitions near their first substantive use,
give global conventions one identifiable home, and add brief reminders after
long absences or scope changes. Report concrete backtracking problems with
locations; label any reading-distance estimates as estimates. Do not invent a
distance threshold or repeatedly redefine stable objects.

Evaluate repetition with a deletion test: remove a passage only when its
mathematical content, explanation, orientation, and useful comparison all survive.
Preserve complementary explanations of difficult concepts and parallel wording
that exposes a change of hypotheses. Word count alone is not a quality metric.

The complementary checks from the supplied manuscript workflow are retained as
follows:

| Readability concern | Where to evaluate it |
| --- | --- |
| Undefined or insufficient notation | Metric 1, including type, scope, and meaningful definitions |
| Proof inside statements | Metric 7; do not remove conditions or constructions specifying the claim |
| Definition locality | The locality check above and the manuscript reconstruction pass |
| Citation misuse | Metric 4, with exact applications and checked coverage |
| Unproductive repetition | Metrics 2 and 5, plus the deletion test above |

## Located issue records

Give each consequential issue a stable ID, such as G1, C1, or N1. Record:

- The source revision and location, plus the active statement it affects.
- The defect, evidence, and classification: missing argument, suspected gap,
  unverified source, misapplication, false claim, or expository confusion.
- For a proof obligation: the relevant objects and hypotheses, the exact missing
  implication, established inputs, and what would discharge the obligation.
- Severity and dependent results. A gap carrying the main new difficulty is a
  core unresolved problem even if it occupies one sentence.
- The proposed repair, any changed assumptions or conclusion, current status,
  and the evidence supporting a status change.

Keep an issue open until a concrete repair, source, counterexample, replacement
argument, or explicit scope change addresses it. Use statuses such as open,
resolved-with-evidence, refuted, or superseded, and explain transitions.
These are review labels, not Espresso kernel trust statuses.

A refuted claim is not an unfinished obligation to prove the false claim.
Preserve the original claim and counterexample, identify any replacement claim
separately, and disclose when the original intended contribution remains unachieved.
Do not merge or silently retire obligations to reduce a gap count. If a valid
alternative proof removes a dependency, record that argument and the reason.

For citations, distinguish supported, misapplied, unverified, and no longer used.
Unverified sources remain visible in coverage; they are not automatically failures.
For unresolved dependencies, propagate the limitation to every result whose
declared proof route needs the unresolved step.

## Audit summary

For each metric, give its scope, counting unit, n/K, assessed coverage K/T,
unresolved items, and representative locations; or say not measured or not
applicable with the reason. Link before/after changes to the same claims and
reader assumptions. Report moved, added, and retired units separately.

Keep the audit outside the manuscript's ordinary exposition. Preserve short,
stable pointers at consequential unresolved steps in the manuscript.
Report exposition status, mathematical review coverage, source coverage, build
and rendering checks, and any requested venue checks separately. Do not claim a
rate of undiscovered errors without an independent reference review or labeled
test set; finding no additional error does not establish a zero error rate.
