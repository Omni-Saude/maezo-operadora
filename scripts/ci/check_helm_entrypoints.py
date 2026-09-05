#!/usr/bin/env python3
"""CI gate: every `python -m maezo.<module>` entrypoint the Helm chart / Terraform renders resolves
to a REAL importable module in `src/` (AF-01 / R-001).

Why this exists
----------------
`deploy/helm/maezo-tenant/values.yaml`'s `fhirSync`, `networkChangeBridge` and
`consentRevocationBridge` blocks each gate a Deployment whose `command` invokes a
`python -m maezo.<module>` entrypoint. Two of those modules (`network_change_bridge`,
`consent_revocation_bridge`) never existed in `src/maezo/platform/integrations/` while their
`values.yaml` flag defaulted to `enabled: true` — every pod would have hit `ModuleNotFoundError`
on first start (guaranteed CrashLoopBackOff). `fhirSync` had the identical defect and was already
flipped to `enabled: false` (t5-deploy-hygiene) with nothing mechanical stopping it — or a future
sibling — from being flipped back to `true`, or a NEW phantom-module template being added, without
anyone noticing until a real `helm install`.

This gate renders the chart (`helm template`, real binary, no mock) AND scans every
`deploy/**/*.tf` file, extracts every entrypoint either shape declares, and resolves each as a REAL
import via `importlib.util.find_spec` against the installed `maezo` package (editable-installed
from `src/` in this repo's venv — see `pyproject.toml`). A module that does not exist, or whose
parent package fails to import, fails the gate loudly and names the offending template/file
(`# Source: ...` for a Helm manifest, the `.tf` path otherwise) and, for Helm, the Kubernetes
resource.

What is covered, and what is not (gatekeeper finding F4)
-----------------------------------------------------------
An earlier revision matched only the literal quoted-list form `"python", "-m", "maezo.<module>"`
inside `command:` and claimed this "makes that class of defect impossible to reintroduce silently"
— false as written: it missed `sh -c`/`bash -c` shell-string invocations, `python3`, bare `-m` in
an `args:` list (no preceding interpreter token), and every `deploy/**/*.tf` container definition —
including THIS SAME PR's own `service-a2a-outbox-relay.tf`, which invokes the relay via
`command = ["sh", "-c", "... exec python -m maezo.a2a.outbox_relay"]`, a shape the old regex could
not see. `_ENTRYPOINT_RE` now matches, case-sensitively, in either a rendered Helm manifest or raw
`.tf` text:

  - the quoted-list form, `"python"`/`"python3"` optionally followed by `"-m"`, `"maezo.<module>"`
    (covers both `command:` and `args:` — the interpreter token is optional so a bare
    `["-m", "maezo.<module>"]` under `args:` with an image `ENTRYPOINT` supplying the interpreter
    still resolves); and
  - the plain shell-word form, `python`/`python3` followed by whitespace, `-m`, whitespace,
    `maezo.<module>` — the shape that appears verbatim inside a `sh -c`/`bash -c` string or a
    Terraform heredoc, since Helm/Terraform never re-quote a shell command's own internal tokens.

Still NOT covered, honestly: an entrypoint reached through a wrapper script file, an
`exec "$VAR"`-style indirection, a language/module name other than `maezo.<dotted>`, or any shape
this regex does not literally match. A rendered manifest or `.tf` sweep that extracts ZERO
entrypoints is treated as a hard failure (`EntrypointCheckResult.ok` requires `found` to be
non-empty) rather than a vacuous pass — the fence would rather fail loudly on its own extraction
breaking than silently stop checking anything.

Design
------
Mirrors `scripts/ci/check_start_process_fence.py`: a pure, dependency-light core
(`extract_entrypoints` + `extract_entrypoints_from_terraform` + `resolve_entrypoints`) that never
shells out, plus a thin `render_chart` subprocess wrapper and a CLI `main`. The pure core is
unit-testable against synthetic rendered-YAML/`.tf` fixtures (RED on an injected phantom module, one
test per covered shape, plus the vacuity test) independently of whether `helm` is on PATH; the
CLI-level tests additionally prove GREEN against the real chart with default values (both flags now
`false` — see AF-01) and RED when the two flags are forced back on via `--set` (proving the fence
would have caught the original defect, and catches any future regression to it), and prove the new
`deployment-a2a-outbox-relay.yaml` template (SC-01, `maezo.a2a.outbox_relay` — a REAL module,
covered via BOTH the Helm render and `service-a2a-outbox-relay.tf`'s `sh -c` form) is accepted.

Usage (CI / local)
-------------------
    python3 scripts/ci/check_helm_entrypoints.py
    python3 scripts/ci/check_helm_entrypoints.py --set networkChangeBridge.enabled=true \\
        --set consentRevocationBridge.enabled=true   # proves the fence catches a reintroduced defect
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Repo root, derived from this file's location (scripts/ci/<file> -> parents[2]).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Default chart + value-file(s) — the SAME render the `validate-helm` CI job already proves
#: succeeds (`ci.yml`'s "Helm template smoke test" step), so this gate never disagrees with it
#: about what a valid render looks like.
DEFAULT_CHART = "deploy/helm/maezo-tenant"
DEFAULT_RELEASE = "maezo-ci"
DEFAULT_VALUE_FILES: tuple[str, ...] = ("deploy/helm/maezo-tenant/values-amh.yaml",)
DEFAULT_TF_ROOT = "deploy"

#: The module-name shape every covered form must end in: `maezo` plus one-or-more `.segment`s.
_MODULE = r"maezo(?:\.[A-Za-z_][A-Za-z0-9_]*)+"

#: Matches EITHER of the two shapes the module docstring's "What is covered" section names:
#:   1. quoted-list form: `"python"`/`"python3"` (OPTIONAL — covers a bare `-m` under `args:` with
#:      no interpreter token of its own), `"-m"`, `"maezo.<module>"` — comma/quote-separated, as
#:      `helm template` emits a `command:`/`args:` flow-sequence written that way in the template
#:      source.
#:   2. plain shell-word form: `python`/`python3` (REQUIRED here — a bare `-m` as loose shell text
#:      with no interpreter is not a realistic invocation and would be indistinguishable from
#:      prose), whitespace, `-m`, whitespace, `maezo.<module>` — the shape that survives verbatim
#:      inside a `sh -c`/`bash -c` string (Helm/Terraform never re-quote a shell command's own
#:      internal tokens) or a Terraform heredoc.
#: Group 1 carries the module for shape 1, group 2 for shape 2 — `extract_entrypoints`/
#: `extract_entrypoints_from_terraform` read whichever fired.
_ENTRYPOINT_RE = re.compile(
    r'(?:"python3?"\s*,\s*)?"-m"\s*,\s*"(' + _MODULE + r')"' r"|python3?\s+-m\s+(" + _MODULE + r")"
)

#: `helm template` prefixes every rendered manifest with this comment naming its source template
#: (default behavior, no flag needed) — used to attribute each entrypoint match to a file for a
#: readable failure message.
_SOURCE_RE = re.compile(r"^#\s*Source:\s*(\S+)\s*$")


@dataclass(frozen=True)
class EntrypointRef:
    """One `python -m maezo.<module>` occurrence found in a rendered manifest."""

    module: str
    source_template: str | None


@dataclass
class EntrypointCheckResult:
    """Outcome of resolving every found entrypoint as a real import."""

    found: list[EntrypointRef] = field(default_factory=list)
    missing: list[EntrypointRef] = field(default_factory=list)
    errored: list[tuple[EntrypointRef, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # `not self.found` is a hard failure, never a vacuous pass (module docstring, gatekeeper
        # finding F4): an extraction sweep that finds NOTHING almost certainly means the regex (or
        # the render/scan itself) broke, not that the platform genuinely has zero entrypoints.
        return bool(self.found) and not self.missing and not self.errored

    def render(self) -> str:
        lines: list[str] = [
            f"check_helm_entrypoints: {len(self.found)} entrypoint(s) found, "
            f"{len(self.found) - len(self.missing) - len(self.errored)} resolved."
        ]
        if not self.found:
            lines.append(
                "  NO ENTRYPOINTS FOUND — refusing to pass vacuously. Either the chart/TF tree "
                "genuinely has zero `python -m maezo.<module>` invocations (verify by hand before "
                "trusting this), or extraction is broken."
            )
        for ref in self.missing:
            where = ref.source_template or "<unknown template>"
            lines.append(
                f"  PHANTOM MODULE: `python -m {ref.module}` ({where}) — "
                f"importlib.util.find_spec('{ref.module}') found nothing in src/."
            )
        for ref, error in self.errored:
            where = ref.source_template or "<unknown template>"
            lines.append(
                f"  IMPORT ERROR: `python -m {ref.module}` ({where}) — "
                f"resolving its parent package raised: {error}"
            )
        if self.ok:
            lines.append("  All entrypoints resolve to a real module. OK.")
        return "\n".join(lines)


def _module_from_match(match: re.Match[str]) -> str:
    """Either alternative of `_ENTRYPOINT_RE` can fire; group 1 is the quoted-list form's capture,
    group 2 the plain-shell-word form's — exactly one is non-`None` per match."""
    return match.group(1) or match.group(2)


