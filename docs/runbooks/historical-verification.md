# Portable historical verification

Use the maintained consumers below for the frozen PR358 AB and PR356 ECS reviews. The original scripts and reports remain immutable historical evidence; their embedded checkout paths are not active execution instructions or retention requirements.

Run modules from a checkout containing these tools. Supply a clean source worktree at the exact historical revision, an explicit preserved evidence packet, and a fresh output outside both. Source revisions remain pinned; directory locations do not. Consumers verify source identity and cleanliness before and after execution. Reconstruct a temporary detached checkout from preserved Git objects when needed, and retire it after checking that it has no unique files or active consumers. Follow the mandatory maintenance procedure in CONTRIBUTING.md.

| Historical consumer | Maintained module | Required source revision |
| --- | --- | --- |
| PR358 AB `independent_controls.py` | `scripts.dev.verify_pr358_ab_delta` | `852d70588e732545471d052ebc2fa7fbc5840d5c` |
| PR356 `prove_scope.py` | `scripts.dev.verify_pr356_ecs_scope` | `f38ec6ad2639ad288611dae240e227057754f93d` |
| PR356 `test_ecs_interfaces.py` | `scripts.dev.verify_pr356_ecs_interfaces` | same PR356 revision |
| PR356 `source_custody.py` | `scripts.dev.capture_pr356_ecs_source` | same PR356 revision |
| PR356 `record.py` | `scripts.dev.record_pr356_ecs_command` | same PR356 revision |
| PR356 `test_delta_controls.py` | `scripts.dev.verify_pr356_ecs_delta_controls` | same PR356 revision |

Every module requires `--source`, `--evidence` and `--output`; `--help` lists additional arguments. AB evidence is the original canonical-groups/PHI model-repair author packet with its 72-member manifest and `OWNED-PATHS.txt`. ECS evidence is the preserved original ECS review packet; the recorder requires its `terraform-cli.tfrc`. Evidence remains private and is not copied into Git.

Example, with caller-selected absolute paths and an existing private output parent:

```sh
python -m scripts.dev.verify_pr358_ab_delta \
  --source "$AB_SOURCE" --evidence "$AB_EVIDENCE" \
  --output "$FRESH_OUTPUT/ab-controls.json"

python -m scripts.dev.capture_pr356_ecs_source \
  --source "$ECS_SOURCE" --evidence "$ECS_EVIDENCE" \
  --output "$FRESH_OUTPUT/source-before.json" --label source-before

python -m scripts.dev.record_pr356_ecs_command \
  --source "$ECS_SOURCE" --evidence "$ECS_EVIDENCE" \
  --output "$FRESH_OUTPUT/record" --label interpreter \
  "$EXPLICIT_PYTHON" -c 'import sys; print(sys.prefix)'

python -m scripts.dev.capture_pr356_ecs_source \
  --source "$ECS_SOURCE" --evidence "$ECS_EVIDENCE" \
  --output "$FRESH_OUTPUT/source-after.json" --label source-after \
  --before "$FRESH_OUTPUT/source-before.json"
```

`EXPLICIT_PYTHON` must be an absolute executable path. Its invocation path is preserved, including virtual-environment symlinks; the resolved binary and SHA-256 are recorded separately. Choose only the bounded verification needed for the current question. Offline scope/interface controls and interpreter checks do not establish engine, database, deployment or current-product acceptance.
