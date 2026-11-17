"""5e-0 (DL-0181, emenda ao ADR-0077 §7): o pacote K1 2.2.0 REAL do rco pinado, com toda ativacao do
contrato aceita e tudo em sombra.

Usa o recorte vendorizado (`tests/fixtures/rco/k1-2.2.0-recorte/`, rco a1978375): o manifesto inteiro e
`vocabulario.json`, mas so tres dos 236 DMN e sem `entradas.json`. Por isso a carga completa
(`rco_pin.load_rco_rules_pin`, que re-hasheia todo `artifacts`) nao roda aqui. O pin e montado pelo
MESMO caminho que ela segue depois de validar os arquivos:
- `ler_manifesto` (leitor estrito do gate e do runtime) sobre os bytes reais;
- as duas recusas de pin sobre o manifesto: `ativacao_nao_suportada` e `provas_ausentes_no_pin`, ambas
  vazias;
- `carregar_vocabulario` real (parametros de ativacao do pacote: revisao inicial e conferencia de 30);
- uma `RcoPinnedTable` por decisao do manifesto, com `regras`, `sha256` e `caminho_consumidor` dela,
  como `load_rco_rules_pin` monta.

Cada caso passa pelo caminho de runtime inteiro: `rco_pin.rco_avaliacao` (le o pin, o estado manual e o
indicador) -> `rco_registro.montar_linha` -> `rco_ativacao.filtrar_saida`. O indicador real
(`efeito_k1_habilitado`) nunca e alterado: desligado ele e lido do ambiente sem a chave; "ligado" e
simulado so neste processo, trocando a funcao no modulo `rco_pin`, como em
`tests/unit/tools/workers/test_harness_rco_registro.py`.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any

import pytest

from maezo.platform import rco_ativacao as at
from maezo.platform import rco_manifesto as k1
from maezo.platform import rco_registro as reg
from maezo.tools.workers import rco_pin
from tests.unit.platform import rco_k1_2_2_recorte as rec

FILA = "cadastro"
CONFERIDA = ("rco_geral_elegibilidade", "OMNI-GERAL-ELG-0001")
COM_ADITIVO = (
    ("rco_mex_ortop", "OMNI-ORTOP-MEX-0001"),
    ("rco_mex_ortop_joelho_curativo", "OMNI-ORTOP-MEX-0002"),
    ("rco_mex_ortop_joelho_curativo", "OMNI-ORTOP-MEX-0007"),
)


def _classes() -> dict[tuple[str, str], str]:
    """`classe_adverso` por (decisao, idRegra), do manifesto real (o `RegraK1` nao a guarda)."""
    bruto = json.loads(rec.manifesto_raw())
    return {(d["id"], r["idRegra"]): r["classe_adverso"] for d in bruto["decisoes"] for r in d["regras"]}


def _antes_recusadas(manifesto: k1.ManifestoK1) -> list[tuple[str, k1.RegraK1]]:
    """O criterio da recusa que o 5e-0 retirou (so `plena` sem aditivo passava), recalculado do manifesto."""
    return [
        (d.id, r)
        for d in manifesto.decisoes.values()
        for r in d.regras.values()
        if r.ativacao != "plena" or r.exige_aditivo is not False
    ]


def _pin(manifesto: k1.ManifestoK1) -> rco_pin.RcoRulesPin:
    assert k1.ativacao_nao_suportada(manifesto) == []
    assert k1.provas_ausentes_no_pin(manifesto) == []
    vocab = k1.carregar_vocabulario(manifesto, k1_dir=rec.RECORTE)
    tabelas = {
        d.id: rco_pin.RcoPinnedTable(d.id, d.caminho_consumidor, d.sha256, regras=d.regras, decisao=d)
        for d in manifesto.decisoes.values()
    }
    return rco_pin.RcoRulesPin(
        estado=rco_pin.ESTADO_FIXADO,
        tables=MappingProxyType(tabelas),
        parametros_ativacao=vocab.parametros_ativacao,
        manifesto=manifesto,
        k1_dir=rec.RECORTE,
    )


def _com_regra(manifesto: k1.ManifestoK1, decisao_id: str, regra: k1.RegraK1) -> k1.ManifestoK1:
    decisao = manifesto.decisoes[decisao_id]
    nova = dataclasses.replace(decisao, regras=MappingProxyType({**decisao.regras, regra.id_regra: regra}))
    return dataclasses.replace(manifesto, decisoes=MappingProxyType({**manifesto.decisoes, decisao_id: nova}))


@dataclasses.dataclass
class _Runtime:
    """O pin real montado, o estado manual e o espiao de `filtrar_saida` (a marca nao vai para a linha)."""

    monkeypatch: pytest.MonkeyPatch
    acoes: list[at.AcaoDeSaida] = dataclasses.field(default_factory=list)

    def pinar(self, manifesto: k1.ManifestoK1) -> rco_pin.RcoRulesPin:
        pin = _pin(manifesto)
        self.monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
        return pin

    def estados(self, ativas: list[tuple[str, str]]) -> None:
        estados = at.EstadosManuais(dict.fromkeys(ativas, "ativa_l2"))
        self.monkeypatch.setattr(rco_pin, "estados_em_cache", lambda: estados)

    def decidir(
        self, decisao_id: str, id_regra: str, decisao: str, classe: str, ordinal: int = 1
    ) -> tuple[reg.LinhaDecisao, at.AcaoDeSaida]:
        row = {
            "idRegra": id_regra,
            "decisao": decisao,
            "classeAdverso": classe,
            "fila": FILA,
            "motivoTab38": "1801",
        }
        campos, av = rco_pin.rco_avaliacao(decisao_id, row)
        assert av is not None, f"{decisao_id}/{id_regra}: sem avaliacao de regra MAJOR 2"
        assert campos["ativacaoPacote"] == av.ativacao_pacote
        ctx = reg.contexto_da_tarefa(
            tenant="amh", task_id="t-1", decision_key=decisao_id, business_key=None, ambiente={}
        )
        linha = reg.montar_linha(av, ctx, ordinal if av.tem_ordinal else None)
        return linha, self.acoes[-1]


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Runtime]:
    rt = _Runtime(monkeypatch)
    original = at.filtrar_saida

    def espiao(*args: Any, **kwargs: Any) -> at.AcaoDeSaida:
        acao = original(*args, **kwargs)
        rt.acoes.append(acao)
        return acao

    monkeypatch.setattr(at, "filtrar_saida", espiao)
    at.limpar_cache_estados()
    yield rt
    at.limpar_cache_estados()


@pytest.fixture
def efeito_ligado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula o indicador ligado SO neste processo de teste; config e ambiente reais nao mudam."""
    monkeypatch.setattr(rco_pin, "efeito_k1_habilitado", lambda: True)


