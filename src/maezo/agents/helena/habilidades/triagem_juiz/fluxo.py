"""A habilidade `triagem_juiz` no turno da Helena (experimento do dev, pedido do dono de 01/11/2026).

A queixa de sintoma (habilidade `sintoma` do roteador, ou alarme incerto) e' conduzida pelo `motor`: a TABELA
e' o piso e o MODELO julga a conversa inteira e so' SOBE a gravidade; no maximo 3 perguntas, uma por turno
(pendencia `PROT_JUIZ:`, dona `sintoma`, tipo aberta). So' roda com `triagem_juiz: true` no pacote
E na allowlist do modelo (`modelo_allowlist.na_allowlist`: dono, Tania, time-1, time-2, simulador).
Chave desligada = a Helena de antes, sem nenhuma diferenca (os protocolos seguem como hoje).

As saidas usam os caminhos que ja' existem: 192 e CVV pelo escalonamento de sempre; pronto-socorro e
"atendimento hoje" pelos textos fixos do protocolo; equipe pela oferta (so' o "sim" abre o caso);
orientacao (e "nao orienta por aqui") com o especialista do mapa, os canais e a linha de sinais de alerta.
Falha do modelo: o 192 de sempre se a palavra de alarme estiver no texto; senao, a oferta da equipe.

Teste do dono (01/11/2026): com a chave ligada, o alarme pela palavra ("desmaiou") nao responde antes da
triagem (ela abre a triagem, e o motor pergunta "foi hoje?"); pronto-socorro e "hoje" dizem o porque; logo
depois da conclusao, "o que eu te respondi?" recebe o porque; mensagem nova de sintoma e' um caso NOVO e
"outro assunto" encerra o caso anterior.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

import structlog

from maezo.agents.helena import modelo_allowlist as _ma
from maezo.agents.helena.frase_afirmada import ultima_fala
from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from maezo.agents.helena.habilidades.protocolo import especialista as _esp
from maezo.agents.helena.habilidades.protocolo import evidencia as _ev
from maezo.agents.helena.habilidades.protocolo import fluxo as _pf
from maezo.agents.helena.habilidades.protocolo import nucleo as _n
from maezo.agents.helena.habilidades.protocolo import sinais as _sinais
from maezo.agents.helena.habilidades.sintoma import ja_respondido as _jr
from maezo.agents.helena.habilidades.sintoma.textos import CHAVE_OFERTA_EQUIPE
from maezo.agents.helena.travas_entrada import sinal_claro_afirmado
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.inference.experimento_global import CAMPOS_SEM_PENSAMENTO
from maezo.runtime.prompt_format import render_untrusted_block

from . import motor as _m

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

logger = structlog.get_logger(__name__)

#: Chave do PACOTE (`spec/agents/helena/pacote/configuracao.yaml`): `triagem_juiz: true` liga.
CHAVE_DO_PACOTE: Final[str] = "triagem_juiz"
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
#: "O que eu te respondi?", "por que o pronto-socorro?": a pessoa pergunta pela conclusao da triagem.
_PERGUNTA_PELA_CONCLUSAO: Final[re.Pattern[str]] = re.compile(
    r"\bo que (eu )?(te |lhe )?(respondi|disse|falei|contei)\b"
    r"|\bpor ?qu[eê] (voc[eê] |vc |tu )?(disse|falou|mandou|pediu|indicou|acha|respondeu)\b"
    r"|\bde onde (voc[eê]|vc) tirou\b|\btirou essa conclus[aã]o\b"
    r"|\bpor ?qu[eê] (ir )?(o |a |ao |pro |pra |para o )?(ps|pronto[- ]?socorro|192|samu|hospital|upa)\b"
    r"|\bn[aã]o entendi (o )?(por ?qu[eê]|motivo)\b|\bqual (foi )?o motivo\b",
    re.I,
)
_RAZAO_FIXA: Final[str] = "foi pelo conjunto do que você me contou na conversa."
_REAVALIA: Final[str] = (
    "Se entendi algo errado, me conte de novo, numa mensagem só, como a pessoa está agora."
)
#: "Agora trago outro assunto", "mudei de assunto": encerra o caso anterior (2o teste do dono, 01/11).
#: So' frases sem ambiguidade: "outra coisa: ele tambem tem febre" e' o MESMO caso e nao entra.
_TROCA_DE_ASSUNTO: Final[re.Pattern[str]] = re.compile(
    r"\b(outro|novo) assunto\b|\bmud(ar|ei|ando|ou|o) (de |o )?assunto\b|\bassunto diferente\b",
    re.I,
)
#: A conclusao avisa como trocar de assunto (dono, 01/11/2026).
LINHA_OUTRO_ASSUNTO: Final[str] = "Se quiser falar de outra pessoa ou de outro assunto, é só mandar."
#: Mensagem de troca de assunto SEM o caso novo ("ok, agora trago outro assunto:").
_SO_A_TROCA_MAX: Final[int] = 60
TEXTO_PODE_CONTAR: Final[str] = "Claro, pode contar."
#: As respostas finais emitidas (a fala seguinte "ok, obrigado" nao recomeca a triagem).
_FINAIS: OrderedDict[str, None] = OrderedDict()
_MAX_FINAIS: Final[int] = 512


def ligada(self: HelenaGraph) -> bool:
    return getattr(self, "_triagem_juiz", False) is True


def assume(self: HelenaGraph, state: Mapping[str, Any]) -> bool:
    """A triagem com juiz conduz o sintoma desta conversa (chave ligada, rota gerativa e allowlist)."""
    return ligada(self) and self._gerativa is not None and _ma.na_allowlist(self, state)


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
    if not isinstance(ctx, dict) or any(k not in ctx for k in chaves):
        return None
    if not isinstance(ctx["pendente"], dict) and not isinstance(ctx.get("final"), dict):
        return None
    return ctx


def _alarme_pela_palavra(self: HelenaGraph, state: Mapping[str, Any], antes: str | None) -> bool:
    """O alarme que a Helena de antes daria pela palavra (sinal claro ou trava de entrada)."""
    from maezo.agents.helena import graph as _g

    texto = str(state.get("message_body") or "")
    return sinal_claro_afirmado(state) or bool(
        self._roteador_alarme(
            texto, antes, self._falas_da_pessoa(state), ultima_fala(_g._falas_da_helena(state))
        )
    )


def _mesma_fala(a: str, b: str) -> bool:
    return bool(b) and " ".join(a.split())[:80] == " ".join(b.split())[:80]


def _depois_da_conclusao(state: Mapping[str, Any], antes: str | None) -> dict[str, Any] | None:
    """O contexto da triagem concluida, se a ultima fala da Helena foi a conclusao (ou o porque dela)."""
    from maezo.agents.helena import graph as _g

    if antes:
        return None
    ctx = _contexto_salvo(state.get("coleta_contexto"))
    final = ctx.get("final") if ctx is not None else None
    falas = _g._falas_da_helena(state)
    if not isinstance(final, dict) or not falas or not _mesma_fala(falas[-1], str(final.get("texto") or "")):
        return None
    return ctx


def _em_caso(state: Mapping[str, Any], antes: str | None) -> bool:
    """A conversa esta numa triagem com juiz (pergunta pendente, ou a ultima fala foi a conclusao)."""
    from maezo.agents.helena import graph as _g

    falas = _g._falas_da_helena(state)
    return (
        bool(antes and antes.startswith(PENDENTE))
        or bool(falas and falas[-1] in _FINAIS)
        or (_depois_da_conclusao(state, antes) is not None)
    )


def troca_de_assunto(self: HelenaGraph, state: Mapping[str, Any], antes: str | None) -> dict[str, Any] | None:
    """ "Agora trago outro assunto" sem o caso novo, no meio ou depois da triagem: encerra o caso anterior
    e pede o novo (com o caso novo na mesma mensagem, `conduzir` comeca uma triagem so' com ela)."""
    texto = str(state.get("message_body") or "")
    if not assume(self, state) or not _TROCA_DE_ASSUNTO.search(texto) or len(texto.strip()) > _SO_A_TROCA_MAX:
        return None
    if not _em_caso(state, antes):
        return None
    logger.info("helena_triagem_juiz_troca_de_assunto", com_caso_novo=False)
    return _pf._texto(self, state, TEXTO_PODE_CONTAR)


def explicar(self: HelenaGraph, state: Mapping[str, Any], antes: str | None) -> dict[str, Any] | None:
    """ "O que eu te respondi?" logo depois da conclusao: o porque dela, sem nova triagem."""
    if not assume(self, state) or not _PERGUNTA_PELA_CONCLUSAO.search(str(state.get("message_body") or "")):
        return None
    ctx = _depois_da_conclusao(state, antes)
    if ctx is None:
        return None
    final = ctx["final"]
    razao = str(final.get("explica") or _RAZAO_FIXA)
    abertura = (
        "" if razao.lower().startswith("pelo que") else "Eu indiquei isso por causa do que você me contou: "
    )
    texto = f"{abertura}{razao}\n\n{_REAVALIA}"
    logger.info(
        "helena_triagem_juiz_explicou", saida=final.get("saida"), com_razao=bool(final.get("explica"))
    )
    update = _pf._texto(self, state, texto)
    update["coleta_contexto"] = json.dumps({**ctx, "final": {**final, "texto": texto}}, ensure_ascii=False)
    return update


async def conduzir(
    self: HelenaGraph, state: Mapping[str, Any], decisao: Any, antes: str | None, alarme: str | None
) -> dict[str, Any] | None:
    """A queixa de sintoma pela triagem com juiz, ou `None` (o caminho de sempre)."""
    from maezo.agents.helena import graph as _g

    if not assume(self, state):
        return None
    texto = str(state.get("message_body") or "")
    palavra = _alarme_pela_palavra(self, state, antes)
    if not palavra and (_g._pedido_explicito_com_verbo(texto) or _g._pedido_explicito_de_pessoa(texto)):
        return None
    continua = bool(antes and antes.startswith(PENDENTE))
    troca = bool(_TROCA_DE_ASSUNTO.search(texto))
    if continua and troca:
        # "Mudando de assunto, meu irmao ...": o caso novo comeca do zero, so' com esta mensagem.
        logger.info("helena_triagem_juiz_troca_de_assunto", com_caso_novo=True)
        continua, antes = False, None
    if antes and not continua and not palavra:
        return None  # outra pergunta pendente: a habilidade dona dela decide
    if palavra and alarme is None:
        alarme = "claro"  # a palavra que a Helena de antes levaria ao 192 sempre abre a triagem
    if continua:
        ctx = _contexto_salvo(state.get("coleta_contexto"))
        if ctx is None or not isinstance(ctx["pendente"], dict):
            logger.warning("helena_triagem_juiz_contexto_invalido")
            return _pf._texto(self, state, self._textos[CHAVE_OFERTA_EQUIPE], pendente=PENDENTE_OFERTA_EQUIPE)
        resposta: str | None = texto
    else:
        habilidade = (
            decisao.habilidade if decisao.habilidade != "duvida" else (decisao.provavel or "conversa")
        )
        if not (habilidade == "sintoma" or alarme in ("incerto", "claro")):
            return None
        if alarme is None and _em_caso(state, antes) and _lc.e_cortesia(texto):
            return None  # "ok, obrigado" depois da conclusao: o caminho de sempre
        if alarme is None and (
            _pf.duvida_sem_queixa(texto, decisao.v7)
            or _jr.pergunta_de_agendamento(texto)
            or (_esp.pergunta_qual_especialista(texto) and not _esp.relata_queixa(texto))
        ):
            return None  # duvida, agendamento e "qual profissional" sem queixa: os protocolos de hoje
        # Mensagem nova depois da conclusao = caso NOVO, so' com ela (2o teste do dono, 01/11/2026: juntar ao
        # caso anterior misturou o caso do irmao com o do marido).
        ctx = _m.contexto_novo([texto])
        resposta = None
    try:
        r = await asyncio.wait_for(_m.passo(_chamador(self), ctx, resposta), timeout=PRAZO_DO_TURNO_S)
    except PROGRAMMING_ERRORS:
        raise
    except (TimeoutError, RuntimeError, ValueError) as exc:
        return _falhou(self, state, type(exc).__name__, continua, palavra)
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        return _falhou(self, state, type(exc).__name__, continua, palavra)
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
        com_razao=bool(r.get("explica")),
    )
    return await _executar(self, state, r, ctx)


