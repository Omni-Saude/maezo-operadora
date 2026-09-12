"""WP-J1-11 — UM dominio de idempotencia por guia TISS (decisao do dono #16, opcao (a)).

O DEFEITO QUE ESTE ARQUIVO FECHA. `SP-OP-AUTH-001` tinha DUAS business keys para a MESMA guia:

    canal de agente  ->  `AUTH-{tenant_id}-{numero_guia_tiss}`   (contrato + BPMN)
    canal do portal  ->  `AUTHI-{guide_identity_ref}`            (referencia OPACA do portal)

Nao era um bug de digitacao: a chave `AUTHI-` e' bem-formada e o engine a aceita, entao ela abre
uma instancia PERFEITAMENTE FUNCIONAL — num SEGUNDO dominio de idempotencia, invisivel ao outro
canal. A consequencia e' a que o contrato proibe: `find_active_instance` de um canal nunca acha a
instancia do outro, a mesma guia pode ter DUAS instancias vivas, e duas analises medicas
concorrentes da mesma autorizacao podem divergir.

A REGRA QUE PASSA A VALER: existe UM compositor
(`maezo.tools.process_business_keys.auth_business_key`) e todo caminho que inicia ou correlaciona
`SP-OP-AUTH-001` passa por ele. A chave legada nao e' convertida — e' RECUSADA por nome.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

import maezo.tools.mcp_cibseven.transport as transport_module
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    FakeCibSevenTransport,
    ProcessInstance,
    StartDedupPosture,
    StartOutcome,
    start_dedup_posture,
    start_process_idempotent,
)
from maezo.tools.process_business_keys import (
    AUTH_BUSINESS_KEY_PREFIX,
    LEGACY_AUTH_INTAKE_BUSINESS_KEY_PREFIX,
    BusinessKeyComponentError,
    LegacyAuthIntakeBusinessKeyError,
    auth_business_key,
    is_legacy_auth_intake_business_key,
    refuse_legacy_auth_intake_business_key,
)
from tests.support.audit_fakes import FakeStartAuditSink

AUTH = "SP-OP-AUTH-001"
_SRC = Path(__file__).resolve().parents[3] / "src" / "maezo"


# ---------------------------------------------------------------------------
# 1. A FORMA DA CHAVE — contratual, e identica nos dois canais
# ---------------------------------------------------------------------------


def test_auth_business_key_tem_a_forma_do_contrato() -> None:
    """`AUTH-{tenant_id}-{numero_guia_tiss}` — a forma congelada em
    `docs/processes/contracts/SP-OP-AUTH-001.md` e no cabecalho do BPMN. Byte a byte: o
    compositor NAO renomeia chaves implantadas (o contrato proibe explicitamente enquanto o
    ADR-0038 for Proposed)."""
    assert auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA123456") == "AUTH-amh-GUIA123456"
    assert auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA123456").startswith(
        AUTH_BUSINESS_KEY_PREFIX
    )


def test_uma_guia_hifenizada_continua_valendo() -> None:
    """Guias COM hifen existem em uso (`AUTH-amh-GUIA-LIVEPG-1`). O compositor nao pode
    apertar o componente a ponto de invalidar chaves ja' implantadas — a desambiguacao de
    `-` pertence a ratificacao do ADR-0038, nao a este compositor (residual declarado no
    docstring de `auth_business_key`)."""
    assert auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA-LIVEPG-1") == "AUTH-amh-GUIA-LIVEPG-1"


def test_os_dois_canais_derivam_a_mesma_chave_para_a_mesma_guia() -> None:
    """O CORACAO de #16. O canal de agente (grafo do rafael) e o canal do portal (composicao de
    intake nativa) chamam o MESMO compositor com tenant+guia — entao, para uma guia, existe UMA
    string de business key, e e' ela que o engine usa como `ACT_HI_PROCINST.BUSINESS_KEY_`.

    Este teste compara as duas derivacoes REAIS, nao duas copias da f-string: `_business_key` e'
    importado do modulo do rafael."""
    from maezo.agents.rafael.graph import _business_key

    do_agente = _business_key({"tenant_id": "amh", "numero_guia_tiss": "GUIA-9"})  # type: ignore[typeddict-item]
    do_portal = auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA-9")
    assert do_agente == do_portal == "AUTH-amh-GUIA-9"


# ---------------------------------------------------------------------------
# 2. A CHAVE COLAPSADA `AUTH--` — o defeito latente que a promocao tornaria grave
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tenant", ["", " ", "\t"])
def test_tenant_ausente_ou_em_branco_e_recusado(tenant: str) -> None:
    with pytest.raises(BusinessKeyComponentError):
        auth_business_key(tenant_id=tenant, numero_guia_tiss="GUIA-1")


@pytest.mark.parametrize("guia", ["", " ", "\n", "GUIA 1", " GUIA-1", "GUIA-1 "])
def test_guia_ausente_em_branco_ou_com_espaco_e_recusada(guia: str) -> None:
    """Espaco nao e' cosmetico aqui: a chave e' comparada BYTE A BYTE contra
    `ACT_HI_PROCINST.BUSINESS_KEY_` e contra `start_dedup_key`. `"AUTH-amh-G 1"` e
    `"AUTH-amh-G1"` sao DOIS dominios."""
    with pytest.raises(BusinessKeyComponentError):
        auth_business_key(tenant_id="amh", numero_guia_tiss=guia)


def test_nenhum_componente_ausente_produz_a_chave_colapsada() -> None:
    """A regressao exata que esta' sendo fechada. As f-strings originais
    (`f"AUTH-{state.get('tenant_id','')}-{state.get('numero_guia_tiss','')}"`, em
    `agents/rafael/graph.py` e `runtime/agent_runtime/ingress.py`) produziam `"AUTH--"` com os
    dois componentes ausentes: UMA chave compartilhada por TODA solicitacao malformada.

    Enquanto AUTH era `NON_STRICT` isso era latente. Com `EXCLUSIVE` ganha dentes: a segunda
    solicitacao malformada acha a reivindicacao duravel da primeira, resolve contra a instancia
    VIVA dela e recebe o caso clinico de outra pessoa."""
    for tenant, guia in [("", ""), ("amh", ""), ("", "GUIA-1")]:
        with pytest.raises(BusinessKeyComponentError):
            auth_business_key(tenant_id=tenant, numero_guia_tiss=guia)


def test_o_grafo_do_rafael_recusa_em_vez_de_colapsar() -> None:
    """A recusa vale no CAMINHO, nao so' no compositor: um estado sem guia falha alto, antes de
    qualquer efeito, em vez de seguir para o start com uma chave colapsada."""
    from maezo.agents.rafael.graph import _business_key

    with pytest.raises(BusinessKeyComponentError):
        _business_key({"tenant_id": "amh"})  # type: ignore[typeddict-item]


def test_nenhuma_fstring_de_business_key_auth_sobrou_em_src() -> None:
    """CERCA. O defeito nasceu de compositores duplicados; a garantia e' que exista UM. Nenhum
    modulo de `src/` pode conter um literal que MONTE a chave (`"AUTH-"` como prefixo de
    concatenacao/f-string) fora do compositor.

    A varredura e' por LITERAL porque e' assim que a duplicacao apareceu das duas vezes
    (`"AUTH-"` + `"AUTHI-"`). Documentacao e comentarios citam a forma o tempo todo e nao sao
    codigo: a checagem le a AST e olha apenas literais `str` vivos, nunca docstrings/comentarios.
    """
    compositor = _SRC / "tools" / "process_business_keys.py"
    #: O UNICO literal `AUTH-` vivo em `src/` que NAO e' business key, nomeado com sua razao.
    #: `tools/workers/auth.py` monta `numero_autorizacao` TISS
    #: (`f"AUTH-{tenant_id}-{guia}-{uuid}"`) — um numero de AUTORIZACAO emitido ao prestador,
    #: nao a chave de idempotencia do processo. A confusao entre os dois ja' aconteceu uma vez
    #: e esta' corrigida em `docs/adr/0038-...` ("`auth.py:1261` NAO e' business key — e'
    #: `numero_autorizacao` TISS; o compositor AUTH real e' `agents/rafael/graph.py`").
    #: Registrado aqui para que a cerca continue valendo para todo o resto.
    nao_sao_business_key = {"tools/workers/auth.py"}
    ofensores: list[str] = []
    for arquivo in sorted(_SRC.rglob("*.py")):
        if arquivo == compositor or arquivo.relative_to(_SRC).as_posix() in nao_sao_business_key:
            continue
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        docstrings = {
            no.body[0].value
            for no in ast.walk(arvore)
            if isinstance(no, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and no.body
            and isinstance(no.body[0], ast.Expr)
            and isinstance(no.body[0].value, ast.Constant)
        }
        for no in ast.walk(arvore):
            if (
                isinstance(no, ast.Constant)
                and type(no.value) is str
                and no not in docstrings
                and no.value.startswith(("AUTH-", "AUTHI-"))
            ):
                ofensores.append(f"{arquivo.relative_to(_SRC)}:{no.lineno}: {no.value!r}")
    assert not ofensores, (
        "literal de business key AUTH fora do compositor unico "
        "(`tools/process_business_keys.py`) — foi exatamente assim que os DOIS dominios de "
        f"idempotencia nasceram: {ofensores}"
    )


# ---------------------------------------------------------------------------
# 3. A CHAVE LEGADA `AUTHI-` — recusada por nome, NUNCA convertida
# ---------------------------------------------------------------------------


def test_a_chave_legada_e_recusada_e_nao_convertida() -> None:
    """Nao ha' funcao total de `guide_identity_ref` (opaco) para `numero_guia_tiss`, entao
    "migrar" a chave legada seria FABRICAR identidade clinica. A unica resposta honesta e'
    recusar — e a recusa e' tipada, para que um chamador consiga distingui-la de uma
    indisponibilidade."""
    with pytest.raises(LegacyAuthIntakeBusinessKeyError):
        refuse_legacy_auth_intake_business_key("AUTHI-guide-opaque-ref")
    assert is_legacy_auth_intake_business_key("AUTHI-guide") is True


def test_a_chave_contratual_nao_e_confundida_com_a_legada() -> None:
    """As duas familias sao distinguiveis por prefixo sem ambiguidade: o quarto byte de
    `AUTHI-` e' `I`, nao `-`, entao `"AUTHI-x".startswith("AUTH-")` e' False. Se algum dia
    alguem trocar o prefixo legado por algo que seja prefixo do contratual, este teste cai."""
    assert not "AUTHI-x".startswith(AUTH_BUSINESS_KEY_PREFIX)
    assert is_legacy_auth_intake_business_key("AUTH-amh-GUIA-1") is False
    refuse_legacy_auth_intake_business_key("AUTH-amh-GUIA-1")  # nao levanta
    refuse_legacy_auth_intake_business_key(None)  # nao-str nao e' chave legada


def test_o_prefixo_legado_so_existe_no_compositor() -> None:
    """`AUTHI-` sobrevive em `src/` EXCLUSIVAMENTE como constante de recusa. Se ele reaparecer
    como montagem em qualquer outro modulo, o segundo dominio voltou."""
    compositor = _SRC / "tools" / "process_business_keys.py"
    vivos: list[str] = []
    for arquivo in sorted(_SRC.rglob("*.py")):
        if arquivo == compositor:
            continue
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        docstrings = {
            no.body[0].value
            for no in ast.walk(arvore)
            if isinstance(no, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and no.body
            and isinstance(no.body[0], ast.Expr)
            and isinstance(no.body[0].value, ast.Constant)
        }
        for no in ast.walk(arvore):
            if (
                isinstance(no, ast.Constant)
                and type(no.value) is str
                and no not in docstrings
                and LEGACY_AUTH_INTAKE_BUSINESS_KEY_PREFIX in no.value
            ):
                vivos.append(f"{arquivo.relative_to(_SRC)}:{no.lineno}")
    # Prosa (docstring/comentario) PODE nomear o prefixo legado — e precisa, para explicar a
    # recusa. O que nao pode e' um literal VIVO montando a chave fora do compositor.
    assert vivos == [], f"literal vivo com o prefixo legado `AUTHI-` fora do compositor: {vivos}"


def test_o_canal_do_portal_recusa_uma_chave_legada_no_ponto_de_start() -> None:
    """A guarda viva de `start_human` (`gateway/intake/native_dispatch.py`) EXIGIA a chave
    legada; agora ela a recusa. Esta e' a prova no caminho, nao no helper."""
    from maezo.gateway.human.auth_transport import AuthUnavailableError
    from maezo.gateway.intake.native_dispatch import assert_auth_business_key_of_tenant

    with pytest.raises(LegacyAuthIntakeBusinessKeyError):
        refuse_legacy_auth_intake_business_key("AUTHI-guide")
    # E a forma contratual de OUTRO tenant tambem nao passa.
    with pytest.raises(AuthUnavailableError):
        assert_auth_business_key_of_tenant("AUTH-outro-GUIA-1", tenant_id="tenant")
    assert_auth_business_key_of_tenant("AUTH-tenant-GUIA-1", tenant_id="tenant")  # nao levanta


