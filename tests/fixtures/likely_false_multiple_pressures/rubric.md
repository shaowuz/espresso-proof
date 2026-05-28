# Likely False Multiple Pressures

This fixture checks that several independent structural pressures can push an idea to `likely_false` without inventing a fully checked concrete refutation.

Expected behavior:

- Grade must be `likely_false`, not `fragile`, `promising`, or `refuted`.
- Idea Grade must list at least two pressure candidates: `test_special_vs_very_general` and `test_specialization_invariance_pressure`.
- Counterexample tests should remain pressure tests unless all claim hypotheses, source grounding, and computations are checked. In particular, `test_special_vs_very_general` must not claim to refute the theorem.
- Strategy Brief should recommend repair, source pinning, or harder-example testing, using routes such as `strategy_narrow_genericity`, `strategy_pin_reference`, and `strategy_test_special_examples`.
- Strategy Brief and Final Report must not recommend proof completion or use proof-complete language.

Main-agent integration notes:

- Add `likely_false_multiple_pressures` to mock scenario detection.
- Implement a scenario artifact builder that returns `idea_grade.status: likely_false`, a score in the 20-35 range, and `proof_language_status: idea_sketch`.
- Include both counterexample candidates in `idea_grade.counterexample_candidates`.
- Include a second counterexample/pressure test with id `test_specialization_invariance_pressure`, `result: inconclusive`, `refutes_claim: false`, and `refutes_step_only: true`.
- Reuse the existing Strategy Brief route ids listed above, but set route status to `likely_false` and keep `next_loop_plan` focused on narrowing the claim, pinning the transfer theorem, or testing harder special examples.
