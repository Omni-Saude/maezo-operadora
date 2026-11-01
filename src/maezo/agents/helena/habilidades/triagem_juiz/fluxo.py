"""A habilidade `triagem_juiz` no turno da Helena (experimento do dev, pedido do dono de 01/11/2026).

A queixa de sintoma (habilidade `sintoma` do roteador, ou alarme incerto) e' conduzida pelo `motor`: a TABELA
e' o piso e o MODELO julga a conversa inteira e so' SOBE a gravidade; no maximo 3 perguntas, uma por turno
(pendencia `PROT_JUIZ:`, dona `sintoma`, tipo aberta). So' roda com a chave `MAEZO_HELENA_TRIAGEM_JUIZ`
ligada E na allowlist do modelo (`modelo_allowlist.na_allowlist`: dono, Tania, time-1, time-2, simulador).
Chave desligada = a Helena de antes, sem nenhuma diferenca (os protocolos seguem como hoje).

As saidas usam os caminhos que ja' existem: 192 e CVV pelo escalonamento de sempre; pronto-socorro e
"atendimento hoje" pelos textos fixos do protocolo; equipe pela oferta (so' o "sim" abre o caso);
orientacao (e "nao orienta por aqui") com o especialista do mapa, os canais e a linha de sinais de alerta.
Falha do modelo: no 1o turno, o caminho de antes (protocolos); no meio da triagem, a oferta da equipe.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import OrderedDict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

import structlog

from maezo.agents.helena import modelo_allowlist as _ma
from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.protocolo import especialista as _esp
from maezo.agents.helena.habilidades.protocolo import evidencia as _ev
from maezo.agents.helena.habilidades.protocolo import fluxo as _pf
from maezo.agents.helena.habilidades.protocolo import nucleo as _n
from maezo.agents.helena.habilidades.protocolo import sinais as _sinais
from maezo.agents.helena.habilidades.sintoma import ja_respondido as _jr
from maezo.agents.helena.habilidades.sintoma.textos import CHAVE_OFERTA_EQUIPE
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.inference.experimento_global import CAMPOS_SEM_PENSAMENTO
from maezo.runtime.prompt_format import render_untrusted_block

from . import motor as _m

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

logger = structlog.get_logger(__name__)

#: Chave do dev (variavel Terraform `helena_triagem_juiz`). `1`, `true`, `on`, `sim` ligam; o resto desliga.
CHAVE: Final[str] = "MAEZO_HELENA_TRIAGEM_JUIZ"
#: Prefixo `PROT_`: a pendencia ja' e' da habilidade `sintoma` no contrato do roteador e do tipo "aberta".
PENDENTE: Final[str] = "PROT_JUIZ:"
#: Teto do turno (ate' 4 chamadas curtas em sequencia; p90 do Haiku ~2 s cada).
PRAZO_DO_TURNO_S: Final[float] = 30.0
ABERTURA_LEVE: Final[str] = "Pelo que você contou, não parece urgente."
_PUBLICO_DO_PROTOCOLO: Final[Mapping[str, str]] = {
    "adulto": "adulto",
    "idoso": "adulto",
    "gestante": "gestante",
    "bebe": "crianca_0_2m",
    "crianca": "crianca_2m_5a",
    "crianca_5_12": "crianca_5_12a",
}
_POPULACAO: Final[Mapping[str, str]] = {"gestante": "gestante", "adulto": "adult", "idoso": "adult"}
#: As respostas finais emitidas (a fala seguinte "ok, obrigado" nao recomeca a triagem).
_FINAIS: OrderedDict[str, None] = OrderedDict()
_MAX_FINAIS: Final[int] = 512


def ligada(ambiente: Mapping[str, str] | None = None) -> bool:
    env = os.environ if ambiente is None else ambiente
    return str(env.get(CHAVE) or "").strip().lower() in ("1", "true", "on", "sim")


def _final(texto: str) -> str:
    _FINAIS[texto] = None
    _FINAIS.move_to_end(texto)
    while len(_FINAIS) > _MAX_FINAIS:
        _FINAIS.popitem(last=False)
    return texto


def _chamador(self: HelenaGraph) -> _m.Chamar:
    gerativa = self._gerativa
    if gerativa is None:
        raise AssertionError("triagem_juiz exige a rota da saude gerativa")
    transporte: Any = gerativa.transporte
    derivar = getattr(transporte, "com_campos", None)
    if callable(derivar):
        transporte = derivar(CAMPOS_SEM_PENSAMENTO)  # como no teste de mesa: esforco baixo, sem pensamento

    async def chamar(sistema: str, texto: str, max_tokens: int) -> str:
        mensagens = [{"role": "user", "content": [{"text": _ma.minimizar_prompt(texto)}]}]
        resposta = await transporte.conversar(sistema, mensagens, max_tokens=max_tokens)
        return str(resposta.texto)

    return chamar


def _contexto_salvo(bruto: object) -> dict[str, Any] | None:
    if not isinstance(bruto, str) or not bruto.startswith("{"):
        return None
    try:
        ctx = json.loads(bruto)
    except json.JSONDecodeError:
        return None
    chaves = ("msgs", "qa", "estado", "f", "pre", "juiz_perguntou", "pendente")
    if (
        not isinstance(ctx, dict)
        or any(k not in ctx for k in chaves)
        or not isinstance(ctx["pendente"], dict)
    ):
        return None
    return ctx


async def conduzir(
    self: HelenaGraph, state: Mapping[str, Any], decisao: Any, antes: str | None, alarme: str | None
) -> dict[str, Any] | None:
    """A queixa de sintoma pela triagem com juiz, ou `None` (o caminho de sempre)."""
    from maezo.agents.helena import graph as _g

    if not ligada() or self._gerativa is None or not _ma.na_allowlist(self, state):
        return None
    texto = str(state.get("message_body") or "")
    if _g._pedido_explicito_com_verbo(texto) or _g._pedido_explicito_de_pessoa(texto):
        return None
    continua = bool(antes and antes.startswith(PENDENTE))
    if antes and not continua:
        return None  # outra pergunta pendente: a habilidade dona dela decide
    if continua:
        ctx = _contexto_salvo(state.get("coleta_contexto"))
        if ctx is None:
            logger.warning("helena_triagem_juiz_contexto_invalido")
            return _pf._texto(self, state, self._textos[CHAVE_OFERTA_EQUIPE], pendente=PENDENTE_OFERTA_EQUIPE)
        resposta: str | None = texto
    else:
        habilidade = (
            decisao.habilidade if decisao.habilidade != "duvida" else (decisao.provavel or "conversa")
        )
        if not (habilidade == "sintoma" or alarme == "incerto"):
            return None
        falas = _g._falas_da_helena(state)
        if falas and falas[-1] in _FINAIS and alarme != "incerto":
            return None  # a fala depois da resposta final: o caminho de sempre (agradecimento, seguimento)
        if (
            _pf.duvida_sem_queixa(texto, decisao.v7)
            or _jr.pergunta_de_agendamento(texto)
            or (_esp.pergunta_qual_especialista(texto) and not _esp.relata_queixa(texto))
        ):
            return None  # duvida, agendamento e "qual profissional" sem queixa: os protocolos de hoje
        ctx = _m.contexto_novo([texto])
        resposta = None
    try:
        r = await asyncio.wait_for(_m.passo(_chamador(self), ctx, resposta), timeout=PRAZO_DO_TURNO_S)
    except PROGRAMMING_ERRORS:
        raise
    except (TimeoutError, RuntimeError, ValueError) as exc:
        return _falhou(self, state, type(exc).__name__, continua)
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        return _falhou(self, state, type(exc).__name__, continua)
    if r["tipo"] == "pergunta":
        logger.info("helena_triagem_juiz_pergunta", motivo=r["motivo"], rodada=len(ctx["qa"]) + 1)
        update = _pf._texto(self, state, str(r["texto"]), pendente=f"{PENDENTE}{len(ctx['qa']) + 1}")
        update["coleta_contexto"] = json.dumps(ctx, ensure_ascii=False)
        return update
    logger.info(
        "helena_triagem_juiz_saida",
        saida=r["saida"],
        regra=str(r.get("regra") or ""),
        piso=r.get("piso"),
        juiz=r.get("juiz"),
        perguntas=len(ctx["qa"]),
        sinal_retirado=bool(r.get("sinal_retirado")),
    )
    return await _executar(self, state, r, ctx)


def _falhou(self: HelenaGraph, state: Mapping[str, Any], falha: str, continua: bool) -> dict[str, Any] | None:
    """Falha do modelo: no 1o turno, o caminho de antes (`None`); no meio da triagem, a oferta da equipe."""
    logger.warning("helena_triagem_juiz_falhou", falha=falha, continua=continua)
    if not continua:
        return None
    return _pf._texto(self, state, self._textos[CHAVE_OFERTA_EQUIPE], pendente=PENDENTE_OFERTA_EQUIPE)


def _escalar(self: HelenaGraph, state: Mapping[str, Any], publico: str, cvv: bool) -> dict[str, Any]:
    """192 (alarme de sempre, `red_flag_clinico` grave) ou CVV 188 e 192 (`risco_psicossocial`)."""
    pediatrico = publico in ("bebe", "crianca", "crianca_5_12")
    update: dict[str, Any] = {
        "intent": "symptom",
        "population": "mental_health"
        if cvv
        else ("pediatric" if pediatrico else _POPULACAO.get(publico, "adult")),
        "psychosocial_risk": cvv,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "idade_anos": None,
        "idade_meses": None,
        "idade_gestacional_semanas": None,
        "coleta_pendente": None,
        "coleta_contexto": "",
    }
    self._expirar_pendencias_do_turno(state, update)
    update["next_kind"] = "escalate"
    update["escalation_motivo"] = "risco_psicossocial" if cvv else "red_flag_clinico"
    update["escalation_severidade"] = "grave"
    return update


async def _executar(
    self: HelenaGraph, state: Mapping[str, Any], r: Mapping[str, Any], ctx: Mapping[str, Any]
) -> dict[str, Any]:
    publico_motor = str(r.get("publico") or (ctx.get("f") or {}).get("faixa_etaria") or "adulto")
    publico = _PUBLICO_DO_PROTOCOLO.get(publico_motor, "adulto")
    saida = r["saida"]
    if saida == "192":
        return _escalar(self, state, publico_motor, bool(r.get("cvv")))
    if saida == "PS":
        return _pf._texto(self, state, _final(_n.texto_do_destino_por_sinal("PRONTO_SOCORRO_AGORA", publico)))
    if saida == "HOJE":
        return _pf._texto(self, state, _final(_n.texto_do_destino_por_sinal("ATENDIMENTO_HOJE", publico)))
    if saida == "EQUIPE":
        return _pf._texto(self, state, self._textos[CHAVE_OFERTA_EQUIPE], pendente=PENDENTE_OFERTA_EQUIPE)
    # CONVERSA e FORA: o especialista do mapa da queixa, os canais e a linha de sinais de alerta.
    catalogo = self._protocolos
    if catalogo is None or catalogo.textos is None:
        return _pf._texto(self, state, self._textos[CHAVE_OFERTA_EQUIPE], pendente=PENDENTE_OFERTA_EQUIPE)
    queixa = " ".join(ctx.get("msgs") or [str(state.get("message_body") or "")])
    lido = await _pf._chamar(
        self,
        state,
        _n.prompt_do_mapa(
            catalogo, bloco_conversa="", bloco_mensagem=render_untrusted_block("message_body", queixa)
        ),
        lambda bruto: _n.ler_mapa(bruto, catalogo),
        "mapa",
    )
    chave = lido[0] if lido is not None and lido[0] else _ev.queixa_do_mapa(queixa, catalogo)
    entrada = catalogo.mapa.get(chave) if chave else None
    frase, _mental = _n.especialista_do_mapa(entrada, publico, catalogo.textos)
    linha = _sinais.linha(None, publico)
    corpo = f"{ABERTURA_LEVE} {frase}" if saida == "CONVERSA" else frase
    return _pf._texto(self, state, _final(f"{corpo}\n\n{linha}"))


__all__ = ["CHAVE", "PENDENTE", "conduzir", "ligada"]
