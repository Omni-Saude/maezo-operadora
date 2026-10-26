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
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

import structlog

from maezo.agents.helena import modelo_allowlist as _ma
from maezo.agents.helena.frase_afirmada import texto_afirmado, ultima_fala
from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.sintoma import ja_respondido as _jr
from maezo.agents.helena.habilidades.sintoma.textos import CHAVE_OFERTA_EQUIPE
from maezo.agents.helena.historico import historico_em_texto
from maezo.agents.helena.triagem_v2 import RESPOSTA_TRIAGEM_BEBE_PEQUENO
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
                _ma.provedor_para(self, state, _ma.PAPEL_PROTOCOLO).generate(
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


def _bebe_pequeno(state: Mapping[str, Any], protocolo: _p.Protocolo | None) -> bool:
    """Bebe com MENOS DE 3 MESES (rodada 12 / DL-0101): protocolo da faixa 0-2m, ou protocolo de crianca com
    recem-nascido/bebe pequeno dito na conversa. So' protocolos de crianca (nao "gravida de 2 meses")."""
    from maezo.agents.helena import graph as _g
    from maezo.agents.helena.triagem_v2 import bebe_pequeno

    if protocolo is not None and protocolo.publico == "crianca_0_2m":
        return True
    if protocolo is None or protocolo.publico not in _p.PUBLICOS_DE_CRIANCA:
        return False
    contexto = _g._plano(
        " ".join([str(state.get("message_body") or ""), *_g._falas_do_beneficiario(state)[-6:]])
    )
    return (
        _g._contexto_de_recem_nascido(state)
        or bebe_pequeno(contexto, None)
        or _IDADE_DE_BEBE_PEQUENO.search(contexto) is not None
    )


#: "ela tem so' 1 mes", "com 2 meses de vida" (crianca; "faz 2 meses" e' tempo, nao idade).
_IDADE_DE_BEBE_PEQUENO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:tem|de|com)(?: so| apenas)? (?:1|um|2|dois) mes(?:es)?\b"
)


