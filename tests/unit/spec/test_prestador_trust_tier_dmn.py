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

#: Cortes do tier DECIDIDOS PARA A SOMBRA pelo dono (DEC-31 D31-3, #1084, 2026-11-02): corte_watch
#: 40 -> 50 e corte_trusted 70 -> 75 sobre a escala de maximo 85. Chave = maximo da escala: os cortes
#: so valem para o maximo sob o qual foram ratificados. Na v0.2.0 (F3=15 entra, maximo 100) os cortes
#: precisam de NOVA ratificacao (proporcional ~59/88) — ate la, este pin reprova.
_CORTES_RATIFICADOS_POR_MAXIMO: Final = {85: (50, 75)}
_CORTE_WATCH, _CORTE_TRUSTED = _CORTES_RATIFICADOS_POR_MAXIMO[_PONTUACAO_MAXIMA]
#: Portoes TRUSTED explicitos (DEC-31 D31-3): F5 >= T meses; F1 >= 90% em >= 10 ordens; sem red flag.
_T_MESES: Final = 12
_GOLD_CARD_TAXA: Final = 0.90
_GOLD_CARD_ORDENS: Final = 10

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
def test_status_decidido_para_a_sombra_declarado_no_arquivo(path: Path) -> None:
    """DEC-31 D31-3: valores INICIAIS DE SOMBRA ratificados pelo dono; ativacao segue pendente."""
    texto = path.read_text(encoding="utf-8")
    # tier v0.1.2: v0.1.1 = reparo B1; v0.1.2 = cortes 50/75 + red flag F1 (DEC-31). Scorecard v0.1.1:
    # so a descricao muda (valores mantidos). A v0.2.0 de ambos fica reservada para a entrada do F3.
    versao = "v0.1.2" if path == _TIER else "v0.1.1"
    assert f"{versao} — status: DECIDIDO PARA A SOMBRA (DEC-31 D31-3)" in texto
    assert "status: DRAFT" not in texto
    assert "NAO LIGADA a BPMN nem a worker" in texto
    for pendencia in ("financas + medico-auditor", "RIPD/LIA", "LGPD art. 20", "RN 503"):
        assert pendencia in texto, pendencia


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


@pytest.mark.parametrize(("lotes", "ausente"), [(9, False), (10, True)])
def test_fronteira_do_percentil_nulo_no_piso_de_lotes(lotes: int, ausente: bool) -> None:
    """O piso de F2 e 10 lotes (mesmo do scorecard): a partir dele, percentil nulo e dado faltante."""
    resultado = tier(**_ti(f2_lotes_janela=lotes, f2_percentil_glosa_liquida_pares_tenant=None))
    assert (resultado == ("WATCH", "ENTRADA_AUSENTE_F2_PERCENTIL")) is ausente


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
        ({"f1_taxa_liberacao_humana": 0.7999}, "RED_FLAG_F1_LIBERACAO_ABAIXO_DE_80"),
        ({"f1_taxa_liberacao_humana": 0.0, "f1_ordens_decididas": 10}, "RED_FLAG_F1_LIBERACAO_ABAIXO_DE_80"),
        ({"pagamentos_liberados_humanos": 9}, "PRESTADOR_NOVO_OU_BAIXO_VOLUME"),
        ({"pagamentos_liberados_humanos": 0}, "PRESTADOR_NOVO_OU_BAIXO_VOLUME"),
        ({"pontuacao_confianca": 86}, "PONTUACAO_FORA_DA_ESCALA"),
        ({"pontuacao_confianca": -1}, "PONTUACAO_FORA_DA_ESCALA"),
        ({"pontuacao_confianca": 49}, "PONTUACAO_ABAIXO_DO_CORTE_WATCH"),
    ],
)
def test_red_flags_e_novos_sao_nao_compensatorios(sobrescritas: dict[str, Any], motivo: str) -> None:
    """Pontuacao maxima + gold-card nao compram saida de uma red flag (decisao do dono D-T2 B)."""
    assert tier(**_ti(**sobrescritas)) == ("WATCH", motivo)


