"""F-077 (R-077): os dois start sites FRAUDE->CRED semeiam as variaveis OBRIGATORIAS do contrato.

`docs/processes/contracts/SP-OP-CRED-001.md` §"Variaveis de entrada" marca como obrigatorias
(`sim`, sem asterisco) `tenant_id`, `prestador_id`, `direcao`, `tipo_prestador`,
`origem_solicitacao`, `data_solicitacao_iso` e `documentos_refs`. Antes deste pacote o worker
in-flow `fraude.start_credenciamento` e a regra-espelho FRAUDE->CRED do `notification_bridge`
iniciavam CRED-001 so' com `tenant_id`/`prestador_id`/`numero_caso`/`bundle_root`: as DMN nativas do
processo (`cred_admissibility`, `cred_route`, `cred_sla` leem `direcao`) recebiam variavel ausente.

O conjunto obrigatorio e' LIDO DO CONTRATO (parse da tabela), nunca reescrito aqui: se o contrato
ganhar uma variavel obrigatoria, este teste fica vermelho nos dois start sites.

Os valores sao os do contrato, nao inventados:
  * `direcao=descredenciamento` — `SP-OP-FRAUDE-001.md`: o handoff `operadora.fraude.start_credenciamento`
    "inicia SP-OP-CRED-001 (descredenciamento)"; `End_EncaminhadoCredenciamento` = handoff "a
    SP-OP-CRED-001 (descredenciamento)".
  * `origem_solicitacao=operadora` — dominio fechado do contrato CRED; a investigacao e' da propria
    operadora (mesmo valor que os handoffs sistemicos irmaos `adequacao.execute_remediation` e
    `inadimplencia.handoff_rescisao` ja' semeiam).
  * `documentos_refs=[]` — o contrato diz "pode ser vazio"; o bundle de custodia da fraude NAO e'
    documentacao de credenciamento (licenca/CRM/CNES/contrato) e segue em `bundle_root`.
  * `tipo_prestador` — ECOADO da origem quando houver; o FRAUDE nao o carrega hoje, entao vai `""`
    (sentinela de "nao informado"), que as DMN levam ao ramo conservador (limite declarado).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.notification_bridge import FRAUDE_COMPLETED_EVENT, HandoffEvent, NotificationBridge
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.fraude import (
    CRED_DIRECAO_HANDOFF_FRAUDE,
    CRED_ORIGEM_HANDOFF_FRAUDE,
    cred_start_seed_from_fraude,
    start_credenciamento,
)
from tests.support.audit_fakes import FakeStartAuditSink

_REPO = Path(__file__).resolve().parents[4]
_CRED_CONTRACT = _REPO / "docs" / "processes" / "contracts" / "SP-OP-CRED-001.md"
_HOJE = dt.date(2026, 10, 30)


def _contract_section(title: str) -> str:
    text = _CRED_CONTRACT.read_text(encoding="utf-8")
    start = text.index(f"## {title}")
    end = text.index("\n## ", start + 3)
    return text[start:end]


def _contract_mandatory_inputs() -> set[str]:
    """Linhas `| `nome` | tipo | sim | ...` da tabela de entrada (`sim*`/`nao` ficam de fora)."""
    names: set[str] = set()
    for line in _contract_section("Variaveis de entrada").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0].startswith("`") and cells[2] == "sim":
            names.add(cells[0].strip("`"))
    return names


def _contract_domain(var: str) -> set[str]:
    """Dominio fechado (`` `a` \\| `b` ``) declarado na descricao da variavel no contrato."""
    for line in _contract_section("Variaveis de entrada").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == f"`{var}`":
            row = line.split("|", 4)[-1]
            return set(re.findall(r"`([a-z_]+)`", row))
    raise AssertionError(f"{var} ausente do contrato")


_MANDATORY = _contract_mandatory_inputs()


def test_o_contrato_declara_as_cinco_variaveis_que_faltavam() -> None:
    """Nao-vacuidade: o parse do contrato acha as 5 do achado (mais as duas ancoras da chave)."""
    assert {
        "tenant_id",
        "prestador_id",
        "direcao",
        "tipo_prestador",
        "origem_solicitacao",
        "data_solicitacao_iso",
        "documentos_refs",
    } <= _MANDATORY


def test_valores_semeados_pertencem_aos_dominios_fechados_do_contrato() -> None:
    assert CRED_DIRECAO_HANDOFF_FRAUDE == "descredenciamento"
    assert CRED_DIRECAO_HANDOFF_FRAUDE in _contract_domain("direcao")
    assert CRED_ORIGEM_HANDOFF_FRAUDE in _contract_domain("origem_solicitacao")


def test_seed_e_deterministico_e_usa_a_data_injetada() -> None:
    seed = cred_start_seed_from_fraude({"prestador_id": "P-1"}, hoje=_HOJE)
    assert seed == {
        "direcao": "descredenciamento",
        "tipo_prestador": "",
        "origem_solicitacao": "operadora",
        "data_solicitacao_iso": "2026-10-30",
        "documentos_refs": [],
    }


@pytest.mark.parametrize("tipo", ["hospital", "clinica"])
def test_seed_ecoa_tipo_prestador_quando_a_origem_informa(tipo: str) -> None:
    assert cred_start_seed_from_fraude({"tipo_prestador": tipo}, hoje=_HOJE)["tipo_prestador"] == tipo


@pytest.mark.parametrize("ausente", [{}, {"tipo_prestador": None}, {"tipo_prestador": "   "}])
def test_seed_nunca_estringifica_none_em_tipo_prestador(ausente: dict[str, Any]) -> None:
    """`None` nunca vira o literal "None" (que casaria com nada mas pareceria um tipo informado)."""
    assert cred_start_seed_from_fraude(ausente, hoje=_HOJE)["tipo_prestador"] == ""


def test_seed_sem_data_injetada_usa_data_civil_iso() -> None:
    iso = cred_start_seed_from_fraude({}, hoje=None)["data_solicitacao_iso"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso)
    dt.date.fromisoformat(iso)


def test_seed_nao_semeia_decisao_nem_saida_do_cred() -> None:
    """Handoff neutro: nunca pre-decide a UT humana do CRED (`decisao_cred` & cia)."""
    seed = cred_start_seed_from_fraude({"decisao_cred": "DESCREDENCIAR"}, hoje=_HOJE)
    assert not {"decisao_cred", "fundamentacao", "responsavel_id"} & set(seed)


# --------------------------------------------------------------------------------------------
# Start site 1 — worker in-flow `operadora.fraude.start_credenciamento`
# --------------------------------------------------------------------------------------------


def _worker_start_variables(source: dict[str, Any]) -> dict[str, Any]:
    engine = FakeCibSevenTransport()
    result = start_credenciamento(source, engine=engine, audit_sink=FakeStartAuditSink(), hoje=_HOJE)
    return asyncio.run(engine.get_process_status(result["cred_business_key"])).variables


def test_worker_start_credenciamento_semeia_toda_variavel_obrigatoria_do_contrato() -> None:
    variables = _worker_start_variables(
        {"tenant_id": "amh", "prestador_id": "P-077", "numero_caso": "CASO-1", "bundle_root": "r1"}
    )
    faltando = _MANDATORY - set(variables)
    assert not faltando, f"start_credenciamento inicia CRED-001 sem {sorted(faltando)} (F-077)"
    assert variables["direcao"] == "descredenciamento"
    assert variables["origem_solicitacao"] == "operadora"
    assert variables["data_solicitacao_iso"] == "2026-10-30"
    assert variables["documentos_refs"] == []
    # Contexto do encaminhamento continua indo (nada removido).
    assert variables["numero_caso"] == "CASO-1"
    assert variables["bundle_root"] == "r1"
    assert variables["origem_encaminhamento"] == "fraude"


# --------------------------------------------------------------------------------------------
# Start site 2 — regra-espelho FRAUDE->CRED do notification_bridge
# --------------------------------------------------------------------------------------------

_FRAUDE_COMPLETED_CRED = {
    "tenant_id": "amh",
    "desfecho": "encaminhado_credenciamento",
    "prestador_id": "P-077",
    "numero_caso": "CASO-1",
    "bundle_root": "r1",
}


def _bridge_variables(payload: dict[str, Any]) -> dict[str, Any]:
    result = NotificationBridge().evaluate(HandoffEvent(event_type=FRAUDE_COMPLETED_EVENT, payload=payload))
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-CRED-001"
    return result.variables


def test_bridge_fraude_to_cred_semeia_toda_variavel_obrigatoria_do_contrato() -> None:
    variables = _bridge_variables(dict(_FRAUDE_COMPLETED_CRED))
    faltando = _MANDATORY - set(variables)
    assert not faltando, f"bridge FRAUDE->CRED inicia CRED-001 sem {sorted(faltando)} (F-077)"
    assert variables["direcao"] == "descredenciamento"
    assert variables["origem_solicitacao"] == "operadora"
    assert variables["documentos_refs"] == []
    dt.date.fromisoformat(variables["data_solicitacao_iso"])


def test_os_dois_start_sites_convergem_no_mesmo_seed() -> None:
    """Worker in-flow e espelho Kafka convergem na MESMA instancia (mesma business key); quem
    chegar primeiro cria — entao os dois precisam semear o mesmo conjunto, com os mesmos valores
    (a data e' a unica coisa que depende do relogio de cada um)."""
    origem = {"tenant_id": "amh", "prestador_id": "P-077", "tipo_prestador": "hospital"}
    worker = _worker_start_variables(origem)
    bridge = _bridge_variables({**_FRAUDE_COMPLETED_CRED, "tipo_prestador": "hospital"})
    comuns = (
        "direcao",
        "tipo_prestador",
        "origem_solicitacao",
        "documentos_refs",
        "tenant_id",
        "prestador_id",
    )
    for name in comuns:
        assert worker[name] == bridge[name], name