def extract_entrypoints(rendered_text: str) -> list[EntrypointRef]:
    """Pure: find every covered `python -m maezo.<module>` shape in rendered Helm YAML text (see
    the module docstring's "What is covered" section for the exact shapes).

    Tracks the most recent `# Source: <template>` comment line so each match can be attributed to
    the template that emitted it (helm template's default per-manifest header — no `--debug`/extra
    flag required).
    """
    refs: list[EntrypointRef] = []
    current_source: str | None = None
    for line in rendered_text.splitlines():
        source_match = _SOURCE_RE.match(line.strip())
        if source_match:
            current_source = source_match.group(1)
            continue
        for match in _ENTRYPOINT_RE.finditer(line):
            refs.append(EntrypointRef(module=_module_from_match(match), source_template=current_source))
    return refs


def extract_entrypoints_from_terraform(tf_root: Path) -> list[EntrypointRef]:
    """Pure-ish (filesystem read only): find every covered entrypoint shape in `<tf_root>/**/*.tf`.

    ECS task definitions declare their `command` as raw text (often inside a `<<-SH ... SH`
    heredoc, e.g. `service-a2a-outbox-relay.tf`'s `exec python -m maezo.a2a.outbox_relay`) rather
    than as rendered Helm YAML, so this reuses the SAME `_ENTRYPOINT_RE` against the `.tf` file's
    own text, attributing each match to the file's repo-relative path (there is no per-manifest
    `# Source:` header to track, unlike `extract_entrypoints`).
    """
    refs: list[EntrypointRef] = []
    for tf_path in sorted(tf_root.rglob("*.tf")):
        text = tf_path.read_text(encoding="utf-8", errors="ignore")
        try:
            rel = str(tf_path.relative_to(REPO_ROOT))
        except ValueError:
            rel = str(tf_path)
        for line in text.splitlines():
            for match in _ENTRYPOINT_RE.finditer(line):
                refs.append(EntrypointRef(module=_module_from_match(match), source_template=rel))
    return refs


