"""E-SF-32 — guardas de citação normativa (pins DOU/Senado de 2026-11-16).

Origem no plano de funil (SALES-FUNNEL-DECISIONS-PLAN-2026-11, IMPLEMENTATION-PLAN.md bloco
E-SF-32 e seção E; registros REG-25, REG-26, REG-27, REG-28 do DEC-REG; CA-15):

- EP-REG-25 ``test_no_citation_mp2177_art5_reajuste`` — o localizador "MP 2.177-44, art. 5º,
  XVIII–XX" (reajuste / faixas etárias) não existe: o art. 5º da MP trata da Lei 10.185
  (baseline §1 P-6, §2.10). Falha: ``locator_refuted_REG-25``.
- EP-REG-26a ``test_citations_rn413_rn558_point_to_dou`` — a cópia "Minuta de Norma … alterada"
  (gov.br/ans, audiência pública 50, SEI) não é fonte de redação vigente; RN 413/558 se citam
  pela matéria DOU (baseline §2.3/§2.4). Falha: ``minuta_not_source_of_vigente``.
- EP-REG-26b ``test_rn558_art5_not_rendered`` — o art. 5º caput da RN 558/2022 tem duas
  redações oficiais e a vigente está indeterminada (GAP-2r, RL-REG-26): não é citado em tela,
  texto ao consumidor nem regra de produto. Falha: ``citation_pending_GAP-2r``.
- EP-REG-27a ``test_rn561_cited_from_dou`` — RN 561/2022 se cita pela matéria DOU, não pela
  cópia Saúde Legis (bvsms, nome de arquivo ``res0561_…``). Falha: ``rn561_not_cited_from_dou``.
- EP-REG-28a ``test_rn500_not_cited_as_buyer_confirmation_duty`` — o texto da RN 500/2022 tem
  grep "confirma" = 0 (baseline §2.12); atribuir a ela um dever de confirmação de dados pelo
  comprador é norma inventada (REG-28 opção B rejeitada). Falha:
  ``rn500_cited_as_buyer_confirmation_duty_REG-28``.

Escopo varrido (declarado): arquivos de texto RASTREADOS (``git ls-files``) sob

- ``docs/``, ``spec/``, ``src/`` e ``config/`` para REG-25, 26a, 27a e 28a — é onde vivem
  citações normativas (especificação, contratos, BPMN/DMN/políticas, código, prompts e
  templates, configuração). ``tests/`` fica fora: as guardas e seus casos negativos carregam
  os próprios tokens proibidos.
- ``src/``, ``spec/``, ``config/`` e ``docs/`` (menos ``docs/compliance/``) para REG-26b — as superfícies que
  renderizam texto ou viram regra de produto. ``docs/compliance/`` fica fora de propósito: é a
  árvore de evidência onde as DUAS redações do art. 5º ficam guardadas, rotuladas "NÃO ÂNCORA —
  redação vigente a confirmar" (``textos-base/RN-558-2022.md``), como manda o REG-26 (ii).

As regras são por LINHA (os tokens têm de co-ocorrer na mesma linha). Cada guarda tem caso
negativo injetado em ``tmp_path`` com o texto que o plano nomeia, e caso positivo vizinho.

Dívida pré-existente (relatada, não "consertada" aqui): cinco linhas de dois documentos de
outra janela citam a RN 561 pela cópia Saúde Legis. Ficam listadas, linha exata a linha exata,
em ``_RN561_KNOWN_DEBT``; a lista é finita e auto-verificada (entrada que deixa de existir
FALHA, para a lista só encolher) — o mesmo padrão das "exceções exatas, finitas e
auto-verificadas" de ``scripts/ci/check_doc_symbol_citations.py``. A proposta de errata está no
relatório do E-SF-32.

Sem rede, sem engine, sem dependência nova: só stdlib + ``git ls-files``.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEXTOS_BASE = REPO / "docs" / "compliance" / "legal" / "textos-base"

CITATION_ROOTS: tuple[str, ...] = ("docs", "spec", "src", "config")
RENDER_ROOTS: tuple[str, ...] = ("src", "spec", "config", "docs")
RENDER_EXCLUDED_PREFIX = "docs/compliance/"

TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".py",
        ".yaml",
        ".yml",
        ".json",
        ".jsonl",
        ".dmn",
        ".bpmn",
        ".xml",
        ".html",
        ".ts",
        ".tsx",
        ".mjs",
        ".java",
        ".sql",
        ".mako",
        ".j2",
        ".jinja",
    }
)


@dataclass(frozen=True)
class Violation:
    path: str
    line_no: int
    token: str
    line: str


Corpus = tuple[tuple[str, tuple[str, ...]], ...]


def _read_lines(path: Path) -> tuple[str, ...]:
    return tuple(path.read_text(encoding="utf-8", errors="replace").splitlines())


@lru_cache(maxsize=4)
def _tracked_corpus(roots: tuple[str, ...]) -> Corpus:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *roots],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    rels = sorted(p for p in out.split("\0") if p and Path(p).suffix.lower() in TEXT_SUFFIXES)
    corpus = tuple((rel, _read_lines(REPO / rel)) for rel in rels if (REPO / rel).is_file())
    # Fail-closed: corpus vazio (checkout sem git, raiz renomeada) nunca passa em silêncio.
    assert len(corpus) > 100, f"corpus suspeitamente pequeno para {roots}: {len(corpus)} arquivos"
    return corpus


def _dir_corpus(base: Path, roots: Iterable[str]) -> Corpus:
    files: list[tuple[str, tuple[str, ...]]] = []
    for root in roots:
        for path in sorted((base / root).rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                files.append((path.relative_to(base).as_posix(), _read_lines(path)))
    return tuple(files)


def _scan(corpus: Corpus, token: str, predicate: Callable[[str], bool]) -> list[Violation]:
    return [
        Violation(rel, i, token, line)
        for rel, lines in corpus
        for i, line in enumerate(lines, start=1)
        if predicate(line)
    ]


def _render(violations: list[Violation]) -> str:
    return "\n".join(f"{v.token}: {v.path}:{v.line_no}: {v.line.strip()[:200]}" for v in violations)


# --------------------------------------------------------------------------- REG-25 (MP 2.177-44)

MOTIVO_REG25 = "locator_refuted_REG-25"
_MP2177 = re.compile(r"2\.?177-44")
# "art. 5º" / "artigo 5" / "Art. 5°" — nunca "art. 15", "art. 50", "art. 35-E".
_ART5 = re.compile(r"(?<![0-9])\bart(?:igo|\.)?\s*5(?![0-9])", re.IGNORECASE)
_ROMAN_XVIII_XX = re.compile(r"\b(?:XVIII|XIX|XX)\b")
_REAJUSTE_FAIXA = re.compile(r"reajust|faixa", re.IGNORECASE)


def _is_reg25_violation(line: str) -> bool:
    return bool(
        _MP2177.search(line)
        and _ART5.search(line)
        and (_ROMAN_XVIII_XX.search(line) or _REAJUSTE_FAIXA.search(line))
    )


def find_reg25(corpus: Corpus) -> list[Violation]:
    return _scan(corpus, MOTIVO_REG25, _is_reg25_violation)


# --------------------------------------------------------------------------- REG-26a (RN 413/558)

MOTIVO_REG26A = "minuta_not_source_of_vigente"
# A cópia oficial "Minuta de Norma … alterada" (SEI 33910.025318/2024-75), servida como
# .../audiencias-publicas/50/SEI_<n>_Minuta_de_Norma.pdf.
_MINUTA = re.compile(r"SEI_\d+_Minuta_de_Norma", re.IGNORECASE)


def find_reg26a(corpus: Corpus) -> list[Violation]:
    return _scan(corpus, MOTIVO_REG26A, lambda line: bool(_MINUTA.search(line)))


# --------------------------------------------------------------------------- REG-26b (RN 558 art. 5º)

MOTIVO_REG26B = "citation_pending_GAP-2r"
_RN558 = re.compile(
    r"\bRN\s*(?:n[º°o.]*\s*)?558(?![0-9])|\b558/2022\b|Resolu[çc][ãa]o Normativa[^\n]{0,24}?558"
)
# Caput: "art. 5º" NÃO seguido de número, §, parágrafo ou inciso (possessivo: o glifo não
# pode ser "devolvido" para escapar do lookahead).
_ART5_CAPUT = re.compile(
    r"(?<![0-9])\bart(?:igo|\.)?\s*5(?![0-9])(?:º|°|o|\.º)?+(?!\s*,?\s*(?:§|par[áa]grafo|inc))",
    re.IGNORECASE,
)


def find_reg26b(corpus: Corpus) -> list[Violation]:
    scoped = tuple(item for item in corpus if not item[0].startswith(RENDER_EXCLUDED_PREFIX))
    return _scan(scoped, MOTIVO_REG26B, lambda line: bool(_RN558.search(line) and _ART5_CAPUT.search(line)))


# --------------------------------------------------------------------------- REG-27a (RN 561)

MOTIVO_REG27A = "rn561_not_cited_from_dou"
# Nome de arquivo da cópia Saúde Legis (bvsms) da RN 561/2022.
_RN561_SAUDELEGIS = re.compile(r"res0*561_\d", re.IGNORECASE)

# Dívida pré-existente (head 211c6ae): linha EXATA por arquivo. Não é isenção por arquivo nem
# por marcador: qualquer outra linha (inclusive uma edição destas) volta a falhar.
_RN561_KNOWN_DEBT: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "docs/compliance/legal/R-054-RATIFICACAO-CITACOES-POR-LINHA.md",
            "- https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0561_30_12_2022.html",
        ),
        (
            "docs/compliance/legal/R-054-RATIFICACAO-CITACOES-POR-LINHA.md",
            "| RN 561/2022 (Saúde Legis) | https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/"
            "res0561_30_12_2022.html | `a04577312886993a9aeafbbb9b27f7d49ec58f587534a2506e9fec5c883be7ed`"
            " | 21476 |",
        ),
        (
            "docs/compliance/rn-currency-review.md",
            '| **RN 412/2016** — `SP-OP-CANCEL-001.md:5,19,236` | Cancelamento a pedido; *"593 substitui'
            ' 412"* | Cancelamento a pedido / exclusão de beneficiário | **superseded (by RN 561, NOT 593)**'
            " | **RN 561/2022** (in force 01/02/2023) | bvsms `res0561_30_12_2022.html`; ans.gov.br"
            " id=MzMyNA%3D%3D (RN412) |",
        ),
        (
            "docs/compliance/rn-currency-review.md",
            "| **RN 561/2022** | **Cancelamento a pedido** / exclusão (revogou RN 412/2016) | vigente"
            " (01/02/2023) | Successor of the RN 412 that `SP-OP-CANCEL-001` cites for cancelamento a"
            " pedido. | bvsms `res0561_30_12_2022.html` |",
        ),
        (
            "docs/compliance/rn-currency-review.md",
            "- RN 412/2016 → 561/2022: `https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/"
            "res0561_30_12_2022.html` · `https://www.ans.gov.br/component/legislacao/?view=legislacao"
            "&task=TextoLei&format=raw&id=MzMyNA%3D%3D`",
        ),
    }
)


def find_reg27a(corpus: Corpus, known_debt: frozenset[tuple[str, str]] = frozenset()) -> list[Violation]:
    # Cada entrada de dívida cobre UMA ocorrência: cópia idêntica da mesma linha volta a falhar.
    seen: set[tuple[str, str]] = set()
    out: list[Violation] = []
    for v in _scan(corpus, MOTIVO_REG27A, lambda line: bool(_RN561_SAUDELEGIS.search(line))):
        key = (v.path, v.line)
        if key in known_debt and key not in seen:
            seen.add(key)
            continue
        out.append(v)
    return out


# --------------------------------------------------------------------------- REG-28a (RN 500)

MOTIVO_REG28A = "rn500_cited_as_buyer_confirmation_duty_REG-28"
_RN500 = re.compile(
    r"\bRN\s*(?:n[º°o.]*\s*)?500(?![0-9])|\b500/2022\b|Resolu[çc][ãa]o Normativa[^\n]{0,24}?500"
)
_CONFIRMA = re.compile(r"confirma", re.IGNORECASE)
_BUYER = re.compile(r"comprador|proponente|interessad|contratante|consumidor|buyer", re.IGNORECASE)


def find_reg28a(corpus: Corpus) -> list[Violation]:
    return _scan(
        corpus,
        MOTIVO_REG28A,
        lambda line: bool(_RN500.search(line) and _CONFIRMA.search(line) and _BUYER.search(line)),
    )


# =========================================================================== testes — árvore real


def test_no_citation_mp2177_art5_reajuste() -> None:
    found = find_reg25(_tracked_corpus(CITATION_ROOTS))
    assert not found, _render(found)


def test_citations_rn413_rn558_point_to_dou() -> None:
    found = find_reg26a(_tracked_corpus(CITATION_ROOTS))
    assert not found, _render(found)


def test_rn558_art5_not_rendered() -> None:
    found = find_reg26b(_tracked_corpus(RENDER_ROOTS))
    assert not found, _render(found)


def test_rn561_cited_from_dou() -> None:
    found = find_reg27a(_tracked_corpus(CITATION_ROOTS), _RN561_KNOWN_DEBT)
    assert not found, _render(found)


def test_rn561_known_debt_is_exact_and_present() -> None:
    """A lista de dívida só encolhe: entrada cujo texto exato sumiu (errata aplicada) FALHA."""
    corpus = dict(_tracked_corpus(CITATION_ROOTS))
    stale = [(path, line) for path, line in sorted(_RN561_KNOWN_DEBT) if line not in corpus.get(path, ())]
    assert not stale, f"remova de _RN561_KNOWN_DEBT as entradas que não existem mais: {stale}"


def test_rn500_not_cited_as_buyer_confirmation_duty() -> None:
    found = find_reg28a(_tracked_corpus(CITATION_ROOTS))
    assert not found, _render(found)


# --------------------------------------------------------------------------- pins (artefatos)

_PINS: dict[str, tuple[str, str]] = {
    "MP-2177-44-2001.md": (
        "https://legis.senado.leg.br/norma/559033/publicacao/15677086",
        "04cc58f15dc7399df68b13346f9f4f14529f210d9436ba204d263138d74df9f8",
    ),
    "RN-413-2016.md": (
        "https://www.in.gov.br/web/dou/-/resolucao-normativa-rn-n-413-de-11-de-novembro-de-2016-24504005",
        "a56fcb7e4ab88a24e48e90f7dba038bc812b0275b923272269dbb12a40adbf2e",
    ),
    "RN-558-2022.md": (
        "https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-558-de-14-de-dezembro-de-2022-455014535",
        "288e19f11a01025d72e6666b98e6895a85019dfa1ee8b5af77e66a297dee7ce2",
    ),
    "RN-561-2022.md": (
        "https://www.in.gov.br/web/dou/-/resolucao-normativa-ans-n-561-de-15-de-dezembro-de-2022-454883907",
        "3821af72d32550a6cbe5bca4e6ac7d7584f7af7e116cb5ce6d05c60df6c60bc9",
    ),
    "RN-500-2022.md": (
        "https://www.in.gov.br/web/dou/-/resolucao-normativa-rn-n-500-de-30-de-marco-de-2022-390313494",
        "acac01119a85e98a710dcd8f6c3c0508eb9e254a32190efa153d8789e81e84eb",
    ),
}


@pytest.mark.parametrize("name", sorted(_PINS))
def test_textos_base_pin_points_to_primary_source(name: str) -> None:
    url, sha = _PINS[name]
    text = (TEXTOS_BASE / name).read_text(encoding="utf-8")
    head = text.split("\n---\n", 1)[0]
    assert url in head, f"{name}: cabeçalho sem a URL primária"
    assert sha in head, f"{name}: cabeçalho sem o sha256 registrado na baseline"
    assert "EXCERTO; integral = GAP" in head, f"{name}: o excerto tem de se declarar excerto"
    assert not _MINUTA.search(text), f"{name}: texto-base não pode apontar a minuta"
    assert not _RN561_SAUDELEGIS.search(text), f"{name}: texto-base não pode apontar a cópia Saúde Legis"


def test_rn558_textos_base_marks_art5_caput_as_non_anchor() -> None:
    text = (TEXTOS_BASE / "RN-558-2022.md").read_text(encoding="utf-8")
    gap_section = text.split("## GAP-2r", 1)
    assert len(gap_section) == 2, "seção GAP-2r ausente"
    # As duas redações ficam SÓ na seção GAP-2r, ambas rotuladas como não âncora.
    for opening in ("Art. 5° Nos planos privados", "Nos planos privados de assistência à saúde, individual"):
        assert opening not in gap_section[0], f"redação do art. 5º caput fora da seção GAP-2r: {opening!r}"
        assert opening in gap_section[1], f"redação ausente da seção GAP-2r: {opening!r}"
    assert gap_section[1].count("NÃO ÂNCORA") >= 2


# =========================================================================== casos injetados


def _inject(tmp_path: Path, rel: str, text: str) -> None:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_reg25_guard_rejects_injected_locator(tmp_path: Path) -> None:
    _inject(tmp_path, "docs/x/artefato.md", "Reajuste coletivo: MP 2.177-44, art. 5º, XVIII.\n")
    vizinho = "Lei 9.656, art. 15 (redação dada pela MP 2.177-44/2001), faixas.\n"
    _inject(tmp_path, "docs/x/vizinho.md", vizinho)
    found = find_reg25(_dir_corpus(tmp_path, CITATION_ROOTS))
    assert [(v.path, v.token) for v in found] == [("docs/x/artefato.md", MOTIVO_REG25)]


def test_reg26a_guard_rejects_injected_minuta_source(tmp_path: Path) -> None:
    url = (
        "https://www.gov.br/ans/pt-br/arquivos/acesso-a-informacao/participacao-da-sociedade/"
        "audiencias-publicas/50/SEI_31143600_Minuta_de_Norma.pdf"
    )
    _inject(tmp_path, "spec/x/regra.yaml", f"fonte_regulatoria: 'RN 413/2016 art. 5º §2º — {url}'\n")
    _inject(
        tmp_path,
        "spec/x/ok.yaml",
        "fonte_regulatoria: 'RN 413/2016 — https://www.in.gov.br/web/dou/-/"
        "resolucao-normativa-rn-n-413-de-11-de-novembro-de-2016-24504005'\n",
    )
    found = find_reg26a(_dir_corpus(tmp_path, CITATION_ROOTS))
    assert [(v.path, v.token) for v in found] == [("spec/x/regra.yaml", MOTIVO_REG26A)]


@pytest.mark.parametrize(
    "line",
    [
        "Conforme RN 558, art. 5º, a declaração é obrigatória.",
        "<p>RN nº 558/2022, art. 5° caput</p>",
        "Base: Resolução Normativa ANS nº 558, artigo 5",
    ],
)
def test_reg26b_guard_rejects_injected_template(tmp_path: Path, line: str) -> None:
    _inject(tmp_path, "src/maezo/x/templates/carta.html", line + "\n")
    found = find_reg26b(_dir_corpus(tmp_path, RENDER_ROOTS))
    assert [(v.path, v.token) for v in found] == [("src/maezo/x/templates/carta.html", MOTIVO_REG26B)]


@pytest.mark.parametrize(
    "line",
    [
        "RN 558, art. 5º, §4º — vedada a alegação de omissão",
        "RN 558/2022, art. 50",
        "RN 558/2022, art. 15",
        "RN 413/2016, art. 5º, §2º — 25 dias",
    ],
)
def test_reg26b_guard_spares_other_dispositivos(tmp_path: Path, line: str) -> None:
    _inject(tmp_path, "spec/x/regra.md", line + "\n")
    assert find_reg26b(_dir_corpus(tmp_path, RENDER_ROOTS)) == []


def test_reg26b_scope_excludes_compliance_evidence_tree(tmp_path: Path) -> None:
    _inject(tmp_path, "docs/compliance/legal/textos-base/x.md", "RN 558, art. 5º — NÃO ÂNCORA\n")
    assert find_reg26b(_dir_corpus(tmp_path, RENDER_ROOTS)) == []


def test_reg27a_guard_rejects_injected_saudelegis_citation(tmp_path: Path) -> None:
    _inject(
        tmp_path,
        "docs/x/cancelamento.md",
        "RN 561/2022, art. 18 — https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0561_30_12_2022.html\n",
    )
    _inject(
        tmp_path,
        "docs/x/ok.md",
        "RN 561/2022, art. 18 — https://www.in.gov.br/web/dou/-/"
        "resolucao-normativa-ans-n-561-de-15-de-dezembro-de-2022-454883907\n",
    )
    found = find_reg27a(_dir_corpus(tmp_path, CITATION_ROOTS), _RN561_KNOWN_DEBT)
    assert [(v.path, v.token) for v in found] == [("docs/x/cancelamento.md", MOTIVO_REG27A)]


def test_reg27a_known_debt_does_not_cover_an_edited_line(tmp_path: Path) -> None:
    path, line = sorted(_RN561_KNOWN_DEBT)[0]
    _inject(tmp_path, path, line + " (editada)\n")
    found = find_reg27a(_dir_corpus(tmp_path, CITATION_ROOTS), _RN561_KNOWN_DEBT)
    assert [(v.path, v.token) for v in found] == [(path, MOTIVO_REG27A)]


def test_reg28a_guard_rejects_injected_buyer_confirmation_duty(tmp_path: Path) -> None:
    _inject(tmp_path, "docs/x/premissa.md", "RN 500 exige confirmação de dados pelo comprador.\n")
    _inject(
        tmp_path,
        "docs/x/ok.md",
        "RN 500/2022, art. 3º, §1º: as operadoras mantêm o cadastro atualizado, correto e fidedigno.\n",
    )
    found = find_reg28a(_dir_corpus(tmp_path, CITATION_ROOTS))
    assert [(v.path, v.token) for v in found] == [("docs/x/premissa.md", MOTIVO_REG28A)]


def test_reg27a_known_debt_covers_one_occurrence_only(tmp_path: Path) -> None:
    path, line = sorted(_RN561_KNOWN_DEBT)[0]
    _inject(tmp_path, path, line + "\n" + line + "\n")
    found = find_reg27a(_dir_corpus(tmp_path, CITATION_ROOTS), _RN561_KNOWN_DEBT)
    assert [(v.path, v.line_no, v.token) for v in found] == [(path, 2, MOTIVO_REG27A)]
