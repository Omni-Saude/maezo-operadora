"""`maezo.platform.rco_registro` (ADR-0077 §4, unidade 5c-2): avaliacao da regra, linha do registro,
amostragem, ordinal e o store (com conexao falsa — o banco real fica em `test_rco_decisao_regra_live_pg.py`).

Sem banco nem engine: a logica pura e o contrato do SQL (lock antes do ordinal, tenant, falha embrulhada).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from maezo.platform import rco_ativacao as at
from maezo.platform import rco_registro as reg
from maezo.platform.rco_manifesto import ParametrosAtivacao, RegraK1

PARAMETROS = ParametrosAtivacao(
    revisao_inicial_decisoes=3,
    revisao_inicial_prazo_dias=7,
    amostra_posterior_pct=50,
    limiar_reversao_pct=5,
    limiar_reversao_min_decisoes=30,
    conferencia_negativa_admin_decisoes=2,
)
SHA = "sha256:" + "a" * 64
SEM_ESTADOS = at.EstadosManuais({})


@pytest.fixture(autouse=True)
def _cache_limpo() -> None:
    at.limpar_cache_estados()


def _regra(ativacao: str | None = "plena", exige_aditivo: bool | None = False) -> RegraK1:
    return RegraK1("REGRA-1", "1.2.3", "b" * 64, ativacao, exige_aditivo)


def _row(**campos: object) -> dict[str, object]:
    base: dict[str, object] = {
        "decisao": "glosar",
        "classeAdverso": "contas",
        "motivoTab38": "1801",
        "fila": "faturamento",
        "idRegra": "REGRA-1",
    }
    base.update(campos)
    return base


def _avaliar(
    row: Mapping[str, Any] | None = None,
    *,
    regra: RegraK1 | None = None,
    estados: at.EstadosManuais = SEM_ESTADOS,
    habilitado: bool = False,
) -> reg.AvaliacaoRegra:
    av = reg.avaliar_regra(
        decision_key="rco_x",
        sha_dmn=SHA,
        regra=regra or _regra(),
        parametros=PARAMETROS,
        row=_row() if row is None else row,
        estados=estados,
        habilitado=habilitado,
    )
    assert av is not None
    return av


def _ctx(**campos: Any) -> reg.ContextoDecisao:
    base: dict[str, Any] = {
        "tenant": "amh",
        "chave_idempotencia": "c" * 64,
        "chave_negocio": "AUTH-amh-123",
        "chave_amostra": b"segredo",
        "id_chave_amostra": "k1",
    }
    base.update(campos)
    return reg.ContextoDecisao(**base)


# --- avaliar_regra ----------------------------------------------------------------------------------


def test_avaliacao_em_sombra_por_padrao_e_campos_de_auditoria() -> None:
    av = _avaliar()
    assert (av.estado_manual, av.ativacao_pacote, av.ativacao_efetiva, av.modo) == (
        "sombra",
        "plena",
        "plena",
        "sombra",
    )
    assert av.campos_de_auditoria() == {
        "estadoManual": "sombra",
        "ativacaoEfetiva": "plena",
        "modo": "sombra",
    }
    assert (av.id_regra, av.versao_regra, av.sha_dmn, av.decisao) == ("REGRA-1", "1.2.3", SHA, "glosar")
    assert all(isinstance(v, str) for v in av.campos_de_auditoria().values())


def test_regra_major1_sem_ativacao_nao_gera_avaliacao() -> None:
    for regra in (_regra(None, None), _regra("plena", None), _regra(None, False)):
        assert (
            reg.avaliar_regra(
                decision_key="rco_x",
                sha_dmn=SHA,
                regra=regra,
                parametros=PARAMETROS,
                row=_row(),
                estados=SEM_ESTADOS,
                habilitado=False,
            )
            is None
        )


def test_ativa_l2_sem_indicador_continua_sombra_com_alerta() -> None:
    estados = at.EstadosManuais({("REGRA-1", "1.2.3"): "ativa_l2"})
    av = _avaliar(estados=estados, habilitado=False)
    assert av.estado_manual == "ativa_l2"  # o que o arquivo diz
    assert av.modo == "sombra"  # o efeito so vale com o indicador ligado (ADR-0077 §1)
    assert any(a.startswith(at.ALERTA_ESTADO_IGNORADO) for a in av.alertas)
    assert _avaliar(estados=estados, habilitado=True).modo == "ativa"


def test_arquivo_ilegivel_vira_desligada_e_alerta() -> None:
    ilegivel = at.EstadosManuais({}, ilegivel=True, motivo="x")
    av = _avaliar(estados=ilegivel)
    assert av.estado_manual == "desligada"
    assert at.ALERTA_ARQUIVO_ILEGIVEL in av.alertas
    assert av.modo == "sombra"  # indicador desligado: nada muda de rumo


@pytest.mark.parametrize("pacote", ["conferida", "em_correcao"])
def test_ativacao_do_pacote_segue_para_a_efetiva(pacote: str) -> None:
    av = _avaliar(regra=_regra(pacote, True))
    assert (av.ativacao_pacote, av.ativacao_efetiva, av.exige_aditivo) == (pacote, pacote, True)


@pytest.mark.parametrize(
    "valor", ["", "com espaco", "x" * 65, "12/34", "Maria da Silva", None, 7, ["glosar"]]
)
def test_saida_da_dmn_fora_da_forma_de_token_vira_nulo(valor: object) -> None:
    """Uma saida FEEL pode ecoar dado de caso: so token curto e gravado."""
    av = _avaliar(_row(decisao=valor, classeAdverso=valor, motivoTab38=valor, fila=valor))
    assert (av.decisao, av.classe_adverso, av.motivo_tab38, av.fila) == (None, None, None, None)


# --- montar_linha: amostragem, ordinal e acao -------------------------------------------------------


@pytest.mark.parametrize(
    ("decisao", "classe", "tem"),
    [
        ("glosar", "contas", True),
        ("glosar", "administrativo", True),
        ("negar", "administrativo", True),
        ("negar", "clinico", False),  # incoerente: vai ao humano, nao consome a janela inicial
        ("negar", "contas", False),
        ("glosar", "clinico", False),
        ("glosar", None, False),
        ("recomendar_negativa", "clinico", False),
        ("liberar", "contas", False),
        ("revisao_humana", "administrativo", False),
        (None, "contas", False),
    ],
)
def test_ordinal_so_para_adversa_coerente_com_a_classe(
    decisao: str | None, classe: str | None, tem: bool
) -> None:
    assert _avaliar(_row(decisao=decisao, classeAdverso=classe)).tem_ordinal is tem


def test_decisao_sem_amostragem_nunca_e_amostrada_e_recusa_ordinal() -> None:
    av = _avaliar(_row(decisao="liberar"))
    linha = reg.montar_linha(av, _ctx(), None)
    assert (linha.amostrada, linha.motivo_amostra, linha.id_chave_amostra, linha.ordinal) == (
        False,
        None,
        None,
        None,
    )
    with pytest.raises(ValueError, match="ordinal"):
        reg.montar_linha(av, _ctx(), 1)
    with pytest.raises(ValueError, match="ordinal"):
        reg.montar_linha(_avaliar(), _ctx(), None)


def test_as_primeiras_decisoes_sao_amostra_inicial_sem_chave() -> None:
    av = _avaliar()
    for ordinal in (1, 2, 3):  # revisao_inicial_decisoes = 3
        linha = reg.montar_linha(av, _ctx(chave_amostra=None, id_chave_amostra=None), ordinal)
        assert (linha.amostrada, linha.motivo_amostra, linha.ordinal) == (True, "inicial", ordinal)
        assert linha.id_chave_amostra is None  # a chave nao decidiu


def test_depois_da_inicial_a_amostra_e_a_do_hmac_com_o_id_da_chave() -> None:
    av = _avaliar()
    esperado = at.amostrar(b"segredo", "k1", "AUTH-amh-123", "REGRA-1", "1.2.3", 50)
    linha = reg.montar_linha(av, _ctx(), 4)
    assert (linha.amostrada, linha.motivo_amostra or "") == esperado
    assert linha.id_chave_amostra == "k1"  # amostrada ou nao, a chave decidiu: a auditoria a reproduz


def test_sem_chave_toda_adversa_apos_a_inicial_e_amostrada_chave_ausente() -> None:
    av = _avaliar()
    for sem in (
        {"chave_amostra": None, "id_chave_amostra": None},
        {"chave_amostra": b"s", "id_chave_amostra": None},
        {"chave_negocio": None},
    ):
        linha = reg.montar_linha(av, _ctx(**sem), 4)
        assert (linha.amostrada, linha.motivo_amostra, linha.id_chave_amostra) == (
            True,
            "chave_ausente",
            None,
        )


def test_a_chave_bruta_de_negocio_nunca_vai_para_a_linha() -> None:
    linha = reg.montar_linha(_avaliar(), _ctx(), 4)
    assert "AUTH-amh-123" not in repr(linha) and "AUTH-amh-123" not in repr(linha.params())
    assert not any("pseudon" in c for c in reg.COLUNAS)  # a chave nem em hash e gravada
    assert "AUTH-amh-123" not in repr(_ctx())  # nem no repr do contexto


def test_em_sombra_a_acao_e_registrar_apenas_em_qualquer_ordinal() -> None:
    for decisao in ("glosar", "negar", "liberar", "recomendar_negativa"):
        av = _avaliar(_row(decisao=decisao, classeAdverso="administrativo"), regra=_regra("conferida", False))
        ordinal = 1 if av.tem_ordinal else None
        assert reg.montar_linha(av, _ctx(), ordinal).acao == "registrar_apenas"


def test_acao_sob_ativa_l2_usa_conferencia_inicial_e_amostragem() -> None:
    """O contrato do 5d: com o indicador ligado so nos testes, `conferida` confere as primeiras
    `conferencia_negativa_admin_decisoes` (2) e depois segue com amostragem."""
    estados = at.EstadosManuais({("REGRA-1", "1.2.3"): "ativa_l2"})
    av = _avaliar(
        _row(decisao="negar", classeAdverso="administrativo"),
        regra=_regra("conferida", False),
        estados=estados,
        habilitado=True,
    )
    assert reg.montar_linha(av, _ctx(), 1).acao == "conferencia"
    assert reg.montar_linha(av, _ctx(), 2).acao == "conferencia"
    assert reg.montar_linha(av, _ctx(), 3).acao == "efeito_com_amostragem"


def test_ordinais_diferentes_da_mesma_regra_dao_linhas_distintas_e_deterministicas() -> None:
    av = _avaliar()
    primeira = reg.montar_linha(av, _ctx(), 7)
    assert reg.montar_linha(av, _ctx(), 7) == primeira
    assert reg.montar_linha(av, _ctx(), 8).ordinal == 8


def test_colunas_e_params_na_mesma_ordem() -> None:
    linha = reg.montar_linha(_avaliar(), _ctx(), 1)
    params = linha.params()
    assert len(params) == len(reg.COLUNAS)
    por_nome = dict(zip(reg.COLUNAS, params, strict=True))
    assert por_nome["tenant"] == "amh" and por_nome["id_regra"] == "REGRA-1" and por_nome["ordinal"] == 1
    assert por_nome["decision_key"] == "rco_x" and por_nome["sha_dmn"] == SHA


# --- chave de idempotencia e chave da amostra -------------------------------------------------------


def test_chave_idempotencia_e_deterministica_e_nao_expoe_a_tarefa() -> None:
    k = reg.chave_idempotencia("amh", "task-1", "rco_x")
    assert k == reg.chave_idempotencia("amh", "task-1", "rco_x")
    assert len(k) == 64 and "task-1" not in k
    assert k != reg.chave_idempotencia("amh", "task-2", "rco_x")
    assert k != reg.chave_idempotencia("amh", "task-1", "rco_y")
    assert k != reg.chave_idempotencia("outro", "task-1", "rco_x")


def test_chave_da_amostra_so_com_segredo_e_id_validos() -> None:
    k, i = reg.VARIAVEL_CHAVE_AMOSTRA, reg.VARIAVEL_ID_CHAVE_AMOSTRA
    assert reg.chave_amostra_do_ambiente({k: " s3 ", i: "k-2026.11"}) == (b"s3", "k-2026.11")
    assert reg.chave_amostra_do_ambiente({}) == (None, None)
    assert reg.chave_amostra_do_ambiente({k: "s3"}) == (None, None)  # sem id nao ha como reproduzir
    assert reg.chave_amostra_do_ambiente({i: "k1"}) == (None, None)
    assert reg.chave_amostra_do_ambiente({k: "   ", i: "k1"}) == (None, None)
    assert reg.chave_amostra_do_ambiente({k: "s3", i: "id com espaco"}) == (None, None)


def test_contexto_da_tarefa_guarda_a_chave_de_negocio_so_em_memoria() -> None:
    ctx = reg.contexto_da_tarefa(
        tenant="amh",
        task_id="t-1",
        decision_key="rco_x",
        business_key="AUTH-amh-guia-999",
        ambiente={reg.VARIAVEL_CHAVE_AMOSTRA: "s", reg.VARIAVEL_ID_CHAVE_AMOSTRA: "k1"},
    )
    assert (ctx.chave_negocio, ctx.chave_amostra, ctx.id_chave_amostra) == ("AUTH-amh-guia-999", b"s", "k1")
    assert "guia-999" not in repr(ctx) and "guia-999" not in ctx.chave_idempotencia
    assert not hasattr(ctx, "chave_negocio_pseudonimizada")
    vazio = reg.contexto_da_tarefa(
        tenant="amh", task_id="t-1", decision_key="rco_x", business_key="", ambiente={}
    )
    assert vazio.chave_negocio is None


def test_contexto_nao_depende_da_chave_phi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem pseudonimo nao ha dependencia de PHI_HMAC_KEY: producao sem a chave tambem monta o contexto."""
    monkeypatch.delenv("RUNTIME_MODE", raising=False)
    monkeypatch.delenv("PHI_HMAC_KEY", raising=False)
    ctx = reg.contexto_da_tarefa(
        tenant="amh", task_id="t-1", decision_key="rco_x", business_key="AUTH-amh-1", ambiente={}
    )
    assert ctx.tenant == "amh"


