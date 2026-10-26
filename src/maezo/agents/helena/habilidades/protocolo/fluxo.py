"""A habilidade de PROTOCOLO dentro do turno do roteador ativo (decisao do dono, 25/10/2026).

Liga com `helena_protocolos_enabled` (composicao: `build()` liga por padrao; o pacote `protocolos: false` ou
`MAEZO_HELENA_PROTOCOLOS=0` desligam) e so' roda nas conversas da allowlist do roteador ativo
(`_classify_ativo`). Catalogo ausente ou invalido = `None` aqui = o caminho de antes (v4/base).

A ORDEM no turno (`roteador._classify_ativo_decidido`), sempre DEPOIS das travas de entrada (a tabela
deterministica de alarme, o risco psicossocial e os dados pessoais rodam antes e nao mudam):
1. `responder_alerta`: com as perguntas de alerta do protocolo pendentes, o modelo le' a resposta a CADA uma
   (sim / nao / sem_clareza; negar ou repetir a pergunta = nao) e a REGRA decide. Sinal grave NOVO, fora das
   perguntas, volta para o alarme do roteador.
2. `conduzir`: pergunta de agendamento / de especialista (o especialista do protocolo ja' respondido, ou o do
   mapa escolhido pelo modelo) e a queixa de sintoma (o modelo escolhe o protocolo pelo sentido; a mesma
   queixa ja' respondida NUNCA recebe a pergunta de alerta de novo).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Final, cast

import structlog

from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.sintoma import ja_respondido as _jr
from maezo.agents.helena.habilidades.sintoma.textos import CHAVE_OFERTA_EQUIPE
from maezo.agents.helena.historico import historico_em_texto
from maezo.conhecimento import protocolos as _p
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.prompt_format import render_untrusted_block

from . import nucleo as _n

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

logger = structlog.get_logger(__name__)

#: Os textos MONTADOS por esta habilidade neste processo (o `respond` os envia sem modelo). Limitado.
_EMITIDOS: OrderedDict[str, None] = OrderedDict()
_MAX_EMITIDOS: Final[int] = 512
_POPULACAO: Final[Mapping[str, str]] = {
    "adulto": "adult",
    "gestante": "gestante",
    "puerpera": "gestante",
    **{p: "pediatric" for p in _p.PUBLICOS_DE_CRIANCA},
}
_HABILIDADES_DO_AGENDAMENTO: Final[frozenset[str]] = frozenset(
    {"saude", "sintoma", "plano", "conversa", "cobertura"}
)


def e_texto_do_protocolo(texto: object) -> bool:
    return isinstance(texto, str) and (texto in _EMITIDOS or texto in _n.textos_fixos())


def _emitir(texto: str) -> str:
    _EMITIDOS[texto] = None
    _EMITIDOS.move_to_end(texto)
    while len(_EMITIDOS) > _MAX_EMITIDOS:
        _EMITIDOS.popitem(last=False)
    return texto


def _texto(
    self: HelenaGraph, state: Mapping[str, Any], texto: str, *, pendente: str | None = None
) -> dict[str, Any]:
    update = self._update_sem_modelo(state)
    update.update({"coleta_pendente": None, "coleta_contexto": ""})
    return self._base_texto_fixo(update, _emitir(texto), pendente=pendente)


def _blocos(self: HelenaGraph, state: Mapping[str, Any]) -> tuple[str, str]:
    from maezo.agents.helena import roteador as _rt

    historico = state.get("historico_conversa") if self._historico_enabled else None
    maximo = getattr(self._roteador_contrato, "maximo_de_mensagens", 8)
    conversa = (
        render_untrusted_block("historico_conversa", historico_em_texto(_rt.ultimas(historico, maximo)))
        if historico
        else ""
    )
    return conversa, render_untrusted_block("message_body", str(state.get("message_body") or ""))


async def _chamar(
    self: HelenaGraph,
    state: Mapping[str, Any],
    prompt: str,
    ler: Callable[[str], tuple[Any, list[str]]],
    etapa: str,
) -> Any:
    """UMA chamada ao modelo do roteador (mesmo provedor, prazo e limite), com UMA re-pergunta quando o JSON
    vem invalido. Falha = `None` (quem chama decide o caminho seguro). O log leva so' a etapa e rotulos."""
    if self._roteador_em_curso >= self._roteador_limite:
        logger.info("helena_protocolo_modelo_falhou", etapa=etapa, falha="ocupado")
        return None
    self._roteador_em_curso += 1
    inicio = time.monotonic()
    resultado: Any = None
    ruins: list[str] = []
    try:
        for tentativa in range(2):
            restante = self._roteador_prazo_s - (time.monotonic() - inicio)
            if tentativa:
                if resultado is not None or restante <= 1.0:
                    break
                prompt = "\n".join([prompt, _n.aviso_de_reenvio(ruins)])
            bruto = await asyncio.wait_for(
                self._roteador_llm.generate(
                    prompt,
                    phi=True,
                    agent_id="helena",
                    tenant_id=str(state.get("tenant_id") or ""),
                    task_kind="task_default",
                ),
                timeout=restante + 1.0,
            )
            resultado, ruins = ler(bruto)
    except PROGRAMMING_ERRORS:
        raise
    except TimeoutError:
        logger.info("helena_protocolo_modelo_falhou", etapa=etapa, falha="TimeoutError")
        return None
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        logger.info("helena_protocolo_modelo_falhou", etapa=etapa, falha=type(exc).__name__)
        return None
    finally:
        self._roteador_em_curso -= 1
    if resultado is None:
        logger.info(
            "helena_protocolo_modelo_falhou", etapa=etapa, falha="json", campos=sorted(set(ruins))[:4]
        )
    return resultado


