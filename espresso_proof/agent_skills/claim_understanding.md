# Skill: claim-understanding

Clarify the mathematical claim before proof search.

Your job is not to solve the problem. Your job is to turn the user's statement
into an auditable claim card that downstream agents can safely use.

## Required Output Shape

Return the fields requested by the caller exactly:

- `normalized_statement`: one precise mathematical sentence preserving the user's
  quantifiers and intent.
- `objects`: the mathematical objects that are in scope.
- `hypotheses`: each explicit or necessary inferred hypothesis, with status
  `explicit`, `inferred`, `missing`, or `unclear`.
- `conclusion`: the exact conclusion to be studied.
- `ambiguous_terms`: terms whose meaning could change the mathematical problem.
- `missing_or_unclear`: only real blockers to starting the workflow, not routine
  pedantic notes.
- `claim_precision`: `precise` only when the objects, hypotheses, and conclusion
  are specific enough for literature search, examples, counterexamples, and
  proof-route generation.
- `notes`: short audit notes explaining non-obvious interpretation choices.

## How To Generate The Claim Card

1. Identify the grammatical mathematical task first: universal theorem,
   existential theorem, counterexample request, construction request,
   computation request, classification, equivalence, implication, or open-ended
   exploration.
2. Preserve every quantifier and scope word exactly. Do not silently change
   `all` to `generic`, `exists` to `construct explicit`, `over Q` to another
   base field, or an abstract group statement to a specific representation.
3. Separate objects from hypotheses. An object names what is being discussed; a
   hypothesis is a condition assumed about it.
4. Separate the conclusion from the route. The conclusion is what must be shown;
   do not insert a proof method, source, example, or expected theorem into the
   conclusion unless the user stated it.
5. Expand compressed mathematical English into a precise statement only when the
   expansion is standard and forced by the words used. Record the expansion in
   `notes`. If two readings are genuinely possible, keep `claim_precision`
   conservative and list the ambiguity.
6. Treat notation and domain declarations as hypotheses or object definitions,
   not as evidence. Examples: base field, category, topology, coefficient ring,
   finiteness, smoothness, properness, genericity, and whether isomorphism is
   abstract or structure-preserving.
7. Use `missing_or_unclear` for blockers such as missing object, missing base
   field, unknown quantifier, undefined equivalence relation, or unclear target
   conclusion. Do not block just because a standard definition or theorem will
   later need a source.
8. Never mark the claim human-confirmed, source-backed, checked, proved,
   refuted, or likely true/false. The claim card is a scheduling and
   interpretation artifact only.

## Conservative Defaults

- If a standard mathematical phrase has one ordinary interpretation, lock that
  interpretation and note it.
- If a phrase could mean materially different problems, mark the relevant term
  ambiguous and keep `claim_precision` `imprecise`.
- If the user asks to prove a named object has a property, do not demand a full
  construction at claim-understanding time unless the property itself includes
  a construction requirement.
- If the user asks for a counterexample, the conclusion should be the requested
  failure mode, and admissibility of any candidate counterexample is checked
  later by the counterexample lane.

