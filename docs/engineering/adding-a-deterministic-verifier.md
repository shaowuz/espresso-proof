# Adding a Deterministic Verifier

The most natural contribution to Espresso Proof is a new deterministic
verifier: a small, offline, side-effect-free function that checks one kind of
finite or computational fact, so runs about your area of mathematics can earn
`instance_checked` / `computation_checked` evidence instead of stopping at
candidate prose.

This guide walks the whole path using a shipped verifier as the worked example:
`permutation_group_witness` (order and derived series of a finite permutation
group), which lives at `espresso_proof/verification/finite_group_verifier.py`.

## The contract

A verifier earns a checked status only if the kernel can re-derive its result
during every `validate_run`. That imposes four hard requirements:

1. **Deterministic** — same input, same output, every time. No randomness, no
   wall-clock, no environment reads.
2. **Offline** — no network, no subprocess, no LLM. If your check needs a model
   or a website, it is not a verifier; it is an agent proposal.
3. **Side-effect free** — it must not write files. Recomputation runs inside
   validation, which is read-only by contract.
4. **Bounded** — explicit caps on input size / work, failing closed (returning
   "not confirmed", never hanging) on anything over the cap. Example:
   `MAX_GENERATED_GROUP_ORDER` in `finite_group_verifier.py`.

"Fail closed" is the house style everywhere: malformed input, an unsupported
case, or an over-cap request must yield a non-confirming result — never an
exception that a caller might interpret, and never a confirmation.

## Step 1 — write the verifier function

Put it in `espresso_proof/verification/`. Shape of the return value:

```python
def my_witness(data, *, expected_thing=None) -> dict:
    # 1. validate input types strictly (reject bool-as-int, strings, etc.)
    # 2. enforce your work cap
    # 3. compute
    return {
        "confirmed": bool(...),        # did every stated expectation hold?
        "transcript_sha256": ...,      # optional: hash of a canonical transcript
        "scope_note": "...",           # what this does and does NOT establish
        ...                            # any fields your entry builder needs
    }
```

The `scope_note` matters: it is rendered to the user, and it must state the
finite scope honestly ("covers THIS table only, never a universal claim").

Look at `permutation_group_witness` for the reference treatment of input
validation (0-indexed one-line images, strict `isinstance` checks), caps, and
the vacuity rule (a group of order < 3 has no non-trivial property to confirm,
so it refuses to earn anything).

## Step 2 — write the entry builder

An entry builder runs the verifier and shapes one `ProofVerificationEntry`
dict. See `build_permutation_group_entry` in
`espresso_proof/verification/proof_verification.py`. Non-negotiable fields:

- `adapter_id` — the registry name the kernel will re-run (step 3).
- `recompute_input` — everything the kernel needs to re-derive the result.
  Record the *data*, not a description of it.
- `target` — **machine-authored**. The requester's wording may only appear
  inside the `(requested for: ...)` note, via `requested_for_note()`. The
  kernel enforces this shape (`REQUESTED_FOR_SUFFIX` in
  `espresso_proof/kernel/recompute.py`) so a true check can never be published
  under a false description.
- `earned_status` — `"instance_checked"` on confirmation, `"unverified"`
  otherwise. Never anything else; the status ladder is closed.

## Step 3 — register the recomputer

Add your adapter to `_install_default_recomputers()` in
`espresso_proof/kernel/recompute.py`. The recompute function must:

- re-run the same verifier over `recompute_input`,
- return `confirmed`, `transcript_sha256` (if the verifier produces one), and
- return a **`canonical_target`** string — what the adapter says it checked.

The `canonical_target` is what binds an earned status to real work. An adapter
that omits it cannot earn a status at all: the validator fails closed
(`checked_entry_rejection_reason`), because opt-in binding is not binding.

## Step 4 — tests

Copy the pattern from `tests/kernel/test_forged_verification_rejected.py`:

- the legitimate path: your builder's entry passes
  `checked_entry_rejection_reason(entry) is None` and recomputes to the same
  transcript hash (`test_every_named_witness_is_recomputable_and_matches`
  auto-covers you if you register in `NAMED_FINITE_WITNESSES`);
- tampering: mutating `recompute_input` breaks the match;
- fail-closed: junk input, over-cap input, and a false expectation all yield
  `confirmed: False`;
- vacuity: if a degenerate input satisfies every expectation trivially, refuse
  to earn (see `test_a_vacuous_group_cannot_earn_a_checked_status`).

Also add your builder's target to
`test_every_shipped_builder_survives_its_own_binding_rule` so the target-binding
rule and your builder cannot drift apart.

## Step 5 — (optional) expose it to agents

If agents should be able to *request* your check, add a request kind or a named
witness in `espresso_proof/verification/proof_verification.py`
(`NAMED_FINITE_WITNESSES` / the `finite_group_from_generators` dispatch) and a
schema entry in `espresso_proof/kernel/models` (`ScopedVerificationRequest`).
Agent proposals are untrusted: they are schema-validated, id-rewritten, capped,
and only ever *executed* by the trusted backend — a proposal never earns
anything by existing.

## What will get your PR rejected

- A verifier that special-cases one named mathematical object or theorem. The
  registry is a general mechanism; per-problem answer keys have been removed
  from this codebase three times and the reviewers look for them specifically.
- A confirming path that trusts any field written by an agent or a human.
- Unbounded work, network access, or writes.
- A `target` assembled from requester free text.

Run the gate before opening a PR:

```bash
uv run pytest -q -n auto --dist loadfile
uv run espresso export-schemas --check
```