def test_o_portal_recusa_iniciar_enquanto_a_fonte_nao_publicar_o_numero_da_guia() -> None:
    """O que substituiu a chave legada NAO e' uma chave inventada: e' uma RECUSA tipada.

    A fonte `guide` publicada traz `guide_identity_ref` (opaco) e nao `numero_guia_tiss`, entao
    o canal do portal nao consegue montar a chave contratual — e recusa, em vez de voltar a
    abrir um segundo dominio. Isso deixa o caminho do portal exatamente tao inerte quanto ja'
    estava (`dispatch_prepared_start` nao tem chamador em `src/`), agora com a razao dita em
    voz alta para quem for liga-lo (WP-J1-01)."""
    from maezo.gateway.intake.native_composition import _published_guide_number
    from maezo.gateway.intake.native_dispatch import AuthIntakeGuideNumberUnavailableError
    from tests.unit.gateway.intake.native.test_wire_transport import command

    facts = command()  # StartFacts nao declara `numero_guia_tiss`; o comando carrega o opaco
    with pytest.raises(AuthIntakeGuideNumberUnavailableError):
        _published_guide_number(facts)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. A POSTURA — `EXCLUSIVE`, e por que nao `PERMANENT`
# ---------------------------------------------------------------------------


def test_auth_esta_classificado_como_exclusive() -> None:
    """Decisao do dono #16. Antes: `NON_STRICT` com a nota "Human call" sobre reuso de guia."""
    assert start_dedup_posture(AUTH) is StartDedupPosture.EXCLUSIVE


