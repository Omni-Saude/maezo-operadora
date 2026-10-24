"""Habilidade `sintoma` da Helena (ADR-0067 §5): a queixa da pessoa — perguntas de alerta da Helena v4 e dos
arquivos `sin-*` da base, a resposta a elas, as opcoes depois do "nao", o pedido de especialista, a triagem
nova (DL-0104, desligada) e a suficiencia da coleta.

MOVIDO SEM MUDANCA de `graph.py` (Plano de ajustes da Helena v2, etapa 6, PR so' de mudanca de lugar): cada
metodo do `HelenaGraph` virou uma funcao deste modulo com o MESMO corpo (`self` e' o grafo) e a classe aponta
para ela (`HelenaGraph._v4_seguimento = fluxo._v4_seguimento`). O comportamento muda so' no PR seguinte.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo LOGO ANTES da classe (os helpers de modulo ja'
existem) e este modulo importa os nomes do graph NO FIM. Assim tanto `import graph` quanto `import` deste
modulo primeiro funcionam.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from maezo.agents.helena import roteador_v7 as _rv7
from maezo.agents.helena.alarme_deterministico import alarme_no_relato
from maezo.agents.helena.consultas_plano import (
    INTENT_CONSULTA_PLANO,
)
from maezo.agents.helena.habilidades.cobertura_fora import CHAVE_TEXTO as CHAVE_TEXTO_COBERTURA_FORA
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from maezo.agents.helena.habilidades.sintoma.textos import (
    CHAVE_AVALIAR_CAUSA,
    CHAVE_OFERTA_EQUIPE,
    CHAVE_OFERTA_RECUSADA,
)
from maezo.agents.helena.helena_v4 import (
    ALERTAS,
    ECO_GENERICO,
    ETAPA_ALERTA,
    ETAPA_CASO_EQUIPE,
    ETAPA_CASO_SIM,
    ETAPA_ESCOLHA,
    ETAPA_OFERTA,
    GRUPO_GERAL,
    LEITURA_NAO,
    LEITURA_SIM,
    POPULACAO_ADULTO,
    POPULACAO_CRIANCA,
    AlertaDoGrupo,
    Pendente,
    codigo_pendente,
    e_agravamento,
    eco_do_relato,
    escolha_da_resposta,
    especialidade_pedida,
    grupo_do_sintoma,
    ler_pendente,
    pede_carteirinha,
    populacao_da_v4,
    relato_cru,
    resposta_ao_alerta,
    texto_da_opcao_1,
    texto_do_especialista,
    texto_final_do_sintoma,
)
from maezo.agents.helena.triagem_v2 import (
    COLETA_MAX_RODADAS_TRIAGEM_V2,
    COLETA_VEREDITO_PERGUNTAR_MAIS,
    DECISAO_ESCLARECER,
    DECISAO_PASSAR_AO_LUCAS,
    DECISAO_PERGUNTAR_MAIS,
    DECISOES_QUE_ABREM_CASO,
    ITEM_IDADE,
    ITEM_INTENSIDADE,
    ITEM_SEMANAS,
    NECESSIDADE_NAO_CLARA,
    NECESSIDADES_DE_SINTOMA,
    RESPOSTA_TRIAGEM_ESCLARECER,
    RESPOSTA_TRIAGEM_OPCAO_PLANO,
    TEXTOS_POR_ID,
    TRIAGEM_V2_DMN_KEY,
    SaidaInvalidaError,
    bebe_pequeno,
    codigo_da_ficha,
    grupo_da_queixa,
    itens_ja_respondidos,
    itens_que_faltam,
    ler_codigo_da_ficha,
    ler_linha,
    necessidade_da_pergunta_clinica,
    necessidade_do_sintoma,
    necessidade_vaga,
    paciente_da_conversa,
    relata_sintoma,
    resposta_curta,
    resposta_numerada,
    texto_de_orientacao,
)
from maezo.conhecimento import base_saude as _bs
from maezo.conhecimento import saude_gerativa as _sg
from maezo.contratos.conversa import INTENT_COBRANCA
from maezo.domain.comunicacao.cerca_de_saida import motivo_de_canal_nao_confirmado, motivo_de_recusa
from maezo.runtime.error_text import dmn_unavailable_error
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnNoResultError, first_row

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph


async def _avaliar_suficiencia(
    self: HelenaGraph, state: HelenaState, update: dict[str, Any]
) -> dict[str, Any] | None:
    """Consulta `triage_sufficiency` e devolve a ATUALIZACAO de estado que decide o turno —
    ou `None` quando os dados bastam e o caminho informativo segue como antes.

    A tabela recebe FATOS sobre o que foi apurado (nunca o texto): a intencao, a populacao,
    se o sintoma casou com um codigo, se a intensidade foi dita, se o campo da populacao esta'
    presente e quantas perguntas ja' foram feitas. Ela devolve UM veredito. Este metodo nao
    interpreta clinica: converte o veredito em rota.

    TEM FIM antes de perguntar a tabela: com `coleta_rodadas >= COLETA_MAX_RODADAS` escala,
    qualquer que fosse o veredito — e' a regra 2 do desenho, e mora no codigo porque nao pode
    depender de a tabela lembrar-se dela.

    FAIL-CLOSED em tres formas: tabela indisponivel, sem linha casada, ou veredito fora do
    vocabulario -> `falha_tecnica` -> humano. Nunca "assume suficiente".
    """
    rodadas = _rodadas_de_coleta(state.get("coleta_rodadas", 0))
    if rodadas >= COLETA_MAX_RODADAS:
        logger.info("helena_coleta_esgotada", rodadas=rodadas, node="classify")
        return {
            "coleta_veredito": COLETA_VEREDITO_ESCALAR,
            "next_kind": "escalate",
            "escalation_motivo": MOTIVO_COLETA_ESGOTADA,
            "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
            "coleta_pendente": None,
        }

    entrada = _entrada_de_suficiencia(update, rodadas)
    try:
        rows, version = await self._dmn.evaluate(SUFFICIENCY_DMN_KEY, entrada)
        row = first_row(rows, SUFFICIENCY_DMN_KEY, entrada)
    except (DmnEvaluationError, DmnNoResultError) as exc:
        return {
            "coleta_veredito": None,
            "next_kind": "escalate",
            "escalation_motivo": "falha_tecnica",
            "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
            "error": dmn_unavailable_error(SUFFICIENCY_DMN_KEY, exc),
        }
    veredito = row.get("veredito")
    veredito = veredito.get("value") if isinstance(veredito, dict) else veredito
    veredito = str(veredito or "")
    ref = f"{SUFFICIENCY_DMN_KEY}#{version.id}"
    if veredito == COLETA_VEREDITO_SUFICIENTE:
        logger.info("helena_coleta_suficiente", ref=ref, node="classify")
        return None
    if veredito in COLETA_VEREDITOS_PERGUNTA:
        logger.info("helena_coleta_pergunta", veredito=veredito, rodada=rodadas + 1, ref=ref, node="classify")
        return {
            "coleta_veredito": veredito,
            "coleta_pergunta": veredito,
            "coleta_rodadas": rodadas + 1,
            "coleta_pendente": veredito,
            # O texto da mensagem ja' chegou pseudonimizado na borda; guarda-lo aqui e' o que
            # permite ao turno seguinte classificar "forte" junto com "dor de cabeca".
            "coleta_contexto": " | ".join(
                t for t in (state.get("coleta_contexto") or "", state.get("message_body") or "") if t
            ),
            "next_kind": "collect",
        }
    if veredito == COLETA_VEREDITO_ESCALAR:
        return {
            "coleta_veredito": veredito,
            "next_kind": "escalate",
            "escalation_motivo": MOTIVO_COLETA_ESGOTADA,
            "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
            "coleta_pendente": None,
        }
    # Vocabulario desconhecido: a tabela nao e' confiavel para este caso -> humano.
    logger.warning(
        "helena_coleta_veredito_desconhecido",
        classification="fora_do_vocabulario",
        ref=ref,
        node="classify",
    )
    return {
        "coleta_veredito": None,
        "next_kind": "escalate",
        "escalation_motivo": "falha_tecnica",
        "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
        "error": f"{SUFFICIENCY_DMN_KEY}: veredito fora do vocabulario",
    }


def _esclarecimento_ja_pedido(state: Mapping[str, Any]) -> bool:
    """A pergunta de esclarecimento (T11) ja' saiu nesta conversa? Pelo historico curto (DL-0080)."""
    inicio = RESPOSTA_TRIAGEM_ESCLARECER[:60]
    return any(_sem_prefixo(f).startswith(inicio) for f in _falas_da_helena(state))