# -------------------------------------------------------------------------------------------------
# 4b. Red flag F1 (DEC-31 D31-3): F1 < 0.80 com volume SUFICIENTE e nao compensatoria
# -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ordens", "taxa", "red_flag"),
    [
        (10, 0.7999, True),  # piso de volume inclusivo: 10 ordens ja e volume suficiente
        (500, 0.0, True),
        (10, 0.80, False),  # fronteira: 0.80 e faixa media, nao red flag
        (10, 0.85, False),
        (9, 0.7999, False),  # volume insuficiente: neutro, NUNCA red flag
        (9, 0.0, False),
        (0, 0.0, False),
    ],
)
def test_red_flag_f1_so_com_volume_suficiente(ordens: int, taxa: float, red_flag: bool) -> None:
    """Com a pontuacao no maximo da escala, so a red flag F1 pode levar a WATCH."""
    resultado = tier(**_ti(f1_ordens_decididas=ordens, f1_taxa_liberacao_humana=taxa))
    assert (resultado == ("WATCH", "RED_FLAG_F1_LIBERACAO_ABAIXO_DE_80")) is red_flag, resultado
    if not red_flag:
        assert resultado == ("STANDARD", "PONTUACAO_STANDARD"), resultado


def test_red_flag_f1_mora_com_as_outras_red_flags() -> None:
    """Posicao na tabela FIRST: depois da validade das entradas, junto das red flags, antes de novos."""
    ids = [r.rid for r in _TIER_T.regras]
    i = ids.index("r_red_flag_f1_liberacao_baixa")
    assert ids[i - 2 : i + 2] == [
        "r_red_flag_pico",
        "r_fraude_monitorar",
        "r_red_flag_f1_liberacao_baixa",
        "r_prestador_novo",
    ]
    regra = _TIER_T.regras[i]
    nomes = [n for n, _ in _ENTRADAS_TIER]
    restritas = {n: e for n, e in zip(nomes, regra.entradas, strict=True) if e != "-"}
    assert restritas == {"f1_ordens_decididas": ">= 10", "f1_taxa_liberacao_humana": "[0..0.80)"}
    assert regra.saidas == ("WATCH", "RED_FLAG_F1_LIBERACAO_ABAIXO_DE_80")


# -------------------------------------------------------------------------------------------------
# 4c. Portoes TRUSTED explicitos e cortes (DEC-31 D31-3)
# -------------------------------------------------------------------------------------------------


def _limite(texto: str, padrao: str) -> int:
    achado = re.fullmatch(padrao, texto)
    assert achado is not None, (padrao, texto)
    return int(achado[1])


def _cortes_na_tabela() -> tuple[int, int, int]:
    """(corte_watch, corte_trusted, maximo) LIDOS da tabela de tier, linha a linha."""
    por_id = {r.rid: r.entradas[0] for r in _TIER_T.regras}
    maximo = _limite(por_id["r_pontuacao_invalida"], r"< 0, > (\d+)")
    watch = _limite(por_id["r_standard"], rf"\[(\d+)\.\.{maximo}\]")
    assert _limite(por_id["r_watch_pontuacao"], r"\[0\.\.(\d+)\)") == watch
    trusted = _limite(por_id["r_trusted"], rf"\[(\d+)\.\.{maximo}\]")
    return watch, trusted, maximo


def _maximo_do_scorecard() -> int:
    """Soma do maior ponto de cada feature, LIDA da tabela de pontos (nao do pin _PESOS)."""
    maior: dict[str, int] = {}
    for regra in _PONTOS.regras:
        feature = regra.rid.removeprefix("r_").split("_")[0]
        maior[feature] = max(maior.get(feature, 0), int(regra.saidas[0]))
    return sum(maior.values())


