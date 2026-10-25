"""Habilidade `saude` da Helena (ADR-0067 §5): a duvida geral de saude — a base curada (ADR-0064) e a redacao
gerativa da allowlist do DPO (ADR-0065), com a fonte pelo nome.

MOVIDO SEM MUDANCA de `graph.py` (Plano de ajustes da Helena v2, etapa 6, PR so' de mudanca de lugar): cada
metodo do `HelenaGraph` virou uma funcao deste modulo com o MESMO corpo (`self` e' o grafo) e a classe aponta
para ela. O comportamento muda so' no PR seguinte.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo LOGO ANTES da classe e este modulo importa os nomes
do graph NO FIM.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from maezo.agents.helena import roteador_v7 as _rv7
from maezo.agents.helena.consultas_plano import INTENT_CONSULTA_PLANO
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from maezo.agents.helena.habilidades.saude.textos import CHAVE_FECHO, CHAVE_OFERTA_MEDICO
from maezo.agents.helena.helena_v4 import (
    ALERTAS,
    ETAPA_ALERTA,
    ETAPA_ESCOLHA,
    GRUPO_GERAL,
    LEITURA_NAO,
    LEITURA_SIM,
    POPULACAO_ADULTO,
    POPULACAO_CRIANCA,
    Pendente,
    codigo_pendente,
    escolha_da_resposta,
    especialidade_pedida,
    populacao_da_v4,
    resposta_ao_alerta,
    texto_da_opcao_1,
)
from maezo.agents.helena.triagem_v2 import relata_sintoma
from maezo.conhecimento import base_saude as _bs
from maezo.conhecimento import jornadas as _jornadas
from maezo.conhecimento import saude_gerativa as _sg
from maezo.contratos.conversa import INTENT_COBRANCA
from maezo.domain.comunicacao.cerca_de_saida import motivo_de_canal_nao_confirmado, motivo_de_recusa
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.prompt_format import render_untrusted_block

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph


def _base_hoje(self: HelenaGraph) -> _bs.BaseSaude:
    """A base APLICAVEL hoje (sem os assuntos com `revisar_ate` vencido ou outro problema de politica),
    recalculada uma vez por dia (o relogio e' o injetavel do grafo)."""
    hoje = self._relogio().date()
    chave = hoje.isoformat()
    if self._base_do_dia is None or self._base_do_dia[0] != chave:
        self._base_do_dia = (chave, _bs.base_aplicavel(self._base, hoje=hoje))
    return self._base_do_dia[1]


async def _base_antes_dos_gatilhos(
    self: HelenaGraph,
    state: HelenaState,
    update: dict[str, Any],
    extraction: dict[str, Any],
    pend_v4: Pendente | None,
    conversa: str | None,
) -> dict[str, Any] | None:
    """O que a base decide depois dos portoes de seguranca e antes dos gatilhos de intencao/pessoa.
    `None` = o turno segue o caminho de sempre (inclusive a v4)."""
    texto = str(state.get("message_body") or "")
    plano = _plano(texto)
    base = self._base_hoje()
    informativa = _bs.pergunta_informativa(plano)
    gerativa = self._gerativa_para(state)
    # RODADA DO DONO (18/10/2026), so' na allowlist: DUVIDA EDUCATIVA com palavras de sintoma ("alteracoes
    # no
    # fluxo, colicas intensas... sao normais ou podem indicar endometriose?") nao e' queixa atual: vai a
    # redacao, sem perguntas de alerta, sem caso.
    duvida = gerativa and _sg.e_duvida_educativa(plano)
    if duvida:
        informativa = True
    # Seguranca (r4 de 22/10/2026): um SINTOMA da base citado em primeira pessoa ou de um familiar ("minha
    # filha bateu a cabeca na quina da mesa") e' queixa, mesmo sem o verbo de sintoma: recebe as perguntas
    # de alerta do arquivo, nunca a informacao geral.
    queixa = (
        (relata_sintoma(plano, None) or bool(_bs.sintomas_citados(base, texto)))
        and _bs.primeira_pessoa(plano)
        and not duvida
    )
    # (1) ALARME de um arquivo `sin-*` numa QUEIXA: escala na hora, como a tabela (192 no texto).
    # Avaliacao de 21/10/2026 (seguranca): com um assunto de sintoma EM CURSO (pergunta de alerta ou
    # opcoes
    # pendentes), a resposta e o que vier no turno seguinte sao avaliados contra os sinais DAQUELE assunto
    # ("vomitou duas vezes" depois da batida na cabeca), sem precisar citar o sintoma de novo.
    em_curso = pend_v4.grupo if pend_v4 is not None and pend_v4.grupo.startswith("sin-") else None
    alarme_em_curso = (
        em_curso is not None and _bs.alarme_do_assunto(base, em_curso, texto)
    ) or _bs.alarme_dos_citados_antes(base, self._falas_da_pessoa(state), texto)
    if alarme_em_curso or (
        not duvida and (queixa or extraction.get("sintoma_codigo")) and _bs.alarme_no_texto(base, texto)
    ):
        logger.warning("helena_base_saude_alarme_do_sintoma", node="classify")
        update["intent"] = "symptom"
        update["next_kind"] = "escalate"
        update["escalation_motivo"] = "red_flag_clinico"
        update["escalation_severidade"] = "grave"
        return update
    # (2) RECUSA explicita / pedido de encerrar com caso aberto. Avaliacao r4 de 22/10/2026 (regra do
    # dono): a recusa depois da oferta/abertura do caso ("nao quero falar com ninguem", "nao, deixa,
    # desisto") ENCERRA o caso P2/P3 — inclusive logo depois do caso aberto pela v4 (antes a pendencia
    # da v4 levava a recusa para a v4, que a lia como sintoma novo). A recusa e' a do leitor de
    # encerrar que ja' existe (`pede_para_encerrar_o_atendimento`) ou a resposta curta de recusa
    # (`cobertura_assistida.ler_resposta` = encerrar).
    pede_encerrar = pede_para_encerrar_o_atendimento(texto)
    desiste = _lc.ler_resposta(texto) == "encerrar"
    if (pede_encerrar or desiste) and not _pedido_explicito_com_verbo(
        _PEDIDO_DE_ENCERRAR_O_ATENDIMENTO.sub(" ", _RECUSA_DE_EQUIPE.sub(" ", plano))
    ):
        if await self._casos_abertos_da_conversa(state):
            if gerativa:
                # DL-0131 / avaliacao de 21/10/2026: na allowlist a recusa ENCERRA direto os casos P2/P3.
                return await self._base_confirmacao_de_encerramento(state, update, "sim")
            logger.info("helena_base_saude_confirma_encerramento", node="classify")
            return self._base_texto_fixo(
                update, RESPOSTA_CONFIRMA_ENCERRAMENTO, pendente=PENDENTE_ENCERRAR_CASO
            )
        if pede_encerrar:
            return None
    pergunta_nova = (
        # RODADA DO DONO (18/10/2026): com as perguntas de ALERTA pendentes, uma PERGUNTA NOVA (nao
        # sim/nao,
        # sem quadro atual) sai do alerta e e' tratada como nova — nunca vira caso.
        pend_v4 is not None
        and pend_v4.etapa == ETAPA_ALERTA
        and resposta_ao_alerta(plano) is None
        and (duvida or ("?" in texto and not _sg.queixa_atual(plano)))
    )
    if pend_v4 is not None and not (
        # FASE 2 (18/10/2026), so' na allowlist: depois das OPCOES 1/2, uma mensagem que NAO e' a escolha
        # ("tenho SOP, e' a mesma coisa que cisto?") e' uma pergunta nova, nao resposta a v4. Antes ela
        # caia no gatilho 2 (`intencao_clinica`, caso P2 na enfermagem — teste do dono de 17/10, 22:28).
        gerativa
        and ((pend_v4.etapa == ETAPA_ESCOLHA and escolha_da_resposta(plano) is None) or pergunta_nova)
    ):
        # A pessoa esta' respondendo a v4 (alerta/opcao): quem decide e' a v4.
        return None
    # (3) PERGUNTA INFORMATIVA / RESULTADO PESSOAL. Nunca por cima de pedido de pessoa, ANS/Procon,
    # cobranca, consulta/cobertura do plano ou pedido de especialista (a v4 responde onde marcar).
    if self._v4_pede_pessoa(state, texto) or conversa is not None:
        return None
    if update.get("intent") in (INTENT_COBRANCA, INTENT_CONSULTA_PLANO, "human_request"):
        return None
    if _pergunta_do_plano(texto) or _pergunta_de_cobertura(texto):
        return None
    pessoal = _bs.resultado_pessoal(plano)
    if queixa:
        # QUEIXA ATUAL (sintoma dito em primeira pessoa ou de um familiar: "estou com", "meu filho esta'
        # com dor"): nunca e' "duvida geral" nem "resultado pessoal" — segue a tabela/v4 (alerta).
        if gerativa and update.get("intent") == "clinical_question" and _sg.queixa_atual(plano):
            # ROTEIRO helena_fases_18_10 (18/10/2026), so' na allowlist: "estou com dor nas costas, e'
            # grave?" e' QUEIXA com uma pergunta junto — vai a tabela e as perguntas de alerta como
            # sintoma,
            # nunca ao gatilho 2 (`intencao_clinica`, o transbordo falso que o dono quer eliminar).
            logger.info("helena_queixa_com_pergunta_vira_sintoma", node="classify")
            update["intent"] = "symptom"
        return None
    # SAUDE GERATIVA (ADR-0065), so' na allowlist: mais formas de pergunta de saude ("como tratar?",
    # "quais
    # os sintomas?") e o SEGUIMENTO curto de uma pergunta de saude anterior ("E osteoporose?").
    seguimento = False
    clinica = update.get("intent") == "clinical_question"
    pessoal_saude = False
    if gerativa:
        informativa = informativa or _sg.pergunta_de_saude(plano)
        seguimento = _sg.e_seguimento(plano, self._falas_da_pessoa(state))
        if not (informativa or pessoal or seguimento) and not (
            _sinal_forte_de_emergencia(texto) or _sinal_fraco_de_emergencia(texto)
        ):
            # FASE 2 (18/10/2026): DUVIDA sobre um diagnostico/condicao DA PESSOA, sem queixa atual
            # ("tenho SOP, e' a mesma coisa que cisto?", "fui diagnosticada com mioma, e' perigoso?"). A
            # tabela de
            # alarme ja' rodou acima (gatilho 1, com codigo de sintoma) e a queixa atual ja' saiu
            # (`queixa`): vai a redacao, nunca ao gatilho 2.
            assunto_de_saude = (
                clinica
                or update.get("intent") == "symptom"
                or bool(extraction.get("sintoma_codigo"))
                or _bs.buscar(base, texto).achado is not None
                or await self._busca_semantica(state, base, texto) is not None
            )
            pessoal_saude = _sg.pergunta_pessoal_de_saude(plano, texto, assunto_de_saude=assunto_de_saude)
            informativa = pessoal_saude
    if extraction.get("sintoma_codigo") and not (informativa or pessoal or seguimento):
        return None
    if not (informativa or pessoal or clinica or seguimento):
        return None
    if pergunta_nova and not (informativa or pessoal or clinica or seguimento):
        informativa = True
    if not informativa and especialidade_pedida(plano) is not None:
        return None  # "preciso de um cardiologista": o texto de onde marcar (v4), nao "o que e'"
    if gerativa:
        if pessoal_saude:
            logger.info("helena_saude_gerativa_pergunta_pessoal", node="classify")
        decidido_g = await self._gerativa_informar(
            state,
            update,
            base,
            modo=_sg.MODO_RESULTADO if pessoal else _sg.MODO_INFORMAR,
            pessoal=pessoal_saude,
        )
        if decidido_g is not None:
            return decidido_g
    assunto = await self._base_achar_assunto(state, base)
    if assunto is None:
        if not (informativa or clinica):
            return None  # "minha ... esta ..." sem assunto da base e sem pergunta de saude: o de sempre
        logger.info("helena_base_saude_nao_encontrado", node="classify", pessoal=pessoal)
        return self._base_texto_fixo(
            update, _bs.RESPOSTA_RESULTADO_NAO_ENCONTRADO if pessoal else _bs.RESPOSTA_NAO_ENCONTRADO
        )
    logger.info("helena_base_saude_responde", node="classify", assunto=assunto.id, pessoal=pessoal)
    return self._base_texto_fixo(
        update,
        _bs.resposta_de_resultado_pessoal(assunto)
        if pessoal
        # OPCAO C (DL-0154): respondida uma vez, com o fecho "Posso ajudar em mais alguma coisa?".
        else _com_o_fecho_da_duvida(_bs.resposta_informativa(assunto), self._textos[CHAVE_FECHO]),
    )


async def _base_achar_assunto(
    self: HelenaGraph, state: HelenaState, base: _bs.BaseSaude
) -> _bs.Assunto | None:
    """Busca DETERMINISTICA primeiro; so' quando ela fica ambigua o modelo escolhe UM id da lista fechada
    de candidatos (ou "nenhum"). Falha do modelo = nenhum (o texto de 'nao tenho informacao aprovada').
    A mensagem vai ao prompt DEMARCADA (`render_untrusted_block`, HEL-06)."""
    texto = str(state.get("message_body") or "")
    busca = _bs.buscar(base, texto)
    if busca.achado is not None:
        return base.get(busca.achado)
    if not busca.candidatos:
        return None
    prompt = _bs.prompt_de_classificacao(
        base, busca.candidatos, render_untrusted_block("message_body", texto)
    )
    try:
        bruto = await self._llm.generate(
            prompt,
            phi=True,
            agent_id="helena",
            tenant_id=state.get("tenant_id", ""),
            task_kind="task_default",
        )
    except PROGRAMMING_ERRORS:
        raise
    except EXTERNAL_DEPENDENCY_FAILURES:
        logger.warning("helena_base_saude_classificacao_falhou", node="classify")
        return None
    escolhido = _bs.ler_classificacao(str(bruto), busca.candidatos)
    logger.info(
        "helena_base_saude_classificacao",
        node="classify",
        candidatos=len(busca.candidatos),
        escolhido=escolhido or _bs.NENHUM,
    )
    return base.get(escolhido) if escolhido else None


def _base_texto_fixo(update: dict[str, Any], texto: str, *, pendente: str | None = None) -> dict[str, Any]:
    update["intent"] = "information"
    update["sintoma_codigo"] = None
    update.pop("consulta_subtipo", None)
    update["response_text"] = texto
    if pendente is not None:
        update["coleta_pendente"] = pendente
    return _rota_informativa(update)


def _gerativa_para(self: HelenaGraph, state: Mapping[str, Any]) -> bool:
    return self._gerativa is not None and self._gerativa.autorizada(state.get("conversation_id"))


def _falas_da_pessoa(state: Mapping[str, Any]) -> list[str]:
    """As mensagens ANTERIORES da pessoa no historico curto (DL-0080), da mais antiga para a mais recente.
    As falas da Helena NUNCA entram (podem ter dado do plano/carteirinha)."""
    historico = state.get("historico_conversa")
    if not isinstance(historico, list):
        return []
    return [
        str(e.get("texto") or "")
        for e in historico
        if isinstance(e, Mapping) and e.get("papel") == "beneficiario" and e.get("texto")
    ]


def _cercas_da_helena(texto: str) -> list[str]:
    """As cercas de saida da Helena sobre um texto redigido (rotulos, nunca o texto): canal nao
    confirmado, recusa de saida (promessa, negativa clinica...), conduta clinica, negativa de acesso e
    promessa de repasse."""
    rotulos: list[str] = []
    canal = motivo_de_canal_nao_confirmado(texto)
    if canal is not None:
        rotulos.append(str(canal[0]))
    # ROTEIRO 19/10/2026 (PSA): a redacao de saude nunca abre caso, entao a cerca e' a do ENVIO
    # (`start_aconteceu=False`): "o medico vai avaliar" reprova AQUI (e vai para a reescrita), em vez de
    # passar e cair no texto generico de `respond` (`promessa_sem_start`).
    recusa = motivo_de_recusa(texto, "inform", start_aconteceu=False)
    if recusa is not None:
        rotulos.append(str(recusa[0]))
    if _conduta_clinica(texto):
        rotulos.append("conduta_clinica")
    if _nega_acesso(texto):
        rotulos.append("nega_acesso")
    if _PROMESSA_DE_REPASSE.search(_plano(texto)):
        rotulos.append("promessa_de_repasse")
    return rotulos


def _guardar_gerativa(self: HelenaGraph, state: Mapping[str, Any], texto: str) -> None:
    chave = self._chave_do_turno(state)
    while len(self._gerativas_do_turno) >= 16:
        self._gerativas_do_turno.pop(next(iter(self._gerativas_do_turno)))
    self._gerativas_do_turno[chave] = texto


def _e_gerativa_do_turno(self: HelenaGraph, state: Mapping[str, Any]) -> bool:
    texto = state.get("response_text")
    return (
        self._gerativa is not None
        and isinstance(texto, str)
        and bool(texto)
        and self._gerativas_do_turno.get(self._chave_do_turno(state)) == texto
    )


def _gerativa_e_crianca(update: Mapping[str, Any], plano: str) -> bool:
    # RODADA DO DONO (18/10/2026): a populacao vale SO' para a pergunta atual (ou quando a pessoa a cita)
    # —
    # "existe alguma pilula que eu possa substituir..." herdou "crianca" do assunto anterior (o bebe).
    return populacao_da_v4("none", plano) == POPULACAO_CRIANCA


async def _busca_semantica(
    self: HelenaGraph,
    state: Mapping[str, Any],
    base: _bs.BaseSaude,
    texto: str,
    *,
    crianca: bool | None = None,
) -> _bs.Assunto | None:
    """FASE 3: o assunto da base pela busca por SIGNIFICADO (embeddings regionais em sa-east-1), so' com a
    chave ligada e na allowlist; uma vez por turno (o resultado fica guardado pela chave do turno).
    `None` =
    abaixo do limiar, chave desligada ou falha (o lexico de antes vale)."""
    busca = getattr(self._gerativa, "busca", None)
    if busca is None or not self._gerativa_para(state):
        return None
    chave = (self._chave_do_turno(state), texto, crianca)
    if chave in self._semantica_do_turno:
        return self._semantica_do_turno[chave]

    def _compativel(assunto: _bs.Assunto) -> bool:
        if crianca is None or assunto.populacao in (None, "todos"):
            return True
        return assunto.populacao == ("crianca" if crianca else "adulto") or (
            not crianca and assunto.populacao == "gestante"
        )

    achado = await busca.buscar(base, _sg.minimizar(texto), filtro=_compativel)
    assunto = base.get(achado.assunto_id) if achado is not None else None
    while len(self._semantica_do_turno) >= 32:
        self._semantica_do_turno.pop(next(iter(self._semantica_do_turno)))
    self._semantica_do_turno[chave] = assunto
    return assunto


async def _apoio_da_pergunta(
    self: HelenaGraph, state: Mapping[str, Any], base: _bs.BaseSaude, texto: str, *, crianca: bool
) -> _sg.Apoio | None:
    """O APOIO da redacao: o assunto pela busca por significado (acima do limiar, com o lexico como
    desempate)
    e, sem ele, a busca lexica confiavel de antes. Nunca e' portao: sem apoio, a redacao segue sem
    fonte."""
    assunto = await self._busca_semantica(state, base, texto, crianca=crianca)
    if assunto is not None:
        apoio = _sg.apoio_do_assunto(assunto)
        if apoio is not None:
            return apoio
    return _sg.apoio_para(base, texto)


def _contexto_do_mesmo_assunto(
    self: HelenaGraph,
    state: Mapping[str, Any],
    base: _bs.BaseSaude,
    anteriores: tuple[str, ...],
    apoio: _sg.Apoio | None,
    plano: str,
) -> tuple[tuple[str, ...], str]:
    """FASE 3: so' as mensagens anteriores da pessoa do MESMO assunto (uma anterior de outro assunto da
    base
    sai; seguimento curto "E osteoporose?" mantem tudo) e o TITULO do assunto anterior (resumo
    fechado)."""
    assunto_anterior = ""
    mantidas: list[str] = []
    seguimento = _sg.e_seguimento(plano, self._falas_da_pessoa(state))
    for anterior in anteriores:
        achado = _sg.assunto_confiavel(base, anterior)
        if achado is not None and (apoio is None or achado.id != apoio.assunto_id):
            assunto_anterior = achado.titulo
            if apoio is not None and not seguimento:
                continue
        mantidas.append(anterior)
    return tuple(mantidas), assunto_anterior


def _populacao_do_resumo(
    state: Mapping[str, Any], update: Mapping[str, Any], contexto: str, crianca: bool
) -> str:
    """FASE 3: a populacao em vocabulario fechado para o resumo da redacao."""
    if crianca:
        return "crianca"
    # RODADA DO DONO (18/10/2026): so' o que a pergunta atual diz (nunca a memoria de outro assunto).
    if re.search(r"\b(?:gravida|gestante|gravidez|gestacao)\b", _plano(contexto)):
        return "gestante"
    idade = update.get("idade_anos")
    if isinstance(idade, int) and idade >= 60:
        return "idoso"
    return "adulto"


async def _gerativa_informar(
    self: HelenaGraph,
    state: HelenaState,
    update: dict[str, Any],
    base: _bs.BaseSaude,
    *,
    modo: str,
    pessoal: bool = False,
) -> dict[str, Any] | None:
    """Pergunta informativa / resultado pessoal: o modelo redige (com o apoio da base quando o assunto
    casa de verdade). `None` = a rota recusou a mensagem (risco): o turno segue o caminho da base."""
    assert self._gerativa is not None
    texto = str(state.get("message_body") or "")
    plano = _plano(texto)
    anteriores = _sg.contexto_da_pessoa(self._falas_da_pessoa(state))
    contexto = " ".join((*anteriores, texto))
    crianca = self._gerativa_e_crianca(update, plano)
    if not crianca:
        # As mensagens anteriores que falam de crianca nao entram numa pergunta nova de adulto.
        anteriores = tuple(a for a in anteriores if populacao_da_v4("none", _plano(a)) != POPULACAO_CRIANCA)
    apoio = await self._apoio_da_pergunta(state, base, texto, crianca=crianca)
    if _insiste_no_mesmo_assunto(self, state, base, apoio, plano):
        # OPCAO C (dono, 23/10/2026, DL-0154): a duvida geral e' respondida UMA vez; quem insiste no MESMO
        # assunto recebe o medico ou a equipe de saude (nunca uma segunda explicacao).
        logger.info("helena_saude_insistencia_no_mesmo_assunto", node="classify")
        return self._base_texto_fixo(
            update, self._textos[CHAVE_OFERTA_MEDICO], pendente=PENDENTE_SAUDE_OFERTA
        )
    anteriores, assunto_anterior = self._contexto_do_mesmo_assunto(state, base, anteriores, apoio, plano)
    # TINA (18/10/2026): no maximo UM passo da jornada do plano (texto fixo da operadora, acrescentado no
    # fim).
    jornada = _jornadas.jornada_da_pergunta(plano, apoio.categoria if apoio is not None else None)
    pedido = _sg.Pedido(
        modo=modo,
        mensagem=_sg.minimizar(texto),
        anteriores=anteriores,
        apoio=apoio,
        crianca=crianca,
        # FASE 3: oferta so' quando faz sentido (resultado, duvida sobre a propria condicao, tratamento).
        oferecer=modo == _sg.MODO_RESULTADO or pessoal or _sg.pede_tratamento(plano),
        forma_da_oferta=len(_falas_da_helena(state)),
        populacao=self._populacao_do_resumo(state, update, contexto, crianca),
        assunto_anterior=assunto_anterior,
        # TINA (18/10/2026): o que a PROPRIA pessoa disse de si antes (fuma, bebe...), so' dela.
        fatos_da_pessoa=_sg.fatos_ditos_pela_pessoa(self._falas_da_pessoa(state)),
        com_jornada=jornada is not None,
    )
    redacao = await self._gerativa.redigir(pedido, cercas=self._cercas_da_helena)
    if redacao is None:
        return None
    texto_final = redacao.texto
    if redacao.desfecho not in ("padrao", "falha") and "Fonte:" not in texto_final:
        # DL-0154 (o modelo conduz): o rascunho VALIDO sem a linha da fonte nao e' mais descartado (era o que
        # trocava a resposta por "nao tenho informacao aprovada"). Com apoio da base, a fonte entra PELO NOME
        # (nunca link); sem apoio, a resposta sai sem a linha.
        logger.info(
            "helena_saude_gerativa_sem_fonte", node="classify", modo=modo, com_apoio=apoio is not None
        )
        if apoio is not None and apoio.fonte_nome:
            texto_final = f"{texto_final.rstrip()}\nFonte: {apoio.fonte_nome}."
    if jornada is not None and redacao.desfecho not in ("padrao", "falha"):
        texto_final = f"{texto_final}\n{_jornadas.TEXTOS[jornada]}"
        logger.info("helena_jornada_do_plano", node="classify", jornada=jornada)
    if redacao.desfecho not in ("padrao", "falha"):
        texto_final = _com_o_fecho_da_duvida(texto_final, self._textos[CHAVE_FECHO])
    self._guardar_gerativa(state, texto_final)
    logger.info("helena_saude_gerativa_responde", node="classify", modo=modo, desfecho=redacao.desfecho)
    return self._base_texto_fixo(update, texto_final)


async def _gerativa_no_alerta(
    self: HelenaGraph, state: HelenaState, update: dict[str, Any], pend: Pendente, texto: str
) -> dict[str, Any] | None:
    """A resposta da pessoa as perguntas de ALERTA (so' na allowlist): "nao" -> a orientacao redigida
    (no lugar do texto fixo com o eco truncado e o "clinico geral ou pediatra"); CONTEXTO sem sinal de
    alarme ("comecou hoje, ja usei X e nao melhorou") -> a orientacao redigida com os sinais que pedem 192
    (no lugar de repetir a pergunta). "Sim", duvida, agravamento, alarme, pedido de pessoa ou outro
    assunto
    -> `None` (a v4 de sempre decide). O fecho com as opcoes 1/2 e' FIXO e a escolha seguinte, da v4."""
    assert self._gerativa is not None
    if not _GERATIVA_NO_ALERTA:
        # Avaliacao r4 de 22/10/2026: o SINTOMA relatado segue o fluxo do sintoma (pergunta de alarme e o
        # texto fixo da v4); a redacao fica so' para a PERGUNTA informativa. A redacao no alerta citava o
        # 192 para dor leve sem sinal de alarme (alarme falso).
        return None
    if self._v4_pede_pessoa(state, texto):
        return None
    plano = _plano(texto)
    relato = str(state.get("coleta_contexto") or "")
    leitura = resposta_ao_alerta(plano)
    if leitura == LEITURA_SIM:
        return None
    if leitura == LEITURA_NAO:
        modo = _sg.MODO_SINTOMA_SEM_ALARME
    else:
        junto = f"{relato} {texto}"
        if (
            _sinal_forte_de_emergencia(texto)
            or _sinal_fraco_de_emergencia(texto)
            or _bs.alarme_no_texto(self._base_hoje(), junto)
            or _pergunta_do_plano(texto)
            or _pergunta_de_cobertura(texto)
            or not _sg.traz_contexto_da_queixa(plano)
        ):
            return None
        modo = _sg.MODO_SINTOMA_CONTEXTO
    assunto_sin = self._base_assunto_do_grupo(pend.grupo)
    if assunto_sin is not None:
        apoio = _sg.apoio_de_sintoma(assunto_sin)
        sinais = ", ".join((assunto_sin.sinais_alarme or ())[:4])
    else:
        alerta = ALERTAS.get(pend.grupo, ALERTAS[GRUPO_GERAL])
        apoio = _sg.apoio_de_sinais(alerta.sinais)
        sinais = alerta.sinais
    anteriores = _sg.contexto_da_pessoa(self._falas_da_pessoa(state) or [relato])
    crianca = pend.populacao == POPULACAO_CRIANCA
    pedido = _sg.Pedido(
        modo=modo,
        mensagem=_sg.minimizar(texto),
        anteriores=anteriores,
        apoio=apoio,
        crianca=crianca,
        sinais=sinais,
        # FASE 3: o resumo em variaveis fechadas (populacao, alarme ja' negado).
        populacao=self._populacao_do_resumo(state, update, f"{relato} {texto}", crianca),
        alarme_negado=modo == _sg.MODO_SINTOMA_SEM_ALARME,
    )
    redacao = await self._gerativa.redigir(pedido, cercas=self._cercas_da_helena)
    if redacao is None:
        return None
    final = f"{redacao.texto}\n{_sg.opcoes_do_sintoma(crianca)}"
    self._guardar_gerativa(state, final)
    logger.info("helena_saude_gerativa_responde", node="classify", modo=modo, desfecho=redacao.desfecho)
    update["intent"] = "information"
    update["sintoma_codigo"] = None
    update["response_text"] = final
    update["coleta_pendente"] = codigo_pendente(ETAPA_ESCOLHA, pend.grupo, pend.populacao)
    update["coleta_contexto"] = relato
    return _rota_informativa(update)


#: A pergunta pendente da OPCAO C ("quer que eu te mostre onde marcar a consulta ou prefere a equipe?").
PENDENTE_SAUDE_OFERTA = "SAUDE_OFERTA"


def _com_o_fecho_da_duvida(texto: str, fecho: str) -> str:
    """OPCAO C (DL-0154): a resposta da duvida geral termina no fecho do dono ("... nao substitui avaliacao
    medica. Posso ajudar em mais alguma coisa?") no lugar do aviso que oferecia a equipe a cada resposta."""
    corpo = texto.replace(_bs.AVISO_PADRAO, "").rstrip()
    if corpo.endswith(fecho):
        return corpo
    return f"{corpo}\n{fecho}"


def _insiste_no_mesmo_assunto(
    self: HelenaGraph, state: Mapping[str, Any], base: _bs.BaseSaude, apoio: _sg.Apoio | None, plano: str
) -> bool:
    """A Helena acabou de responder uma duvida geral (a ultima fala dela termina no fecho da OPCAO C) e a
    pessoa continua no MESMO assunto: o mesmo assunto da base, ou um seguimento sem assunto novo ("e como
    trata?"). Uma pergunta DIFERENTE ("e osteoporose?") e' respondida uma vez, como a primeira."""
    falas = _falas_da_helena(state)
    if not falas or not falas[-1].rstrip().endswith(self._textos[CHAVE_FECHO]):
        return False
    v7 = _rv7.LEITURA_DO_TURNO.get()
    if v7 is not None:
        # Contrato v7: o MESMO assunto e' o que o modelo le' como continuacao da duvida geral.
        return v7.continuacao and v7.tipo == "duvida_geral"
    pessoa = self._falas_da_pessoa(state)
    if not pessoa:
        return False
    anterior = _sg.assunto_confiavel(base, pessoa[-1])
    if apoio is not None:
        return anterior is not None and apoio.assunto_id == anterior.id
    # Sem assunto novo achado: so' o pedido de tratamento ("e como trata?") ou a pergunta curta que se refere
    # ao assunto anterior ("isso e' grave?", "e ele tem cura?") continuam o MESMO assunto.
    return _sg.pede_tratamento(plano) or (
        len(plano.split()) <= 6 and _REFERE_AO_ANTERIOR.search(plano) is not None
    )


#: A pergunta que se refere ao assunto da resposta anterior ("isso", "ele", "ela", "disso"), sem assunto novo.
_REFERE_AO_ANTERIOR = re.compile(r"\b(?:isso|isto|disso|nisso|ele|ela|dele|dela|essa doenca|esse problema)\b")


def _saude_resposta_a_oferta(
    self: HelenaGraph, state: HelenaState, update: dict[str, Any], texto: str
) -> dict[str, Any] | None:
    """A resposta a oferta da OPCAO C: "onde marcar" -> o caminho da consulta; "a equipe" -> caso com a equipe
    de saude (`intencao_clinica`, so' com a escolha da pessoa); "nao" -> fecha. O resto: `None`."""
    if state.get("coleta_pendente") != PENDENTE_SAUDE_OFERTA:
        return None
    from maezo.agents.helena.habilidades.sintoma.fluxo import _escolha_marcar_ou_equipe

    escolha = _escolha_marcar_ou_equipe(_plano(texto))
    if escolha == "1":
        logger.info("helena_saude_oferta", node="classify", opcao="1")
        return self._base_texto_fixo(update, texto_da_opcao_1(POPULACAO_ADULTO))
    if escolha == "2":
        logger.info("helena_saude_oferta", node="classify", opcao="2")
        update.update(
            {
                "intent": "human_request",
                "next_kind": "escalate",
                "escalation_motivo": "intencao_clinica",
                "escalation_severidade": "moderada",
                "coleta_pendente": None,
            }
        )
        return update
    if escolha == "nao":
        logger.info("helena_saude_oferta", node="classify", opcao="nao")
        return self._base_texto_fixo(update, _lc.TEXTO_COMBINADO)
    return None


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
from maezo.agents.helena.graph import (  # noqa: E402
    _GERATIVA_NO_ALERTA,
    _PEDIDO_DE_ENCERRAR_O_ATENDIMENTO,
    _PROMESSA_DE_REPASSE,
    _RECUSA_DE_EQUIPE,
    PENDENTE_ENCERRAR_CASO,
    RESPOSTA_CONFIRMA_ENCERRAMENTO,
    HelenaState,
    _conduta_clinica,
    _falas_da_helena,
    _nega_acesso,
    _pedido_explicito_com_verbo,
    _pergunta_de_cobertura,
    _pergunta_do_plano,
    _plano,
    _rota_informativa,
    _sinal_forte_de_emergencia,
    _sinal_fraco_de_emergencia,
    logger,
    pede_para_encerrar_o_atendimento,
)