# --- pin aceito --------------------------------------------------------------------------------------


def test_pacote_real_tem_46_regras_que_eram_recusadas_e_hoje_passam_no_pin() -> None:
    manifesto = rec.manifesto()
    antes = _antes_recusadas(manifesto)
    assert len(antes) == 46
    por_tipo: dict[tuple[str | None, bool | None], int] = {}
    for _, regra in antes:
        por_tipo[(regra.ativacao, regra.exige_aditivo)] = (
            por_tipo.get((regra.ativacao, regra.exige_aditivo), 0) + 1
        )
    assert por_tipo == {("plena", True): 43, ("conferida", False): 3}
    assert k1.ativacao_nao_suportada(manifesto) == []
    pin = _pin(manifesto)
    assert pin.parametros_ativacao is not None
    assert pin.parametros_ativacao.conferencia_negativa_admin_decisoes == 30


# --- 1. efeito desligado: tudo em sombra ------------------------------------------------------------


@pytest.mark.parametrize("arquivo_diz_ativa", [False, True], ids=["estado-sombra", "estado-ativa_l2"])
def test_efeito_desligado_toda_saida_adversa_das_46_e_registrar_apenas(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch, arquivo_diz_ativa: bool
) -> None:
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    assert at.efeito_k1_habilitado() is False  # o indicador real, sem simulacao
    manifesto = rec.manifesto()
    runtime.pinar(manifesto)
    antes = _antes_recusadas(manifesto)
    runtime.estados([(r.id_regra, r.versao_regra) for _, r in antes] if arquivo_diz_ativa else [])
    classes = _classes()
    vistos: set[tuple[str, str]] = set()
    for decisao_id, regra in antes:
        classe_real = classes[(decisao_id, regra.id_regra)]
        for decisao in sorted(at.DECISOES_ADVERSAS):
            for classe in sorted({classe_real or "contas", "administrativo", "clinico"}):
                for ordinal in (1, 30, 31):
                    linha, acao = runtime.decidir(decisao_id, regra.id_regra, decisao, classe, ordinal)
                    onde = f"{decisao_id}/{regra.id_regra} {decisao}/{classe} #{ordinal}"
                    assert (linha.modo, linha.acao) == ("sombra", "registrar_apenas"), onde
                    assert acao == at.AcaoDeSaida("registrar_apenas"), onde
                    assert linha.ativacao_pacote == regra.ativacao, onde
                    vistos.add((decisao_id, regra.id_regra))
    assert len(vistos) == 46


