"""Validação por campo do catálogo (blueprint §3.2): um teste por linha da tabela, mais o carregador.

Cada caso parte de um catálogo VÁLIDO e quebra um único campo; o achado tem de citar o campo. A
validação é a mesma do portal e do `regras-sync` (biblioteca única).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from maezo.regras.catalogo.carregar import CatalogoYamlError, carregar_yaml_texto
from maezo.regras.catalogo.schema import somar_validade, validar_catalogo
from tests.unit.regras.fabrica import ARQ_CATALOGO, catalogo_base, regra_base

_HOJE = date(2026, 11, 19)


def _achados(repo: Path, catalogo: dict[str, Any], arquivo: str = ARQ_CATALOGO) -> list[str]:
    validado, achados = validar_catalogo(catalogo, arquivo=arquivo, raiz=repo, hoje=_HOJE)
    assert (validado is None) == bool(achados)
    return achados


def _com(repo: Path, **over: Any) -> list[str]:
    return _achados(repo, catalogo_base(regra_base(**over)))


def test_catalogo_valido_passa(repo: Path) -> None:
    validado, achados = validar_catalogo(catalogo_base(), arquivo=ARQ_CATALOGO, raiz=repo, hoje=_HOJE)
    assert achados == []
    assert validado is not None and validado.regras[0].aprovam == ("financas", "dono")


# --- schema / processo ---------------------------------------------------------------------------


def test_schema_literal(repo: Path) -> None:
    cat = catalogo_base()
    cat["schema"] = "maezo-regras-catalogo.v2"
    assert any("`schema`" in a or "schema:" in a for a in _achados(repo, cat))


def test_chave_desconhecida_na_raiz_reprova(repo: Path) -> None:
    cat = catalogo_base()
    cat["extra"] = 1
    assert any("fora do formato" in a for a in _achados(repo, cat))


@pytest.mark.parametrize(
    ("processo", "trecho"),
    [
        ("REEMBOLSO", "fora de"),
        ("SP-OP-OUTRO-001", "nome do arquivo"),
    ],
)
def test_processo_formato_e_nome_do_arquivo(repo: Path, processo: str, trecho: str) -> None:
    cat = catalogo_base()
    cat["processo"] = processo
    assert any(trecho in a for a in _achados(repo, cat))


def test_processo_sem_contrato_reprova(repo: Path) -> None:
    (repo / "docs/processes/contracts/SP-OP-TESTE-001.md").unlink()
    assert any("contrato" in a and "nao existe" in a for a in _achados(repo, catalogo_base()))


# --- id / alias ------------------------------------------------------------------------------------


@pytest.mark.parametrize("rid", ["teste.minusculo", "SEMPONTO", "A." + "B" * 100, "A.B.C.D.E.F.G"])
def test_id_fora_do_formato(repo: Path, rid: str) -> None:
    assert any("`id`" in a for a in _com(repo, id=rid))


def test_id_e_alias_repetidos_no_catalogo(repo: Path) -> None:
    cat = catalogo_base(regra_base(), regra_base())
    achados = _achados(repo, cat)
    assert any("`id` repetido" in a for a in achados)
    assert any("`alias` repetido" in a for a in achados)


@pytest.mark.parametrize("alias", ["fin-01", "FINANCAS-01", "FIN-1"])
def test_alias_fora_do_formato(repo: Path, alias: str) -> None:
    assert any("`alias`" in a for a in _com(repo, alias=alias))


def test_alias_e_opcional(repo: Path) -> None:
    regra = regra_base()
    del regra["alias"]
    assert _achados(repo, catalogo_base(regra)) == []


# --- estado_declarado / tipo / fonte / sha256 ---------------------------------------------------


def test_estado_declarado_enum(repo: Path) -> None:
    assert any("`estado_declarado`" in a for a in _com(repo, estado_declarado="aprovada"))


def test_tipo_enum(repo: Path) -> None:
    assert any("`tipo`" in a for a in _com(repo, tipo="planilha"))


@pytest.mark.parametrize(
    ("fonte", "trecho"),
    [
        ([], "1..10"),
        ([{"arquivo": "spec/policies/p.yaml", "caminho": ["a"]}] * 11, "1..10"),
        ([{"arquivo": "spec/policies/p.yaml"}], "sem ['caminho']"),
        ([{"arquivo": "spec/policies/p.yaml", "caminho": ["a"], "linha": 3}], "fora do formato"),
        ([{"arquivo": "spec/policies/p.yaml", "caminho": []}], "lista nao vazia"),
        (
            [
                {"arquivo": "spec/policies/p.yaml", "caminho": ["acoes"]},
                {"arquivo": "spec/policies/p.yaml", "caminho": ["acoes"]},
            ],
            "repete",
        ),
    ],
)
def test_fonte_forma(repo: Path, fonte: Any, trecho: str) -> None:
    assert any(trecho in a for a in _com(repo, fonte=fonte)), _com(repo, fonte=fonte)


@pytest.mark.parametrize("sha", ["ABC", "A" * 64, None])
def test_sha256_64_hex_minusculo(repo: Path, sha: Any) -> None:
    assert any("`sha256`" in a for a in _com(repo, sha256=sha))


# --- aprovam / ciencia ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("aprovam", "trecho"),
    [
        ([], "1..7"),
        (["financas", "financas"], "repete"),
        (["financas", "atuaria"], "desconhecido"),
        ("financas", "lista de papeis"),
    ],
)
def test_aprovam(repo: Path, aprovam: Any, trecho: str) -> None:
    assert any("`aprovam`" in a and trecho in a for a in _com(repo, aprovam=aprovam))


def test_ciencia_disjunta_de_aprovam_e_no_dominio(repo: Path) -> None:
    assert any("disjuntos" in a for a in _com(repo, ciencia=["dono"]))
    assert any("`ciencia`" in a and "desconhecido" in a for a in _com(repo, ciencia=["ti"]))
    assert _com(repo, ciencia=[]) == []


# --- textos de negocio ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("titulo", "abc"),
        ("titulo", "x" * 121),
        ("regra", "curta"),
        ("exemplo", "x" * 1501),
        ("orientacao", "x" * 1501),
        ("o_que_e", "curto"),
        ("o_que_nao_e", "x" * 601),
    ],
)
def test_tamanho_dos_textos(repo: Path, campo: str, valor: str) -> None:
    assert any(f"`{campo}`" in a and "caracteres" in a for a in _com(repo, **{campo: valor}))


@pytest.mark.parametrize(
    "exemplo",
    [
        "A Ana, CPF 123.456.789-09, pediu reembolso.",
        "Escreva para ana.souza@exemplo.com.br sobre o pedido.",
        "Ligue para (11) 98765-4321 e confirme o pedido.",
    ],
)
def test_dado_pessoal_no_exemplo_reprova(repo: Path, exemplo: str) -> None:
    assert any("dado pessoal" in a for a in _com(repo, exemplo=exemplo))


# --- norma / validade ------------------------------------------------------------------------------


def _norma(repo: Path, **over: Any) -> dict[str, Any]:
    from maezo.regras.catalogo.schema import sha256_arquivo_normalizado

    base = {
        "ref": "RN566-ART.1",
        "arquivo": "spec/regras/normas/RN566.txt",
        "sha256": sha256_arquivo_normalizado(repo / "spec/regras/normas/RN566.txt"),
    }
    base.update(over)
    return base


def test_norma_valida_passa_e_crlf_nao_muda(repo: Path) -> None:
    norma = _norma(repo)
    assert _com(repo, norma=norma) == []
    caminho = repo / "spec/regras/normas/RN566.txt"
    caminho.write_bytes(caminho.read_bytes().replace(b"\n", b"\r\n"))
    assert _com(repo, norma=norma) == []


@pytest.mark.parametrize(
    ("over", "trecho"),
    [
        ({"ref": "rn"}, "`norma.ref`"),
        ({"arquivo": "docs/RN566.txt"}, "spec/regras/normas/"),
        ({"arquivo": "spec/regras/normas/nao.txt"}, "nao existe"),
        ({"sha256": "1" * 64}, "a norma mudou"),
    ],
)
def test_norma_invalida(repo: Path, over: dict[str, Any], trecho: str) -> None:
    assert any(trecho in a for a in _com(repo, norma=_norma(repo, **over)))


def test_validade_so_p1y(repo: Path) -> None:
    assert any("`validade`" in a for a in _com(repo, validade="P2Y"))


# --- editavel (7 tipos) -----------------------------------------------------------------------------


def _ed(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "campo": "teto",
        "tipo": "valor",
        "endereco": {"arquivo": "spec/policies/p.yaml", "caminho": ["acoes"]},
        "minimo": 0,
        "maximo": 50000,
    }
    base.update(over)
    return base


def test_editavel_dos_sete_tipos_valido(repo: Path) -> None:
    end = {"arquivo": "spec/policies/p.yaml", "caminho": ["acoes"]}
    editavel = [
        _ed(),
        {
            "campo": "prazo_dias",
            "tipo": "prazo",
            "endereco": end,
            "unidades": ["dias_uteis"],
            "minimo": 1,
            "maximo": 30,
        },
        {"campo": "pct", "tipo": "percentual", "endereco": end, "minimo": 0, "maximo": 100},
        {"campo": "qtd", "tipo": "numero", "endereco": end, "minimo": 1, "maximo": 5, "menor_que": "qtd_max"},
        {"campo": "qtd_max", "tipo": "numero", "endereco": end, "minimo": 1, "maximo": 9},
        {"campo": "canal", "tipo": "opcao", "endereco": end, "opcoes": ["whatsapp", "email"]},
        {"campo": "ligado", "tipo": "sim_nao", "endereco": end},
        {
            "campo": "aviso",
            "tipo": "texto",
            "endereco": end,
            "max": 200,
            "valor_atual": "Ola {nome}, seu pedido saiu.",
            "variaveis_travadas": ["{nome}"],
        },
    ]
    assert _com(repo, editavel=editavel) == []


@pytest.mark.parametrize(
    ("item", "trecho"),
    [
        (_ed(tipo="data"), "`editavel[1].tipo`"),
        (_ed(campo="Teto"), "`editavel[1].campo`"),
        (_ed(minimo=10, maximo=1), "minimo <= maximo"),
        (_ed(tipo="percentual", maximo=101), "percentual"),
        (_ed(tipo="prazo", unidades=["semanas"], minimo=1, maximo=2), "unidades"),
        (_ed(tipo="prazo", unidades=["horas"], minimo=0, maximo=2), "minimo >= 1"),
        ({**_ed(tipo="opcao", opcoes=["a"]), "minimo": None}, "fora do formato"),
        (_ed(endereco={}), "endereco"),
        (_ed(menor_que="nao_existe"), "inexistente"),
        (_ed(menor_que="teto"), "outro campo"),
    ],
)
def test_editavel_incoerente_reprova(repo: Path, item: dict[str, Any], trecho: str) -> None:
    assert any(trecho in a for a in _com(repo, editavel=[item])), _com(repo, editavel=[item])


def test_editavel_texto_variavel_travada_ausente_e_campo_repetido(repo: Path) -> None:
    end = {"arquivo": "x", "caminho": ["a"]}
    texto = {
        "campo": "aviso",
        "tipo": "texto",
        "endereco": end,
        "max": 10,
        "valor_atual": "Ola",
        "variaveis_travadas": ["{nome}"],
    }
    assert any("valor_atual" in a for a in _com(repo, editavel=[texto]))
    assert any("repetido" in a for a in _com(repo, editavel=[_ed(), _ed()]))


def test_editavel_opcoes_repetidas(repo: Path) -> None:
    item = {"campo": "canal", "tipo": "opcao", "endereco": {"a": 1}, "opcoes": ["x", "x"]}
    assert any("opcoes" in a for a in _com(repo, editavel=[item]))


# --- aprovacoes transcritas (§3.4.3; vazias no piloto) ----------------------------------------------


def _aprovacao(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "papel": "financas",
        "aprovador": "Pessoa Ficticia",
        "decisao": "de_acordo",
        "regra_sha256": "a" * 64,
        "data": "2026-11-18",
        "valida_ate": "2027-11-18",
        "recibo": None,
        "transcricao_manual": {
            "evidencia_url": "https://evidencias.exemplo/resposta-1",
            "evidencia_sha256": "b" * 64,
            "transcrito_por": "Pessoa do time tecnico",
        },
    }
    base.update(over)
    return base


def test_transcricao_manual_valida_passa(repo: Path) -> None:
    assert _com(repo, aprovacoes=[_aprovacao()]) == []


def test_aprovacao_com_recibo_valida_passa(repo: Path) -> None:
    item = _aprovacao(
        recibo={"id": "rcb_01JD3X5Y7Z8A9B0C1D2E3F4G5H", "sha256": "c" * 64, "cofre_version_id": "v1"},
        evidencia={"url": "https://portal.exemplo/x", "sha256": "c" * 64, "commit": "d" * 40},
    )
    del item["transcricao_manual"]
    assert _com(repo, aprovacoes=[item]) == []


@pytest.mark.parametrize(
    ("over", "trecho"),
    [
        ({"papel": "juridico"}, "nao esta' em `aprovam`"),
        ({"decisao": "nao"}, "`aprovacoes[1].decisao`"),
        ({"decisao": "importada"}, "so' com papel `dono`"),
        ({"decisao": "de_acordo_com_ressalva"}, "ressalva` obrigatoria"),
        ({"ressalva": {"tipo": "condicao", "texto": "Comprovar a base."}}, "ressalva` obrigatoria"),
        ({"valida_ate": "2027-11-17"}, "data + P1Y"),
        ({"data": "2026-12-01", "valida_ate": "2027-12-01"}, "no futuro"),
        ({"aprovador": " "}, "aprovador"),
        ({"transcricao_manual": None}, "transcricao_manual"),
        (
            {"evidencia": {"url": "https://x", "sha256": "e" * 64, "commit": "f" * 40}},
            "so' existe com recibo",
        ),
        ({"recibo": {"id": "rcb_x", "sha256": "c" * 64, "cofre_version_id": "v"}}, "exclusivos"),
        ({"quem_mandou": "agente"}, "fora do formato"),
    ],
)
def test_aprovacao_transcrita_invalida(repo: Path, over: dict[str, Any], trecho: str) -> None:
    achados = _com(repo, aprovacoes=[_aprovacao(**over)])
    assert any(trecho in a for a in achados), achados


def test_ressalva_condicao_valida_passa(repo: Path) -> None:
    item = _aprovacao(
        decisao="de_acordo_com_ressalva", ressalva={"tipo": "condicao", "texto": "Comprovar a base."}
    )
    assert _com(repo, aprovacoes=[item]) == []


def test_transcricao_manual_exige_https(repo: Path) -> None:
    manual = {"evidencia_url": "http://x", "evidencia_sha256": "b" * 64, "transcrito_por": "P"}
    assert any("https" in a for a in _com(repo, aprovacoes=[_aprovacao(transcricao_manual=manual)]))


def test_liberacao_e_comprovacao_transcritas(repo: Path) -> None:
    manual = _aprovacao()["transcricao_manual"]
    lib = {
        "liberador": "Pessoa",
        "regra_sha256": "a" * 64,
        "pr": None,
        "data": "2026-11-18",
        "recibo": None,
        "transcricao_manual": manual,
    }
    comp = {
        "papel_da_condicao": "financas",
        "regra_sha256": "a" * 64,
        "comprovacao": {"url": "https://x/y", "sha256": "1" * 64},
        "comprovado_por": "Pessoa",
        "data": "2026-11-18",
        "recibo": None,
        "transcricao_manual": manual,
    }
    assert _com(repo, liberacoes=[lib], comprovacoes=[comp]) == []
    assert any("`liberacoes[1].pr`" in a for a in _com(repo, liberacoes=[{**lib, "pr": 0}]))
    assert any(
        "papel_da_condicao" in a for a in _com(repo, comprovacoes=[{**comp, "papel_da_condicao": "dpo"}])
    )


def test_historico_de_mudanca(repo: Path) -> None:
    ok = {"pr": 1290, "o_que_mudou": "Teto de 200 para 300.", "motivo": "Decisao do dono."}
    assert _com(repo, historico_de_mudanca=[ok]) == []
    assert any(
        "`historico_de_mudanca[1].pr`" in a for a in _com(repo, historico_de_mudanca=[{**ok, "pr": "x"}])
    )


def test_somar_validade_29_de_fevereiro() -> None:
    assert somar_validade(date(2028, 2, 29), "P1Y") == date(2029, 2, 28)
    assert somar_validade(date(2026, 11, 20), "P1Y") == date(2027, 11, 20)


# --- carregador estrito ---------------------------------------------------------------------------


def test_carregador_recusa_chave_duplicada_e_merge() -> None:
    with pytest.raises(CatalogoYamlError, match="duplicada"):
        carregar_yaml_texto("a: 1\na: 2\n")
    with pytest.raises(CatalogoYamlError, match="merge"):
        carregar_yaml_texto("base: &b {x: 1}\nc:\n  <<: *b\n")
    assert carregar_yaml_texto("a: {b: 1}\n") == {"a": {"b": 1}}
