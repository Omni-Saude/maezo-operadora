"""O interruptor de zona da narrativa do dossiê — prova que ele RECUSA.

Um interruptor fail-closed só vale se a recusa for testada. Cada teste aqui fixa um
modo de falha e a razão estável que ele deve devolver, para que um refactor futuro
não transforme silenciosamente "não ratificado" em "zona geral".

O teste que mais importa é `test_draft_do_repositorio_mantem_phi`: ele lê o artefato
REAL do repositório e exige que, como está commitado, a narrativa continue na zona
PHI. Se alguém ratificar por engano num commit, este teste falha.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from maezo.platform.privacy.dossier_zone import (
    REASON_DIGEST_MISMATCH,
    REASON_FILE_NOT_FOUND,
    REASON_NOT_RATIFIED,
    REASON_PLACEHOLDER,
    REASON_REVIEW_MISSING,
    REASON_ZONE_NOT_GENERAL,
    SYNTHETIC_ONLY_ENV,
    DossierZoneNotRatifiedError,
    dossier_narrative_requires_phi_zone,
    load_ratification,
    resolve_ratification_path,
)

_GRAPH = Path("src/maezo/agents/rafael/graph.py")


def _digest_do_grafo() -> str:
    return hashlib.sha256(_GRAPH.read_bytes()).hexdigest()


def _artefato(tmp_path: Path, **campos: object) -> Path:
    base: dict[str, object] = {
        "status": "RATIFICADO",
        "ratificado": True,
        "zona_declarada": "GERAL",
        "dpo_review": "APPROVED",
        "medico_auditor_review": "APPROVED",
        "ratificado_por": "Fulana de Tal — DPO",
        "data_ratificacao": "2026-08-18",
        "graph_sha256": _digest_do_grafo(),
        "fundamentacao": "Narrativa opera sobre pseudo-ids; parecer em anexo.",
    }
    base.update(campos)
    linhas = []
    for chave, valor in base.items():
        if isinstance(valor, bool):
            linhas.append(f"{chave}: {'true' if valor else 'false'}")
        else:
            # Citado SEMPRE: sem aspas o YAML converteria "true" em booleano e a
            # data num objeto `date`, e o teste mediria outra coisa que nao ele mesmo.
            escapado = str(valor).replace('"', '\\"')
            linhas.append(f'{chave}: "{escapado}"')
    caminho = tmp_path / "dossier-narrative-zone.yaml"
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


def test_draft_do_repositorio_mantem_phi() -> None:
    """O artefato COMMITADO deve manter a narrativa na zona PHI."""
    assert dossier_narrative_requires_phi_zone() is True

    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(resolve_ratification_path())
    assert exc.value.reason == REASON_NOT_RATIFIED


def test_arquivo_ausente_mantem_phi(tmp_path: Path) -> None:
    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(tmp_path / "nao-existe.yaml")
    assert exc.value.reason == REASON_FILE_NOT_FOUND


def test_ratificado_falso(tmp_path: Path) -> None:
    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(_artefato(tmp_path, ratificado=False))
    assert exc.value.reason == REASON_NOT_RATIFIED


def test_ratificado_string_nao_conta(tmp_path: Path) -> None:
    """`ratificado: "true"` (texto) NAO e' o booleano `true`."""
    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(_artefato(tmp_path, ratificado="true"))
    assert exc.value.reason == REASON_NOT_RATIFIED


def test_placeholder_remanescente(tmp_path: Path) -> None:
    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(_artefato(tmp_path, ratificado_por="PENDING-NOME-E-FUNCAO"))
    assert exc.value.reason == REASON_PLACEHOLDER


def test_zona_diferente_de_geral(tmp_path: Path) -> None:
    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(_artefato(tmp_path, zona_declarada="PHI"))
    assert exc.value.reason == REASON_ZONE_NOT_GENERAL


@pytest.mark.parametrize("campo", ["dpo_review", "medico_auditor_review"])
def test_uma_revisao_sozinha_nao_basta(tmp_path: Path, campo: str) -> None:
    """A pergunta e' juridica E clinica: falta uma, nao ativa."""
    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(_artefato(tmp_path, **{campo: "REJECTED"}))
    assert exc.value.reason == REASON_REVIEW_MISSING


def test_digest_que_nao_bate(tmp_path: Path) -> None:
    """Prompt editado depois da aprovacao devolve a narrativa para a zona PHI."""
    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(_artefato(tmp_path, graph_sha256="0" * 64))
    assert exc.value.reason == REASON_DIGEST_MISMATCH


def test_artefato_completo_ativa_zona_geral(tmp_path: Path) -> None:
    """O caminho felizmente completo — e SO' ele — libera a zona geral."""
    ratificacao = load_ratification(_artefato(tmp_path))
    assert ratificacao.zona_declarada == "GERAL"
    assert ratificacao.graph_sha256 == _digest_do_grafo()
    assert ratificacao.ratificado_por.startswith("Fulana")


def test_override_por_ambiente_e_respeitado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com o override apontando para um artefato valido, a zona vira geral."""
    caminho = _artefato(tmp_path)
    monkeypatch.setenv("MAEZO_DOSSIER_ZONE_RATIFICATION", str(caminho))
    assert dossier_narrative_requires_phi_zone() is False


def test_override_para_lixo_mantem_phi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Arquivo ilegivel nao vira 'zona geral' por acidente."""
    ruim = tmp_path / "ruim.yaml"
    ruim.write_text("isto: [nao\n  fecha", encoding="utf-8")
    monkeypatch.setenv("MAEZO_DOSSIER_ZONE_RATIFICATION", str(ruim))
    assert dossier_narrative_requires_phi_zone() is True


# ---------------------------------------------------------------------------
# Declaração de ambiente somente-sintético — a segunda via, e ela NÃO é ratificação
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valor", ["1", "true", "TRUE", "yes", "Yes"])
def test_ambiente_sintetico_libera_zona_geral(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv(SYNTHETIC_ONLY_ENV, valor)
    assert dossier_narrative_requires_phi_zone() is False


@pytest.mark.parametrize("valor", ["0", "false", "no", "", " ", "sim", "talvez", "2"])
def test_valor_ambiguo_nao_liga_nada(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    """Uma declaração desta natureza não pode ser ligada por acidente de digitação."""
    monkeypatch.setenv(SYNTHETIC_ONLY_ENV, valor)
    assert dossier_narrative_requires_phi_zone() is True


def test_ambiente_sintetico_nao_e_ratificacao(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chave de ambiente não faz o artefato virar ratificado.

    Se algum dia ela passar a satisfazer `load_ratification`, alguém conseguiu
    fabricar uma aprovação de DPO com uma variável de ambiente — que é exatamente o
    que o artefato existe para impedir.
    """
    monkeypatch.setenv(SYNTHETIC_ONLY_ENV, "1")
    with pytest.raises(DossierZoneNotRatifiedError) as exc:
        load_ratification(resolve_ratification_path())
    assert exc.value.reason == REASON_NOT_RATIFIED


def test_ausencia_da_variavel_mantem_phi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SYNTHETIC_ONLY_ENV, raising=False)
    assert dossier_narrative_requires_phi_zone() is True
