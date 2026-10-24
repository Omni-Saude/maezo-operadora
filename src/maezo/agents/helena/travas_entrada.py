"""TRAVAS DE ENTRADA da Helena (ADR-0066 camada 1, ADR-0067 §5): regras fixas, sem modelo decidindo —
risco psicossocial pelo texto (CVV 188), alarme pela tabela DMN (ADR-0012) e o alarme deterministico que o
roteador consulta.

MOVIDO SEM MUDANCA de `graph.py` (Plano de ajustes da Helena v2, etapa 6, PR so' de mudanca de lugar): cada
metodo do `HelenaGraph` virou uma funcao deste modulo com o MESMO corpo (`self` e' o grafo) e a classe aponta
para ela. O comportamento muda so' no PR seguinte.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo LOGO ANTES da classe e este modulo importa os nomes
do graph NO FIM.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from maezo.agents.helena.alarme_deterministico import alarme_no_relato
from maezo.agents.helena.helena_v4 import e_agravamento, ler_pendente
from maezo.conhecimento import base_saude as _bs
from maezo.conhecimento import saude_gerativa as _sg
from maezo.runtime.error_text import dmn_unavailable_error
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnNoResultError, first_row

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph


async def _risco_pelo_texto(self: HelenaGraph, state: HelenaState) -> dict[str, Any] | None:
    """RISCO PSICOSSOCIAL PELO TEXTO, ANTES DO MODELO (pendencia 15, DL-0151).

    Termo de autolesao ou de nao querer viver na mensagem (`saude_gerativa.tem_risco`, lista do
    DL-0123, inalterada) escala `risco_psicossocial` SEM chamar o classificador: a falha do modelo nao
    rebaixa mais o turno a `falha_tecnica`. A severidade continua sendo a da tabela `mental_health`
    (mesmo bloco do gatilho 5 em `classify`; tabela fora do ar = `grave`) e nenhum `sintoma_codigo` e'
    inventado a partir do texto; o ROTEAMENTO e' a `r2` da `escalation_routing` (P1, plantao clinico,
    qualquer severidade). O texto e' o fixo `RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL` (CVV 188, 192), que
    `escalate` escolhe pelo motivo. Sem o termo devolve `None` e o turno segue como antes."""
    if not _sg.tem_risco(_plano(str(state.get("message_body") or ""))):
        return None
    logger.warning("helena_risco_psicossocial_pelo_texto", node="classify", antes_do_modelo=True)
    update: dict[str, Any] = {
        "intent": "symptom",
        "population": "mental_health",
        "psychosocial_risk": True,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "idade_anos": None,
        "idade_meses": None,
        "idade_gestacional_semanas": None,
    }
    self._expirar_pendencias_do_turno(state, update)
    # `risco_imediato` ausente = risco imediato (`_evaluate_dmn`, postura conservadora).
    dmn_out = await self._evaluate_dmn({"psychosocial_risk": True}, force_population="mental_health")
    update.update(dmn_out)
    update["next_kind"] = "escalate"
    update["escalation_motivo"] = "risco_psicossocial"
    if dmn_out.get("error"):
        update["escalation_severidade"] = "grave"
    else:
        prioridade = str((dmn_out.get("dmn_decision") or {}).get("prioridade", ""))
        update["escalation_severidade"] = _severidade_from_prioridade(prioridade)
    return update


async def _triar_red_flag(self: HelenaGraph, extraction: dict[str, Any], update: dict[str, Any]) -> bool:
    """Consulta a tabela de red flag da populacao e ESCREVE o veredito em `update`. Devolve
    `True` quando a tabela decidiu o roteamento do turno.

    EXTRAIDO DO RAMO `symptom` EM 22/09/2026 (CRITICO 3), sem mudar uma linha do que ele fazia:
    a triagem precisava acontecer ANTES dos gatilhos de INTENCAO, e duas copias da mesma
    sequencia (avaliar -> tratar tabela fora do ar -> marcar a avaliacao -> ler a bandeira)
    derivariam na primeira edicao que tocasse so' uma delas.

    DECIDE O ROTEAMENTO em dois casos, e os dois sao `escalate`:
      * a tabela nao respondeu (gatilho 4, `falha_tecnica`) — NUNCA lido como "sem bandeira"
        (fail-safe, ADR-0028 §3);
      * a tabela acusou bandeira (`red_flag` ou `conduta` `ESCALATE*`) — e ai a severidade sai
        da PRIORIDADE que ela emitiu, nunca de um literal.
    Fora esses dois, devolve `False` e quem chamou segue com os proprios gatilhos.
    """
    dmn_out = await self._evaluate_dmn(extraction)
    update.update(dmn_out)
    if dmn_out.get("error"):
        # Gatilho 4: DMN unavailable/no-result is a technical failure -> human, NEVER
        # treated as "no red flag" (fail-safe, ADR-0028 §3).
        update["next_kind"] = "escalate"
        update["escalation_motivo"] = "falha_tecnica"
        # HEL-04: AQUI ha' classificacao — o sintoma foi extraido e validado antes de a DMN
        # cair —, entao a severidade DERIVA dela em vez de ser um literal.
        update["escalation_severidade"] = _severidade_de_intensidade(update.get("intensidade"))
        return True
    # F5: a avaliacao ACONTECEU — so' AQUI, depois de a tabela responder, "houve avaliacao" e'
    # fato e nao intencao. A conversa passa a lembrar QUAL dado ela usou, que e' a unica
    # informacao que permite refaze-la se a pessoa corrigir esse dado
    # (`_correcao_de_dado_avaliado`). NAO e' lembrar o sintoma da mensagem anterior — ver a nota
    # em `_MEMORIA_SINTOMA_AVALIADO` para a diferenca e por que ela importa.
    update["memoria_clinica"] = _marcar_avaliacao(
        update.get("memoria_clinica"),
        sintoma_codigo=extraction.get("sintoma_codigo"),
        intensidade=update.get("intensidade"),
    )
    decision = dmn_out.get("dmn_decision", {})
    conduta = str(decision.get("conduta", "CONTINUE"))
    if decision.get("red_flag") is True or conduta.startswith("ESCALATE"):
        prioridade = str(decision.get("prioridade", "P2"))
        is_mental = dmn_out.get("dmn_table") == _DMN_BY_POPULATION["mental_health"]
        update["next_kind"] = "escalate"
        update["escalation_motivo"] = "risco_psicossocial" if is_mental else "red_flag_clinico"
        update["escalation_severidade"] = _severidade_from_prioridade(prioridade)
        return True
    return False


async def _evaluate_dmn(
    self: HelenaGraph,
    extraction: dict[str, Any],
    *,
    force_population: str | None = None,
) -> dict[str, Any]:
    population = force_population or extraction.get("population") or "adult"
    table = _DMN_BY_POPULATION.get(population, "triage_redflag_adult")

    dmn_input: dict[str, Any] = {
        "sintoma_codigo": extraction.get("sintoma_codigo"),
        "intensidade": extraction.get("intensidade", "desconhecida"),
    }
    if table == "triage_redflag_adult":
        dmn_input["idade_anos"] = extraction.get("idade_anos")
    elif table == "triage_redflag_pediatric":
        dmn_input["idade_meses"] = extraction.get("idade_meses")
    elif table == "triage_redflag_gestante":
        dmn_input["idade_gestacional_semanas"] = extraction.get("idade_gestacional_semanas")
    elif table == "triage_redflag_mental_health":
        risco = extraction.get("risco_imediato")
        # Conservative posture: immediate risk UNLESS the LLM says explicitly false. This
        # matches the DMN's fail-safe stance and the classify prompt's instruction, and fixes
        # a wrong coercion: the prior `bool(risco)` read the string `"false"` as `True`
        # (harmless here — it fails safe) but also read `""`/`0`/`[]` as `False` (fail-OPEN).
        # `_is_explicitly_false` yields True only for native `False`/`"false"`; everything
        # else (None, "true", unparseable) stays immediate-risk.
        dmn_input["risco_imediato"] = not _is_explicitly_false(risco)

    try:
        rows, version = await self._dmn.evaluate(table, dmn_input)
        row = first_row(rows, table, dmn_input)
    except (DmnEvaluationError, DmnNoResultError) as exc:
        # HEL-05 (feeder): the DMN-down path routes to escalate `falha_tecnica` in THIS turn,
        # so this message reaches `resumo_contexto` directly (`table` is a class token).
        return {
            "dmn_table": table,
            "dmn_decision": {},
            "error": dmn_unavailable_error(table, exc),
        }

    # Provenance reference: `{table}#{decision-definition id}` — the engine's evaluate
    # response does not return which RULE fired (no `ruleId` in the Camunda 7 REST evaluate
    # contract), so this cites the authoritative deployed decision-definition id/version
    # (ADR-0028 §2) rather than fabricating a rule id the engine never gave us.
    ref = f"{table}#{version.id}"
    return {"dmn_table": table, "dmn_decision": row, "dmn_decision_ref": ref}


def _roteador_alarme(
    self: HelenaGraph, texto: str, pendente: str | None, falas_anteriores: Sequence[str] = ()
) -> bool:
    plano = _plano(texto)
    if alarme_no_relato(plano) is not None or _sinal_forte_de_emergencia(texto):
        return True
    if not self._base_saude:
        return False
    anterior = ler_pendente(pendente)
    if (
        anterior is not None
        and anterior.grupo.startswith("sin-")
        and _bs.alarme_do_assunto(self._base, anterior.grupo, texto)
    ):
        return True
    if falas_anteriores and _bs.alarme_dos_citados_antes(self._base, falas_anteriores, texto):
        return True
    return _bs.alarme_no_texto(self._base, texto)


# --- O alarme DITO SO' PELO MODELO (contrato v7; decisao do dono de 23/10/2026, DL-0154) ---------------
#: O VOCABULARIO DA TABELA de alarme (os sinais das tabelas `triage_redflag_*`, da aba "3 Sinais de alarme"
#: e dos arquivos `sin-*`): o sinal que o modelo diz so' vale como "claro" se estiver aqui (ou nos lexicos de
#: sempre). "Vomito" sozinho NAO esta' (so' com sangue); febre de adulto sozinha tambem nao.
_VOCABULARIO_DA_TABELA: re.Pattern[str] = re.compile(
    r"\b(?:dor|aperto|pressao|queimacao) no peito\b|\bfalta de ar\b|\bdificuldade (?:para|pra|de) respirar\b"
    r"|\bnao (?:consegue|consigo) respirar\b|\bdesmai\w*|\bdesacordad\w*|\bconvuls\w*"
    r"|\b(?:vomit\w*|tosse|tossindo|escarr\w*)\b.{0,12}\bsangue\b|\bsangue no vomito\b"
    r"|\bsangramento (?:forte|intenso|muito|grande)\b|\bhemorragia\b"
    r"|\b(?:fraqueza|formigamento|dormencia|paralisi\w*)\b.{0,20}\b(?:lado|braco|perna|rosto)\b"
    r"|\b(?:face|rosto|boca)\b.{0,8}\b(?:caid[oa]|torta)\b|\bfala (?:enrolada|arrastada)\b|\bconfus\w*"
    r"|\b(?:molinh[oa]|sem reagir|nao reage|nao acorda|prostrad[oa])\b"
    r"|\bfebre\b.{0,30}\b(?:bebe|recem nascid\w*|\d+ (?:dias?|semanas?|mes(?:es)?)|idos[oa]|\d{2,3} anos)\b"
    r"|\b(?:bateu|batida|pancada|caiu)\b.{0,25}\bcabeca\b.{0,25}\b(?:bebe|nenem|recem nascid\w*|\d+ "
    r"mes(?:es)?)\b"
    r"|\b(?:bebe|nenem|recem nascid\w*|\d+ "
    r"mes(?:es)?)\b.{0,25}\b(?:bateu|batida|pancada|caiu)\b.{0,25}\bcabeca\b"
    r"|\bbolsa\b.{0,10}\b(?:rompeu|rompida|estourou|estourada)\b|\bsangr\w*\b.{0,25}\b(?:gravid\w*|gestac\w*)\b"
    r"|\b(?:inchac\w*|inchad\w*|inchando)\b.{0,20}\b(?:garganta|lingua|boca|rosto|labios?)\b"
    r"|\b(?:garganta|lingua|boca)\b.{0,15}\b(?:inchad\w*|inchando|fechando)\b"
    r"|\bpior dor de cabeca\b|\bdor de cabeca (?:subita|muito forte|explosiva)\b|\bpior (?:dor )?da "
    r"(?:minha )?vida\b"
    r"|\b(?:vontade de morrer|me matar|se matar|acabar com tudo|me machucar|se machucar|tirar a (?:minha "
    r")?vida)\b"
)


def _sinal_do_modelo_na_tabela(self: HelenaGraph, sinal: str) -> bool:
    """O sinal que o MODELO disse esta' no vocabulario da tabela de alarme? So' entao ele vale como "claro"
    (192 na hora); senao, a pergunta de alerta. O modelo pode ACRESCENTAR urgencia, nunca tirar: a tabela e
    os lexicos de sempre rodam antes, sobre a mensagem."""
    plano = _plano(sinal)
    if not plano:
        return False
    return bool(
        _VOCABULARIO_DA_TABELA.search(plano)
        or alarme_no_relato(plano) is not None
        or _sinal_forte_de_emergencia(sinal)
        or _sg.tem_risco(plano)
        or (self._base_saude and _bs.alarme_no_texto(self._base_hoje(), sinal))
    )


def _radicais(texto: str) -> set[str]:
    return {p[:5] for p in _plano(texto).split() if len(p) > 3}


def _sinal_do_modelo_e_novo(self: HelenaGraph, state: HelenaState, sinais: Sequence[str]) -> bool:
    """TRAVA "SEM SEGUNDO 192" para o sinal do modelo: algum sinal tem palavras que a pessoa ainda NAO
    tinha dito nesta conversa (pelos radicais)? O alarme ja' tratado, repetido ou comentado, nao e' novo.
    """
    falas = self._falas_da_pessoa(state)
    antes = set().union(*(_radicais(f) for f in falas)) if falas else set()
    return any(not (_radicais(s) & antes) for s in sinais)


def _urgencia_que_piorou(self: HelenaGraph, update: dict[str, Any], texto: str) -> dict[str, Any] | None:
    """Rodada de 24/10/2026: depois do 192, "piorou" / "nao consigo respirar" e' urgencia NOVA (nunca
    "mantive o seu caso"): o mesmo escalonamento do alarme (`red_flag_clinico` grave, 192 no texto)."""
    if not e_agravamento(_plano(texto)):
        return None
    logger.warning("helena_urgencia_piorou_depois_do_alarme", node="classify")
    update.update(
        {"intent": "symptom", "next_kind": "escalate", "escalation_motivo": "red_flag_clinico"}
        | {"escalation_severidade": "grave"}
    )
    return update


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
from maezo.agents.helena.graph import (  # noqa: E402
    _DMN_BY_POPULATION,
    HelenaState,
    _is_explicitly_false,
    _marcar_avaliacao,
    _plano,
    _severidade_de_intensidade,
    _severidade_from_prioridade,
    _sinal_forte_de_emergencia,
    logger,
)