def _opcao_do_esclarecimento(state: Mapping[str, Any], texto: str) -> str | None:
    """A opcao ("1".."4") que a mensagem escolhe, SO' com a oferta de opcoes (T11) RECENTE: entre as
    ultimas `_FALAS_DA_OFERTA_RECENTE` falas da Helena no historico curto (DL-0080) — inclusive depois
    do "Ainda nao entendi" (catch-all). Sem oferta recente, um numero solto segue o fluxo normal."""
    numero = resposta_numerada(_plano(texto))
    if numero is None:
        return None
    inicio = RESPOSTA_TRIAGEM_ESCLARECER[:60]
    recentes = _falas_da_helena(state)[-_FALAS_DA_OFERTA_RECENTE:]
    if not any(_sem_prefixo(f).startswith(inicio) for f in recentes):
        return None
    return numero


def _triagem_v2_opcao(
    self: HelenaGraph,
    opcao: str,
    state: HelenaState,
    update: dict[str, Any],
    *,
    pessoa_dentro_da_triagem: bool,
) -> dict[str, Any] | None:
    """O turno ja' roteado para a OPCAO escolhida do T11, ou `None` (opcao 1 com sintoma ja' relatado:
    o turno segue como sintoma e a tabela nova leva a ficha).

      1 -> convite a contar o que sente (`RESPOSTA_DUVIDA_DE_SAUDE`), sem caso;
      2 -> o caminho da consulta do plano pela Helena (`RESPOSTA_TRIAGEM_OPCAO_PLANO`), sem caso;
      3 -> o Lucas pela passagem de cobranca de sempre (`_rota_de_cobranca`, precondicao de 2
           camadas; o handoff so' nasce no no' `handoff_cobranca`). Sem o roteador, fora do canal;
      4 -> pedido de pessoa (RN 623/2024, art. 9, III): abre o caso como o pedido explicito —
           `solicitacao_humano`/`leve`; dentro da triagem, falando do quadro, `intencao_clinica`.
    """
    logger.info("helena_triagem_v2_opcao", node="classify", opcao=opcao)
    if opcao == "4":
        update["intent"] = "human_request"
        update["next_kind"] = "escalate"
        if pessoa_dentro_da_triagem:
            update["escalation_motivo"] = "intencao_clinica"
            update["escalation_severidade"] = "moderada"
        else:
            update["escalation_motivo"] = "solicitacao_humano"
            update["escalation_severidade"] = "leve"
        return update
    if opcao == "1" and update.get("sintoma_codigo"):
        update["intent"] = "symptom"
        return None
    if opcao == "3" and self._roteador_lucas_enabled:
        update["intent"] = INTENT_COBRANCA
        subtipo = _subtipo_pelo_texto(str(state.get("message_body") or ""))
        self._preencher_cobranca(update, subtipo, None, state)
        return _rota_de_cobranca(update, state, roteador_ligado=self._roteador_lucas_enabled)
    update["intent"] = "information"
    update["response_text"] = {
        "1": RESPOSTA_DUVIDA_DE_SAUDE,
        "2": RESPOSTA_TRIAGEM_OPCAO_PLANO,
    }.get(opcao, RESPOSTA_FORA_DO_CANAL)
    return _rota_informativa(update)


