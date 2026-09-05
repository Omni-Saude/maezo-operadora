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

REPARO §Δ (gatekeeper §Delta, REVISE F6 confirmado). Um primeiro reparo (F6, ja substituido pelo
que segue) so checava que cada NOME semeado aparecesse em algum lugar do bloco/arquivo — o roll
"VARIAVEIS DE ENTRADA" e a tabela ja garantiam isso, entao o teste passava mesmo com as mutacoes
F/G/H do gatekeeper (reverter o paragrafo GATILHO do BPMN, apagar o paragrafo da terceira
business key, reverter a prosa do contrato com a tabela intacta) — nenhuma delas mexe em nome
nenhum, so em CONTEUDO de dois paragrafos/secoes especificos que nunca eram lidos. Os quatro
testes abaixo checam o CONTEUDO desses dois sites, cada pedaco tree-derived: os topicos
`camunda:topic` dos handoffs (lidos das `bpmn:serviceTask id="ST_HandoffPagamento*"` REAIS de
`SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn`/`SP-OP-RECURSO-001_Recurso_Glosa.bpmn`, nunca
digitados aqui) e o template da terceira forma da business key (parseado via `inspect.getsource`
do corpo real de `recurso._pagto_business_key` — a f-string `PAGTO-{key_segment(tenant_id)}-...`
vira o template `PAGTO-{tenant_id}-{numero_guia_tiss}-{glosa_id}` por substituicao mecanica de
`{key_segment(NOME)}` por `{NOME}`, nunca escrito a mao). Cada site (paragrafo GATILHO do BPMN,
paragrafo BUSINESS KEY do BPMN, linha Gatilho do contrato, secao Business key do contrato) e
extraido isoladamente — nao o bloco/arquivo inteiro — para que uma regressao em UM site nao seja
mascarada pelo mesmo conteudo sobrevivendo em outro. Aceita a forma curta do topico
(`contas.handoff_pagamento`/`recurso.handoff_pagamento`, sem o prefixo `operadora.`) alem da
forma completa: e assim que a prosa do CONTRATO ja nomeia o handoff de RECURSO hoje (a tabela e o
paragrafo GATILHO usam a forma completa; a secao Business key usa a curta) — um fato do texto
real, nao uma frouxidao inventada; ambas as formas sao derivadas mecanicamente do MESMO literal
completo (`topico.split(".", 1)[1]`), nunca digitadas soltas.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from maezo.tools.workers.contas import HANDOFF_PAGTO_FORBIDDEN_KEYS as CONTAS_FORBIDDEN_KEYS
from maezo.tools.workers.contas import HANDOFF_PAGTO_SEEDED_KEYS as CONTAS_HANDOFF_KEYS
from maezo.tools.workers.recurso import HANDOFF_PAGTO_FORBIDDEN_KEYS as RECURSO_FORBIDDEN_KEYS
from maezo.tools.workers.recurso import HANDOFF_PAGTO_SEEDED_KEYS as RECURSO_HANDOFF_KEYS
from maezo.tools.workers.recurso import _pagto_business_key

_REPO = Path(__file__).resolve().parents[3]
_CONTRATO_PAGTO = _REPO / "docs/processes/contracts/SP-OP-PAGTO-001.md"
_BPMN_PAGTO = _REPO / "spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn"
_BPMN_CONTAS = _REPO / "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"
_BPMN_RECURSO = _REPO / "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn"


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


def _topicos_camunda_para_prefixo(bpmn_path: Path, prefixo_id: str) -> frozenset[str]:
    """Le, do arquivo BPMN PRODUTOR real, o `camunda:topic` de TODO `bpmn:serviceTask` cujo `id`
    comeca por `prefixo_id` (ex.: todas as pernas `ST_HandoffPagamento*`). Nao vacuo (>= 1 elemento
    casado) e exige que TODAS concordem no mesmo topico — se um dia divergirem, isso e uma
    inconsistencia real do produtor, entao o teste que usa isto tem de RED, nao adivinhar qual usar.
    """
    texto = bpmn_path.read_text(encoding="utf-8")
    achados = re.findall(
        rf'<bpmn:serviceTask\s+id="{re.escape(prefixo_id)}[^"]*"[^>]*?camunda:topic="([^"]+)"[^>]*>',
        texto,
        re.DOTALL,
    )
    assert achados, f"nenhum bpmn:serviceTask id={prefixo_id}* encontrado em {bpmn_path.name}"
    topicos = frozenset(achados)
    assert len(topicos) == 1, f"topicos divergentes para {prefixo_id}* em {bpmn_path.name}: {topicos}"
    return topicos


