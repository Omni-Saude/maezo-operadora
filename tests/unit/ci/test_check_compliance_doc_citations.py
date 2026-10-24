"""Unit tests for the docs/compliance citation + GAP-REGISTER mirror gate (GOV-0.8 / D-31a).

Two layers, following the check_plans_counts test convention:

1. **Pure-core** tests drive `check_md_citations` / `verify_mirror` / `main` against
   synthetic `docs/compliance/` trees written to `tmp_path` — RED on the defect classes
   the fence exists to kill (a citation into a gitignored/absent tree — the original
   `docs/audits/.../GAP-REGISTER.yaml` pending-citation sin; a tampered custody block;
   a drifted per-line map; an empty scan) and GREEN on a reconciled tree whose mirror
   still hashes back to the declared custody sha256.
2. **Real-tree** test runs the gate against the SHIPPED `docs/compliance/` (md files +
   `GAP-REGISTER.yaml`) and asserts a non-vacuous PASS — the regression proof that the
   mirror and the guard landed together.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.ci.check_compliance_doc_citations import (
    MIRROR_REL,
    check_md_citations,
    main,
    verify_mirror,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_FONTESHA = hashlib.sha256(b"probe").hexdigest()


def _sha(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _indent_block(source: str) -> str:
    """Replica o gerador do espelho: cada linha da fonte com recuo de 2 espaços."""
    lines = source.split("\n")
    assert lines[-1] == ""
    return "\n".join(("  " + line if line else "") for line in lines[:-1])


_FONTE_MINIMA = """meta:
  generated: '2026-09-02'
gaps:
- id: AF-01
  title: exemplo
  notes: 'x'
- id: DU-01-b
  title: outro
work_packages:
- id: WP-X
  gaps:
  - AF-01
