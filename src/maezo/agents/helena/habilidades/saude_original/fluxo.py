"""A PORTA UNICA da saude (desenho original de 05/07/2026; dono, 01/11/2026 — DL-0160).

Nas conversas da allowlist, com `saude_original: true` no pacote, TODA mensagem de saude passa so' por aqui:

1. risco de suicidio/autolesao pelo texto: o caminho de sempre (`_risco_pelo_texto`: tabela de saude mental,
   CVV 188 e 192, caso para a equipe);
2. o modelo LE a conversa (`leitura`) — nao conversa, nao pergunta, nao julga;
3. duvida geral de saude: so' o texto REVISADO da base de saude (nada escrito na hora); sem texto revisado,
   a equipe;
4. sintoma ou pergunta clinica: a tabela DMN decide (ADR-0012). P1 = caso grave e o texto de emergencia;
   P2 = caso moderado e o aviso da equipe de saude; sem alarme = caso para a equipe (`intencao_clinica`).
   Na duvida (leitura ou tabela fora do ar), uma pessoa assume: caso para a equipe.

Nenhuma camada antiga de saude (protocolos, triagem com juiz, perguntas de alerta, saude gerativa, palavras
de alarme) responde nestas conversas.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

import structlog

from maezo.agents.helena import modelo_allowlist as _ma
from maezo.agents.helena.frase_afirmada import ultima_fala
from maezo.agents.helena.travas_entrada import sinal_claro_afirmado
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.inference.experimento_global import CAMPOS_SEM_PENSAMENTO

from . import leitura as _l

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

logger = structlog.get_logger(__name__)

#: Chave do PACOTE (`spec/agents/helena/pacote/configuracao.yaml`): `saude_original: true` liga.
CHAVE_DO_PACOTE: Final[str] = "saude_original"
#: As habilidades do roteador que sao saude.
HABILIDADES_DE_SAUDE: Final[frozenset[str]] = frozenset({"sintoma", "saude"})
_MAX_FALAS: Final[int] = 12
#: Fora da saude, nas conversas da porta unica, o que o roteador chama de `conversa` (fora cortesia e
#: cumprimento) recebe texto FIXO — nunca a redacao antiga do modelo ("Sou Helena... nao sou uma pessoa").
TEXTO_PODE_CONTAR: Final[str] = "Claro, pode contar."
TEXTO_CONVERSA: Final[str] = (
    "Pode me contar o que você precisa? Por aqui eu ajudo com o seu plano, com dúvidas de saúde e passo o "
    "seu caso para a nossa equipe de saúde quando for preciso."
)
_TROCA_DE_ASSUNTO: Final[re.Pattern[str]] = re.compile(
    r"(outro|novo) assunto|mud(ar|ei|ando|ou|o) (de |o )?assunto|assunto diferente", re.I
)


def texto_de_conversa(texto: str) -> str:
    """O texto fixo de `conversa` nas conversas da porta unica ("outro assunto" = "Claro, pode contar.")."""
    return TEXTO_PODE_CONTAR if _TROCA_DE_ASSUNTO.search(texto or "") else TEXTO_CONVERSA


#: Caso de saude JA' aberto nesta conversa, sem emergencia nova: o portal do atendente NAO mostra a conversa
#: (`graph._texto_de_caso_aberto`), entao a Helena pede para a pessoa contar ao profissional (nunca "a equipe
#: vai ver"). Emergencia nova (P1 da tabela) continua no 192, mesmo com caso aberto.
TEXTO_CASO_DE_SAUDE_ABERTO: Final[str] = (
    "A nossa equipe de saúde já está com um caso aberto com você nesta conversa, e um profissional vai falar "
    "com você por aqui. Quando ele falar com você, conte também o que você me disse agora. Se for uma "
    "emergência, ligue 192 (SAMU) ou vá ao pronto-socorro mais próximo."
)
#: Os textos fixos da porta (o `inform` os envia sem modelo: `roteador._e_texto_do_roteador`).
TEXTOS_FIXOS: Final[frozenset[str]] = frozenset(
    {TEXTO_PODE_CONTAR, TEXTO_CONVERSA, TEXTO_CASO_DE_SAUDE_ABERTO}
)
_MOTIVOS_DE_SAUDE: Final[frozenset[str]] = frozenset(
    {"red_flag_clinico", "risco_psicossocial", "intencao_clinica"}
)
#: Sem alarme (ou na duvida): caso para a equipe de saude, moderado (AGJ-HELENA-TRIAGE: pergunta clinica).
_CASO_CLINICO: Final[Mapping[str, str]] = {
    "next_kind": "escalate",
    "escalation_motivo": "intencao_clinica",
    "escalation_severidade": "moderada",
}


def ligada(self: HelenaGraph) -> bool:
    return getattr(self, "_saude_original", False) is True


def assume(self: HelenaGraph, state: Mapping[str, Any]) -> bool:
    """A porta unica conduz a saude desta conversa (chave ligada, rota do modelo e allowlist)."""
    return ligada(self) and self._gerativa is not None and _ma.na_allowlist(self, state)


def alarme_pela_palavra(self: HelenaGraph, state: Mapping[str, Any], antes: str | None) -> bool:
    """A palavra de alarme que a Helena de antes levaria ao 192 (so' para trazer a mensagem a esta porta)."""
    from maezo.agents.helena import graph as _g

    texto = str(state.get("message_body") or "")
    return sinal_claro_afirmado(state) or bool(
        self._roteador_alarme(
            texto, antes, self._falas_da_pessoa(state), ultima_fala(_g._falas_da_helena(state))
        )
    )


def _chamador(self: HelenaGraph) -> _l.Chamar:
    gerativa = self._gerativa
    if gerativa is None:
        raise AssertionError("saude_original exige a rota do modelo da allowlist")
    transporte: Any = gerativa.transporte
    derivar = getattr(transporte, "com_campos", None)
    if callable(derivar):
        transporte = derivar(CAMPOS_SEM_PENSAMENTO)

    async def chamar(sistema: str, texto: str, max_tokens: int) -> str:
        mensagens = [{"role": "user", "content": [{"text": _ma.minimizar_prompt(texto)}]}]
        resposta = await transporte.conversar(sistema, mensagens, max_tokens=max_tokens)
        return str(resposta.texto)

    return chamar


def conversa_para_leitura(state: Mapping[str, Any]) -> str:
    """O historico curto (pessoa e Helena) e a ultima mensagem, em texto simples."""
    linhas: list[str] = []
    historico = state.get("historico_conversa")
    if isinstance(historico, list):
        for e in historico[-_MAX_FALAS:]:
            if isinstance(e, Mapping) and e.get("texto"):
                quem = "Helena" if e.get("papel") == "helena" else "Pessoa"
                linhas.append(f"{quem}: {e['texto']}")
    linhas.append(f"Pessoa (ÚLTIMA MENSAGEM): {state.get('message_body') or ''}")
    return "\n".join(linhas)


async def atender(
    self: HelenaGraph, state: Mapping[str, Any], *, saude_pelo_roteador: bool, alarme: bool = False
) -> dict[str, Any] | None:
    """O turno de saude inteiro, ou `None` quando a mensagem nao e' de saude (o caminho de sempre).

    `alarme`: a mensagem chegou pelo alarme (do modelo do roteador ou da palavra no texto) e nunca vira
    "nao e' saude"; sem leitura, o sinal CLARO de agora (convulsionando, nao respira) e' o 192 de sempre."""
    if (risco := await self._risco_pelo_texto(state)) is not None:  # type: ignore[arg-type]
        logger.info("helena_saude_original", via="risco_pelo_texto")
        return risco
    try:
        lida = await _l.ler(_chamador(self), conversa_para_leitura(state))
    except PROGRAMMING_ERRORS:
        raise
    except ValueError:
        return _leitura_falhou(self, state, "ValueError", saude_pelo_roteador, alarme)
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        return _leitura_falhou(self, state, type(exc).__name__, saude_pelo_roteador, alarme)
    logger.info(
        "helena_saude_original_leitura",
        assunto=lida.assunto,
        populacao=lida.populacao,
        sintoma_codigo=lida.sintoma_codigo,
        intensidade=lida.intensidade,
    )
    if lida.assunto == "outro" and not alarme:
        return None
    if lida.assunto == "duvida_saude" and (texto := await _texto_revisado(self, state)) is not None:
        logger.info("helena_saude_original", via="base_revisada")
        return texto
    return await _pela_tabela(self, state, lida)


def _leitura_falhou(
    self: HelenaGraph, state: Mapping[str, Any], falha: str, saude_pelo_roteador: bool, alarme: bool
) -> dict[str, Any] | None:
    """Sem leitura: sinal claro de agora no texto = o 192 de sempre; saude (roteador ou alarme) = uma pessoa
    assume; senao, o caminho de sempre."""
    logger.warning("helena_saude_original_leitura_falhou", falha=falha, alarme=alarme)
    if alarme and sinal_claro_afirmado(state):
        update = _base_do_update(self, state, None)
        update.update(
            next_kind="escalate", escalation_motivo="red_flag_clinico", escalation_severidade="grave"
        )
        logger.info("helena_saude_original", via="sinal_claro_sem_leitura")
        return update
    if not (saude_pelo_roteador or alarme):
        return None
    return _para_a_equipe(self, state, None, motivo="leitura_falhou")


async def _texto_revisado(self: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any] | None:
    """A resposta REVISADA da base de saude (texto fixo do arquivo), ou `None` sem assunto na base."""
    from maezo.agents.helena.habilidades.saude.informar import _com_o_fecho_da_duvida
    from maezo.agents.helena.habilidades.saude.textos import CHAVE_FECHO
    from maezo.conhecimento import base_saude as _bs

    if not getattr(self, "_base_saude", False):
        return None
    assunto = await self._base_achar_assunto(state, self._base_hoje())  # type: ignore[arg-type]
    if assunto is None:
        return None
    update = self._update_sem_modelo(state)
    self._expirar_pendencias_do_turno(state, update)
    texto = _com_o_fecho_da_duvida(_bs.resposta_informativa(assunto), self._textos[CHAVE_FECHO])
    return dict(self._base_texto_fixo(update, texto))


async def _pela_tabela(self: HelenaGraph, state: Mapping[str, Any], lida: _l.Leitura) -> dict[str, Any]:
    """A tabela DMN do publico decide; qualquer saida abre caso para a equipe (o texto sai pelo motivo)."""
    from maezo.agents.helena.graph import _DMN_BY_POPULATION, _severidade_from_prioridade

    extracao = lida.para_a_tabela()
    dmn = await self._evaluate_dmn(extracao)
    if dmn.get("error"):
        logger.warning("helena_saude_original_tabela_fora", tabela=dmn.get("dmn_table"))
        return _para_a_equipe(self, state, lida, motivo="tabela_fora")
    decisao = dmn.get("dmn_decision") or {}
    conduta = str(decisao.get("conduta") or "CONTINUE")
    update = _base_do_update(self, state, lida)
    update.update(dmn)
    if decisao.get("red_flag") is True or conduta.startswith("ESCALATE"):
        mental = dmn.get("dmn_table") == _DMN_BY_POPULATION["mental_health"]
        severidade = _severidade_from_prioridade(str(decisao.get("prioridade") or "P2"))
        update.update(
            next_kind="escalate",
            escalation_motivo="risco_psicossocial" if mental else "red_flag_clinico",
            escalation_severidade=severidade,
            psychosocial_risk=mental,
        )
        logger.info("helena_saude_original", via="tabela_alarme", conduta=conduta, severidade=severidade)
        return update
    update.update(_CASO_CLINICO)
    logger.info("helena_saude_original", via="tabela_sem_alarme")
    return update


def texto_do_caso_aberto(self: HelenaGraph, state: Mapping[str, Any]) -> str | None:
    """O texto do turno da porta que reaproveitou um caso de saude JA' aberto (`ja_ativo`), sem emergencia
    (P1); `None` = o texto de sempre (`graph._texto_de_caso_aberto`, com o 192 primeiro na emergencia)."""
    if not assume(self, state) or state.get("escalation_motivo") not in _MOTIVOS_DE_SAUDE:
        return None
    if state.get("escalation_severidade") == "grave":
        from maezo.agents.helena.graph import RESPOSTA_CASO_ABERTO_EMERGENCIA

        return RESPOSTA_CASO_ABERTO_EMERGENCIA  # emergencia nova (P1) com caso aberto: o 192 primeiro
    return TEXTO_CASO_DE_SAUDE_ABERTO


def _base_do_update(self: HelenaGraph, state: Mapping[str, Any], lida: _l.Leitura | None) -> dict[str, Any]:
    update = self._update_sem_modelo(state, lida.populacao if lida is not None else "adult")
    self._expirar_pendencias_do_turno(state, update)
    update.update(
        intent="symptom" if lida is None or lida.assunto == "sintoma" else "clinical_question",
        coleta_pendente=None,
        coleta_contexto="",
    )
    if lida is not None:
        update.update(
            sintoma_codigo=lida.sintoma_codigo,
            intensidade=lida.intensidade,
            idade_anos=lida.idade_anos,
            idade_meses=lida.idade_meses,
            idade_gestacional_semanas=lida.idade_gestacional_semanas,
        )
    return update


def _para_a_equipe(
    self: HelenaGraph, state: Mapping[str, Any], lida: _l.Leitura | None, *, motivo: str
) -> dict[str, Any]:
    """Na duvida, uma pessoa assume: caso para a equipe de saude (`intencao_clinica`, moderada)."""
    logger.info("helena_saude_original", via=motivo)
    update = _base_do_update(self, state, lida)
    update.update(_CASO_CLINICO)
    return update


__all__ = [
    "CHAVE_DO_PACOTE",
    "HABILIDADES_DE_SAUDE",
    "TEXTO_CASO_DE_SAUDE_ABERTO",
    "TEXTO_CONVERSA",
    "TEXTOS_FIXOS",
    "TEXTO_PODE_CONTAR",
    "alarme_pela_palavra",
    "assume",
    "texto_do_caso_aberto",
    "atender",
    "conversa_para_leitura",
    "ligada",
    "texto_de_conversa",
]
