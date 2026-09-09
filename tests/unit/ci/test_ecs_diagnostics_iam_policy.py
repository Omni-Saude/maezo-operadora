"""Complete inline-policy source checks for ADR-0006/0007/0049, PR357 F1-F4.

This is a deliberately limited offline IAM evaluator, not AWS policy simulation.
All statements are parsed; unsupported syntax/operators fail closed. Terraform's
rendered JSON is independently compared during source qualification. These tests
prove inline-policy decisions only, not deployed SCP/boundary/network/image state.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_POLICY = _ROOT / "deploy/aws-identity-center/permission-set-agent-engineer.tf"
_TASK = _ROOT / "deploy/aws-ecs/envs/dev-sa-east-1/task-diagnostics.tf"
_CLUSTER = "arn:aws:ecs:sa-east-1:123456789012:cluster/maezo-operadora-dev"
_TASK_ARN = "arn:aws:ecs:sa-east-1:123456789012:task/maezo-operadora-dev/diagnostic-id"
_FAMILY = "arn:aws:ecs:sa-east-1:123456789012:task-definition/maezo-operadora-dev-diagnostics:7"
_ROLE = "arn:aws:iam::123456789012:role/maezo-operadora-dev-diagnostics-execution"
_OWNER = "AROAEXAMPLE:engineer-a"
_BINDINGS = {
    "local.partition": "aws",
    "local.conta": "123456789012",
    "var.aws_region": "sa-east-1",
    "var.cluster_name": "maezo-operadora-dev",
}
_TOKEN = re.compile(r'\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|[a-zA-Z_][a-zA-Z_0-9]*|[][{}=,]')


def _body(source: str, header: str) -> str:
    start = source.index(header) + len(header)
    depth = 1
    position = start
    while depth:
        token = _TOKEN.match(source, position)
        assert token, f"Unsupported HCL at {source[position : position + 40]!r}"
        text = token.group()
        depth += (text == "{") - (text == "}")
        position = token.end()
    return source[start : position - 1]


def _parse_body(source: str) -> dict[str, Any]:
    tokens = []
    position = 0
    while position < len(source):
        match = _TOKEN.match(source, position)
        assert match, f"Unsupported policy HCL: {source[position : position + 40]!r}"
        position = match.end()
        if not match.group().isspace() and not match.group().startswith("#"):
            tokens.append(match.group())
    cursor = 0

    def value() -> Any:
        nonlocal cursor
        token = tokens[cursor]
        cursor += 1
        if token == "[":
            result = []
            while tokens[cursor] != "]":
                result.append(value())
                if tokens[cursor] == ",":
                    cursor += 1
            cursor += 1
            return result
        assert token.startswith('"'), f"Only literal policy values supported: {token}"
        return json.loads(token)

    def block() -> dict[str, Any]:
        nonlocal cursor
        result: dict[str, Any] = {}
        while cursor < len(tokens) and tokens[cursor] != "}":
            name, operator = tokens[cursor : cursor + 2]
            cursor += 2
            if operator == "=":
                assert name not in result, f"Duplicate attribute {name}"
                result[name] = value()
            else:
                assert operator == "{" and name in {"statement", "condition"}
                result.setdefault(name, []).append(block())
                assert tokens[cursor] == "}"
                cursor += 1
        return result

    result = block()
    assert cursor == len(tokens)
    return result


def _policy() -> list[dict[str, Any]]:
    parsed = _parse_body(_body(_POLICY.read_text(), 'data "aws_iam_policy_document" "agent_engineer" {'))
    assert set(parsed) == {"statement"}
    statements: list[dict[str, Any]] = parsed["statement"]
    for statement in statements:
        assert set(statement) <= {"sid", "effect", "actions", "resources", "not_resources", "condition"}
        assert statement["actions"] and bool(statement.get("resources")) != bool(
            statement.get("not_resources")
        )
        assert statement.get("effect", "Allow") in {"Allow", "Deny"}
        for condition in statement.get("condition", []):
            assert set(condition) == {"test", "variable", "values"}
    return statements


def _expand(value: str, context: dict[str, Any]) -> str:
    for name, replacement in _BINDINGS.items():
        value = value.replace("${" + name + "}", replacement)
    value = value.replace("&{aws:userid}", context.get("aws:userid", "<missing-userid>"))
    assert "${" not in value and "&{" not in value, f"Unbound policy variable: {value}"
    return value


def _condition(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    operator, key = condition["test"], condition["variable"]
    wanted = [_expand(value, context) for value in condition["values"]]
    actual = context.get(key)
    if operator == "Null":
        return str(actual is None).lower() in wanted
    if operator == "ForAllValues:StringEquals":
        assert actual is None or isinstance(actual, list)
        return all(value in wanted for value in actual or [])
    assert operator in {
        "StringEquals",
        "ArnEquals",
        "StringNotEquals",
        "ArnNotEquals",
        "StringNotEqualsIfExists",
    }
    assert actual is None or isinstance(actual, str)
    if "NotEquals" in operator:
        return actual not in wanted
    return actual is not None and actual in wanted


def _decision(action: str, resource: str, context: dict[str, Any] | None = None) -> str:
    context = {"aws:userid": _OWNER, **(context or {})}
    allowed = False
    for statement in _policy():
        if not any(fnmatch.fnmatchcase(action.lower(), pattern.lower()) for pattern in statement["actions"]):
            continue
        matches = any(
            fnmatch.fnmatchcase(resource, _expand(pattern, context))
            for pattern in statement.get("resources", statement.get("not_resources", []))
        )
        if matches == ("not_resources" in statement):
            continue
        if not all(_condition(condition, context) for condition in statement.get("condition", [])):
            continue
        if statement.get("effect", "Allow") == "Deny":
            return "explicitDeny"
        allowed = True
    return "allowed" if allowed else "implicitDeny"


def _run_context() -> dict[str, Any]:
    return {
        "ecs:cluster": _CLUSTER,
        "ecs:enable-execute-command": "false",
        "aws:RequestTag/MaezoPurpose": "diagnostics",
        "aws:RequestTag/MaezoOwner": _OWNER,
        "aws:TagKeys": ["MaezoPurpose", "MaezoOwner"],
    }


def _stop_context() -> dict[str, Any]:
    return {
        "ecs:cluster": _CLUSTER,
        "aws:ResourceTag/MaezoPurpose": "diagnostics",
        "aws:ResourceTag/MaezoOwner": _OWNER,
    }


def test_complete_diagnostics_launch_and_cleanup_is_allowed() -> None:
    assert _decision("ecs:RunTask", _FAMILY, _run_context()) == "allowed"
    assert _decision("iam:PassRole", _ROLE, {"iam:PassedToService": "ecs-tasks.amazonaws.com"}) == "allowed"
    assert (
        _decision("ecs:TagResource", _TASK_ARN, {**_run_context(), "ecs:CreateAction": "RunTask"})
        == "allowed"
    )
    assert _decision("ecs:StopTask", _TASK_ARN, _stop_context()) == "allowed"


@pytest.mark.parametrize(
    "family",
    [
        "bootstrap-db",
        "engine-bootstrap",
        "migrations",
        "deploy-processes",
        "agent-helena",
        "cibseven",
        "kafka",
        "diagnostics-evil",
    ],
)
def test_every_non_diagnostic_task_family_is_refused(family: str) -> None:
    resource = _FAMILY.replace("-diagnostics:7", f"-{family}:7")
    assert _decision("ecs:RunTask", resource, _run_context()) == "explicitDeny"


@pytest.mark.parametrize(
    "cluster",
    [
        _CLUSTER + "-foreign",
        _CLUSTER.replace("sa-east-1", "us-east-1"),
        _CLUSTER.replace("123456789012", "999999999999"),
        None,
    ],
)
def test_run_task_cluster_is_a_condition_not_an_extra_resource(cluster: str | None) -> None:
    context = _run_context()
    if cluster is None:
        del context["ecs:cluster"]
    else:
        context["ecs:cluster"] = cluster
    assert _decision("ecs:RunTask", _FAMILY, context) != "allowed"


@pytest.mark.parametrize(
    "key,value",
    [
        ("aws:RequestTag/MaezoOwner", "AROAEXAMPLE:engineer-b"),
        ("aws:RequestTag/MaezoPurpose", "workload"),
        ("aws:TagKeys", ["MaezoPurpose", "MaezoOwner", "admin"]),
        ("ecs:enable-execute-command", "true"),
    ],
)
def test_launch_rejects_forged_owner_extra_tags_and_exec(key: str, value: Any) -> None:
    assert _decision("ecs:RunTask", _FAMILY, {**_run_context(), key: value}) != "allowed"


@pytest.mark.parametrize(
    "key", ["aws:RequestTag/MaezoOwner", "aws:RequestTag/MaezoPurpose", "ecs:enable-execute-command"]
)
def test_launch_rejects_missing_required_context(key: str) -> None:
    context = _run_context()
    del context[key]
    assert _decision("ecs:RunTask", _FAMILY, context) != "allowed"


@pytest.mark.parametrize(
    "workload",
    [
        "kafka",
        "cibseven",
        "worker-runtime",
        "notifications-bridge",
        "cloudflared",
        "agent-helena",
        "bootstrap-db",
    ],
)
def test_stop_task_cannot_interrupt_a_workload(workload: str) -> None:
    assert (
        _decision("ecs:StopTask", _TASK_ARN.replace("diagnostic-id", workload), {"ecs:cluster": _CLUSTER})
        != "allowed"
    )


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"aws:ResourceTag/MaezoPurpose": "diagnostics"},
        {"aws:ResourceTag/MaezoOwner": _OWNER},
        {**_stop_context(), "aws:ResourceTag/MaezoOwner": "AROAEXAMPLE:other"},
        {**_stop_context(), "ecs:cluster": _CLUSTER + "-other"},
    ],
)
def test_stop_requires_both_immutable_owner_and_purpose_and_cluster(context: dict[str, Any]) -> None:
    assert _decision("ecs:StopTask", _TASK_ARN, context) != "allowed"


@pytest.mark.parametrize("action", ["ecs:TagResource", "ecs:UntagResource"])
def test_existing_workload_cannot_be_relabelled_for_cleanup(action: str) -> None:
    assert _decision(action, _TASK_ARN.replace("diagnostic-id", "kafka"), _run_context()) != "allowed"


@pytest.mark.parametrize("role", ["task", "task-execution", "bootstrap", "diagnostics-execution-evil"])
def test_shared_bootstrap_and_other_roles_cannot_be_passed(role: str) -> None:
    assert (
        _decision(
            "iam:PassRole",
            _ROLE.replace("diagnostics-execution", role),
            {"iam:PassedToService": "ecs-tasks.amazonaws.com"},
        )
        == "explicitDeny"
    )


@pytest.mark.parametrize("service", ["lambda.amazonaws.com", "ec2.amazonaws.com", None])
def test_diagnostic_role_cannot_be_passed_to_another_service(service: str | None) -> None:
    context = {} if service is None else {"iam:PassedToService": service}
    assert _decision("iam:PassRole", _ROLE, context) == "explicitDeny"


@pytest.mark.parametrize(
    "action",
    [
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:PutRolePolicy",
        "iam:UpdateAssumeRolePolicy",
        "iam:CreatePolicyVersion",
        "iam:CreateAccessKey",
        "iam:TagRole",
        "iam:DeleteRolePermissionsBoundary",
        "iam:CreateServiceLinkedRole",
        "iam:GetRole",
        "iam:FutureAction",
    ],
)
@pytest.mark.parametrize("resource", [_ROLE, "arn:aws:iam::123456789012:role/other", "*"])
def test_iam_self_promotion_and_all_non_passrole_iam_stay_explicitly_denied(
    action: str, resource: str
) -> None:
    assert _decision(action, resource) == "explicitDeny"


@pytest.mark.parametrize(
    "action",
    [
        "s3:GetObject",
        "rds:DescribeDBClusters",
        "rds-data:ExecuteStatement",
        "secretsmanager:GetSecretValue",
        "secretsmanager:PutSecretValue",
        "glue:GetTable",
        "athena:StartQueryExecution",
        "lakeformation:GetDataAccess",
        "dms:StartReplicationTask",
        "sso:CreatePermissionSet",
        "sso-directory:CreateUser",
        "identitystore:CreateUser",
        "organizations:CreateAccount",
        "ecs:RegisterTaskDefinition",
        "ecs:ExecuteCommand",
        "bedrock:PutModelInvocationLoggingConfiguration",
    ],
)
def test_hard_explicit_denials_are_preserved(action: str) -> None:
    assert _decision(action, "*") == "explicitDeny"


def test_exec_and_session_channels_are_unavailable_even_on_owned_diagnostics() -> None:
    assert _decision("ecs:ExecuteCommand", _TASK_ARN, _stop_context()) == "explicitDeny"
    assert _decision("ssmmessages:OpenDataChannel", "*") != "allowed"
    assert _decision("ssm:StartSession", _TASK_ARN) != "allowed"


def test_existing_agent_operation_and_model_invocation_remain_allowed() -> None:
    assert (
        _decision(
            "ecs:UpdateService", "arn:aws:ecs:sa-east-1:123456789012:service/maezo-operadora-dev/agent-helena"
        )
        == "allowed"
    )
    assert (
        _decision(
            "bedrock:InvokeModel", "arn:aws:bedrock:sa-east-1::foundation-model/anthropic.claude-sonnet"
        )
        == "allowed"
    )


def test_identity_center_is_in_the_terraform_validation_job() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("  validate-terraform:", 1)[1].split("  validate-helm:", 1)[0]
    assert "deploy/aws-identity-center" in job


def test_role_and_family_cannot_cross_account_region_or_prefix() -> None:
    for old, new in [
        ("123456789012", "999999999999"),
        ("sa-east-1", "us-east-1"),
        ("maezo-operadora-dev", "maezo-operadora-dev-other"),
    ]:
        assert _decision("ecs:RunTask", _FAMILY.replace(old, new), _run_context()) == "explicitDeny"
    assert (
        _decision(
            "iam:PassRole",
            _ROLE.replace("123456789012", "999999999999"),
            {"iam:PassedToService": "ecs-tasks.amazonaws.com"},
        )
        == "explicitDeny"
    )
    assert (
        _decision("ecs:StopTask", _TASK_ARN.replace("123456789012", "999999999999"), _stop_context())
        != "allowed"
    )


def test_diagnostics_execution_role_has_only_image_pull_and_own_log_writes() -> None:
    source = _TASK.read_text()
    role_policy = source.split('data "aws_iam_policy_document" "diagnostics_execution" {', 1)[1].split(
        'resource "aws_iam_role_policy"', 1
    )[0]
    # Replace only Terraform resource references, then parse every action and
    # resource. A new unknown action/reference must not disappear from the test.
    role_policy = role_policy.rsplit("}", 1)[0]
    role_policy = role_policy.replace("aws_ecr_repository.app.arn", '"app-ecr-arn"')
    role_policy = role_policy.replace("${aws_cloudwatch_log_group.diagnostics.arn}", "diagnostics-log-arn")
    statements = _parse_body(role_policy)["statement"]
    assert len(statements) == 3
    assert {action for statement in statements for action in statement["actions"]} == {
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }
    assert statements[0]["resources"] == ["*"]
    assert statements[0]["actions"] == ["ecr:GetAuthorizationToken"]
    assert statements[1]["resources"] == ["app-ecr-arn"]
    assert statements[2]["resources"] == ["diagnostics-log-arn:*"]
    assert not re.search(
        r"\btask_role_arn\s*=|aws_iam_role_policy_attachment|aws_secretsmanager|kms:", source
    )
    assert "assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json" in source


def test_task_is_fixed_nonroot_readonly_digest_pinned_and_has_no_injected_inputs() -> None:
    source = _TASK.read_text()
    assert 'family                   = "${local.name}-diagnostics"' in source
    assert "execution_role_arn       = aws_iam_role.diagnostics_execution.arn" in source
    assert (
        'entryPoint             = ["/usr/local/bin/python", "-I", "-S", "-c", local.diagnostics_probe]'
        in source
    )
    assert (
        'image                  = "${aws_ecr_repository.app.repository_url}@${var.diagnostics_image_digest}"'
        in source
    )
    assert "^sha256:[0-9a-f]{64}$" in source
    for key in ("secrets", "environment"):
        assert re.search(rf"\b{key}\s*=\s*\[\]", source)
    assert re.search(r'\buser\s*=\s*"1000:1000"', source)
    assert re.search(r"\breadonlyRootFilesystem\s*=\s*true", source)
    assert not re.search(r"\b(?:mountPoints|volumes|environmentFiles|repositoryCredentials)\s*=", source)
    assert 'drop = ["ALL"]' in source
    assert re.search(r'\bcommand\s*=\s*\["probe"\]', source)


def _probe_source() -> str:
    import textwrap

    source = _TASK.read_text()
    matched = re.search(r"diagnostics_probe = <<-PY\n(.*?)\n  PY", source, re.DOTALL)
    assert matched
    return textwrap.dedent(matched.group(1)).replace("${local.name}", "maezo-operadora-dev")


@pytest.mark.parametrize("refused", [False, True])
def test_fixed_probe_closes_two_connections_and_emits_only_status(
    refused: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    import builtins
    from types import SimpleNamespace

    calls: list[Any] = []
    environment = {
        "ENGINE_REST_URL": "http://attacker",
        "DATABASE_URL": "secret-canary",
        "PYTHONPATH": "/attacker",
    }

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: Any) -> None:
            calls.append("closed")

    def connect(address: tuple[str, int], *, timeout: int) -> Connection:
        calls.append((address, timeout))
        if refused:
            raise OSError("sensitive-exception-canary")
        return Connection()

    modules = {
        "os": SimpleNamespace(environ=environment),
        "sys": SimpleNamespace(argv=["-c", "probe"]),
        "json": json,
        "socket": SimpleNamespace(create_connection=connect),
        "signal": SimpleNamespace(
            SIGALRM=14, signal=lambda *args: None, alarm=lambda value: calls.append(("alarm", value))
        ),
    }

    def import_module(name: str, *args: Any, **kwargs: Any) -> Any:
        return modules[name]

    with pytest.raises(SystemExit) as error:
        exec(
            compile(_probe_source(), "fixed-diagnostics-probe", "exec"),
            {"__builtins__": {**vars(builtins), "__import__": import_module}},
        )
    assert error.value.code == (1 if refused else 0)
    assert environment == {}
    assert calls[0] == ("alarm", 20) and calls[-1] == ("alarm", 0)
    assert (("cibseven.maezo-operadora-dev.internal", 8080), 3) in calls
    assert (("kafka.maezo-operadora-dev.internal", 9092), 3) in calls
    assert calls.count("closed") == (0 if refused else 2)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records == [
        {"target": target, "dns_tcp": "unreachable" if refused else "reachable"}
        for target in ["engine", "broker"]
    ]


def test_fixed_probe_rejects_command_override_before_any_network(capsys: pytest.CaptureFixture[str]) -> None:
    import builtins
    from types import SimpleNamespace

    modules = {
        "os": SimpleNamespace(environ={}),
        "sys": SimpleNamespace(argv=["-c", "sh", "-c", "cat /secret"]),
        "json": json,
        "socket": SimpleNamespace(),
        "signal": SimpleNamespace(),
    }

    def import_module(name: str, *args: Any, **kwargs: Any) -> Any:
        return modules[name]

    with pytest.raises(SystemExit) as error:
        exec(
            compile(_probe_source(), "fixed-diagnostics-probe", "exec"),
            {"__builtins__": {**vars(builtins), "__import__": import_module}},
        )
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out) == {"status": "arguments_rejected"}


def test_diagnostic_logs_are_readable_but_engine_and_bootstrap_logs_are_not() -> None:
    prefix = "arn:aws:logs:sa-east-1:123456789012:log-group:/ecs/maezo-operadora-dev/"
    for action in ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:DescribeLogStreams"]:
        assert _decision(action, prefix + "diagnostics:log-stream:diagnostics/probe/id") == "allowed"
        for name in ["cibseven", "worker-runtime", "bootstrap-db"]:
            assert _decision(action, prefix + name + ":log-stream:other") != "allowed"