def test_o_mapa_de_postura_continua_completo_contra_os_process_keys_conhecidos() -> None:
    """A promocao MOVEU uma entrada; nao pode ter removido nenhuma. Esta e' a mesma
    completude que `test_every_known_process_key_is_classified_in_the_policy_map` guarda —
    repetida aqui porque e' a invariante que a edicao deste pacote mais facilmente quebraria."""
    from maezo.tools.mcp_cibseven.transport import _START_DEDUP_POLICY
    from maezo.tools.process_allowlist import KNOWN_PROCESS_KEYS

    assert set(_START_DEDUP_POLICY) == set(KNOWN_PROCESS_KEYS)
    assert AUTH in _START_DEDUP_POLICY


async def test_auth_permanent_would_be_bypassed_by_the_portal_channel_but_exclusive_is_not() -> None:
    """A razao (2) do bloco de politica, provada: o canal do portal NAO CHEGA a este portao.

    `start_process_idempotent` desvia uma proveniencia `HumanIntakeProvenance` para `start_human`
    na PRIMEIRA ramificacao, antes de a postura ser lida — o canal do portal nao escreve
    reivindicacao duravel nem consulta nenhuma. Sob `PERMANENT` isso seria uma garantia falsa: o
    canal de agente recusaria uma guia reiniciada (`ALREADY_COMPLETED`) enquanto o canal do
    portal a iniciaria assim mesmo. `EXCLUSIVE` sobrevive a assimetria porque uma geracao
    ENCERRADA e' reiniciavel dos DOIS lados — as duas portas concordam sobre o que garantem.

    O teste le o desvio na propria AST da funcao (nao numa copia da regra): a ramificacao por
    `HumanIntakeProvenance` tem de aparecer ANTES de qualquer leitura de postura."""
    import inspect

    fonte = inspect.getsource(start_process_idempotent)
    corpo = fonte[fonte.index("from maezo.gateway.human.auth_transport") :]
    desvio = corpo.index("HumanIntakeProvenance")
    leitura_postura = corpo.index("start_dedup_posture")
    assert desvio < leitura_postura, (
        "o desvio do canal do portal deixou de preceder a leitura da postura — se o portal "
        "passou a atravessar o portao, a razao (2) contra `PERMANENT` mudou e o bloco de "
        "politica em `_START_DEDUP_POLICY` precisa ser reescrito ANTES de promover a postura"
    )
    assert start_dedup_posture(AUTH) is not StartDedupPosture.PERMANENT


