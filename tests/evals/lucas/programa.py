"""Programa de teste do Lucas isolado (plano `docs/plans/lucas-numero-unico.md` §6(a)).

Roda cada caso de `casos.json` pelo grafo REAL do Lucas (`maezo.agents.lucas.graph.build`), com:
  - os fatos de conciliacao vindos da porta `FonteCobranca` (a `FonteCobrancaSimulada`, a unica
    que existe hoje), nunca escritos a mao no caso;
  - a entrada montada por `new_lucas_state` (o construtor ESTRITO da fronteira de entrada, o mesmo
    que a onda (d) vai usar), entao um caso que tentasse plantar campo de saida quebra aqui;
  - a DMN que o chamador escolher: `DmnDraftLocal` (lida do XML DRAFT em `spec/`, para o lane
    unitario), `CibSevenDmnTransport` (motor real, `tests/integration/agents/test_lucas_dmn_real.py`)
    ou `DmnIndisponivel` (caso que declara `"dmn": "indisponivel"`, em qualquer modo);
  - inferencia ROTEIRIZADA (sem modelo, sem rede) e um envio gravado que conta quantas mensagens o
    turno mandou.

Quem consome: o teste unitario `tests/unit/agents/test_lucas_programa.py`, o de integracao e o
script `tools/scripts/programa_lucas.py`, que grava um JSONL por rodada (insumo do Excel da onda
(g)). UM modulo para os tres, para que o programa nao tenha duas definicoes de "passou".

O REGISTRO DE UM CASO NAO CARREGA PSEUDONIMO NEM HASH DE DESTINO: so' o id do caso, tokens de classe
e os textos que o turno enviou (texto roteirizado, sintetico).

LIMITE DECLARADO: o mapeamento `HandoffCobranca.cobranca_subtipo` -> entrada do Lucas (§2.5) e' da
onda (d) (`LucasTurno`). Aqui cada caso ja' declara a entrada do Lucas; o subtipo que a originaria
fica como anotacao (`subtipo_handoff`), sem uma segunda copia da tabela.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from maezo.agents.lucas.fonte_cobranca import FatosCobranca, FonteCobranca, FonteCobrancaSimulada
from maezo.agents.lucas.graph import (
    ACK_ESCALACAO_RECUSADO,
    DMN_BILLING_ADMISSIBILITY,
    DMN_ESCALATION_ROUTING,
    build,
    new_lucas_state,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnTransport, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, DecisionTable, evaluate, live_table_digest, read_live_table

CASOS_PATH: Final[Path] = Path(__file__).with_name("casos.json")

TENANT: Final[str] = "amh"

#: Texto padrao da inferencia roteirizada. Sem "cancel"/"suspen" e sem promessa de humano: passa na
#: cerca de saida das duas rotas, entao um caso so' cai na cerca quando declara `rascunho_llm`.
MENSAGEM_PADRAO: Final[str] = "Aqui esta a informacao sobre a sua cobranca, conforme o registro da operadora."
ACK_PADRAO: Final[str] = (
    "Recebemos sua solicitacao e um atendente da equipe vai continuar o atendimento por aqui."
)
NARRATIVA_PADRAO: Final[str] = "Narrativa sintetica do dossie para o atendente."

#: Radicais proibidos em texto ENVIADO (prova de §6(a)): cobre cancelar/cancelamento/cancelado e
#: suspender/suspensao/suspenso, com o texto ja' sem acento e em caixa baixa.
_RADICAIS_PROIBIDOS: Final[re.Pattern[str]] = re.compile(r"cancel|suspen")

JORNADAS: Final[tuple[str, ...]] = ("J1", "J2", "J3", "failsafe")
SUBJORNADAS_J3: Final[tuple[str, ...]] = ("inadimplencia", "contestacao", "cancelamento")


def normalizar(texto: str) -> str:
    """Sem acento e em caixa baixa — a mesma leitura que as cercas de saida fazem."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def tem_radical_proibido(texto: str) -> bool:
    return _RADICAIS_PROIBIDOS.search(normalizar(texto)) is not None


def carregar_casos(path: Path = CASOS_PATH) -> list[dict[str, Any]]:
    dados = json.loads(path.read_text(encoding="utf-8"))
    casos: list[dict[str, Any]] = dados["casos"]
    return casos


# --- Dubles -------------------------------------------------------------------------------------