async def _triagem_v2_decidir(
    self: HelenaGraph,
    state: HelenaState,
    update: dict[str, Any],
    extraction: dict[str, Any],
    *,
    ja_triado: bool,
    conversa: str | None,
) -> dict[str, Any] | None:
    """Apura a NECESSIDADE do catalogo, faz a ficha (se for sintoma), consulta a tabela nova e devolve o
    `update` roteado — ou `None` quando o turno nao e' da nova triagem (segue o caminho de antes).

    (a) pergunta clinica SEM sintoma -> S06/S08/S09/S10: texto que nao avalia, mostra o caminho e
        oferece uma pessoa, SEM caso (R15/R16/R18);
    (b) pergunta clinica COM sintoma relatado -> vira sintoma: tabela de alarme e ficha, sem escalar
        na hora;
    (c) necessidade nao clara -> UMA pergunta de esclarecimento (R23); depois, oferta de pessoa sem caso;
    (d) sintoma sem alarme -> ficha de ate' 3 rodadas (`_ficha_v2`) e, completa ou esgotada, o caminho
        (T05) sem caso.
    """
    intent = str(update.get("intent") or "")
    texto = str(state.get("message_body") or "")
    plano = _plano(texto)

    # A escolha de uma opcao do T11 ("4") ja' foi decidida em `classify` (`_triagem_v2_opcao`), antes
    # dos portoes de intencao: aqui `pedido_pessoa` e' sempre falso.
    sintoma = intent == "symptom" or bool(update.get("sintoma_codigo"))
    if intent == "clinical_question" and not sintoma and relata_sintoma(plano, update.get("intensidade")):
        sintoma = True
    ficha: dict[str, Any] = {"completa": False, "pergunta": None, "rodadas": 0}
    if sintoma:
        if intent != "symptom":
            # (b) "o que pode ser essa dor ao urinar?" e' relato de sintoma: tabela de alarme + ficha.
            logger.info("helena_triagem_v2_pergunta_com_sintoma", node="classify", intent_do_modelo=intent)
            update["intent"] = "symptom"
        if not ja_triado and await self._triar_red_flag(extraction, update):
            return update
        necessidade = necessidade_do_sintoma(update.get("population"))
        resultado = await self._ficha_v2(state, update)
        if "next_kind" in resultado:
            # A tabela de suficiencia nao respondeu (ou respondeu fora do vocabulario): falha tecnica,
            # como hoje — nunca "suficiente" por suposicao.
            update.update(resultado)
            return update
        ficha = resultado
    elif intent == "clinical_question":
        necessidade = necessidade_da_pergunta_clinica(plano, navegacao=_pergunta_de_navegacao(texto))
    elif intent in ("information", "greeting") and conversa is None and necessidade_vaga(plano):
        necessidade = NECESSIDADE_NAO_CLARA
    else:
        return None

    entrada: dict[str, Any] = {
        "necessidade": necessidade,
        # REDE DE SEGURANCA: com a ordem de hoje, chegar aqui ja' significa sem alarme e sem risco (os
        # portoes acima escalaram), e sem pedido de pessoa (o portao de pessoa e a opcao "4" do T11,
        # acima, escalaram).
        "sinal_alarme": False,
        "risco_psicossocial": bool(update.get("psychosocial_risk")),
        "pedido_pessoa": False,
        "ficha_completa": bool(ficha["completa"]),
        "rodadas": int(ficha["rodadas"]),
        "esclarecimento_pedido": self._esclarecimento_ja_pedido(state),
    }
    try:
        rows, version = await self._dmn.evaluate(TRIAGEM_V2_DMN_KEY, entrada)
        row = first_row(rows, TRIAGEM_V2_DMN_KEY, entrada)
        decisao, motivo, severidade, texto_id = ler_linha(row)
    except (DmnEvaluationError, DmnNoResultError) as exc:
        # O erro e' montado AQUI pelo helper sancionado (token de classe, nunca o texto cru da excecao;
        # cerca LUC-06/NEW-01) e so' a string segue para o fallback.
        return self._triagem_v2_falhou(update, necessidade, dmn_unavailable_error(TRIAGEM_V2_DMN_KEY, exc))
    except SaidaInvalidaError:
        return self._triagem_v2_falhou(update, necessidade, ERRO_TRIAGEM_V2_FORA_DO_VOCABULARIO)
    logger.info(
        "helena_triagem_v2_decisao",
        node="classify",
        necessidade=necessidade,
        decisao=decisao,
        texto=texto_id or "",
        ref=f"{TRIAGEM_V2_DMN_KEY}#{version.id}",
    )
    if decisao in DECISOES_QUE_ABREM_CASO:
        update["next_kind"] = "escalate"
        update["escalation_motivo"] = motivo
        update["escalation_severidade"] = severidade
        return update
    if decisao == DECISAO_PASSAR_AO_LUCAS:
        # Reservada (R08): hoje a cobranca e' decidida antes da tabela. Mesmo assim, a passagem so' sai
        # pela precondicao de duas camadas de sempre.
        update["intent"] = INTENT_COBRANCA
        self._preencher_cobranca(update, SUBTIPO_COBRANCA_PADRAO, None, state)
        return _rota_de_cobranca(update, state, roteador_ligado=self._roteador_lucas_enabled)
    if decisao == DECISAO_PERGUNTAR_MAIS:
        pergunta = ficha["pergunta"]
        if not (necessidade in NECESSIDADES_DE_SINTOMA and isinstance(pergunta, str) and pergunta):
            # A tabela mandou perguntar, mas a ficha nao tem pergunta: estado injustificado -> humano.
            return self._triagem_v2_falhou(update, necessidade, ERRO_TRIAGEM_V2_FORA_DO_VOCABULARIO)
        update.update(
            {
                "coleta_veredito": COLETA_VEREDITO_PERGUNTAR_MAIS,
                "coleta_pergunta": pergunta,
                "coleta_rodadas": int(ficha["rodadas"]) + 1,
                "coleta_pendente": pergunta,
                # O texto ja' chegou pseudonimizado na borda (o mesmo de `_avaliar_suficiencia`).
                "coleta_contexto": " | ".join(t for t in (state.get("coleta_contexto") or "", texto) if t),
                "next_kind": "collect",
            }
        )
        return update
    # ORIENTAR / RESPONDER / ESCLARECER: texto FIXO escolhido pela tabela, sem caso.
    if texto_id == "T12":
        update["response_text"] = self._texto_de_navegacao(state)
    elif texto_id == "T05":
        # O caminho da POPULACAO (aceite de 15/10/2026): crianca -> pediatra (bebe com menos de 3 meses ->
        # oferta da equipe de saude agora, DECISAO CLINICA PENDENTE); gestante -> obstetra; adulto ->
        # clinico geral.
        contexto_da_ficha = _plano(f"{state.get('coleta_contexto') or ''} {texto}")
        update["response_text"] = texto_de_orientacao(
            update.get("population"),
            bebe_com_menos_de_3_meses=_contexto_de_recem_nascido(state)
            or bebe_pequeno(contexto_da_ficha, update.get("idade_meses")),
        )
    else:
        update["response_text"] = TEXTOS_POR_ID[str(texto_id)]
    if update.get("intent") == "symptom":
        # A ficha terminou (completa ou esgotada): a pergunta deixa de estar em aberto.
        update["coleta_pendente"] = None
    else:
        # Pergunta clinica sem sintoma e necessidade nao clara saem como `information` (allowlist de
        # `inform`); sintoma continua `symptom` e a precondicao exige o veredito da tabela de alarme.
        update["intent"] = "information"
    if decisao == DECISAO_ESCLARECER:
        logger.info("helena_triagem_v2_esclarecimento", node="classify")
    return _rota_informativa(update)


def _triagem_v2_falhou(update: dict[str, Any], necessidade: str, erro: str) -> dict[str, Any]:
    """FALLBACK SEGURO quando a tabela nova nao responde (ou responde fora do vocabulario): o
    comportamento de falha de hoje, nunca silencio. Assunto de SAUDE -> `falha_tecnica` (humano), com a
    severidade derivada da intensidade; necessidade nao clara -> pedir para a pessoa mandar de novo.

    `erro` ja' chega como TOKEN montado no `except` de quem chamou (nunca a excecao)."""
    logger.warning("helena_triagem_v2_tabela_indisponivel", node="classify", necessidade=necessidade)
    if necessidade == NECESSIDADE_NAO_CLARA and not update.get("sintoma_codigo"):
        update["intent"] = "information"
        update["response_text"] = RESPOSTA_PECA_REENVIO
        update["next_kind"] = "inform"
        return update
    update["next_kind"] = "escalate"
    update["escalation_motivo"] = "falha_tecnica"
    update["escalation_severidade"] = _severidade_de_intensidade(update.get("intensidade"))
    update["error"] = erro
    return update