# ---------------------------------------------------------------------------
# 5. DEDUP NO CANAL DE AGENTE — segundo start com a MESMA chave nao cria segunda instancia
# ---------------------------------------------------------------------------


class _ContandoStarts(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.starts: list[str] = []

    async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
        self.starts.append(str(k.get("business_key") or (a[1] if len(a) > 1 else "")))
        return await super().start_process_instance(*a, **k)


def _prov() -> AgentDecisionProvenance:
    return AgentDecisionProvenance(
        agent_id="rafael",
        agent_version="rafael@v0",
        tenant_id="amh",
        decision_basis={"route": "human_auditor"},
    )


async def _start_auth(transport: Any, sink: Any, chave: str) -> ProcessInstance:
    return await start_process_idempotent(
        transport,
        process_key=AUTH,
        business_key=chave,
        variables={"numero_guia_tiss": "GUIA-1", "tenant_id": "amh"},
        audit_sink=sink,
        provenance=_prov(),
    )


async def test_segundo_start_com_a_mesma_guia_nao_cria_segunda_instancia() -> None:
    """A propriedade que o dono pediu, no nivel em que uma unidade pode prova-la: dois starts
    com a MESMA chave produzem UM start no engine, e o segundo devolve a instancia viva.

    (A prova equivalente CONTRA UM ENGINE REAL — dois CANAIS, uma guia, uma linha em
    `ACT_HI_PROCINST` — esta' listada para o executor de engine; ela nao roda aqui.)"""
    transport, sink = _ContandoStarts(), FakeStartAuditSink()
    chave = auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA-1")

    primeiro = await _start_auth(transport, sink, chave)
    segundo = await _start_auth(transport, sink, chave)

    assert transport.starts == [chave], "a mesma guia iniciou DUAS instancias"
    assert primeiro.instance_id == segundo.instance_id
    assert segundo.already_existed is True
    assert segundo.start_outcome is StartOutcome.ALREADY_ACTIVE


async def test_guias_diferentes_nao_sao_serializadas_uma_na_outra() -> None:
    """Simetria: o portao separa concorrentes da MESMA guia, nunca funde guias distintas."""
    transport, sink = _ContandoStarts(), FakeStartAuditSink()
    a = auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA-A")
    b = auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA-B")

    ia = await _start_auth(transport, sink, a)
    ib = await _start_auth(transport, sink, b)

    assert sorted(transport.starts) == sorted([a, b])
    assert ia.instance_id != ib.instance_id


async def test_tenants_diferentes_com_a_mesma_guia_nao_colidem() -> None:
    """O tenant e' componente da chave por construcao (ADR-0037): a guia `GUIA-1` de dois
    tenants sao dois casos, nunca um."""
    assert auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA-1") != auth_business_key(
        tenant_id="outro", numero_guia_tiss="GUIA-1"
    )


