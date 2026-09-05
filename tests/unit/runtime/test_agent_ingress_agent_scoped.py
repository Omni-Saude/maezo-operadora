"""O ingresso HTTP e' ESCOPADO POR AGENTE — so' quem DECLARA o canal no spec o recebe (NEW-02).

O DEFEITO QUE ESTES TESTES FECHAM

`build_ingress_router` monta uma rota cujo corpo e' validado por `new_rafael_state` e cuja
`business_key` e' `AUTH-{tenant}-{guia}`: o contrato do rafael, importado incondicionalmente.
`service.py` a montava sob uma unica condicao — `settings.agent_ingress_enabled` — sem olhar
para `settings.agent_id`. Medido em 05/09/2026: zero comparacoes de `agent_id` em qualquer
ponto do caminho de montagem, e os nove casos de `tests/unit/runtime/test_agent_ingress.py`
fixavam `agent_id="rafael"` nas settings falsas, entao nenhum deles jamais exercitou outro id.

O UNICO freio existente era uma convencao de IaC:
`deploy/aws-ecs/envs/dev-sa-east-1/service-agents.tf` liga `MAEZO_AGENT_INGRESS_ENABLED` com
`each.key == "rafael" ? "1" : "0"`. Convencao de operacao nao e' invariante de codigo: um
edit de Terraform, um override local ou uma variavel de ambiente manual bastavam para montar
`POST /v1/autorizacoes` DENTRO do daemon de outro agente e empurrar um estado com forma de
rafael para o `harness.invoke()` de um grafo estranho.

POR QUE OS SIMBOLOS NOVOS SAO RESOLVIDOS POR `getattr` E NAO POR `import`

Disciplina de prova vermelha: no commit A deste WP os simbolos abaixo ainda nao existem, e um
`from ... import INGRESS_BY_AGENT` faria a coleta do arquivo inteiro morrer com `ImportError`
— um vermelho que nao distingue "o comportamento falta" de "o arquivo nao existe". Resolvidos
por `getattr`, o vermelho do commit A e' a mensagem de `_simbolo`, que NOMEIA o comportamento
ausente. Depois do commit B o mesmo acessador continua util: apagar ou renomear qualquer um
dos quatro simbolos devolve exatamente este vermelho, com a mesma frase.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
import structlog
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import maezo.runtime.agent_runtime.ingress as ingresso
import maezo.runtime.agent_runtime.service as servico
from maezo.agents import AgentLoader
from maezo.platform.health import create_health_app
from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

#: `tests/unit/runtime/<arquivo>` -> `parents[3]` e' a raiz do repo (mesma derivacao de
#: `tests/unit/runtime/test_model_tiering.py`).
_SPEC_AGENTS: Final[Path] = Path(__file__).resolve().parents[3] / "spec" / "agents"

#: A rota que o ingresso publica hoje. Escrita por extenso aqui de proposito: se ela mudar,
#: estes testes devem falhar junto com o contrato de quem a chama, nao seguir verdes lendo a
#: constante do modulo sob teste.
_ROTA: Final[str] = "/v1/autorizacoes"

#: O UNICO agente que declara canal de ingresso hoje (`spec/agents/rafael/agent.yaml`).
_UNICO_DECLARANTE: Final[str] = "rafael"

#: Token de classe que a recusa emite. Um log sem token estavel nao e' alarmavel.
_TOKEN_DE_RECUSA: Final[str] = "agent_ingress_refused_undeclared_agent"

_AUSENTE: Final[object] = object()


def _simbolo(modulo: Any, nome: str) -> Any:
    """Resolve um simbolo do fecho NEW-02, com o vermelho certo quando ele nao existe."""
    valor = getattr(modulo, nome, _AUSENTE)
    assert valor is not _AUSENTE, (
        f"{modulo.__name__}.{nome} nao existe. NEW-02 continua aberto: o ingresso e' montado "
        "sob `settings.agent_ingress_enabled` sozinho, sem olhar `settings.agent_id`, entao "
        "QUALQUER agente com a flag ligada ganha uma rota com o contrato do rafael."
    )
    return valor


def _registro() -> Mapping[str, Any]:
    return _simbolo(ingresso, "INGRESS_BY_AGENT")


def _erro_de_recusa() -> type[BaseException]:
    return _simbolo(ingresso, "IngressNotDeclaredForAgentError")


def _exigir_spec() -> Any:
    return _simbolo(ingresso, "require_ingress_spec_or_fail_closed")


def _montar_ingresso() -> Any:
    return _simbolo(servico, "mount_ingress_if_declared")


@dataclass
class _SettingsFalsas:
    agent_id: str


@dataclass
class _EstadoFalso:
    """Duck-typed como `AgentState`: o objeto sob teste e' a decisao de montagem, nao o turno."""

    settings: _SettingsFalsas
    harness: object | None = None


