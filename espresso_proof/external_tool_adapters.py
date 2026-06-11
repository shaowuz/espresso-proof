"""Goal 25 (slice 3): one allowlisted deterministic adapter.

An adapter wraps an allowlisted deterministic external tool, records its audited
run (slice 1/2), and -- ONLY on a confirmed deterministic result -- earns a
SCOPED ``computation_checked`` proof-verification entry through the existing
kernel bridge (``append_verification_entries``). The kernel decides whether the
entry is admissible; the adapter cannot bypass it.

The smallest stable tool available in every environment is Python's own exact
integer arithmetic, so the bundled adapter is ``exact_modexp`` (exact modular
exponentiation). It is a REAL deterministic computation -- not a stub -- so a
confirmed run honestly earns scoped computation evidence for THAT specific
arithmetic fact, never a universal proof. Arbitrary GitHub/PyPI/Conda tools are
NOT runnable (only the allowlisted tools in external_tool_runner.FAKE_TOOLS) and
can never earn anything.

Hard guarantees:
- earning happens ONLY through the kernel-gated proof-verification bridge;
- a non-confirming result, a failed/timed-out run, or a tool/adapter that is not
  allowlisted earns NOTHING (no proof-verification entry);
- the earned status is the SCOPED ``computation_checked`` (this computation only),
  rendered by the kernel display as a scoped instance, never a global proof.
"""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path

from .external_tool_runner import (
    run_and_record_external_tool,
    run_completed_cleanly,
)
from .io import read_yaml
from .proof_verification import append_verification_entries


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("espresso-proof")
    except Exception:  # noqa: BLE001 - provenance only; never block earning
        return "unknown"


def run_exact_modexp_adapter(
    run_dir: Path,
    *,
    base: int,
    exp: int,
    mod: int,
    expected: int,
    entry_id: str,
    statement_checked: str = "",
    timeout_seconds: float = 10.0,
) -> dict:
    """Run the allowlisted exact modular-exponentiation tool and, IF it confirms
    ``base**exp mod mod == expected``, earn a SCOPED computation_checked entry via
    the kernel bridge. Returns ``{run, earned, entry_id, detail}``.

    Fails closed: a non-integer operand, ``mod < 1`` or ``exp < 0``, a failed /
    timed-out run, unparseable output, or a result that does not match BOTH the
    authoritative computation AND ``expected`` earns nothing.
    """
    for name, value in (("base", base), ("exp", exp), ("mod", mod), ("expected", expected)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"exact_modexp operand {name} must be an int, got {value!r}")
    if mod < 1 or exp < 0:
        raise ValueError("exact_modexp requires mod >= 1 and exp >= 0")

    run = run_and_record_external_tool(
        run_dir,
        "exact_modexp",
        entry_id=entry_id,
        statement_checked=statement_checked or f"{base}^{exp} mod {mod} == {expected}",
        extra_args=[str(base), str(exp), str(mod)],
        timeout_seconds=timeout_seconds,
    )

    # Authoritative deterministic computation (the trusted exact-arithmetic backend).
    authoritative = pow(base, exp, mod)
    clean = run_completed_cleanly(run.timed_out, run.exit_code)
    tool_value: int | None = None
    if clean and run.parsed_result:
        try:
            tool_value = int(run.parsed_result.strip())
        except (TypeError, ValueError):
            tool_value = None
    confirmed = clean and tool_value == authoritative and authoritative == expected

    earned = False
    if confirmed:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
        entry = {
            "id": f"{entry_id}_pv",
            "verifier_type": "exact_computation",
            "claim_id": claim_id,
            "target": f"exact value of {base}^{exp} mod {mod}",
            "input_summary": f"exact_modexp(base={base}, exp={exp}, mod={mod}); expected={expected}",
            "result": f"{base}^{exp} mod {mod} = {authoritative} (== expected)",
            "trusted": True,
            "confirmed": True,
            "earned_status": "computation_checked",
            "scope_note": (
                f"Exact integer modular exponentiation: {base}^{exp} mod {mod} = "
                f"{authoritative}. This single arithmetic computation only; NOT a "
                "universal proof of any claim."
            ),
            # Goal 38 reproducibility provenance (never earns/upgrades status):
            "adapter_id": "exact_modexp",
            "code_version": f"espresso-proof {_package_version()}",
            "dependency_version": f"python {platform.python_version()} pow()",
            "input_hash": _sha256(f"exact_modexp|{base}|{exp}|{mod}|{expected}"),
            "output_hash": _sha256(str(authoritative)),
            "replay_command": (
                f"espresso verify-exact-modexp --run <run_dir> --base {base} "
                f"--exp {exp} --mod {mod} --expected {expected} --id {entry_id} "
                "(replaying into the same run needs a fresh --id; identical "
                "input/output hashes confirm the reproduction)"
            ),
        }
        # Earn ONLY through the bridge: append_verification_entries persists the
        # entry and calls validate_run, whose _validate_proof_verification gate
        # re-checks trusted + confirming + verifier_type->earned and fails closed.
        # The adapter only ever builds the one admissible combination, above.
        append_verification_entries(run_dir, [entry])
        earned = True

    return {
        "run": run,
        "earned": earned,
        "entry_id": f"{entry_id}_pv" if earned else None,
        "detail": (
            f"confirmed {base}^{exp} mod {mod} = {authoritative}"
            if earned
            else "not confirmed: earned no status (fail-closed)"
        ),
    }


__all__ = ["run_exact_modexp_adapter"]
