"""Habilidade `atendimento_humano` da Helena (ADR-0067 §5): o escalonamento para a equipe
(SP-OP-ESCALATION-001), a trava "caso so' com pedido ou sim", o caso ja' aberto e o encerramento a pedido
da pessoa (P1/grave nunca se encerra por aqui).

MOVIDO SEM MUDANCA de `graph.py` (Plano de ajustes da Helena v2, etapa 8, PR so' de mudanca de lugar): cada
metodo do `HelenaGraph` virou uma funcao deste modulo com o MESMO corpo (`self` e' o grafo) e a classe aponta
para ela. O comportamento muda so' no PR seguinte.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo LOGO ANTES da classe (os helpers de modulo ja'
existem) e este modulo importa os nomes do graph NO FIM.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from maezo.agents.helena import roteador_v7 as _rv7
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from maezo.agents.helena.prompts import SYSTEM_PROMPT_VERSION
from maezo.domain.conversa.caso_clinico import (
    MOTIVOS_DE_ALERTA_CLINICO,
    SUFIXO_CASO_CLINICO,
    chave_do_escalonamento,
)
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.error_text import start_unavailable_error
from maezo.runtime.start_outcome import notify_start_failure as emit_start_failure_notice
from maezo.tools.workers.phi_vars import redact_error_message, redact_free_text

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph


def _chaves_da_conversa(self: HelenaGraph, state: Mapping[str, Any]) -> tuple[str, str]:
    tenant, conversa = str(state.get("tenant_id", "")), str(state.get("conversation_id", ""))
    return (
        chave_do_escalonamento(tenant, conversa),
        chave_do_escalonamento(tenant, conversa, clinico=True),
    )


async def _caso_ativo(self: HelenaGraph, business_key: str) -> ProcessInstance | None:
    """A instancia VIVA da chave, ou `None`. Leitura que falha = `None` (quem chama decide o lado
    seguro: sem caso legivel, nada e' encerrado)."""
    try:
        return await self._cibseven.find_active_instance(business_key)
    except PROGRAMMING_ERRORS:
        raise
    except CibSevenError:
        return None
    except EXTERNAL_DEPENDENCY_FAILURES:
        return None


async def _casos_abertos_da_conversa(self: HelenaGraph, state: Mapping[str, Any]) -> list[str]:
    abertos = []
    for chave in self._chaves_da_conversa(state):
        if await self._caso_ativo(chave) is not None:
            abertos.append(chave)
    return abertos


def _sim_ou_nao_ao_encerramento(plano: str) -> str | None:
    """ "sim"/"nao" a "Quer que eu encerre o seu atendimento?": com o roteador v7 no turno, a leitura do
    MODELO no contexto ("pode encerrar, ja' resolvi" = sim); sem ele, as respostas curtas de sempre."""
    v7 = _rv7.LEITURA_DO_TURNO.get()
    if v7 is not None:
        return v7.sim_nao if v7.sim_nao in ("sim", "nao") else None
    if _RESPOSTA_NAO.match(plano):
        return "nao"
    return "sim" if _RESPOSTA_SIM.match(plano) else None


async def _base_confirmacao_de_encerramento(
    self: HelenaGraph,
    state: HelenaState,
    update: dict[str, Any],
    texto: str,
    *,
    leitura: str | None = None,
) -> dict[str, Any] | None:
    """A resposta a "Quer que eu encerre o seu atendimento com a equipe? Responda SIM para encerrar."

    "sim" -> encerra cada caso VIVO da conversa (a chave da conversa e a `-clin`) que NAO seja grave; se
    algum for grave, ele fica e a pessoa ouve o texto do caso grave com 192. "nao" -> o caso continua.
    Qualquer outra coisa (inclusive conteudo clinico ou sinal de emergencia) -> `None`: o de sempre.
    `leitura` ja' decidida por quem chama (a recusa da equipe lida pelo roteador = "sim")."""
    plano = _plano(texto)
    if (
        _conteudo_clinico(texto)
        or state.get("sinal_saude_lexico") is True
        or _sinal_forte_de_emergencia(texto)
        or _sinal_fraco_de_emergencia(texto)
    ):
        return None
    resposta = leitura or _sim_ou_nao_ao_encerramento(plano)
    if resposta == "nao":
        logger.info("helena_base_saude_encerramento", node="classify", resposta="nao")
        return self._base_texto_fixo(update, RESPOSTA_ENCERRAMENTO_MANTIDO)
    if resposta != "sim":
        return None
    encerrados, graves, falhas = 0, 0, 0
    for chave in self._chaves_da_conversa(state):
        instancia = await self._caso_ativo(chave)
        if instancia is None:
            continue
        motivo, severidade = await self._motivo_e_severidade(chave)
        if caso_grave_para_encerrar(motivo, severidade):
            graves += 1
            continue
        if await self._retirar_caso(state, chave, motivo=motivo, severidade=severidade):
            encerrados += 1
        else:
            falhas += 1
    logger.info(
        "helena_base_saude_encerramento",
        node="classify",
        resposta="sim",
        encerrados=encerrados,
        graves=graves,
        falhas=falhas,
    )
    if graves:
        return self._base_texto_fixo(update, RESPOSTA_CASO_GRAVE_NAO_ENCERRA)
    if falhas:
        return self._base_texto_fixo(update, RESPOSTA_ENCERRAMENTO_FALHOU)
    if encerrados:
        return self._base_texto_fixo(update, RESPOSTA_CASO_ENCERRADO)
    return self._base_texto_fixo(update, RESPOSTA_SEM_CASO_PARA_ENCERRAR)


async def _retirar_caso(
    self: HelenaGraph, state: HelenaState, business_key: str, *, motivo: str, severidade: object
) -> bool:
    """Encerra UM caso nao grave pela mensagem `msg.escalation.withdrawn` (SP-OP-ESCALATION-001 v1.1.0),
    com AUDITORIA ANTES DO EFEITO (`correlate_message_audited`, ADR-0007). Releitura da instancia logo
    antes (ela pode ter terminado no meio) e a confirmacao DEPOIS: so' conta como encerrado quando o
    motor nao tem mais instancia viva na chave. `False` = nao encerrou (a pessoa ouve que o caso
    continua aberto)."""
    instancia = await self._caso_ativo(business_key)
    if instancia is None:
        return False
    provenance = AgentDecisionProvenance(
        agent_id="helena",
        agent_version=self._agent_version,
        tenant_id=state.get("tenant_id", ""),
        model_id=self._model_id,
        prompt_version=SYSTEM_PROMPT_VERSION,
        decision_basis={
            "acao": "encerramento_a_pedido_do_beneficiario",
            "motivo_do_caso": motivo,
            "severidade_do_caso": str(severidade) if severidade is not None else "",
            "confirmacao": "sim",
        },
    )
    try:
        await correlate_message_audited(
            self._cibseven,
            process_key=PROCESS_KEY,
            message_name=NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO,
            business_key=business_key,
            instance_id=instancia.instance_id,
            variables={
                "resultado": RESULTADO_ENCERRADO_A_PEDIDO,
                "encerramento_origem": "beneficiario_canal",
            },
            audit_sink=self._audit_sink,
            provenance=provenance,
        )
    except PROGRAMMING_ERRORS:
        raise
    except CibSevenError:
        logger.warning("helena_base_saude_encerramento_falhou", node="classify", business_key=business_key)
        return False
    ainda_viva = await self._caso_ativo(business_key)
    if ainda_viva is not None and ainda_viva.instance_id == instancia.instance_id:
        logger.warning(
            "helena_base_saude_encerramento_nao_confirmado", node="classify", business_key=business_key
        )
        return False
    logger.info("helena_base_saude_caso_encerrado", node="classify", business_key=business_key, motivo=motivo)
    return True


async def _motivo_e_severidade(self: HelenaGraph, business_key: str) -> tuple[str, object]:
    """`motivo_categoria` e `severidade` do caso vivo. Leitura que falha -> `("", None)`, que
    `caso_grave_para_encerrar` trata como GRAVE (na duvida, nao encerra)."""
    try:
        status = await self._cibseven.get_process_status(business_key)
    except PROGRAMMING_ERRORS:
        raise
    except CibSevenError:
        return "", None
    except EXTERNAL_DEPENDENCY_FAILURES:
        return "", None
    variaveis = status.variables or {}
    return str(variaveis.get("motivo_categoria") or ""), variaveis.get("severidade")


async def escalar_revogacao_pendente(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """ACESSO DO BENEFICIARIO (DL-0083, revisao do #700): a revogacao do consentimento nao foi gravada no
    lago apos o teto de regravacoes. MESMO caminho de `escalar_identidade_nao_verificada` (categoria
    `solicitacao_humano`, a que o roteamento DMN ja' conhece; severidade `leve`), com o resumo FIXO que
    diz ao atendente o motivo operacional (`revogacao_consentimento_pendente`) e o runbook."""
    return await self._start_escalation(
        state,
        motivo="solicitacao_humano",
        severidade="leve",
        response_kind="escalate",
        resumo_fixo=RESUMO_REVOGACAO_PENDENTE,
    )


async def escalar_identidade_nao_verificada(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """ACESSO DO BENEFICIARIO (DL-0083): a pessoa esgotou as tentativas de verificar a identidade.

    Abre SP-OP-ESCALATION-001 pelo MESMO caminho de `escalate`/`schedule` (chave de negocio idempotente
    `ESC-{tenant}-{conversation_id}`, auditoria antes do efeito, start idempotente), com
    `motivo_categoria="solicitacao_humano"` (a categoria EXISTENTE mais proxima de "uma pessoa precisa
    assumir"; nao ha' uma propria para falha de verificacao) e `severidade="leve"` (a mesma do
    agendamento, valor de negocio explicito, nao um chute). O resumo para o atendente e' FIXO e nenhum
    modelo e' chamado: a mensagem que levou aqui pode ser um CPF, e ela nem entra no estado recebido.

    O `response_text` devolvido e' o do escalonamento generico e NAO e' enviado por quem chama (o
    despachante envia o texto fixo do acesso).
    """
    return await self._start_escalation(
        state,
        motivo="solicitacao_humano",
        severidade="leve",
        response_kind="escalate",
        resumo_fixo=RESUMO_IDENTIDADE_NAO_VERIFICADA,
    )


async def escalate(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """Start SP-OP-ESCALATION-001 idempotently and draft the handoff response.

    A tool failure here never blocks the turn — the response still tells the beneficiary a
    human will follow up (`error` records the failure for observability; the runtime's own
    fail-closed posture treats an unstarted escalation as an incident, never a silent drop).

    HELENA-SEVERIDADE-DEFAULT: `severidade` is read WITHOUT a fallback. Every real gatilho in
    `classify` (the only place that decides `next_kind="escalate"` off a genuine clinical/
    administrative signal) assigns `escalation_severidade` explicitly; the one path that
    reaches here without it is `receive`'s own missing-runtime-context escalate. That case's
    severidade is genuinely UNKNOWN, and an unknown one is never announced as `leve` (mirrors
    GAP-ESC-SEVERITY-GROUP's own principle, one layer down). `None` is forwarded verbatim into
    `_start_escalation`.

    §Delta-3 (regressao P-12) — CORRECAO of what this docstring used to claim: that a `None`
    `severidade` was REFUSED fail-closed on both notify channels and that "NO notification is
    published on either channel", the case still reaching the HITL through
    `BE_NotifFallbackFailed`. It did not reach it — the live engine proved the escalation
    stopped short of `UT_TratarEscalonamento`. Both paths that arrive here without a severidade
    (missing runtime context, and the classifier failure) carry
    `motivo_categoria="falha_tecnica"`, which is exactly the motivo the contract declares
    `null` for; `escalation.py::_exigir_severidade` now implements that declared exception, so
    the notification IS published (to the group `escalation_routing`'s rule `r6` chose — that
    rule matches `severidade` at the `-` wildcard, so it never read it) and the instance
    continues along `Flow_Notificar_UT` -> `UT_TratarEscalonamento`. What is removed is the
    fabricated value, not the page.
    """
    # Ausencia nao e classificacao "outro" (HELENA-ESCALATION-MOTIVO-OUTRO-FALLBACK).
    motivo: MotivoCategoria | None = state.get("escalation_motivo") or (
        "falha_tecnica" if state.get("error") else None
    )
    severidade: Severidade | None = state.get("escalation_severidade")
    barrado = await self._trava_caso_sem_pedido(state, motivo)
    if barrado is not None:
        return barrado
    return await self._start_escalation(state, motivo=motivo, severidade=severidade, response_kind="escalate")


async def _trava_caso_sem_pedido(
    self: HelenaGraph, state: HelenaState, motivo: MotivoCategoria | None
) -> dict[str, Any] | None:
    """TRAVA DE SAIDA `caso_so_com_sim` (ADR-0066; regra do dono: caso so' com "sim" ou pedido EXPLICITO
    de pessoa). Um pedido de pessoa (`solicitacao_humano` com `intent=human_request`) so' abre o caso
    quando a mensagem tem pedido explicito de pessoa (verbo + pessoa/atendente/equipe), um "sim"/"quero" a
    uma oferta, a escolha numerica de uma opcao oferecida ou a ameaca a orgao regulador. Fora disso
    ("beleza, valeu" depois de uma oferta da equipe) a abertura e' barrada e a Helena responde com
    cortesia. Os demais motivos (alarme, risco, clinico, falha tecnica, coleta esgotada) nao passam
    aqui."""
    if motivo != "solicitacao_humano" or state.get("intent") != "human_request":
        return None
    texto = str(state.get("message_body") or "")
    plano = _plano(texto)
    v7 = _rv7.LEITURA_DO_TURNO.get()
    if _rv7.pede_pessoa_no_contexto():
        # Contrato v7 (etapa 8): o "sim" a uma oferta e o pedido de pessoa lidos no CONTEXTO ("pode passar
        # pra equipe sim, mas rapido"); a recusa nunca abre caso.
        return None
    if (
        _pedido_explicito_com_verbo(texto)
        or _pedido_explicito_de_pessoa(texto)
        or (v7 is None and _SIM_A_OFERTA.search(plano))
        # Avaliacao r4 de 22/10/2026: o polegar sozinho a uma oferta e' "sim" no contexto.
        or _lc.so_emoji_de_acordo(texto)
        or _ESCOLHA_DE_OPCAO.match(plano.strip())
        or _CITA_UMA_PESSOA.search(plano)
        or _ameaca_regulatoria(texto)
    ):
        return None
    if await self._motivo_do_caso_aberto(_business_key(state)):
        # Ja' ha' caso ABERTO nesta conversa: nada de caso NOVO aqui. O start reaproveita o aberto e
        # responde com o texto do caso aberto ("quanto tempo demora?", recado ao atendente), como sempre.
        return None
    logger.warning("helena_trava_caso_sem_pedido", node="escalate")
    return {
        "response_text": _lc.resposta_de_cortesia(texto),
        "response_kind": "inform",
        "next_kind": "inform",
        "intent": "information",
        "escalation_motivo": None,
        "escalation_severidade": None,
        "coleta_pendente": None,
        "coleta_contexto": "",
    }


async def _motivo_do_caso_aberto(self: HelenaGraph, business_key: str) -> str:
    """O `motivo_categoria` do caso que ocupa a chave da conversa, ou `""` (na duvida / leitura que
    falhou). Quem le' decide o lado seguro: abrir um caso clinico a mais custa um atendimento; deixar
    de abrir um custa o alerta (a idempotencia da chave `-clin` impede que o "a mais" se repita)."""
    try:
        status = await self._cibseven.get_process_status(business_key)
    except PROGRAMMING_ERRORS:
        raise
    except CibSevenError:
        return ""
    except EXTERNAL_DEPENDENCY_FAILURES:
        return ""
    return str((status.variables or {}).get("motivo_categoria") or "")


async def _caso_aberto_e_clinico(self: HelenaGraph, business_key: str) -> bool:
    """O caso que ocupa a chave da conversa e' um alerta clinico? Na duvida, NAO."""
    return await self._motivo_do_caso_aberto(business_key) in MOTIVOS_DE_ALERTA_CLINICO


async def _start_escalation(
    self: HelenaGraph,
    state: HelenaState,
    *,
    motivo: MotivoCategoria | None,
    severidade: Severidade | None,
    response_kind: ResponseKind,
    error: str | None = None,
    resumo_fixo: str | None = None,
) -> dict[str, Any]:
    """Shared SP-OP-ESCALATION-001 start, factored out of `escalate` (GAP 9.2) so `schedule`
    can hand off to a human through the EXACT SAME audited/idempotent path instead of a
    second, parallel (and easy-to-drift) implementation. `response_kind` is the only thing
    that varies downstream: it selects which honest reply `prompts.py.response_prompt()`
    drafts (`"escalate"`'s generic handoff text vs `"schedule"`'s scheduling-specific one) —
    the escalation itself (business key, audit-before-effect, idempotent start, provenance)
    is identical regardless of caller.

    HELENA-SEVERIDADE-DEFAULT: `severidade` is `Severidade | None` — `None` on `escalate`'s
    missing-runtime-context path and on its classifier-failure path (never fabricated to
    `leve`); `schedule` and every real clinical `escalate` gatilho always pass a real domain
    value. `None` rides verbatim into the `severidade` process variable. §Delta-3: the worker
    (`escalation.py::_exigir_severidade`) ACCEPTS that `None` under the contract's declared
    `motivo_categoria=falha_tecnica` exception — it is not refused, and it is never coerced to
    a domain value anywhere along the way; every OTHER motivo still fails closed there.
    """
    business_key = _business_key(state)

    # HEL-05 — DEFENSE IN DEPTH, on top of the chokepoint's own scrub. `_resumo_contexto` is
    # a free-text LLM draft over the beneficiary's own message, and Helena is the DIRECT
    # producer of the contractual `resumo_contexto` (SP-OP-ESCALATION-001 §Variaveis:
    # "pseudonimizado"), so the identifier net is applied HERE, at the producer, and again at
    # `start_process_idempotent` (CC-06). Scrubbing twice is idempotent: `redact_free_text`
    # replaces identifier substrings with class tokens, and the class tokens match no pattern.
    # DL-0083: o chamador pode trazer um resumo FIXO (texto escrito pelo codigo, nunca da pessoa) e
    # entao o
    # modelo nao e' chamado. E' o caso do bloqueio do acesso do beneficiario, em que a mensagem pode ser
    # um CPF e NUNCA pode chegar a um prompt.
    resumo = redact_free_text(
        resumo_fixo if resumo_fixo is not None else await self._resumo_contexto(state, motivo)
    )
    motivo_tecnico = error or state.get("error")
    if motivo == "falha_tecnica" and motivo_tecnico:
        # R1 cycle-1 fix: carry the technical-failure reason into the human handoff so the
        # attendant sees WHY the automated turn failed. The reason is bounded and contains
        # no raw LLM output / no beneficiary text (see `_classify_llm`) — everything else in
        # this variable set is already pseudonymized (ADR-0006).
        #
        # HEL-05: `str(...)[:300]` was a LENGTH bound, never a CONTENT one. `error` is not
        # always the bounded classifier token this comment describes — `escalate`'s own
        # `CibSevenError` handler writes `f"start_process indisponivel: {exc}"` into it, and a
        # transport exception message is arbitrary text from another system. `redact_error_message`
        # (which delegates to the same `redact_free_text` net) bounds BOTH.
        resumo = f"{resumo} [falha tecnica: {redact_error_message(motivo_tecnico)}]"
    variables: dict[str, Any] = {
        "tenant_id": state.get("tenant_id", ""),
        "source_agent_id": "helena",
        "source_agent_version": self._agent_version,
        "conversation_id": state.get("conversation_id", ""),
        "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
        "canal": state.get("canal", "whatsapp"),
        "motivo_categoria": motivo,
        "severidade": severidade,
        "resumo_contexto": resumo,
    }
    ref = state.get("dmn_decision_ref")
    if ref:
        variables["dmn_decision_ref"] = ref

    if response_kind == "escalate":
        # TEXTO FIXO, sem modelo — ver `RESPOSTA_ESCALONAMENTO_URGENTE`. O modelo continua
        # redigindo so' o `schedule` (hoje sem rota de entrada, DL-0052).
        _paciente = _paciente_da_conversa(state)
        response_text = _texto_de_escalonamento(
            motivo,
            severidade,
            saude=_mensagem_de_saude(state),
            paciente=_paciente[0] if _paciente else None,
        )
        if motivo == "solicitacao_humano" and _PEDE_CONTATO_DA_EQUIPE.search(
            _plano(str(state.get("message_body") or ""))
        ):
            # RODADA 7: pediu o CONTATO da equipe: nao ha' contato a dar; a equipe fala aqui.
            response_text = RESPOSTA_HANDOFF_CONTATO_DA_EQUIPE
    else:
        try:
            response_text = await self._respond_llm(state, response_kind)
        except RespostaRecusadaError:
            # A recusa ja foi registrada e contada em `_respond_llm`. AQUI um humano FOI mesmo
            # acionado (este metodo esta' abrindo o processo), entao a constante honesta promete
            # o que e' verdade e nao contem nenhum dos padroes proibidos. Nao ha nova tentativa
            # pelo mesmo motivo que em `inform`. DL-0088: a equipe nomeada sai do MOTIVO.
            response_text = _texto_de_handoff(motivo, saude=_mensagem_de_saude(state))
    provenance = AgentDecisionProvenance(
        agent_id="helena",
        agent_version=self._agent_version,
        tenant_id=state.get("tenant_id", ""),
        model_id=self._model_id,
        prompt_version=SYSTEM_PROMPT_VERSION,
        # Bounded routing tokens ONLY — never `resumo_contexto` (PHI, ADR-0006).
        decision_basis={
            "escalation_motivo": motivo,
            "escalation_severidade": severidade,
            "dmn_decision_ref": state.get("dmn_decision_ref") or "",
        },
    )
    try:
        instance = await start_process_idempotent(
            self._cibseven,
            process_key=PROCESS_KEY,
            business_key=business_key,
            variables=variables,
            audit_sink=self._audit_sink,
            provenance=provenance,
        )
        motivo_do_caso = ""
        ja_ativo = _start_desfecho_de(instance.start_outcome) == START_DESFECHO_JA_ATIVO
        if ja_ativo:
            motivo_do_caso = await self._motivo_do_caso_aberto(business_key)
        if (
            ja_ativo
            and motivo in _MOTIVOS_CLINICOS_COM_CHAVE_PROPRIA
            and not business_key.endswith(SUFIXO_CASO_CLINICO)
            and not _caso_clinico_cobre_o_motivo(motivo_do_caso, motivo)
        ):
            # DL-0072 (LU090): alerta clinico GRAVE nunca e' suprimido por um caso de outra
            # classe. A chave da conversa esta' ocupada por cobranca (ou outro assunto): abre-se um
            # caso clinico PROPRIO, P1, e o outro segue como esta. Uma falha aqui cai no mesmo
            # `except CibSevenError` abaixo: a pessoa recebe a resposta honesta de falha, nunca o
            # "seu atendimento ja esta aberto" que escondia a emergencia.
            business_key = chave_do_escalonamento(
                state.get("tenant_id", ""), state.get("conversation_id", ""), clinico=True
            )
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=PROCESS_KEY,
                business_key=business_key,
                variables={
                    **variables,
                    "resumo_contexto": f"{variables['resumo_contexto']} {NOTA_CASO_CLINICO_PARALELO}",
                },
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
            motivo_do_caso = ""
    except CibSevenError as exc:
        return {
            "escalation_started": False,
            # CC-01: o marcador que `respond` le para NAO prometer um humano que ninguem
            # acionou. `escalation_started is False` sozinho nao serve: e tambem o neutro de
            # um turno informativo, que nunca tentou escalar coisa nenhuma.
            "start_failed": True,
            "start_desfecho": START_DESFECHO_FALHOU,
            "escalation_motivo": motivo,
            "escalation_severidade": severidade,
            "escalation_business_key": business_key,
            # HEL-05 (feeder): a transport exception message is arbitrary text from another
            # system, and this `error` survives into the NEXT turn's `[falha tecnica: ...]`
            # suffix under the live checkpointed dispatch (T4b).
            "error": start_unavailable_error(exc),
            "response_text": response_text,
            "response_kind": response_kind,
        }

    # CERCA TEXTO x FATO (F1): o veredito que o chokepoint ja' ASSERTAVA do proprio fluxo de
    # controle (`StartOutcome`, F3 MAJOR-2) e que este grafo ignorava. `already_existed` sozinho
    # nao serve — e' um booleano REPORTADO PELO TRANSPORTE, e nao distingue "instancia viva" de
    # "instancia que ja' rodou e terminou"; o proprio docstring de `StartOutcome` registra que
    # e' por isso que o campo tipado existe.
    start_desfecho = _start_desfecho_de(instance.start_outcome)
    if start_desfecho != START_DESFECHO_NOVO:
        # ITEM 3 DO DIRETOR ("toda recusa/nao-start deixa rastro"). Antes deste log um
        # nao-start SEM erro era completamente silencioso: nenhuma excecao, nenhum contador,
        # nenhum campo — foi assim que 27 escalonamentos reusados atravessaram uma bateria
        # inteira sem aparecer. `business_key` e' a chave idempotente que o proprio engine ja
        # ve; nada aqui e' texto de beneficiario.
        logger.warning(
            "helena_escalonamento_nao_aberto",
            node="_start_escalation",
            start_desfecho=start_desfecho,
            process_key=PROCESS_KEY,
            business_key=business_key,
            instance_id=instance.instance_id,
            engine_state=instance.state,
            motivo_categoria=motivo,
        )
    saida_ok: dict[str, Any] = {
        "escalation_started": True,
        "start_desfecho": start_desfecho,
        "escalation_motivo": motivo,
        "escalation_severidade": severidade,
        "escalation_business_key": business_key,
        "escalation_process_ref": {
            "instance_id": instance.instance_id,
            "state": instance.state,
            "already_existed": instance.already_existed,
            # RODADA 10: de que TIPO e' o caso que ja' estava aberto (so' o token do motivo): o aviso de
            # caso aberto diz isso em termos humanos ("seu pedido sobre cobranca...").
            "motivo_do_caso": motivo_do_caso,
            # O veredito do chokepoint, verbatim, ao lado do booleano que ele corrige — quem
            # le' o handoff no painel precisa saber se a instancia citada nasceu AGORA.
            "start_outcome": str(getattr(instance.start_outcome, "value", instance.start_outcome)),
        },
        "response_text": response_text,
        "response_kind": response_kind,
    }
    if error:
        # A recusa de saida (o unico chamador que informa `error` hoje) precisa sobreviver no
        # estado do turno: sem ela o desfecho seria indistinguivel de um escalonamento clinico
        # comum, e a linha de log seria a unica testemunha de que a Helena foi barrada.
        saida_ok["error"] = error
    return saida_ok


def _start_failure_outcome(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """CC-01: o desfecho + o alerta do turno em que a escalacao NAO pode ser aberta.

    O texto que acompanha (`RESPOSTA_FALHA_TECNICA_START`) e uma CONSTANTE, nao um draft de
    LLM: um modelo, pedido para "explicar uma falha tecnica", volta a prometer um atendente
    com facilidade — e a promessa e exatamente o que nao pode existir aqui. Ele substitui o
    handoff que `_start_escalation` ja havia redigido ANTES de tentar o start.

    O desfecho e o alerta vem do helper compartilhado (uma definicao para os 9 agentes).

    ITEM 2 DA REVISAO DE 21/09/2026 — "ele parece cobrir so' uma forma de falha". Cobre, e a
    auditoria abaixo e' a razao de estar CERTO assim. Este ramo e' alcancado por UM caminho:
    `start_failed=True`, escrito por EXATAMENTE um `except CibSevenError` (`_start_escalation`).
    O que mais `start_process_idempotent` pode levantar, e por que nao entra aqui:

      * `AuditPersistenceError`, `StartVariableRedactionError`, `StartClaimWithoutInstanceError`
        e `StartDedupGateUnavailableError` NAO sao `CibSevenError`, e cada uma declara no
        proprio docstring que isso e' DELIBERADO: um scrub de PHI que nao roda, um audit que
        nao persiste ou um portao de dedup nao avaliavel nao sao indisponibilidade de
        fornecedor — sao defeito do proprio controle. Elas PROPAGAM e derrubam o turno alto,
        que e' a postura correta, e nunca degradam para "uma falha tecnica a mais".
      * `CibSevenStartAuthorizationError` E' subclasse de `CibSevenError` (D7-A) e portanto
        CAI aqui, como toda indisponibilidade/recusa do engine no momento do start.
      * A RECUSA DE CONTRATO DO WORKER (`tools/workers/escalation.py::_exigir_severidade`, a
        hipotese do relatorio) nao pode chegar a este `except` por construcao, e nao por
        sorte: o worker e' uma EXTERNAL TASK que o engine entrega DEPOIS de a instancia
        existir, num processo separado. Se ele recusar, a instancia ja' nasceu — o start
        SUCEDEU. A prova esta' em `test_helena_cerca_texto_x_fato.py`
        (`test_falha_nao_cibseven_no_start_propaga_em_vez_de_virar_falha_tecnica` e o teste
        irmao que fixa o caminho da subclasse).

    E o C1 da bateria NAO era esta classe de falha: nada falhou la'. O start foi idempotente
    sobre uma instancia viva — silencio sem excecao —, que e' exatamente o que
    `start_desfecho`/`_humano_acionado` passaram a tornar visivel.

    F1 (VERIFY-CC09): `HelenaState` nao tem chave `route` (usa `response_kind`), entao o
    `state.get("route")` generico do helper devolveria `None` — passamos
    `route=RESPONSE_KIND_FALHA_TECNICA_START` explicitamente, o literal que
    `_ROUTE_VOCAB["helena"]` ja declara para este caso.
    """
    return emit_start_failure_notice(
        dict(state),
        agent_id="helena",
        process_key=PROCESS_KEY,
        route=RESPONSE_KIND_FALHA_TECNICA_START,
        extra={
            "response_text": RESPOSTA_FALHA_TECNICA_START,
            "response_kind": RESPONSE_KIND_FALHA_TECNICA_START,
            "escalation_started": False,
        },
    )


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
from maezo.agents.helena.graph import (  # noqa: E402
    _CITA_UMA_PESSOA,
    _ESCOLHA_DE_OPCAO,
    _MOTIVOS_CLINICOS_COM_CHAVE_PROPRIA,
    _PEDE_CONTATO_DA_EQUIPE,
    _RESPOSTA_NAO,
    _RESPOSTA_SIM,
    _SIM_A_OFERTA,
    NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO,
    NOTA_CASO_CLINICO_PARALELO,
    PROCESS_KEY,
    RESPONSE_KIND_FALHA_TECNICA_START,
    RESPOSTA_CASO_ENCERRADO,
    RESPOSTA_CASO_GRAVE_NAO_ENCERRA,
    RESPOSTA_ENCERRAMENTO_FALHOU,
    RESPOSTA_ENCERRAMENTO_MANTIDO,
    RESPOSTA_FALHA_TECNICA_START,
    RESPOSTA_HANDOFF_CONTATO_DA_EQUIPE,
    RESPOSTA_SEM_CASO_PARA_ENCERRAR,
    RESULTADO_ENCERRADO_A_PEDIDO,
    RESUMO_IDENTIDADE_NAO_VERIFICADA,
    RESUMO_REVOGACAO_PENDENTE,
    START_DESFECHO_FALHOU,
    START_DESFECHO_JA_ATIVO,
    START_DESFECHO_NOVO,
    AgentDecisionProvenance,
    CibSevenError,
    HelenaState,
    MotivoCategoria,
    ProcessInstance,
    ResponseKind,
    RespostaRecusadaError,
    Severidade,
    _ameaca_regulatoria,
    _business_key,
    _caso_clinico_cobre_o_motivo,
    _conteudo_clinico,
    _mensagem_de_saude,
    _paciente_da_conversa,
    _pedido_explicito_com_verbo,
    _pedido_explicito_de_pessoa,
    _plano,
    _sinal_forte_de_emergencia,
    _sinal_fraco_de_emergencia,
    _start_desfecho_de,
    _texto_de_escalonamento,
    _texto_de_handoff,
    caso_grave_para_encerrar,
    correlate_message_audited,
    logger,
    start_process_idempotent,
)
