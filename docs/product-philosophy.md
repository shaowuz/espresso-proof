# product philosophy

Espresso Proof is a local mathematical idea-testing workspace. It helps a
mathematician turn a claim or proof sketch into auditable artifacts: locked
claim, hypotheses, candidate sources, examples, counterexamples, strategies,
reviewer objections, and a visible process trace.

Espresso is intentionally not a theorem prover, not a Lean formalization
product, and not a general research office suite. The product path is:

```text
React cockpit -> espresso serve -> external Codex agent -> artifact kernel
```

## core promise

The product should answer a practical research question:

```text
Is this mathematical idea worth continuing, and what is the cheapest next
action?
```

The default answer should be a compact research memo:

- the clarified claim;
- the strongest routes;
- what is sourced, checked, inferred, or speculative;
- the main obstruction;
- the next decision for the mathematician.

## design principles

1. Explore multiple routes before judging an idea.
2. Keep reviewers independent from generators.
3. Treat counterexamples and pressure tests as first-class work.
4. Require exact source grounding for important proof steps.
5. Never let a candidate source, example, reviewer approval, or memory entry
   become trusted evidence by wording alone.
6. Preserve failed attempts as useful advisory memory.
7. Let the human control continuation: quick test, deeper exploration, or stop.
8. Keep the runtime local, inspectable, and artifact-backed.

## trust boundary

Skills and external agents may generate candidate mathematical material. They do
not own trusted status transitions.

The Python artifact kernel owns:

- schema validation;
- source/proof/evidence status;
- path confinement;
- rollback after failed writes;
- display-safe wording.

When product ambition conflicts with these boundaries, the boundaries win.
