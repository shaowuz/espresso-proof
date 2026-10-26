# Proof repairs and evidence

Use this distinction before filling an omitted argument. It governs manuscript
editing and advisory issue records; it does not change Espresso kernel statuses.

## Three kinds of change

| Kind | Examples | Treatment |
| --- | --- | --- |
| Editorial reconstruction | Move a definition, name a cited input, reorder an existing argument, expand an unambiguous abbreviation | Revise directly within scope and preserve the original mathematical content. |
| Routine deductive expansion | Show the displayed algebraic identity, verify a stated map's type, write a finite deduction from explicit premises | Give the actual steps. Record an expansion that fills an omission; do not describe it as an independently verified new theorem. |
| Nontrivial proposed repair | Introduce a new lemma, comparison theorem, gluing argument, convergence justification, or hypothesis not supplied in the proof | Give it a repair ID and keep it as a candidate until the needed evidence is recorded. |

Length is not the criterion. A one-line appeal to a deep theorem can be a
nontrivial repair; a longer expansion of an existing identity may be routine.
If the classification is uncertain, use proposed repair.

## Candidate repair record

For each nontrivial repair, record the original gap ID, original claim, proposed
argument, all assumptions and new inputs, changed conclusions if any, dependent
results, and evidence still needed. Keep the candidate in the author note or a
clearly labeled proposed-argument section. Leave a short pointer at the gap.
Do not silently paste the candidate into an apparently settled proof.

Distinguish these advisory states:

- **Open:** the needed implication has not been established.
- **Repair proposed:** an inspectable candidate exists; correctness is not assumed.
- **Reviewed:** a separate review occurred; record who or what reviewed which
  revision, the review's scope, objections, and remaining limitations.
- **Resolved with evidence:** record the specific argument and evidence addressing
  the original obligation. A status word or a favorable review is not the evidence.
- **Refuted or superseded:** preserve the counterexample or replacement argument
  and explain what happened to the original claim.

The generating model's self-assessment cannot promote a nontrivial candidate to
resolved. A second model's agreement is additional review evidence, not a proof
certificate. Keep such a candidate reviewed/proposed unless stronger evidence
addresses the actual obligation. Relevant evidence may include an applicable
original result checked against the exact hypotheses, a formal certificate for
the matching statement, or an identified human mathematical review. Record
limitations: a finite computation is not automatically evidence for a universal
claim, and human acceptance alone does not make a false derivation valid.

If the author explicitly wants a candidate-based draft, it may include the
proposed argument with its provisional label and unresolved issue pointer.
Do not imply that author acceptance or integration into prose establishes truth.

## Preserve the statement and the problem

Do not resolve an original unconditional claim by silently adding hypotheses,
weakening its conclusion, or assuming the missing lemma. Identify a replacement
claim separately and explain whether the original goal remains open or is refuted.
When a proof route remains conditional, make that limitation visible without
misrepresenting the conditional statement as the original theorem.

A counterexample closes investigation of that false statement as a refutation;
it does not leave an obligation to prove it. Reordering mutually dependent lemmas
does not break a circular argument. An unavailable citation is still unverified.

If no further mathematical evidence is available, finish the authorized editorial
work and deliver the candidate and exact outstanding obligation. Do not run
indefinite review rounds until a model eventually agrees.