def _alerta_neonatal(self: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Rodada 10, regra A (DL-0101): recem-nascido COM sinal de sofrimento na mensagem -> o escalonamento de
    alarme de sempre, ANTES de qualquer decisao do protocolo."""
    from maezo.agents.helena import graph as _g

    memoria = state.get("memoria_clinica")
    if not _g._alerta_neonatal(state, {}, memoria if isinstance(memoria, Mapping) else None):
        return None
    logger.warning("helena_protocolo_alerta_neonatal", node="classify")
    return _escalar(self, state, None, "LIGAR_192_AGORA")


def _escalar(
    self: HelenaGraph, state: Mapping[str, Any], protocolo: _p.Protocolo | None, destino: str
) -> dict[str, Any]:
    """LIGAR_192_AGORA = o escalonamento de alarme de sempre (`red_flag_clinico` grave, 192); CVV_188_E_192 =
    o caminho do risco psicossocial (CVV 188 e 192). Sem protocolo (alerta neonatal): pediatrico."""
    risco = destino == "CVV_188_E_192"
    publico = protocolo.publico if protocolo is not None else "crianca_0_2m"
    update: dict[str, Any] = {
        "intent": "symptom",
        "population": "mental_health" if risco else _POPULACAO.get(publico, "adult"),
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
    if destino in ("CONSULTA_EM_BREVE", "CONSULTA_ROTINA") and _bebe_pequeno(state, protocolo):
        # Rodada 12 (bebe com menos de 3 meses): NUNCA "marcar consulta" — a oferta EXPLICITA da equipe de
        # saude (o "sim" abre o caso), com pronto-atendimento/192 se piorar.
        logger.info("helena_protocolo_bebe_pequeno_oferta", protocolo=protocolo.id)
        return _texto(self, state, RESPOSTA_TRIAGEM_BEBE_PEQUENO, pendente=PENDENTE_OFERTA_EQUIPE)
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
    if (
        decisao is not None
        and decisao.habilidade not in ("sintoma", "saude", "conversa")
        and not decisao.responde_a_pendencia
    ):
        return None  # outro assunto (cobranca, plano, equipe): a pendencia cai e o roteador decide
    if (neonatal := _alerta_neonatal(self, state)) is not None:
        return neonatal
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
    if (
        decisao is not None
        and leitura.outro_sinal_grave
        and _rt._alarme_do_modelo(self, cast(Any, state), decisao.v7) == "claro"
    ):
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


#: Palavra de EXAME ou MEDIDA: "minha lombar esta' travada" e' queixa, nao resultado ("minha X esta' ...").
_PALAVRA_DE_RESULTADO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:exames?|resultados?|deu|veio|taxa|nivel|niveis|psa|colesterol|glicose|glicemia|hemograma|tsh"
    r"|creatinina|triglicerid\w*|vitamina|ferritina|pressao|parametros|referencia)\b"
)
#: A leitura do roteador que diz QUEIXA (da pessoa ou de outra): vai ao protocolo, nunca a resposta de saude.
_TIPOS_DE_QUEIXA: Final[frozenset[str]] = frozenset({"sintoma_proprio", "sintoma_de_terceiro"})


def duvida_sem_queixa(texto: str, v7: Any = None) -> bool:
    """Resultado de exame/medida pessoal, pergunta informativa ou duvida educativa, SEM relato de sintoma e
    sem o roteador ter lido queixa (`v7.tipo`)."""
    from maezo.agents.helena import graph as _g
    from maezo.agents.helena.triagem_v2 import relata_sintoma
    from maezo.conhecimento import base_saude as _bs
    from maezo.conhecimento import saude_gerativa as _sg

    plano = _g._plano(texto)
    if relata_sintoma(plano):
        return False
    # Rodada de 26/10/2026: "fui diagnosticada com mioma, e' perigoso?" e' duvida sobre o DIAGNOSTICO,
    # sem queixa: a resposta de saude, mesmo que o roteador leia "sintoma_proprio".
    if _sg.pergunta_pessoal_de_saude(plano, texto, assunto_de_saude=True):
        return True
    if v7 is not None and getattr(v7, "tipo", None) in _TIPOS_DE_QUEIXA:
        return False
    resultado = _bs.resultado_pessoal(plano) and _PALAVRA_DE_RESULTADO.search(plano) is not None
    duvida = resultado or _bs.pergunta_informativa(plano) or _sg.e_duvida_educativa(plano)
    return bool(duvida)


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
    if sintoma and ultimo is None and antes != _n.PENDENTE_IDADE and duvida_sem_queixa(texto, v7):
        # Rodada de 26/10/2026: resultado de exame ("meu PSA deu alto") ou duvida geral ("o que e' X?") sem
        # queixa na mensagem e' a resposta de saude (base/redacao), nunca as perguntas de alerta.
        logger.info("helena_protocolo_duvida_sem_queixa")
        return None
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
    if (neonatal := _alerta_neonatal(self, state)) is not None:
        return neonatal
    conversa, mensagem = _blocos(self, state)
    escolha: _n.Escolha | None = await _chamar(
        self,
        state,
        _n.prompt_da_escolha(catalogo, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_escolha(bruto, catalogo),
        "escolha",
    )
    if escolha is None and ultimo is not None:
        # Sem a escolha do modelo, com uma queixa ja' respondida: nunca recomecar perguntas (o seguimento).
        return _texto(
            self, state, _n.texto_de_seguimento(ultimo, catalogo.textos, reclamou=False, agendamento=False)
        )
    if escolha is None:
        publico = _publico_do_estado(state)
        escolha = _n.Escolha("crianca" if publico == "crianca_2m_5a" else publico, "desconhecida", None)
    afirmado = _g._plano(texto_afirmado(texto, ultima_fala(_g._falas_da_helena(state))))
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
        # Teste de 26/10/2026 ("minha filha caiu da cama e bateu a cabeca" so' recebia "qual a idade?"): a
        # idade vai JUNTO das perguntas de alerta que valem para qualquer idade (alerta universal da crianca:
        # convulsao, desmaio, sonolencia, respiracao, vomito, manchas) — nenhum alarme espera pela idade.
        universal = catalogo.universal("crianca_2m_5a")
        if universal is not None:
            logger.info("helena_protocolo_pede_idade_com_alerta", protocolo=escolha.protocolo)
            return _texto(
                self,
                state,
                f"{_n.texto_das_perguntas(universal)}\n{_n.TEXTO_IDADE_JUNTO}",
                pendente=_n.codigo_do_alerta(universal.id),
            )
        return _texto(self, state, _n.TEXTO_IDADE, pendente=_n.PENDENTE_IDADE)
    protocolo = catalogo.protocolos.get(escolha.protocolo or "")
    if protocolo is None or _n.precisa_da_idade(escolha, catalogo):
        # Sem protocolo proprio (ou a idade da crianca continua sem resposta): o alerta universal.
        protocolo = catalogo.universal(_n.publico_do_protocolo(escolha))
    if protocolo is None:
        return None
    logger.info("helena_protocolo_escolhido", protocolo=protocolo.id, rascunho=protocolo.rascunho)
    return await _perguntar_o_que_falta(self, state, protocolo, conversa, mensagem, afirmado=bool(afirmado))


async def _perguntar_o_que_falta(
    self: HelenaGraph,
    state: Mapping[str, Any],
    protocolo: _p.Protocolo,
    conversa: str,
    mensagem: str,
    *,
    afirmado: bool,
) -> dict[str, Any]:
    """Simulador de 26/10/2026: nunca perguntar o que a pessoa acabou de contar ("cai da escada" ja' responde
    "veio depois de uma queda?"). O modelo marca as perguntas ja' respondidas; a REGRA decide: "sim" urgente
    -> o destino na hora; o resto -> so' as perguntas que faltam; tudo respondido -> o destino. Sem leitura
    (ou sem frase afirmada), todas as perguntas, como antes."""
    k = len(protocolo.perguntas_de_alerta)
    pre: tuple[str, ...] | None = await _chamar(
        self,
        state,
        _n.prompt_da_pre_leitura(protocolo, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_pre_leitura(bruto, k),
        "pre_leitura",
    )
    if pre is not None and not afirmado:
        # Mensagem so' de pergunta/negacao/hipotese: "sim" nao conta (mesmo espirito da frase afirmada).
        pre = tuple("nao_dito" if r == "sim" else r for r in pre)
    if pre is None or all(r == "nao_dito" for r in pre):
        return _texto(
            self, state, _n.texto_das_perguntas(protocolo), pendente=_n.codigo_do_alerta(protocolo.id)
        )
    plano = _n.plano_das_perguntas(protocolo, pre)
    logger.info(
        "helena_protocolo_ja_respondidas",
        protocolo=protocolo.id,
        sim=pre.count("sim"),
        nao=pre.count("nao"),
        faltam=len(plano.faltam),
    )
    if plano.decisao is not None:
        return _executar(self, state, protocolo, plano.decisao)
    return _texto(
        self,
        state,
        _n.texto_das_perguntas(protocolo, plano.faltam, primeira=True),
        pendente=_n.codigo_do_alerta(protocolo.id, 0, plano.faltam),
    )


@dataclass(frozen=True)
class _SemRoteador:
    """A "decisao" quando o roteador falhou (prazo, provedor): so' a habilidade de sintoma, sem leitura v7."""

    habilidade: str = "sintoma"
    provavel: str | None = None
    responde_a_pendencia: bool = True
    v7: Any = None


#: O codigo da pergunta de alerta GENERICA da v4 (grupo GERAL: peito, falta de ar, AVC).
_ALERTA_GERAL_V4: Final[str] = "V4_ALERTA:GERAL"


async def sem_roteador(
    self: HelenaGraph, state: Mapping[str, Any], antes: str | None
) -> dict[str, Any] | None:
    """Simulador de 26/10/2026 (roteador fora: `BrRegionalTransportUnavailableError`): com as perguntas do
    PROTOCOLO pendentes, a resposta continua do protocolo (a leitura; sem ela, re-pergunta e depois a equipe)
    — nunca a pergunta generica da v4. `None` = o caminho de sempre."""
    if self._protocolos is None or not antes:
        return None
    if antes.startswith(_n.PENDENTE_ALERTA):
        logger.info("helena_protocolo_sem_roteador", etapa="alerta")
        return await responder_alerta(self, state, None, antes)
    if antes == _n.PENDENTE_IDADE:
        logger.info("helena_protocolo_sem_roteador", etapa="idade")
        return await conduzir(self, state, _SemRoteador(), antes, None)
    return None


async def sem_alerta_geral(
    self: HelenaGraph, state: Mapping[str, Any], antes: str | None, resultado: dict[str, Any]
) -> dict[str, Any]:
    """O caminho de sempre (roteador fora) ia fazer a pergunta GENERICA da v4 (peito/AVC) com os protocolos
    ligados: no lugar dela, o protocolo (escolhido pelo modelo; sem modelo, o alerta universal ou o seguimento
    da queixa ja' respondida)."""
    catalogo = self._protocolos
    if catalogo is None or not str(resultado.get("coleta_pendente") or "").startswith(_ALERTA_GERAL_V4):
        return resultado
    logger.info("helena_protocolo_no_lugar_do_alerta_geral")
    prot = await conduzir(self, state, _SemRoteador(), None, None)
    if prot is not None:
        return prot
    universal = catalogo.universal(_publico_do_estado(state))
    if universal is None:
        return resultado
    return _texto(self, state, _n.texto_das_perguntas(universal), pendente=_n.codigo_do_alerta(universal.id))


__all__ = [
    "conduzir",
    "sem_alerta_geral",
    "sem_roteador",
    "duvida_sem_queixa",
    "e_texto_do_protocolo",
    "protocolo_na_conversa",
    "responder_alerta",
]
