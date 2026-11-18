"""E-SF-RT-04 (variante RT-04r): "Meu plano" construido fechado; fatos so' da fonte; cancelar em 2 toques."""

from __future__ import annotations

import ast
import asyncio
import inspect
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from maezo.portal.api import meu_plano as mp
from maezo.portal.api.meu_plano import (
    ACOES_DA_VISAO,
    ACOES_DO_CANCELAMENTO,
    LINHA_ALTERNATIVA_EXIBIDA,
    MEU_PLANO_LIGADO,
    TEXTO_LINHA_ALTERNATIVA,
    TEXTO_PEDIDO_DE_CANCELAMENTO,
    AvisoDeReajuste,
    MeuPlanoRecusado,
    TelaDeCancelamento,
    VisaoMeuPlano,
    abrir_cancelamento,
    confirmar_cancelamento,
    listar_opcoes,
    pedir_troca_assistida,
    projetar_meu_plano,
)

_RAIZ = Path(mp.__file__).resolve().parents[4]
_FONTE_DO_MODULO = Path(inspect.getfile(mp)).read_text(encoding="utf-8")
TITULAR = "ben_0123456789abcdef"
PLANO = "pla_fedcba9876543210"


def _fatos(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "plano_ref": PLANO,
        "aniversario": date(2027, 3, 1),
        "valor_atual": "320,25",
        "valor_reajustado": "345,10",
        "data_do_fato": date(2027, 1, 30),
        "recibo_renovacao_ref": None,
    }
    base.update(kw)
    return base


class _Fonte:
    def __init__(self, dados: Mapping[str, object] | None) -> None:
        self.dados = dados

    async def fatos(self, *, titular_ref: str) -> Mapping[str, object] | None:
        return self.dados


class _Catalogo:
    def __init__(self, itens: Sequence[Mapping[str, object]] | None) -> None:
        self.itens = itens

    async def opcoes(self, *, plano_ref: str) -> Sequence[Mapping[str, object]] | None:
        return self.itens


class _Custodia:
    def __init__(self, protocolo: str | None) -> None:
        self.protocolo = protocolo
        self.chamadas: list[tuple[str, str]] = []

    async def registrar(self, *, titular_ref: str, assunto: str) -> str | None:
        self.chamadas.append((titular_ref, assunto))
        return self.protocolo


@pytest.fixture
def ligado(monkeypatch: pytest.MonkeyPatch) -> None:
    """So' para exercitar a logica atras da chave; o estado publicado e' desligado (teste proprio)."""
    monkeypatch.setattr(mp, "MEU_PLANO_LIGADO", True)


def _visao() -> VisaoMeuPlano:
    return asyncio.run(projetar_meu_plano(_Fonte(_fatos()), titular_ref=TITULAR))


# ------------------------------------------------------------------------------- estado fechado
def test_superficie_desligada_recusa() -> None:
    assert MEU_PLANO_LIGADO is False
    custodia = _Custodia("20270130-0001")
    chamadas = [
        lambda: asyncio.run(projetar_meu_plano(_Fonte(_fatos()), titular_ref=TITULAR)),
        lambda: asyncio.run(listar_opcoes(_Catalogo([]), plano_ref=PLANO)),
        lambda: asyncio.run(pedir_troca_assistida(custodia, titular_ref=TITULAR, produto_ref=PLANO)),
        lambda: abrir_cancelamento(None),  # type: ignore[arg-type]
        lambda: asyncio.run(
            confirmar_cancelamento(
                TelaDeCancelamento(acoes=ACOES_DO_CANCELAMENTO, linha_alternativa=None),
                custodia,
                titular_ref=TITULAR,
            )
        ),
    ]
    for chamada in chamadas:
        with pytest.raises(MeuPlanoRecusado) as e:
            chamada()
        assert e.value.code == "meu_plano_desligado"
    assert custodia.chamadas == []


def test_linha_alternativa_desligada(ligado: None) -> None:
    assert LINHA_ALTERNATIVA_EXIBIDA is False and TEXTO_LINHA_ALTERNATIVA is None
    tela = abrir_cancelamento(_visao())
    assert tela.linha_alternativa is None
    assert tela.acoes == ("confirmar_cancelamento",)
    assert "LINHA_ALTERNATIVA_EXIBIDA" not in _usos_fora_da_definicao("LINHA_ALTERNATIVA_EXIBIDA")


