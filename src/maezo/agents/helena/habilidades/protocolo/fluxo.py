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

from . import especialista as _esp
from . import evidencia as _ev
from . import leitura_sem_modelo as _lsm
from . import nucleo as _n
from . import sinais as _sinais
from . import sinonimos as _sinonimos

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
    self: HelenaGraph,
    state: Mapping[str, Any],
    protocolo: _p.Protocolo,
    decisao: _n.Decisao,
    especialista: str | None = None,
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
        if textos is None:
            return _texto(self, state, _n.texto_do_destino_por_sinal(destino, protocolo.publico))
        return _texto(
            self, state, _n.texto_do_destino_com_especialista(destino, protocolo, textos, especialista)
        )
    # o catalogo so' existe com os textos validos (`catalogo_do_ambiente`)
    if textos is None:
        raise AssertionError("catalogo de protocolos sem textos validos")
    return _texto(self, state, _n.texto_sem_sinal(protocolo, textos, especialista=especialista))


def _sem_sinal_de_consulta(protocolo: _p.Protocolo, decisao: _n.Decisao) -> bool:
    return (
        decisao.tipo == "destino"
        and decisao.sinal is None
        and decisao.destino in ("CONSULTA_EM_BREVE", "CONSULTA_ROTINA")
        and protocolo.publico not in _p.PUBLICOS_DE_CRIANCA
    )


def _falas_da_pessoa(state: Mapping[str, Any]) -> list[str]:
    """As falas da pessoa no historico, da mais antiga para a mais recente."""
    historico = state.get("historico_conversa")
    if not isinstance(historico, list):
        return []
    return [
        str(e.get("texto") or "")
        for e in historico
        if isinstance(e, Mapping) and e.get("papel") == "beneficiario" and e.get("texto")
    ]


def _pergunta_que_abriu(state: Mapping[str, Any], protocolo: _p.Protocolo) -> str:
    """A fala da pessoa que levou as perguntas deste protocolo (a fala dela logo antes delas), ou ""."""
    historico = state.get("historico_conversa")
    if not isinstance(historico, list):
        return ""
    cab = _n.cabecalho(protocolo)
    ultima_da_pessoa = ""
    achada = ""
    for e in historico:
        if not isinstance(e, Mapping):
            continue
        texto = str(e.get("texto") or "")
        if e.get("papel") == "beneficiario":
            ultima_da_pessoa = texto
        elif e.get("papel") == "helena" and texto.startswith(cab):
            achada = ultima_da_pessoa
    return achada


