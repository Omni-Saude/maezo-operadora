"""Cerca do aceite dos ADRs 0068 a 0073 pela decisao do dono DEC-29 (issue #1084, 2026-11-01).

O dono ratificou os seis ADRs no comentario
https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6566975547, e o mesmo comentario
registra a DEC-28 (ratificacao juridica da errata RN 259, ADR-0073 Parte B / folha R-054 L9). O
comentario limita a mudanca ao bloco de aceite. Esta cerca fica vermelha se:

  (1) o Status de algum dos seis nao nomear a decisao, a issue e a data, ou houver dois Status;
  (2) a secao de aceite nao citar o comentario do dono, a regra de nao assinar (ADR-0007) e o Status
      anterior (historico preservado);
  (3) o texto do ADR fora do paragrafo de Status e da secao de aceite mudar em relacao ao commit base
      `f3e45587` (o aceite nao reescreve a decisao — `docs/adr/README.md`, regra 2);
  (4) o aceite deixar de declarar o que NAO faz: Q1-Q5 do ADR-0068 abertas, ruleset do ADR-0069 nao
      executado, DEC-28 no ADR-0073 e na folha R-054;
  (5) algum campo de assinatura for preenchido por agente.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_ADR_DIR: Final[Path] = _REPO_ROOT / "docs" / "adr"
_R054: Final[Path] = _REPO_ROOT / "docs" / "compliance" / "legal" / "R-054-RATIFICACAO-CITACOES-POR-LINHA.md"
_PACOTE: Final[Path] = _REPO_ROOT / "docs" / "compliance" / "human-windows" / "R-079-dec29-adrs-0068-0073.md"

_URL: Final[str] = "https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6566975547"

#: sha256 do texto de cada ADR no commit base `f3e45587`, sem o paragrafo de Status e sem a secao de
#: aceite (ver `_corpo`). Mudar a decisao de um ADR aceito pede ADR nova, nao novo pin.
_CORPO_BASE_SHA256: Final[dict[str, str]] = {
    "0068": "9c96f76ad8ded32c7467bcc515f5cebd092d69bbc5121c59236d0038e56669e6",
    "0069": "1636642644dd0bd88b161c00f240ac58fd3da95db180039c6beb485ec386e503",
    "0070": "748ebd0a6e936d151b19ac15df6f8ded879e126ffcd2d0fbe9ce34ebb9eb6c8b",
    "0071": "ab8c3540a14ba0cf120269b935c2e2ad353b5fb95748d8d59de499aa096be7fd",
    "0072": "4750f088c4aebcf6f204ad4693418e84ed0661cf485704a9ddb7c29e9c047189",
    "0073": "cbb9370d54b7280cd8db4be10e70b2d5f0c036a2992d5cece5f5160d2bfc932a",
}

_SECOES: Final[tuple[str, ...]] = (
    "\n## Decisão de aceite (DEC-29, 2026-11-01)",
    "\n## Decisao de aceite (DEC-29, 2026-11-01)",
)


def _texto(numero: str) -> str:
    achados = sorted(_ADR_DIR.glob(f"{numero}-*.md"))
    assert len(achados) == 1, f"ADR-{numero}: esperado um arquivo, achados {achados}"
    return achados[0].read_text(encoding="utf-8")


def _inicio_secao(texto: str) -> int:
    posicoes = [texto.find(s) for s in _SECOES if s in texto]
    assert len(posicoes) == 1, "esperada exatamente uma secao de aceite DEC-29"
    return posicoes[0]


def _secao(texto: str) -> str:
    return texto[_inicio_secao(texto) :]


def _corpo(texto: str) -> str:
    """Texto sem a secao de aceite e com o paragrafo de Status trocado por um marcador."""
    for s in _SECOES:
        if s in texto:
            texto = texto[: texto.index(s)]
    saida: list[str] = []
    pulando = False
    for linha in texto.split("\n"):
        if linha.startswith("**Status:**"):
            pulando = True
            saida.append("<STATUS>")
            continue
        if pulando and (not linha.strip() or linha.startswith("**Data:**")):
            pulando = False
        if not pulando:
            saida.append(linha)
    return "\n".join(saida).rstrip("\n") + "\n"


def _status(texto: str) -> str:
    linhas = texto.split("\n")
    i = next(n for n, linha in enumerate(linhas) if linha.startswith("**Status:**"))
    bloco = [linhas[i]]
    for linha in linhas[i + 1 :]:
        if not linha.strip() or linha.startswith("**Data:**"):
            break
        bloco.append(linha)
    return "\n".join(bloco)


@pytest.mark.parametrize("numero", sorted(_CORPO_BASE_SHA256))
def test_status_cita_a_decisao_do_dono_e_e_unico(numero: str) -> None:
    texto = _texto(numero)
    assert texto.count("**Status:**") == 1, f"ADR-{numero}: mais de um `**Status:**`"
    status = _status(texto)
    assert status.replace("*", "").split()[1] == "Accepted", f"ADR-{numero}: Status nao e Accepted"
    for marca in ("DEC-29", "#1084", "2026-11-01"):
        assert marca in status, f"ADR-{numero}: Status sem {marca!r}"


@pytest.mark.parametrize("numero", sorted(_CORPO_BASE_SHA256))
def test_secao_de_aceite_cita_o_ato_e_preserva_o_status_anterior(numero: str) -> None:
    secao = _secao(_texto(numero))
    assert _URL in secao, f"ADR-{numero}: secao de aceite sem o comentario do dono"
    assert "ADR-0007" in secao, f"ADR-{numero}: secao de aceite sem a regra de nao assinar"
    anterior = '"Proposed' in secao or '"**Proposed' in secao
    assert anterior, f"ADR-{numero}: secao de aceite nao cita o Status anterior"


@pytest.mark.parametrize("numero", sorted(_CORPO_BASE_SHA256))
def test_aceite_nao_reescreve_o_texto_do_adr(numero: str) -> None:
    obtido = hashlib.sha256(_corpo(_texto(numero)).encode("utf-8")).hexdigest()
    assert obtido == _CORPO_BASE_SHA256[numero], (
        f"ADR-{numero}: o texto fora do Status e da secao de aceite mudou desde o aceite (DEC-29)"
    )


@pytest.mark.parametrize("numero", sorted(_CORPO_BASE_SHA256))
def test_nenhum_campo_de_assinatura_preenchido_por_agente(numero: str) -> None:
    texto = _texto(numero)
    proibidos = ("ASSINADO", "Ratificado", "Assinatura: rodaquino")
    achados = [p for p in proibidos if p in texto]
    assert not achados, f"ADR-{numero}: aceite registrado por agente alega assinatura ({achados})"


def test_adr_0068_mantem_as_perguntas_abertas() -> None:
    texto = _texto("0068")
    for q in ("**Q1 —", "**Q2 —", "**Q3 —", "**Q4 —", "**Q5 —"):
        assert q in texto, f"ADR-0068 perdeu a pergunta {q}"
    secao = _secao(texto)
    assert "Não responde às perguntas abertas" in secao and "(Q1 a Q5)" in secao


def test_adr_0069_declara_o_ruleset_nao_executado() -> None:
    secao = _secao(_texto("0069"))
    assert "Nao executa a mudanca do ruleset" in secao
    assert "`20775655`" in secao


def test_adr_0073_e_folha_r054_registram_a_dec28() -> None:
    secao = _secao(_texto("0073"))
    assert "DEC-28" in secao and "R-054" in secao and "L9" in secao
    folha = _R054.read_text(encoding="utf-8")
    assert "A ratificação jurídica está pendente (linha L9)" not in folha
    assert "DEC-28" in folha and _URL in folha
    l9 = folha[folha.index("## L9:") :]
    tabela = l9[l9.index("| Ratificação do jurídico (L9) | |") :]
    for campo in ("Nome e papel", "Data", "Assinatura"):
        linha = next(x for x in tabela.splitlines() if x.startswith(f"| {campo} |"))
        assert linha == f"| {campo} | |", f"folha R-054 L9: campo {campo!r} preenchido por agente"


def test_pacote_de_registro_cita_o_ato_e_deixa_o_bloco_do_dono_em_branco() -> None:
    pacote = _PACOTE.read_text(encoding="utf-8")
    assert _URL in pacote and "DEC-28" in pacote and "DEC-29" in pacote
    assert "**Decisor (dono):** [nome]   **Data:** [  ]   **Assinatura:** [  ]" in pacote