CONTAS_HANDOFF_TOPIC = next(iter(_topicos_camunda_para_prefixo(_BPMN_CONTAS, "ST_HandoffPagamento")))
RECURSO_HANDOFF_TOPIC = next(iter(_topicos_camunda_para_prefixo(_BPMN_RECURSO, "ST_HandoffPagamento")))


def _forma_curta_do_topico(topico: str) -> str:
    """`operadora.contas.handoff_pagamento` -> `contas.handoff_pagamento`. Derivacao mecanica do
    MESMO literal completo (nunca um segundo literal digitado) — a prosa do CONTRATO usa essa
    forma curta na secao Business key, enquanto o paragrafo Gatilho e a tabela usam a forma
    completa; aceitar as duas e um fato do texto real, nao uma frouxidao inventada.
    """
    return topico.split(".", 1)[1]


def _template_terceira_forma_business_key() -> str:
    """Parseia o CORPO REAL de `recurso._pagto_business_key` (via `inspect.getsource`, nunca
    reescrito a mao) e substitui cada `{key_segment(NOME)}` pelo template `{NOME}` que a prosa
    (BPMN e contrato) documenta — deriva `PAGTO-{tenant_id}-{numero_guia_tiss}-{glosa_id}`
    mecanicamente do codigo-fonte, nao de um literal solto neste arquivo de teste.
    """
    src = inspect.getsource(_pagto_business_key)
    m = re.search(r'return f"(.+)"', src)
    assert m, "corpo f-string de _pagto_business_key nao encontrado (assinatura mudou?)"
    partes = re.split(r"\{key_segment\((\w+)\)\}", m.group(1))
    assert len(partes) >= 3, f"_pagto_business_key nao usa key_segment(...) como esperado: {partes}"
    template = "".join(p if i % 2 == 0 else f"{{{p}}}" for i, p in enumerate(partes))
    assert template.startswith("PAGTO-"), template
    return template


def _paragrafo_por_prefixo(bloco: str, prefixo: str) -> str:
    """Isola, dentro do bloco de documentacao (paragrafos separados por linha em branco), o UNICO
    paragrafo cujo texto comeca por `prefixo` (ex.: "GATILHO REGULATORIO", "BUSINESS KEY"). Isolar
    por paragrafo (nao o bloco inteiro) e o que faz uma mutacao QUE SO MEXE NESSE paragrafo
    (gatekeeper §Delta, mutacoes F/G) derrubar exatamente o teste certo, sem que o mesmo conteudo
    sobrevivendo em outro paragrafo mascare a regressao.
    """
    paragrafos = [p for p in bloco.split("\n\n") if p.strip().startswith(prefixo)]
    assert len(paragrafos) == 1, (
        f"esperava exatamente 1 paragrafo comecando por {prefixo!r} no bloco de documentacao do "
        f"BPMN de PAGTO, achei {len(paragrafos)}"
    )
    return paragrafos[0]


def test_o_paragrafo_gatilho_regulatorio_do_bpmn_nomeia_o_handoff_real_de_contas() -> None:
    """F6/§Δ (mutacao F do gatekeeper: reverter bpmn:26-27 a premissa antiga, sem nomear o
    handoff). Isolado ao paragrafo `GATILHO REGULATORIO` (nao o bloco inteiro): o topico de CONTAS
    sobrevive em outro paragrafo (BUSINESS KEY) mesmo se este for revertido, entao so um teste
    ESCOPADO a este paragrafo pega a regressao.
    """
    paragrafo = _paragrafo_por_prefixo(_bloco_documentacao_processo_bpmn(), "GATILHO REGULATORIO")
    assert CONTAS_HANDOFF_TOPIC in paragrafo or _forma_curta_do_topico(CONTAS_HANDOFF_TOPIC) in paragrafo, (
        f"paragrafo GATILHO REGULATORIO do BPMN de PAGTO nao nomeia o handoff real de CONTAS-001 "
        f"({CONTAS_HANDOFF_TOPIC}, lido de bpmn:serviceTask ST_HandoffPagamento* em "
        f"{_BPMN_CONTAS.name}) — premissa stale (gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION)."
    )