async def _especialista_da_queixa(
    self: HelenaGraph, state: Mapping[str, Any], protocolo: _p.Protocolo
) -> str | None:
    """Teste do dono de 26/10/2026 ("estou sentindo dores nas juntas, que profissional procuro?"): as
    perguntas do protocolo vieram antes e a resposta final leva o especialista do MAPA da queixa (o do
    protocolo quando o mapa nao acha a queixa ou o modelo falha)."""
    catalogo = self._protocolos
    if catalogo is None or catalogo.textos is None:
        raise AssertionError("catalogo de protocolos ausente ou sem textos")
    conversa, mensagem = _blocos(self, state)
    # Rodada de 26/10/2026: com "poxa, eu ja' respondi nao para todas" como mensagem nova, o modelo
    # escolheu "dor nas costas" para uma queixa de articulacoes. A queixa e' a fala que abriu as perguntas.
    abertura = _pergunta_que_abriu(state, protocolo)
    if abertura:
        mensagem = render_untrusted_block("message_body", abertura)
    lido = await _chamar(
        self,
        state,
        _n.prompt_do_mapa(catalogo, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_mapa(bruto, catalogo),
        "mapa",
    )
    entrada = catalogo.mapa.get(lido[0]) if lido is not None and lido[0] else None
    if entrada is None:
        return None
    logger.info("helena_protocolo_especialista_do_mapa", protocolo=protocolo.id, queixa=entrada.id)
    return _n.especialista_do_mapa(entrada, protocolo.publico, catalogo.textos)[0]


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
    if _esp.contesta_o_assunto(texto):
        # Teste do dono de 27/10/2026: "Quem disse que e' depressao? Eu disse que estou sentindo cansaco..."
        # foi lido como resposta sem clareza (re-pergunta + CVV). A contestacao do assunto: a desculpa curta e
        # a orientacao para a queixa repetida (a pendencia cai).
        logger.info("helena_protocolo_contestacao", protocolo=protocolo.id)
        return await resposta_direta(self, state, fala=_fala_da_queixa(state, catalogo), contestou=True)
    sem_modelo = _lsm.ler_sem_modelo(texto, len(indices))
    if sem_modelo is None and pend.rodada == 1 and _pergunta_nova(texto, decisao, catalogo):
        # Rodada de 26/10/2026 (7ac0219e): com a re-pergunta pendente, "um homem de 50 anos com PSA alterado
        # deve procurar qual profissional?" foi lida como a resposta (e recebeu o destino da crianca). Uma
        # pergunta NOVA de saude ou de qual profissional e' tratada como nova: a pendencia cai.
        logger.info("helena_protocolo_pergunta_nova_na_repergunta", protocolo=protocolo.id)
        return None
    if sem_modelo is not None:
        # Teste do dono (26/10/2026): a resposta OBVIA ("nehuma", "ja' respondi nao para todas", "a 2 sim") e'
        # lida pelo codigo, sem esperar o modelo (que caia no prazo e virava re-pergunta e oferta da equipe).
        logger.info("helena_protocolo_lido_sem_modelo", protocolo=protocolo.id, sim=sem_modelo.count("sim"))
        regra_sem_modelo = _n.decidir(protocolo, indices, sem_modelo, pend.rodada)
        return await _executar_com_especialista(self, state, protocolo, regra_sem_modelo)
    conversa, mensagem = _blocos(self, state)
    leitura: _n.Leitura | None = await _chamar(
        self,
        state,
        _n.prompt_da_leitura(protocolo, indices, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_leitura(bruto, len(indices)),
        "leitura",
    )
    if leitura is None:
        # Teste do dono (26/10/2026): falha TECNICA do modelo nao e' "sem clareza" da pessoa (virava
        # re-pergunta e depois a oferta da equipe). O pedido curto de repetir, com as MESMAS perguntas
        # pendentes.
        logger.info("helena_protocolo_leitura_pede_repetir", protocolo=protocolo.id)
        return _texto(self, state, _g.RESPOSTA_PECA_REENVIO, pendente=antes)
    if (
        decisao is not None
        and leitura.outro_sinal_grave
        and _rt._alarme_do_modelo(self, cast(Any, state), decisao.v7) == "claro"
    ):
        return None  # sinal grave NOVO, validado na tabela: o 192 do roteador
    regra = _n.decidir(protocolo, indices, leitura.respostas, pend.rodada)
    return await _executar_com_especialista(self, state, protocolo, regra)


def _pergunta_nova(texto: str, decisao: Any, catalogo: _p.Catalogo) -> bool:
    """A mensagem e' uma pergunta NOVA (qual profissional, sugestao de especialista, ou outra pergunta de
    saude que o roteador nao le' como resposta a pendencia), nao a resposta as perguntas de alerta?"""
    if _esp.pergunta_qual_especialista(texto) or (
        catalogo.textos is not None and _esp.sugere_especialista(texto, catalogo.textos)
    ):
        return True
    return (
        decisao is not None
        and not decisao.responde_a_pendencia
        and decisao.habilidade in ("saude", "sintoma")
        and "?" in texto
    )


async def _executar_com_especialista(
    self: HelenaGraph, state: Mapping[str, Any], protocolo: Any, regra: Any
) -> dict[str, Any]:
    """A decisao da regra; sem sinal e com a pergunta "qual profissional?" na abertura, o especialista do mapa
    (a mesma saida com a leitura do modelo ou a leitura sem modelo)."""
    especialista = None
    if _sem_sinal_de_consulta(protocolo, regra) and _esp.pergunta_qual_especialista(
        _pergunta_que_abriu(state, protocolo)
    ):
        especialista = await _especialista_da_queixa(self, state, protocolo)
    return _executar(self, state, protocolo, regra, especialista)


def _publico_do_estado(state: Mapping[str, Any]) -> str:
    populacao = str(state.get("population") or "")
    return {"pediatric": "crianca_2m_5a", "gestante": "gestante"}.get(populacao, "adulto")


async def _especialista_pelo_mapa(
    self: HelenaGraph,
    state: Mapping[str, Any],
    *,
    por_aqui: bool,
    ultimo: _p.Protocolo | None = None,
) -> dict[str, Any]:
    catalogo = self._protocolos
    if catalogo is None or catalogo.textos is None:
        raise AssertionError("catalogo de protocolos ausente ou sem textos")
    conversa, mensagem = _blocos(self, state)
    lido = await _chamar(
        self,
        state,
        _n.prompt_do_mapa(catalogo, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_mapa(bruto, catalogo),
        "mapa",
    )
    if lido is None and ultimo is not None:
        # Modelo fora, com uma queixa ja' conduzida na conversa: o especialista DESSA queixa e os canais.
        logger.info("helena_protocolo_seguimento", protocolo=ultimo.id, agendamento=True)
        corpo = _n.texto_de_seguimento(ultimo, catalogo.textos, reclamou=False, agendamento=True)
        return _texto(self, state, f"{_n.TEXTO_NAO_MARCO} {corpo}" if por_aqui else corpo)
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


async def _sugestao_de_especialista(
    self: HelenaGraph, state: Mapping[str, Any], ultimo: _p.Protocolo | None
) -> dict[str, Any]:
    """Teste do dono de 26/10/2026 ("o correto nao seria um ortopedista?", "nao seria melhor um
    reumatologista?"): o especialista do MAPA para a queixa (a fala que abriu o protocolo; sem ele, a da
    conversa; sem o modelo, a do ultimo protocolo), o clinico como primeira avaliacao, e a sugestao aceita
    so' quando o mapa a admite."""
    catalogo = self._protocolos
    if catalogo is None or catalogo.textos is None:
        raise AssertionError("catalogo de protocolos ausente ou sem textos")
    textos = catalogo.textos
    conversa, mensagem = _blocos(self, state)
    # Rodada de 26/10/2026: "o certo nao seria ortopedista?" depois de "torci o tornozelo" recebeu "boa
    # opcao para dor nas costas (lombar)". Com protocolo na conversa, a queixa e' SO' a fala que abriu as
    # perguntas dele (sem a conversa: o modelo nao escolhe outra queixa dela), como no #1035.
    abertura = _pergunta_que_abriu(state, ultimo) if ultimo is not None else ""
    if not abertura:
        # Rodada de 26/10/2026 (7ac0219e): sem perguntas na conversa (o destino veio direto), o mapa da
        # conversa inteira escolheu "dor no pescoco" para a torcao. A queixa e' a ultima fala da pessoa que
        # descreveu um problema.
        atual = str(state.get("message_body") or "")
        abertura = _ev.ultima_queixa([f for f in _falas_da_pessoa(state) if f != atual], catalogo)
    if abertura:
        conversa, mensagem = "", render_untrusted_block("message_body", abertura)
    lido = await _chamar(
        self,
        state,
        _n.prompt_do_mapa(catalogo, bloco_conversa=conversa, bloco_mensagem=mensagem),
        lambda bruto: _n.ler_mapa(bruto, catalogo),
        "mapa",
    )
    sugestoes = _esp.sugeridos(str(state.get("message_body") or ""), textos)
    entrada = catalogo.mapa.get(lido[0]) if lido is not None and lido[0] else None
    publico = ("crianca_2m_5a" if lido[1] == "crianca" else lido[1]) if lido is not None else None
    if entrada is None and lido is None and ultimo is not None:
        logger.info("helena_protocolo_sugestao_de_especialista", queixa="protocolo", protocolo=ultimo.id)
        fim = _n.seguranca(textos, ultimo.publico, mental=ultimo.tema == "saude_mental")
        corpo = _esp.texto_da_sugestao(
            sugestoes,
            _esp.alternativas_do_protocolo(ultimo, textos),
            _n.rotulo(ultimo),
            _n.especialista_do_protocolo(ultimo, textos),
        )
        return _texto(self, state, f"{corpo}\n\n{fim}")
    publico_p = publico or _publico_do_estado(state)
    frase, mental = _n.especialista_do_mapa(entrada, publico_p, textos)
    alternativas = _esp.alternativas_do_mapa(entrada, textos) if entrada is not None else frozenset()
    if publico_p in _p.PUBLICOS_DE_CRIANCA or publico_p in ("gestante", "puerpera"):
        alternativas = frozenset()  # crianca e gestacao: a porta de entrada do publico decide
    logger.info(
        "helena_protocolo_sugestao_de_especialista",
        queixa=entrada.id if entrada is not None else "nenhum",
        aceita=sum(1 for s in sugestoes if s in alternativas),
    )
    corpo = _esp.texto_da_sugestao(
        sugestoes, alternativas, entrada.queixa if entrada is not None else None, frase
    )
    return _texto(self, state, f"{corpo}\n\n{_n.seguranca(textos, publico_p, mental=mental)}")


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
    pronta: _n.Escolha | None = None
    ja_escolheu = False
    if (
        alarme != "claro"
        and antes != _n.PENDENTE_IDADE
        and not _esp.quer_marcar(texto)
        and _esp.contesta_o_assunto(texto)
        and _g._falas_da_helena(state)
    ):
        # Teste do dono de 27/10/2026 ("Quem disse que e' depressao? Eu disse que estou sentindo..."): a
        # desculpa curta e a orientacao de novo para a queixa que a pessoa repetiu.
        logger.info("helena_protocolo_contestacao")
        return await resposta_direta(self, state, fala=_fala_da_queixa(state, catalogo), contestou=True)
    if (
        habilidade in _HABILIDADES_DO_AGENDAMENTO
        and alarme != "claro"
        and antes != _n.PENDENTE_IDADE
        and not _esp.quer_marcar(texto)
    ):
        # Testes do dono de 26 e 27/10/2026: QUAL profissional procurar (nunca "eu nao avalio sintomas" nem
        # "eu nao pesquiso especialistas"). O pedido de marcar continua com o agendamento abaixo.
        if _esp.sugere_especialista(texto, catalogo.textos) or (
            _esp.cita_especialista(texto, catalogo.textos) and not _esp.pergunta_qual_especialista(texto)
        ):
            # "Faz sentido um cardiologista?" depois do protocolo ia ao classify/DMN (192 e caso): a
            # conferencia
            # do especialista nunca abre caso nem liga 192 sem um sinal NOVO afirmado (alarme "claro" acima).
            return await _sugestao_de_especialista(self, state, ultimo)
        if _esp.pergunta_qual_especialista(texto):
            if not _esp.relata_queixa(texto):
                return await _especialista_pelo_mapa(self, state, por_aqui=_por_aqui(texto), ultimo=ultimo)
            if (neonatal := _alerta_neonatal(self, state)) is not None:
                return neonatal
            # Decisao do dono de 27/10/2026 (opcao A): a resposta direta, sem as perguntas de alerta (so' com
            # um
            # sinal afirmado na propria mensagem o protocolo decide).
            return await resposta_direta(self, state)
    if _jr.pergunta_de_agendamento(texto) and habilidade in _HABILIDADES_DO_AGENDAMENTO and not ja_escolheu:
        if ultimo is not None:
            logger.info("helena_protocolo_seguimento", protocolo=ultimo.id, agendamento=True)
            corpo = _n.texto_de_seguimento(
                ultimo, catalogo.textos, reclamou=_jr.reclama_da_repeticao(texto), agendamento=True
            )
            return _texto(self, state, f"{_n.TEXTO_NAO_MARCO} {corpo}" if _por_aqui(texto) else corpo)
        return await _especialista_pelo_mapa(self, state, por_aqui=_por_aqui(texto))
    sintoma = habilidade == "sintoma" or alarme == "incerto" or antes == _n.PENDENTE_IDADE or ja_escolheu
    if (
        sintoma
        and not ja_escolheu
        and ultimo is None
        and antes != _n.PENDENTE_IDADE
        and duvida_sem_queixa(texto, v7)
    ):
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
    escolha: _n.Escolha | None = (
        pronta
        if ja_escolheu
        else await _chamar(
            self,
            state,
            _n.prompt_da_escolha(catalogo, bloco_conversa=conversa, bloco_mensagem=mensagem),
            lambda bruto: _n.ler_escolha(bruto, catalogo),
            "escolha",
        )
    )
    if escolha is None:
        # Teste do dono (26/10/2026): sem o modelo, a queixa pelo jeito de falar (titulo, sinonimos da base de
        # saude, exemplos do protocolo) antes do alerta universal.
        publico = _publico_do_estado(state)
        pelo_texto = _sinonimos.escolher(catalogo, self._base, texto, publico)
        logger.info("helena_protocolo_escolha_sem_modelo", protocolo=pelo_texto or "nenhum")
        if ultimo is not None and (pelo_texto is None or pelo_texto in feitos):
            # Sem a escolha do modelo, com uma queixa ja' respondida (ou a mesma de novo): nunca recomecar
            # as perguntas (o seguimento).
            return _texto(
                self,
                state,
                _n.texto_de_seguimento(ultimo, catalogo.textos, reclamou=False, agendamento=False),
            )
        escolha = _n.Escolha("crianca" if publico == "crianca_2m_5a" else publico, "desconhecida", pelo_texto)
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
    if protocolo.id in feitos:
        # Teste do dono (26/10/2026, 17:06-17:07 UTC): o alerta universal JA' conduzido nesta conversa
        # recomecava ("nenhum" protocolo proprio). A mesma queixa nunca recebe as perguntas de novo.
        logger.info("helena_protocolo_seguimento", protocolo=protocolo.id, agendamento=False)
        return _texto(
            self, state, _n.texto_de_seguimento(protocolo, catalogo.textos, reclamou=False, agendamento=False)
        )
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
    pre = await _pre_conferida(self, state, protocolo, conversa, mensagem, afirmado=afirmado)
    return await _seguir_a_pre_leitura(self, state, protocolo, pre)


async def _pre_conferida(
    self: HelenaGraph,
    state: Mapping[str, Any],
    protocolo: _p.Protocolo,
    conversa: str,
    mensagem: str,
    *,
    afirmado: bool,
) -> tuple[str, ...] | None:
    """A pre-leitura do modelo com o "sim" so' para frase afirmada e com o SINAL dito pela pessoa."""
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
    if pre is not None and "sim" in pre:
        # Rodada de 26/10/2026 (7ac0219e): "acabei torcendo o tornozelo" virou "sim" para "nao consegue
        # apoiar?" e pronto-socorro sem nenhuma pergunta. O "sim" so' vale com o SINAL dito pela pessoa.
        falas = [str(state.get("message_body") or ""), *_falas_da_pessoa(state)]
        conferido = _ev.so_sim_com_sinal_dito(pre, protocolo, falas)
        if conferido != pre:
            logger.info(
                "helena_protocolo_sim_sem_sinal_dito",
                protocolo=protocolo.id,
                descartados=pre.count("sim") - conferido.count("sim"),
            )
        pre = conferido
    return pre


async def _seguir_a_pre_leitura(
    self: HelenaGraph, state: Mapping[str, Any], protocolo: _p.Protocolo, pre: tuple[str, ...] | None
) -> dict[str, Any]:
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
        especialista = None
        if _sem_sinal_de_consulta(protocolo, plano.decisao) and _esp.pergunta_qual_especialista(
            str(state.get("message_body") or "")
        ):
            especialista = await _especialista_da_queixa(self, state, protocolo)
        return _executar(self, state, protocolo, plano.decisao, especialista)
    return _texto(
        self,
        state,
        _n.texto_das_perguntas(protocolo, plano.faltam, primeira=True),
        pendente=_n.codigo_do_alerta(protocolo.id, 0, plano.faltam),
    )


#: A desculpa curta quando a pessoa contesta o assunto escolhido (teste do dono de 27/10/2026).
TEXTO_DESCULPA: Final[str] = "Desculpe, eu entendi errado."


def _urgencia(protocolo: _p.Protocolo | None) -> str:
    """A TABELA decide: protocolo cujo destino SEM sinal e' atendimento hoje (ou mais) diz isso junto do
    especialista ("dor no peito deve ser avaliada hoje; o especialista e' o cardiologista")."""
    if protocolo is None:
        return ""
    destino = protocolo.destino_se_nenhum_sinal
    rotulo = _n.rotulo(protocolo)
    gestacao = protocolo.publico in ("gestante", "puerpera")
    if destino == "ATENDIMENTO_HOJE":
        if gestacao:
            return (
                f"Para {rotulo}, o ideal é ser avaliada ainda hoje, na maternidade ou num pronto-atendimento."
            )
        return (
            f"Para {rotulo}, o ideal é ser avaliado ainda hoje, num pronto-atendimento ou numa consulta no "
            "mesmo dia."
        )
    if destino in ("PRONTO_SOCORRO_AGORA", "LIGAR_192_AGORA"):
        lugar = "à maternidade ou a um pronto-socorro" if gestacao else "a um pronto-socorro"
        return f"Para {rotulo}, o mais seguro é ir agora {lugar}."
    return ""


async def resposta_direta(
    self: HelenaGraph,
    state: Mapping[str, Any],
    *,
    fala: str | None = None,
    contestou: bool = False,
) -> dict[str, Any]:
    """Decisao do dono de 27/10/2026 (opcao A): quem pergunta QUAL profissional procurar recebe a resposta
    direta, sem as perguntas de alerta: o especialista do MAPA, o clinico como porta de entrada, os canais e
    UMA linha com os sinais de alerta da queixa (`sinais.py`); a TABELA ainda decide quando o destino sem
    sinal e' atendimento hoje ou mais. As perguntas e o protocolo so' quando a propria mensagem AFIRMA um
    sinal (pre-leitura conferida). `fala`: a queixa (a mensagem atual por padrao)."""
    from maezo.agents.helena import graph as _g

    catalogo = self._protocolos
    if catalogo is None or catalogo.textos is None:
        raise AssertionError("catalogo de protocolos ausente ou sem textos")
    textos = catalogo.textos
    texto = fala if fala is not None else str(state.get("message_body") or "")
    mensagem = render_untrusted_block("message_body", texto)
    # A escolha do protocolo e o mapa nao dependem um do outro: em paralelo (rodada de 27/10/2026, mediana
    # do turno 8,2 s com as tres chamadas em sequencia).
    escolha_lida, lido = await asyncio.gather(
        _chamar(
            self,
            state,
            _n.prompt_da_escolha(catalogo, bloco_conversa="", bloco_mensagem=mensagem),
            lambda bruto: _n.ler_escolha(bruto, catalogo),
            "escolha",
        ),
        _chamar(
            self,
            state,
            _n.prompt_do_mapa(catalogo, bloco_conversa="", bloco_mensagem=mensagem),
            lambda bruto: _n.ler_mapa(bruto, catalogo),
            "mapa",
        ),
    )
    escolha: _n.Escolha | None = escolha_lida
    if escolha is None:
        publico_e = _publico_do_estado(state)
        pelo_texto = _sinonimos.escolher(catalogo, self._base, texto, publico_e)
        escolha = _n.Escolha(
            "crianca" if publico_e == "crianca_2m_5a" else publico_e, "desconhecida", pelo_texto
        )
    protocolo = catalogo.protocolos.get(escolha.protocolo or "")
    if protocolo is not None and not contestou:
        afirmado = bool(_g._plano(texto_afirmado(texto, ultima_fala(_g._falas_da_helena(state)))))
        pre = await _pre_conferida(self, state, protocolo, "", mensagem, afirmado=afirmado)
        if pre is not None and "sim" in pre:
            # Um sinal AFIRMADO na propria mensagem: o protocolo decide (destino ou as perguntas que faltam).
            logger.info("helena_protocolo_direto_com_sinal", protocolo=protocolo.id)
            return await _seguir_a_pre_leitura(self, state, protocolo, pre)
    entrada = catalogo.mapa.get(lido[0]) if lido is not None and lido[0] else None
    if lido is None and (pelo_texto_do_mapa := _ev.queixa_do_mapa(texto, catalogo)) is not None:
        # Rodada de 27/10/2026: o mapa caiu no prazo e a resposta virou o clinico generico. Sem o modelo, a
        # queixa do mapa pelo jeito de falar (unica e com duas palavras em comum, no minimo).
        logger.info("helena_protocolo_mapa_sem_modelo", queixa=pelo_texto_do_mapa)
        entrada = catalogo.mapa.get(pelo_texto_do_mapa)
    if lido is not None:
        publico = "crianca_2m_5a" if lido[1] == "crianca" else lido[1]
    elif protocolo is not None:
        publico = protocolo.publico
    else:
        publico = _publico_do_estado(state)
    if entrada is None and lido is None and protocolo is not None:
        frase, mental_mapa = _n.especialista_do_protocolo(protocolo, textos), _sinais.mental(protocolo)
    else:
        frase, mental_mapa = _n.especialista_do_mapa(entrada, publico, textos)
    # Teste do dono de 27/10/2026 ("cansaco, perda de libido e desanimo" virou depressao): o protocolo de
    # saude mental so' da' a linha de sinais (com o CVV) quando o mapa tambem le' saude mental.
    sinal_de = (
        protocolo if (protocolo is not None and (not _sinais.mental(protocolo) or mental_mapa)) else None
    )
    urgencia = _urgencia(sinal_de)
    linha = _sinais.linha(sinal_de, publico)
    logger.info(
        "helena_protocolo_resposta_direta",
        protocolo=protocolo.id if protocolo is not None else "nenhum",
        queixa=entrada.id if entrada is not None else "nenhum",
        urgencia=bool(urgencia),
        contestou=contestou,
    )
    abertura = " ".join(p for p in (TEXTO_DESCULPA if contestou else "", urgencia) if p)
    corpo = f"{abertura} {frase}" if abertura else frase
    return _texto(self, state, f"{corpo}\n\n{linha}")


def _fala_da_queixa(state: Mapping[str, Any], catalogo: _p.Catalogo) -> str:
    """A queixa para a resposta direta na contestacao: a mensagem atual quando ela repete a queixa ("eu disse
    que estou sentindo cansaco..."), senao a ultima fala da pessoa que descreveu um problema."""
    atual = str(state.get("message_body") or "")
    if _ev.ultima_queixa([atual], catalogo):
        return atual
    return _ev.ultima_queixa([f for f in _falas_da_pessoa(state) if f != atual], catalogo) or atual


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
    — nunca a pergunta generica da v4. `None` = o caminho de sempre.

    Teste do dono de 26/10/2026: sem pendencia, a pergunta de QUAL profissional procurar e a sugestao de
    especialista tambem seguem o mapa do protocolo (nunca a navegacao nem o texto do especialista da v4)."""
    catalogo = self._protocolos
    if catalogo is None or catalogo.textos is None:
        return None
    if not antes:
        texto = str(state.get("message_body") or "")
        if not _esp.quer_marcar(texto) and (
            _esp.pergunta_qual_especialista(texto)
            or _esp.sugere_especialista(texto, catalogo.textos)
            or _esp.cita_especialista(texto, catalogo.textos)
            or _esp.contesta_o_assunto(texto)
        ):
            logger.info("helena_protocolo_sem_roteador", etapa="especialista")
            return await conduzir(self, state, _SemRoteador(), None, None)
        return None
    if antes.startswith(PENDENTE_OFERTA_EQUIPE):
        # Teste do dono (26/10/2026, 17:06 UTC): o "nao" a oferta da equipe ia ao classificador e recomecava
        # as perguntas do protocolo. A resposta a oferta e' do codigo (a recusa curta; o "sim" abre o caso).
        from maezo.agents.helena.habilidades.atendimento_humano import oferta as _oferta

        resposta = _oferta.responder(self, state)
        if resposta is not None:
            logger.info("helena_protocolo_sem_roteador", etapa="oferta")
        return resposta
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
