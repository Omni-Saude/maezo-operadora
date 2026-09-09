"""Executa uma lane pytest live com evidência verificável e falha fechada.

O subcomando ``collect`` fixa a coleção, as identidades JUnit, os marcadores e os
hashes das fontes antes de subir os serviços. O subcomando ``run`` executa a
mesma seleção com prova das fases e de entrada no corpo, confronta o resultado
com o manifesto fixado e grava um relatório de validação legível por máquina.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from scripts.ci.pytest_execution_evidence import (
        SCHEMA_VERSION,
        EvidencePlugin,
        public_junit_identity,
        redact,
        validate_execution,
    )
else:
    from pytest_execution_evidence import (
        SCHEMA_VERSION,
        EvidencePlugin,
        public_junit_identity,
        redact,
        validate_execution,
    )


def _pytest_args(raw: Sequence[str]) -> list[str]:
    args = list(raw)
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        raise ValueError("argumentos do pytest ausentes depois de `--`")
    forbidden = ("--collect-only", "--junitxml", "--junit-xml")
    if any(arg == flag or arg.startswith(f"{flag}=") for arg in args for flag in forbidden):
        raise ValueError("o wrapper controla --collect-only e --junitxml")
    return args


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON deve ser objeto: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_stale(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


@contextmanager
def _private_pytest_output(anchor: Path) -> Iterator[Path]:
    """Retém saída Python e FD (incluindo filhos) fora das superfícies públicas.

    Cada invocação tem custódia própria; nenhum texto arbitrário é reconstruído
    por regex para o console. O log inclui traceback e permanece disponível ao
    operador local. O workflow publica somente os quatro artefatos nomeados.
    """
    anchor.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=f".{anchor.name}.pytest-", dir=anchor.parent))
    with ExitStack() as stack:
        fd = os.open(directory / "output.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stream = stack.enter_context(os.fdopen(fd, "w", encoding="utf-8"))
        sys.stdout.flush()
        sys.stderr.flush()
        for target in (1, 2):
            saved = os.dup(target)
            stack.callback(os.close, saved)
            stack.callback(os.dup2, saved, target)
            os.dup2(stream.fileno(), target)
        stack.enter_context(redirect_stdout(stream))
        stack.enter_context(redirect_stderr(stream))
        try:
            yield directory
        except BaseException:
            traceback.print_exc(file=stream)
            raise
        finally:
            stream.flush()


def _print_collection(items: list[dict[str, Any]]) -> None:
    for item in items:
        print(f"[live-pytest] caso: {item['nodeid']} sha256={item['nodeid_sha256']}")


def _safe_exception(context: str, exc: BaseException) -> str:
    """Não publica conteúdo arbitrário de exceções; conserva tipo e correlação."""
    digest = hashlib.sha256(str(exc).encode("utf-8")).hexdigest()
    return f"{context}: {type(exc).__name__} sha256={digest}"


def _publication_context(source: Path) -> Callable[[str], str]:
    """Propaga somente valores privados conhecidos; o XML original é autoridade."""
    tree = ET.parse(source)
    secrets: set[str] = set()

    def remember(value: str) -> None:
        if value:
            secrets.update((value, repr(value)[1:-1], json.dumps(value)[1:-1]))

    for prop in tree.iter("property"):
        name = prop.get("name", "")
        context = f'{json.dumps(name)}: "pytest-property-value"'
        if redact(context) == f'{json.dumps(name)}: "<redacted>"':
            remember(prop.get("value", ""))
    for case in tree.iter("testcase"):
        for key in ("classname", "name"):
            _, marker, parameter = case.get(key, "").partition("[")
            if marker:
                parameter = parameter.removesuffix("]")
                remember(parameter)
                # Pytest escapa IDs Unicode/controle; decodifica escapes individuais
                # sem reinterpretar caracteres Unicode que já chegaram literais.
                remember(
                    re.sub(
                        r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[\\abfnrtv])",
                        lambda match: codecs.decode(match[0], "unicode_escape"),
                        parameter,
                    )
                )
    literals = (
        re.compile("|".join(re.escape(value) for value in sorted(secrets, key=len, reverse=True)))
        if secrets
        else None
    )

    def diagnostic(value: str) -> str:
        safe = literals.sub(lambda _: "<redacted>", value) if literals else value
        return redact(safe)

    return diagnostic


def _project_narratives(payload: Any, diagnostic: Callable[[str], str], *, narrative: bool = False) -> Any:
    """Preserva autoridade estrutural/identidades; projeta folhas narrativas."""
    if isinstance(payload, dict):
        return {
            key: _project_narratives(
                value,
                diagnostic,
                narrative=key
                in {
                    "skip_reason",
                    "wasxfail",
                    "errors",
                    "collection_error",
                    "reason",
                },
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_project_narratives(value, diagnostic, narrative=narrative) for value in payload]
    return diagnostic(payload) if narrative and isinstance(payload, str) else payload


def _withhold_narrative(value: str) -> str:
    """A coleta não tem contexto do corpo para classificar razões livres."""
    return "<redacted: contexto indisponivel>" if value else value


def _collection_projection(payload: dict[str, Any]) -> dict[str, Any]:
    items = []
    for original in payload["collection"]:
        item = dict(original)
        for key in ("xfail", "inactive_companion"):
            marker = item.get(key)
            if marker is not None and "reason" in marker:
                item[key] = {**marker, "reason": _withhold_narrative(marker["reason"])}
        items.append(item)
    return {**payload, "collection": items}


def _private_json_write(path: Path, payload: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")


def _custody_binding(output: Path, root: Path, reference: dict[str, str]) -> dict[str, Any]:
    return {"schema": 1, "manifest": str(output.resolve()), "root": str(root.resolve()), **reference}


def _private_bytes(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError("arquivo de custodia deve ser regular privado 0600")
        return stream.read()


def _expected_original(output: Path, root: Path) -> dict[str, Any]:
    """Autentica a ligação local; não é assinatura contra o próprio usuário OS.

    O manifesto público nunca se torna expected_items. Digest e parse usam os
    mesmos bytes privados, e a projeção inteira deve coincidir com o manifesto.
    A mesma coleta íntegra pode ser executada novamente.
    """
    if not stat.S_ISREG(output.lstat().st_mode):
        raise ValueError("manifesto deve ser arquivo regular sem symlink")
    public = _read_json(output)
    reference = public.get("private_collection")
    if (
        not isinstance(reference, dict)
        or set(reference) != {"directory", "sha256"}
        or not all(isinstance(value, str) for value in reference.values())
        or re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]) is None
    ):
        raise ValueError("referencia de custodia ausente ou invalida")
    name = reference["directory"]
    if Path(name).name != name or not name.startswith(f".{output.name}.pytest-"):
        raise ValueError("referencia de custodia fora do confinamento")
    directory = output.parent.resolve() / name
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise ValueError("diretorio de custodia deve ser privado 0700 sem symlink")
    binding = json.loads(_private_bytes(directory / "binding.json"))
    if binding != _custody_binding(output, root, reference):
        raise ValueError("vinculo da coleta diverge do root, manifesto ou instancia")
    raw = _private_bytes(directory / "collection.json")
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("bytes da coleta divergem do digest fixado")
    original = json.loads(raw)
    if not isinstance(original, dict):
        raise ValueError("coleta original deve ser objeto")
    projected = {**_collection_projection(original), "private_collection": reference}
    if projected != public:
        raise ValueError("projecao da coleta diverge do manifesto publico")
    return original


def _publish_sanitized_junit(private_path: Path, public_path: Path) -> None:
    """Projeta identidades do XML já validado e publica conteúdo seguro."""
    tree = ET.parse(private_path)
    diagnostic = _publication_context(private_path)
    for prop in tree.iter("property"):
        name = prop.get("name", "")
        context = f'{json.dumps(name)}: "pytest-property-value"'
        if redact(context) == f'{json.dumps(name)}: "<redacted>"':
            prop.set("value", "<redacted>")
    for element in tree.getroot().iter():
        if element.tag == "testcase":
            identity = public_junit_identity(
                element.get("classname", ""),
                element.get("name", ""),
            )
            element.set("classname", identity["classname"])
            element.set("name", identity["name"])
            element.set("junit_identity_sha256", identity["junit_identity_sha256"])
        for name, value in tuple(element.attrib.items()):
            if element.tag == "property" and name == "value" and value == "<redacted>":
                continue

            if element.tag == "testcase" and name in {"classname", "name", "junit_identity_sha256"}:
                continue  # Fingerprints/identidade vêm exclusivamente do par original.
            # Metadados estruturais (contagens, tempo, linha) não são narrativas.
            if name in {"tests", "errors", "failures", "skipped", "time", "timestamp", "line"}:
                element.set(name, redact(value))
            else:
                element.set(name, diagnostic(value))
        if element.text:
            element.text = diagnostic(element.text)
        if element.tail:
            element.tail = diagnostic(element.tail)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = public_path.with_name(f".{public_path.name}.publishing")
    temporary.unlink(missing_ok=True)
    try:
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        temporary.replace(public_path)
    finally:
        temporary.unlink(missing_ok=True)


def collect(output: Path, root: Path, raw_pytest_args: Sequence[str]) -> int:
    _remove_stale(output)
    args = _pytest_args(raw_pytest_args)
    try:
        with _private_pytest_output(output) as private:
            raw_evidence = private / "collection.json"
            raw_evidence.touch(mode=0o600)
            plugin = EvidencePlugin(output=raw_evidence, root=root)
            pytest_rc = int(pytest.main([*args, "--collect-only"], plugins=[plugin]))
    except BaseException as exc:
        _remove_stale(output)
        print(f"[live-pytest] FAIL coleta: {_safe_exception('pytest interrompido', exc)}", file=sys.stderr)
        return 3
    if pytest_rc != 0:
        print(
            f"[live-pytest] FAIL coleta: pytest rc={pytest_rc}; diagnostico em custodia privada",
            file=sys.stderr,
        )
        return pytest_rc
    try:
        original_bytes = _private_bytes(raw_evidence)
        evidence = json.loads(original_bytes)
        items = evidence["collection"]
        nodeids = [item["nodeid"] for item in items]
        if (
            evidence.get("schema") != SCHEMA_VERSION
            or evidence.get("finished") is not True
            or evidence.get("pytest_exitstatus") != pytest_rc
            or evidence.get("reports") != []
            or not nodeids
            or len(nodeids) != len(set(nodeids))
        ):
            raise ValueError("manifesto de coleta vazio, duplicado, incompleto ou inconsistente")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _remove_stale(output)
        print(f"[live-pytest] FAIL coleta: {_safe_exception('manifesto invalido', exc)}", file=sys.stderr)
        return 3
    try:
        reference = {"directory": private.name, "sha256": hashlib.sha256(original_bytes).hexdigest()}
        _private_json_write(private / "binding.json", _custody_binding(output, root, reference))
        _write_json(output, {**_collection_projection(evidence), "private_collection": reference})
    except BaseException as exc:
        _remove_stale(output)
        print(f"[live-pytest] FAIL coleta: {_safe_exception('publicacao recusada', exc)}", file=sys.stderr)
        return 3
    _print_collection(items)
    print(f"[live-pytest] coleta fixada: {len(nodeids)} casos em {output}")
    return 0


def run(
    expected_path: Path,
    evidence_path: Path,
    junit_path: Path,
    validation_path: Path,
    root: Path,
    raw_pytest_args: Sequence[str],
) -> int:
    private_junit_path = junit_path.with_name(f".{junit_path.name}.private")
    publishing_path = junit_path.with_name(f".{junit_path.name}.publishing")
    validation_temporary = validation_path.with_name(f".{validation_path.name}.tmp")
    _remove_stale(
        evidence_path,
        private_junit_path,
        publishing_path,
        junit_path,
        validation_temporary,
        validation_path,
    )
    args = _pytest_args(raw_pytest_args)
    try:
        expected = _expected_original(expected_path, root)
        expected_items = expected["collection"]
        if (
            expected.get("schema") != SCHEMA_VERSION
            or expected.get("finished") is not True
            or expected.get("pytest_exitstatus") != 0
            or expected.get("reports") != []
            or not isinstance(expected_items, list)
            or not expected_items
        ):
            raise ValueError("manifesto de coleta não é uma coleta completa e não vazia")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[live-pytest] FAIL manifesto: {_safe_exception('manifesto invalido', exc)}", file=sys.stderr)
        return 3

    pytest_rc = 3
    validation_completed = False
    try:
        with _private_pytest_output(evidence_path) as private:
            # O produtor não deve criar o XML cru com a umask pública do job.
            private_junit_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(private_junit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            raw_evidence = private / "execution.json"
            raw_evidence.touch(mode=0o600)
            plugin = EvidencePlugin(output=raw_evidence, root=root)
            pytest_rc = int(pytest.main([*args, f"--junitxml={private_junit_path}"], plugins=[plugin]))
        evidence = _read_json(raw_evidence)
        report = validate_execution(private_junit_path, evidence, expected_items, pytest_rc)
        validation_completed = True
    except BaseException as exc:
        report = {
            "return_code": pytest_rc or 3,
            "errors": [_safe_exception("validacao interrompida", exc)],
        }
    try:
        if validation_completed:
            try:
                diagnostic = _publication_context(private_junit_path)
                safe_evidence = _collection_projection(_project_narratives(evidence, diagnostic))
                report = _project_narratives(report, diagnostic)
                _publish_sanitized_junit(private_junit_path, junit_path)
                _write_json(evidence_path, safe_evidence)
            except BaseException as exc:
                # A projeção pode falhar antes de substituir o relatório original.
                # Nenhuma narrativa desse original pode chegar ao fallback público.
                report = {
                    "return_code": pytest_rc or 3,
                    "errors": [_safe_exception("publicacao recusada", exc)],
                }
                _remove_stale(junit_path, evidence_path)
    finally:
        private_junit_path.unlink(missing_ok=True)
        publishing_path.unlink(missing_ok=True)
    try:
        _write_json(validation_path, report)
    except OSError as exc:
        validation_path.unlink(missing_ok=True)
        print(f"[live-pytest] FAIL: {_safe_exception('relatorio nao publicado', exc)}", file=sys.stderr)
        return pytest_rc or 3

    return_code = int(report.get("return_code", 3))
    if return_code:
        for error in report.get("errors", ["relatório sem lista de erros"]):
            print(f"[live-pytest] FAIL: {redact(str(error))}", file=sys.stderr)
        print(f"[live-pytest] relatório: {validation_path}", file=sys.stderr)
        return return_code
    counts = report["case_counts"]
    print(
        "[live-pytest] PASS: "
        f"passed={counts.get('passed', 0)}, "
        f"xfailed_executed={counts.get('xfailed_executed', 0)}, "
        f"inactive_companion={counts.get('inactive_companion', 0)}; "
        f"relatório={validation_path}"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--expected", type=Path, required=True)
    run_parser.add_argument("--evidence", type=Path, required=True)
    run_parser.add_argument("--junit", type=Path, required=True)
    run_parser.add_argument("--validation", type=Path, required=True)
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "collect":
            return collect(args.output, args.root, args.pytest_args)
        return run(
            args.expected,
            args.evidence,
            args.junit,
            args.validation,
            args.root,
            args.pytest_args,
        )
    except ValueError as exc:
        print(f"[live-pytest] FAIL argumentos: {redact(str(exc))}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