# --- store (conexao falsa) --------------------------------------------------------------------------


class _Tx:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def __aenter__(self) -> None:
        self._log.append("begin")

    async def __aexit__(self, *exc: object) -> None:
        self._log.append("commit")


class _Conn:
    def __init__(self, *, inserido: bool = True, falha_em: str | None = None) -> None:
        self.log: list[str] = []
        self.args: list[tuple[Any, ...]] = []
        self.sqls: list[str] = []
        self.terminada = False
        self.close_trava = False
        self._inserido = inserido
        self._falha_em = falha_em
        self.fechada = False

    async def execute(self, sql: str, *args: Any) -> None:
        self.log.append(
            "lock" if "pg_advisory_xact_lock" in sql else "set_local" if "SET LOCAL" in sql else "set_path"
        )
        self.sqls.append(sql)
        self.args.append(args)
        if self._falha_em == "execute":
            raise ConnectionError("fora")

    def transaction(self, **_: Any) -> _Tx:
        return _Tx(self.log)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.args.append(args)
        if sql.startswith("SELECT COALESCE(MAX(ordinal)"):
            self.log.append("ordinal")
            return 5
        self.log.append("insert")
        return 99 if self._inserido else None

    async def close(self) -> None:
        if self.close_trava:
            await asyncio.sleep(3600)
        self.fechada = True

    def terminate(self) -> None:
        self.terminada = True