async def _ficha_v2(self: HelenaGraph, state: HelenaState, update: dict[str, Any]) -> dict[str, Any]:
    """A FICHA MINIMA da nova triagem: `{"completa", "pergunta", "rodadas"}` — ou, se a tabela de
    suficiencia falhar, o `update` de falha tecnica (com `next_kind`).

    ORDEM (aba 4 do catalogo; CONTEUDO DRAFT, o medico decide):
      1. primeira rodada: a pergunta POR TIPO DE QUEIXA (texto fixo da aba 4) quando o tipo e' conhecido;
      2. depois, `triage_sufficiency` diz o que ainda falta (intensidade, idade, semanas). O LIMITE de
         rodadas e' da tabela nova (3); a de suficiencia e' consultada com `rodadas` abaixo do limite
         DELA (2), para responder so' "o que falta?";
      3. sintoma sem codigo da allowlist ja' descrito (uma rodada feita): a caracterizacao nao tem como
         casar um codigo, entao conta como respondida — perguntar de novo seria interrogatorio;
      4. 3 rodadas feitas: ficha esgotada (a tabela nova decide o que fazer).

    NAO PERGUNTA O QUE JA' FOI DITO (aceite em dev, 15/10/2026: "Estou com dor nas costas" ouviu "Em
    que parte do corpo e' a dor?"). `itens_ja_respondidos` junta a extracao (com a memoria clinica
    fundida), o recem-nascido dito em palavras (DL-0101) e o lexico sobre o texto acumulado da ficha: a
    pergunta da aba 4 leva SO' as frases que faltam, falando do paciente como a pessoa disse ("sua
    filha"); com a ficha da queixa toda respondida ja' na primeira mensagem, conta como caracterizada e
    o veredito segue sem perguntar. E NAO CONCLUI SEM RESPOSTA: item perguntado e nao respondido volta
    na rodada seguinte ("So' para eu entender: ..."), dentro do limite de 3 rodadas. Na
    `triage_sufficiency`, intensidade/idade/semanas ditas no texto contam como informadas — so' para ela
    nao mandar perguntar de novo; as tabelas de alarme ja' rodaram com a extracao e nao mudam.
    """
    rodadas = _rodadas_de_coleta(state.get("coleta_rodadas", 0), teto=COLETA_MAX_RODADAS_TRIAGEM_V2)
    if rodadas >= COLETA_MAX_RODADAS_TRIAGEM_V2:
        logger.info("helena_triagem_v2_ficha_esgotada", rodadas=rodadas, node="classify")
        return {"completa": False, "pergunta": None, "rodadas": rodadas}
    atual = _plano(str(state.get("message_body") or ""))
    contexto = _plano(f"{state.get('coleta_contexto') or ''} {state.get('message_body') or ''}")
    grupo = grupo_da_queixa(update.get("population"), update.get("sintoma_codigo"), contexto)
    respondidos = itens_ja_respondidos(contexto, update)
    if _contexto_de_recem_nascido(state):
        # DL-0101: recem-nascido dito em palavras e' idade informada (`idade_meses = 0`).
        respondidos = respondidos | {ITEM_IDADE}
    paciente = paciente_da_conversa(contexto)
    if grupo is not None:
        anterior = ler_codigo_da_ficha(state.get("coleta_pendente"))
        faltam: tuple[int, ...] = ()
        if rodadas == 0:
            faltam = itens_que_faltam(grupo, contexto, respondidos)
        elif anterior is not None and anterior.grupo == grupo and not resposta_curta(atual):
            # PERGUNTADO E NAO RESPONDIDO volta na rodada seguinte, so' ele (aceite de 15/10/2026: a
            # pessoa respondeu outra coisa e a ficha concluiu "suficiente"). Uma resposta curta ("nao",
            # "tudo normal") cobre o que foi perguntado.
            faltam = itens_que_faltam(grupo, contexto, respondidos, anterior.indices)
        if faltam:
            codigo = codigo_da_ficha(grupo, faltam, paciente=paciente, de_novo=rodadas > 0)
            return {"completa": False, "pergunta": codigo, "rodadas": rodadas}
        if rodadas == 0:
            logger.info("helena_triagem_v2_ficha_ja_respondida", grupo=grupo, node="classify")
    # Caracterizado: uma rodada ja' feita, ou a ficha da queixa toda respondida na propria mensagem.
    caracterizado = rodadas >= 1 or grupo is not None
    entrada = _entrada_de_suficiencia(update, min(rodadas, COLETA_MAX_RODADAS - 1))
    if ITEM_INTENSIDADE in respondidos:
        entrada["intensidade_informada"] = True
    population = entrada["population"]
    if (population in ("adult", "pediatric") and ITEM_IDADE in respondidos) or (
        population == "gestante" and ITEM_SEMANAS in respondidos
    ):
        entrada["campo_populacao_disponivel"] = True
    try:
        rows, version = await self._dmn.evaluate(SUFFICIENCY_DMN_KEY, entrada)
        row = first_row(rows, SUFFICIENCY_DMN_KEY, entrada)
    except (DmnEvaluationError, DmnNoResultError) as exc:
        return {
            "coleta_veredito": None,
            "next_kind": "escalate",
            "escalation_motivo": "falha_tecnica",
            "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
            "error": dmn_unavailable_error(SUFFICIENCY_DMN_KEY, exc),
        }
    bruto = row.get("veredito")
    veredito = str((bruto.get("value") if isinstance(bruto, dict) else bruto) or "")
    logger.info(
        "helena_triagem_v2_suficiencia",
        veredito=veredito,
        rodadas=rodadas,
        ref=f"{SUFFICIENCY_DMN_KEY}#{version.id}",
        node="classify",
    )
    if veredito == COLETA_VEREDITO_SUFICIENTE or (veredito == "PERGUNTAR_CARACTERIZACAO" and caracterizado):
        return {"completa": True, "pergunta": None, "rodadas": rodadas}
    if veredito in COLETA_VEREDITOS_PERGUNTA:
        return {"completa": False, "pergunta": veredito, "rodadas": rodadas}
    logger.warning(
        "helena_coleta_veredito_desconhecido", classification="fora_do_vocabulario", node="classify"
    )
    return {
        "coleta_veredito": None,
        "next_kind": "escalate",
        "escalation_motivo": "falha_tecnica",
        "escalation_severidade": _severidade_de_intensidade(update.get("intensidade")),
        "error": f"{SUFFICIENCY_DMN_KEY}: veredito fora do vocabulario",
    }


def _v4_pede_pessoa(state: Mapping[str, Any], texto: str) -> bool:
    """A mensagem pede uma pessoa (ou ameaca ANS/Procon, ou e' cobranca)? Entao a v4 NAO responde: o
    gatilho de sempre (pedido de pessoa, RN 623/2024 art. 9, III; ANS/Procon; Lucas) decide."""
    return bool(
        state.get("pedido_humano_lexico") is True
        or _pedido_explicito_de_pessoa(texto)
        or _pedido_explicito_com_verbo(texto)
        or _ameaca_regulatoria(texto)
        or _pergunta_de_cobranca(texto)
    )