def _estado(agent_id: str) -> Any:
    return _EstadoFalso(settings=_SettingsFalsas(agent_id=agent_id))


def _rota_montada(app: FastAPI) -> bool:
    """Montada = o app RESPONDE em `/v1/autorizacoes`; nao-montada = 404.

    Comportamental, e nao introspeccao de `app.routes`: desde fastapi 0.139 um router incluido
    vira um `_IncludedRouter` sem `.path`, entao varrer `app.routes` procurando a string devolve
    "nao montada" para TODO app — um verde falso que esconderia exatamente o defeito NEW-02.
    """
    return TestClient(app, raise_server_exceptions=False).post(_ROTA, json={}).status_code != 404


def _arquivos_de_spec() -> list[Path]:
    return sorted(_SPEC_AGENTS.glob("*/agent.yaml"))


def _ids_do_spec() -> list[str]:
    return sorted(p.parent.name for p in _arquivos_de_spec())


def _declaracoes_de_ingresso() -> dict[str, Mapping[str, Any]]:
    """`agent_id -> bloco `ingress:`` lido do YAML CRU (nao do modelo pydantic).

    Cru de proposito: a cerca de paridade tem de comparar o registro do codigo com o que o
    ARQUIVO diz. Passar pelo `AgentDefinition` compararia o codigo com o codigo — se o campo
    fosse removido do modelo, a declaracao viraria invisivel e a cerca ficaria verde a toa.
    """
    declaracoes: dict[str, Mapping[str, Any]] = {}
    for caminho in _arquivos_de_spec():
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
        bloco = dados.get("ingress")
        if bloco:
            declaracoes[str(dados.get("id") or caminho.parent.name)] = bloco
    return declaracoes


# =================================================================================================
# A cerca de paridade: o YAML declara <=> o registro tem entrada
# =================================================================================================


def test_a_cerca_de_paridade_nao_e_vacua() -> None:
    """Sem isto, uma cerca que varre zero arquivos passaria calada."""
    arquivos = _arquivos_de_spec()
    assert len(arquivos) >= 11, f"esperado >= 11 agent.yaml (10 agentes + _template), vi {arquivos}"
    assert set(_declaracoes_de_ingresso()) == {_UNICO_DECLARANTE}, (
        "a cerca so' prova alguma coisa se EXATAMENTE um agente declarar ingresso hoje; "
        f"declarantes vistos: {sorted(_declaracoes_de_ingresso())}"
    )


def test_registro_espelha_a_declaracao_do_spec() -> None:
    """Spec-first: o registro do codigo nao pode conter um agente que o spec nao declara."""
    declaracoes = _declaracoes_de_ingresso()
    registro = _registro()

    assert set(registro) == set(declaracoes), (
        "registro de ingresso e spec divergiram — um agente com entrada no registro e sem "
        "declaracao no seu agent.yaml recebe uma rota que ninguem contratou, e um agente que "
        f"declara e nao tem entrada nunca sobe. registro={sorted(registro)} "
        f"spec={sorted(declaracoes)}"
    )
    for agent_id, bloco in declaracoes.items():
        spec = registro[agent_id]
        assert spec.agent_id == agent_id
        assert spec.canal == bloco["canal"], f"{agent_id}: canal do registro != canal do spec"
        assert spec.rota == bloco["rota"], f"{agent_id}: rota do registro != rota do spec"


def test_definicao_do_rafael_carrega_a_declaracao_de_ingresso() -> None:
    """A declaracao e' CONSUMIDA pelo modelo, nao uma chave morta no YAML.

    Config morta que PARECE viva e' pior que config ausente: o proximo leitor confia nela.
    """
    definicao = AgentLoader().load(_SPEC_AGENTS / _UNICO_DECLARANTE / "agent.yaml")
    assert definicao.ingress.get("canal") == "portal_tiss"
    assert definicao.ingress.get("rota") == f"POST {_ROTA}"


# =================================================================================================
# A recusa: `build_ingress_router` nao monta contrato de rafael em daemon alheio
# =================================================================================================


@pytest.mark.parametrize("agent_id", [i for i in _ids_do_spec() if i != _UNICO_DECLARANTE])
def test_agente_sem_declaracao_de_ingresso_e_recusado(agent_id: str) -> None:
    """Todo agente do spec que NAO declara ingresso e' recusado com erro tipado."""
    with pytest.raises(_erro_de_recusa()) as capturado:
        ingresso.build_ingress_router(_estado(agent_id))
    assert agent_id in str(capturado.value)


