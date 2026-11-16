"""Golden evals da COBERTURA da Helena (DL-0167, HEL-6a), Tier A deterministico, com a chave LIGADA.

Nesta onda as fontes sao SINTETICAS (`golden/helena/cobertura/fontes_sinteticas/`, na forma do teste de uso
do rco) e o pacote K2 e' a amostra sintetica do leitor. O conversor e a avaliacao sao os mesmos que vao rodar
sobre as 180 perguntas reais quando o K2 chegar (Onda 2): so' as fontes trocam.

O resultado da amostra e' FIXADO aqui (nao e' o corte do dono, que so' vale sobre o K2 real): a amostra
mostra o que a Onda 1 ainda nao faz (prazo em "dias uteis", pela trava `sem_prazo_da_ans`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena.habilidades import cobertura as cob
from maezo.agents.helena.habilidades.cobertura.manual import ManualK2, PinDoK2, carregar
from maezo.agents.helena.habilidades.cobertura.regra_geral import CamadaRegraGeral
from maezo.agents.helena.habilidades.cobertura.verificador import verificar
from maezo.agents.helena.habilidades.cobertura_fora import TEXTO_COBERTURA_FORA
from tests.evals import helena_cobertura_conversor as conv
from tests.unit.agents.test_helena_cobertura_ampla import _grafo, _ligada, _Modelo
from tests.unit.agents.test_helena_rodada_3_simulador import _entrada

pytestmark = pytest.mark.eval

_RAIZ = Path(__file__).parent / "golden" / "helena" / "cobertura"
_FONTES = _RAIZ / "fontes_sinteticas"
_AMOSTRA_K2 = Path(__file__).parents[1] / "unit" / "agents" / "helena_k2_amostra" / "pacote"
_PIN = PinDoK2(
    versao="amostra-0.1.0",
    sha256_manifesto="9af9c38159fc23b4b73e576bab19afbb87cb7562839906a67ff660320e6dd22a",
)


def _casos() -> list[conv.CasoDeCobertura]:
    return conv.converter(
        _FONTES / "perguntas.yaml", _FONTES / "gabarito.yaml", conv.carregar_camadas(_RAIZ / "camadas.yaml")
    )


@pytest.fixture(scope="module")
def manual() -> ManualK2:
    return carregar(_AMOSTRA_K2, pin=_PIN)


async def _rodar(manual: ManualK2, caso: conv.CasoDeCobertura) -> conv.Resultado:
    """UMA pergunta pelo grafo inteiro (roteador -> `cobertura` -> camada 1 -> travas -> envio)."""
    grafo, whatsapp, _cib = _grafo(_Modelo(), _ligada(CamadaRegraGeral(manual)))
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada(caso.pergunta))
    texto = whatsapp.enviados[-1]
    citadas = [
        f.split(":", 1)[1].split("@", 1)[0]
        for e in logs
        if e.get("event") == "helena_cobertura_respondeu"
        for f in e.get("fontes", [])
    ]
    barrado = any(e.get("event") == "helena_cobertura_texto_barrado" for e in logs)
    if texto.startswith(cob.TEXTO_TRANSFERIR):
        desfecho = "transferir"
    elif texto.startswith(TEXTO_COBERTURA_FORA) or barrado:
        desfecho = "aviso"
    else:
        desfecho = "resposta" if citadas else "esclarecer"
    trechos = [t for i in citadas if (r := manual.regra(i)) is not None for t in (r.texto, r.resumo)]
    confere = desfecho != "resposta" or verificar(texto, trechos).ok
    return conv.avaliar(caso, desfecho=desfecho, texto=texto, regras_citadas=citadas, fatos_conferem=confere)


def test_o_conversor_so_leva_o_beneficiario_e_marca_a_camada() -> None:
    casos = _casos()
    assert [c.id for c in casos] == [f"S9-00{i}" for i in range(1, 9)]  # S9-101 e' de prestador
    assert {c.id: c.camada for c in casos}["S9-008"] == "2"
    assert all(c.origem["arquivo"] == "validacao/teste-uso/fase-e" for c in casos)


def test_os_json_versionados_batem_com_a_conversao(tmp_path: Path) -> None:
    """Os golden JSON do repositorio sao exatamente a saida do conversor (nada editado a mao)."""
    assert (
        conv.main(
            [
                "--perguntas",
                str(_FONTES / "perguntas.yaml"),
                "--gabarito",
                str(_FONTES / "gabarito.yaml"),
                "--camadas",
                str(_RAIZ / "camadas.yaml"),
                "--saida",
                str(tmp_path),
            ]
        )  # fmt: skip
        == 0
    )
    gerados = {p.name: p.read_text(encoding="utf-8") for p in sorted(tmp_path.glob("*.json"))}
    versionados = {p.name: p.read_text(encoding="utf-8") for p in sorted(_RAIZ.glob("EVL-HELENA-COB-*.json"))}
    assert gerados == versionados


@pytest.mark.parametrize(
    ("ajuste", "motivo"),
    [
        (lambda g: g["gabarito"].pop(0), "pergunta sem gabarito"),
        (lambda g: g["gabarito"][3].pop("tipo_resposta"), "armadilha sem tipo_resposta"),
        (lambda g: g["gabarito"][0].update(elementos_obrigatorios="texto"), "lista de textos"),
        (lambda g: g["gabarito"][0].pop("dominio"), "sem campo obrigatorio"),
    ],
)
def test_fonte_fora_da_forma_falha_fechado(tmp_path: Path, ajuste: Any, motivo: str) -> None:
    import yaml

    gabarito: dict[str, Any] = yaml.safe_load((_FONTES / "gabarito.yaml").read_text(encoding="utf-8"))
    ajuste(gabarito)
    (tmp_path / "gabarito.yaml").write_text(yaml.safe_dump(gabarito, allow_unicode=True), encoding="utf-8")
    with pytest.raises(conv.FonteInvalidaError, match=motivo):
        conv.converter(_FONTES / "perguntas.yaml", tmp_path / "gabarito.yaml", {})


def test_a_avaliacao_pega_invencao_e_elemento_ausente() -> None:
    [caso] = [c for c in _casos() if c.id == "S9-001"]
    ok = conv.avaliar(
        caso, desfecho="resposta", texto="Precisa de autorização prévia.", regras_citadas=["AMOSTRA-AUT-001"]
    )
    assert ok.correta and not ok.invencao
    inventou = conv.avaliar(
        caso, desfecho="resposta", texto="Não precisa de autorização.", regras_citadas=["AMOSTRA-AUT-001"]
    )
    assert not inventou.correta and inventou.invencao
    sem_fonte = conv.avaliar(
        caso, desfecho="resposta", texto="Precisa de autorização prévia.", fatos_conferem=False
    )
    assert sem_fonte.invencao


async def test_resultado_da_amostra_com_a_chave_ligada(manual: ManualK2) -> None:
    """O resultado FIXADO da amostra sintetica (relatorio da HEL-6a). Mudou? Atualize aqui e no relatorio."""
    resultados = {c.id: await _rodar(manual, c) for c in _casos()}
    erradas = {i: r.motivos for i, r in resultados.items() if not r.correta}
    assert erradas == {
        "S9-003": ("desfecho",),  # prazo em "dias uteis": `sem_prazo_da_ans` (HEL-5b, PR propria)
        # camada 2 (carencia, consulta ao plano) nao existe: antes a regra de AUTORIZACAO da ressonancia
        # respondia a pergunta de carencia pelo sinonimo; desde a HEL-5a a camada 1 transfere (correto).
        "S9-008": ("desfecho",),
    }
    resumo = conv.corte(list(resultados.values()))
    assert (resumo.casos_camada_1, resumo.acertos_camada_1, resumo.invencoes) == (7, 6, 0)
    assert not resumo.aprovado  # 85,7% < 95%: o corte do dono so' vale sobre o K2 real
    print(json.dumps({"acerto_camada_1": round(resumo.acerto, 3), "invencoes": resumo.invencoes}))
