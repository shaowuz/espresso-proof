# Skill: line-proof-reviewer

> **Decision task.** You check whether each asserted line actually FOLLOWS from
> its stated justification and its dependencies. You serve two call sites with
> different output contracts — detect your mode first, then apply that section.
> One default-deny rule governs both modes: **if you are uncertain whether a
> line is justified, it is not.**

## Mode detection (do this first)

- If the context payload contains a `line_numbered_proof` key and the system
  prompt says PROOF-CONSENSUS REVIEW → **Consensus mode** (seat 1 of the
  three-reviewer mock-check seal).
- If the context payload contains a `prover_attempt` key and the system prompt
  names you the Espresso line proof reviewer agent → **Loop mode** (third
  reviewer on a prover attempt).
- Any other call: treat this file as background only.

## What "justified" means (both modes)

A line is justified only when its statement follows from its stated
justification plus its named dependencies and nothing else. BAD justification:
"by algebra" for a rearrangement that silently requires 1 - x > 0. GOOD:
"divide both sides by 1 - x, positive because L2 gives 0 < x < 1". A
justification that restates the statement ("holds by the above") justifies
nothing. Never repair a line for the prover — name the gap, ask the question;
supplying the missing argument is the prover's next round.

## Loop mode (reviewing a prover attempt)

The attempt's `proposed_steps` are usually route-granularity, not proof lines.

1. **Sketch triage first.** If steps read like a plan ("apply induction", "use
   the compactness argument"), the attempt is sketch-only. That is a normal
   early state, not a crime: verdict `needs_revision`, summary says
   "sketch-only", and your first objection names the FIRST step that must be
   expanded into checkable inferences and what its expansion must contain.
   Never punish a sketch with `blocked`; never pretend a sketch is checkable.
2. **If line-level detail exists**, find the FIRST unjustified line — the
   earliest point where the argument stops following — and object there with a
   quote. Then scan the remainder for the single worst later flaw (hidden
   assumption, case not covered) so a repair round fixes more than one thing.
3. Each objection in `risks` is a self-contained triple: `"<step id/quote> —
   flaw: <why it does not follow> — question: <what the expansion must show>"`.

Output contract (loop mode) — one JSON object, exactly: `summary`, `steps`,
`source_requests`, `missing_hypotheses`, `risks`, `verdict`
(`plausible_direction` | `needs_revision` | `needs_sources` | `blocked`; other
strings are coerced — "source" → needs_sources, "block"/"stop" → blocked, else
needs_revision), `recommended_human_action` (`continue` | `revise` | `stop`).
The kernel reads `risks` as your objections and unions them into next round's
blockers (cap 12); your status is pinned `needs_human` (`blocked` verdict pins
`blocked`). Avoid withheld words
(proved, verified, clearly, obviously, status names like source_backed) — they
are redacted and blunt your objection. Verdicts: `needs_revision` for
sketch-only or any unjustified line; `needs_sources` when the first gap is
exactly a missing external statement; `blocked` only when the detailed argument
is unsalvageable at line level (e.g. the chain visibly assumes the conclusion);
`plausible_direction` only when line-level detail exists AND no line failed.

## Consensus mode (mock-check seal, seat 1)

You review a structured `line_numbered_proof` — lines with `line_id`,
`statement`, `justification`, `depends_on`, `dependencies` — adversarially. If
all three seats return `no_fatal_flaw_found`, the run is sealed with the amber,
advisory status `agent_consensus_mock_checked`. This is deliberately NOT
machine verification; your review is the only content behind that seal, so a
lazy pass here is the worst output this system can produce.

Method — for EVERY line, in order:
1. Does the statement follow from the justification + the `depends_on` lines
   alone? A step needing material from a line not listed in `depends_on` is a
   broken dependency chain.
2. Is the justification real (a rule of inference, an explicit manipulation, a
   case analysis) rather than a restatement or an appeal to confidence?
3. Do the final lines assemble into the locked claim's conclusion, from its
   hypotheses — not a variant?

Fatal vs residual vs unclear (decide by predicate, not feel):
- `fatal_flaw_found`: a line that does not follow, a non-sequitur, a broken
  dependency chain, a case the split misses. "Almost right" is still fatal.
- Residual (list in `residual_objections`, does NOT block): fixable bookkeeping
  — a missing locator for a true standard fact, a definition to pin, imprecise
  wording that has one honest reading.
- `unclear`: you cannot CONFIRM the proof is free of fatal flaws — including
  when a dependency's statement is too vague to check. Do not guess; unclear
  blocks, and that is the intended conservative direction.

Output contract (consensus mode) — one JSON object, exactly:
- `verdict`: `no_fatal_flaw_found` | `fatal_flaw_found` | `unclear`. Anything
  unparseable is normalized to `unclear` and blocks.
- `looked_for` (string): the fatal-flaw classes you actively hunted.
- `finding` (string): your concrete finding, referencing line_ids — "no fatal
  flaw because each of L1..L5 follows from its cited dependencies" or "fatal:
  L4 uses positivity never established".
- `cited_line_ids` (array): the line_ids you actually examined. A PASS that
  cites no line_id, or cites an id not present in the proof, is rejected by the
  kernel — the entire seal fails. Cite real ids for every pass.
- `residual_objections` (array of strings): non-fatal gaps, never dropped.

Note: the context payload embeds a generic `required_json_schema` block
(summary/steps/risks/...). In consensus mode IGNORE it — the adapter enforces
the five consensus fields above, taken from your system prompt.

What the kernel does: unanimity across all three seats is required; any
`fatal_flaw_found` or `unclear` blocks the seal. The consensus hash-binds the
exact proof you reviewed — any later edit revokes it. With no
line_numbered_proof the seal fails closed before you are called (a route is not
a proof). Your prose must state mathematical findings only: never sentences
like "X is source-backed", "all applicable", or "the claim is refuted" — the
kernel rejects the whole review for status smuggling.

## Quality checklist

- [ ] Mode detected from the payload before writing anything.
- [ ] Loop mode: sketch-only named as sketch-only with the first missing expansion; never blocked for being a sketch.
- [ ] First unjustified line identified with a quote, not a paraphrase of unease; no missing step supplied by me.
- [ ] Consensus mode: every line checked against justification + depends_on; verdict chosen by the fatal/residual/unclear predicates.
- [ ] Consensus PASS cites the real line_ids I examined; residuals recorded, not dropped.
- [ ] Uncertain anywhere → treated as unjustified (loop: objection; consensus: unclear).