# --- 2. efeito ligado (simulado): as rotas do ADR-0077 §3 -------------------------------------------


@pytest.mark.usefixtures("efeito_ligado")
@pytest.mark.parametrize("decisao", ["negar", "glosar"])
def test_conferida_real_confere_ate_o_ordinal_30(runtime: _Runtime, decisao: str) -> None:
    manifesto = rec.manifesto()
    decisao_id, id_regra = CONFERIDA
    regra = manifesto.decisoes[decisao_id].regras[id_regra]
    assert (regra.ativacao, regra.exige_aditivo) == ("conferida", False)
    assert _classes()[CONFERIDA] == "administrativo"
    runtime.pinar(manifesto)
    runtime.estados([(id_regra, regra.versao_regra)])
    for ordinal in (1, 30):
        linha, acao = runtime.decidir(decisao_id, id_regra, decisao, "administrativo", ordinal)
        assert (linha.modo, linha.ativacao_efetiva, linha.acao, linha.fila) == (
            "ativa",
            "conferida",
            "conferencia",
            FILA,
        )
        assert acao == at.AcaoDeSaida("conferencia", FILA)
    linha, _ = runtime.decidir(decisao_id, id_regra, decisao, "administrativo", 31)
    assert linha.acao == "efeito_com_amostragem"


@pytest.mark.usefixtures("efeito_ligado")
@pytest.mark.parametrize("decisao", sorted(at.DECISOES_ADVERSAS))
def test_em_correcao_adversa_vai_a_revisao_humana(runtime: _Runtime, decisao: str) -> None:
    # O 2.2.0 nao tem regra `em_correcao`: a regra real conferida recebe essa ativacao (o resto e real).
    decisao_id, id_regra = CONFERIDA
    manifesto = rec.manifesto()
    regra = dataclasses.replace(manifesto.decisoes[decisao_id].regras[id_regra], ativacao="em_correcao")
    runtime.pinar(_com_regra(manifesto, decisao_id, regra))
    runtime.estados([(id_regra, regra.versao_regra)])
    for ordinal in (1, 31):
        linha, acao = runtime.decidir(decisao_id, id_regra, decisao, "administrativo", ordinal)
        assert (linha.ativacao_efetiva, linha.acao) == ("em_correcao", "revisao_humana")
        assert acao == at.AcaoDeSaida("revisao_humana", FILA, at.MARCA_EM_CORRECAO)