def test_o_paragrafo_business_key_do_bpmn_nomeia_os_dois_handoffs_e_a_terceira_forma() -> None:
    """F6/§Δ (mutacao G do gatekeeper: apagar o paragrafo/trecho da terceira business key). Se o
    paragrafo BUSINESS KEY inteiro sumir, `_paragrafo_por_prefixo` ja falha (0 achados); se so o
    trecho da terceira forma sumir, as asserts de conteudo abaixo falham.
    """
    paragrafo = _paragrafo_por_prefixo(_bloco_documentacao_processo_bpmn(), "BUSINESS KEY")
    template = _template_terceira_forma_business_key()
    assert CONTAS_HANDOFF_TOPIC in paragrafo or _forma_curta_do_topico(CONTAS_HANDOFF_TOPIC) in paragrafo, (
        f"paragrafo BUSINESS KEY do BPMN nao nomeia o handoff de CONTAS-001 ({CONTAS_HANDOFF_TOPIC})"
    )
    assert RECURSO_HANDOFF_TOPIC in paragrafo or _forma_curta_do_topico(RECURSO_HANDOFF_TOPIC) in paragrafo, (
        f"paragrafo BUSINESS KEY do BPMN nao nomeia o handoff de RECURSO-001 ({RECURSO_HANDOFF_TOPIC}, "
        f"lido de bpmn:serviceTask ST_HandoffPagamento* em {_BPMN_RECURSO.name}) — a terceira forma "
        "da business key (glosa revertida) ficou sem premissa (gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION)."
    )
    assert template in paragrafo, (
        f"paragrafo BUSINESS KEY do BPMN nao contem o template real da terceira forma ({template!r}, "
        "derivado de recurso._pagto_business_key via inspect.getsource)."
    )


def _linha_gatilho_regulatorio_contrato() -> str:
    """Isola a linha `**Gatilho regulatorio:**` do contrato — o site analogo, no contrato, ao
    paragrafo GATILHO REGULATORIO do BPMN."""
    texto = _CONTRATO_PAGTO.read_text(encoding="utf-8")
    m = re.search(r"^\*\*Gatilho regulatorio:\*\*.*$", texto, re.MULTILINE)
    assert m, "linha '**Gatilho regulatorio:**' nao encontrada no contrato de PAGTO"
    return m.group(0)


def _secao_business_key_contrato() -> str:
    """Isola a secao `## Business key (idempotencia)` do contrato ate o proximo `## ` — o site
    analogo, no contrato, ao paragrafo BUSINESS KEY do BPMN."""
    texto = _CONTRATO_PAGTO.read_text(encoding="utf-8")
    inicio = texto.find("## Business key")
    assert inicio != -1, "secao '## Business key' nao encontrada no contrato de PAGTO"
    fim = texto.find("\n## ", inicio + 1)
    return texto[inicio : fim if fim != -1 else len(texto)]


def test_a_linha_gatilho_regulatorio_do_contrato_nomeia_o_handoff_real_de_contas() -> None:
    """F6/§Δ, contraparte de contrato da mutacao F/H do gatekeeper (reverter a prosa do contrato
    com a tabela intacta): isolada a linha, nao o arquivo inteiro (a tabela ja nomeia
    `numero_guia_tiss`/`glosa_id`/etc. e nao deve mascarar uma regressao so na prosa)."""
    linha = _linha_gatilho_regulatorio_contrato()
    assert CONTAS_HANDOFF_TOPIC in linha or _forma_curta_do_topico(CONTAS_HANDOFF_TOPIC) in linha, (
        f"linha 'Gatilho regulatorio' do contrato de PAGTO nao nomeia o handoff real de CONTAS-001 "
        f"({CONTAS_HANDOFF_TOPIC}) — premissa stale (gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION)."
    )


def test_a_secao_business_key_do_contrato_nomeia_os_dois_handoffs_e_a_terceira_forma() -> None:
    """F6/§Δ, contraparte de contrato da mutacao G/H do gatekeeper. Isolada a secao "## Business
    key" (nao o arquivo inteiro, pela mesma razao acima): a tabela "Variaveis de entrada" nomeia
    `glosa_id`/`numero_guia_tiss` independentemente e NAO pode mascarar uma regressao na prosa
    desta secao especifica.
    """
    secao = _secao_business_key_contrato()
    template = _template_terceira_forma_business_key()
    assert CONTAS_HANDOFF_TOPIC in secao or _forma_curta_do_topico(CONTAS_HANDOFF_TOPIC) in secao, (
        f"secao 'Business key' do contrato nao nomeia o handoff de CONTAS-001 ({CONTAS_HANDOFF_TOPIC})"
    )
    assert RECURSO_HANDOFF_TOPIC in secao or _forma_curta_do_topico(RECURSO_HANDOFF_TOPIC) in secao, (
        f"secao 'Business key' do contrato nao nomeia o handoff de RECURSO-001 ({RECURSO_HANDOFF_TOPIC}) "
        "— a terceira forma da business key (glosa revertida) ficou sem premissa "
        "(gap PERSP-PAGTO-STALE-CONTAS-ASSUMPTION)."
    )
    assert template in secao, (
        f"secao 'Business key' do contrato nao contem o template real da terceira forma "
        f"({template!r}, derivado de recurso._pagto_business_key via inspect.getsource)."
    )