def _escalar(
    self: HelenaGraph, state: Mapping[str, Any], protocolo: _p.Protocolo, destino: str
) -> dict[str, Any]:
    """LIGAR_192_AGORA = o escalonamento de alarme de sempre (`red_flag_clinico` grave, 192); CVV_188_E_192 =
    o caminho do risco psicossocial (CVV 188 e 192)."""
    risco = destino == "CVV_188_E_192"
    update: dict[str, Any] = {
        "intent": "symptom",
        "population": "mental_health" if risco else _POPULACAO.get(protocolo.publico, "adult"),
        "psychosocial_risk": risco,
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
    update["escalation_motivo"] = "risco_psicossocial" if risco else "red_flag_clinico"
    update["escalation_severidade"] = "grave"
    return update


def _executar(
    self: HelenaGraph, state: Mapping[str, Any], protocolo: _p.Protocolo, decisao: _n.Decisao
) -> dict[str, Any]:
    catalogo = self._protocolos
    textos = catalogo.textos if catalogo is not None else None
    logger.info(
        "helena_protocolo_decidiu",
        protocolo=protocolo.id,
        tipo=decisao.tipo,
        destino=decisao.destino,
        sinal=decisao.sinal,
        rascunho=protocolo.rascunho,
    )
    if decisao.tipo == "repergunta":
        return _texto(
            self,
            state,
            _n.texto_das_perguntas(protocolo, decisao.indices),
            pendente=_n.codigo_do_alerta(protocolo.id, 1, decisao.indices),
        )
    if decisao.tipo == "oferta_equipe":
        # Sem clareza de novo: a oferta da equipe de saude, SEM 192 e sem caso (o "sim" abre o caso).
        return _texto(self, state, self._textos[CHAVE_OFERTA_EQUIPE], pendente=PENDENTE_OFERTA_EQUIPE)
    destino = str(decisao.destino)
    if destino in ("LIGAR_192_AGORA", "CVV_188_E_192"):
        return _escalar(self, state, protocolo, destino)
    if decisao.sinal is not None:
        return _texto(self, state, _n.texto_do_destino_por_sinal(destino, protocolo.publico))
    assert textos is not None  # o catalogo so' existe com os textos validos (`catalogo_do_ambiente`)
    return _texto(self, state, _n.texto_sem_sinal(protocolo, textos))


async def responder_alerta(
    self: HelenaGraph, state: Mapping[str, Any], decisao: Any, antes: str | None
) -> dict[str, Any] | None:
    """A resposta as perguntas de alerta do protocolo (pendencia `PROT_ALERTA:`), ou `None`."""
    from maezo.agents.helena import graph as _g
    from maezo.agents.helena import roteador as _rt

    catalogo = self._protocolos
    pend = _n.ler_pendente_do_alerta(antes)
    if catalogo is None or pend is None or catalogo.textos is None:
        return None
    protocolo = catalogo.protocolos.get(pend.protocolo)
    texto = str(state.get("message_body") or "")
    if protocolo is None or _g._pedido_explicito_com_verbo(texto) or _g._pedido_explicito_de_pessoa(texto):
        return None
    if decisao.habilidade not in ("sintoma", "saude", "conversa") and not decisao.responde_a_pendencia:
        return None  # outro assunto (cobranca, plano, equipe): a pendencia cai e o roteador decide
    n = len(protocolo.perguntas_de_alerta)
    indices = tuple(i for i in pend.indices if 1 <= i <= n) or tuple(range(1, n + 1))
    conversa, mensagem = _blocos(self, state)
    leitura: _n.Leitura | None = await _chamar(
        self,
        state,
        _n.prompt_da_leitura(protocolo, indices, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_leitura(bruto, len(indices)),
        "leitura",
    )
    if leitura is None:
        # Sem a leitura do modelo nao se adivinha: vale como "sem clareza" (re-pergunta, depois a equipe).
        leitura = _n.Leitura(tuple("sem_clareza" for _ in indices), False)
    if leitura.outro_sinal_grave and _rt._alarme_do_modelo(self, cast(Any, state), decisao.v7) == "claro":
        return None  # sinal grave NOVO, validado na tabela: o 192 do roteador
    return _executar(self, state, protocolo, _n.decidir(protocolo, indices, leitura.respostas, pend.rodada))


def _publico_do_estado(state: Mapping[str, Any]) -> str:
    populacao = str(state.get("population") or "")
    return {"pediatric": "crianca_2m_5a", "gestante": "gestante"}.get(populacao, "adulto")


async def _especialista_pelo_mapa(
    self: HelenaGraph, state: Mapping[str, Any], *, por_aqui: bool
) -> dict[str, Any]:
    catalogo = self._protocolos
    assert catalogo is not None and catalogo.textos is not None
    conversa, mensagem = _blocos(self, state)
    lido = await _chamar(
        self,
        state,
        _n.prompt_do_mapa(catalogo, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_mapa(bruto, catalogo),
        "mapa",
    )
    queixa, publico = lido if lido is not None else (None, "adulto")
    publico_p = "crianca_2m_5a" if publico == "crianca" else publico
    if lido is None:
        publico_p = _publico_do_estado(state)
    entrada = catalogo.mapa.get(queixa) if queixa else None
    frase, mental = _n.especialista_do_mapa(entrada, publico_p, catalogo.textos)
    logger.info("helena_protocolo_especialista", queixa=queixa or "nenhum", publico=publico_p)
    inicio = f"{_n.TEXTO_NAO_MARCO} " if por_aqui else ""
    return _texto(
        self, state, f"{inicio}{frase}\n\n{_n.seguranca(catalogo.textos, publico_p, mental=mental)}"
    )


def _por_aqui(texto: str) -> bool:
    from maezo.agents.helena import graph as _g

    plano = _g._plano(texto)
    return " por aqui" in f" {plano}" or "pelo whats" in plano or "com voce" in plano


def protocolo_na_conversa(self: HelenaGraph, state: Mapping[str, Any]) -> bool:
    """Algum protocolo ja' foi conduzido nesta conversa (as perguntas dele estao nas falas da Helena)? Entao o
    agendamento recebe o especialista DESSA queixa junto dos canais (`conduzir`)."""
    from maezo.agents.helena import graph as _g

    catalogo = self._protocolos
    if catalogo is None or catalogo.textos is None:
        return False
    return bool(_n.protocolos_na_conversa(_g._falas_da_helena(state), catalogo))


async def conduzir(
    self: HelenaGraph, state: Mapping[str, Any], decisao: Any, antes: str | None, alarme: str | None
) -> dict[str, Any] | None:
    """Agendamento/especialista e a queixa de sintoma pelos protocolos, ou `None` (o caminho de sempre)."""
    from maezo.agents.helena import graph as _g

    catalogo = self._protocolos
    if catalogo is None or catalogo.textos is None:
        return None
    if antes and not antes.startswith(_n.PREFIXO):
        return None  # outra pergunta pendente (cobertura, oferta, v4): a habilidade dona dela decide
    texto = str(state.get("message_body") or "")
    if _g._pedido_explicito_com_verbo(texto) or _g._pedido_explicito_de_pessoa(texto):
        return None
    habilidade = decisao.habilidade if decisao.habilidade != "duvida" else (decisao.provavel or "conversa")
    v7 = decisao.v7
    feitos = _n.protocolos_na_conversa(_g._falas_da_helena(state), catalogo)
    ultimo = catalogo.protocolos[feitos[-1]] if feitos else None
    if _jr.pergunta_de_agendamento(texto) and habilidade in _HABILIDADES_DO_AGENDAMENTO:
        if ultimo is not None:
            logger.info("helena_protocolo_seguimento", protocolo=ultimo.id, agendamento=True)
            corpo = _n.texto_de_seguimento(
                ultimo, catalogo.textos, reclamou=_jr.reclama_da_repeticao(texto), agendamento=True
            )
            return _texto(self, state, f"{_n.TEXTO_NAO_MARCO} {corpo}" if _por_aqui(texto) else corpo)
        return await _especialista_pelo_mapa(self, state, por_aqui=_por_aqui(texto))
    sintoma = habilidade == "sintoma" or alarme == "incerto" or antes == _n.PENDENTE_IDADE
    if (
        ultimo is not None
        and _jr.reclama_da_repeticao(texto)
        and habilidade in ("sintoma", "saude", "conversa")
    ):
        logger.info("helena_protocolo_seguimento", protocolo=ultimo.id, agendamento=False)
        return _texto(
            self, state, _n.texto_de_seguimento(ultimo, catalogo.textos, reclamou=True, agendamento=False)
        )
    if not sintoma:
        return None
    conversa, mensagem = _blocos(self, state)
    escolha: _n.Escolha | None = await _chamar(
        self,
        state,
        _n.prompt_da_escolha(catalogo, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_escolha(bruto, catalogo),
        "escolha",
    )
    if escolha is None:
        publico = _publico_do_estado(state)
        escolha = _n.Escolha("crianca" if publico == "crianca_2m_5a" else publico, "desconhecida", None)
    continua = v7 is not None and v7.continuacao
    if ultimo is not None and (escolha.protocolo in feitos or (escolha.protocolo is None and continua)):
        # A MESMA queixa ja' respondida: nunca a pergunta de alerta de novo ("dormi de mau jeito, sera' que e'
        # isso?" -> quem avalia e' o medico + o especialista e os canais).
        alvo = catalogo.protocolos.get(escolha.protocolo or "", ultimo)
        logger.info("helena_protocolo_seguimento", protocolo=alvo.id, agendamento=False)
        return _texto(
            self, state, _n.texto_de_seguimento(alvo, catalogo.textos, reclamou=False, agendamento=False)
        )
    if _n.precisa_da_idade(escolha, catalogo) and antes != _n.PENDENTE_IDADE:
        logger.info("helena_protocolo_pede_idade", protocolo=escolha.protocolo)
        return _texto(self, state, _n.TEXTO_IDADE, pendente=_n.PENDENTE_IDADE)
    protocolo = catalogo.protocolos.get(escolha.protocolo or "")
    if protocolo is None or _n.precisa_da_idade(escolha, catalogo):
        # Sem protocolo proprio (ou a idade da crianca continua sem resposta): o alerta universal.
        protocolo = catalogo.universal(_n.publico_do_protocolo(escolha))
    if protocolo is None:
        return None
    logger.info("helena_protocolo_escolhido", protocolo=protocolo.id, rascunho=protocolo.rascunho)
    return _texto(self, state, _n.texto_das_perguntas(protocolo), pendente=_n.codigo_do_alerta(protocolo.id))


__all__ = ["conduzir", "e_texto_do_protocolo", "protocolo_na_conversa", "responder_alerta"]
