#!/usr/bin/env python3
"""CI gate (GOV-0.8 / decisão D-31a): citações em docs/compliance/ resolvem na árvore
e o espelho rastreado docs/compliance/GAP-REGISTER.yaml confere com a custódia.

O defeito que MOTIVOU esta cerca (GOV-0.8, decisão D-31a do dono, 2026-10-23):
docs/Tarefas_Pendentes.md citava `docs/audits/maezo-deep-audit/remediation/GAP-REGISTER.yaml`
— um caminho dentro de docs/audits/ (gitignored) que não existe no checkout de nenhum
terceiro: a "citação pendente" clássica. A fonte real sobreviveu só no tar de custódia
docs-audits-maezo-deep-audit.tar.gz; o espelho rastreado `docs/compliance/GAP-REGISTER.yaml`
é a cópia fiel do snapshot custodiado (decisão D-31a), e esta cerca existe para que a
PRÓXIMA citação pendente desta espécie — em qualquer doc de docs/compliance/ — quebre o
build no mesmo dia, em vez de esperar um leitor humano notar.

Escopo: docs/compliance/** (arquivos .md RASTREADOS) + a integridade do espelho
GAP-REGISTER.yaml. Precedente R-089 (`check_doc_symbol_citations.py`): escopo estreito e
deliberado — há dezenas de citações fantasma conhecidas em OUTRAS árvores de docs
(reports, plans, review packets) e alargar o escopo para além de docs/compliance/ é uma
decisão separada, do dono. Diferença deliberada do R-089: AQUI NÃO HÁ isenção para
caminho ausente sob árvore gitignored — a casa dos espelhos rastreados só pode apontar
para coisa que o leitor abre no checkout; apontar para árvore ignorada/ausente é
exatamente a citação pendente que esta cerca mata.

Duas verificações, ambas fail-closed (escaneamento vazio também falha):

  1. **Citação de caminho** — todo token entre backticks que começa por raiz canônica do
     repositório (`src/`, `spec/`, `tests/`, `config/`, `deploy/`, `docs/`, `scripts/`,
     `.github/`) deve existir no disco. Número de linha (`caminho.py:116`, `:116-120`) é
     informativo — só a existência do ARQUIVO é verificada (precedente R-089 F4: linha
     deriva, arquivo sumido não). Âncora markdown (`#secao`) é aparada. Token com `...`
     é elisão declarada, contada à parte, nunca apresentada como verificada. Diretório
     com `/` final verifica isdir. Sem fallback para basename/arquivado.

  2. **Integridade do espelho GAP-REGISTER.yaml** — o envelope `espelho:` declara
     `fonte_sha256` (o snapshot custodiado) e `mapa_gap_linhas` (provenância POR LINHA:
     gap id → intervalo de linhas da fonte). A cerca reverte o recuo de 2 espaços do
     bloco `registro_custodiado`, recomputa o sha256 (deve bater com `fonte_sha256`) e
     reextrai o mapa da seção `gaps:` (deve bater com o declarado). Divergência em
     qualquer um = o espelho deixou de ser cópia fiel da custódia = falha. Sem
     dependência de YAML parser: o bloco é texto opaco + hash; o mapa é regex de linha.

Stdlib-only. `main()` usa `git ls-files` para enumerar os .md rastreados; os testes
injetam a lista de arquivos e nunca tocam git.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

MIRROR_REL = Path("docs/compliance/GAP-REGISTER.yaml")
COMPLIANCE_DIR = Path("docs/compliance")

# Raízes canônicas de repositório (mesma família do R-089) + `.github`. `:` entra na
# classe para citar `caminho:linha` / `caminho::simbolo`; o aparo acontece em
# _strip_citation_token (a existência verificada é sempre só do ARQUIVO/diretório).
_CITATION_RE = re.compile(r"`((?:src|spec|tests|config|deploy|docs|scripts|\.github)/[A-Za-z0-9_./#:-]+)`")
# `caminho.py:116` / `caminho.py:116-120` — o número é informativo, o arquivo não.
_LINE_SUFFIX_RE = re.compile(r"^(.*?):\d+(?:-\d+)?$")
_GAP_ID_RE = re.compile(r"^- id: '?([A-Za-z0-9._-]+?)'?$")
_SHA_RE = re.compile(r"^  fonte_sha256: ([0-9a-f]{64})$", re.M)
_BLOCO_RE = re.compile(r"^  bloco_chave: (\S+)$", re.M)
_MAPA_ROW_RE = re.compile(r"^    '?([A-Za-z0-9._-]+?)'?: '(\d+)-(\d+)'$", re.M)


def _strip_citation_token(token: str) -> tuple[str, str]:
    """Apara âncora markdown, `::simbolo` e sufixo `:linha`; devolve (caminho, rótulo).

    Só o CAMINHO é verificado — linha e símbolo ficam informativos (precedente R-089:
    deriva de linha/símbolo é outra cerca; arquivo sumido é esta).
    """
    anchor = ""
    if "#" in token:
        token, anchor = token.split("#", 1)
        anchor = f"#{anchor}"
    symbol = ""
    if "::" in token:
        token, symbol = token.split("::", 1)
        symbol = f"::{symbol}"
    line_info = ""
    line_match = _LINE_SUFFIX_RE.match(token)
    if line_match:
        token, line_info = line_match.group(1), token[len(line_match.group(1)) :]
    token = token.rstrip("/")
    label = "".join(part for part in (line_info, symbol, anchor) if part)
    return token, label


def check_md_citations(
    rel_path: Path, text: str, exists: Callable[[Path], bool]
) -> tuple[list[tuple[Path, int, str, str]], dict[str, int]]:
    """Varre um .md por citações de caminho enraizadas; devolve (achados, contagens)."""
    findings: list[tuple[Path, int, str, str]] = []
    stats = {"eligible": 0, "ok": 0, "elided": 0, "missing": 0}
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _CITATION_RE.finditer(line):
            raw = match.group(1)
            if "..." in raw:
                stats["elided"] += 1
                continue
            token, suffix = _strip_citation_token(raw)
            if not token:
                stats["elided"] += 1
                continue
            stats["eligible"] += 1
            if exists(Path(token)):
                stats["ok"] += 1
                continue
            stats["missing"] += 1
            findings.append(
                (rel_path, lineno, raw, f"caminho inexistente na árvore{f' ({suffix})' if suffix else ''}")
            )
    return findings, stats


def _revert_custody_block(text: str) -> tuple[str | None, str | None]:
    """Reverte o recuo de 2 espaços do bloco literal `registro_custodiado: |`."""
    block_match = re.search(r"^registro_custodiado: \|\n", text, flags=re.M)
    if not block_match:
        return None, "bloco `registro_custodiado: |` não encontrado"
    lines = text[block_match.end() :].split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    reverted: list[str] = []
    for idx, line in enumerate(lines, start=1):
        if line == "":
            reverted.append("")
            continue
        if not line.startswith("  "):
            return None, f"linha {idx} do bloco sem o recuo de 2 espaços: {line[:40]!r}"
        reverted.append(line[2:])
    return "\n".join(reverted) + "\n", None


def _recompute_gap_map(block_text: str) -> dict[str, str] | None:
    """Reextrai {gap_id: 'inicio-fim'} (linhas 1-based) da seção `gaps:` do bloco."""
    lines = block_text.split("\n")
    try:
        gaps_start = lines.index("gaps:")
        wp_start = next(i for i in range(gaps_start + 1, len(lines)) if lines[i].startswith("work_packages:"))
    except (ValueError, StopIteration):
        return None
    ids: list[tuple[str, int]] = []
    for i in range(gaps_start + 1, wp_start):
        id_match = _GAP_ID_RE.match(lines[i])
        if id_match:
            ids.append((id_match.group(1), i + 1))
    if not ids:
        return None
    recomputed: dict[str, str] = {}
    for idx, (name, start) in enumerate(ids):
        end = (ids[idx + 1][1] - 1) if idx + 1 < len(ids) else wp_start
        recomputed[name] = f"{start}-{end}"
    return recomputed


def verify_mirror(
    text: str,
) -> tuple[list[str], dict[str, int]]:
    """Confere envelope + bloco custodiado + mapa por linha do espelho. (achados, contagens)"""
    findings: list[str] = []
    stats = {"mapa_ids": 0, "verificados": 0}
    sha_match = _SHA_RE.search(text)
    if not sha_match:
        findings.append(
            "envelope sem `fonte_sha256` (sha256 de 64 hex) — espelho sem provenância verificável"
        )
        return findings, stats
    declared_sha = sha_match.group(1)
    bloco_match = _BLOCO_RE.search(text)
    bloco_chave = bloco_match.group(1) if bloco_match else "registro_custodiado"
    if bloco_chave != "registro_custodiado":
        findings.append(f"envelope declara bloco_chave inesperado: {bloco_chave!r}")
    block, error = _revert_custody_block(text)
    if block is None:
        findings.append(f"bloco custodiado irrecuperável: {error}")
        return findings, stats
    actual_sha = hashlib.sha256(block.encode("utf-8")).hexdigest()
    if actual_sha != declared_sha:
        findings.append(
            "sha256 do bloco registro_custodiado NÃO bate com fonte_sha256 declarada "
            f"({actual_sha} != {declared_sha}) — o espelho deixou de ser cópia fiel da custódia"
        )
        return findings, stats
    recomputed = _recompute_gap_map(block)
    if recomputed is None:
        findings.append("seção `gaps:` não encontrada no bloco custodiado — mapa por linha não recomputável")
        return findings, stats
    declared_map = {name: f"{start}-{end}" for name, start, end in _MAPA_ROW_RE.findall(text)}
    stats["mapa_ids"] = len(declared_map)
    for name in sorted(set(declared_map) | set(recomputed)):
        declared_range = declared_map.get(name)
        actual_range = recomputed.get(name)
        if declared_range is None:
            findings.append(f"mapa_gap_linhas não declara o gap {name} (fonte: linha {actual_range})")
        elif actual_range is None:
            findings.append(f"mapa_gap_linhas declara gap inexistente no bloco: {name}")
        elif declared_range != actual_range:
            findings.append(
                f"mapa_gap_linhas divergente para {name}: declarado {declared_range}, fonte {actual_range}"
            )
        else:
            stats["verificados"] += 1
    return findings, stats


def _tracked_compliance_markdown(repo_root: Path) -> list[Path]:
    """Enumera .md RASTREADOS sob docs/compliance/ via `git ls-files` (falha fechada)."""
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--", str(COMPLIANCE_DIR)],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files falhou: {proc.stderr.decode(errors='replace').strip()}")
    return sorted(
        Path(rel.decode("utf-8"))
        for rel in proc.stdout.split(b"\0")
        if rel and rel.decode("utf-8").endswith(".md")
    )


def main(
    repo_root: Path | None = None,
    files: list[Path] | None = None,
    stream: Callable[[str], None] = print,
) -> int:
    """Executa a cerca. `files` injeta a lista de .md (testes); None = `git ls-files`."""
    root = repo_root if repo_root is not None else Path(__file__).resolve().parents[2]
    md_files = files if files is not None else _tracked_compliance_markdown(root)
    findings: list[tuple[Path, int, str, str]] = []
    totals = {"eligible": 0, "ok": 0, "elided": 0, "missing": 0}

    def exists(token: Path) -> bool:
        # Citações são enraizadas na raiz do repositório — nunca relativas ao .md.
        return (root / token).exists()

    for rel in md_files:
        path = root / rel
        file_findings, file_stats = check_md_citations(rel, path.read_text(encoding="utf-8"), exists)
        findings.extend(file_findings)
        for key in totals:
            totals[key] += file_stats[key]

    mirror_rel = MIRROR_REL
    mirror_path = root / mirror_rel
    if not mirror_path.exists():
        mirror_findings = [f"espelho rastreado ausente: {mirror_rel} (decisão D-31a)"]
        mirror_stats: dict[str, int] = {"mapa_ids": 0, "verificados": 0}
    else:
        mirror_findings, mirror_stats = verify_mirror(mirror_path.read_text(encoding="utf-8"))

    stream(
        "docs/compliance: "
        f"{len(md_files)} .md varridos · {totals['eligible']} citações verificadas "
        f"({totals['ok']} resolvem, {totals['missing']} inexistentes, {totals['elided']} elisões) · "
        f"espelho {mirror_rel}: "
        f"{mirror_stats['verificados']}/{mirror_stats['mapa_ids']} linhas de mapa conferidas"
    )
    for rel_path, lineno, token, reason in findings:
        stream(f"  FALHA {rel_path}:{lineno} — `{token}`: {reason}")
    for message in mirror_findings:
        stream(f"  FALHA {mirror_rel} — {message}")
    if findings or mirror_findings:
        stream("check-compliance-doc-citations: FALHA")
        return 1
    if totals["eligible"] == 0:
        stream(
            "check-compliance-doc-citations: FALHA (varredura vazia — nenhuma citação elegível encontrada)"
        )
        return 1
    if mirror_stats["verificados"] == 0:
        stream(
            "check-compliance-doc-citations: FALHA (nenhuma linha do mapa por linha verificada no espelho)"
        )
        return 1
    stream("check-compliance-doc-citations: PASSA (citações resolvem; espelho = custódia, byte a byte)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
