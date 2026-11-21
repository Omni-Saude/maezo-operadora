"""E-SF-24b (parcial, CD-231) — template v1 do termo da negativa e porta FECHADA do fundamento.

(A) O v1 traz, num bloco próprio com título, os incisos I, II e III do art. 16, § 1°, da RN 623,
byte a byte do texto-base versionado; o v0 fica intacto e não é mais aceito.
(B) O fundamento do art. 14 só entra por `FundamentoDoTermo` de um catálogo pinado. Não há
catálogo neste build (`CATALOGO_DE_FUNDAMENTOS_SHA256 is None`): a composição recusa sempre,
mesmo com um fundamento perfeito. Nenhum teste aqui pina catálogo. Fixtures sintéticas.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from maezo.tools.workers import denial_term
from maezo.tools.workers.denial_term import (
    CATALOGO_DE_FUNDAMENTOS_SHA256,
    DENIAL_TERM_TEMPLATE,
    ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO,
    ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO,
    ClausulaContratual,
    DenialTermRefusalError,
    DispositivoLegal,
    FundamentoDoTermo,
    TemplatePin,
    hash_da_entrada,
    load_denial_term_template,
    render_denial_term,
)

_ROOT = Path(__file__).resolve().parents[3]
_V0 = _ROOT / "spec/policies/ans/templates/termo-negativa.v0.txt"
_V1 = _ROOT / "spec/policies/ans/templates/termo-negativa.v1.txt"
_RN623 = _ROOT / "docs/compliance/legal/textos-base/RN-623-2024.md"

#: Pino do v0 publicado pela E-SF-24a (#1343): o arquivo fica byte-idêntico.
_V0_SHA256 = "536162999723833e90f113df684bce8489bda116bcbd9af65a7a4dc17dcacb87"

_TITULO_DO_BLOCO = "RN 623/2024, art. 16, § 1° — elementos mínimos:"


def _art16_linhas() -> list[str]:
    linhas = _RN623.read_text(encoding="utf-8").splitlines()
    inicio = next(i for i, linha in enumerate(linhas) if linha.startswith("Art. 16. "))
    fim = next(i for i in range(inicio + 1, len(linhas)) if linhas[i].startswith("Art. 17. "))
    return linhas[inicio:fim]


def _negar() -> dict[str, Any]:
    return {
        "decisao_auditor": "NEGAR",
        "auditor_id": "auditor-sintetico-01",
        "justificativa_clinica": "Motivo sintetico para teste",
        "fundamentacao_dut": "Clausula sintetica 9.9 — teste",
    }


def _fundamento_perfeito(hash_do_catalogo: str) -> FundamentoDoTermo:
    fundamento = FundamentoDoTermo(
        id_regra="REGRA-SINTETICA-001",
        versao_regra="1",
        modelo_contrato="MODELO-SINTETICO-A",
        motivo_texto="Motivo sintetico do catalogo",
        clausula_contratual=ClausulaContratual(
            ancora="ancora-sintetica", secao="Clausula sintetica 9.9", texto="teste", versao_contrato="1"
        ),
        dispositivos_legais=(
            DispositivoLegal(
                id_fonte="FONTE-SINTETICA", localizador="art. 1", rotulo="Dispositivo sintetico"
            ),
        ),
        status_juridico="aprovado_juridico",
        hash_do_catalogo=hash_do_catalogo,
        hash_entrada="",
    )
    return dataclasses.replace(fundamento, hash_entrada=hash_da_entrada(fundamento))


# --- (A) template v1 -----------------------------------------------------------------------------


def test_o_v1_e_o_unico_template_aceito_e_o_v0_fica_intacto() -> None:
    assert (DENIAL_TERM_TEMPLATE.path, DENIAL_TERM_TEMPLATE.versao) == (
        "spec/policies/ans/templates/termo-negativa.v1.txt",
        "v1",
    )
    assert hashlib.sha256(_V1.read_bytes()).hexdigest() == DENIAL_TERM_TEMPLATE.sha256
    assert hashlib.sha256(_V0.read_bytes()).hexdigest() == _V0_SHA256
    with pytest.raises(DenialTermRefusalError) as refused:
        load_denial_term_template(_V0)
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO
    # Nem com o pino do v0: a composição exige o pino vigente (v1).
    v0 = load_denial_term_template(_V0, TemplatePin(path=str(_V0), versao="v0", sha256=_V0_SHA256))
    with pytest.raises(DenialTermRefusalError) as refused:
        render_denial_term(
            _negar(),
            v0,
            fundamento=_fundamento_perfeito("0" * 64),
            canais_ouvidoria=("Telefone sintetico",),
            prazo_reanalise_dias_uteis=7,
        )
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO


def test_os_tres_incisos_do_art_16_par_1_estao_verbatim_em_bloco_proprio_com_titulo() -> None:
    art16 = _art16_linhas()
    incisos = [linha for linha in art16 if re.match(r"(I|II|III) - ", linha)]
    assert [linha.split(" - ", 1)[0] for linha in incisos] == ["I", "II", "III"]
    v1 = _V1.read_bytes().decode("utf-8").splitlines()
    assert v1.count(_TITULO_DO_BLOCO) == 1
    titulo = v1.index(_TITULO_DO_BLOCO)
    # O título usa só a expressão da própria norma ("elementos mínimos", § 1°).
    assert "elementos mínimos" in next(linha for linha in art16 if linha.startswith("§ 1°"))
    bloco = v1[titulo + 1 :]
    posicoes = []
    for inciso in incisos:
        # Byte a byte: a linha inteira do texto-base é uma linha inteira do bloco.
        assert [linha.encode("utf-8") for linha in bloco].count(inciso.encode("utf-8")) == 1, inciso
        posicoes.append(bloco.index(inciso))
    assert posicoes == sorted(posicoes)
    # Os dados da operadora ficam logo abaixo do inciso a que respondem.
    assert bloco[posicoes[1] + 1] == "$canais_ouvidoria"
    assert bloco[posicoes[2] + 1] == "$prazo_reanalise_dias_uteis dias úteis"
    # Fora do bloco, nenhum inciso (o bloco é próprio).
    assert not [linha for linha in v1[:titulo] if re.match(r"(I|II|III) - ", linha)]


def test_o_v1_nao_tem_mais_slot_que_os_da_composicao() -> None:
    slots = set(re.findall(r"\$(\w+)", _V1.read_text(encoding="utf-8")))
    assert slots == {"motivo", "fundamento", "canais_ouvidoria", "prazo_reanalise_dias_uteis"}


# --- (B) porta fechada ---------------------------------------------------------------------------


def test_nao_ha_catalogo_pinado_neste_build() -> None:
    assert CATALOGO_DE_FUNDAMENTOS_SHA256 is None
    assert denial_term.CATALOGO_DE_FUNDAMENTOS_SHA256 is None


@pytest.mark.parametrize("hash_do_catalogo", ["0" * 64, "a" * 64, ""])
def test_sem_catalogo_pinado_a_composicao_recusa_ate_o_fundamento_perfeito(hash_do_catalogo: str) -> None:
    template = load_denial_term_template(_V1)
    with pytest.raises(DenialTermRefusalError) as refused:
        render_denial_term(
            _negar(),
            template,
            fundamento=_fundamento_perfeito(hash_do_catalogo),
            canais_ouvidoria=("Telefone sintetico",),
            prazo_reanalise_dias_uteis=7,
        )
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO


@pytest.mark.parametrize("fundamento", [None, "Clausula sintetica 9.9 — teste", {"id_regra": "x"}])
def test_sem_catalogo_nada_substitui_o_fundamento(fundamento: Any) -> None:
    """Nem texto livre, nem dicionário, nem `fundamentacao_dut` (presente no registro) abrem a porta."""
    template = load_denial_term_template(_V1)
    with pytest.raises(DenialTermRefusalError) as refused:
        render_denial_term(
            _negar(),
            template,
            fundamento=fundamento,
            canais_ouvidoria=("Telefone sintetico",),
            prazo_reanalise_dias_uteis=7,
        )
    assert refused.value.code == ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO


def test_o_fundamento_e_um_objeto_fechado_com_os_campos_do_catalogo() -> None:
    assert [f.name for f in dataclasses.fields(FundamentoDoTermo)] == [
        "id_regra",
        "versao_regra",
        "modelo_contrato",
        "motivo_texto",
        "clausula_contratual",
        "dispositivos_legais",
        "status_juridico",
        "hash_do_catalogo",
        "hash_entrada",
    ]
    assert [f.name for f in dataclasses.fields(ClausulaContratual)] == [
        "ancora",
        "secao",
        "texto",
        "versao_contrato",
    ]
    assert [f.name for f in dataclasses.fields(DispositivoLegal)] == ["id_fonte", "localizador", "rotulo"]
    fundamento = _fundamento_perfeito("0" * 64)
    campo, valor = "hash_do_catalogo", "a" * 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(fundamento, campo, valor)
    assert fundamento.hash_do_catalogo == "0" * 64
    # Com slots não há `__dict__`: nenhum campo além dos declarados.
    for objeto in (fundamento, fundamento.clausula_contratual, fundamento.dispositivos_legais[0]):
        assert not hasattr(objeto, "__dict__")


#: Nomes de campo PROVISÓRIOS do contrato-k2: só o bloco do mapeamento pode lê-los. `texto` sai
#: da varredura porque também é campo do template e do termo composto (nomes do próprio módulo).
_CAMPOS_K2 = frozenset(
    f.name
    for cls in (FundamentoDoTermo, ClausulaContratual, DispositivoLegal)
    for f in dataclasses.fields(cls)
) - {
    f.name
    for cls in (denial_term.DenialTermTemplate, denial_term.RenderedDenialTerm)
    for f in dataclasses.fields(cls)
}


def test_o_mapeamento_provisorio_do_contrato_k2_fica_num_unico_ponto() -> None:
    """Até o contrato-k2 sair, só o bloco marcado em `denial_term.py` lê campos do catálogo; nenhum
    outro módulo de `src/` conhece os tipos do catálogo."""
    fonte = Path(denial_term.__file__).read_text(encoding="utf-8")
    linhas = fonte.splitlines()
    inicio = next(i for i, linha in enumerate(linhas, 1) if "Mapeamento provisório do contrato-k2" in linha)
    fim = next(i for i, linha in enumerate(linhas, 1) if "fim do mapeamento provisório" in linha)
    assert inicio < fim
    arvore = ast.parse(fonte)
    leituras = [
        (n.attr, n.lineno) for n in ast.walk(arvore) if isinstance(n, ast.Attribute) and n.attr in _CAMPOS_K2
    ]
    assert leituras  # controle: a varredura vê as leituras do bloco
    assert not [(attr, linha) for attr, linha in leituras if not inicio < linha < fim]
    tipos = {"FundamentoDoTermo", "ClausulaContratual", "DispositivoLegal", "hash_da_entrada"}
    for path in sorted((_ROOT / "src").rglob("*.py")):
        if path.resolve() == Path(denial_term.__file__).resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        nomes = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        nomes |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        nomes |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not nomes & tipos, path


def test_a_recusa_por_catalogo_vem_antes_de_ler_o_fundamento(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controle de mutação: com um pino válido o mesmo fundamento compõe; sem pino, recusa. Assim a
    recusa acima é da porta, não de outro defeito do fixture."""
    pino = hashlib.sha256(b"catalogo sintetico de fundamentos - teste").hexdigest()
    monkeypatch.setattr(denial_term, "CATALOGO_DE_FUNDAMENTOS_SHA256", pino)
    termo = render_denial_term(
        _negar(),
        load_denial_term_template(_V1),
        fundamento=_fundamento_perfeito(pino),
        canais_ouvidoria=("Telefone sintetico",),
        prazo_reanalise_dias_uteis=7,
    )
    assert "Motivo sintetico do catalogo\nMotivo sintetico para teste\n" in termo.texto
    assert "Clausula sintetica 9.9 — teste\nDispositivo sintetico\n" in termo.texto
    assert termo.modo == "entrega"
    assert _TITULO_DO_BLOCO in termo.texto
