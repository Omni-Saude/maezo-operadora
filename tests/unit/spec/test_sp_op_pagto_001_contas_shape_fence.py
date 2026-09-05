"""Fence: o que PAGTO DECLARA consumir de CONTAS/RECURSO tem de cobrir o que elas SEMEIAM.

Por que este modulo existe (gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION). SP-OP-PAGTO-001.bpmn e o
seu contrato pressupunham, em 8 lugares, um SP-OP-CONTAS-001 que "adjudica a conta e gera a
obrigacao de pagamento" sem nomear o handoff real nem o vocabulario que ele carrega. Desde
ADR-0040 (`operadora.contas.handoff_pagamento` / `operadora.recurso.handoff_pagamento`, invariante
I-PAGTO-1) a forma REAL do handoff e conhecida e esta PINADA do lado produtor —
`contas.HANDOFF_PAGTO_SEEDED_KEYS` / `recurso.HANDOFF_PAGTO_SEEDED_KEYS`, cada uma com um teste de
IGUALDADE de conjunto no proprio modulo produtor (`test_handoff_pagamento_semeia_exatamente_o_
conjunto_declarado` em `test_contas.py`/`test_recurso.py`).

O que faltava e o que este modulo prova: que a tabela "## Variaveis de entrada" do CONTRATO de
PAGTO — a superficie que declara o que PAGTO CONSOME — nao ficasse para tras quando aquele
conjunto produtor mudar. Antes deste fix a tabela do contrato de PAGTO omitia `numero_guia_tiss`,
`fonte_valor` e `glosa_id` — tres chaves que os dois handoffs JA semeavam em producao — uma
premissa (o que "vem de CONTAS") mais estreita que a forma real.

TREE-DERIVED, NAO LITERAL: nenhuma das duas pontas e uma lista copiada a mao aqui. O lado
PRODUTOR e IMPORTADO do proprio codigo-fonte (`contas.HANDOFF_PAGTO_SEEDED_KEYS` /
`recurso.HANDOFF_PAGTO_SEEDED_KEYS` — os mesmos frozensets que o worker usa para recusar o
handoff, via `assert set(payload) == HANDOFF_PAGTO_SEEDED_KEYS`, se o payload alguma vez divergir).
O lado CONSUMIDOR e PARSEADO da tabela markdown do contrato (coluna `Variavel`, celula
`` `nome` ``) — nunca reescrito a mao aqui. Rename um nome de qualquer lado (o `HANDOFF_PAGTO_
SEEDED_KEYS` do produtor OU a celula do contrato) e este teste vai a RED, porque os dois
conjuntos deixam de casar.
"""

from __future__ import annotations

import re
from pathlib import Path

from maezo.tools.workers.contas import HANDOFF_PAGTO_FORBIDDEN_KEYS as CONTAS_FORBIDDEN_KEYS
from maezo.tools.workers.contas import HANDOFF_PAGTO_SEEDED_KEYS as CONTAS_HANDOFF_KEYS
from maezo.tools.workers.recurso import HANDOFF_PAGTO_FORBIDDEN_KEYS as RECURSO_FORBIDDEN_KEYS
from maezo.tools.workers.recurso import HANDOFF_PAGTO_SEEDED_KEYS as RECURSO_HANDOFF_KEYS

_REPO = Path(__file__).resolve().parents[3]
_CONTRATO_PAGTO = _REPO / "docs/processes/contracts/SP-OP-PAGTO-001.md"
_BPMN_PAGTO = _REPO / "spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn"

#: Uniao das duas chaves semeadas (CONTAS tem 15, RECURSO tem as mesmas 15 + `glosa_id`) —
#: usada pelos testes de "prosa" abaixo (F6), que checam os DOIS sites narrativos (o bloco de
#: `<bpmn:documentation>` de nivel de processo e o texto do contrato) em vez de so a tabela/roll
#: ja cobertos acima.
_TODAS_AS_CHAVES_SEMEADAS = CONTAS_HANDOFF_KEYS | RECURSO_HANDOFF_KEYS


