"""C1 (item 3): no `Dockerfile.human`, `conf/bpm-platform.xml` pertence ao root e o uid do processo nao o
regrava nem o substitui. Cerca estatica sobre a ordem das instrucoes; a prova de runtime e' a asserção
fail-closed que a propria imagem executa COMO camunda no build (stat 0:0:444, `! -w`, mv/rm recusados).
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[3] / "deploy/cibseven/Dockerfile.human"
FILE = "/camunda/conf/bpm-platform.xml"


def _instructions() -> list[str]:
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_descriptor_is_root_owned_read_only_after_its_last_write() -> None:
    lines = _instructions()
    writes = [
        i
        for i, line in enumerate(lines)
        if line.startswith("RUN") and re.search(r"sed -i[^;&]*" + re.escape(FILE), line)
    ]
    lock = [
        i
        for i, line in enumerate(lines)
        if f"chown root:root {FILE}" in line and f"chmod 0444 {FILE}" in line
    ]
    assert writes and len(lock) == 1, "o descriptor tem que ser travado exatamente uma vez"
    assert lock[0] > max(writes), "nenhum sed pode rodar depois da trava"
    assert lines[lock[0] - 1] == "USER root", "chown exige root imediatamente antes"
    assert (
        "chown root:camunda /camunda/conf" in lines[lock[0]] and "chmod 1775 /camunda/conf" in lines[lock[0]]
    )


def test_process_uid_is_camunda_after_the_lock_and_the_build_proves_it() -> None:
    lines = _instructions()
    lock = next(i for i, line in enumerate(lines) if f"chmod 0444 {FILE}" in line)
    users = [line for line in lines[lock + 1 :] if line.startswith("USER ")]
    assert users == ["USER camunda"], "o ultimo USER (processo) tem que ser camunda, e so' um depois da trava"
    tail = " ".join(lines[lock + 1 :])
    for proof in ('"0:0:444"', '[ ! -w "$f" ]', '"0:1775"', 'mv "$f"', 'rm -f "$f"'):
        assert proof in tail, f"falta a prova de build {proof}"