def _usos_fora_da_definicao(nome: str) -> set[str]:
    """Funcoes que leem `nome`: vazio prova que nenhuma tela consulta a linha (nada a ligar por flag)."""
    arvore = ast.parse(_FONTE_DO_MODULO)
    return {
        f.name
        for f in ast.walk(arvore)
        if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)
        for n in ast.walk(f)
        if isinstance(n, ast.Name) and n.id == nome
    }


def test_modulo_nao_montado_e_sem_importador() -> None:
    src = _RAIZ / "src" / "maezo"
    importadores = []
    for p in src.rglob("*.py"):
        if p == Path(mp.__file__).resolve():
            continue
        arvore = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(arvore):
            if isinstance(n, ast.ImportFrom) and (
                (n.module or "").endswith("meu_plano") or any(a.name == "meu_plano" for a in n.names)
            ):
                importadores.append(str(p))
            if isinstance(n, ast.Import) and any(a.name.endswith("meu_plano") for a in n.names):
                importadores.append(str(p))
    assert importadores == []
    nomes = {n.id for n in ast.walk(ast.parse(_FONTE_DO_MODULO)) if isinstance(n, ast.Name)}
    assert "APIRouter" not in nomes and "FastAPI" not in nomes


def test_sem_envio_sem_medicao_sem_nome_de_sinal() -> None:
    assert re.findall(r"(?<![\w.])ret\.[a-z0-9_]+\.[a-z0-9_]+", _FONTE_DO_MODULO) == []
    arvore = ast.parse(_FONTE_DO_MODULO)
    modulos = [n.module or "" for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)]
    modulos += [a.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names]
    for m in modulos:
        for vedado in ("outbound_gate", "telemetry", "outbox", "notices", "mcp_whatsapp", "reajuste"):
            assert vedado not in m


# ------------------------------------------------------------------------------- fatos so' da fonte
def test_meu_plano_sem_total_local(ligado: None) -> None:
    visao = _visao()
    assert visao.aviso_de_reajuste == AvisoDeReajuste("320,25", "345,10", date(2027, 1, 30))
    campos = {f for f in AvisoDeReajuste.__slots__} | {f for f in VisaoMeuPlano.__slots__}
    for proibido in ("percentual", "diferenca", "total", "preco", "variacao"):
        assert not any(proibido in c for c in campos)
    # Nenhuma aritmetica sobre valores no modulo: nem operador binario numerico, nem Decimal/float.
    arvore = ast.parse(_FONTE_DO_MODULO)
    aritmetica = [
        n
        for n in ast.walk(arvore)
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add | ast.Sub | ast.Mult | ast.Div | ast.Pow)
    ]
    assert aritmetica == []
    assert not {n.id for n in ast.walk(arvore) if isinstance(n, ast.Name)} & {"Decimal", "float", "round"}


def test_sem_fonte_recusa_sem_valor_padrao(ligado: None) -> None:
    class _FonteQueFalha:
        async def fatos(self, *, titular_ref: str) -> Mapping[str, object] | None:
            raise RuntimeError("indisponivel")

    for fonte in (_Fonte(None), _FonteQueFalha()):
        with pytest.raises(MeuPlanoRecusado) as e:
            asyncio.run(projetar_meu_plano(fonte, titular_ref=TITULAR))  # type: ignore[arg-type]
        assert e.value.code == "meu_plano_sem_fonte"
    for falta in ("valor_atual", "aniversario"):
        dados = _fatos()
        del dados[falta]
        with pytest.raises(MeuPlanoRecusado) as e:
            asyncio.run(projetar_meu_plano(_Fonte(dados), titular_ref=TITULAR))
        assert e.value.code == "meu_plano_fonte_fora_do_contrato"


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("plano_ref", "gestante_hipertensa"),
        ("plano_ref", "pla_0123456789abcdeZ"),
        ("plano_ref", "hiv_0123456789abcdef"),  # prefixo fora do conjunto fechado
        ("aniversario", "2027-03-01"),
        ("aniversario", datetime(2027, 3, 1)),
        ("valor_atual", "320.25"),
        ("valor_reajustado", "+10%"),
        ("valor_reajustado", 345.10),
        ("data_do_fato", "ontem"),
        ("recibo_renovacao_ref", "renovado com CID J45"),
    ],
)
def test_valor_fora_da_classe_recusado_sem_depender_da_lista(
    ligado: None, monkeypatch: pytest.MonkeyPatch, campo: str, valor: Any
) -> None:
    monkeypatch.setattr(mp, "contem_dado_de_saude", lambda _t: False)
    with pytest.raises(MeuPlanoRecusado) as e:
        asyncio.run(projetar_meu_plano(_Fonte(_fatos(**{campo: valor})), titular_ref=TITULAR))
    assert e.value.code == "meu_plano_valor_fora_da_classe"
    assert str(valor) not in str(e.value)