def _v4_pede_pessoa_no_alerta(
    self: HelenaGraph, state: Mapping[str, Any], pend: Pendente, texto: str
) -> bool:
    """A pessoa pediu EXPLICITAMENTE uma pessoa quando a v4 esperava a resposta de alerta, e a mensagem em
    si nao traz queixa, emergencia, cobranca nem recusa? So' entao a v4 abre o caso sem rodar a tabela."""
    if not self._v4 or pend.etapa != ETAPA_ALERTA:
        return False
    plano = _plano(texto)
    return bool(
        _pedido_explicito_de_pessoa(texto)
        and not _RECUSA_DE_PESSOA.search(plano)
        and not _conteudo_clinico(texto)
        and state.get("sinal_saude_lexico") is not True
        and not _sinal_forte_de_emergencia(texto)
        and not _sinal_fraco_de_emergencia(texto)
        and not _pergunta_de_cobranca(texto)
        and not _ameaca_regulatoria(texto)
    )


def _v4_escolha_pura(self: HelenaGraph, state: Mapping[str, Any], pend: Pendente, texto: str) -> bool:
    """A mensagem e' SO' a escolha de uma opcao da oferta da v4 ("2", "opcao 2", "falar com a equipe") e
    nao traz queixa, emergencia nem sinal de saude? Entao a escolha vale sem passar pela tabela."""
    if not self._v4 or pend.etapa != ETAPA_ESCOLHA:
        return False
    plano = _plano(texto)
    return bool(
        escolha_da_resposta(plano) is not None
        and not _conteudo_clinico(texto)
        and state.get("sinal_saude_lexico") is not True
        and not _sinal_forte_de_emergencia(texto)
        and not _sinal_fraco_de_emergencia(texto)
    )


def _v4_eco(relato: str) -> str:
    """O eco do que a pessoa disse ("dor nas costas desde ontem"), passado pelas cercas de saida: se uma
    delas barrar o trecho, volta o generico."""
    eco = eco_do_relato(relato)
    if eco != ECO_GENERICO and (motivo_de_canal_nao_confirmado(eco) or motivo_de_recusa(eco, "inform")):
        logger.info("helena_v4_eco_barrado", node="classify")
        return ECO_GENERICO
    return eco


async def _v4_antes_dos_gatilhos(
    self: HelenaGraph,
    state: HelenaState,
    update: dict[str, Any],
    extraction: dict[str, Any],
    pend: Pendente | None,
) -> dict[str, Any] | None:
    """O que a v4 decide ANTES dos gatilhos de intencao: a resposta da pessoa ao que a v4 perguntou/
    ofereceu no turno anterior, e o pedido de especialista. `None` = o turno segue o caminho de sempre."""
    texto = str(state.get("message_body") or "")
    if pend is not None and pend.etapa == ETAPA_ALERTA and self._gerativa_para(state):
        # SAUDE GERATIVA (ADR-0065), so' na allowlist: o "nao" e o CONTEXTO sem alarme recebem a
        # orientacao
        # redigida; o resto ("sim", duvida, alarme, pedido de pessoa) segue a v4 de sempre, logo abaixo.
        decidido_g = await self._gerativa_no_alerta(state, update, pend, texto)
        if decidido_g is not None:
            return decidido_g
    if pend is not None:
        decidido = self._v4_seguimento(state, update, pend, texto)
        if decidido is not None:
            return decidido
    return await self._v4_especialista(state, update, extraction, texto)


def _v4_abrir_caso(
    self: HelenaGraph,
    update: dict[str, Any],
    pend: Pendente,
    relato: str,
    etapa: str,
    *,
    motivo: str,
    severidade: str,
) -> dict[str, Any]:
    """Abre o caso pelo no' `escalate` de sempre. O codigo da etapa fica em `coleta_pendente` e o
    relato em `coleta_contexto` SO' neste turno: `_resumo_contexto` os le' para o resumo do atendente."""
    update["intent"] = "symptom" if etapa == ETAPA_CASO_SIM else "human_request"
    update["next_kind"] = "escalate"
    update["escalation_motivo"] = motivo
    update["escalation_severidade"] = severidade
    update["coleta_pendente"] = codigo_pendente(etapa, pend.grupo, pend.populacao)
    update["coleta_contexto"] = relato
    logger.info("helena_v4_caso", node="classify", etapa=etapa, motivo=motivo)
    return update


def _v4_seguimento(
    self: HelenaGraph, state: HelenaState, update: dict[str, Any], pend: Pendente, texto: str
) -> dict[str, Any] | None:
    """A resposta da pessoa ao que a v4 perguntou (ALERTA) ou ofereceu (ESCOLHA, OFERTA) no turno anterior.

    ESCOLHA: "1" (ou "onde marcar") -> como marcar pelo plano, sem diagnostico; "2" (ou "a equipe") -> abre o
    caso com o resumo pronto; "nao" -> fecha sem caso; outra fala sobre o mesmo sintoma ("dormi de mau jeito,
    sera' que e' isso?") -> quem avalia a causa e' o medico, com as mesmas opcoes (DL-0154: a Helena nunca
    explica causa nem orienta o sintoma).
    ALERTA: "nao" -> o caminho (consulta pelo plano, prazo da ANS) ou a equipe, e fecha; "sim" -> alerta
    clinico, P1; SEM CLAREZA (DL-0154) -> pergunta UMA vez de novo, com outras palavras; continuou sem
    clareza -> OFERTA da equipe de saude, sem 192 e sem caso.
    OFERTA: "sim" -> caso com a equipe de saude; "nao" -> fecha sem caso.
    Pedido de pessoa, outro assunto e tudo o que nao e' resposta -> `None` (o caminho de sempre)."""
    plano = _plano(texto)
    relato = str(state.get("coleta_contexto") or "")
    if pend.etapa == ETAPA_OFERTA:
        return self._v4_resposta_a_oferta(state, update, pend, texto)
    if pend.etapa == ETAPA_ESCOLHA:
        escolha = _escolha_marcar_ou_equipe(plano)
        # DL-0154: depois do caminho (ou do "nao, obrigado") o sintoma continua FECHADO mas lembrado por um
        # turno: a fala seguinte sobre ele ("eu dormi de mau jeito") recebe "quem avalia e' o medico", nunca a
        # pergunta de alerta de novo. A pendencia vale so' para o turno seguinte (expira como sempre).
        fechado = {
            "coleta_pendente": codigo_pendente(ETAPA_ESCOLHA, pend.grupo, pend.populacao),
            "coleta_contexto": relato,
        }
        if escolha == "1":
            logger.info("helena_v4_escolha", node="classify", opcao="1")
            update["intent"] = "information"
            update["sintoma_codigo"] = None
            update["response_text"] = texto_da_opcao_1(pend.populacao)
            return _rota_informativa(update) | fechado
        if escolha == "2":
            logger.info("helena_v4_escolha", node="classify", opcao="2")
            return self._v4_abrir_caso(
                update, pend, relato, ETAPA_CASO_EQUIPE, motivo="intencao_clinica", severidade="moderada"
            )
        if escolha == "nao":
            logger.info("helena_v4_escolha", node="classify", opcao="nao")
            return self._base_texto_fixo(update, self._textos[CHAVE_OFERTA_RECUSADA]) | fechado
        if self._v4_seguimento_do_sintoma(state, texto):
            # DL-0154: depois de FECHAR, a conversa sobre o mesmo sintoma nunca reinicia a pergunta de alerta
            # nem vira explicacao de causa: quem avalia e' o medico; as mesmas opcoes continuam valendo.
            logger.info("helena_v4_seguimento_depois_de_fechar", node="classify")
            return self._base_texto_fixo(
                update,
                self._textos[CHAVE_AVALIAR_CAUSA],
                pendente=codigo_pendente(ETAPA_ESCOLHA, pend.grupo, pend.populacao),
            ) | {"coleta_contexto": relato}
        return None
    if pend.etapa != ETAPA_ALERTA or self._v4_pede_pessoa(state, texto):
        return None
    resposta = _leitura_do_alerta(plano)
    if resposta == LEITURA_NAO:
        logger.info("helena_v4_alerta", node="classify", resposta="nao")
        update["intent"] = "information"
        update["sintoma_codigo"] = None
        # DL-0154: o caminho ou a equipe, e FECHA — sem o texto de orientacao do arquivo `sin-*` (a Helena
        # nunca orienta o sintoma); o profissional pela idade (adulto: clinico geral; crianca: pediatra).
        update["response_text"] = texto_final_do_sintoma(self._v4_eco(relato), pend.populacao)
        update["coleta_pendente"] = codigo_pendente(ETAPA_ESCOLHA, pend.grupo, pend.populacao)
        update["coleta_contexto"] = relato
        return _rota_informativa(update)
    if resposta == LEITURA_SIM:
        logger.warning("helena_v4_alerta", node="classify", resposta="sim")
        return self._v4_abrir_caso(
            update, pend, relato, ETAPA_CASO_SIM, motivo="red_flag_clinico", severidade="grave"
        )
    # Nao deu para entender. So' insiste se a mensagem parece uma resposta (curta ou ainda falando do
    # sintoma); outro assunto segue o caminho de sempre.
    parece_resposta = (
        relata_sintoma(plano) or len(plano.split()) <= 4 or self._v4_seguimento_do_sintoma(state, texto)
    )
    if not parece_resposta:
        return None
    if pend.repetida:
        # DL-0154 (regra da ambiguidade; aval do medico responsavel recomendado antes de producao): duas
        # respostas sem clareza -> a OFERTA da equipe de saude, SEM 192 e SEM caso (o caso so' com "sim").
        logger.info("helena_v4_alerta", node="classify", resposta="oferta_da_equipe")
        return self._base_texto_fixo(
            update,
            self._textos[CHAVE_OFERTA_EQUIPE],
            pendente=codigo_pendente(ETAPA_OFERTA, pend.grupo, pend.populacao),
        ) | {"coleta_contexto": relato}
    codigo = codigo_pendente(ETAPA_ALERTA, pend.grupo, pend.populacao, repetida=True)
    logger.info("helena_v4_alerta", node="classify", resposta="repete")
    update.update(
        {
            "coleta_veredito": COLETA_VEREDITO_PERGUNTAR_MAIS,
            "coleta_pergunta": codigo,
            "coleta_pendente": codigo,
            "coleta_contexto": relato,
            "coleta_rodadas": 2,
            "next_kind": "collect",
        }
    )
    return update


