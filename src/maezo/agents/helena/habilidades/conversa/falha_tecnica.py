"""Falha TECNICA do modelo ou da tabela nunca abre caso sozinha (teste do dono de 26/10/2026).

Logs `/ecs/maezo-operadora-dev/agent-helena` (26/10, 16:45:26-16:46:00 UTC): o roteador caiu no prazo
(`helena_roteador_falhou falha=prazo`), o classificador deu `ReadTimeoutError` (17,6 s) e, como a mensagem
falava de saude ("febre desde ontem"), `_falha_deve_escalar` abriu o caso (`agent_desfecho_recorded
desfecho=escalado_humano motivo_categoria=falha_tecnica`, `cibseven_process_started SP-OP-ESCALATION-001`)
sem a pessoa pedir.

Agora, nas conversas do roteador ATIVO (a allowlist do dono), o turno que ia escalar `falha_tecnica` por falha
TECNICA — o classificador (excecao, JSON ilegivel ou fora do esquema, inclusive a segunda falha seguida), uma
tabela DMN fora do ar ou com veredito fora do vocabulario — responde o pedido curto de repetir
(`RESPOSTA_PECA_REENVIO`) e GUARDA a pergunta pendente. Excecoes (o escalonamento de sempre): sinal de alarme
AFIRMADO na mensagem (tabela deterministica ou lexico forte de emergencia), risco psicossocial, pedido
explicito de pessoa. As travas de decisao (`inform`/`handoff`/`consulta` recusados) nao sao falha do modelo e
nao passam aqui.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

#: O inicio do `error` das falhas TECNICAS (classificador e DMN); a classe vai ao log, nunca o texto.
_ERROS_TECNICOS: Final[tuple[tuple[str, str], ...]] = (
    ("classify LLM", "classificador"),
    ("DMN `", "tabela_indisponivel"),
)
_VEREDITO_FORA: Final[str] = "veredito fora do vocabulario"


def _classe(erro: str) -> str | None:
    for inicio, classe in _ERROS_TECNICOS:
        if erro.startswith(inicio):
            return classe
    return "tabela_veredito" if _VEREDITO_FORA in erro else None


def sem_caso(self: HelenaGraph, state: Mapping[str, Any], resultado: dict[str, Any]) -> dict[str, Any]:
    """O resultado do `classify`, com o escalonamento `falha_tecnica` por falha tecnica trocado pelo pedido
    de repetir (a pendencia do turno fica). Qualquer outro resultado volta como veio."""
    from maezo.agents.helena import graph as _g
    from maezo.agents.helena.travas_entrada import alarme_afirmado
    from maezo.conhecimento import saude_gerativa as _sg

    if resultado.get("next_kind") != "escalate" or resultado.get("escalation_motivo") != "falha_tecnica":
        return resultado
    classe = _classe(str(resultado.get("error") or ""))
    if classe is None:
        return resultado
    texto = str(state.get("message_body") or "")
    if (
        resultado.get("psychosocial_risk") is True
        or alarme_afirmado(state) is not None
        or _g._sinal_forte_de_emergencia(texto)
        or _sg.tem_risco(_g._plano(texto))
        or _g._pedido_explicito_com_verbo(texto)
        or _g._pedido_explicito_de_pessoa(texto)
    ):
        return resultado
    _g.logger.warning("helena_falha_tecnica_sem_caso", node="classify", origem=classe)
    return {
        **resultado,
        "next_kind": "inform",
        "intent": "information",
        "psychosocial_risk": False,
        "response_text": _g.RESPOSTA_PECA_REENVIO,
        "escalation_motivo": None,
        "escalation_severidade": None,
        "error": None,
        "coleta_pendente": state.get("coleta_pendente"),
        "coleta_contexto": state.get("coleta_contexto") or "",
    }


__all__ = ["sem_caso"]