def test_pin_maximo_do_scorecard_amarrado_aos_cortes() -> None:
    """Mudou o maximo do scorecard (ex.: F3=15 na v0.2.0 -> 100) sem mudar os cortes? REPROVA.

    Os cortes 50/75 foram ratificados (DEC-31 D31-3) para a escala de maximo 85. Com outra escala
    os cortes tem de ser RE-RATIFICADOS pelo dono (proporcional ~59/88 para maximo 100) e so entao
    entram em _CORTES_RATIFICADOS_POR_MAXIMO junto com a tabela.
    """
    maximo = _maximo_do_scorecard()
    assert maximo == _PONTUACAO_MAXIMA == 85
    assert maximo in _CORTES_RATIFICADOS_POR_MAXIMO, (
        f"maximo do scorecard mudou para {maximo}: cortes do tier precisam de re-ratificacao "
        f"(proporcional ~{round(50 * maximo / 85)}/{round(75 * maximo / 85)}) — DEC-31 D31-3"
    )
    watch, trusted, maximo_tier = _cortes_na_tabela()
    assert maximo_tier == maximo, "escala do tier (PONTUACAO_FORA_DA_ESCALA) diverge do scorecard"
    assert (watch, trusted) == _CORTES_RATIFICADOS_POR_MAXIMO[maximo] == (50, 75)


def test_portoes_trusted_explicitos_na_linha_trusted() -> None:
    """TRUSTED exige os portoes NA PROPRIA LINHA, nao so pela pontuacao (DEC-31 D31-3)."""
    regra = next(r for r in _TIER_T.regras if r.rid == "r_trusted")
    nomes = [n for n, _ in _ENTRADAS_TIER]
    restritas = {n: e for n, e in zip(nomes, regra.entradas, strict=True) if e != "-"}
    assert restritas == {
        "pontuacao_confianca": f"[{_CORTE_TRUSTED}..{_PONTUACAO_MAXIMA}]",
        "f1_ordens_decididas": f">= {_GOLD_CARD_ORDENS}",
        "f1_taxa_liberacao_humana": f"[{_GOLD_CARD_TAXA:.2f}..1]",
        "f5_meses_relacionamento": f">= {_T_MESES}",
    }
    # "sem red flags": toda linha de red flag vem ANTES da linha TRUSTED (FIRST).
    ids = [r.rid for r in _TIER_T.regras]
    for red_flag in ("r_red_flag_pico", "r_fraude_monitorar", "r_red_flag_f1_liberacao_baixa"):
        assert ids.index(red_flag) < ids.index("r_trusted"), red_flag


@pytest.mark.parametrize(
    ("sobrescritas", "esperado"),
    [
        # Cada portao, SOZINHO, com todo o resto no maximo, impede TRUSTED.
        ({"f5_meses_relacionamento": 11}, ("STANDARD", "PONTUACAO_STANDARD")),  # F5 < T
        ({"f1_ordens_decididas": 9, "f1_taxa_liberacao_humana": 1.0}, ("STANDARD", "PONTUACAO_STANDARD")),
        ({"f1_taxa_liberacao_humana": 0.8999}, ("STANDARD", "PONTUACAO_STANDARD")),  # F1 < 90%
        ({"pontuacao_confianca": 74}, ("STANDARD", "PONTUACAO_STANDARD")),  # abaixo de corte_trusted
        ({"red_flag_pico_faturamento": True}, ("WATCH", "RED_FLAG_PICO_FATURAMENTO")),
        ({"fraude_monitorar_12m": True}, ("WATCH", "RED_FLAG_FRAUDE_MONITORAR_12M")),
        ({"f1_taxa_liberacao_humana": 0.79}, ("WATCH", "RED_FLAG_F1_LIBERACAO_ABAIXO_DE_80")),
    ],
)
def test_cada_portao_trusted_bloqueia_sozinho(
    sobrescritas: dict[str, Any], esperado: tuple[str, str]
) -> None:
    assert tier(**_ti()) == ("TRUSTED", "GOLD_CARD_ATINGIDO")
    assert tier(**_ti(**sobrescritas)) == esperado