def _variaveis_declaradas_no_contrato_pagto() -> frozenset[str]:
    """Parseia a secao ``## Variaveis de entrada`` do contrato de PAGTO — nomes entre crase na
    primeira coluna de cada linha de tabela. Tree-derived: le o arquivo real, nao uma copia.
    """
    texto = _CONTRATO_PAGTO.read_text(encoding="utf-8")
    inicio = texto.index("## Variaveis de entrada")
    fim = texto.find("\n## ", inicio + 1)
    secao = texto[inicio : fim if fim != -1 else len(texto)]

    nomes: set[str] = set()
    for linha in secao.splitlines():
        if not linha.startswith("|"):
            continue
        celulas = linha.split("|")
        if len(celulas) < 2:
            continue
        primeira = celulas[1].strip()
        m = re.fullmatch(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", primeira)
        if m:
            nomes.add(m.group(1))
    return frozenset(nomes)


def test_parser_nao_retorna_vazio_e_pega_o_nucleo_conhecido() -> None:
    """Sanity do proprio parser: um regex que nunca casa daria um fence vacuamente verde."""
    declaradas = _variaveis_declaradas_no_contrato_pagto()
    assert len(declaradas) >= 15, declaradas
    for nome in ("tenant_id", "ordem_pagamento_id", "prestador_id", "valor_pagamento_cents"):
        assert nome in declaradas, f"{nome} deveria estar na tabela de entrada de PAGTO"


def test_contrato_pagto_declara_todas_as_chaves_que_contas_semeia() -> None:
    """O que `operadora.contas.handoff_pagamento` PINA como semeado (fonte: contas.py, nao uma
    lista local) tem de aparecer na tabela "Variaveis de entrada" do contrato de PAGTO. Se
    contas.py ganhar uma chave nova, ou se a tabela perder uma linha, isto vai a RED.
    """
    declaradas = _variaveis_declaradas_no_contrato_pagto()
    faltando = CONTAS_HANDOFF_KEYS - declaradas
    assert not faltando, (
        f"PAGTO nao declara consumir {sorted(faltando)}, que "
        "operadora.contas.handoff_pagamento (contas.HANDOFF_PAGTO_SEEDED_KEYS) semeia de fato — "
        "premissa de PAGTO sobre a forma de CONTAS-001 ficou mais estreita que a real "
        "(gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION)."
    )


def test_contrato_pagto_declara_todas_as_chaves_que_recurso_semeia() -> None:
    """Espelho do teste acima para `operadora.recurso.handoff_pagamento` (a terceira forma da
    business key, glosa revertida) — mesma fonte tree-derived, `recurso.HANDOFF_PAGTO_SEEDED_KEYS`.
    """
    declaradas = _variaveis_declaradas_no_contrato_pagto()
    faltando = RECURSO_HANDOFF_KEYS - declaradas
    assert not faltando, (
        f"PAGTO nao declara consumir {sorted(faltando)}, que "
        "operadora.recurso.handoff_pagamento (recurso.HANDOFF_PAGTO_SEEDED_KEYS) semeia de fato — "
        "premissa de PAGTO sobre a forma de RECURSO-001 ficou mais estreita que a real "
        "(gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION)."
    )


def test_as_quatro_proibidas_nao_sao_confundidas_com_semeadas_por_origem() -> None:
    """Sanity cruzada com I-PAGTO-1: as quatro booleans que PAGTO resolve sozinho NUNCA estao nos
    conjuntos semeados pelos handoffs (isso ja e testado NEGATIVAMENTE do lado produtor; aqui so
    confirmamos que o fence acima nao passaria "por acidente" caso essa invariante quebrasse).
    `HANDOFF_PAGTO_FORBIDDEN_KEYS` e IMPORTADO de cada modulo produtor (nunca copiado a mao aqui):
    uma chave nova adicionada la que colida com o que o mesmo modulo semeia derruba este teste.
    """
    assert not (CONTAS_HANDOFF_KEYS & CONTAS_FORBIDDEN_KEYS)
    assert not (RECURSO_HANDOFF_KEYS & RECURSO_FORBIDDEN_KEYS)


def _bloco_documentacao_processo_bpmn() -> str:
    """Texto do UNICO `<bpmn:documentation>` de nivel de processo do BPMN de PAGTO — o primeiro,
    envolto em `CDATA`, logo apos a abertura de `<bpmn:process>` e antes de qualquer elemento de
    fluxo. Tree-derived: le o arquivo real; os demais `<bpmn:documentation>` do arquivo (por
    tarefa, sem CDATA) nao entram por construcao do regex.
    """
    texto = _BPMN_PAGTO.read_text(encoding="utf-8")
    m = re.search(
        r"<bpmn:process\b.*?<bpmn:documentation><!\[CDATA\[(.*?)\]\]></bpmn:documentation>",
        texto,
        re.DOTALL,
    )
    assert m, "bloco de documentacao de nivel de processo nao encontrado no BPMN de PAGTO"
    return m.group(1)


def test_o_bloco_de_documentacao_do_bpmn_nomeia_toda_chave_semeada() -> None:
    """F6: os dois sites de PROSA do BPMN (o paragrafo `GATILHO REGULATORIO`, que nomeia
    `operadora.contas.handoff_pagamento`, e o paragrafo `BUSINESS KEY`, que nomeia a terceira
    forma via `operadora.recurso.handoff_pagamento`) nao tinham fence nenhum antes deste teste —
    so o roll `VARIAVEIS DE ENTRADA` (dentro do MESMO bloco) era coberto, pela cerca do corpus PHI
    em `test_validation_phi_completeness.py`. Remover qualquer nome semeado do bloco inteiro
    (prosa OU roll) derruba este teste.
    """
    bloco = _bloco_documentacao_processo_bpmn()
    faltando = sorted(nome for nome in _TODAS_AS_CHAVES_SEMEADAS if nome not in bloco)
    assert not faltando, (
        f"bloco de documentacao de nivel de processo do BPMN de PAGTO nao nomeia {faltando}, que "
        "os handoffs de CONTAS-001/RECURSO-001 semeiam de fato (gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION)."
    )


def test_o_texto_do_contrato_nomeia_toda_chave_semeada() -> None:
    """Espelho do teste acima para o segundo site de prosa (F6): o contrato de PAGTO
    (`docs/processes/contracts/SP-OP-PAGTO-001.md`) tem de continuar nomeando toda chave que os
    dois handoffs semeiam — tanto na tabela "Variaveis de entrada" (ja parseada estruturalmente
    acima) quanto na prosa (`Gatilho regulatorio`, `Business key`). Le o arquivo INTEIRO, entao
    cobre os dois.
    """
    texto = _CONTRATO_PAGTO.read_text(encoding="utf-8")
    faltando = sorted(nome for nome in _TODAS_AS_CHAVES_SEMEADAS if nome not in texto)
    assert not faltando, (
        f"contrato de PAGTO nao nomeia {faltando} em lugar nenhum do texto, que os handoffs de "
        "CONTAS-001/RECURSO-001 semeiam de fato (gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION)."
    )
