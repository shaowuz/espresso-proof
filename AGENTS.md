# Agent rules

You are working in Espresso Proof. It is a research workflow whose whole value
is that a status in an artifact means something. Almost every rule here exists
to protect that.

Read [docs/product-philosophy.md](docs/product-philosophy.md) for the reasoning.
[CONTRIBUTING.md](CONTRIBUTING.md) has setup and test commands.

## The one rule everything else follows from

**Your output cannot upgrade a status.**

You may generate candidate mathematics freely — claims, routes, examples,
counterexamples, sources, objections. You do not own trusted status
transitions. Only deterministic Python kernel code grants a status, and only
against evidence it can check itself.

You cannot mark your own work `proved`. You cannot turn a source you merely
cited into a checked one. You cannot make something trusted by describing it
confidently. If the kernel refuses, the answer is that the evidence is missing —
not that the kernel needs adjusting.

## Do not weaken the boundary

Do not, as part of any other task:

- relax a validator, a schema, or a status rule so that a run gets through;
- delete or loosen a hard-negative test — those tests exist to prove that
  forged or invalid input is actually rejected, so a passing suite means
  nothing once they are gone;
- widen a status enum, add a permissive fallback, or make a required field
  optional to route around a validation error;
- drop, rewrite, or backfill provenance so that output looks better sourced
  than it is;
- write outside the run root, or skip rollback after a failed write.

If a change genuinely needs the boundary to move, that is its own change, with
its own justification and its own tests. Never fold it into a feature.

## Honesty about what you did

- Do not report work as verified that you did not run. Say which checks you ran
  and which you skipped.
- Do not describe sampled or partial work as complete.
- If you could not do something, say so plainly. A blocked task reported
  honestly is more useful than a task reported as done.
- Do not invent file paths, command output, test results, or citations.

## No architecture theater

Do not produce documentation that describes structure the code does not have.
A design document is not a substitute for a change, and a rename is not a
refactor. If a document and the code disagree, the code is what is true —
fix the document or fix the code, and say which one you did.

Prefer the smallest coherent change that solves the actual problem. Match the
conventions already in the file you are editing.

## Reference material is not runtime

comparison. It is never executed. Product code must not import from it, and it
must not be merged into `espresso_proof/`. Porting a narrow piece is fine as its
own change, with tests.

## Tests

Run scoped tests while working:

```bash
uv run python -m pytest tests/<subsystem> -q
```

The full suite runs on GitHub when a PR targets `main`. Do not weaken a test to
make that gate pass, and do not mark a task complete on the strength of a gate
you did not see finish.

## What this project is not

Espresso Proof is not a theorem prover and does not certify mathematical truth.
Do not add documentation, UI text, commit messages, or artifact wording that
implies it does.
