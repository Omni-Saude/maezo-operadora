"""SC-05 / R-026 (OWNER-DECISIONS-REGISTER, APROVADO-APOS-REVISAO-HUMANA): the RDS Proxy scaffold
in `deploy/terraform/modules/aurora-postgres/` must create ZERO AWS resources while
`enable_rds_proxy` stays at its default (`false`) — this is what "declarado, nao provisionado"
means operationally, and it is exactly the property `terraform plan` cannot show without real AWS
credentials, so this test proves it structurally instead.
"""

from __future__ import annotations

import re
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parents[3] / "deploy" / "terraform" / "modules" / "aurora-postgres"
_MAIN_TF = (_MODULE_DIR / "main.tf").read_text(encoding="utf-8")
_VARIABLES_TF = (_MODULE_DIR / "variables.tf").read_text(encoding="utf-8")
_OUTPUTS_TF = (_MODULE_DIR / "outputs.tf").read_text(encoding="utf-8")

#: Every resource this scaffold adds — all must be gated by the same flag.
_PROXY_RESOURCES: tuple[str, ...] = (
    'resource "aws_iam_role" "rds_proxy"',
    'resource "aws_iam_role_policy" "rds_proxy_secrets"',
    'resource "aws_db_proxy" "this"',
    'resource "aws_db_proxy_default_target_group" "this"',
    'resource "aws_db_proxy_target" "this"',
)


def test_enable_rds_proxy_variable_defaults_to_false() -> None:
    match = re.search(
        r'variable\s+"enable_rds_proxy"\s*\{[^}]*default\s*=\s*(true|false)', _VARIABLES_TF, re.DOTALL
    )
    assert match is not None, 'variable "enable_rds_proxy" not found'
    assert match.group(1) == "false", "enable_rds_proxy must default to false — no AWS resources yet"


def test_every_proxy_resource_exists() -> None:
    for needle in _PROXY_RESOURCES:
        assert needle in _MAIN_TF, f"missing {needle}"


_COUNT_GATE_RE = re.compile(r"count\s*=\s*var\.enable_rds_proxy\s*\?\s*1\s*:\s*0")


def test_every_proxy_resource_is_gated_by_the_same_flag() -> None:
    """RED proof: if a future edit adds a proxy resource without the `count` gate (or copies a
    DIFFERENT condition), this fails — every one of the five resources must share the identical
    `count = var.enable_rds_proxy ? 1 : 0` gate, so flipping one variable is genuinely all-or-
    nothing."""
    for needle in _PROXY_RESOURCES:
        start = _MAIN_TF.index(needle)
        block = _MAIN_TF[start : start + 400]
        assert _COUNT_GATE_RE.search(block), (
            f"{needle} is not gated by `count = var.enable_rds_proxy ? 1 : 0`"
        )


def test_proxy_reuses_the_existing_aurora_security_group_not_a_new_one() -> None:
    """No new security group is introduced — the proxy inherits `aws_security_group.aurora`
    (the same ingress already scoped to `var.allowed_security_group_ids`)."""
    start = _MAIN_TF.index('resource "aws_db_proxy" "this"')
    block = _MAIN_TF[start : start + 600]
    assert "aws_security_group.aurora.id" in block
    assert 'resource "aws_security_group"' not in _MAIN_TF[start:]


def test_output_uses_one_not_a_zero_index_so_it_never_crashes_while_disabled() -> None:
    """`aws_db_proxy.this[0]` in an output would make EVERY `terraform plan`/`apply` crash while
    `enable_rds_proxy=false` (count=0, no index 0 exists) — `one(...)` returns null instead."""
    assert "one(aws_db_proxy.this[*].endpoint)" in _OUTPUTS_TF
    assert "aws_db_proxy.this[0].endpoint" not in _OUTPUTS_TF


def _is_comment_line(text: str, offset: int) -> bool:
    line_start = text.rfind("\n", 0, offset) + 1
    line_prefix = text[line_start:offset]
    return line_prefix.lstrip().startswith("#")


def test_no_resource_references_terraform_output_index_zero_outside_a_gated_block() -> None:
    """Every REAL (non-comment) `aws_db_proxy...[0]` / `aws_iam_role.rds_proxy[0]` reference must
    live inside a resource block that ITSELF carries the same `count` gate (so it only evaluates
    when the referenced resource actually exists) — never in an ungated output or unrelated
    resource. Prose mentions inside `#` comments (this file has one, explaining the scaffold) are
    not code and are skipped."""
    for match in re.finditer(r"aws_(?:db_proxy\w*|iam_role\.rds_proxy)\.\w+\[0\]", _MAIN_TF):
        if _is_comment_line(_MAIN_TF, match.start()):
            continue
        # walk backward to the nearest preceding `resource "..." "..." {` and confirm it's gated
        preceding = _MAIN_TF[: match.start()]
        last_resource_start = preceding.rindex("resource ")
        block_head = _MAIN_TF[last_resource_start : match.start()]
        assert _COUNT_GATE_RE.search(block_head), (
            f"un-gated reference to {match.group(0)} at offset {match.start()}"
        )