def _v4_sintoma_leve(self: HelenaGraph, state: HelenaState, update: dict[str, Any]) -> dict[str, Any] | None:
    """O sintoma que passou pela tabela de alarme sem bandeira: UMA mensagem com 1 ou 2 perguntas de
    ALERTA do tipo de queixa (`helena_v4.ALERTAS`). `None` quando a v4 nao trata (gestante, saude
    mental, recem-nascido ou bebe com menos de 3 meses): o caminho de hoje."""
    texto = str(state.get("message_body") or "")
    plano = _plano(texto)
    populacao = populacao_da_v4(update.get("population"), plano)
    if populacao is None or _contexto_de_recem_nascido(state):
        return None
    if bebe_pequeno(plano, update.get("idade_meses")):
        return None
    grupo = grupo_do_sintoma(populacao, plano)
    if self._base_saude:
        # BASE DE SAUDE (ADR-0064): um arquivo `sin-*` da base para ESTA queixa (e esta populacao) traz as
        # perguntas de alerta proprias; sem arquivo, as genericas do grupo (v4).
        assunto_sin = self._base_sintoma_do_texto(texto, populacao)
        if assunto_sin is not None:
            grupo = assunto_sin.id
        else:
            # Avaliacao de 21/10/2026: o CONTEXTO do sintoma persiste ate' ser resolvido — sem citar outro
            # assunto, o turno seguinte continua no assunto em curso (nunca as genericas da populacao).
            anterior = ler_pendente(state.get("coleta_pendente"))
            if anterior is not None and anterior.grupo.startswith("sin-"):
                grupo = anterior.grupo
    codigo = codigo_pendente(ETAPA_ALERTA, grupo, populacao)
    logger.info("helena_v4_sintoma_leve", node="classify", grupo=grupo, populacao=populacao)
    update.update(
        {
            "coleta_veredito": COLETA_VEREDITO_PERGUNTAR_MAIS,
            "coleta_pergunta": codigo,
            "coleta_pendente": codigo,
            # So' o relato DESTA queixa: o que a pessoa falou antes, de outro assunto, nao entra.
            "coleta_contexto": relato_cru(texto),
            "coleta_rodadas": 1,
            "next_kind": "collect",
        }
    )
    return update


async def _v4_especialista(
    self: HelenaGraph,
    state: HelenaState,
    update: dict[str, Any],
    extraction: dict[str, Any],
    texto: str,
) -> dict[str, Any] | None:
    """Pedido de especialista ("Preciso consultar com um cardiologista", "Onde tem dermatologista pelo
    plano?"): o texto fixo do catalogo v4. NAO pesquisa especialidade nem agenda; NAO e' sintoma, pedido
    de pessoa nem pergunta de cobertura de procedimento ("cobre ressonancia?" segue o caminho de hoje).

    A INTENCAO do modelo nao decide: "preciso de um cardiologista" pode sair `symptom`, `scheduling` ou
    `clinical_question`. Decide o TEXTO — sem codigo de sintoma e sem palavra de sintoma, e' consulta."""
    plano = _plano(texto)
    achado = especialidade_pedida(plano)
    if achado is None:
        return None
    if (
        extraction.get("sintoma_codigo")
        or relata_sintoma(plano, extraction.get("intensidade"))
        or self._v4_pede_pessoa(state, texto)
    ):
        return None
    nome, basica = achado
    # DL-0154 (etapa 8): sem o "paragrafo de segmentacao" do cadastro — quem pede especialista quer saber
    # onde marcar e o prazo, nao o tipo do plano (a cobertura saiu da Helena).
    logger.info("helena_v4_especialista", node="classify", basica=basica)
    update["intent"] = "information"
    update["sintoma_codigo"] = None
    update.pop("consulta_subtipo", None)
    update["response_text"] = texto_do_especialista(
        nome,
        basica=basica,
        segmentacao=None,
        frase_cobertura=(
            self._textos[CHAVE_TEXTO_COBERTURA_FORA] if _pergunta_de_cobertura_ampla(texto) else None
        ),
    )
    return _rota_informativa(update)


