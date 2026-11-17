"""DL-0182 (unidade 5d-1): a janela da `conferida` conta so conferencias CONFIRMADAS por humano, um `nao`
zera a janela e alerta a coordenacao, e o escritor de desfecho da conferencia de um clique.

Sem banco: a definicao pura da contagem (`rco_ativacao.conferencias_confirmadas`), o roteamento com a
contagem, e o contrato do SQL do store com conexao falsa. O SQL real (a contagem e o escritor sobre a 0035)
fica em `test_rco_conferencia_live_pg.py`.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog

from maezo.platform import observability
from maezo.platform import rco_ativacao as at
from maezo.platform import rco_registro as reg
from maezo.platform.rco_manifesto import ParametrosAtivacao, RegraK1

JANELA = 30  # `conferencia_negativa_admin_decisoes` do pacote 2.2.0
PARAMETROS = ParametrosAtivacao(
    revisao_inicial_decisoes=3,
    revisao_inicial_prazo_dias=7,
    amostra_posterior_pct=50,
    limiar_reversao_pct=5,
    limiar_reversao_min_decisoes=30,
    conferencia_negativa_admin_decisoes=JANELA,
)
SHA = "sha256:" + "a" * 64
AGORA = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
ATIVA = at.EstadosManuais({("REGRA-1", "1.2.3"): "ativa_l2"})


def _avaliar(
    decisao: str = "glosar",
    *,
    ativacao: str = "conferida",
    habilitado: bool = True,
    estados: at.EstadosManuais = ATIVA,
    classe: str = "administrativo",
) -> reg.AvaliacaoRegra:
    av = reg.avaliar_regra(
        decision_key="rco_x",
        sha_dmn=SHA,
        regra=RegraK1("REGRA-1", "1.2.3", "b" * 64, ativacao, False),
        parametros=PARAMETROS,
        row={"decisao": decisao, "classeAdverso": classe, "motivoTab38": "1801", "fila": "cadastro"},
        estados=estados,
        habilitado=habilitado,
    )
    assert av is not None
    return av


def _ctx() -> reg.ContextoDecisao:
    return reg.ContextoDecisao(tenant="amh", chave_idempotencia="c" * 64, chave_negocio="AUTH-amh-1")


# --- 1. a contagem (definicao pura) -----------------------------------------------------------------


def _sims(n: int, *, desde_id: int = 1, desde_decisao: int = 1) -> list[tuple[int, int, str]]:
    return [(desde_id + i, desde_decisao + i, "sim") for i in range(n)]


def _contar(desfechos: list[tuple[int, int, str]], decisao: str = "glosar") -> int:
    """Os desfechos sao todos de conferencias de `glosar` (salvo o teste de escopo)."""
    return at.conferencias_confirmadas([(i, d, c, "glosar") for i, d, c in desfechos], decisao)


def test_janela_e_por_decisao_adversa_30_negar_confirmadas_nao_abrem_glosar() -> None:
    negar = [(i, i, "sim", "negar") for i in range(1, 31)]
    assert at.conferencias_confirmadas(negar, "negar") == 30
    assert at.conferencias_confirmadas(negar, "glosar") == 0
    assert at.janela_de_conferencia_aberta(at.conferencias_confirmadas(negar, "glosar"), JANELA)
    # um `nao` em conferencia de `negar` zera tambem a janela da `glosar` (lado seguro)
    misto = [(i, 100 + i, "sim", "glosar") for i in range(1, 11)] + [(50, 1, "nao", "negar")]
    assert at.conferencias_confirmadas(misto, "glosar") == 0


def test_contagem_limitada_devolve_o_minimo_com_a_mesma_semantica() -> None:
    desfechos = [(i, i, "sim", "glosar") for i in range(1, 51)]
    assert at.conferencias_confirmadas(desfechos, "glosar", JANELA) == JANELA
    assert at.conferencias_confirmadas(desfechos[:29], "glosar", JANELA) == 29
    com_nao = [*desfechos, (60, 999, "nao", "negar"), (61, 70, "sim", "glosar")]
    assert at.conferencias_confirmadas(com_nao, "glosar", JANELA) == 1  # o reinicio vale com o limite


def test_janela_precisa_de_30_confirmacoes() -> None:
    assert _contar(_sims(29)) == 29
    assert at.janela_de_conferencia_aberta(29, JANELA) is True
    assert _contar(_sims(30)) == 30
    assert at.janela_de_conferencia_aberta(30, JANELA) is False


def test_sem_desfecho_nem_conta_nem_zera() -> None:
    desfechos = (
        _sims(10)
        + [(100 + i, 100 + i, "sem_desfecho") for i in range(50)]
        + _sims(5, desde_id=200, desde_decisao=300)
    )
    assert _contar(desfechos) == 15


def test_mesma_decisao_confirmada_duas_vezes_conta_uma() -> None:
    assert _contar([(1, 7, "sim"), (2, 7, "sim"), (3, 8, "sim")]) == 2


def test_rejeicao_zera_e_depois_precisa_de_30_novas() -> None:
    antes = _sims(29)
    nao = [(50, 99, "nao")]
    assert _contar(antes + nao) == 0
    depois = _sims(29, desde_id=51, desde_decisao=200)
    assert _contar(antes + nao + depois) == 29
    assert at.janela_de_conferencia_aberta(29, JANELA)
    assert _contar(antes + nao + depois + [(90, 300, "sim")]) == 30


def test_correcao_de_sim_para_nao_na_mesma_decisao_zera() -> None:
    assert _contar(_sims(30) + [(31, 30, "nao")]) == 0
    # e a correcao de volta (nao -> sim) conta so a partir do `nao`
    assert _contar(_sims(30) + [(31, 30, "nao"), (32, 30, "sim")]) == 1


def test_contagem_nao_depende_da_ordem_de_chegada() -> None:
    desfechos = _sims(20) + [(25, 99, "nao")] + _sims(7, desde_id=26, desde_decisao=500)
    embaralhados = list(desfechos)
    random.Random(7).shuffle(embaralhados)
    assert _contar(embaralhados) == _contar(desfechos) == 7


@pytest.mark.parametrize("valor", [None, True, False, "30", 30.0, -1])
def test_contagem_desconhecida_ou_invalida_mantem_a_janela_aberta(valor: Any) -> None:
    assert at.janela_de_conferencia_aberta(valor, JANELA) is True


# --- 2. o roteamento com a contagem -----------------------------------------------------------------


def test_glosar_em_conferida_confere_ate_30_confirmadas_e_depois_tem_efeito_com_amostragem() -> None:
    av = _avaliar("glosar")
    for confirmadas in (None, 0, 1, 29):
        assert reg.montar_linha(av, _ctx(), 5000, conferencias_confirmadas=confirmadas).acao == "conferencia"
    for confirmadas in (30, 31, 10_000):
        linha = reg.montar_linha(av, _ctx(), 1, conferencias_confirmadas=confirmadas)
        assert linha.acao == "efeito_com_amostragem"


def test_negar_em_conferida_com_indicador_ligado_nunca_sai_sem_conferencia() -> None:
    """Falha se uma negativa de regra `conferida` sair sem conferencia, com QUALQUER contagem ou ordinal."""
    av = _avaliar("negar")
    assert av.modo == "ativa" and av.ativacao_efetiva == "conferida"
    for ordinal in (1, 30, 31, 10_000):
        for confirmadas in (None, 0, 29, 30, 31, 1000, 10**9):
            linha = reg.montar_linha(av, _ctx(), ordinal, conferencias_confirmadas=confirmadas)
            assert linha.acao == "conferencia", (ordinal, confirmadas)


def test_indicador_desligado_tudo_registrar_apenas_com_qualquer_contagem() -> None:
    for decisao in ("glosar", "negar"):
        av = _avaliar(decisao, habilitado=False)
        assert av.modo == "sombra"
        for confirmadas in (None, 0, 30, 1000):
            linha = reg.montar_linha(av, _ctx(), 1, conferencias_confirmadas=confirmadas)
            assert linha.acao == "registrar_apenas"


def test_plena_com_efeito_amostra_inicial_conta_so_efeitos_anteriores_nao_o_ordinal() -> None:
    """D1 (DL-0182): com o indicador ligado, as primeiras `revisao_inicial_decisoes` (3 aqui) glosas COM
    EFEITO sao `inicial`, qualquer que seja o ordinal (que a sombra consome); contagem desconhecida
    amostra."""
    av = _avaliar("glosar", ativacao="plena", classe="contas")
    assert av.modo == "ativa"
    for ordinal in (1, 41, 5000):
        for efeitos in (None, 0, 1, 2):
            linha = reg.montar_linha(av, _ctx(), ordinal, efeitos_anteriores=efeitos)
            assert (linha.amostrada, linha.motivo_amostra, linha.acao) == (
                True,
                "inicial",
                "efeito_com_amostragem",
            ), (ordinal, efeitos)
    for efeitos in (3, 4, 100):
        linha = reg.montar_linha(av, _ctx(), 1, efeitos_anteriores=efeitos)
        assert linha.motivo_amostra != "inicial"
        assert linha.motivo_amostra == "chave_ausente"  # sem chave HMAC no contexto: a amostra posterior


def test_em_sombra_a_amostra_inicial_observacional_segue_o_ordinal() -> None:
    av = _avaliar("glosar", ativacao="plena", classe="contas", habilitado=False)
    assert reg.montar_linha(av, _ctx(), 3, efeitos_anteriores=0).motivo_amostra == "inicial"
    assert reg.montar_linha(av, _ctx(), 4, efeitos_anteriores=0).motivo_amostra != "inicial"


@pytest.mark.parametrize("valor", [None, True, "3", 2.0])
def test_amostra_inicial_por_efeitos_desconhecida_amostra(valor: Any) -> None:
    assert at.amostra_inicial_por_efeitos(valor, 3) == (True, "inicial")
    assert at.amostra_inicial_por_efeitos(3, 3) == (False, "")


def test_acao_de_efeito_adverso_e_a_unica_saida_adversa_com_efeito() -> None:
    """`filtrar_saida`: negar/glosar que chegam a efeito saem em `efeito_com_amostragem`, nunca `efeito`."""
    estado = at.EstadoEfetivo("ativa", "plena")
    saidas = {
        at.filtrar_saida(
            estado, decisao=d, classe_adverso=c, fila="f", exige_aditivo=False, aditivo_cobre=None
        ).acao
        for d, c in (("glosar", "contas"), ("glosar", "administrativo"), ("negar", "administrativo"))
    }
    assert saidas == {at.ACAO_EFEITO_ADVERSO}


# --- 3. registrar: a contagem e lida sob o lock, so para a `conferida` ativa ------------------------


class _Tx:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> None:
        self._conn.log.append("begin")

    async def __aexit__(self, tipo: object, *_: object) -> None:
        self._conn.log.append("rollback" if tipo is not None else "commit")


class _Conn:
    """Conexao falsa: devolve o que o teste manda, na ordem; guarda o SQL e os argumentos."""

    def __init__(
        self,
        *,
        confirmadas: int = 0,
        efeitos: int = 0,
        decisao: dict[str, Any] | None = None,
        ultimo: str | None = None,
        falha: BaseException | None = None,
    ) -> None:
        self.log: list[str] = []
        self.args: list[tuple[str, tuple[Any, ...]]] = []
        self._confirmadas = confirmadas
        self._efeitos = efeitos
        self._decisao = decisao
        self._ultimo = ultimo
        self._falha = falha

    async def execute(self, sql: str, *args: Any) -> None:
        if self._falha is not None:
            raise self._falha
        self.args.append((sql, args))
        if "pg_advisory_xact_lock" in sql:
            self.log.append("lock")

    def transaction(self, **_: Any) -> _Tx:
        return _Tx(self)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.args.append((sql, args))
        if sql.startswith("SELECT COALESCE(MAX(ordinal)"):
            self.log.append("ordinal")
            return 77
        if sql == reg._CONFIRMADAS_SQL:
            self.log.append("confirmadas")
            return self._confirmadas
        if sql == reg._EFEITOS_ANTERIORES_SQL:
            self.log.append("efeitos")
            return self._efeitos
        if sql == reg._ULTIMO_DESFECHO_SQL:
            self.log.append("ultimo")
            return self._ultimo
        if sql == reg._INSERT_DESFECHO_SQL:
            self.log.append("insert_desfecho")
            return 501
        self.log.append("insert")
        return 1

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.args.append((sql, args))
        self.log.append("decisao")
        return self._decisao

    async def close(self) -> None:
        self.log.append("close")

    def terminate(self) -> None:
        pass


def _store(conn: _Conn) -> reg.PostgresRegistroDecisoesRco:
    async def conectar() -> _Conn:
        return conn

    return reg.PostgresRegistroDecisoesRco(dsn="postgresql://x", tenant="amh", conectar=conectar)


def _insert(conn: _Conn) -> tuple[Any, ...]:
    return next(a for sql, a in conn.args if sql.startswith("INSERT INTO rco_decisao_regra ("))


@pytest.mark.parametrize(
    ("confirmadas", "acao"), [(0, "conferencia"), (29, "conferencia"), (30, "efeito_com_amostragem")]
)
async def test_registrar_le_as_confirmadas_sob_o_lock_e_decide_por_elas(confirmadas: int, acao: str) -> None:
    conn = _Conn(confirmadas=confirmadas)
    assert await _store(conn).registrar(_avaliar("glosar"), _ctx()) is True
    assert [p for p in conn.log if p in {"lock", "ordinal", "confirmadas", "efeitos", "insert"}] == [
        "lock",
        "ordinal",
        "confirmadas",
        "efeitos",
        "insert",
    ]
    linha = _insert(conn)
    assert linha[reg.COLUNAS.index("acao")] == acao
    assert linha[reg.COLUNAS.index("ordinal")] == 77  # o ordinal nao decide a janela
    sql_conf = next(a for sql, a in conn.args if sql == reg._CONFIRMADAS_SQL)
    # a janela e a da mesma decisao adversa, e a consulta e limitada ao tamanho da janela
    assert sql_conf == ("amh", "REGRA-1", "1.2.3", "glosar", JANELA)


async def test_registrar_le_os_efeitos_anteriores_so_com_efeito_possivel() -> None:
    conn = _Conn(efeitos=2)
    await _store(conn).registrar(_avaliar("glosar", ativacao="plena", classe="contas"), _ctx())
    assert next(a for sql, a in conn.args if sql == reg._EFEITOS_ANTERIORES_SQL) == (
        "amh",
        "REGRA-1",
        "1.2.3",
        "glosar",
        PARAMETROS.revisao_inicial_decisoes,  # limitada: so importa se a revisao inicial foi cumprida
    )
    assert _insert(conn)[reg.COLUNAS.index("motivo_amostra")] == "inicial"
    sombra = _Conn(efeitos=0)
    await _store(sombra).registrar(
        _avaliar("glosar", ativacao="plena", classe="contas", habilitado=False), _ctx()
    )
    assert "efeitos" not in sombra.log
    for trecho in (
        "decisao = $4",
        "modo = 'ativa'",
        "acao = 'efeito_com_amostragem'",
        "tenant = $1",
        "LIMIT $5",
    ):
        assert trecho in reg._EFEITOS_ANTERIORES_SQL


async def test_registrar_negar_conferida_confere_mesmo_com_mil_confirmadas() -> None:
    conn = _Conn(confirmadas=1000)
    await _store(conn).registrar(_avaliar("negar"), _ctx())
    assert _insert(conn)[reg.COLUNAS.index("acao")] == "conferencia"
    assert next(a for sql, a in conn.args if sql == reg._CONFIRMADAS_SQL)[3] == "negar"


@pytest.mark.parametrize(
    "av",
    [
        _avaliar("glosar", habilitado=False),  # sombra: indicador desligado
        _avaliar("glosar", ativacao="plena", classe="contas"),  # nao e conferida
        _avaliar("liberar"),  # sem ordinal
    ],
    ids=["sombra", "plena", "liberar"],
)
async def test_registrar_so_conta_para_a_conferida_ativa(av: reg.AvaliacaoRegra) -> None:
    conn = _Conn(confirmadas=999)
    await _store(conn).registrar(av, _ctx())
    assert "confirmadas" not in conn.log


def test_o_sql_da_contagem_filtra_regra_conferencia_e_modo_ativo() -> None:
    sql = reg._CONFIRMADAS_SQL
    for trecho in (
        "r.tenant = $1",
        "r.id_regra = $2",
        "r.versao_regra = $3",
        "r.acao = 'conferencia'",
        "r.modo = 'ativa'",
        "SELECT DISTINCT f.rco_decisao_regra_id",
        "r.decisao = $4",
        "ORDER BY n.id DESC LIMIT 1",
        "LIMIT $5) s",
        "concordou = 'sim'",
        "concordou = 'nao'",
    ):
        assert trecho in sql


# --- 4. escritor de desfecho: funcoes puras ---------------------------------------------------------


def test_contrato_do_clique() -> None:
    assert reg.concordancia_do_clique("confirmar") == "sim"
    assert reg.concordancia_do_clique("rejeitar") == "nao"
    assert reg.concordancia_do_clique("sem_desfecho") == "sem_desfecho"
    assert set(reg.CLIQUES_DA_CONFERENCIA.values()) == set(reg.CONCORDOU)
    for ruim in ("sim", "nao", "aprovar", "", None, 1):
        with pytest.raises(reg.DesfechoRecusadoError) as erro:
            reg.concordancia_do_clique(ruim)  # type: ignore[arg-type]
        assert erro.value.codigo == "clique_desconhecido"


@pytest.mark.parametrize(
    ("concordou", "quando", "codigo"),
    [
        ("sim", None, "desfecho_em_ausente"),
        ("nao", "2026-11-17", "desfecho_em_ausente"),
        ("sim", datetime(2026, 11, 17, 12, 0), "desfecho_em_sem_fuso"),
        ("sem_desfecho", AGORA, "desfecho_em_em_sem_desfecho"),
    ],
)
def test_instante_do_desfecho(concordou: str, quando: Any, codigo: str) -> None:
    with pytest.raises(reg.DesfechoRecusadoError) as erro:
        reg.validar_desfecho_em(concordou, quando)
    assert erro.value.codigo == codigo and codigo in reg.RECUSAS_DESFECHO


def test_instante_valido() -> None:
    reg.validar_desfecho_em("sim", AGORA)
    reg.validar_desfecho_em("nao", AGORA)
    reg.validar_desfecho_em("sem_desfecho", None)


@pytest.mark.parametrize(
    ("concordou", "ultimo", "esperado"),
    [
        ("sim", None, True),
        ("nao", None, True),
        ("sem_desfecho", None, True),
        ("sim", "sim", False),  # re-tentativa
        ("nao", "nao", False),
        ("sem_desfecho", "sem_desfecho", False),
        ("nao", "sim", True),  # correcao humana: linha nova
        ("sim", "nao", True),
        ("sim", "sem_desfecho", True),  # conferencia vencida decidida depois
        ("nao", "sem_desfecho", True),
        ("sem_desfecho", "sim", "conferencia_ja_decidida"),
        ("sem_desfecho", "nao", "conferencia_ja_decidida"),
    ],
)
def test_decidir_desfecho(concordou: str, ultimo: str | None, esperado: bool | str) -> None:
    if isinstance(esperado, str):
        with pytest.raises(reg.DesfechoRecusadoError) as erro:
            reg.decidir_desfecho(acao="conferencia", concordou=concordou, ultimo=ultimo)
        assert erro.value.codigo == esperado
    else:
        assert reg.decidir_desfecho(acao="conferencia", concordou=concordou, ultimo=ultimo) is esperado


@pytest.mark.parametrize("acao", sorted(at.ACOES - {"conferencia"}))
def test_desfecho_so_em_decisao_de_conferencia(acao: str) -> None:
    with pytest.raises(reg.DesfechoRecusadoError) as erro:
        reg.decidir_desfecho(acao=acao, concordou="sim", ultimo=None)
    assert erro.value.codigo == "acao_nao_e_conferencia"


# --- 5. escritor de desfecho: store -----------------------------------------------------------------

DECISAO = {
    "id_regra": "REGRA-1",
    "versao_regra": "1.2.3",
    "acao": "conferencia",
    "ativacao_efetiva": "conferida",
}


async def test_confirmar_grava_uma_linha_sob_o_lock_da_regra() -> None:
    conn = _Conn(decisao=DECISAO)
    with structlog.testing.capture_logs() as logs:
        resultado = await _store(conn).registrar_desfecho(42, "confirmar", desfecho_em=AGORA)
    assert resultado == reg.ResultadoDesfecho(gravado=True, concordou="sim", id_desfecho=501)
    assert [p for p in conn.log if p not in {"begin", "commit", "close"}] == [
        "decisao",
        "lock",
        "ultimo",
        "insert_desfecho",
    ]
    # o MESMO lock do registro (ordinal + janela): o desfecho e a decisao seguinte se serializam
    lock = next(a for sql, a in conn.args if "pg_advisory_xact_lock" in sql)
    assert lock == ("rco-ordinal|amh|REGRA-1|1.2.3",)
    assert next(a for sql, a in conn.args if sql == reg._DECISAO_SQL) == (42, "amh")
    assert next(a for sql, a in conn.args if sql == reg._INSERT_DESFECHO_SQL) == (42, "sim", AGORA)
    assert not any(e["event"] == reg.EVENTO_CONFERENCIA_REJEITADA for e in logs)
    # APPEND-ONLY: o escritor so faz SELECT e INSERT
    assert all(sql.split()[0] in {"SET", "SELECT", "INSERT"} for sql, _ in conn.args)


async def test_reentrega_do_mesmo_desfecho_nao_grava() -> None:
    conn = _Conn(decisao=DECISAO, ultimo="sim")
    resultado = await _store(conn).registrar_desfecho(42, "confirmar", desfecho_em=AGORA)
    assert resultado == reg.ResultadoDesfecho(gravado=False, concordou="sim")
    assert "insert_desfecho" not in conn.log


async def test_rejeitar_zera_a_janela_e_alerta_com_log_e_metrica() -> None:
    registro = observability.get_metrics_collector().registry
    nome = "maezo_rco_conferencia_rejeitada_total"
    antes = registro.get_sample_value(nome, {"ativacao": "conferida"}) or 0.0
    conn = _Conn(decisao=DECISAO)
    with structlog.testing.capture_logs() as logs:
        resultado = await _store(conn).registrar_desfecho(42, "rejeitar", desfecho_em=AGORA)
    assert resultado.janela_reiniciada and resultado.gravado and resultado.concordou == "nao"
    alertas = [e for e in logs if e["event"] == reg.EVENTO_CONFERENCIA_REJEITADA]
    assert len(alertas) == 1
    alerta = alertas[0]
    assert alerta["log_level"] == "warning"
    # so codigos e ids, nunca dado de caso
    assert set(alerta) == {
        "event",
        "log_level",
        "tenant",
        "id_regra",
        "versao_regra",
        "rco_decisao_regra_id",
        "ativacao_efetiva",
    }
    assert (alerta["id_regra"], alerta["versao_regra"], alerta["rco_decisao_regra_id"]) == (
        "REGRA-1",
        "1.2.3",
        42,
    )
    assert registro.get_sample_value(nome, {"ativacao": "conferida"}) == antes + 1


async def test_rejeitar_repetido_nao_alerta_de_novo() -> None:
    registro = observability.get_metrics_collector().registry
    nome = "maezo_rco_conferencia_rejeitada_total"
    antes = registro.get_sample_value(nome, {"ativacao": "conferida"}) or 0.0
    conn = _Conn(decisao=DECISAO, ultimo="nao")
    with structlog.testing.capture_logs() as logs:
        resultado = await _store(conn).registrar_desfecho(42, "rejeitar", desfecho_em=AGORA)
    assert not resultado.gravado and not resultado.janela_reiniciada
    assert not any(e["event"] == reg.EVENTO_CONFERENCIA_REJEITADA for e in logs)
    assert (registro.get_sample_value(nome, {"ativacao": "conferida"}) or 0.0) == antes


def test_metrica_de_rejeicao_tem_rotulo_fechado() -> None:
    registro = observability.get_metrics_collector().registry
    nome = "maezo_rco_conferencia_rejeitada_total"
    antes = registro.get_sample_value(nome, {"ativacao": "outro"}) or 0.0
    observability.record_rco_conferencia_rejeitada("REGRA-1")
    assert registro.get_sample_value(nome, {"ativacao": "outro"}) == antes + 1
    assert registro.get_sample_value(nome, {"ativacao": "REGRA-1"}) is None
    com_conferencia = {a for a in at.ATIVACOES_EFETIVAS if a in {"conferida", "conferencia_integral"}}
    ativacoes = observability.RCO_CONFERENCIA_ATIVACOES
    assert ativacoes == com_conferencia


@pytest.mark.parametrize(
    ("args", "codigo"),
    [
        ((42, "aprovar", AGORA), "clique_desconhecido"),
        ((42, "confirmar", None), "desfecho_em_ausente"),
        ((42, "rejeitar", datetime(2026, 11, 17)), "desfecho_em_sem_fuso"),
        ((42, "sem_desfecho", AGORA), "desfecho_em_em_sem_desfecho"),
        ((0, "confirmar", AGORA), "id_decisao_invalido"),
        ((True, "confirmar", AGORA), "id_decisao_invalido"),
        (("42", "confirmar", AGORA), "id_decisao_invalido"),
    ],
)
async def test_recusa_antes_da_rede(args: tuple[Any, str, Any], codigo: str) -> None:
    conn = _Conn(decisao=DECISAO)
    with pytest.raises(reg.DesfechoRecusadoError) as erro:
        await _store(conn).registrar_desfecho(args[0], args[1], desfecho_em=args[2])
    assert erro.value.codigo == codigo
    assert conn.log == []


@pytest.mark.parametrize(
    ("decisao", "ultimo", "clique", "codigo"),
    [
        (None, None, "confirmar", "decisao_inexistente"),
        ({**DECISAO, "acao": "registrar_apenas"}, None, "confirmar", "acao_nao_e_conferencia"),
        ({**DECISAO, "acao": "efeito_com_amostragem"}, None, "rejeitar", "acao_nao_e_conferencia"),
        ({**DECISAO, "acao": "revisao_humana"}, None, "confirmar", "acao_nao_e_conferencia"),
        (DECISAO, "sim", "sem_desfecho", "conferencia_ja_decidida"),
    ],
)
async def test_recusa_no_banco_nao_grava_nada(
    decisao: dict[str, Any] | None, ultimo: str | None, clique: str, codigo: str
) -> None:
    conn = _Conn(decisao=decisao, ultimo=ultimo)
    quando = None if clique == "sem_desfecho" else AGORA
    with pytest.raises(reg.DesfechoRecusadoError) as erro:
        await _store(conn).registrar_desfecho(42, clique, desfecho_em=quando)
    assert erro.value.codigo == codigo
    assert "insert_desfecho" not in conn.log and "rollback" in conn.log and "close" in conn.log
    if codigo in {"decisao_inexistente", "acao_nao_e_conferencia"}:
        assert "lock" not in conn.log  # recusa antes de esperar o lock da regra


async def test_falha_de_banco_vira_indisponivel_sem_a_mensagem() -> None:
    segredo = "Failing row contains (AUTH-amh-1)"
    conn = _Conn(decisao=DECISAO, falha=ConnectionError(segredo))
    with pytest.raises(reg.RegistroRcoIndisponivelError) as erro:
        await _store(conn).registrar_desfecho(42, "confirmar", desfecho_em=AGORA)
    assert segredo not in str(erro.value) and "ConnectionError" in str(erro.value)
    assert erro.value.__cause__ is None