def resolve_entrypoints(refs: Sequence[EntrypointRef]) -> EntrypointCheckResult:
    """Pure (given an already-importable `maezo`): resolve each ref via `importlib.util.find_spec`.

    `find_spec` on a dotted name that does not exist returns `None` when the parent package DOES
    import cleanly (the common phantom-submodule case, e.g. `maezo.platform.integrations.
    network_change_bridge`) and raises `ModuleNotFoundError`/`ImportError` when a PARENT segment
    itself does not exist — both are treated as a hard failure, never silently skipped.
    """
    result = EntrypointCheckResult(found=list(refs))
    seen: set[str] = set()
    for ref in refs:
        if ref.module in seen:
            continue
        seen.add(ref.module)
        try:
            spec = importlib.util.find_spec(ref.module)
        except (ImportError, ModuleNotFoundError) as exc:
            result.errored.append((ref, f"{type(exc).__name__}: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 - any import-time exception is a hard failure here
            result.errored.append((ref, f"{type(exc).__name__}: {exc}"))
            continue
        if spec is None:
            result.missing.append(ref)
    return result


def render_chart(
    *,
    chart: str = DEFAULT_CHART,
    release: str = DEFAULT_RELEASE,
    value_files: Sequence[str] = DEFAULT_VALUE_FILES,
    set_overrides: Sequence[str] = (),
    repo_root: Path = REPO_ROOT,
) -> str:
    """Run `helm template` (the real binary) against the real chart and return its stdout.

    Raises `RuntimeError` (never silently returns partial/empty output) on a non-zero exit —
    a broken render is a hard failure for this gate, not a "nothing found" pass.
    """
    argv = ["helm", "template", release, chart]
    for vf in value_files:
        argv.extend(["-f", vf])
    for override in set_overrides:
        argv.extend(["--set", override])
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, helm is an explicit CI/dev dependency
        argv, cwd=repo_root, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"`{' '.join(argv)}` failed (exit {proc.returncode}):\n{proc.stderr}")
    return proc.stdout


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--chart", default=DEFAULT_CHART)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    parser.add_argument(
        "-f",
        "--values-file",
        dest="value_files",
        action="append",
        default=None,
        help=f"repeatable; default {list(DEFAULT_VALUE_FILES)}",
    )
    parser.add_argument(
        "--set",
        dest="set_overrides",
        action="append",
        default=[],
        help="repeatable `helm template --set` override (e.g. to force a gated flag on)",
    )
    parser.add_argument(
        "--tf-root",
        default=DEFAULT_TF_ROOT,
        help=f"scanned recursively for *.tf entrypoints too (default {DEFAULT_TF_ROOT!r})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    value_files = args.value_files if args.value_files is not None else list(DEFAULT_VALUE_FILES)
    try:
        rendered = render_chart(
            chart=args.chart,
            release=args.release,
            value_files=value_files,
            set_overrides=args.set_overrides,
        )
    except RuntimeError as exc:
        print(f"check_helm_entrypoints: helm template FAILED: {exc}", file=sys.stderr)
        return 1

    refs = extract_entrypoints(rendered) + extract_entrypoints_from_terraform(REPO_ROOT / args.tf_root)
    result = resolve_entrypoints(refs)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