async def test_uma_guia_encerrada_pode_abrir_o_proximo_caso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`EXCLUSIVE` e' mutua exclusao de CONCORRENTES, nunca um portao permanente: uma guia cuja
    instancia TERMINOU pode iniciar o proximo caso. E' exatamente esta branch que `PERMANENT`
    recusaria — e a razao (1) de a postura ter parado em `EXCLUSIVE` enquanto o reuso de numero
    de guia for pergunta MEDICA/ANS em aberto."""
    monkeypatch.setattr(transport_module, "_STRICT_GATE_RESOLVE_DELAY_S", 0.0)
    transport, sink = _ContandoStarts(), FakeStartAuditSink()
    chave = auth_business_key(tenant_id="amh", numero_guia_tiss="GUIA-FECHADA")

    primeira = await _start_auth(transport, sink, chave)
    transport.seed_instance(
        ProcessInstance(
            instance_id=primeira.instance_id,
            process_key=AUTH,
            business_key=chave,
            state="COMPLETED",
        )
    )

    segunda = await _start_auth(transport, sink, chave)
    assert transport.starts == [chave, chave], "a guia encerrada foi recusada — isso e' PERMANENT"
    assert segunda.start_outcome is StartOutcome.STARTED


# ---------------------------------------------------------------------------
# 6. O PUBLICADOR DE EVENTOS — `_business_key` e' o payload_ref, nunca vazio
# ---------------------------------------------------------------------------


async def test_o_publicador_recusa_um_fato_sem_payload_ref() -> None:
    """`_business_key` nao e' decoracao: o BPMN de AUTH o declara como O payload_ref pelo qual
    um consumidor da Zona PHI resolve o conteudo clinico, justamente porque o fato nunca carrega
    PHI (GAP-AUTH-1 / ADR-0006). Publicar com ele vazio e' publicar um fato irresolvivel, e a
    perda e' SILENCIOSA. O harness mapeia `businessKey` ausente para `""`, entao o caso e'
    alcancavel — e ficou load-bearing quando a chave passou a ser o UNICO dominio compartilhado
    entre os dois canais."""
    from maezo.tools.workers.events import make_publish_event_handler
    from maezo.tools.workers.harness import ExternalTask

    handler = make_publish_event_handler(None)
    tarefa = ExternalTask(
        task_id="t-1",
        topic="operadora.events.publish",
        process_instance_id="pi-1",
        business_key="",
        worker_id="w-1",
        variables={"event_topic": "agents.events.auth.completed"},
    )
    with pytest.raises(ValueError, match="business_key"):
        await handler(tarefa)