class InferenciaRoteirizada:
    """Inferencia sem modelo. `reasoning` e' a narrativa do dossie; `task_default` e' a mensagem
    (J1/J2) ou o ACK (escalacao) — o caso escolhe o texto com `rascunho_llm`."""

    def __init__(self, rascunho: str | None, *, rota_padrao: str) -> None:
        self._rascunho = rascunho
        self._padrao = ACK_PADRAO if rota_padrao == "escalate_human" else MENSAGEM_PADRAO
        self.phi: list[bool] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        del prompt, agent_id, tenant_id
        self.phi.append(phi)
        if task_kind == "reasoning":
            return NARRATIVA_PADRAO
        return self._rascunho if self._rascunho is not None else self._padrao


class EnvioGravado:
    """`WhatsAppSender` que grava cada envio. Devolve uma entrega normal (nunca duplicata)."""

    def __init__(self) -> None:
        self.textos: list[str] = []
        self.chaves: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash
        self.textos.append(text)
        self.chaves.append(idempotency_key)
        return {"ok": True}


class DmnIndisponivel:
    """DMN fora do ar: toda avaliacao falha como o transporte real falha (`DmnEvaluationError`)."""

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        del variables, tenant
        raise DmnEvaluationError(f"DMN indisponivel (programa do Lucas): `{decision_key}`")

    async def close(self) -> None:
        return None


class DmnDraftLocal:
    """As duas tabelas DRAFT do Lucas lidas do XML em `spec/processes/dmn/` (FIRST-hit).

    Nao e' o motor: e' o leitor fail-closed de `tests/support/dmn_first_hit.py`, que recusa entrada
    fora da gramatica que declara. A paridade com o motor e' provada caso a caso pela suite de
    integracao, que roda o MESMO corpus contra o CIB Seven. A versao devolvida carrega o sha256 do
    XML, entao uma edicao da tabela aparece no `dmn_refs` do registro.
    """

    def __init__(self) -> None:
        self._tabelas: dict[str, tuple[DecisionTable, DmnVersion]] = {}
        for chave in (DMN_BILLING_ADMISSIBILITY, DMN_ESCALATION_ROUTING):
            caminho = DMN_DIR / f"{chave}.dmn"
            digest = live_table_digest(caminho)
            self._tabelas[chave] = (
                read_live_table(caminho),
                DmnVersion(
                    key=chave, id=f"{chave}:draft-local:{digest[:12]}", version=0, deployment_id="local"
                ),
            )

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        del tenant
        if decision_key not in self._tabelas:
            raise DmnEvaluationError(f"DmnDraftLocal: `{decision_key}` nao e' tabela do Lucas")
        tabela, versao = self._tabelas[decision_key]
        if set(variables) != set(tabela.input_names):
            raise DmnEvaluationError(f"DmnDraftLocal: entradas de `{decision_key}` divergem da tabela")
        veredito = evaluate(tabela, {nome: variables[nome] for nome in tabela.input_names})
        return [dict(veredito.saidas)], versao

    async def close(self) -> None:
        return None


class DmnContada:
    """Embrulha qualquer `DmnTransport` contando as chaves avaliadas (o motor real nao conta)."""

    def __init__(self, interno: DmnTransport) -> None:
        self._interno = interno
        self.calls: list[str] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.calls.append(decision_key)
        return await self._interno.evaluate(decision_key, variables, tenant=tenant)

    async def close(self) -> None:
        await self._interno.close()


# --- Execucao de um caso ------------------------------------------------------------------------


def _hex_do_caso(caso_id: str) -> str:
    return hashlib.sha256(f"programa-lucas:{caso_id}".encode()).hexdigest()


async def montar_entrada(caso: Mapping[str, Any], fonte: FonteCobranca) -> dict[str, Any]:
    """A entrada do Lucas para o caso: identificadores sinteticos + `entrada` + fatos da fonte.

    `contexto` (so' nos casos fail-safe) sobrescreve por ultimo — e' por ele que um caso tira o
    `conversation_id` para provar o desvio de contexto ausente.
    """
    hx = _hex_do_caso(str(caso["id"]))
    entrada: dict[str, Any] = {
        "tenant_id": TENANT,
        "conversation_id": f"wa:{TENANT}:hk1_{hx[:24]}",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": str(caso["pseudo_id"]),
        "to_hash": hx[24:48],
        **dict(caso["entrada"]),
    }
    fatos = await fonte.fatos(str(caso["pseudo_id"]), entrada.get("competencia"))
    if isinstance(fatos, FatosCobranca):
        entrada.update(fatos.como_entrada_lucas())
    entrada.update(dict(caso.get("contexto") or {}))
    return entrada