@pytest.mark.parametrize("agent_id", ["RAFAEL", " rafael ", "rafael2", "rafae", ""])
def test_id_quase_certo_e_recusado_sem_normalizacao(agent_id: str) -> None:
    """Nada de `strip()`/`lower()`/prefixo: a comparacao e' de igualdade exata.

    Mesma disciplina ja fixada para `AGENT_RUNTIME_MODE` (`" local "` e `"Local"` continuam
    falhando para producao): normalizar um identificador de governanca converte um erro de
    configuracao em um acerto silencioso.
    """
    with pytest.raises(_erro_de_recusa()):
        ingresso.build_ingress_router(_estado(agent_id))


def test_recusa_nao_deixa_rota_alguma_no_app() -> None:
    """ "Recusa" tem de significar ZERO rota montada, nao uma rota que responde erro."""
    app = FastAPI()
    with pytest.raises(_erro_de_recusa()):
        app.include_router(ingresso.build_ingress_router(_estado("helena")))
    assert not _rota_montada(app)


def test_recusa_emite_o_token_de_classe() -> None:
    """Uma recusa que nao aparece no log e' indistinguivel de uma flag esquecida desligada."""
    with structlog.testing.capture_logs() as registros, pytest.raises(_erro_de_recusa()):
        ingresso.build_ingress_router(_estado("helena"))
    eventos = [r.get("event") for r in registros]
    assert _TOKEN_DE_RECUSA in eventos, eventos


def test_exigir_spec_devolve_o_contrato_do_rafael() -> None:
    """O helper de fail-closed e' o MESMO caminho que a rota e o boot usam."""
    spec = _exigir_spec()(_UNICO_DECLARANTE)
    assert spec.agent_id == _UNICO_DECLARANTE
    assert spec.canal == "portal_tiss"
    assert spec.rota == f"POST {_ROTA}"


def test_rafael_continua_montando_a_rota() -> None:
    """Nenhuma mudanca de comportamento para o unico agente que declara o canal."""
    router = ingresso.build_ingress_router(_estado(_UNICO_DECLARANTE))
    assert _ROTA in {r.path for r in router.routes}  # type: ignore[attr-defined]
    app = FastAPI()
    app.include_router(router)
    assert _rota_montada(app)


# =================================================================================================
# O boot: `service.py` recusa e o daemon segue vivo
# =================================================================================================


def _boot(agent_id: str, *, ligado: bool) -> tuple[FastAPI, Any, bool]:
    estado = servico.AgentState(
        settings=AgentRuntimeSettings(agent_id=agent_id, agent_ingress_enabled=ligado)
    )
    app = create_health_app(readiness_checks=[], is_live=estado.is_live)
    montado = _montar_ingresso()(app, estado)
    return app, estado, bool(montado)


def test_boot_com_flag_ligada_recusa_agente_nao_declarado() -> None:
    app, estado, montado = _boot("helena", ligado=True)
    assert montado is False
    assert not _rota_montada(app)
    assert estado.ingress_mounted is False
    assert estado.ingress_refusal is not None and "helena" in estado.ingress_refusal


def test_daemon_segue_saudavel_depois_da_recusa() -> None:
    """A recusa e' fail-closed da CAPACIDADE, nao CrashLoop do pod.

    O daemon perderia a liveness inteira por um erro de configuracao de UMA rota — e um pod em
    CrashLoopBackOff nao mostra a `/readyz` nem o log que explicam o motivo.
    """
    app, _estado, _montado = _boot("helena", ligado=True)
    assert TestClient(app).get("/healthz").status_code == 200


def test_boot_com_flag_ligada_monta_para_o_declarante() -> None:
    app, estado, montado = _boot(_UNICO_DECLARANTE, ligado=True)
    assert montado is True
    assert _rota_montada(app)
    assert estado.ingress_mounted is True
    assert estado.ingress_refusal is None


def test_boot_com_flag_desligada_nao_monta_nem_para_o_declarante() -> None:
    """A flag continua sendo o opt-in; o registro so' ESTREITA quem ela pode alcancar."""
    app, estado, montado = _boot(_UNICO_DECLARANTE, ligado=False)
    assert montado is False
    assert not _rota_montada(app)
    assert estado.ingress_mounted is False
    assert estado.ingress_refusal is None, "flag desligada nao e' recusa — e' ausencia de pedido"
