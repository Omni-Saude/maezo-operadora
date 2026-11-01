"""MPTT P5 — `prestador_trust_scorecard.dmn` (COLLECT SUM + motivos) e `prestador_tier.dmn` (FIRST).

Fonte do desenho: `docs/prompts/fcp-protocol-2026-10-30/analyses/PROVIDER-trust-tiers.md` §3/§4/§8-P5
e a decisao do dono de 2026-10-31 ("Pagamentos": scorecard hibrido = red flags nao compensatorias
+ scorecard de pontos; gold-card >= 90% em >= 10 ordens; pares por tenant/CADE; novos em WATCH ate
N pagamentos; custo de episodio lake-side, so rebaixa, v2).

O QUE ESTE ARQUIVO PROVA (engenharia, sem motor):
  1. forma: hitPolicy/aggregation, entradas e saidas EXATAS das tres decisoes;
  2. a decisao de motivos tem as MESMAS linhas da decisao de pontos, na mesma ordem, e
     `pontos_perdidos == peso_da_feature - pontos` linha a linha (as duas nao podem divergir);
  3. cada linha toca UMA feature, e para cada feature exatamente UMA linha casa em toda a grade de
     sondagem (null, negativo, fronteiras, fora de faixa) — a soma COLLECT tem sempre 3 parcelas;
  4. o tier e fail-closed: qualquer entrada nula, pontuacao fora da escala, red flag ou prestador
     novo da WATCH; TRUSTED exige o portao gold-card mesmo com pontuacao maxima;
  5. guardas negativas: dominio do tier fechado (nada de BLOQUEIO/NEGAR/RECUSAR), nenhuma saida
     de valor/teto (alcada fica na politica), nenhuma entrada de beneficiario/clinica (PHI, F4
     fora), benchmark de pares SO do tenant (CADE);
  6. os valores sao DRAFT: as duas tabelas estao em `pendentes` do registro de aprovacoes, na
     fila de revisao, e nao ha consumidor (BPMN/src) — a ligacao e do P6.

O avaliador abaixo e um LEITOR de teste com gramatica fechada (`-`, `null`, `true`/`false`,
comparacoes unarias, intervalos FEEL, disjuncao por virgula) que LEVANTA em qualquer outra forma.
Nao e o motor (ADR-0028): a avaliacao autoritativa e a do CIB Seven, provada em
`tests/integration/dmn/test_prestador_trust_tier_engine.py` (marcado `integration`, roda no CI).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from xml.etree.ElementTree import Element

import pytest
import yaml

from maezo.platform.xml_safe import parse_xml_without_dtd

_ROOT: Final = Path(__file__).resolve().parents[3]
_DMN_DIR: Final = _ROOT / "spec" / "processes" / "dmn"
_SCORECARD: Final = _DMN_DIR / "prestador_trust_scorecard.dmn"
_TIER: Final = _DMN_DIR / "prestador_tier.dmn"
_NS: Final = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}

#: Pesos DRAFT (analise §3: F1 40, F2 30, F5 15; F3 15 entra com o PAGTO PR-E). Mudar um peso e um
#: ato deliberado: este pin, a tabela e o registro DRAFT mudam juntos.
_PESOS: Final = {"f1": 40, "f2": 30, "f5": 15}
_PONTUACAO_MAXIMA: Final = sum(_PESOS.values())

_ENTRADAS_SCORECARD: Final = (
    ("f1_ordens_decididas", "integer"),
    ("f1_taxa_liberacao_humana", "double"),
    ("f2_lotes_janela", "integer"),
    ("f2_percentil_glosa_liquida_pares_tenant", "double"),
    ("f5_meses_relacionamento", "integer"),
)
_ENTRADAS_TIER: Final = (
    ("pontuacao_confianca", "integer"),
    ("f1_ordens_decididas", "integer"),
    ("f1_taxa_liberacao_humana", "double"),
    ("f5_meses_relacionamento", "integer"),
    ("pagamentos_liberados_humanos", "integer"),
    ("red_flag_pico_faturamento", "boolean"),
    ("fraude_monitorar_12m", "boolean"),
    ("f2_lotes_janela", "integer"),
    ("f2_percentil_glosa_liquida_pares_tenant", "double"),
)
_DOMINIO_TIER: Final = frozenset({"TRUSTED", "STANDARD", "WATCH"})

# -------------------------------------------------------------------------------------------------
# Leitor de teste (gramatica fechada)
# -------------------------------------------------------------------------------------------------

_INTERVALO: Final = re.compile(
    r"^(?P<abre>[\[(])\s*(?P<lo>-?\d+(?:\.\d+)?)\s*\.\.\s*(?P<hi>-?\d+(?:\.\d+)?)\s*(?P<fecha>[\])])$"
)
_COMPARACAO: Final = re.compile(r"^(?P<op>>=|<=|>|<)\s*(?P<n>-?\d+(?:\.\d+)?)$")


class EntradaIlegivelError(RuntimeError):
    """Forma de entrada fora da gramatica do leitor — nunca lida como "nao casa"."""


def _casa_um(teste: str, valor: Any) -> bool:
    teste = teste.strip()
    if teste == "null":
        return valor is None
    if teste in ("true", "false"):
        if valor is None:
            return False
        return valor is (teste == "true")
    numero = valor is not None and not isinstance(valor, bool) and isinstance(valor, int | float)
    if (cmp := _COMPARACAO.match(teste)) is not None:
        if not numero:
            return False
        n = float(cmp["n"])
        return bool({">=": valor >= n, "<=": valor <= n, ">": valor > n, "<": valor < n}[cmp["op"]])
    if (itv := _INTERVALO.match(teste)) is not None:
        if not numero:
            return False
        lo, hi = float(itv["lo"]), float(itv["hi"])
        acima = valor >= lo if itv["abre"] == "[" else valor > lo
        abaixo = valor <= hi if itv["fecha"] == "]" else valor < hi
        return bool(acima and abaixo)
    raise EntradaIlegivelError(f"entrada fora da gramatica do leitor: {teste!r}")


def casa(entrada: str, valor: Any) -> bool:
    texto = entrada.strip()
    if texto == "-":
        return True
    return any(_casa_um(parte, valor) for parte in texto.split(","))


def _saida(texto: str) -> Any:
    texto = texto.strip()
    if texto.startswith('"') and texto.endswith('"'):
        return texto[1:-1]
    if re.fullmatch(r"-?\d+", texto):
        return int(texto)
    raise EntradaIlegivelError(f"saida fora da gramatica do leitor: {texto!r}")


@dataclass(frozen=True, slots=True)
class Regra:
    rid: str
    entradas: tuple[str, ...]
    saidas: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class Tabela:
    decisao: str
    hit_policy: str
    aggregation: str | None
    entradas: tuple[tuple[str, str], ...]
    saidas: tuple[tuple[str, str], ...]
    regras: tuple[Regra, ...]

    def casadas(self, valores: dict[str, Any]) -> list[Regra]:
        nomes = tuple(nome for nome, _ in self.entradas)
        assert tuple(valores) == nomes, f"{self.decisao}: entradas devem ser exatamente {nomes}"
        return [
            regra
            for regra in self.regras
            if all(casa(e, valores[n]) for e, n in zip(regra.entradas, nomes, strict=True))
        ]


def _texto(el: Element | None) -> str:
    assert el is not None
    return (el.findtext("d:text", "", _NS) or "").strip()


def _tabelas(path: Path) -> dict[str, Tabela]:
    root = parse_xml_without_dtd(path.read_bytes())
    out: dict[str, Tabela] = {}
    for decisao in root.findall("d:decision", _NS):
        dt = decisao.find("d:decisionTable", _NS)
        assert dt is not None
        entradas = tuple(
            (_texto(i.find("d:inputExpression", _NS)), i.find("d:inputExpression", _NS).get("typeRef", ""))  # type: ignore[union-attr]
            for i in dt.findall("d:input", _NS)
        )
        saidas = tuple((o.get("name", ""), o.get("typeRef", "")) for o in dt.findall("d:output", _NS))
        regras = tuple(
            Regra(
                rid=r.get("id", ""),
                entradas=tuple(_texto(e) for e in r.findall("d:inputEntry", _NS)),
                saidas=tuple(_saida(_texto(o)) for o in r.findall("d:outputEntry", _NS)),
            )
            for r in dt.findall("d:rule", _NS)
        )
        out[decisao.get("id", "")] = Tabela(
            decisao=decisao.get("id", ""),
            hit_policy=dt.get("hitPolicy", "UNIQUE"),
            aggregation=dt.get("aggregation"),
            entradas=entradas,
            saidas=saidas,
            regras=regras,
        )
    return out


_SC: Final = _tabelas(_SCORECARD)
_PONTOS: Final = _SC["prestador_trust_scorecard"]
_MOTIVOS: Final = _SC["prestador_trust_motivos"]
_TIER_T: Final = _tabelas(_TIER)["prestador_tier"]


def pontuacao(**valores: Any) -> int:
    """COLLECT + SUM: soma a unica saida das linhas casadas (o que o motor faz)."""
    casadas = _PONTOS.casadas(valores)
    assert casadas, "COLLECT SUM sem linha casada: o motor devolveria resultado vazio"
    return sum(int(r.saidas[0]) for r in casadas)


def motivos(**valores: Any) -> list[tuple[str, int]]:
    return [(str(r.saidas[0]), int(r.saidas[1])) for r in _MOTIVOS.casadas(valores)]


def tier(**valores: Any) -> tuple[str, str]:
    casadas = _TIER_T.casadas(valores)
    assert casadas, "FIRST sem linha casada: falta catch-all"
    return str(casadas[0].saidas[0]), str(casadas[0].saidas[1])


def _sc(
    ordens: Any = 50, taxa: Any = 0.95, lotes: Any = 50, percentil: Any = 30.0, meses: Any = 24
) -> dict[str, Any]:
    return {
        "f1_ordens_decididas": ordens,
        "f1_taxa_liberacao_humana": taxa,
        "f2_lotes_janela": lotes,
        "f2_percentil_glosa_liquida_pares_tenant": percentil,
        "f5_meses_relacionamento": meses,
    }


def _ti(**sobrescritas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pontuacao_confianca": 85,
        "f1_ordens_decididas": 50,
        "f1_taxa_liberacao_humana": 0.95,
        "f5_meses_relacionamento": 24,
        "pagamentos_liberados_humanos": 40,
        "red_flag_pico_faturamento": False,
        "fraude_monitorar_12m": False,
        "f2_lotes_janela": 50,
        "f2_percentil_glosa_liquida_pares_tenant": 30.0,
    }
    base.update(sobrescritas)
    return base


# -------------------------------------------------------------------------------------------------
# 1. Forma
# -------------------------------------------------------------------------------------------------


def test_scorecard_e_collect_sum_com_uma_saida_inteira() -> None:
    assert (_PONTOS.hit_policy, _PONTOS.aggregation) == ("COLLECT", "SUM")
    assert _PONTOS.entradas == _ENTRADAS_SCORECARD
    # O motor (Camunda 7 / CIB Seven) recusa agregacao sobre saida composta: UMA coluna so.
    assert _PONTOS.saidas == (("pontuacao_confianca", "integer"),)


def test_motivos_e_collect_sem_agregacao() -> None:
    assert (_MOTIVOS.hit_policy, _MOTIVOS.aggregation) == ("COLLECT", None)
    assert _MOTIVOS.entradas == _ENTRADAS_SCORECARD
    assert _MOTIVOS.saidas == (("motivo_codigo", "string"), ("pontos_perdidos", "integer"))


def test_arquivo_do_scorecard_tem_exatamente_as_duas_decisoes() -> None:
    assert set(_SC) == {"prestador_trust_scorecard", "prestador_trust_motivos"}


def test_tier_e_first_com_entradas_e_saidas_exatas() -> None:
    assert _TIER_T.hit_policy == "FIRST"
    assert _TIER_T.entradas == _ENTRADAS_TIER
    assert _TIER_T.saidas == (("prestador_tier", "string"), ("motivo_tier", "string"))


@pytest.mark.parametrize("path", [_SCORECARD, _TIER], ids=lambda p: p.name)
def test_status_draft_declarado_no_arquivo(path: Path) -> None:
    texto = path.read_text(encoding="utf-8")
    versao = "v0.1.1" if path == _TIER else "v0.1.0"  # tier: reparo B1 (fail-closed em entrada invalida)
    assert f"{versao} — status: DRAFT" in texto
    assert "NAO LIGADA a BPMN nem a worker" in texto


# -------------------------------------------------------------------------------------------------
# 2. Paridade pontos x motivos
# -------------------------------------------------------------------------------------------------


def test_motivos_espelham_as_linhas_de_pontos() -> None:
    assert len(_MOTIVOS.regras) == len(_PONTOS.regras)
    for p, m in zip(_PONTOS.regras, _MOTIVOS.regras, strict=True):
        assert m.rid == "m_" + p.rid.removeprefix("r_"), (p.rid, m.rid)
        assert m.entradas == p.entradas, p.rid
        feature = p.rid.removeprefix("r_")[:2]
        assert m.saidas[1] == _PESOS[feature] - p.saidas[0], p.rid
        assert str(m.saidas[0]).startswith(feature.upper() + "_"), p.rid


def test_cada_linha_toca_so_a_sua_feature() -> None:
    nomes = [n for n, _ in _ENTRADAS_SCORECARD]
    for regra in _PONTOS.regras:
        feature = regra.rid.removeprefix("r_")[:2]
        assert feature in _PESOS, regra.rid
        for nome, entrada in zip(nomes, regra.entradas, strict=True):
            if not nome.startswith(feature + "_"):
                assert entrada == "-", f"{regra.rid} restringe {nome} de outra feature"


def test_pontos_nunca_excedem_o_peso_e_o_maximo_e_atingivel() -> None:
    for regra in _PONTOS.regras:
        feature = regra.rid.removeprefix("r_")[:2]
        assert 0 <= regra.saidas[0] <= _PESOS[feature], regra.rid
    for feature, peso in _PESOS.items():
        assert max(r.saidas[0] for r in _PONTOS.regras if r.rid.startswith(f"r_{feature}_")) == peso


# -------------------------------------------------------------------------------------------------
# 3. Exaustividade e disjuncao por feature
# -------------------------------------------------------------------------------------------------

_SONDA_CONTAGEM: Final = (None, -1, 0, 1, 9, 10, 11, 500)
_SONDA_TAXA: Final = (None, -0.01, 0, 0.5, 0.7999, 0.8, 0.85, 0.8999, 0.9, 0.95, 1, 1.01)
_SONDA_PERCENTIL: Final = (None, -1, 0, 25, 50, 50.01, 75, 75.01, 99, 100, 100.5)
_SONDA_MESES: Final = (None, -1, 0, 11, 12, 13, 240)


@pytest.mark.parametrize(
    ("ordens", "taxa", "lotes", "percentil", "meses"),
    [
        *[(o, t, 50, 30.0, 24) for o, t in itertools.product(_SONDA_CONTAGEM, _SONDA_TAXA)],
        *[(50, 0.95, lo, pc, 24) for lo, pc in itertools.product(_SONDA_CONTAGEM, _SONDA_PERCENTIL)],
        *[(50, 0.95, 50, 30.0, me) for me in _SONDA_MESES],
    ],
)
def test_exatamente_uma_faixa_por_feature(
    ordens: Any, taxa: Any, lotes: Any, percentil: Any, meses: Any
) -> None:
    casadas = _PONTOS.casadas(_sc(ordens, taxa, lotes, percentil, meses))
    por_feature = sorted(r.rid.removeprefix("r_")[:2] for r in casadas)
    assert por_feature == ["f1", "f2", "f5"], [r.rid for r in casadas]


# -------------------------------------------------------------------------------------------------
# 3b. Pontuacao — cenarios
# -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valores", "esperado"),
    [
        (_sc(), _PONTUACAO_MAXIMA),
        (_sc(None, None, None, None, None), 0),  # tudo ausente -> 0 (fail-closed)
        (_sc(0, None, 0, None, 0), 20 + 15 + 0),  # prestador novo: F1/F2 neutros, F5 curto
        (_sc(ordens=9, taxa=1.0), 20 + 30 + 15),  # 9 ordens nunca e gold-card: neutro
        (_sc(taxa=0.8999), 20 + 30 + 15),
        (_sc(taxa=0.79), 0 + 30 + 15),
        (_sc(ordens=10, taxa=0.9), 40 + 30 + 15),  # fronteira inclusiva do gold-card
        (_sc(percentil=50.0), 40 + 30 + 15),
        (_sc(percentil=60.0), 40 + 15 + 15),
        (_sc(percentil=90.0), 40 + 0 + 15),
        (_sc(meses=11), 40 + 30 + 0),
        (_sc(taxa=1.2), 0 + 30 + 15),  # taxa invalida nunca soma
        (_sc(ordens=50, taxa=None), 0 + 30 + 15),  # volume sem taxa nunca soma
    ],
)
def test_pontuacao(valores: dict[str, Any], esperado: int) -> None:
    assert pontuacao(**valores) == esperado


def test_motivos_ordenaveis_por_pontos_perdidos() -> None:
    lista = motivos(**_sc(ordens=50, taxa=0.85, lotes=50, percentil=90.0, meses=3))
    assert sorted(lista, key=lambda m: -m[1]) == [
        ("F2_GLOSA_LIQUIDA_ACIMA_P75", 30),
        ("F1_LIBERACAO_ABAIXO_DE_90", 20),
        ("F5_RELACIONAMENTO_CURTO", 15),
    ]
    assert sum(p for _, p in lista) == _PONTUACAO_MAXIMA - pontuacao(
        **_sc(ordens=50, taxa=0.85, lotes=50, percentil=90.0, meses=3)
    )


# -------------------------------------------------------------------------------------------------
# 4. Tier — fail-closed e portao gold-card
# -------------------------------------------------------------------------------------------------


def test_caminho_feliz_trusted() -> None:
    assert tier(**_ti()) == ("TRUSTED", "GOLD_CARD_ATINGIDO")


@pytest.mark.parametrize("nome", [n for n, _ in _ENTRADAS_TIER])
def test_qualquer_entrada_nula_da_watch(nome: str) -> None:
    """Toda entrada nula da WATCH. O percentil F2 nulo so importa com volume (base: 50 lotes)."""
    resultado, motivo = tier(**_ti(**{nome: None}))
    assert resultado == "WATCH"
    assert motivo.startswith("ENTRADA_AUSENTE_"), motivo


def test_percentil_nulo_com_volume_insuficiente_e_neutro_nao_ausente() -> None:
    """Com menos de 10 lotes o percentil nao entra na pontuacao (faixa neutra): nulo e legitimo."""
    assert tier(**_ti(f2_lotes_janela=5, f2_percentil_glosa_liquida_pares_tenant=None)) == (
        "TRUSTED",
        "GOLD_CARD_ATINGIDO",
    )


def test_taxa_f1_nula_da_watch_mesmo_sem_volume() -> None:
    """Aviso A2: o P4 deve SEMPRE emitir a taxa F1; nula da WATCH em qualquer volume de ordens."""
    assert tier(**_ti(f1_ordens_decididas=3, f1_taxa_liberacao_humana=None)) == (
        "WATCH",
        "ENTRADA_AUSENTE_F1_TAXA",
    )


@pytest.mark.parametrize(
    ("sobrescritas", "motivo"),
    [
        ({"f1_ordens_decididas": -5}, "ENTRADA_INVALIDA_F1_ORDENS"),
        ({"f1_taxa_liberacao_humana": -0.01}, "ENTRADA_INVALIDA_F1_TAXA"),
        ({"f1_taxa_liberacao_humana": 1.01}, "ENTRADA_INVALIDA_F1_TAXA"),
        ({"f5_meses_relacionamento": -1}, "ENTRADA_INVALIDA_F5_MESES"),
        ({"pagamentos_liberados_humanos": -1}, "ENTRADA_INVALIDA_PAGAMENTOS_LIBERADOS"),
        ({"f2_lotes_janela": -3}, "ENTRADA_INVALIDA_F2_LOTES"),
        ({"f2_percentil_glosa_liquida_pares_tenant": -1}, "ENTRADA_INVALIDA_F2_PERCENTIL"),
        ({"f2_percentil_glosa_liquida_pares_tenant": 150}, "ENTRADA_INVALIDA_F2_PERCENTIL"),
    ],
)
def test_qualquer_entrada_invalida_da_watch(sobrescritas: dict[str, Any], motivo: str) -> None:
    """Reparo B1: dado invalido nunca promove, nem com pontuacao maxima e gold-card."""
    assert tier(**_ti(**sobrescritas)) == ("WATCH", motivo)


@pytest.mark.parametrize(
    ("valor", "invalido"),
    [(0, False), (100, False), (100.01, True), (-0.01, True)],
)
def test_fronteiras_do_percentil(valor: float, invalido: bool) -> None:
    resultado = tier(**_ti(f2_percentil_glosa_liquida_pares_tenant=valor))
    assert (resultado == ("WATCH", "ENTRADA_INVALIDA_F2_PERCENTIL")) is invalido


@pytest.mark.parametrize(
    ("sobrescritas", "motivo"),
    [
        ({"red_flag_pico_faturamento": True}, "RED_FLAG_PICO_FATURAMENTO"),
        ({"fraude_monitorar_12m": True}, "RED_FLAG_FRAUDE_MONITORAR_12M"),
        ({"pagamentos_liberados_humanos": 9}, "PRESTADOR_NOVO_OU_BAIXO_VOLUME"),
        ({"pagamentos_liberados_humanos": 0}, "PRESTADOR_NOVO_OU_BAIXO_VOLUME"),
        ({"pontuacao_confianca": 86}, "PONTUACAO_FORA_DA_ESCALA"),
        ({"pontuacao_confianca": -1}, "PONTUACAO_FORA_DA_ESCALA"),
        ({"pontuacao_confianca": 39}, "PONTUACAO_ABAIXO_DO_CORTE_WATCH"),
    ],
)
def test_red_flags_e_novos_sao_nao_compensatorios(sobrescritas: dict[str, Any], motivo: str) -> None:
    """Pontuacao maxima + gold-card nao compram saida de uma red flag (decisao do dono D-T2 B)."""
    assert tier(**_ti(**sobrescritas)) == ("WATCH", motivo)


@pytest.mark.parametrize(
    "sobrescritas",
    [
        {"f1_ordens_decididas": 9, "f1_taxa_liberacao_humana": 1.0},  # 100% em 9 ordens nao e gold-card
        {"f1_taxa_liberacao_humana": 0.8999},
        {"f5_meses_relacionamento": 11},
        {"pontuacao_confianca": 69},
    ],
)
def test_sem_portao_completo_nunca_trusted(sobrescritas: dict[str, Any]) -> None:
    assert tier(**_ti(**sobrescritas)) == ("STANDARD", "PONTUACAO_STANDARD")


def test_fronteiras_dos_cortes() -> None:
    assert tier(**_ti(pontuacao_confianca=70))[0] == "TRUSTED"
    assert tier(**_ti(pontuacao_confianca=40, f1_taxa_liberacao_humana=0.5)) == (
        "STANDARD",
        "PONTUACAO_STANDARD",
    )
    assert tier(**_ti(pagamentos_liberados_humanos=10))[0] == "TRUSTED"


def test_catch_all_e_a_ultima_linha_e_e_watch() -> None:
    ultima = _TIER_T.regras[-1]
    assert all(e == "-" for e in ultima.entradas)
    assert ultima.saidas == ("WATCH", "COMBINACAO_NAO_MAPEADA")


@pytest.mark.parametrize(
    ("scorecard", "tier_extra", "esperado"),
    [
        (_sc(), {}, "TRUSTED"),
        (_sc(0, None, 0, None, 0), {"f1_ordens_decididas": 0, "f5_meses_relacionamento": 0}, "WATCH"),
        (_sc(ordens=9, taxa=1.0), {"f1_ordens_decididas": 9, "f1_taxa_liberacao_humana": 1.0}, "STANDARD"),
        (_sc(None, None, None, None, None), {}, "WATCH"),
    ],
)
def test_composicao_scorecard_tier(
    scorecard: dict[str, Any], tier_extra: dict[str, Any], esperado: str
) -> None:
    resultado, _ = tier(**_ti(pontuacao_confianca=pontuacao(**scorecard), **tier_extra))
    assert resultado == esperado


@pytest.mark.parametrize(
    ("scorecard", "pontos_esperados", "motivo"),
    [
        (_sc(lotes=None), 55, "ENTRADA_AUSENTE_F2_LOTES"),
        (_sc(lotes=50, percentil=None), 55, "ENTRADA_AUSENTE_F2_PERCENTIL"),
        (_sc(percentil=150.0), 55, "ENTRADA_INVALIDA_F2_PERCENTIL"),
        (_sc(lotes=-3), 55, "ENTRADA_INVALIDA_F2_LOTES"),
        (_sc(taxa=1.2), 45, "ENTRADA_INVALIDA_F1_TAXA"),
        (_sc(ordens=-5), 45, "ENTRADA_INVALIDA_F1_ORDENS"),
        (_sc(meses=-1), 70, "ENTRADA_INVALIDA_F5_MESES"),
    ],
)
def test_composicao_b1_dado_faltante_ou_invalido_nunca_chega_a_standard(
    scorecard: dict[str, Any], pontos_esperados: int, motivo: str
) -> None:
    """B1 do gate cego: o scorecard da 0 a feature ruim, mas as OUTRAS ainda passam de corte_watch.

    O tier recebe as MESMAS entradas brutas que o scorecard recebeu e as valida por si.
    """
    pontos = pontuacao(**scorecard)
    assert pontos == pontos_esperados
    assert pontos >= 40, "sem o reparo esta combinacao chegaria a STANDARD"
    compartilhadas = {k: v for k, v in scorecard.items() if k in dict(_ENTRADAS_TIER)}
    assert tier(**_ti(pontuacao_confianca=pontos, **compartilhadas)) == ("WATCH", motivo)


# -------------------------------------------------------------------------------------------------
# 5. Guardas negativas (dominio, alcada, PHI, CADE)
# -------------------------------------------------------------------------------------------------


def test_dominio_do_tier_e_fechado_e_sem_saida_adversa() -> None:
    emitidos = {r.saidas[0] for r in _TIER_T.regras}
    assert emitidos == _DOMINIO_TIER
    adversos = {
        "BLOQUEIO",
        "BLOQUEAR",
        "NEGAR",
        "NEGADO",
        "RECUSAR",
        "RECUSADO",
        "GLOSAR",
        "REDUZIR",
        "SUSPENDER",
    }
    assert not emitidos & adversos


def test_nenhuma_saida_de_valor_ou_teto_alcada_fica_na_politica() -> None:
    proibidos = re.compile(r"teto|threshold|valor|limite|cents|brl|liberar|aprovar", re.IGNORECASE)
    saidas = [n for t in (_PONTOS, _MOTIVOS, _TIER_T) for n, _ in t.saidas]
    assert [n for n in saidas if proibidos.search(n)] == []
    entradas = [n for t in (_PONTOS, _TIER_T) for n, _ in t.entradas]
    assert [n for n in entradas if re.search(r"cents|brl|valor", n)] == []


def test_nenhuma_entrada_de_beneficiario_ou_clinica() -> None:
    """PHI/ADR-0019: so agregados por prestador; F4 (custo de episodio) e v2 lake-side."""
    proibidos = {"beneficiario", "beneficiarios", "cpf", "cid", "cid10", "tuss", "paciente", "episodio"}
    proibidos |= {"diagnostico", "mpi", "procedimento", "custo"}
    entradas = [n for t in (_PONTOS, _TIER_T) for n, _ in t.entradas]
    assert [n for n in entradas if proibidos & set(n.lower().split("_"))] == []


def test_benchmark_de_pares_e_do_tenant() -> None:
    """CADE: o unico benchmark contra pares e por tenant; nada agrega entre operadoras."""
    entradas = [n for t in (_PONTOS, _TIER_T) for n, _ in t.entradas]
    com_pares = [n for n in entradas if "pares" in n or "percentil" in n]
    assert set(com_pares) == {"f2_percentil_glosa_liquida_pares_tenant"}
    pooling = re.compile(r"mercado|global|cross|operadoras|todos_tenants|setor", re.IGNORECASE)
    assert [n for n in entradas if pooling.search(n)] == []
    assert "pares DO MESMO TENANT" in _SCORECARD.read_text(encoding="utf-8")


# -------------------------------------------------------------------------------------------------
# 6. DRAFT e nao ligado
# -------------------------------------------------------------------------------------------------

_ARQUIVOS: Final = (
    "spec/processes/dmn/prestador_trust_scorecard.dmn",
    "spec/processes/dmn/prestador_tier.dmn",
)
_DECISOES: Final = ("prestador_trust_scorecard", "prestador_trust_motivos", "prestador_tier")


def test_registrados_como_pendentes_nunca_aprovados() -> None:
    registro = yaml.safe_load((_ROOT / "spec" / "aprovacoes-de-regras.yaml").read_text(encoding="utf-8"))
    pendentes = {p["artefato"]: p for p in registro["pendentes"]}
    aprovados = {a["artefato"] for a in registro["aprovados"] or []}
    for arquivo in _ARQUIVOS:
        assert arquivo in pendentes, arquivo
        assert arquivo not in aprovados, arquivo
        marca = pendentes[arquivo]["marca_no_arquivo"]
        assert marca in (_ROOT / arquivo).read_text(encoding="utf-8"), arquivo


def test_na_fila_de_revisao() -> None:
    fila = (_ROOT / "docs" / "review-queue.md").read_text(encoding="utf-8")
    for arquivo in _ARQUIVOS:
        assert f"`{arquivo}`" in fila, arquivo


def test_sem_consumidor_ate_o_p6() -> None:
    bpmns = "".join(
        p.read_text(encoding="utf-8") for p in (_ROOT / "spec" / "processes" / "bpmn").glob("*.bpmn")
    )
    fontes = "".join(p.read_text(encoding="utf-8") for p in (_ROOT / "src").rglob("*.py"))
    for decisao in _DECISOES:
        assert f'decisionRef="{decisao}"' not in bpmns, decisao
        assert decisao not in fontes, decisao
    orfaos = yaml.safe_load((_DMN_DIR / "orphans-allowlist.yaml").read_text(encoding="utf-8"))["orphans"]
    for decisao in _DECISOES:
        assert decisao in orfaos, decisao