def _base_assunto_do_grupo(self: HelenaGraph, grupo: str) -> _bs.Assunto | None:
    """O arquivo `sin-*` de um grupo que e' id da base (`None` para os grupos da v4 ou id ausente)."""
    if not self._base_saude or not grupo.startswith("sin-"):
        return None
    assunto = self._base_hoje().get(grupo)
    return assunto if assunto is not None and assunto.categoria == _bs.CATEGORIA_SINTOMA else None


def _base_alerta_do_grupo(self: HelenaGraph, grupo: str) -> AlertaDoGrupo | None:
    """As perguntas de alerta do arquivo `sin-*` (ou, se o id sumiu da base, as GERAIS da v4); `None`
    para os grupos da propria v4 (o texto de sempre)."""
    if not grupo.startswith("sin-"):
        return None
    assunto = self._base_assunto_do_grupo(grupo)
    if assunto is None:
        return ALERTAS[GRUPO_GERAL]
    return AlertaDoGrupo(tuple(assunto.perguntas_alerta or ()), ", ".join(assunto.sinais_alarme or ()))


def _base_sintoma_do_texto(self: HelenaGraph, texto: str, populacao: str) -> _bs.Assunto | None:
    """O arquivo `sin-*` da queixa, so' quando a busca e' DETERMINISTICA (sem modelo) e a populacao do
    arquivo cobre a da conversa."""
    busca = _bs.buscar(self._base_hoje(), texto, categorias=(_bs.CATEGORIA_SINTOMA,))
    if busca.achado is None:
        return None
    assunto = self._base_hoje().get(busca.achado)
    if assunto is None:
        return None
    compativel = {POPULACAO_ADULTO: ("adulto", "todos"), POPULACAO_CRIANCA: ("crianca", "todos")}
    return assunto if assunto.populacao in compativel.get(populacao, ()) else None


def _base_dor_sem_alarme(
    self: HelenaGraph, state: Mapping[str, Any], update: Mapping[str, Any], pend: Pendente | None
) -> bool:
    """A tabela escalou SO' pelo fail-safe de intensidade (r10 da tabela de ADULTO) uma DOR sem sinal de
    alarme? So' entao (chaves da base e da v4 ligadas) o turno segue para as perguntas de alerta.

    Conferido, TUDO: a tabela e' a de adulto; a saida e' EXATAMENTE a da r10 (`MOTIVO_FAILSAFE_ADULTO`,
    P2, ESCALATE_NURSE — o teste amarra o literal ao arquivo da DMN); a populacao e' adulto (nao gestante,
    nao saude mental, nao crianca); o relato (esta mensagem, ou a queixa que esta' sendo perguntada) fala
    de DOR; e nao ha' sinal de emergencia do lexico nem termo de alarme dos arquivos `sin-*`."""
    if not (self._base_saude and self._v4):
        return False
    if update.get("dmn_table") != _DMN_BY_POPULATION["adult"]:
        return False
    decisao = update.get("dmn_decision") or {}
    if not (
        str(decisao.get("motivo", "")) == MOTIVO_FAILSAFE_ADULTO
        and str(decisao.get("prioridade", "")) == "P2"
        and str(decisao.get("conduta", "")) == "ESCALATE_NURSE"
    ):
        return False
    texto = str(state.get("message_body") or "")
    relato = texto
    if pend is not None and pend.etapa == ETAPA_ALERTA:
        relato = f"{state.get('coleta_contexto') or ''} {texto}"
    plano = _plano(relato)
    if populacao_da_v4(update.get("population"), plano) != POPULACAO_ADULTO:
        return False
    if not _DOR_NO_RELATO.search(plano):
        return False
    if (
        _sinal_forte_de_emergencia(relato)
        or _sinal_fraco_de_emergencia(relato)
        or _bs.alarme_no_texto(self._base_hoje(), relato)
    ):
        return False
    logger.warning("helena_base_saude_failsafe_de_dor_sem_alarme", node="classify")
    return True


def _leitura_do_alerta(plano: str) -> str | None:
    """A resposta a pergunta de alerta: com o roteador v7 no turno, a leitura do MODELO no contexto ("sim",
    "nao" ou sem clareza); sem ele, o leitor deterministico. O agravamento ("nao consigo respirar",
    "piorou") e' "sim" nos dois (lado seguro do sinal); a tabela de alarme ja' rodou antes."""
    v7 = _rv7.LEITURA_DO_TURNO.get()
    if v7 is None:
        return resposta_ao_alerta(plano)
    if e_agravamento(plano):
        return LEITURA_SIM
    return {"sim": LEITURA_SIM, "nao": LEITURA_NAO}.get(v7.sim_nao)


def _escolha_marcar_ou_equipe(plano: str) -> str | None:
    v7 = _rv7.LEITURA_DO_TURNO.get()
    if v7 is not None:
        # Contrato v7: a opcao (ou a recusa) lida pelo modelo no contexto, no lugar das palavras.
        if v7.opcao in ("1", "2"):
            return v7.opcao
        if v7.recusa_equipe or v7.sim_nao == "nao":
            return "nao"
        if v7.skill == "atendimento_humano" and not v7.recusa_equipe:
            return "2"
        return None
    """A escolha entre "onde marcar" e "a equipe" dita com outras palavras ("me mostra onde marcar", "prefiro
    a equipe", "nao precisa"), alem do numero/rotulo de `escolha_da_resposta`. Le' a RESPOSTA a pergunta da
    propria Helena (validacao da pendencia), nunca decide o assunto. Na etapa 7 o roteador v7 le' a escolha
    no contexto."""
    escolha = escolha_da_resposta(plano)
    if escolha is not None:
        return escolha
    palavras = set(plano.split())
    if len(palavras) <= 10:
        if palavras & {"equipe", "atendente", "pessoa", "enfermeira", "enfermagem"}:
            return "2"
        if palavras & {"marcar", "agendar", "mostra", "mostre", "consulta", "onde"}:
            return "1"
    if _lc.ler_resposta(plano) in ("nao", "encerrar") or resposta_ao_alerta(plano) == LEITURA_NAO:
        return "nao"
    return None