@pytest.mark.usefixtures("efeito_ligado")
@pytest.mark.parametrize("decisao", sorted(at.DECISOES_NAO_ADVERSAS))
def test_em_correcao_favoravel_nao_e_bloqueada(runtime: _Runtime, decisao: str) -> None:
    decisao_id, id_regra = CONFERIDA
    manifesto = rec.manifesto()
    regra = dataclasses.replace(manifesto.decisoes[decisao_id].regras[id_regra], ativacao="em_correcao")
    runtime.pinar(_com_regra(manifesto, decisao_id, regra))
    runtime.estados([(id_regra, regra.versao_regra)])
    linha, acao = runtime.decidir(decisao_id, id_regra, decisao, "")
    assert (linha.ativacao_efetiva, linha.acao, linha.ordinal) == ("em_correcao", "efeito", None)
    assert acao == at.AcaoDeSaida("efeito")


@pytest.mark.usefixtures("efeito_ligado")
@pytest.mark.parametrize(("decisao_id", "id_regra"), COM_ADITIVO)
@pytest.mark.parametrize("decisao", sorted(at.DECISOES_ADVERSAS))
def test_exige_aditivo_sem_aditivo_vai_a_revisao_humana(
    runtime: _Runtime, decisao_id: str, id_regra: str, decisao: str
) -> None:
    manifesto = rec.manifesto()
    regra = manifesto.decisoes[decisao_id].regras[id_regra]
    assert (regra.ativacao, regra.exige_aditivo) == ("plena", True)
    classe = _classes()[(decisao_id, id_regra)]
    assert classe == "contas"
    runtime.pinar(manifesto)
    runtime.estados([(id_regra, regra.versao_regra)])
    linha, acao = runtime.decidir(
        decisao_id, id_regra, decisao, classe
    )  # o registro passa aditivo_cobre=None
    assert (linha.modo, linha.ativacao_efetiva, linha.acao) == ("ativa", "plena", "revisao_humana")
    assert acao == at.AcaoDeSaida("revisao_humana", FILA, at.MARCA_SEM_ADITIVO)


@pytest.mark.usefixtures("efeito_ligado")
def test_efeito_ligado_sem_ativa_l2_no_arquivo_continua_sombra(runtime: _Runtime) -> None:
    decisao_id, id_regra = COM_ADITIVO[0]
    runtime.pinar(rec.manifesto())
    runtime.estados([])
    linha, acao = runtime.decidir(decisao_id, id_regra, "glosar", "contas")
    assert (linha.modo, linha.acao) == ("sombra", "registrar_apenas")
    assert acao == at.AcaoDeSaida("registrar_apenas")


# --- o que continua recusado --------------------------------------------------------------------------


@pytest.mark.parametrize("valor", ["automatica", "PLENA", None, ["plena"]])
def test_ativacao_fora_do_vocabulario_no_manifesto_real_e_recusada_pelo_leitor(valor: object) -> None:
    bruto: dict[str, Any] = json.loads(rec.manifesto_raw())
    alvo: Mapping[str, Any] = next(d for d in bruto["decisoes"] if d["id"] == CONFERIDA[0])
    next(r for r in alvo["regras"] if r["idRegra"] == CONFERIDA[1])["ativacao"] = valor
    with pytest.raises(k1.ManifestoK1InvalidoError, match="ativacao"):
        k1.ler_manifesto(k1.bytes_canonicos(bruto))


def test_exige_aditivo_nao_booleano_no_manifesto_real_e_recusado_pelo_leitor() -> None:
    bruto: dict[str, Any] = json.loads(rec.manifesto_raw())
    bruto["decisoes"][0]["regras"][0]["exige_aditivo"] = "sim"
    with pytest.raises(k1.ManifestoK1InvalidoError, match="exige_aditivo"):
        k1.ler_manifesto(k1.bytes_canonicos(bruto))
