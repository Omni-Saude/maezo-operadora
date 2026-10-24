"""Habilidade `plano` da Helena (ADR-0067 §5): os fatos do plano do proprio beneficiario (elegibilidade,
carteirinha, carencia, autorizacao) pelas rotas da operadora, os dependentes e a redacao da resposta so' com
os fatos (DL-0081, DL-0127).

MOVIDO SEM MUDANCA de `graph.py` (Plano de ajustes da Helena v2, etapa 8, PR so' de mudanca de lugar): cada
metodo do `HelenaGraph` virou uma funcao deste modulo com o MESMO corpo (`self` e' o grafo) e a classe aponta
para ela. O comportamento muda so' no PR seguinte.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo LOGO ANTES da classe (os helpers de modulo ja'
existem) e este modulo importa os nomes do graph NO FIM.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

from maezo.agents.helena.consultas_plano import (
    CONSULTA_PLANO_PROMPT_VERSION,
    CONSULTA_SUBTIPOS,
    INTENT_CONSULTA_PLANO,
    RESPOSTA_CONSULTA_SEM_IDENTIDADE,
    RESPOSTA_DEPENDENTES_FALHA,
    SUBTIPOS_REDIGIDOS,
    CarteirinhasDoTurno,
    FonteIndisponivel,
    NomesDoTurno,
    consulta_prompt,
    fatos_da_consulta,
    grau_legivel,
    ler_carteirinhas_completas,
    ler_dependentes_nominais,
    literais_dos_fatos,
    motivo_de_omissao_da_elegibilidade,
    motivo_de_recusa_da_consulta,
    resposta_deterministica,
    sem_marcador_de_carteirinha,
    sem_marcador_de_dependente,
    so_deterministica,
    texto_de_dependentes_com_nomes,
)
from maezo.agents.helena.habilidades.cobertura_fora import CHAVE_TEXTO as CHAVE_TEXTO_COBERTURA_FORA
from maezo.agents.helena.helena_v4 import fatos_da_carteirinha, texto_da_carteirinha
from maezo.contratos.identidade_beneficiario import IDENTIDADE_RECONHECIDA
from maezo.domain.conversa.competencia import hoje_em_brasilia
from maezo.domain.conversa.terceiro_no_texto import com_aviso_se_for_de_outra_pessoa
from maezo.ports.tina import ElegibilidadeView
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.prompt_format import render_untrusted_block

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph


def _consulta_recusada(self: HelenaGraph, estado: Mapping[str, Any]) -> str | None:
    """Precondicao DETERMINISTICA da rota `consultar_plano` (as duas camadas, como `inform`)."""
    if estado.get("error"):
        return "erro_do_turno"
    if self._consultas is None:
        return "consultas_desligadas"
    if estado.get("intent") != INTENT_CONSULTA_PLANO:
        return "intent_nao_e_consulta"
    if estado.get("psychosocial_risk") is not False:
        return "risco_psicossocial"
    if estado.get("sintoma_codigo"):
        return "sintoma_reportado"
    if not _em_dominio(estado.get("consulta_subtipo"), CONSULTA_SUBTIPOS):
        return "subtipo_fora_do_dominio"
    return None


def _rota_de_consulta(self: HelenaGraph, update: dict[str, Any]) -> dict[str, Any]:
    """Camada 1: `consultar_plano` so' com a precondicao; senao `falha_tecnica` (estado injustificado)."""
    recusa = self._consulta_recusada(update)
    if recusa is None:
        update["next_kind"] = NEXT_KIND_CONSULTA_PLANO
        return update
    logger.warning("helena_consulta_recusada", motivo=recusa, node="classify")
    update["next_kind"] = "escalate"
    update["escalation_motivo"] = "falha_tecnica"
    update["escalation_severidade"] = _severidade_de_intensidade(update.get("intensidade"))
    update["error"] = f"{ERRO_CONSULTA_RECUSADA}: {recusa}"
    return update