@pytest.mark.parametrize(
    ("sobrescritas", "trusted"),
    [
        ({"f5_meses_relacionamento": 12}, True),
        ({"f5_meses_relacionamento": 11}, False),
        ({"f1_ordens_decididas": 10, "f1_taxa_liberacao_humana": 0.90}, True),
        ({"f1_ordens_decididas": 9, "f1_taxa_liberacao_humana": 0.90}, False),
        ({"f1_taxa_liberacao_humana": 0.8999}, False),
    ],
)
def test_fronteiras_dos_portoes_trusted(sobrescritas: dict[str, Any], trusted: bool) -> None:
    assert (tier(**_ti(**sobrescritas))[0] == "TRUSTED") is trusted


@pytest.mark.parametrize(
    ("pontos", "esperado"),
    [
        (85, "TRUSTED"),
        (75, "TRUSTED"),  # corte_trusted inclusivo
        (74, "STANDARD"),
        (70, "STANDARD"),  # era TRUSTED com corte_trusted=70
        (50, "STANDARD"),  # corte_watch inclusivo
        (49, "WATCH"),
        (45, "WATCH"),  # era STANDARD com corte_watch=40
        (40, "WATCH"),  # era STANDARD com corte_watch=40
        (0, "WATCH"),
    ],
)
def test_fronteiras_dos_cortes(pontos: int, esperado: str) -> None:
    assert tier(**_ti(pontuacao_confianca=pontos))[0] == esperado


def test_piso_de_novos_n10() -> None:
    assert tier(**_ti(pagamentos_liberados_humanos=10))[0] == "TRUSTED"
    assert tier(**_ti(pagamentos_liberados_humanos=9))[0] == "WATCH"


def test_catch_all_e_a_ultima_linha_e_e_watch() -> None:
    ultima = _TIER_T.regras[-1]
    assert all(e == "-" for e in ultima.entradas)
    assert ultima.saidas == ("WATCH", "COMBINACAO_NAO_MAPEADA")


def _tier_composto(scorecard: dict[str, Any]) -> tuple[int, tuple[str, str]]:
    """Composicao scorecard -> tier com as MESMAS entradas brutas (o que o P6 fara)."""
    pontos = pontuacao(**scorecard)
    compartilhadas = {k: v for k, v in scorecard.items() if k in dict(_ENTRADAS_TIER)}
    return pontos, tier(**_ti(pontuacao_confianca=pontos, **compartilhadas))


@pytest.mark.parametrize(
    ("scorecard", "pontos_esperados", "esperado"),
    [
        (_sc(), 85, ("TRUSTED", "GOLD_CARD_ATINGIDO")),
        (_sc(0, None, 0, None, 0), 35, ("WATCH", "ENTRADA_AUSENTE_F1_TAXA")),
        (_sc(0, 0.5, 0, None, 0), 35, ("WATCH", "PONTUACAO_ABAIXO_DO_CORTE_WATCH")),
        (_sc(ordens=9, taxa=1.0), 65, ("STANDARD", "PONTUACAO_STANDARD")),
        (_sc(ordens=9, taxa=0.10), 65, ("STANDARD", "PONTUACAO_STANDARD")),  # sem volume: sem red flag
        (_sc(None, None, None, None, None), 0, ("WATCH", "ENTRADA_AUSENTE_F1_ORDENS")),
        (_sc(percentil=90.0), 55, ("STANDARD", "PONTUACAO_STANDARD")),
        (_sc(meses=11), 70, ("STANDARD", "PONTUACAO_STANDARD")),
        (_sc(taxa=0.85, percentil=60.0), 50, ("STANDARD", "PONTUACAO_STANDARD")),
    ],
)
def test_composicao_scorecard_tier(
    scorecard: dict[str, Any], pontos_esperados: int, esperado: tuple[str, str]
) -> None:
    assert _tier_composto(scorecard) == (pontos_esperados, esperado)


