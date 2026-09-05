"""Leitor minimalista de HCL para os testes de `deploy/terraform/**`.

POR QUE UM LEITOR PROPRIO. Os testes deste pacote precisam afirmar coisas sobre a FONTE
Terraform (quais acoes uma policy concede, se uma `variable` tem `default`, se um recurso
e' incondicional). Nenhuma dessas perguntas pode ser respondida por `terraform validate`
— que so' checa sintaxe e tipos — nem por `terraform plan`, que exige credencial AWS que
este repositorio deliberadamente nao tem. E o repositorio nao carrega `python-hcl2`.

O QUE ESTE MODULO FAZ E O QUE NAO FAZ. Ele casa chaves e colchetes com consciencia de
literais de string, comentarios (`#`, `//`, `/* */`) e heredocs (`<<EOT` / `<<-EOT`) —
o suficiente para recortar um bloco e ler os literais de uma lista. Ele NAO avalia
expressoes, nao resolve `var.`/`local.` e nao entende `dynamic`. Toda funcao falha ALTO
(`AssertionError`) quando nao encontra o que procura: um teste que deixasse de achar o
bloco por causa de uma refatoracao passaria em branco, e passar em branco e' o modo de
falha que estes testes existem para impedir.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

# tests/unit/deploy/<arquivo> -> parents[3] == raiz do repositorio.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
TERRAFORM_ROOT: Final[Path] = REPO_ROOT / "deploy" / "terraform"

_HEREDOC: Final[re.Pattern[str]] = re.compile(r"<<-?(?P<tag>[A-Za-z_][A-Za-z0-9_]*)")
_QUOTED: Final[re.Pattern[str]] = re.compile(r'"((?:[^"\\]|\\.)*)"')

_CLOSERS: Final[dict[str, str]] = {"{": "}", "[": "]", "(": ")"}


def _walk(src: str, start: int = 0):
    """Itera `(indice, tipo)` marcando o inicio de string/comentario/heredoc/codigo.

    Compartilhado por `_match_bracket`, `strip_comments` e `direct_attrs` para que as tres
    tenham exatamente a mesma nocao de "isto e' codigo HCL de verdade".
    """
    i = start
    n = len(src)
    while i < n:
        rest2 = src[i : i + 2]
        if src[i] == "#" or rest2 == "//":
            nl = src.find("\n", i)
            end = n if nl == -1 else nl + 1
            yield i, end, "comment"
            i = end
            continue
        if rest2 == "/*":
            close = src.find("*/", i + 2)
            end = n if close == -1 else close + 2
            yield i, end, "comment"
            i = end
            continue
        if src[i] == '"':
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    j += 1
                    break
                j += 1
            yield i, j, "string"
            i = j
            continue
        heredoc = _HEREDOC.match(src, i)
        if heredoc is not None:
            terminator = re.compile(rf"^[ \t]*{re.escape(heredoc.group('tag'))}[ \t]*$", re.MULTILINE)
            found = terminator.search(src, heredoc.end())
            end = n if found is None else found.end()
            yield i, end, "heredoc"
            i = end
            continue
        yield i, i + 1, "code"
        i += 1


def strip_comments(src: str) -> str:
    """`src` sem comentarios, para que uma cerca sobre o CODIGO nao seja satisfeita
    (nem quebrada) por uma mencao em prosa."""
    out: list[str] = []
    for start, end, kind in _walk(src):
        out.append(" " * (end - start) if kind == "comment" else src[start:end])
    return "".join(out)


def direct_attrs(body: str) -> str:
    """`body` sem os sub-blocos `{...}`, deixando so' os atributos DIRETOS.

    Necessario porque um `dynamic "notification" { for_each = ... }` aninhado nao torna o
    recurso que o contem condicional — e' o `for_each` do PROPRIO recurso que faria isso.
    """
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        for start, end, kind in _walk(body, i):
            if kind == "code" and body[start] == "{":
                close = _match_bracket(body, start)
                out.append(" ")
                i = close + 1
                break
            out.append(body[start:end])
            i = end
        else:
            break
    return "".join(out)


def read_tf(relative: str) -> str:
    """Le um arquivo sob `deploy/terraform/`, falhando alto se ele nao existir."""
    path = TERRAFORM_ROOT / relative
    assert path.is_file(), f"arquivo Terraform ausente: {path}"
    return path.read_text(encoding="utf-8")


def _match_bracket(src: str, open_idx: int) -> int:
    """Indice do delimitador que fecha o aberto em `open_idx`, pulando strings/comentarios."""
    opener = src[open_idx]
    closer = _CLOSERS[opener]
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        rest2 = src[i : i + 2]
        if src[i] == "#" or rest2 == "//":
            nl = src.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if rest2 == "/*":
            end = src.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if src[i] == '"':
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        heredoc = _HEREDOC.match(src, i)
        if heredoc is not None:
            terminator = re.compile(rf"^[ \t]*{re.escape(heredoc.group('tag'))}[ \t]*$", re.MULTILINE)
            found = terminator.search(src, heredoc.end())
            i = n if found is None else found.end()
            continue
        if src[i] == opener:
            depth += 1
        elif src[i] == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError(f"delimitador {opener!r} aberto em {open_idx} nunca fecha")


def block_body(src: str, header: str) -> str:
    """Corpo (sem as chaves) do primeiro bloco cujo cabecalho casa a regex `header`."""
    match = re.search(header, src)
    assert match is not None, f"bloco nao encontrado: /{header}/"
    open_idx = src.index("{", match.end() - 1 if src[match.end() - 1] == "{" else match.end())
    return src[open_idx + 1 : _match_bracket(src, open_idx)]


def has_block(src: str, header: str) -> bool:
    return re.search(header, src) is not None


def sub_blocks(body: str, kind: str) -> list[str]:
    """Corpos de todos os sub-blocos `kind { ... }` diretamente legiveis em `body`."""
    out: list[str] = []
    for match in re.finditer(rf"(?m)^\s*{re.escape(kind)}\s*\{{", body):
        open_idx = body.index("{", match.end() - 1)
        out.append(body[open_idx + 1 : _match_bracket(body, open_idx)])
    return out


def attr_strings(body: str, attr: str) -> list[str]:
    """Literais de string do atributo `attr` (lista `[...]` ou valor unico)."""
    match = re.search(rf"(?m)^\s*{re.escape(attr)}\s*=\s*", body)
    assert match is not None, f"atributo ausente: {attr}"
    tail = body[match.end() :]
    if tail.lstrip().startswith("["):
        open_idx = body.index("[", match.end())
        inner = body[open_idx + 1 : _match_bracket(body, open_idx)]
        return [m.group(1) for m in _QUOTED.finditer(inner)]
    line = tail.split("\n", 1)[0]
    return [m.group(1) for m in _QUOTED.finditer(line)]


def attr_raw(body: str, attr: str) -> str:
    """Primeira linha do valor bruto de `attr` (para checar referencias como `var.x`)."""
    match = re.search(rf"(?m)^\s*{re.escape(attr)}\s*=\s*", body)
    assert match is not None, f"atributo ausente: {attr}"
    return body[match.end() :].split("\n", 1)[0].strip()


def has_attr(body: str, attr: str) -> bool:
    return re.search(rf"(?m)^\s*{re.escape(attr)}\s*=", body) is not None
