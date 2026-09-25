"""Adocao de telemetria de desfecho de turno nos nos terminais dos grafos (CC-09, audit 2026-09-04).

O DEFEITO QUE ESTE MODULO FECHA
--------------------------------
`spec/agents/*/agent.yaml` declara KPIs de desfecho/rota — `resolution_rate: track`,
`escalation_precision: track`, `false_denial_rate: ==0`, `human_routing_precision: track` etc. —
mas ate CC-09 NENHUM grafo de agente emitia telemetria de desfecho por turno
(`grep -rln 'record_' src/maezo/agents/*/graph.py` devolvia vazio). `record_worker_task_outcome`
(tools/workers/harness.py) conta o desfecho do ENGINE por external task; `record_agent_turn`
(platform/observability.py) conta a FORMA da mensagem. Nenhum dos dois diz se o caso foi
auto-roteado ou escalado, por que motivo, ou se a mensagem realmente saiu. Um KPI sem emissor nao
e' "ainda nao atingido" — e' inaferivel, e um inaferivel e indistinguivel de um KPI que ninguem
olha.

O PADRAO (uma definicao, adotada por todos os 10 grafos — nunca 10 copias)
---------------------------------------------------------------------------
:func:`emit_turn_desfecho` e' a UNICA funcao que os grafos chamam. Ela:

1. Extrai `desfecho`/`route`/`motivo_categoria`/`mensagem_enviada`/`start_failed` do estado
   MERGEADO que o no terminal recebe (langgraph ja aplicou os `dict` parciais dos nos anteriores
   antes de invocar o no terminal — por isso `finalize`/`complete` conseguem ler campos que
   `assess`/`human_review`/`escalate` escreveram, mesmo sendo `return {}` eles mesmos).
2. Aceita OVERRIDES posicionais por keyword (`desfecho=`, `route=`, `motivo_categoria=`,
   `enviada=`) para os poucos nos cujo valor final ainda NAO esta em `state` no momento da
   chamada — o proprio `return` do no que esta prestes a devolver (Fernando's `notify`, cujo
   `desfecho`/`mensagem_enviada` sao locais ate o `return`; Helena's `respond`, cujo desfecho e'
   DERIVADO de `response_kind`/`escalation_started` porque `HelenaState.desfecho` e' um campo
   morto — ver a nota HELENA abaixo).
3. VALIDA cada valor contra o vocabulario FECHADO do agente (`_DESFECHO_VOCAB`/`_ROUTE_VOCAB`/
   `_MOTIVO_CATEGORIA_VOCAB` abaixo) e normaliza qualquer valor fora do conjunto para `"outro"`
   — nunca o valor cru chega a um label do Prometheus. Isto cobre DOIS riscos com a MESMA
   resposta: (a) cardinalidade (um branch novo que esqueceu de declarar seu literal aqui) e (b)
   PHI-like (um valor derivado de texto livre por engano chegando a um label) — mesma disciplina
   de `record_worker_task_outcome`/`record_phi_business_key_mint`
   (`platform/observability.py`).
4. Chama `observability.record_agent_desfecho` — NUNCA levanta: um defeito de telemetria nao
   pode derrubar um turno que ja terminou (mesma postura de `record_agent_turn`/
   `notify_start_failure`).

NOTA HELENA (campo morto FECHADO — NEW-10, §Delta-F3)
-----------------------------------------------------------------------
`HelenaState.desfecho` existe no TypedDict (`graph.py:245`) e e' inicializado como `""`
(`graph.py:299`). Ate 2026-09-05 NENHUM no de `helena/graph.py` escrevia nele fora do ramo de
falha de start; desde entao `respond` GRAVA o mesmo valor em `saida["desfecho"]` em AMBOS os
ramos (sucesso E falha) — deixou de ser campo morto. O LABEL da telemetria continua DERIVADO
aqui, via override de `escalation_started`/`response_kind`/`escalation_motivo` — os sinais reais
desta agente — e NUNCA lido de volta de `state.get("desfecho")`: o valor deste modulo e o valor
gravado no estado sao a MESMA computacao feita uma vez, nunca uma leitura do outro (ver
`helena/graph.py::respond` para a narracao completa e a razao de nao ler de volta).

DISCIPLINA C3 (telemetria nao decide nada)
--------------------------------------------
Nenhuma linha deste modulo influencia uma aresta, um valor de estado devolvido ao grafo, ou um
efeito (WhatsApp, engine, DMN). A normalizacao para `"outro"` e' PURAMENTE do lado do LABEL do
Prometheus — o `desfecho`/`route` que o proprio no ja decidiu e devolve ao grafo segue intocado.

CHOKEPOINT DE FALHA DE START
------------------------------
O turno de falha tecnica de start (`start_failed=True`, `desfecho="erro_inicio_processo"`) NAO e'
emitido por este modulo diretamente pelos grafos — e' emitido UMA UNICA VEZ dentro de
`runtime.start_outcome.notify_start_failure`, que chama `emit_turn_desfecho` no seu proprio
corpo. Isso cobre os 9 agentes com `start_process` (todos exceto Beatriz) sem duplicar a chamada
em cada `graph.py::notify_start_failure` (que so' delega ao helper compartilhado, CC-01).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import structlog

from maezo.platform import observability

logger = structlog.get_logger(__name__)

#: Sentinela interna: distingue "o chamador nao passou este campo" (le do `state`) de "o
#: chamador passou `None` de proposito" (um valor legitimo — ex.: `route` no estado inicial de
#: Rafael e' `None`). Nunca escapa deste modulo.
_UNSET: Final[object] = object()

#: Token de normalizacao para qualquer valor fora do vocabulario fechado do agente. Nunca o
#: valor cru chega a um label do Prometheus — ver a nota de cardinalidade/PHI no docstring do
#: modulo e em `observability.record_agent_desfecho`.
_FALLBACK_TOKEN: Final[str] = "outro"

#: Desfecho tecnico compartilhado (CC-01) — mesma constante que
#: `runtime.start_outcome.DESFECHO_ERRO_INICIO_PROCESSO` declara; repetida aqui como STRING
#: LITERAL (nao importada) para que este modulo, que `start_outcome` importa, nunca precise
#: importar de volta `start_outcome` (evita um ciclo). As duas devem permanecer iguais — um
#: teste de fence (`test_terminal_nodes_emit_desfecho.py`) prova a igualdade.
_DESFECHO_ERRO_INICIO_PROCESSO: Final[str] = "erro_inicio_processo"

# ---------------------------------------------------------------------------------------------
# Vocabularios FECHADOS por agente (CC-09). Cada conjunto e' a enumeracao de TODO literal que o
# `graph.py` do agente de fato atribui a este campo — reproduzido por
#   grep -oE '"desfecho":\s*"[a-zA-Z0-9_]+"|return\s*"[a-zA-Z0-9_]+"' src/maezo/agents/<agente>/graph.py
# (para `desfecho`) e pelo `Route`/`MotivoCategoria` Literal de cada `graph.py` (para
# `route`/`motivo_categoria`). Um valor fora do conjunto e' normalizado para `_FALLBACK_TOKEN`
# (ver `_normalize`) — nunca chega cru ao label.
# ---------------------------------------------------------------------------------------------

_DESFECHO_VOCAB: Final[dict[str, frozenset[str]]] = {
    "andre": frozenset(
        {
            "analytics_pronto",
            "dossie_pronto_clerical",
            "dossie_remediacao_humano",
            "egresso_bloqueado",
            "dossie_aprovacao_humana",
            "pagamento_pendente_dados",
            _DESFECHO_ERRO_INICIO_PROCESSO,
        }
    ),
    "beatriz": frozenset({"dossie_instruido", "instrucao_incompleta"}),  # nunca abre processo
    "carolina": frozenset(
        {
            "analise_humana",
            "documentacao_pendente",
            "credenciamento_clerical",
            "analise_descredenciamento",
            "analise_credenciamento",
            _DESFECHO_ERRO_INICIO_PROCESSO,
        }
    ),
    # FERNANDO: os seis rotulos do caminho `notify` sao DUAS jornadas x TRES estados de entrega
    # (auditoria de frota 2026-09-04, FER-03/FER-04). Ate essa data so' existiam os dois de
    # SUCESSO, e `notify` os emitia mesmo quando o envio falhava ou o canal nao tinha remetente
    # nenhum — o `enviada=False` que este mesmo modulo ja carregava ao lado tornava a contradicao
    # visivel sem corrigi-la. Fonte dos literais: `agents/fernando/graph.py::
    # {_DESFECHO_NOTIFICACAO_PREVIA,_DESFECHO_LEMBRETE}` (cerca:
    # `tests/unit/agents/test_fernando_input_allowlist_fence.py::
    # test_notify_desfecho_tables_are_declared_in_the_closed_vocabulary`).
    "fernando": frozenset(
        {
            "notificacao_previa_enviada",
            "notificacao_previa_nao_enviada",
            "notificacao_previa_canal_sem_entrega",
            "lembrete_regularizacao_enviado",
            "lembrete_regularizacao_nao_enviado",
            "lembrete_regularizacao_canal_sem_entrega",
            "encaminhado_analise_humana",
            _DESFECHO_ERRO_INICIO_PROCESSO,
        }
    ),
    "gustavo": frozenset(
        {
            "envio_encaminhado_revisao_humana",
            "nip_encaminhada_instrucao_humana",
            _DESFECHO_ERRO_INICIO_PROCESSO,
        }
    ),
    # HELENA: `HelenaState.desfecho` NAO e' mais campo morto (ver NOTA HELENA no docstring do
    # modulo, §Delta-F3) — `respond` GRAVA o mesmo `desfecho` DERIVADO via override, um destes
    # quatro tokens, tanto no label da telemetria quanto de volta no `dict` que o no devolve.
    "helena": frozenset(
        {
            "resolvido_automatico",
            "escalado_humano",
            _DESFECHO_ERRO_INICIO_PROCESSO,
            # HEL-07: o rascunho de resposta voltou VAZIO e Helena NAO enviou nada. Precisa de
            # token proprio: sem ele o valor cairia na normalizacao `"outro"` e o turno ficaria
            # indistinguivel de qualquer outro desvio — que e' justamente o que este achado
            # existe para tornar visivel (`agents/helena/graph.py::DESFECHO_RESPOSTA_VAZIA`).
            "resposta_vazia_nao_enviada",
            # COLETA (passo 4, 09/09/2026): o turno terminou numa PERGUNTA ao beneficiario — nem
            # resolvido nem escalado. Token proprio para nao inflar `resolvido_automatico` com
            # perguntas. Literal duplicado de `agents/helena/graph.py::DESFECHO_PERGUNTA_COLETA`
            # (este modulo nao importa grafos); `test_helena_coleta.py` impede a divergencia.
            "pergunta_coleta",
            # CERCA TEXTO x FATO (21/09/2026, F1): o start foi idempotente sobre uma instancia
            # VIVA desta conversa — nenhuma escalacao NOVA nasceu e um humano ja' esta' com o
            # caso. Token proprio porque contado como `escalado_humano` este turno inflaria a taxa
            # de escalonamento com fila que ele nao criou; foi exatamente essa invisibilidade que
            # deixou 27 escalonamentos reusados atravessarem a bateria de 21/09 sem aparecer em
            # numero nenhum. Literal duplicado de
            # `agents/helena/graph.py::DESFECHO_ESCALONAMENTO_JA_ABERTO` (este modulo nao importa
            # grafos); `test_helena_cerca_texto_x_fato.py` impede a divergencia.
            "escalonamento_ja_aberto",
            # GAP-XHITL-4 — o turno de RETOMADA (porta `resume`), depois que um humano devolveu o
            # caso. Quatro tokens porque sao quatro fatos distintos: a instrucao chegou ao
            # beneficiario; a cerca a barrou (foi o placeholder de recusa); nao havia instrucao;
            # o envio falhou (o evento volta). Literais duplicados de
            # `agents/helena/graph.py::DESFECHO_RETOMADA_*`; `test_helena_retomada.py` impede a
            # divergencia.
            "retomada_enviada",
            "retomada_recusada",
            "retomada_sem_instrucoes",
            "retomada_falha_envio",
            # Decisao do dono (25/09/2026): fora da janela de 24h da Meta nada e' enviado, a
            # equipe e' avisada; emitido por `platform/integrations/agent_resume.py`.
            "retomada_fora_da_janela",
        }
    ),
    "lucas": frozenset(
        {
            "resposta_informativa_enviada",
            "lembrete_enviado",
            # §Delta W4-HYGIENE F1b: o envio informativo foi SUPRIMIDO pelo guard duravel de
            # idempotencia (LUC-08) — nao houve entrega neste turno, entao ele nao pode ser
            # contado sob um token terminado em "enviada". Declarado aqui, e nao normalizado
            # para `outro`, porque a taxa de supressao e' justamente o sinal que um operador
            # precisa ver. Literal duplicado de `agents/lucas/graph.py::
            # DESFECHO_ENVIO_SUPRIMIDO_DUPLICATA` (nunca importado — este modulo nao importa
            # grafos); o teste `test_lucas.py::
            # test_o_desfecho_de_envio_suprimido_esta_no_vocabulario_de_telemetria` e' o que
            # impede as duas copias de divergirem. Deliberadamente FORA do numerador de
            # `maezo_lucas_resolution_rate` (`deploy/observability/alert-rules.yml`): uma
            # supressao nao e' uma resolucao.
            "envio_suprimido_duplicata",
            "escalado_humano",
            # CERCA DE SAIDA (18/09/2026): o rascunho do modelo foi BARRADO antes de sair e o
            # beneficiario recebeu a constante segura. Declarado, e nao normalizado para `outro`,
            # porque a taxa de recusa e' a medida de quanto a proibicao no prompt valeria sozinha
            # — e some do radar se ninguem a contar. Literal duplicado de
            # `agents/lucas/graph.py::DESFECHO_RESPOSTA_RECUSADA` (este modulo nao importa
            # grafos); o teste `test_lucas.py::
            # test_o_desfecho_de_recusa_esta_no_vocabulario_de_telemetria` impede a divergencia.
            "resposta_recusada_na_saida",
            _DESFECHO_ERRO_INICIO_PROCESSO,
        }
    ),
    "marina": frozenset(
        {
            "pagar_integral",
            "recurso_segue_analise",
            "reembolso_dossie_humano",
            "recurso_humano",
            "triagem_humana",
            _DESFECHO_ERRO_INICIO_PROCESSO,
        }
    ),
    "rafael": frozenset(
        {
            "aprovacao_automatica_solicitada",
            "encaminhado_auditor",
            "nao_requer",
            _DESFECHO_ERRO_INICIO_PROCESSO,
        }
    ),
    "valentina": frozenset(
        {
            "sem_consentimento",
            # VAL-05 (fleet audit ciclo 2): distinct from a genuine no-consent verdict — reached
            # only via `receive`'s missing-context guard (inability to VERIFY consent, never a
            # verdict). Without this entry the label would silently normalize to "outro".
            "falha_verificacao_consentimento",
            "interrompido_revogacao",
            "analise_humana_clinica",
            "enrollment_realizado",
            "nao_elegivel",
            _DESFECHO_ERRO_INICIO_PROCESSO,
        }
    ),
}

#: `route` por agente. Vazio == o agente nao tem campo `route` no seu estado (Beatriz: grafo
#: linear, sem `add_conditional_edges` — L0 estrutural, `zero_auto_accusation`); nesse caso
#: `_normalize` deixa o valor passar sem checagem (sera' sempre `None`, ver `emit_turn_desfecho`).
_ROUTE_VOCAB: Final[dict[str, frozenset[str]]] = {
    "andre": frozenset({"auto_route", "human_review"}),
    "beatriz": frozenset(),
    "carolina": frozenset({"auto_route", "human_review"}),
    "fernando": frozenset({"notify", "escalate"}),
    "gustavo": frozenset({"review_submission", "instruct_nip"}),
    # HELENA nao tem `route` no estado — o call site passa `response_kind` (`ResponseKindOut`)
    # no lugar, via override; o mesmo campo do label cobre os dois conceitos (rota terminal).
    # `collect` entrou em 21/09/2026: o conjunto tinha QUATRO dos CINCO valores do `Literal`, e
    # todo turno de coleta caia em `_normalize` -> `"outro"` emitindo
    # `agent_desfecho_label_out_of_vocab`. E' a mesma classe de defeito que o `desfecho` da mesma
    # agente teve de consertar um campo ao lado (`escalonamento_ja_aberto`): um valor fora do
    # vocabulario apaga exatamente a distincao que o campo existe para criar — aqui, a rota que
    # PERGUNTA ficava indistinguivel de qualquer valor desconhecido. A cerca que impede a
    # reincidencia compara com o proprio `Literal`
    # (`test_helena_adv_extracao_e_wiring.py`), nao com uma lista copiada.
    "helena": frozenset({"inform", "schedule", "escalate", "collect", "falha_tecnica_start", "retomada"}),
    "lucas": frozenset({"respond_member", "escalate_human"}),
    "marina": frozenset({"auto_route", "human_review"}),
    "rafael": frozenset({"auto_approve", "human_auditor"}),
    "valentina": frozenset({"auto_route", "human_review"}),
}

#: `motivo_categoria` por agente. So' Fernando/Lucas/Helena tem este campo no contrato
#: (`escalation_precision`); os outros 7 agentes passam sempre `None` (o label renderiza `""`) —
#: `human_routing_precision` neles e' medido so' por `route`, sem uma categoria mais fina.
_MOTIVO_CATEGORIA_VOCAB: Final[dict[str, frozenset[str]]] = {
    "fernando": frozenset({"inadimplencia", "rescisao", "dmn_unavailable", "ambiguity", "falha_tecnica"}),
    "helena": frozenset(
        {
            "red_flag_clinico",
            "risco_psicossocial",
            "intencao_clinica",
            "solicitacao_humano",
            "falha_tecnica",
            "outro",
        }
    ),
    "lucas": frozenset({"outro", "solicitacao_humano", "falha_tecnica"}),
}


def _normalize(value: str | None, vocab: frozenset[str] | None, *, agent_id: str, field: str) -> str | None:
    """Valida `value` contra o vocabulario fechado do agente; normaliza fora-do-conjunto.

    `None` sempre passa — o campo nao se aplica a este agente/turno, e `None` nunca vira
    `"outro"` (isso inflaria a serie `motivo_categoria="outro"` com todo agente que nao tem essa
    dimensao). Um `vocab` vazio/ausente TAMBEM significa "sem conjunto para validar contra"
    (ex.: `route` em Beatriz) — passa o valor (que sera' sempre `None` na pratica, ja que nenhum
    call site de um agente sem vocab preenche este campo). Um valor presente cujo agente TEM
    vocabulario declarado mas que nao pertence a ele e' normalizado para `_FALLBACK_TOKEN` e um
    WARNING estruturado preserva agente+campo — NUNCA o valor — para deteccao operacional de um
    branch novo que esqueceu de estender o conjunto aqui.
    """
    if value is None:
        return None
    if not vocab:
        return value
    if value in vocab:
        return value
    logger.warning("agent_desfecho_label_out_of_vocab", agent_id=agent_id, field=field)
    return _FALLBACK_TOKEN


def emit_turn_desfecho(
    state: Mapping[str, Any],
    *,
    agent_id: str,
    flow: str | None = None,
    desfecho: Any = _UNSET,
    route: Any = _UNSET,
    motivo_categoria: Any = _UNSET,
    enviada: Any = _UNSET,
) -> None:
    """Emite UM `maezo_agent_desfecho_total` para o turno que acabou de terminar (CC-09).

    Chamado do NO TERMINAL de cada grafo (`complete`/`finalize`/`respond`/os dois gates LGPD de
    Valentina) e do UNICO site de falha de start (`runtime.start_outcome.notify_start_failure`).
    NUNCA chamado diretamente de `observability.record_agent_desfecho` — este e' o unico
    adaptador entre o `state` (forma varia por agente) e o contrato fixo daquela funcao.

    EXTRACAO. Por padrao le `state.get("desfecho")` / `state.get("route")` /
    `state.get("motivo_categoria")` / `state.get("mensagem_enviada")` — o `state` que um no
    terminal recebe ja e' o MERGE de tudo que os nos anteriores devolveram (langgraph aplica os
    `dict` parciais antes de invocar o proximo no), entao um `finalize`/`complete` que so' faz
    `return {}` ainda ve o `desfecho`/`route` que `assess`/`human_review`/`escalate` gravaram.
    Os quatro parametros de override (`desfecho=`/`route=`/`motivo_categoria=`/`enviada=`)
    existem para os poucos nos cujo valor final ainda NAO esta em `state` neste ponto — o
    proprio `return` que o no esta prestes a devolver (ver o docstring do modulo, notas
    FERNANDO/HELENA). `start_failed` NUNCA e' um override: e' sempre lido de
    `state.get("start_failed") is True`, porque o marcador (CC-01,
    `start_outcome.STATE_KEY_START_FAILED`) e' escrito por EXATAMENTE um caminho de codigo.

    NORMALIZACAO. Cada valor extraido passa por `_normalize` contra o vocabulario fechado do
    agente antes de alcancar `observability.record_agent_desfecho` — ver o docstring do modulo.

    FAIL-SAFE, MAS SO' CONTRA A FALHA DE METRICA (REG-03). Uma falha do registro Prometheus
    (`.labels()` com nomes/quantidade errados, serie duplicada — sempre `ValueError`) NAO derruba
    um turno que ja terminou: e' capturada e registrada em WARNING com o token de classe. Um BUG
    deste modulo, porem, PROPAGA — `AttributeError` de um `state` que nao e' `Mapping`,
    `TypeError` de uma assinatura derivada, `KeyError` de um contrato de estado violado. A postura
    anterior (`except Exception:` + `logger.debug`) nao distinguia os dois e podia deixar o
    contador `maezo_agent_desfecho_total` parar de emitir na frota inteira sem sinal nenhum.
    """
    try:
        raw_desfecho = state.get("desfecho") if desfecho is _UNSET else desfecho
        raw_route = state.get("route") if route is _UNSET else route
        raw_motivo = state.get("motivo_categoria") if motivo_categoria is _UNSET else motivo_categoria
        raw_enviada = state.get("mensagem_enviada") if enviada is _UNSET else enviada
        start_failed = state.get("start_failed") is True

        final_desfecho = _normalize(
            raw_desfecho if raw_desfecho else "",
            _DESFECHO_VOCAB.get(agent_id),
            agent_id=agent_id,
            field="desfecho",
        )
        final_route = _normalize(raw_route, _ROUTE_VOCAB.get(agent_id), agent_id=agent_id, field="route")
        final_motivo = _normalize(
            raw_motivo, _MOTIVO_CATEGORIA_VOCAB.get(agent_id), agent_id=agent_id, field="motivo_categoria"
        )

        observability.record_agent_desfecho(
            agent_id=agent_id,
            desfecho=final_desfecho or "",
            route=final_route,
            motivo_categoria=final_motivo,
            enviada=bool(raw_enviada) if raw_enviada is not None else None,
            start_failed=start_failed,
            flow=flow,
        )
    except ValueError as exc:
        # REG-03 (audit 2026-09-04). Antes: `except Exception:` + `logger.debug(...)`. Duas coisas
        # erradas numa linha so'. (a) LARGURA — o corpo do `try` le `state` por `.get` e chama
        # `_normalize`/`record_agent_desfecho`; um `AttributeError` de um chamador que passou algo
        # que nao e' `Mapping`, ou um `TypeError` de uma assinatura derivada, sao BUGS deste
        # modulo, e eram absorvidos exatamente como uma falha de metrica (reproduzido LIVE na base
        # 87b51a8: `emit_turn_desfecho(objeto_sem_get, agent_id="carolina")` retornou em silencio).
        # A falha EXTERNA real e' uma so': o `prometheus_client` levanta `ValueError` para uso
        # indevido do registro (`.labels()` com nomes/quantidade errados, serie duplicada).
        # (b) NIVEL — em DEBUG (que nenhum ambiente liga por padrao) o contador CC-09 podia parar
        # de emitir NA FROTA INTEIRA sem sinal operacional nenhum, que e' precisamente o KPI
        # inaferivel que CC-09 foi aberto para fechar. WARNING, com o token de CLASSE (nunca o
        # texto do erro, que pode ecoar um label).
        logger.warning(
            "agent_desfecho_telemetry_emit_failed",
            agent_id=agent_id,
            erro=type(exc).__name__,
            exc_info=True,
        )