#: Perfis (todas as entradas validas, sem F6/FRAUDE, >= 10 pagamentos) que MUDAM de tier com os
#: valores da DEC-31 D31-3. "antes" = v0.1.1 (corte_watch=40, corte_trusted=70, sem red flag F1).
_PERFIS_QUE_MUDAM: Final = (
    # F1=40 + F2=15 (P50..P75) + F5=15 = 70: TRUSTED -> STANDARD (fecha o aviso A1 do #1171)
    (_sc(percentil=60.0), 70, "TRUSTED", ("STANDARD", "PONTUACAO_STANDARD")),
    # idem com F2 NEUTRO (menos de 10 lotes): o neutro sozinho nao basta mais para TRUSTED
    (_sc(lotes=5, percentil=None), 70, "TRUSTED", ("STANDARD", "PONTUACAO_STANDARD")),
    # F1=40 + F2=0 (> P75) + F5=0 (< 12 meses) = 40: STANDARD -> WATCH
    (_sc(percentil=90.0, meses=11), 40, "STANDARD", ("WATCH", "PONTUACAO_ABAIXO_DO_CORTE_WATCH")),
    # F1=0 (< 80% em >= 10 ordens) + F2=30 + F5=15 = 45: STANDARD -> WATCH (red flag E abaixo do corte)
    (_sc(taxa=0.79), 45, "STANDARD", ("WATCH", "RED_FLAG_F1_LIBERACAO_ABAIXO_DE_80")),
    (_sc(ordens=10, taxa=0.0), 45, "STANDARD", ("WATCH", "RED_FLAG_F1_LIBERACAO_ABAIXO_DE_80")),
)


@pytest.mark.parametrize(
    ("scorecard", "pontos_esperados", "antes", "depois"), _PERFIS_QUE_MUDAM, ids=lambda v: str(v)[:40]
)
def test_perfis_que_mudam_de_tier_com_a_dec31(
    scorecard: dict[str, Any], pontos_esperados: int, antes: str, depois: tuple[str, str]
) -> None:
    pontos, resultado = _tier_composto(scorecard)
    assert pontos == pontos_esperados
    assert resultado == depois
    assert resultado[0] != antes


def test_enumeracao_completa_dos_perfis_validos_bate_com_a_lista_dos_que_mudam() -> None:
    """Varre a grade de perfis validos e compara com a regra ANTIGA (v0.1.1), escrita aqui so como
    oraculo de teste: so os perfis de _PERFIS_QUE_MUDAM (por classe de pontos) mudam de tier."""

    def antigo(pontos: int, ordens: int, taxa: float, meses: int) -> str:
        if ordens >= 10 and taxa >= 0.90 and meses >= 12 and pontos >= 70:
            return "TRUSTED"
        return "STANDARD" if pontos >= 40 else "WATCH"

    mudancas: set[tuple[int, int, int, str, str]] = set()
    for ordens, taxa, lotes, percentil, meses in itertools.product(
        (5, 10, 50), (0.5, 0.79, 0.8, 0.85, 0.9, 1.0), (5, 50), (30.0, 60.0, 90.0), (6, 12, 24)
    ):
        sc = _sc(ordens, taxa, lotes, percentil, meses)
        f1 = pontuacao(**_sc(ordens, taxa, 50, 30.0, 0)) - 30
        f2 = pontuacao(**_sc(5, 0.5, lotes, percentil, 0)) - 20
        f5 = pontuacao(**_sc(5, 0.5, 5, None, meses)) - 35
        pontos, (novo, _) = _tier_composto(sc)
        velho = antigo(pontos, ordens, taxa, meses)
        if novo != velho:
            mudancas.add((f1, f2, f5, velho, novo))
    assert mudancas == {
        (40, 15, 15, "TRUSTED", "STANDARD"),
        (40, 0, 0, "STANDARD", "WATCH"),
        (0, 30, 15, "STANDARD", "WATCH"),
    }


