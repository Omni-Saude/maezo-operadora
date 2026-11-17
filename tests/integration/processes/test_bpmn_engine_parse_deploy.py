"""EP-PROC-04 (E-SF-31) — deploy SO'-PARA-PARSE de cada `.bpmn`/`.dmn` na engine real e pinada.

O QUE PROVA. Criterio 5 do registro PROC-04 (DEC-PROC): "Deploy sem erro de parse na imagem pinada
(...) O corpus CA faz deploy so' para parse." Cada artefato vai SOZINHO num deployment proprio, para
que um erro de parse aponte o arquivo exato (o `make deploy-artifacts` manda tudo junto e o primeiro
erro esconde o resto). Para BPMN, alem do 2xx, a prova exige que o engine tenha CRIADO a definicao de
cada processo `isExecutable="true"` do arquivo — um 2xx que nao registrou nada nao conta como parse.

ENGINE. Real, sem mock (AGENTS.md regra 3; ADR-0011), na URL de `ENGINE_REST_URL` (mesma resolucao
de `maezo.platform.deploy.engine_deploy`). Cliente REST REUSADO: `EngineRest` de
`tests/integration/processes/engine_rest.py`, o mesmo das suites SP-OP. A imagem e' a do digest
`cibseven/cibseven:2.1.0@sha256:20a2135b…` (docker-compose.yml / deploy/cibseven/Dockerfile;
cercado em `test_bpmn_gates_harness.py`). Engine fora do ar FALHA (nao pula): este modulo so' e'
coletado com `-m integration`, e nessa selecao a engine e' pre-condicao, nao opcao.

ISOLAMENTO. O engine da lane de integracao e' COMPARTILHADO pelo shard (`restante` do ci.yml, que
tambem roda `make deploy-artifacts` no tenant `amh`). Para nao virar a `latestVersion` de ninguem:
(1) cada deploy vai para um tenant proprio (`bpmngatesparse`), e (2) o deployment e' apagado com
`cascade=true` logo depois da verificacao, no `finally` — o engine volta ao estado anterior.

REJEICOES CONHECIDAS (revisao F1, 16/11/2026). CA_0, CA_1 e CA_3 do corpus CA tem serviceTasks sem
binding por desenho ("Binding de implementacao nao fornecido no corpus") e o cibseven 2.1.0 recusa
o deploy com ENGINE-09005. Nao e' xfail nem skip: o contrato para esses arquivos e' EXATAMENTE essa
rejeicao, nas linhas/ids das entradas `G2-IMPL` de `scripts/bpmn-gates/baseline.json` (lidas do
JSON, nao copiadas). Erro diferente, a mais ou a menos reprova; deploy ACEITO tambem reprova
("remova a entrada da baseline"). Os demais artefatos seguem exigindo deploy ok.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from lxml import etree

from maezo.platform.deploy.engine_deploy import collect_artifacts, resolve_engine_rest_url
from tests.integration.processes.engine_rest import EngineRest, EngineRestError

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC_PROCESSES = _REPO_ROOT / "spec" / "processes"
_CORPUS_DOC = _REPO_ROOT / "docs" / "processes" / "New_BPMN" / "Adapted_BPMN"
_BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
_BASELINE = _REPO_ROOT / "scripts" / "bpmn-gates" / "baseline.json"
_MANDATORY = "One of the attributes 'class', 'delegateExpression', 'type', or 'expression' is mandatory on"
#: Tenant exclusivo deste modulo (vocabulario `^[A-Za-z][A-Za-z0-9_]{0,39}$` do engine_deploy).
_TENANT = "bpmngatesparse"


def _artifacts() -> list[Path]:
    # spec/processes/{bpmn,dmn} pela MESMA listagem do deploy canonico (inclui dmn/rco/), mais o
    # corpus CA (so' parse).
    return [*collect_artifacts(_SPEC_PROCESSES), *sorted(_CORPUS_DOC.glob("*.bpmn"))]


def _executable_process_ids(path: Path) -> set[str]:
    root = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
    return {
        p.get("id", "")
        for p in root.findall(f"{{{_BPMN_NS}}}process")
        if (p.get("isExecutable") or "").strip().lower() == "true"
    }


def _known_parse_rejections() -> dict[str, dict[str, int]]:
    """{nome do arquivo: {id do elemento: linha}} das entradas G2-IMPL da baseline versionada."""
    entries = json.loads(_BASELINE.read_text(encoding="utf-8"))["entries"]
    out: dict[str, dict[str, int]] = {}
    for entry in entries:
        if entry["code"] != "G2-IMPL":
            continue
        match = re.search(r"linha (\d+)$", entry["message"])
        assert match, f"entrada G2-IMPL sem linha: {entry}"
        out.setdefault(Path(entry["file"]).name, {})[entry["element"]] = int(match.group(1))
    return out


_KNOWN_PARSE_REJECTIONS = _known_parse_rejections()
_ARTIFACTS = _artifacts()


def _parse_problems(body: str) -> list[tuple[str, int, str | None]]:
    """(mensagem, linha, id) de cada erro de parse do corpo de erro do engine-rest.

    Usa `details` (ParseException do Camunda 7 REST: por recurso, `errors[]` com `line` e
    `mainElementId`) quando presente; senao, as linhas `* <msg> | <recurso> | line N` da mensagem."""
    payload: Any = json.loads(body)
    problems: list[tuple[str, int, str | None]] = []
    details = payload.get("details") if isinstance(payload, dict) else None
    if isinstance(details, dict) and details:
        for report in details.values():
            for err in report.get("errors", []):
                problems.append((str(err.get("message")), int(err.get("line", -1)), err.get("mainElementId")))
        return problems
    message = str(payload.get("message", "")) if isinstance(payload, dict) else body
    for match in re.finditer(r"\* (.+?) \| [^|]+ \| line (\d+)", message):
        problems.append((match.group(1), int(match.group(2)), None))
    return problems


async def _assert_known_rejection(engine: EngineRest, base_url: str, artifact: Path, name: str) -> None:
    expected = _KNOWN_PARSE_REJECTIONS[artifact.name]
    try:
        deployment_id = await engine.deploy(artifact, name=name, tenant_id=_TENANT)
    except EngineRestError as exc:
        assert "ENGINE-09005" in str(exc), f"{artifact.name}: rejeicao diferente da conhecida: {exc}"
    else:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            await client.delete(f"/deployment/{deployment_id}", params={"cascade": "true"})
        pytest.fail(
            f"{artifact.name}: o engine ACEITOU o deploy — a rejeicao G2-IMPL sumiu; remova as entradas "
            "G2-IMPL deste arquivo da baseline (scripts/bpmn-gates/baseline.json) e baixe o teto"
        )
    # `EngineRest.deploy` corta o corpo em 300 caracteres; o corpo inteiro vem do mesmo POST cru.
    with artifact.open("rb") as fh:
        async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
            resp = await client.post(
                "/deployment/create",
                data={"deployment-name": name, "tenant-id": _TENANT},
                files=[(artifact.name, (artifact.name, fh, "application/xml"))],
            )
    assert resp.status_code // 100 != 2, f"{artifact.name}: o POST cru foi aceito ({resp.status_code})"
    assert "ENGINE-09005" in resp.text, resp.text[:2000]
    problems = _parse_problems(resp.text)
    assert problems, f"{artifact.name}: corpo sem erros legiveis: {resp.text[:2000]}"
    others = [p for p in problems if _MANDATORY not in p[0]]
    assert not others, f"{artifact.name}: erros de parse fora da rejeicao conhecida: {others}"
    assert sorted(p[1] for p in problems) == sorted(expected.values()), (
        f"{artifact.name}: linhas rejeitadas {sorted(p[1] for p in problems)} != baseline "
        f"{sorted(expected.values())}"
    )
    ids = {p[2] for p in problems}
    if None not in ids:
        assert ids == set(expected), (
            f"{artifact.name}: ids rejeitados {sorted(map(str, ids))} != {sorted(expected)}"
        )


def test_the_artifact_list_is_not_vacuous() -> None:
    """17 SP-OP + DMNs + 4 CA no head de 16/11/2026; vazio seria um verde por vacuidade."""
    kinds = {p.suffix for p in _ARTIFACTS}
    assert kinds == {".bpmn", ".dmn"}, kinds
    assert any(p.is_relative_to(_CORPUS_DOC) for p in _ARTIFACTS)
    # toda rejeicao conhecida aponta para um artefato que este modulo realmente deploya
    assert set(_KNOWN_PARSE_REJECTIONS) <= {p.name for p in _ARTIFACTS}, _KNOWN_PARSE_REJECTIONS


@pytest.mark.parametrize(
    "artifact", _ARTIFACTS, ids=[p.relative_to(_REPO_ROOT).as_posix() for p in _ARTIFACTS]
)
async def test_engine_parses_artifact(artifact: Path) -> None:
    base_url = resolve_engine_rest_url().rstrip("/")
    engine = EngineRest(base_url)
    try:
        assert await engine.is_available(), (
            f"engine indisponivel em {base_url} — este teste e' `integration`: suba a engine pinada "
            "(docker compose --profile core up -d cibseven) ou deixe o CI roda-lo"
        )
        name = f"bpmn-gates-parse::{artifact.name}"
        if artifact.name in _KNOWN_PARSE_REJECTIONS:
            await _assert_known_rejection(engine, base_url, artifact, name)
            return
        deployment_id = await engine.deploy(artifact, name=name, tenant_id=_TENANT)
        try:
            if artifact.suffix == ".bpmn":
                keys = await engine.process_definition_keys_of_deployment(deployment_id)
                assert keys == _executable_process_ids(artifact), (
                    f"{artifact.name}: o engine registrou {sorted(keys)}, o XML declara executaveis "
                    f"{sorted(_executable_process_ids(artifact))}"
                )
            else:
                resources = await engine.deployment_resource_names(deployment_id)
                assert artifact.name in resources, (
                    f"{artifact.name} ausente do deployment: {sorted(resources)}"
                )
        finally:
            async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
                resp = await client.delete(f"/deployment/{deployment_id}", params={"cascade": "true"})
            assert resp.status_code in (200, 204), (
                f"limpeza do deployment {deployment_id}: {resp.status_code}"
            )
    finally:
        await engine.aclose()