"""


def _mapa_da_fonte(source: str) -> dict[str, str]:
    """Mapa válido por construção — reusa o próprio recomputador da cerca."""
    from scripts.ci.check_compliance_doc_citations import _recompute_gap_map

    mapa = _recompute_gap_map(source)
    assert mapa is not None
    return mapa


def _chave(nome: str) -> str:
    """Espelha a regra de quoting do gerador: chave iniciada por digito vai quoted."""
    return f"'{nome}'" if nome[0].isdigit() else nome


def _mirror_text(
    source: str = _FONTE_MINIMA,
    *,
    sha: str | None = None,
    mapa: dict[str, str] | None = None,
    envelope_extra: str = "",
) -> str:
    sha_resolved = sha if sha is not None else _sha(source)
    mapa_resolved = mapa if mapa is not None else _mapa_da_fonte(source)
    mapa_rows = "\n".join(f"    {_chave(nome)}: '{intervalo}'" for nome, intervalo in mapa_resolved.items())
    envelope = (
        "espelho:\n"
        "  estado: teste\n"
        f"  fonte_sha256: {sha_resolved}\n"
        "  bloco_chave: registro_custodiado\n"
        "  mapa_gap_linhas:\n"
        f"{mapa_rows}\n"
        f"{envelope_extra}"
        "registro_custodiado: |\n"
        f"{_indent_block(source)}\n"
    )
    return envelope


def _escrever_arvore(
    tmp_path: Path,
    *,
    mds: dict[str, str],
    mirror: str | None = _mirror_text(),
    alvos: tuple[str, ...] = ("docs/plans/maezo-completion-program.md",),
) -> Path:
    (tmp_path / "docs" / "compliance").mkdir(parents=True)
    for rel, conteudo in mds.items():
        caminho = tmp_path / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")
    if mirror is not None:
        (tmp_path / MIRROR_REL).write_text(mirror, encoding="utf-8")
    for rel in alvos:
        caminho = tmp_path / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("alvo\n", encoding="utf-8")
    return tmp_path


def _rodar(
    tmp_path: Path,
    mds: dict[str, str],
    *,
    mirror: str | None = _mirror_text(),
    alvos: tuple[str, ...] = ("docs/plans/maezo-completion-program.md",),
) -> tuple[int, list[str]]:
    root = _escrever_arvore(tmp_path, mds=mds, mirror=mirror, alvos=alvos)
    saida: list[str] = []
    codigo = main(repo_root=root, files=[Path(rel) for rel in mds], stream=saida.append)
    return codigo, saida


# ── 1. citações de caminho ──────────────────────────────────────────────────────


def test_citation_viva_passa(tmp_path: Path) -> None:
    codigo, saida = _rodar(
        tmp_path,
        {"docs/compliance/nota.md": "Ver `docs/plans/maezo-completion-program.md` para a decisão.\n"},
    )
    assert codigo == 0, saida
    assert any("PASSA" in linha for linha in saida)


def test_citation_morta_falha_com_arquivo_e_linha(tmp_path: Path) -> None:
    dead = "docs/audits/maezo-deep-audit/remediation/GAP-REGISTER.yaml"
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": f"Registro em `{dead}`.\n"})
    assert codigo == 1
    achado = next(linha for linha in saida if "FALHA docs/compliance/nota.md:1" in linha)
    assert dead in achado
    assert "caminho inexistente" in achado


def test_citation_em_arvore_gitignored_e_a_classe_derrotada(tmp_path: Path) -> None:
    """A classe do pecado original: apontar para árvore gitignored ausente deve falhar."""
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "Fonte em `docs/prompts/qualquer.md`.\n"})
    assert codigo == 1
    assert any("docs/prompts/qualquer.md" in linha for linha in saida)


def test_citacao_de_linha_verifica_so_o_arquivo(tmp_path: Path) -> None:
    """Número de linha é informativo (precedente R-089 F4): arquivo vivo, linha stale -> passa."""
    codigo, saida = _rodar(
        tmp_path,
        {"docs/compliance/nota.md": "Ver `docs/plans/maezo-completion-program.md:99999` e `:12-34`.\n"},
    )
    assert codigo == 0, saida


def test_linha_nao_salva_arquivo_morto(tmp_path: Path) -> None:
    codigo, _ = _rodar(tmp_path, {"docs/compliance/nota.md": "Ver `docs/sumiu.md:12`.\n"})
    assert codigo == 1


def test_anchor_e_aparado(tmp_path: Path) -> None:
    codigo, saida = _rodar(
        tmp_path,
        {"docs/compliance/nota.md": "Ver `docs/plans/maezo-completion-program.md#secao-decisoes`.\n"},
    )
    assert codigo == 0, saida


def test_elisao_contada_mais_nao_verificada(tmp_path: Path) -> None:
    codigo, saida = _rodar(
        tmp_path,
        {
            "docs/compliance/nota.md": (
                "Família `docs/audits/...` (elisão declarada); "
                "decisão em `docs/plans/maezo-completion-program.md`.\n"
            )
        },
    )
    assert codigo == 0, saida
    resumo = next(linha for linha in saida if "citações verificadas" in linha)
    assert "1 elisões" in resumo


def test_diretorio_com_barra_final(tmp_path: Path) -> None:
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "Casa em `docs/compliance/`.\n"})
    assert codigo == 0, saida


def test_varredura_vazia_falha_nao_vacuidade(tmp_path: Path) -> None:
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "Nada a citar aqui.\n"})
    assert codigo == 1
    assert any("varredura vazia" in linha for linha in saida)


def test_sem_citacao_backtick_nao_elegivel(tmp_path: Path) -> None:
    """Token fora das raízes canônicas não é citação de caminho enraizada."""
    achados, stats = check_md_citations(
        Path("docs/compliance/x.md"),
        "Roda `uv run python -m pytest` e veja `https://exemplo.tld/a`.\n",
        lambda token: False,
    )
    assert achados == []
    assert stats["eligible"] == 0


# ── 2. integridade do espelho ───────────────────────────────────────────────────


def test_espelho_integro_passa(tmp_path: Path) -> None:
    codigo, saida = _rodar(
        tmp_path, {"docs/compliance/nota.md": "Ver `docs/plans/maezo-completion-program.md`.\n"}
    )
    assert codigo == 0, saida
    resumo = next(linha for linha in saida if "linhas de mapa conferidas" in linha)
    assert "2/2" in resumo


def test_bloco_violado_falha_no_sha(tmp_path: Path) -> None:
    espelho = _mirror_text().replace("  notes: 'x'\n", "  notes: 'Y VIOLADO'\n", 1)
    assert espelho != _mirror_text()
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "x\n"}, mirror=espelho)
    assert codigo == 1
    assert any("NÃO bate com fonte_sha256" in linha for linha in saida)


def test_sha_declarado_falso_falha(tmp_path: Path) -> None:
    espelho = _mirror_text(sha=_FONTESHA)
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "x\n"}, mirror=espelho)
    assert codigo == 1
    assert any(_FONTESHA in linha for linha in saida)


def test_linha_sem_recuo_torna_bloco_irrecuperavel(tmp_path: Path) -> None:
    espelho = _mirror_text().replace("registro_custodiado: |\n", "registro_custodiado: |\nsem recuo\n", 1)
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "x\n"}, mirror=espelho)
    assert codigo == 1
    assert any("irrecuperável" in linha for linha in saida)


def test_mapa_divergente_falha(tmp_path: Path) -> None:
    mapa = _mapa_da_fonte(_FONTE_MINIMA)
    nome = sorted(mapa)[0]
    inicio, fim = mapa[nome].split("-")
    mapa_perturbado = dict(mapa)
    mapa_perturbado[nome] = f"{inicio}-{int(fim) + 1}"
    espelho = _mirror_text(mapa=mapa_perturbado)
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "x\n"}, mirror=espelho)
    assert codigo == 1
    assert any(f"mapa_gap_linhas divergente para {nome}" in linha for linha in saida)


def test_mapa_sem_entrada_falha(tmp_path: Path) -> None:
    mapa = _mapa_da_fonte(_FONTE_MINIMA)
    nome = sorted(mapa)[0]
    mapa_faltante = {k: v for k, v in mapa.items() if k != nome}
    espelho = _mirror_text(mapa=mapa_faltante)
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "x\n"}, mirror=espelho)
    assert codigo == 1
    assert any(f"não declara o gap {nome}" in linha for linha in saida)


def test_mapa_com_gap_fantasma_falha(tmp_path: Path) -> None:
    mapa = dict(_mapa_da_fonte(_FONTE_MINIMA))
    mapa["GAP-FANTASMA"] = "1-2"
    espelho = _mirror_text(mapa=mapa)
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "x\n"}, mirror=espelho)
    assert codigo == 1
    assert any("declara gap inexistente no bloco: GAP-FANTASMA" in linha for linha in saida)


def test_envelope_sem_sha_falha(tmp_path: Path) -> None:
    espelho = _mirror_text().replace("  fonte_sha256: ", "  fonte_hash: ", 1)
    codigo, saida = _rodar(tmp_path, {"docs/compliance/nota.md": "x\n"}, mirror=espelho)
    assert codigo == 1
    assert any("sem `fonte_sha256`" in linha for linha in saida)


def test_espelho_ausente_falha(tmp_path: Path) -> None:
    codigo, saida = _rodar(
        tmp_path,
        {"docs/compliance/nota.md": "Ver `docs/plans/maezo-completion-program.md`.\n"},
        mirror=None,
    )
    assert codigo == 1
    assert any("espelho rastreado ausente" in linha for linha in saida)


def test_verify_mirror_isolado(tmp_path: Path) -> None:
    """verify_mirror não precisa de árvore — só do texto do espelho."""
    achados, stats = verify_mirror(_mirror_text())
    assert achados == []
    assert stats["verificados"] == 2
    assert stats["mapa_ids"] == 2


def test_gap_id_quoted_no_bloco_e_coberto_pelo_mapa() -> None:
    """F-1 (gate cego Tier-2, PR #931): ids YAML-quoted no bloco custodiado — `- id: '9.1'`,
    15 ocorrências reais na fonte pinada (9.x/10.x/11.x, quoted porque iniciam por dígito) —
    têm que entrar no mapa por linha. Positivo: extrator cobre quoted e não-quoted.
    Negativo: mapa que OMITE o id quoted é achado da cerca (a classe do defeito F-1)."""
    fonte_quoted = (
        "meta:\n"
        "gaps:\n"
        "- id: AF-01\n"
        "  title: plano\n"
        "- id: '9.1'\n"
        "  title: quoted\n"
        "work_packages:\n"
        "- id: WP-X\n"
        "  gaps:\n"
        "  - AF-01\n"
    )
    espelho = _mirror_text(source=fonte_quoted)
    assert "    '9.1': '5-6'" in espelho  # chave quoted, como no espelho embarcado
    achados, stats = verify_mirror(espelho)
    assert achados == []
    assert stats["verificados"] == 2  # AF-01 (não-quoted) + 9.1 (quoted)
    assert stats["mapa_ids"] == 2

    mapa_sem_quoted = {"AF-01": _mapa_da_fonte(fonte_quoted)["AF-01"]}
    achados_omissao, _ = verify_mirror(_mirror_text(source=fonte_quoted, mapa=mapa_sem_quoted))
    assert any("não declara o gap 9.1" in achado for achado in achados_omissao)


# ── 3. árvore real (prova de regressão do espelho embarcado) ────────────────────


def test_arvore_real_passa_nao_vacuo() -> None:
    """O espelho SHIPPED confere com a custódia declarada e os .md resolvem."""
    md_files = sorted(rel for rel in (_REPO_ROOT / "docs" / "compliance").rglob("*.md") if rel.is_file())
    saida: list[str] = []
    codigo = main(
        repo_root=_REPO_ROOT,
        files=[rel.relative_to(_REPO_ROOT) for rel in md_files],
        stream=saida.append,
    )
    assert codigo == 0, saida
    resumo = next(linha for linha in saida if "citações verificadas" in linha)
    assert "0 inexistentes" in resumo
    mapa_resumo = next(linha for linha in saida if "linhas de mapa conferidas" in linha)
    assert "/269" in mapa_resumo