def test_campo_extra_da_fonte_recusa(ligado: None) -> None:
    for extra in ("cid", "uso_do_plano", "idade", "percentual"):
        dados = _fatos()
        dados[extra] = "x"
        with pytest.raises(MeuPlanoRecusado) as e:
            asyncio.run(projetar_meu_plano(_Fonte(dados), titular_ref=TITULAR))
        assert e.value.code == "meu_plano_fonte_fora_do_contrato"


# ------------------------------------------------------------------------------- renovacao zero acao
def test_sem_botao_renovar(ligado: None) -> None:
    visao = _visao()
    for acao in visao.acoes + ACOES_DO_CANCELAMENTO:
        assert "renov" not in acao and "confirmar_plano" not in acao
    assert visao.recibo_renovacao_ref is None
    com_recibo = asyncio.run(
        projetar_meu_plano(_Fonte(_fatos(recibo_renovacao_ref="rec_0123456789abcdef")), titular_ref=TITULAR)
    )
    assert com_recibo.recibo_renovacao_ref == "rec_0123456789abcdef"
    assert not any(re.search(r"def\s+\w*renov", _FONTE_DO_MODULO) for _ in [0])


# ------------------------------------------------------------------------------- cancelar
def test_cancelar_sempre_visivel(ligado: None) -> None:
    assert ACOES_DA_VISAO[0] == "cancelar"
    for fatos in (_fatos(), _fatos(valor_atual=None, valor_reajustado=None, data_do_fato=None)):
        visao = asyncio.run(projetar_meu_plano(_Fonte(fatos), titular_ref=TITULAR))
        assert visao.acoes[0] == "cancelar"


def test_cancelamento_dois_toques(ligado: None) -> None:
    custodia = _Custodia("20270130-0001")
    tela = abrir_cancelamento(_visao())  # toque 1
    pedido = asyncio.run(confirmar_cancelamento(tela, custodia, titular_ref=TITULAR))  # toque 2
    assert pedido.protocolo_ref == "20270130-0001"
    assert pedido.texto_ao_cliente == TEXTO_PEDIDO_DE_CANCELAMENTO.format(protocolo="20270130-0001")
    assert custodia.chamadas == [(TITULAR, "cancelamento")]
    assert not any(c.isdigit() for c in TEXTO_PEDIDO_DE_CANCELAMENTO.replace("{protocolo}", ""))
    for termo in ("prazo", "dias", "art.", "rn ", "lei", "em análise", "analise"):
        assert termo not in TEXTO_PEDIDO_DE_CANCELAMENTO.lower()


@pytest.mark.parametrize("protocolo", [None, "", "inventado", "PROTO 1"])
def test_cancelamento_sem_protocolo_da_custodia_recusa(ligado: None, protocolo: str | None) -> None:
    tela = abrir_cancelamento(_visao())
    with pytest.raises(MeuPlanoRecusado) as e:
        asyncio.run(confirmar_cancelamento(tela, _Custodia(protocolo), titular_ref=TITULAR))
    assert e.value.code == "meu_plano_sem_protocolo"


def test_aviso_sem_oferta_no_bloco(ligado: None) -> None:
    visao = _visao()
    assert set(AvisoDeReajuste.__slots__) == {"valor_atual", "valor_reajustado", "data_do_fato"}
    assert "ver_opcoes" not in AvisoDeReajuste.__slots__
    assert visao.acoes == ("cancelar", "ver_opcoes")


# ------------------------------------------------------------------------------- opcoes e troca
def test_opcoes_so_a_pedido(ligado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mp, "NOMES_DO_CATALOGO_PUBLICADO", frozenset({"Plano Essencial", "Plano Amplo"}))
    visao = _visao()
    assert not any("opc" in s for s in VisaoMeuPlano.__slots__)  # a visao nao traz opcoes
    itens = [
        {"produto_ref": "pro_0123456789abcdef", "nome": "Plano Essencial"},
        {"produto_ref": "pro_fedcba9876543210", "nome": "Plano Amplo"},
    ]
    opcoes = asyncio.run(listar_opcoes(_Catalogo(itens), plano_ref=visao.plano_ref))
    assert [o.nome for o in opcoes] == ["Plano Essencial", "Plano Amplo"]  # ordem da fonte
    assert set(type(opcoes[0]).__slots__) == {"produto_ref", "nome"}