def _store(conn: _Conn) -> reg.PostgresRegistroDecisoesRco:
    async def conectar() -> _Conn:
        return conn

    return reg.PostgresRegistroDecisoesRco(dsn="postgresql://x", tenant="amh", conectar=conectar)


async def test_store_toma_o_lock_antes_de_ler_o_ordinal_e_grava_na_mesma_transacao() -> None:
    conn = _Conn()
    gravou = await _store(conn).registrar(_avaliar(), _ctx())
    assert gravou is True
    assert conn.log == ["set_path", "begin", "set_local", "set_local", "lock", "ordinal", "insert", "commit"]
    assert conn.sqls[1:3] == ["SET LOCAL lock_timeout = '2s'", "SET LOCAL statement_timeout = '3s'"]
    assert conn.args[3] == ("rco-ordinal|amh|REGRA-1|1.2.3",)
    insert = conn.args[-1]
    assert insert[reg.COLUNAS.index("ordinal")] == 5  # o ordinal lido sob o lock entra na linha
    assert conn.fechada


async def test_store_sem_ordinal_nao_toma_lock() -> None:
    conn = _Conn()
    await _store(conn).registrar(_avaliar(_row(decisao="liberar")), _ctx())
    assert conn.log == ["set_path", "begin", "set_local", "set_local", "insert", "commit"]