@pytest.mark.parametrize(
    ("scorecard", "pontos_esperados", "motivo", "chegaria_a_standard"),
    [
        (_sc(lotes=None), 55, "ENTRADA_AUSENTE_F2_LOTES", True),
        (_sc(lotes=50, percentil=None), 55, "ENTRADA_AUSENTE_F2_PERCENTIL", True),
        (_sc(percentil=150.0), 55, "ENTRADA_INVALIDA_F2_PERCENTIL", True),
        (_sc(lotes=-3), 55, "ENTRADA_INVALIDA_F2_LOTES", True),
        # F1 invalido soma 45: com corte_watch=50 ja nao chegaria a STANDARD; a linha de validade
        # continua respondendo PRIMEIRO (motivo especifico, nao "abaixo do corte").
        (_sc(taxa=1.2), 45, "ENTRADA_INVALIDA_F1_TAXA", False),
        (_sc(ordens=-5), 45, "ENTRADA_INVALIDA_F1_ORDENS", False),
        (_sc(meses=-1), 70, "ENTRADA_INVALIDA_F5_MESES", True),
    ],
)
def test_composicao_b1_dado_faltante_ou_invalido_nunca_chega_a_standard(
    scorecard: dict[str, Any], pontos_esperados: int, motivo: str, chegaria_a_standard: bool
) -> None:
    """B1 do gate cego: o scorecard da 0 a feature ruim, mas as OUTRAS ainda passam de corte_watch.

    O tier recebe as MESMAS entradas brutas que o scorecard recebeu e as valida por si.
    """
    pontos, resultado = _tier_composto(scorecard)
    assert pontos == pontos_esperados
    assert (pontos >= _CORTE_WATCH) is chegaria_a_standard, "sem o reparo esta combinacao chegaria a STANDARD"
    assert resultado == ("WATCH", motivo)


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
# 6. Decidido para a sombra (DEC-31), ativacao pendente e nao ligado
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


#: Ativacao (DEC-31 D31-3) segue exigindo: calibracao financas + medico-auditor, RIPD/LIA do DPO
#: (LGPD art. 20) e clausula aditiva RN 503 do juridico. Os campos de assinatura ficam EM BRANCO
#: (ADR-0007: nenhum agente preenche bloco de aprovacao).
_PAPEIS_DA_ATIVACAO: Final = ("financas", "medico-auditor", "dpo", "juridico")


@pytest.mark.parametrize("arquivo", _ARQUIVOS)
def test_decidido_para_a_sombra_com_ativacao_pendente_e_assinaturas_em_branco(arquivo: str) -> None:
    registro = yaml.safe_load((_ROOT / "spec" / "aprovacoes-de-regras.yaml").read_text(encoding="utf-8"))
    entrada = {p["artefato"]: p for p in registro["pendentes"]}[arquivo]
    assert "DEC-31 D31-3" in entrada["nota"]
    assert "DECIDIDO PARA A SOMBRA" in entrada["nota"]
    assinaturas = entrada["assinaturas_para_ativacao"]
    assert tuple(a["papel"] for a in assinaturas) == _PAPEIS_DA_ATIVACAO
    for assinatura in assinaturas:
        assert assinatura["objeto"].strip(), assinatura["papel"]
        assert assinatura["assinado_por"] == "", assinatura["papel"]
        assert assinatura["assinado_em"] == "", assinatura["papel"]


def test_na_fila_de_revisao() -> None:
    fila = (_ROOT / "docs" / "review-queue.md").read_text(encoding="utf-8")
    for arquivo in _ARQUIVOS:
        assert f"`{arquivo}`" in fila, arquivo
    linhas = [linha for linha in fila.splitlines() if any(f"`{a}`" in linha for a in _ARQUIVOS)]
    linhas = [linha for linha in linhas if linha.startswith("| `spec/processes/dmn/prestador_t")]
    assert len(linhas) == 2
    for linha in linhas:
        assert "DECIDIDO PARA A SOMBRA (DEC-31 D31-3" in linha, linha[:80]
        assert "assinaturas em branco" in linha, linha[:80]


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