@pytest.mark.parametrize(
    "item",
    [
        {"produto_ref": "pro_0123456789abcdef", "nome": "Plano Essencial", "preco": "100,00"},
        {"produto_ref": "pro_0123456789abcdef", "nome": "Plano Essencial", "faixa_etaria": "59+"},
        {"produto_ref": "pro_0123456789abcdef", "nome": "Plano Essencial", "uso": "alto"},
        {"produto_ref": "pro_0123456789abcdef"},
    ],
)
def test_opcao_fora_do_contrato_recusa(ligado: None, item: dict[str, object]) -> None:
    with pytest.raises(MeuPlanoRecusado) as e:
        asyncio.run(listar_opcoes(_Catalogo([item]), plano_ref=PLANO))
    assert e.value.code == "meu_plano_fonte_fora_do_contrato"


def test_nome_fora_do_catalogo_publicado_recusa_sem_depender_da_lista(
    ligado: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mp, "contem_dado_de_saude", lambda _t: False)
    monkeypatch.setattr(mp, "NOMES_DO_CATALOGO_PUBLICADO", frozenset({"Plano Essencial"}))
    for nome in ("Plano gestante hipertensa", "Plano Losartana", "plano essencial", "Plano Essencial "):
        item = {"produto_ref": "pro_0123456789abcdef", "nome": nome}
        with pytest.raises(MeuPlanoRecusado) as e:
            asyncio.run(listar_opcoes(_Catalogo([item]), plano_ref=PLANO))
        assert e.value.code == "meu_plano_valor_fora_da_classe"
        assert nome not in str(e.value)


def test_catalogo_publicado_vazio_recusa_toda_opcao(ligado: None) -> None:
    assert isinstance(mp.NOMES_DO_CATALOGO_PUBLICADO, frozenset) and not mp.NOMES_DO_CATALOGO_PUBLICADO
    item = {"produto_ref": "pro_0123456789abcdef", "nome": "Plano Essencial"}
    with pytest.raises(MeuPlanoRecusado) as e:
        asyncio.run(listar_opcoes(_Catalogo([item]), plano_ref=PLANO))
    assert e.value.code == "meu_plano_valor_fora_da_classe"


_PROIBIDOS = (
    "idade",
    "faixa",
    "nascimento",
    "uso",
    "utiliza",
    "sinistr",
    "saude",
    "cid",
    "cpt",
    "doenca",
    "ds",
)


def test_opcoes_sem_criterio_de_idade_uso_ou_saude() -> None:
    funcoes = [listar_opcoes, pedir_troca_assistida, projetar_meu_plano, mp.OpcaoDoPortfolio]
    for f in funcoes:
        for nome in inspect.signature(f).parameters:
            assert not any(
                nome == p or nome.startswith(p + "_") or nome.endswith("_" + p) for p in _PROIBIDOS
            )
    for campo in mp.CAMPOS_DA_OPCAO | mp.CAMPOS_DOS_FATOS:
        assert not any(campo == p or campo.startswith(p + "_") for p in _PROIBIDOS)
    # Nenhuma ordenacao nem filtro aqui: a ordem e' a da fonte.
    arvore = ast.parse(_FONTE_DO_MODULO)
    chamados = {
        n.func.id for n in ast.walk(arvore) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not chamados & {"sorted", "filter"}
    atributos = {n.attr for n in ast.walk(arvore) if isinstance(n, ast.Attribute)}
    assert "sort" not in atributos


def test_troca_so_assistida(ligado: None) -> None:
    custodia = _Custodia("20270130-0002")
    pedido = asyncio.run(
        pedir_troca_assistida(custodia, titular_ref=TITULAR, produto_ref="pro_0123456789abcdef")
    )
    assert pedido.protocolo_ref == "20270130-0002" and pedido.fila == "especialista_faixa_assistida"
    assert custodia.chamadas == [(TITULAR, "troca_de_plano")]
    publicas = {n for n, o in inspect.getmembers(mp, inspect.isfunction) if o.__module__ == mp.__name__}
    assert not any(n for n in publicas if "troca" in n and n != "pedir_troca_assistida")
    assert not any(n for n in publicas if n.startswith(("executar", "trocar", "efetivar", "contratar")))
    with pytest.raises(MeuPlanoRecusado) as e:
        asyncio.run(
            pedir_troca_assistida(_Custodia(None), titular_ref=TITULAR, produto_ref="pro_0123456789abcdef")
        )
    assert e.value.code == "meu_plano_sem_protocolo"