async def test_store_reentrega_nao_grava_de_novo() -> None:
    conn = _Conn(inserido=False)
    assert await _store(conn).registrar(_avaliar(), _ctx()) is False


async def test_store_falha_de_banco_vira_indisponivel_e_fecha_a_conexao() -> None:
    conn = _Conn(falha_em="execute")
    with pytest.raises(reg.RegistroRcoIndisponivelError, match="ConnectionError"):
        await _store(conn).registrar(_avaliar(), _ctx())
    assert conn.fechada


async def test_store_recusa_tenant_diferente_antes_da_rede() -> None:
    conn = _Conn()
    with pytest.raises(ValueError, match="tenant"):
        await _store(conn).registrar(_avaliar(), _ctx(tenant="outro"))
    assert conn.log == []


def test_store_exige_tenant_de_schema_seguro() -> None:
    with pytest.raises(ValueError):
        reg.PostgresRegistroDecisoesRco(dsn="postgresql://x", tenant='a"; DROP')


def test_o_store_cumpre_a_porta() -> None:
    assert isinstance(_store(_Conn()), reg.RegistroDecisoesRco)


# --- cache do arquivo de estado ---------------------------------------------------------------------


def test_cache_do_estado_le_uma_vez_por_janela_e_depois_relê(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[int] = []

    def fake() -> at.EstadosManuais:
        chamadas.append(1)
        return at.EstadosManuais({("R", "1.0.0"): "ativa_l2"} if len(chamadas) > 1 else {})

    monkeypatch.setattr(at, "carregar_estados", fake)
    t = [100.0]
    primeiro = at.estados_em_cache(ttl_s=30, relogio=lambda: t[0])
    t[0] = 129.0
    assert at.estados_em_cache(ttl_s=30, relogio=lambda: t[0]) is primeiro and len(chamadas) == 1
    t[0] = 130.0  # a janela fechou: le de novo e ve o arquivo corrigido
    assert at.estados_em_cache(ttl_s=30, relogio=lambda: t[0]).estado_de("R", "1.0.0") == "ativa_l2"
    assert len(chamadas) == 2
    at.limpar_cache_estados()
    at.estados_em_cache(ttl_s=30, relogio=lambda: t[0])
    assert len(chamadas) == 3


def test_cache_do_estado_guarda_arquivo_ilegivel_como_ilegivel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed e mantido: o cache nunca transforma um arquivo ruim em 'sombra para todos'."""
    ruim = at.EstadosManuais({}, ilegivel=True, motivo="x")
    monkeypatch.setattr(at, "carregar_estados", lambda: ruim)
    assert at.estados_em_cache(relogio=lambda: 1.0).estado_de("R", "1.0.0") == "desligada"


async def test_store_close_pendurado_e_derrubado_por_terminate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reg, "_FECHAR_TIMEOUT_S", 0.05)
    conn = _Conn()
    conn.close_trava = True
    assert await _store(conn).registrar(_avaliacao_sombra(), _ctx()) is True  # o resultado ja era conhecido
    assert conn.terminada and not conn.fechada


async def test_store_falha_de_banco_nao_vaza_a_mensagem_do_driver() -> None:
    class _Falha(_Conn):
        async def fetchval(self, sql: str, *args: Any) -> Any:
            raise RuntimeError("Failing row contains (AUTH-amh-guia-1)")

    with pytest.raises(reg.RegistroRcoIndisponivelError) as exc:
        await _store(_Falha()).registrar(_avaliacao_sombra(), _ctx())
    assert "guia-1" not in str(exc.value) and exc.value.__cause__ is None


def _avaliacao_sombra() -> reg.AvaliacaoRegra:
    return _avaliar()
