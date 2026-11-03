"""Uma conversa da Helena contra o GRAFO REAL (tabelas `.dmn` reais), com a rota global FALSA.

Limpeza da saude, etapa 3 (dono, 02/11/2026): o harness que morava em `test_helena_saude_gerativa.py` e em
`test_helena_base_saude.py` (as camadas sairam do codigo, com os testes delas). Sobrou o que os testes da
conversa, do caso a pedido, do roteador e das travas de saida usam: a conversa AUTORIZADA (a rota global do
modelo e a allowlist, sem base, sem gerativa, sem v4) e o motor que encerra o caso a pedido.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

from maezo.agents.helena.graph import _HELENA_MEMORIA_DE_CONVERSA, HelenaGraph, new_helena_state
from maezo.domain.conversa.caso_clinico import chave_do_escalonamento
from maezo.runtime.inference.experimento_global import (
    MODELO_HAIKU_GLOBAL,
    ROTA_DE_RESIDENCIA,
    RespostaDaRotaGlobal,
)
from maezo.runtime.inference.rota_global import RotaGlobal
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_DONO = "wa:amh:hk1_" + "a" * 64
_OUTRA = "wa:amh:hk1_" + "b" * 64
#: A conversa do caso a pedido (ex-`test_helena_base_saude`).
_CONVERSA_DE_CASO = "wa:amh:hk1_base_saude"
_BK = chave_do_escalonamento("amh", _CONVERSA_DE_CASO)
_BK_CLIN = chave_do_escalonamento("amh", _CONVERSA_DE_CASO, clinico=True)
_TABELAS = (
    "triage_redflag_adult",
    "triage_redflag_pediatric",
    "triage_redflag_gestante",
    "triage_redflag_mental_health",
)
_BOA_ENDOMETRIOSE = (
    "A endometriose é uma condição em que um tecido parecido com o que reveste o útero cresce fora dele. "
    "Quem costuma cuidar é o ginecologista."
)


class _DmnReal:
    """As tabelas `.dmn` REAIS (primeiro acerto), no formato que o grafo espera."""

    def __init__(self) -> None:
        self._tabelas = {chave: read_live_table(DMN_DIR / f"{chave}.dmn") for chave in _TABELAS}
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.chamadas.append((decision_key, dict(variables)))
        if decision_key not in self._tabelas:
            raise DmnEvaluationError(f"tabela indisponivel: {decision_key}")
        tabela = self._tabelas[decision_key]
        veredito = evaluate(tabela, {nome: variables.get(nome) for nome in tabela.input_names})
        return [veredito.saidas], DmnVersion(
            key=decision_key, id=f"{decision_key}:1:offline", version=1, deployment_id="offline"
        )

    def chaves(self) -> list[str]:
        return [chave for chave, _ in self.chamadas]


class _Motor(FakeCibSevenTransport):
    """O motor: guarda os starts e, como o CIB Seven com o BPMN v1.1.0, ENCERRA a instancia viva quando recebe
    `msg.escalation.withdrawn` pela business key (sem instancia viva, recusa como o motor: 400)."""

    def __init__(self, *, recusa_correlacao: bool = False) -> None:
        super().__init__()
        self.inicios: list[dict[str, Any]] = []
        self._recusa = recusa_correlacao

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.inicios.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        await super().correlate_message(message_name, business_key, variables)
        if self._recusa:
            raise CibSevenError("CIB Seven correlate_message failed [500]")
        linhas = self._history.get(business_key, [])
        if not any(i.state == "ACTIVE" for i in linhas):
            raise CibSevenError("CIB Seven correlate_message failed [400]")
        self._history[business_key] = [
            replace(i, state="COMPLETED") if i.state == "ACTIVE" else i for i in linhas
        ]

    def abrir(self, business_key: str, **variaveis: Any) -> None:
        self.seed_instance(
            ProcessInstance(
                instance_id=f"inst-{business_key}",
                process_key="SP-OP-ESCALATION-001",
                business_key=business_key,
                state="ACTIVE",
            ),
            variables=variaveis,
        )


class _Modelo:
    """O modelo da zona PHI: so' classifica, aqui."""

    def __init__(self) -> None:
        self.respostas: list[str] = []
        self.prompts: list[str] = []
        self.model_id = "fake-glm"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self.respostas.pop(0) if self.respostas else ""


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