def _v4_seguimento_do_sintoma(self: HelenaGraph, state: Mapping[str, Any], texto: str) -> bool:
    """A mensagem CONTINUA a conversa sobre o sintoma em curso ("eu dormi de mau jeito", "sera' que e' isso?",
    "e o que pode ser?") — e nao traz sinal de alarme, pedido de pessoa, cobranca nem pergunta do plano?

    Seguranca: qualquer sinal de alarme (lexico de emergencia, termo de alarme da base, alarme deterministico)
    devolve `False` e a mensagem segue pela tabela, como sempre."""
    plano = _plano(texto)
    if not plano:
        return False
    v7 = _rv7.LEITURA_DO_TURNO.get()
    if v7 is not None and not (v7.skill == "saude" and v7.continuacao and not v7.sinais_atuais()):
        # Contrato v7: so' o MESMO assunto de saude, sem sinal novo, e' seguimento do sintoma.
        return False
    if (
        _sinal_forte_de_emergencia(texto)
        or _sinal_fraco_de_emergencia(texto)
        or alarme_no_relato(plano) is not None
        or (self._base_saude and _bs.alarme_no_texto(self._base_hoje(), texto))
    ):
        return False
    if (
        self._v4_pede_pessoa(state, texto)
        or _pergunta_do_plano(texto)
        or _pergunta_de_cobertura_ampla(texto)
        or _pergunta_de_cobranca(texto)
        or especialidade_pedida(plano) is not None
        or pede_carteirinha(plano)
        or _lc.e_cortesia(texto)
        or _e_despedida(texto)
    ):
        return False
    if self._base_saude:
        # Uma pergunta sobre OUTRO assunto de saude da base ("tenho SOP, e' a mesma coisa que cisto?") nao e'
        # seguimento do sintoma: e' respondida uma vez, como qualquer duvida geral (OPCAO C).
        achado = _sg.assunto_confiavel(self._base_hoje(), texto)
        em_curso = ler_pendente(state.get("coleta_pendente"))
        if achado is not None and (em_curso is None or achado.id != em_curso.grupo):
            return False
        if _sg.e_duvida_educativa(plano) or _sg.pergunta_pessoal_de_saude(
            plano, texto, assunto_de_saude=True
        ):
            return False
    return relata_sintoma(plano) or "?" in texto or len(plano.split()) <= 8


def _v4_resposta_a_oferta(
    self: HelenaGraph, state: HelenaState, update: dict[str, Any], pend: Pendente, texto: str
) -> dict[str, Any] | None:
    """A resposta a OFERTA da equipe de saude feita depois de duas respostas sem clareza (DL-0154): "sim" abre
    o caso com a equipe de saude (`intencao_clinica`, moderada — sem 192); "nao" fecha sem caso; o resto segue
    o caminho de sempre."""
    plano = _plano(texto)
    relato = str(state.get("coleta_contexto") or "")
    v7 = _rv7.LEITURA_DO_TURNO.get()
    leitura = _lc.ler_resposta(texto)
    if v7 is not None:
        # Contrato v7: o "sim" ("pode ser", "pode passar pra equipe sim") e a recusa lidos no contexto.
        leitura = "nao" if v7.recusa_equipe else {"sim": "sim", "nao": "nao"}.get(v7.sim_nao)
    if leitura == "sim" or (leitura is None and _escolha_marcar_ou_equipe(plano) == "2"):
        logger.info("helena_v4_oferta", node="classify", resposta="sim")
        return self._v4_abrir_caso(
            update, pend, relato, ETAPA_CASO_EQUIPE, motivo="intencao_clinica", severidade="moderada"
        )
    if leitura in ("nao", "encerrar"):
        logger.info("helena_v4_oferta", node="classify", resposta="nao")
        return self._base_texto_fixo(update, self._textos[CHAVE_OFERTA_RECUSADA])
    return None


#: As intencoes do classificador que dizem OUTRO assunto no meio do sintoma em curso.
_INTENCOES_DE_OUTRO_ASSUNTO: frozenset[str] = frozenset(
    {INTENT_CONSULTA_PLANO, INTENT_COBRANCA, "human_request", "scheduling", "outside_channel"}
)


def _v4_conduzir_pendencia(
    self: HelenaGraph, state: HelenaState, update: dict[str, Any], pend: Pendente | None, texto: str
) -> dict[str, Any] | None:
    """DL-0154 (o modelo conduz; a tabela so' protege): a RESPOSTA a pergunta/oferta pendente do sintoma e'
    decidida ANTES da tabela de red flag do turno — a tabela ja' rodou para a queixa quando ela foi contada, e
    re-triar a resposta com o codigo do historico era o que reabria P1/192 por engano ("acho que e' so' mau
    jeito"). Qualquer sinal de alarme NA PROPRIA mensagem devolve `None`: a tabela decide, como sempre."""
    if not self._v4 or pend is None or pend.etapa not in (ETAPA_ALERTA, ETAPA_ESCOLHA, ETAPA_OFERTA):
        return None
    plano = _plano(texto)
    if (
        _sinal_forte_de_emergencia(texto)
        or _sinal_fraco_de_emergencia(texto)
        or alarme_no_relato(plano) is not None
        or (self._base_saude and _bs.alarme_no_texto(self._base_hoje(), texto))
        or (
            self._base_saude
            and pend.grupo.startswith("sin-")
            and _bs.alarme_do_assunto(self._base_hoje(), pend.grupo, texto)
        )
    ):
        return None
    v7 = _rv7.LEITURA_DO_TURNO.get()
    if v7 is not None and v7.sim_nao == "nao_se_aplica" and v7.opcao == "nenhuma" and not v7.continuacao:
        # O modelo leu OUTRO assunto: a pendencia do sintoma nao sequestra a mensagem.
        return None
    leitura = _leitura_do_alerta(plano) if pend.etapa == ETAPA_ALERTA else None
    if leitura == LEITURA_SIM:
        # O "sim" a pergunta de alerta segue a v4 de sempre (abre o caso urgente com 192).
        return None
    if leitura is None and update.get("intent") in _INTENCOES_DE_OUTRO_ASSUNTO:
        # O classificador leu OUTRO assunto (plano, cobranca, pedido de pessoa, agendamento): o caminho de
        # sempre decide (a pendencia do sintoma nao sequestra a mensagem).
        return None
    return self._v4_seguimento(state, update, pend, texto)


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
from maezo.agents.helena.graph import (  # noqa: E402
    _DMN_BY_POPULATION,
    _DOR_NO_RELATO,
    _FALAS_DA_OFERTA_RECENTE,
    _RECUSA_DE_PESSOA,
    COLETA_MAX_RODADAS,
    COLETA_VEREDITO_ESCALAR,
    COLETA_VEREDITO_SUFICIENTE,
    COLETA_VEREDITOS_PERGUNTA,
    ERRO_TRIAGEM_V2_FORA_DO_VOCABULARIO,
    MOTIVO_COLETA_ESGOTADA,
    MOTIVO_FAILSAFE_ADULTO,
    RESPOSTA_DUVIDA_DE_SAUDE,
    RESPOSTA_FORA_DO_CANAL,
    RESPOSTA_PECA_REENVIO,
    SUBTIPO_COBRANCA_PADRAO,
    SUFFICIENCY_DMN_KEY,
    HelenaState,
    _ameaca_regulatoria,
    _conteudo_clinico,
    _contexto_de_recem_nascido,
    _e_despedida,
    _entrada_de_suficiencia,
    _falas_da_helena,
    _pedido_explicito_com_verbo,
    _pedido_explicito_de_pessoa,
    _pergunta_de_cobertura_ampla,
    _pergunta_de_cobranca,
    _pergunta_de_navegacao,
    _pergunta_do_plano,
    _plano,
    _rodadas_de_coleta,
    _rota_de_cobranca,
    _rota_informativa,
    _sem_prefixo,
    _severidade_de_intensidade,
    _sinal_forte_de_emergencia,
    _sinal_fraco_de_emergencia,
    _subtipo_pelo_texto,
    logger,
)