async def consultar_plano(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """A consulta do plano; com SOFRIMENTO na mesma mensagem ("o plano cobre psiquiatra? ando muito mal"),
    o acolhimento (com o CVV 188 e o 192) vem PRIMEIRO (dossie de seguranca, 18/10/2026). O portao de
    risco ja' rodou em `classify` (risco psicossocial vence a consulta)."""
    resultado = await self._consultar_plano_sem_acolhimento(state)
    return self._com_acolhimento(state, resultado)


async def _consultar_plano_sem_acolhimento(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """CONSULTA DO PLANO (DL de 07/10/2026): fatos do cadastro do PROPRIO beneficiario.

    SO' com identidade resolvida (DL-0077: candidato unico, `portable_subject_ref` no estado). Sem
    ela, ou com qualquer falha da AMH, o texto e' FIXO (`RESPOSTA_CONSULTA_SEM_IDENTIDADE`): nao
    abre processo e oferece a pessoa — quem pedir cai no gatilho 3 no turno seguinte. Com fatos, o
    modelo redige SO' a partir do bloco de fatos; a cerca propria
    (`motivo_de_recusa_da_consulta`) troca qualquer rascunho recusado pela resposta
    DETERMINISTICA, montada so' com os fatos — nunca por um segundo rascunho.

    LOG: so' tokens (subtipo, desfecho). Nunca a referencia, os fatos nem o texto.
    """
    subtipo = str(state.get("consulta_subtipo") or "")
    identidade = state.get("identidade_beneficiario")
    ref = identidade.get("portable_subject_ref") if isinstance(identidade, Mapping) else None
    fixo = {"response_text": RESPOSTA_CONSULTA_SEM_IDENTIDADE, "response_kind": "inform"}
    if (
        self._consultas is None
        or subtipo not in CONSULTA_SUBTIPOS
        # RODADA 3: com o acesso do beneficiario ligado o despachante ZERA o desfecho (DL-0083: quem diz
        # quem e' a pessoa e' a verificacao) e a identidade vem da referencia VERIFICADA. Sem esta
        # leitura, a consulta nunca saia do texto fixo no dev. Desfecho declarado diferente de
        # `reconhecido` continua recusando.
        or state.get("identidade_desfecho") not in (IDENTIDADE_RECONHECIDA, None)
        or not isinstance(ref, str)
        or not ref
    ):
        logger.info(
            "helena_consulta_plano", node="consultar_plano", subtipo=subtipo, resultado="sem_identidade"
        )
        return fixo
    mensagem_do_turno = str(state.get("message_body") or "")
    habilidade_do_roteador = state.get("habilidade_roteador")
    if habilidade_do_roteador == "cobertura" or (
        habilidade_do_roteador in (None, "")
        and _pergunta_de_cobertura_ampla(mensagem_do_turno)
        and not _pergunta_de_outro_fato_do_plano(mensagem_do_turno)
    ):
        # COBERTURA FORA (dono, 23/10/2026, DL-0154): o aviso unico com os canais, sem consulta.
        logger.info(
            "helena_consulta_plano", node="consultar_plano", subtipo=subtipo, resultado="cobertura_fora"
        )
        return {
            "response_text": _com_emergencia_se_preciso(state, self._textos[CHAVE_TEXTO_COBERTURA_FORA]),
            "response_kind": "inform",
        }
    view = await self._ler_consulta(ref, subtipo)
    if subtipo == "dependentes" and isinstance(view, FonteIndisponivel):
        # A rota de dependentes nao esta' carregada neste ambiente: o texto provisorio da rodada 10.
        logger.info(
            "helena_consulta_plano",
            node="consultar_plano",
            subtipo=subtipo,
            resultado="fonte_nao_carregada",
        )
        return {"response_text": RESPOSTA_DEPENDENTES, "response_kind": "inform"}
    fatos = fatos_da_consulta(
        subtipo, view, so_ativos=self._carteirinha_completa, hoje=self._hoje_dos_vinculos()
    )
    if self._dependentes_nome and subtipo == "dependentes" and fatos and fatos.get("eh_titular") is True:
        # NOME COMPLETO DOS DEPENDENTES (DL-0127): so' com a lista nominal COMPLETA do proprio titular e
        # do mesmo tamanho da contagem anonima; senao, o texto de sempre (sem nomes).
        com_nomes = await self._dependentes_com_nome(state, ref, fatos)
        if com_nomes is not None:
            return com_nomes
    if fatos is None and subtipo == "dependentes":
        # Falha da consulta (prazo, erro, recusa): nunca "nao consta" — a verdade e' "nao consegui agora".
        logger.info(
            "helena_consulta_plano", node="consultar_plano", subtipo=subtipo, resultado="indisponivel"
        )
        return {"response_text": RESPOSTA_DEPENDENTES_FALHA, "response_kind": "inform"}
    if fatos is None:
        logger.info(
            "helena_consulta_plano", node="consultar_plano", subtipo=subtipo, resultado="indisponivel"
        )
        # RODADA 3: a fonte falhou com a pessoa identificada: nao e' "nao consegui confirmar seus
        # dados" (falso, e D7). RODADA 8: nem "ainda nao estao disponiveis por este chat" — com a
        # fonte LIGADA a falha e' desta consulta, agora; o texto avanca (retentar -> pessoa -> canais).
        return {
            "response_text": _texto_plano_indisponivel(state, fonte_ligada=True),
            "response_kind": "inform",
        }
    # Carteirinha completa (chave ligada): o numero do PROPRIO verificado (a `ref` acima), so' na
    # memoria do turno; o texto leva o marcador e `respond` o troca no envio. Falha da rota = mascarado.
    numeros = CarteirinhasDoTurno({})
    if self._carteirinha_completa and subtipo == "carteirinha":
        numeros = await ler_carteirinhas_completas(
            self._consultas, ref, state.get("message_ref"), fatos, prazo_s=CONSULTA_PRAZO_S
        )
        logger.info("helena_carteirinha_completa", node="consultar_plano", com_numero=bool(numeros))
        self._guardar_carteirinhas(state, numeros)
    if self._v4 and subtipo == "carteirinha" and isinstance(view, ElegibilidadeView):
        # HELENA V4 (DL-0115): plano, segmentacao, vigencia e numero ANS com o codigo MASCARADO, e o
        # cartao no aplicativo/portal. Texto deterministico (sem modelo) sobre uma lista FECHADA de
        # fatos: nunca CNS, codigo inteiro, nome nem data de nascimento.
        fatos_v4 = fatos_da_carteirinha(
            view, so_ativos=self._carteirinha_completa, hoje=self._hoje_dos_vinculos()
        )
        logger.info("helena_consulta_plano", node="consultar_plano", subtipo=subtipo, resultado="v4")
        return {
            "response_text": texto_da_carteirinha(
                fatos_v4, completos=numeros.mascaras, so_ativos=self._carteirinha_completa
            ),
            "response_kind": "inform",
            "consulta_literais": literais_dos_fatos(fatos_v4),
            "resposta_com_dados_do_plano": True,
        }
    texto: str | None = None
    recusa: str | None
    if subtipo in SUBTIPOS_REDIGIDOS and not so_deterministica(
        subtipo, fatos, carteirinha_completa=self._carteirinha_completa
    ):
        texto = await self._redigir_consulta(state, subtipo, fatos)
        recusa = motivo_de_recusa_da_consulta(texto, fatos) if isinstance(texto, str) else "sem_rascunho"
        if recusa is None and subtipo == "elegibilidade" and isinstance(texto, str):
            # Rascunho sem a segmentacao/acomodacao que o cadastro tem: volta ao texto deterministico.
            recusa = motivo_de_omissao_da_elegibilidade(texto, fatos)
    else:
        # Autorizacao e carencia (revisao do #690): SEMPRE a resposta deterministica, sem modelo.
        recusa = "subtipo_deterministico"
    if recusa is not None or not isinstance(texto, str):
        texto = resposta_deterministica(
            subtipo, fatos, completos=numeros.mascaras, so_ativos=self._carteirinha_completa
        )
    else:
        # RODADA 4: sem frase de pagamento (papel do Lucas) nem "nao consta" sobre cobertura.
        texto = _frases_curtas(_sem_frases_fora_do_papel(texto))
    assert isinstance(texto, str)
    # D-01 (red team): "este e' o CPF de outra pessoa... qual a carencia dele?" recebia a carencia do
    # PROPRIO titular, sem aviso. A consulta nunca usa um CPF do texto; o aviso diz isso a quem le'.
    texto = com_aviso_se_for_de_outra_pessoa(state.get("message_body"), texto)
    mensagem = str(state.get("message_body") or "")
    if _pergunta_de_cobertura(mensagem) and RESPOSTA_COBERTURA_ESPECIFICA not in texto:
        # RODADA 3: cobertura de procedimento especifico nunca e' prometida pela segmentacao.
        texto = f"{texto.strip()} {RESPOSTA_COBERTURA_ESPECIFICA}"
    # RODADA 4: o 192 na frente so' com sinal de saude; "e' urgente" sobre cobertura leva o 192 no fim.
    texto = _com_emergencia_se_preciso(state, texto.strip())
    logger.info(
        "helena_consulta_plano",
        node="consultar_plano",
        subtipo=subtipo,
        resultado="redigido" if recusa is None else "deterministico",
        recusa=recusa,
        prompt_version=CONSULTA_PLANO_PROMPT_VERSION,
    )
    return {
        "response_text": texto.strip(),
        "response_kind": "inform",
        "consulta_literais": literais_dos_fatos(fatos),
        # Onda 0 (historico): a resposta carrega dados do plano -> o historico grava so' um marcador.
        "resposta_com_dados_do_plano": True,
    }


async def _dependentes_com_nome(
    self: HelenaGraph, state: Mapping[str, Any], ref: str, fatos: Mapping[str, Any]
) -> dict[str, Any] | None:
    """A resposta de dependentes com o nome completo (marcador; o nome so' no envio), ou `None`."""
    nomes, graus = await ler_dependentes_nominais(
        self._consultas, ref, state.get("message_ref"), prazo_s=CONSULTA_PRAZO_S
    )
    total = fatos.get("total_ativos")
    if not nomes or fatos.get("lista_truncada") is True or not isinstance(total, int) or total != len(graus):
        logger.info("helena_dependentes_nome", node="consultar_plano", com_nome=False)
        return None
    self._guardar_nomes(state, nomes)
    logger.info("helena_dependentes_nome", node="consultar_plano", com_nome=True, dependentes=len(graus))
    return {
        "response_text": _com_emergencia_se_preciso(state, texto_de_dependentes_com_nomes(graus)),
        "response_kind": "inform",
        "consulta_literais": literais_dos_fatos(fatos),
        "resposta_com_dados_do_plano": True,
    }


async def _graus_dos_dependentes(
    self: HelenaGraph, state: Mapping[str, Any], ref: str
) -> tuple[list[str], bool]:
    """Os graus dos dependentes ATIVOS do titular (um por dependente), ou `[]`, e se os NOMES (DL-0127)
    ficaram no turno. Com a chave dos nomes, a lista nominal do contrato `tina-dependentes-nome` (a mesma
    ordem); sem ela ou com falha, o contrato anonimo."""
    if self._dependentes_nome:
        agora = self._relogio().timestamp()
        guardado = self._nomes_em_memoria.get(ref)
        if guardado is not None and agora - guardado[0] < NOMES_EM_MEMORIA_S:
            nomes, graus_nominais = guardado[1], guardado[2]
        else:
            nomes, graus_nominais = await ler_dependentes_nominais(
                self._consultas, ref, state.get("message_ref"), prazo_s=CONSULTA_PRAZO_S
            )
            if not nomes and guardado is not None:
                # DL-0131: a leitura falhou (limite, prazo): os nomes ja' lidos antes valem.
                nomes, graus_nominais = guardado[1], guardado[2]
            if nomes:
                while len(self._nomes_em_memoria) >= 64:
                    self._nomes_em_memoria.pop(next(iter(self._nomes_em_memoria)))
                self._nomes_em_memoria[ref] = (agora, nomes, graus_nominais)
        if nomes and len(graus_nominais) <= 9:
            self._guardar_nomes(state, nomes)
            return [g for _, g in graus_nominais], True
    return await self._graus_anonimos(ref), False


async def _graus_anonimos(self: HelenaGraph, ref: str) -> list[str]:
    """Os graus dos dependentes ATIVOS do titular (um por dependente, pelo contrato anonimo), ou `[]`."""
    view = await self._ler_consulta(ref, "dependentes")
    if view is None or isinstance(view, FonteIndisponivel):
        return []
    fatos = fatos_da_consulta(
        "dependentes", view, so_ativos=self._carteirinha_completa, hoje=self._hoje_dos_vinculos()
    )
    if not fatos or fatos.get("eh_titular") is not True:
        return []
    graus: list[str] = []
    for r in fatos.get("relacoes") or []:
        quantidade = r.get("quantidade")
        vezes = quantidade if isinstance(quantidade, int) and quantidade > 0 else 0
        graus.extend([grau_legivel(str(r.get("relacao") or "")) or "sem grau informado"] * vezes)
    return graus[:9]


def _hoje_dos_vinculos(self: HelenaGraph) -> date | None:
    """O dia (Brasilia) da regra de vinculo ativo, so' com a chave da carteirinha completa (`None` sem
    ela)."""
    return hoje_em_brasilia(self._relogio()) if self._carteirinha_completa else None


def _chave_do_turno(state: Mapping[str, Any]) -> str:
    return f"{state.get('conversation_id') or ''}|{state.get('message_ref') or ''}"


def _guardar_carteirinhas(self: HelenaGraph, state: Mapping[str, Any], numeros: CarteirinhasDoTurno) -> None:
    """Guarda os numeros completos do turno SO' em memoria (nunca no estado): `respond` os tira daqui."""
    chave = self._chave_do_turno(state)
    self._carteirinhas_do_turno.pop(chave, None)
    if numeros:
        while len(self._carteirinhas_do_turno) >= 16:
            self._carteirinhas_do_turno.pop(next(iter(self._carteirinhas_do_turno)))
        self._carteirinhas_do_turno[chave] = numeros


def _guardar_nomes(self: HelenaGraph, state: Mapping[str, Any], nomes: NomesDoTurno) -> None:
    """Guarda os nomes dos dependentes do turno SO' em memoria (nunca no estado); `respond` os tira
    daqui."""
    chave = self._chave_do_turno(state)
    self._nomes_do_turno.pop(chave, None)
    if nomes:
        while len(self._nomes_do_turno) >= 16:
            self._nomes_do_turno.pop(next(iter(self._nomes_do_turno)))
        self._nomes_do_turno[chave] = nomes


def _texto_do_envio(self: HelenaGraph, state: Mapping[str, Any], texto: str) -> str:
    """O texto ENVIADO: com a chave da carteirinha completa, os marcadores viram o numero do turno (ou a
    mascara, sem numero); com a dos nomes dos dependentes (DL-0127), o marcador vira o nome completo (ou
    "dependente N"). Desligadas, o texto como esta' (byte a byte)."""
    if self._dependentes_nome:
        nomes = self._nomes_do_turno.pop(self._chave_do_turno(state), None)
        texto = nomes.preencher(texto) if nomes else sem_marcador_de_dependente(texto)
    if not self._carteirinha_completa:
        return texto
    numeros = self._carteirinhas_do_turno.pop(self._chave_do_turno(state), None)
    return numeros.preencher(texto) if numeros else sem_marcador_de_carteirinha(texto)


async def _ler_consulta(self: HelenaGraph, ref: str, subtipo: str) -> Any:
    """UMA leitura da fonte do plano, com o prazo do turno. Falha de rede/servico/prazo = `None`; bug
    (`PROGRAMMING_ERRORS`) sobe. So' chamada com a fonte ligada."""
    assert self._consultas is not None
    try:
        async with asyncio.timeout(CONSULTA_PRAZO_S):
            return await self._consultas.consultar(ref, subtipo)
    except PROGRAMMING_ERRORS:
        raise
    except EXTERNAL_DEPENDENCY_FAILURES:
        return None


def _ref_verificada(self: HelenaGraph, state: HelenaState) -> str | None:
    """A referencia opaca da pessoa VERIFICADA (a mesma regra de `consultar_plano`), ou `None`."""
    identidade = state.get("identidade_beneficiario")
    ref = identidade.get("portable_subject_ref") if isinstance(identidade, Mapping) else None
    if (
        self._consultas is None
        or state.get("identidade_desfecho") not in (IDENTIDADE_RECONHECIDA, None)
        or not isinstance(ref, str)
        or not ref
    ):
        return None
    return ref


async def _plano_na_passagem(self: HelenaGraph, state: HelenaState) -> tuple[str, dict[str, Any]]:
    """RODADA 8 (teste real do dono): a parte do PLANO de uma mensagem MISTA ("quando vence o boleto? e
    meu plano cobre dermatologista?"), entregue JUNTO da frase de passagem — a cobranca segue com o
    Lucas neste mesmo turno e a pessoa nunca precisa reenviar nada. Devolve `(texto, extras_do_estado)`.

    Sem a fonte (ou sem a pessoa verificada), ou com a fonte falhando, so' o "onde confirmar"."""
    mensagem = str(state.get("message_body") or "")
    if _pergunta_de_cobertura(mensagem):
        # COBERTURA FORA (dono, 23/10/2026, DL-0154): a parte de cobertura recebe so' o aviso.
        return (self._textos[CHAVE_TEXTO_COBERTURA_FORA], {})
    ref = self._ref_verificada(state)
    if ref is None:
        return (RESPOSTA_PLANO_FALHA_NA_PASSAGEM, {})
    subtipo = _subtipo_da_consulta(mensagem)
    fatos = fatos_da_consulta(
        subtipo,
        await self._ler_consulta(ref, subtipo),
        so_ativos=self._carteirinha_completa,
        hoje=self._hoje_dos_vinculos(),
    )
    if fatos is None:
        return (RESPOSTA_PLANO_FALHA_NA_PASSAGEM, {})
    texto = resposta_deterministica(subtipo, fatos, so_ativos=self._carteirinha_completa)
    return (
        texto,
        {"consulta_literais": literais_dos_fatos(fatos), "resposta_com_dados_do_plano": True},
    )


async def _redigir_consulta(
    self: HelenaGraph, state: HelenaState, subtipo: str, fatos: Mapping[str, Any]
) -> str | None:
    """O rascunho do modelo sobre os FATOS, ou `None` se o modelo falhou. SEM cerca (quem cerca e'
    `consultar_plano`). Os fatos e a mensagem vao em blocos demarcados (HEL-06)."""
    prompt = consulta_prompt(
        subtipo=subtipo,
        fatos=fatos,
        bloco_mensagem=render_untrusted_block("message_body", state.get("message_body", "")),
    )
    try:
        return await self._llm.generate(
            prompt,
            phi=True,
            agent_id="helena",
            tenant_id=state.get("tenant_id", ""),
            # ADR-0009 §2 / CC-12: fraseia fatos ja' decididos -> task_default.
            task_kind="task_default",
        )
    except PROGRAMMING_ERRORS:
        raise
    except EXTERNAL_DEPENDENCY_FAILURES:
        return None


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
from maezo.agents.helena.graph import (  # noqa: E402
    CONSULTA_PRAZO_S,
    ERRO_CONSULTA_RECUSADA,
    NEXT_KIND_CONSULTA_PLANO,
    NOMES_EM_MEMORIA_S,
    RESPOSTA_COBERTURA_ESPECIFICA,
    RESPOSTA_DEPENDENTES,
    RESPOSTA_PLANO_FALHA_NA_PASSAGEM,
    HelenaState,
    _com_emergencia_se_preciso,
    _em_dominio,
    _frases_curtas,
    _pergunta_de_cobertura,
    _pergunta_de_cobertura_ampla,
    _pergunta_de_outro_fato_do_plano,
    _sem_frases_fora_do_papel,
    _severidade_de_intensidade,
    _subtipo_da_consulta,
    _texto_plano_indisponivel,
    logger,
)