class _Transporte:
    """A rota global, FALSA: devolve os textos da fila (ou levanta a falha da fila)."""

    rota = ROTA_DE_RESIDENCIA
    modelo = MODELO_HAIKU_GLOBAL

    def __init__(self, *respostas: str | Exception) -> None:
        self.fila: list[str | Exception] = list(respostas)
        self.chamadas: list[tuple[str, list[dict[str, Any]]]] = []

    async def conversar(self, sistema: str, mensagens: Any, *, max_tokens: int) -> RespostaDaRotaGlobal:
        self.chamadas.append((sistema, [dict(m) for m in mensagens]))
        item = self.fila.pop(0) if self.fila else _BOA_ENDOMETRIOSE
        if isinstance(item, Exception):
            raise item
        return RespostaDaRotaGlobal(texto=item, parada="end_turn", entrada=1, saida=1, latencia_ms=1)

    def enviado(self) -> str:
        return "\n".join(
            str(c.get("text", "")) for _, msgs in self.chamadas for m in msgs for c in m["content"]
        )


def _classify(**campos: Any) -> str:
    dados: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    dados.update(campos)
    return json.dumps(dados)


_INFO = _classify()
_CLINICA = _classify(intent="clinical_question")
_SINTOMA = _classify(intent="symptom", population="adult", intensidade="leve")
_MUITA_DOR = _classify(intent="symptom", population="adult", intensidade="grave")
_DOR_NO_PEITO = _classify(
    intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
)
_RISCO = _classify(intent="symptom", population="adult", psychosocial_risk=True)
_PESSOA = _classify(intent="human_request")


class _Conversa:
    """A conversa (sem o roteador ativo). `gerativa=True` (nome de antes) = a conversa AUTORIZADA: a rota
    global do modelo com a allowlist (as regras de conversa do DL-0131 valem so' nela)."""

    def __init__(
        self,
        transporte: _Transporte | None = None,
        *,
        conversa: str = _DONO,
        allowlist: tuple[str, ...] = (_DONO,),
        gerativa: bool = True,
        motor: _Motor | None = None,
        auditoria: FakeStartAuditSink | None = None,
    ) -> None:
        self.transporte = transporte or _Transporte()
        self.conversa = conversa
        self.rota = RotaGlobal(self.transporte, frozenset(allowlist)) if gerativa else None
        self.cib = motor or _Motor()
        self.auditoria = auditoria or FakeStartAuditSink()
        self.dmn = _DmnReal()
        self.whatsapp = _WhatsApp()
        self.modelo = _Modelo()
        self.memoria: dict[str, Any] = {}
        self._n = 0

    def _grafo(self) -> Any:
        grafo = HelenaGraph(
            inference=self.modelo,  # type: ignore[arg-type]
            dmn=self.dmn,  # type: ignore[arg-type]
            cibseven=self.cib,
            audit_sink=self.auditoria,
            whatsapp=self.whatsapp,
            historico_enabled=True,
            rota_global=self.rota,
            relogio=lambda: datetime(2026, 10, 17, 15, 0, tzinfo=UTC),
        )
        return grafo.compile_graph().compile()

    async def diz(self, mensagem: str, extracao: str = _INFO, *extras: str) -> dict[str, Any]:
        self._n += 1
        self.modelo.respostas.extend([extracao, *extras])
        estado: dict[str, Any] = dict(
            new_helena_state(
                tenant_id="amh",
                conversation_id=self.conversa,
                canal="whatsapp",
                beneficiario_pseudo_id="PSEUDO-CONVERSA",
                message_body=mensagem,
                message_ref=f"hk1_msg_conversa_{self._n}",
            )
        )
        estado.update(self.memoria)
        resultado = await self._grafo().ainvoke(estado)
        self.memoria = {k: resultado[k] for k in _HELENA_MEMORIA_DE_CONVERSA if k in resultado}
        return cast(dict[str, Any], resultado)

    @property
    def motivos(self) -> list[str]:
        return [v["motivo_categoria"] for v in self.cib.inicios]


class _ConversaDeCaso(_Conversa):
    """A conversa do caso a pedido (ex-`test_helena_base_saude._Conversa`): fora da allowlist, sem a rota."""

    def __init__(self, *, motor: _Motor | None = None, auditoria: FakeStartAuditSink | None = None) -> None:
        super().__init__(conversa=_CONVERSA_DE_CASO, gerativa=False, motor=motor, auditoria=auditoria)


def _texto(resultado: dict[str, Any]) -> str:
    return str(resultado.get("response_text") or "")