async def executar_caso(
    caso: Mapping[str, Any],
    *,
    dmn: DmnTransport,
    fonte: FonteCobranca | None = None,
) -> dict[str, Any]:
    """Um turno do Lucas para `caso`. Devolve o REGISTRO (o que vai para o JSONL)."""
    fonte = fonte or FonteCobrancaSimulada()
    esperado: Mapping[str, Any] = caso["esperado"]
    transporte = DmnContada(DmnIndisponivel() if caso.get("dmn") == "indisponivel" else dmn)
    inferencia = InferenciaRoteirizada(caso.get("rascunho_llm"), rota_padrao=str(esperado.get("route", "")))
    envio = EnvioGravado()

    estado = new_lucas_state(await montar_entrada(caso, fonte))
    grafo = build(
        {
            "inference": inferencia,
            "dmn": transporte,
            "cibseven": FakeCibSevenTransport(),
            "audit_sink": FakeStartAuditSink(),
            "whatsapp": envio,
        }
    ).compile()
    final: dict[str, Any] = dict(await grafo.ainvoke(estado))

    dossie = final.get("dossier") or {}
    mensagem = final.get("mensagem") or {}
    registro: dict[str, Any] = {
        "caso": caso["id"],
        "jornada": caso["jornada"],
        "subjornada": caso.get("subjornada"),
        "subtipo_handoff": caso.get("subtipo_handoff"),
        "perfil_fonte": caso.get("perfil_fonte"),
        "dmn": caso.get("dmn", "modo_da_rodada"),
        "route": final.get("route", ""),
        "admissibilidade": final.get("admissibilidade", ""),
        "roteamento_escalacao": final.get("roteamento_escalacao", ""),
        "motivo_humano": final.get("motivo_humano", ""),
        "motivo_categoria": final.get("motivo_categoria", ""),
        "severidade": final.get("severidade", ""),
        "grupo_humano": final.get("grupo_humano", ""),
        "desfecho": final.get("desfecho", ""),
        "process_started": bool(final.get("process_started")),
        "dmn_avaliadas": list(transporte.calls),
        "dmn_refs": dict(final.get("dmn_refs") or {}),
        "envios": len(envio.textos),
        "textos_enviados": list(envio.textos),
        "recusa_de_saida": bool(mensagem.get("recusa_de_saida")),
        "ack_recusado": ACK_ESCALACAO_RECUSADO in envio.textos,
        "tem_dossie": bool(dossie),
        "decisao_cancelamento": dossie.get("decisao_cancelamento"),
        "comunicacao_cancelamento": mensagem.get("comunicacao_cancelamento"),
        "comunicacao_suspensao": mensagem.get("comunicacao_suspensao"),
        "llm_todas_phi": all(inferencia.phi),
        "llm_chamadas": len(inferencia.phi),
    }
    registro["divergencias"] = divergencias(esperado, registro)
    registro["ok"] = not registro["divergencias"]
    return registro


#: Campos do `esperado` que sao comparados por igualdade com o registro.
CAMPOS_ESPERADOS: Final[tuple[str, ...]] = (
    "route",
    "admissibilidade",
    "roteamento_escalacao",
    "motivo_humano",
    "motivo_categoria",
    "grupo_humano",
    "desfecho",
    "process_started",
    "envios",
    "recusa_de_saida",
    "ack_recusado",
    "dmn_avaliadas",
)


def divergencias(esperado: Mapping[str, Any], registro: Mapping[str, Any]) -> list[str]:
    """Cada campo esperado que o turno nao entregou, mais as provas de §6(a) que valem para TODO
    caso. So' nomes de campo e tokens de classe."""
    achados = [
        f"{campo}: esperado {esperado[campo]!r}, obtido {registro.get(campo)!r}"
        for campo in CAMPOS_ESPERADOS
        if campo in esperado and registro.get(campo) != esperado[campo]
    ]
    if registro.get("jornada") == "J3" and registro.get("route") != "escalate_human":
        achados.append("J3 sem escalate_human")
    if registro.get("decisao_cancelamento") is not None:
        achados.append("decisao_cancelamento preenchida")
    if any(tem_radical_proibido(str(t)) for t in registro.get("textos_enviados") or []):
        achados.append("texto enviado com cancelar/suspender")
    if registro.get("envios") != 1:
        achados.append("turno sem exatamente um envio")
    if not registro.get("llm_todas_phi"):
        achados.append("chamada de LLM sem phi=True")
    return achados


async def executar_programa(
    *, dmn: DmnTransport, casos: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Todos os casos, em ordem, contra a mesma DMN."""
    return [await executar_caso(caso, dmn=dmn) for caso in (casos if casos is not None else carregar_casos())]