def _falhou(
    self: HelenaGraph, state: Mapping[str, Any], falha: str, continua: bool, palavra: bool
) -> dict[str, Any]:
    """Falha do modelo: com a palavra de alarme no texto, o 192 de sempre; senao, a oferta da equipe (teste do
    dono, 01/11/2026: nunca a lista de perguntas do protocolo antigo no meio da conversa)."""
    logger.warning("helena_triagem_juiz_falhou", falha=falha, continua=continua, palavra=palavra)
    if palavra:
        return _escalar(self, state, "adulto", False)
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
    if saida == "EQUIPE":
        return _pf._texto(self, state, self._textos[CHAVE_OFERTA_EQUIPE], pendente=PENDENTE_OFERTA_EQUIPE)
    explica = _explica(r)
    if saida in ("PS", "HOJE"):
        destino = "PRONTO_SOCORRO_AGORA" if saida == "PS" else "ATENDIMENTO_HOJE"
        fixo = _n.texto_do_destino_por_sinal(destino, publico)
        return _concluir(self, state, ctx, r, f"{explica}\n\n{fixo}" if explica else fixo)
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
    return _concluir(self, state, ctx, r, f"{corpo}\n\n{linha}")


def _explica(r: Mapping[str, Any]) -> str | None:
    """O porque so' quando a saida e' a do juiz (com o piso da tabela acima, a frase dele nao a explica)."""
    explica = r.get("explica")
    return explica if isinstance(explica, str) and explica and r.get("juiz") == r["saida"] else None


def _concluir(
    self: HelenaGraph, state: Mapping[str, Any], ctx: Mapping[str, Any], r: Mapping[str, Any], texto: str
) -> dict[str, Any]:
    """A conclusao (com a linha de outro assunto), com o contexto guardado para o porque no turno seguinte."""
    texto = f"{texto}\n\n{LINHA_OUTRO_ASSUNTO}"
    update = _pf._texto(self, state, _final(texto))
    final = {"saida": r["saida"], "explica": _explica(r), "texto": texto}
    update["coleta_contexto"] = json.dumps({**ctx, "pendente": None, "final": final}, ensure_ascii=False)
    return update


__all__ = [
    "CHAVE_DO_PACOTE",
    "PENDENTE",
    "LINHA_OUTRO_ASSUNTO",
    "TEXTO_PODE_CONTAR",
    "assume",
    "conduzir",
    "explicar",
    "ligada",
    "troca_de_assunto",
]
