"""A sombra do montador com um pin REAL `fixado` nao produz efeito (MZO-3, exigencias da coordenacao).

Um pacote K1 SINTETICO 2.1.0 (`tests/unit/platform/rco_k1_sintetico.py`, `montar` + `com_provas`) e
reescrito para ter UMA tabela, `rco_geral_prazos_conta`, cujas entradas sao as do recorte real do K1 e
cujo `entradas.json` e o recorte 1.2.0 (`rco_recorte.py`). O pin e carregado pelo leitor de producao
(`rco_pin.load_rco_rules_pin`) e o dicionario pelo de producao (`carregar_dicionario_entradas`); nada de
`_dicionario`/`_versao_k3_pinada` substituido (o pin AMH e o real do repositorio). O motor e o
`FakeDmnTransport`, que devolve a linha ADVERSA da tabela (`glosar`, classe `contas`): o pior caso.

Prova:
- o pin `fixado` expoe `manifesto`, `k1_dir` e `RcoPinnedTable.decisao`, e o dicionario carrega ponta a
  ponta (minor 4 da revisao);
- a saida do worker (variaveis de processo: decisao, pagamento, roteamento) e IDENTICA com e sem a
  sombra, pela funcao e pelo harness (MAJOR-1 e exigencia 1);
- com o indicador do K1 LIGADO e a regra `ativa_l2`, a sombra grava `modo=sombra` na auditoria e no
  registro, sem ordinal nem amostragem; o mesmo pin fora da sombra daria `modo=ativa` (controle);
- log, auditoria e registro so tem campos de uma lista de permissao: nada de chave de negocio, guia,
  lote, beneficiario ou prestador (exigencia 3).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.platform import rco_ativacao as at
from maezo.platform.rco_entradas import carregar_dicionario_entradas
from maezo.platform.rco_registro import AvaliacaoRegra, ContextoDecisao, montar_linha
from maezo.tools.workers import contas, rco_pin, rco_sombra
from maezo.tools.workers._audit_ctx import collect_dmn_versions, collect_rco_avaliacoes, record_dmn_version
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import DmnVersion, FakeDmnTransport
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness
from tests.unit.platform import rco_k1_sintetico as sint
from tests.unit.tools.workers import rco_recorte

CHAVE = "rco_geral_prazos_conta"
ORIGINAL = "geral_prazos_conta"
TOPICO = "operadora.contas.identify_glosa"
ROW_GLOSAR = {
    "decisao": "glosar",
    "motivoTab38": "1702",
    "idRegra": "PRAZO-0002",
    "fila": "faturamento",
    "classeAdverso": "contas",
}
REGRA_ADVERSA = ("PRAZO-0002", "1.0.1")
#: Identificadores sinteticos do caso: nenhum deles pode aparecer em log, auditoria ou registro.
LOTE = "LOTE-SINT-4242"
GUIA = "GUIA-SINT-5151"
PRESTADOR = "PRESTADOR-SINT-7777"
BENEFICIARIO = "BEN-SINT-9898"
CHAVE_NEGOCIO = f"CONTAS-tenant-sint-{GUIA}-CONTA-SINT-1"
PROIBIDOS = (LOTE, GUIA, PRESTADOR, BENEFICIARIO, CHAVE_NEGOCIO, "CONTA-SINT-1")
_TIPO_DMN = {"integer": "integer", "string": "string", "boolean": "boolean", "double": "double"}


def _montar_pacote_prazos(raiz: Path) -> None:
    """Pacote 2.1.0 valido com so `rco_geral_prazos_conta` (uma linha `liberar`, uma `glosar`)."""
    manifesto = sint.montar(raiz)
    rco = raiz / sint.RULES_DIR
    k1 = raiz / sint.K1_DIR
    for original in ("sint_a", "sint_b"):
        (rco / f"{original}.dmn").unlink()
    saidas = sint.SAIDAS_OBRIGATORIAS
    raw_dmn = sint.dmn_bytes(
        CHAVE,
        saidas,
        [
            ['"liberar"', '""', '"PRAZO-0001"', '""', '""'],
            ['"glosar"', '"1702"', '"PRAZO-0002"', '"faturamento"', '"contas"'],
        ],
    )
    (rco / f"{ORIGINAL}.dmn").write_bytes(raw_dmn)
    raw_entradas = (rco_recorte.RECORTE_1_2_0 / "entradas.json").read_bytes()
    (k1 / "entradas.json").write_bytes(raw_entradas)
    dic_json = json.loads(raw_entradas)
    por_nome = {e["nome_dmn"]: e for e in dic_json["entradas"]}
    nomes = rco_recorte.decisoes()[CHAVE].entradas
    decisao = {
        "arquivo": f"dmn/{ORIGINAL}.dmn",
        "caminho_consumidor": f"{sint.RULES_DIR}/{ORIGINAL}.dmn",
        "dominio": "regras-gerais",
        "entradas": [
            {
                "dominio_valores": por_nome[n]["dominio"],
                "nome": n,
                "nome_maezo": por_nome[n]["nome_maezo"],
                "nome_snake": por_nome[n]["nome_snake"],
                "origem": por_nome[n]["origem"],
                "typeRef": _TIPO_DMN[por_nome[n]["tipo"]],
            }
            for n in nomes
        ],
        "history_ttl": "P180D",
        "hit_policy": "UNIQUE",
        "id": CHAVE,
        "id_original": ORIGINAL,
        "regras": [
            sint._regra("PRAZO-0001", "1.0.0", 2, ("plena", False)),
            sint._regra("PRAZO-0002", "1.0.1", 2, ("plena", False)),
        ],
        "saidas": [{"nome": s, "nome_snake": s.lower(), "typeRef": "string"} for s in saidas],
        "sha256": sint.sha(raw_dmn),
    }
    manifesto["decisoes"] = [decisao]
    manifesto["dicionario_entradas"] = {
        "path": "entradas.json",
        "sha256": dic_json["sha256_conteudo"],
        "sha256_arquivo": sint.sha(raw_entradas),
        "versao": dic_json["versao"],
    }
    manifesto["exemplos_sinteticos"][0]["decisao"] = CHAVE
    lock = sint.lock(raiz)
    lock["artifacts"] = [{"path": decisao["caminho_consumidor"], "sha256": decisao["sha256"]}]
    sint.gravar_lock(raiz, lock)
    sint.regravar(raiz, manifesto)


@pytest.fixture
def pin_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> rco_pin.RcoRulesPin:
    _montar_pacote_prazos(tmp_path)
    pin = rco_pin.load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    at.limpar_cache_estados()
    return pin


@pytest.fixture
def flag_ligada_regra_ativa(monkeypatch: pytest.MonkeyPatch) -> None:
    """O pior caso de MAJOR-1: indicador do K1 LIGADO e a regra adversa em `ativa_l2`."""
    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", lambda: True)
    estados = at.EstadosManuais({REGRA_ADVERSA: "ativa_l2"})
    monkeypatch.setattr(rco_pin, "estados_em_cache", lambda: estados)


def _dmn_adverso() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register(CHAVE, [dict(ROW_GLOSAR)])
    return dmn


def _variaveis(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "tenant-sint",
        "numero_lote_tiss": LOTE,
        "numero_guia_tiss": GUIA,
        "numero_conta": "CONTA-SINT-1",
        "prestador_id": PRESTADOR,
        "beneficiario_pseudo_id": BENEFICIARIO,
        "competencia": "2026-10",
        "valor_apresentado_brl": 150.0,
        "linhas_conta_refs": [{"valor_apresentado_centavos": 15000, "valor_glosado_centavos": 1000}],
        "data_recebimento_lote": "2026-11-10",
        "data_vencimento": "2026-12-10",
        "tipo_lote": "internacao",
        "item_conforme_tabela": True,
        "documentacao_anexa": True,
    }
    base.update(extra)
    return base


# --- minor 4: o pin `fixado` real expoe o que a sombra le ------------------------------------------------


def test_pin_fixado_real_expoe_manifesto_k1_dir_e_decisao(
    pin_real: rco_pin.RcoRulesPin, tmp_path: Path
) -> None:
    assert pin_real.estado == rco_pin.ESTADO_FIXADO
    assert pin_real.manifesto is not None
    assert pin_real.k1_dir == tmp_path / sint.K1_DIR
    tabela = pin_real.tables[CHAVE]
    assert tabela.decisao is not None
    assert tabela.decisao.dominio == "regras-gerais"
    assert tabela.decisao.entradas == rco_recorte.decisoes()[CHAVE].entradas
    assert tabela.tolerancia_no_dmn == ()
    dicionario = carregar_dicionario_entradas(pin_real.manifesto, k1_dir=pin_real.k1_dir)
    assert dicionario.versao == "1.2.0"
    assert dicionario.sha256_conteudo == pin_real.manifesto.dicionario_entradas_sha256
    assert set(tabela.decisao.entradas) <= set(dicionario.entradas)


def test_sombra_ponta_a_ponta_com_pin_real(pin_real: rco_pin.RcoRulesPin) -> None:
    dmn = _dmn_adverso()
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes():
        desfecho = rco_sombra.avaliar(dmn, CHAVE, contas.fatos_prazo_conta_rco(contas.GlosaInput("t", "l")))
    assert desfecho == rco_sombra.AVALIADA
    assert versoes[CHAVE]["versaoDicionarioEntradas"] == "1.2.0"
    assert versoes[CHAVE]["origemAvaliacao"] == rco_sombra.ORIGEM_SOMBRA


# --- exigencia 1 e MAJOR-1: sem efeito, sempre `modo=sombra` -----------------------------------------------


@pytest.mark.parametrize("ligada", [False, True])
def test_saida_do_worker_identica_e_modo_sombra(
    pin_real: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch, ligada: bool
) -> None:
    if ligada:
        monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", lambda: True)
        monkeypatch.setattr(
            rco_pin, "estados_em_cache", lambda: at.EstadosManuais({REGRA_ADVERSA: "ativa_l2"})
        )
    sem_sombra = contas.identify_glosa_entry(_variaveis())
    dmn = _dmn_adverso()
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes() as avaliacoes:
        com_sombra = contas.identify_glosa_entry(_variaveis(), dmn=dmn)
    assert len(dmn.calls) == 1  # a tabela foi avaliada e devolveu a linha adversa
    assert com_sombra == sem_sombra  # nenhuma variavel de processo, decisao ou pagamento mudou
    entrada = versoes[CHAVE]
    assert "decisao" not in entrada  # a saida da tabela nao vai a auditoria
    assert entrada["idRegra"] == "PRAZO-0002"
    assert entrada["modo"] == "sombra"
    assert entrada["estadoManual"] == ("ativa_l2" if ligada else at.ESTADO_PADRAO)
    avaliacao = avaliacoes[CHAVE]
    assert (avaliacao.modo, avaliacao.sombra, avaliacao.tem_ordinal) == ("sombra", True, False)
    assert avaliacao.decisao == "glosar" and avaliacao.classe_adverso == "contas"
    linha = montar_linha(avaliacao, ContextoDecisao(tenant="amh", chave_idempotencia="h"), None)
    assert (linha.modo, linha.acao, linha.ordinal, linha.amostrada, linha.motivo_amostra) == (
        "sombra",
        "registrar_apenas",
        None,
        False,
        None,
    )


def test_controle_fora_da_sombra_o_mesmo_pin_daria_modo_ativa(
    pin_real: rco_pin.RcoRulesPin, flag_ligada_regra_ativa: None
) -> None:
    """Sem este controle o teste acima poderia passar com o indicador desligado por acidente."""
    versao = DmnVersion(key=CHAVE, id="x", version=1, deployment_id="d")
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes() as avaliacoes:
        record_dmn_version(versao, row=ROW_GLOSAR)
    assert versoes[CHAVE]["modo"] == "ativa"
    assert avaliacoes[CHAVE].tem_ordinal is True and avaliacoes[CHAVE].sombra is False
    with collect_dmn_versions() as versoes, collect_rco_avaliacoes() as avaliacoes:
        record_dmn_version(versao, row=ROW_GLOSAR, sombra=True)
    assert versoes[CHAVE]["modo"] == "sombra"
    assert avaliacoes[CHAVE].tem_ordinal is False


def test_sombra_nao_le_o_indicador(pin_real: rco_pin.RcoRulesPin, monkeypatch: pytest.MonkeyPatch) -> None:
    def nao_leia() -> bool:
        raise AssertionError("a sombra leu o indicador do K1")

    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", nao_leia)
    dmn = _dmn_adverso()
    with collect_dmn_versions() as versoes:
        contas.identify_glosa_entry(_variaveis(), dmn=dmn)
    assert versoes[CHAVE]["modo"] == "sombra"


class _Registro:
    def __init__(self) -> None:
        self.tenant = "amh"
        self.chamadas: list[tuple[AvaliacaoRegra, ContextoDecisao]] = []

    async def registrar(self, avaliacao: AvaliacaoRegra, contexto: ContextoDecisao) -> bool:
        self.chamadas.append((avaliacao, contexto))
        return True


def _harness(
    dmn: Any, registro: _Registro | None
) -> tuple[WorkerHarness, FakeWorkerTransport, FakeAuditSink]:
    transport = FakeWorkerTransport()
    sink = FakeAuditSink()
    harness = WorkerHarness(transport, worker_id="w", tenant="amh", audit_sink=sink, rco_decisoes=registro)
    harness.register_worker(
        FunctionWorker(TOPICO, functools.partial(contas.identify_glosa_entry, dmn=dmn)),
        variables=tuple(_variaveis()),
    )
    return harness, transport, sink


def _task() -> ExternalTask:
    return ExternalTask(
        task_id="task-sint-1",
        topic=TOPICO,
        process_instance_id="proc-sint-1",
        business_key=CHAVE_NEGOCIO,
        worker_id="w-1",
        variables=_variaveis(),
    )


async def test_harness_completa_igual_e_registra_so_sombra(
    pin_real: rco_pin.RcoRulesPin, flag_ligada_regra_ativa: None
) -> None:
    sem, t_sem, _ = _harness(None, None)
    await sem._handle(_task())
    registro = _Registro()
    com, t_com, sink = _harness(_dmn_adverso(), registro)
    with structlog.testing.capture_logs() as logs:
        await com._handle(_task())
    assert t_com.failures == [] and t_sem.failures == []
    assert [v for _, v in t_com.completed] == [v for _, v in t_sem.completed]
    [(avaliacao, _ctx)] = registro.chamadas
    assert (avaliacao.modo, avaliacao.sombra, avaliacao.tem_ordinal) == ("sombra", True, False)
    entrada = sink.emitted[0][0].dmn_versions[CHAVE]
    _assert_auditoria_permitida(entrada)
    _assert_logs_permitidos(logs)
    linha = montar_linha(avaliacao, _ctx, None)
    assert not any(p in repr(linha.params()) for p in PROIBIDOS)
    assert not any(p in repr(avaliacao) for p in PROIBIDOS)


# --- exigencia 3: so campos permitidos ---------------------------------------------------------------------

#: Chaves permitidas na entrada `rco_` de `dmn_versions` escrita pela sombra.
CHAVES_AUDITORIA: frozenset[str] = frozenset(
    {
        "version",
        "id",
        "deploymentId",
        "shaDmn",
        "idRegra",
        "versaoRegra",
        "ativacaoPacote",
        "exigeAditivo",
        "estadoManual",
        "ativacaoEfetiva",
        "modo",
        "derivacoes",
        "entradasNulas",
        "versaoDicionarioEntradas",
        "origemAvaliacao",
    }
)
CHAVES_DERIVACAO: frozenset[str] = frozenset(
    {"diasBrutos", "toleranciaAplicada", "valorEntregue", "motivoTolerancia"}
)
#: Campos permitidos nos eventos de log da sombra.
CAMPOS_LOG: frozenset[str] = frozenset({"event", "log_level", "decision_key", "desfecho", "topico", "erro"})


def _assert_auditoria_permitida(entrada: dict[str, Any]) -> None:
    assert set(entrada) <= CHAVES_AUDITORIA, set(entrada) - CHAVES_AUDITORIA
    for nome, registro in entrada["derivacoes"].items():
        assert set(registro) <= CHAVES_DERIVACAO, nome
        assert all(v is None or isinstance(v, int | str) for v in registro.values())
    assert all(isinstance(v, str) for v in entrada["entradasNulas"].values())
    for chave in set(entrada) - {"derivacoes", "entradasNulas", "version"}:
        assert isinstance(entrada[chave], str), chave
    assert not any(p in json.dumps(entrada) for p in PROIBIDOS)


def _assert_logs_permitidos(logs: list[dict[str, Any]]) -> None:
    da_sombra = [e for e in logs if str(e["event"]).startswith("rco_sombra")]
    assert da_sombra
    for evento in da_sombra:
        assert set(evento) <= CAMPOS_LOG, set(evento) - CAMPOS_LOG
        assert not any(p in repr(evento) for p in PROIBIDOS)


def test_auditoria_e_log_da_sombra_so_com_campos_permitidos(pin_real: rco_pin.RcoRulesPin) -> None:
    dmn = _dmn_adverso()
    with (
        collect_dmn_versions() as versoes,
        collect_rco_avaliacoes(),
        structlog.testing.capture_logs() as logs,
    ):
        contas.identify_glosa_entry(_variaveis(), dmn=dmn)
    _assert_auditoria_permitida(versoes[CHAVE])
    _assert_logs_permitidos(logs)
    # o motor tambem nao recebe identificador do caso: so as entradas do dicionario
    [(_, enviadas)] = dmn.calls
    assert set(enviadas) == set(rco_recorte.decisoes()[CHAVE].entradas)
    assert not any(p in repr(enviadas) for p in PROIBIDOS)
