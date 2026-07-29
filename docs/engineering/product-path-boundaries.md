# product path boundaries

## canonical

```text
React cockpit -> espresso serve -> local server -> external Codex agent -> artifact kernel
```

Canonical files:

- `web/src/`
- `espresso_proof/server.py`
- `espresso_proof/ui/`
- `espresso_proof/orchestration/`
- `espresso_proof/kernel/`
- `espresso_proof/agents/codex_adapter.py`

## development support

- `uv run espresso export-schemas` writes ignored local JSON Schema snapshots from the package model registry.

## removed surfaces

The VS Code prototype, browser validation scripts, a root scripts directory, archived planning folders, checked-in seed projects, and checked-in example run artifacts were all removed and are not part of the current product surface. (`examples/quickstart.sh` is the one checked-in example script and stays — execution-trunk points at it.)

## cleanup rule

Clean old surfaces aggressively, but do not delete the artifact kernel, validators, stage runner, source/proof checks, or server path-confinement code as part of UI or documentation cleanup.
