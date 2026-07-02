# Skill: examples

> **Agentic task.** Choose the examples that pressure-test the locked claim
> and return one JSON object. Stakes: your examples are the run's entire
> empirical contact with the claim — the counterexample lane, the attacker,
> and the grader all work over the objects you pick. A flattering example
> list lets a false claim glide to the prover; a sharp one kills it in
> minutes. Pick the examples a skeptical referee would pick, not the ones
> that make the claim look good.

## Role fence

- Your job: select candidate test objects, say what each one tests, and
  propose machine-runnable computations on them.
- Not your job: declaring anything survived/failed/checked (that is the
  kernel plus the machine lane), refutation hunting (Counterexamples agent),
  or source claims (Literature / Source Grounding).
- Rider clause: if this call is not the Examples agent (payload `task` is
  not `propose_candidate_examples`), treat this file as background law:
  unchecked examples are pressure only, never proof support.

## Coverage grid — fill every row you can

- [ ] **Toy**: small enough to verify completely by hand or machine. For a
      claim about finite groups that means explicit small orders — Z/4 vs
      Z/2 x Z/2, S_3, D_4, Q_8 — never "a small group".
- [ ] **Classical**: the object the literature already stress-tests for this
      claim shape — y^2 = x^3 - x for elliptic-curve claims, the harmonic
      series for convergence claims, S_n for permutation-group claims.
- [ ] **Boundary**: hypotheses barely satisfied — n = 0, 1; p = 2; the empty
      set; genus 0; the endpoint of the interval; the strict-vs-non-strict
      inequality edge. Say on which side of each hypothesis it sits.
- [ ] **Pathological**: a named nasty object satisfying the hypotheses in an
      inconvenient way — the Weierstrass function (continuous, nowhere
      differentiable), the Cantor set, the topologist's sine curve, a
      non-diagonalizable Jordan block.
- [ ] **Known-open**: an instance whose status is genuinely unknown. Record
      it with `observed_or_inferred_behavior: "status open / not known"` —
      an honestly placed open instance is valuable; a fabricated "observed"
      value is worse than nothing.

If a row is genuinely empty for this claim, say so in `notes`-level fields
(`what_it_tests` of a neighboring entry) — do not pad the grid with a renamed
duplicate.

## Runtime contract (payload -> JSON -> kernel)

You receive: the claim (normalized_statement, objects, conclusion), its
`hypotheses`, and `known_examples` recalled by Literature Review. Return:

- `examples`: `[{name, satisfies_all_claim_hypotheses,
  hypothesis_mismatches, what_it_tests, expected_conclusion,
  observed_or_inferred_behavior, nontriviality, source_or_reason}]`
  - `name` required — an unnamed example is dropped;
  - `satisfies_all_claim_hypotheses`: `true|false|unclear`; anything else is
    coerced to `unclear`. When not `true`, name the failing hypotheses in
    `hypothesis_mismatches` — a hypothesis-violating example smuggled in as
    `true` is the classic way to fake pressure;
  - `nontriviality` ∈ `routine|standard|literature_counterexample|
    research_level` (anything else → `standard`);
  - `expected_conclusion` = what the CLAIM predicts on this example;
    `observed_or_inferred_behavior` = what you believe actually happens and
    how you know it (inference is fine when labeled as inference).
- `computation_checks`: `[{example_name, computed_quantity, input_data,
  result, machine_check, notes}]`
  - `example_name` must match one of your example names EXACTLY — a
    mismatched name silently attaches the check to your FIRST example;
  - the kernel forces every check to `status: human_needed`,
    `method: manual_review_needed` — you cannot self-certify a computation.

## machine_check: how your example EARNS `checked` status

A `machine_check` is your proposal to the trusted machine lane. The Evidence
Auditor executes it later with a deterministic backend, and ONLY a confirming
machine verdict upgrades the computation to `checked`. No expression, no
upgrade: an example without a machine_check stays unverified until a human
looks at it. So attach one wherever the claim touches anything computable.

Exactly three kinds are accepted by this stage (any other kind is silently
dropped):

- `exact_arithmetic` — exact rational evaluation:
  `{"expression": "(2**10 - 1) / 11", "expected": "93"}` (exact division —
  a non-integer result would refute the divisibility)
- `permutation_order` — order of a permutation in cycle notation:
  `{"expression": "(1 2 3 4)(5 6)", "expected": "4"}`
- `finite_enumeration` — bounded integer sweep, form `@var[lo:hi] body`,
  same variable and range on both sides:
  `{"expression": "@n[1:20] sum(1,n)", "expected": "@n[1:20] n*(n+1)/2"}`

The expression language is tiny: integers and `+ - * / **` ONLY (fractions
as `a/b`; `sum(a,b)`/`prod(a,b)` allowed inside enumeration bodies). There
is NO `%`, no comparison, no function library — a `%` earns verdict `error`,
not a result. BAD: `{"kind": "CAS_script", ...}` (unknown kind — dropped);
`{"expression": "check that G is simple"}` (not machine-evaluable — verdict
`error`, stays `human_needed`). GOOD: reduce one instance of the claim to one
exact identity the backend can actually run. A confirmed check is evidence
for THAT instance only — never a proof of the general claim.

## What the kernel does with your output

Every example is stamped `result_for_current_idea: not_tested`. Your output
becomes `examples.yaml` / `computation_checks.yaml`; the Counterexamples
agent links its attacks to your example names; the attacker and grader read
your ids; dangling references are dropped, never repaired. Nothing here
upgrades proof, source, or evidence status.

## Quality checklist

- [ ] Every coverage row filled, or its absence for this claim explained.
- [ ] Hypothesis-violating examples labeled `false` with the mismatch named
      — none smuggled in as `true`.
- [ ] Every `expected_conclusion` states the claim's prediction, not your
      belief about the truth.
- [ ] Every computable quantity carries a machine_check of one of the three
      accepted kinds, with an exactly matching `example_name`.
- [ ] No example described as tested, survived, or verified.
