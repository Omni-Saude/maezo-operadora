"""Cerca PERSP-AUTH-VOICE: nenhum artefato normativo atribui ao lado PAGADOR a emissao da guia TISS.

GAP-REGISTER `PERSP-AUTH-VOICE`: em SP-OP-AUTH-001 a operadora AUTORIZA (emite a autorizacao /
o numero de autorizacao); quem emite a guia TISS e' sempre o prestador. O padrao de voz de
prestador ja apareceu em artefatos reais desta cadeia: o rotulo BPMN de `ST_EmitirAutorizacaoAuto`,
o comentario de `spec/agents/rafael/agent.yaml`, o SYSTEM_PROMPT de
`src/maezo/agents/rafael/prompts.py` (o de MAIOR risco — um LLM le a frase, nota do GAP-REGISTER)
e, em INGLES, a `description` do golden `tests/evals/golden/rafael/EVL-RAFAEL-02.json`
(`git show 308626b0^:tests/evals/golden/rafael/EVL-RAFAEL-02.json`, linha 6: "SP-OP-AUTH-001's own
automatic path issues the TISS guide"), removida incidentalmente por RAF-01/RAF-06.

Escopo ESTRUTURAL, sem NENHUMA excecao por caminho
--------------------------------------------------
A cerca varre apenas os artefatos NORMATIVOS, enumerados a partir do indice do git
(`git ls-files`, so' arquivos RASTREADOS): `spec/`, `src/`, `tests/evals/` (goldens),
`docs/processes/` (contratos e test-specs) e `docs/runbooks/`.

Duas consequencias, ambas deliberadas e ambas pinadas por teste abaixo:

1. **Hermetica.** A enumeracao vem do indice do git, nunca do sistema de arquivos. Diretorios
   IGNORADOS e presentes so' na maquina de quem roda (`docs/audits/`, `docs/prompts/` — as fontes
   de auditoria vivas, editadas por sessoes irmas) nao existem para esta cerca, mesmo quando estao
   no checkout. Um veredito de teste unitario nao pode depender de conteudo nao rastreado.
2. **Sem allowlist.** `docs/evidence-ledger.md` e `docs/review-queue.md` — os registros
   append-only cuja narrativa CITA o padrao rejeitado para dizer o que foi corrigido — ficam fora
   por CONSTRUCAO (nao estao em nenhuma das raizes varridas), nao por excecao. Este e' o mesmo
   mecanismo estrutural de `maezo.platform.validation.perspective`, que resolve o problema
   escolhendo o que le (Tier A so' `spec/processes/*.bpmn` e `*.dmn`, Tier B so' nos YAML
   parseados) e declara, em texto, que NAO ha mecanismo de excecao — nem por arquivo nem por bloco
   `historico:` (ADR-0040 D7). CORRECAO DE UMA AFIRMACAO ANTERIOR DESTE MESMO ARQUIVO: a versao
   inicial desta cerca varria `docs/` inteiro e recortava aqueles dois arquivos por caminho,
   dizendo seguir "a mesma convencao" de `perspective`. Era o contrario: `perspective` RECUSA
   allowlist por arquivo pelo nome. O recorte foi removido, junto com a excecao implicita do
   proprio arquivo de teste. A secao "Where historical references go" de `perspective` tambem
   nomeia `docs/processes/` como lugar de narrativa; aqui `docs/processes/` E' varrido, porque a
   classe de defeito desta cerca e' vocabulario de contrato ("quem emite a guia") e um contrato de
   processo e' artefato normativo — e porque a propria nota de PR-4 daquele modulo planeja varrer
   `docs/processes/**.md`. A narrativa de remocao desta cadeia pertence ao ledger/review-queue.

Este modulo tambem nao se exclui da varredura por caminho: ele simplesmente nao esta em nenhuma
raiz varrida, e TODA citacao do defeito aqui dentro e' montada por CONCATENACAO DE FRAGMENTOS
(`_frag(...)`), de modo que nenhum literal deste arquivo casaria os proprios padroes.

Deteccao: conjunto EXPLICITO de padroes, sobre texto normalizado
----------------------------------------------------------------
O texto de cada arquivo e' normalizado (todo run de espaco em branco -> um espaco unico), o que
faz a cerca enxergar frases QUEBRADAS EM VARIAS LINHAS — o SYSTEM_PROMPT corrigido por esta
mesma PR ocupa tres linhas. As lacunas dos padroes NUNCA atravessam `.` nem `;`, para nao unir
duas frases independentes.

Os padroes sao nomeados e pinados (`_PADROES`), em pt-BR E em ingles:

* `P1-ptbr-ativo`     — ator pagador ... emit(e|ir|ida|indo|iu)/gera/expede ... "a guia"
* `P2-en-ativo`       — ator pagador ... issues|issued|issuing ... "(the) (TISS) guide|guia"
* `P3-ptbr-passivo`   — "guia ... emitida pela operadora" (passiva com a guia como SUJEITO)
* `P4-guia-passivo`   — "emitida pela operadora ... a guia" (passiva com a guia POSPOSTA)
* `P5-ptbr-nominal`   — ator pagador ... "a emissao da guia" (nominal, ator ANTES)
* `P7-ptbr-nominal-posposto` — "a emissao da guia ... da/pela operadora" (nominal, ator DEPOIS)
* `P8-en-passivo`     — "(the) (TISS) guide is/was/gets issued by (the) operadora|payer"
* `P6-bpmn-rotulo`    — SO' em atributos `name=` de `.bpmn`: emitir/gerar/expedir tendo a guia
  como OBJETO DIRETO ("Emitir autorizacao (guia TISS)"). Num BPMN de `spec/processes/` o sujeito
  de toda tarefa e' o processo da operadora: o ator pagador e' ESTRUTURAL, nao lexical, e por isso
  este padrao nao exige token de ator. "Emitir autorizacao (numero de autorizacao NA guia TISS)"
  nao casa, porque ali a guia e' locativo, nao objeto.

`ATOR PAGADOR` = operadora | processo | payer | health plan | `SP-OP-<CHAVE>-<NNN>`. O prestador
como sujeito e' a voz CORRETA e nunca deve acusar.

Tres GUARDAS reduzem falsos positivos (todas medidas e pinadas por teste):

* **negacao** — se a palavra imediatamente anterior ao verbo (ou, quando essa palavra e' copula,
  a anterior a ela) e' `nao/nunca/jamais/never/not`, nao e' achado. "A operadora NAO emite a guia
  TISS: quem emite e o prestador" — a frase mais natural para DOCUMENTAR a regra correta — fica
  verde. A guarda e' ESTRITA de proposito: em "o PROCESSO (nao voce) emite a guia" a palavra
  anterior ao verbo e' "voce)", nao "nao", entao aquele defeito historico continua vermelho.
* **prestador como sujeito local** — o sintagma do prestador imediatamente antes do verbo, com
  determinante e modificador OPCIONAIS ("o prestador", "cada prestador", "o proprio prestador",
  "prestadores credenciados"), ou a relativa "prestador, que emite", e' voz CORRETA e nao e'
  achado — mesmo com "operadora" antes na frase. Duas condicoes impedem que a guarda engula
  defeito: uma PREPOSICAO antes do sintagma torna o prestador obliquo ("a operadora,
  diferentemente DO prestador, emite a guia" continua vermelho) e uma VIRGULA entre o sintagma e
  o verbo tambem ("a operadora, e nao o prestador, emite a guia" continua vermelho). A mesma
  guarda vale para o trecho entre o verbo e o objeto, o que deixa verde a elipse correta "a
  operadora emite a autorizacao e o prestador a guia TISS".
* **objeto emitido, SO' nas passivas** — em `P4`/`P8` o sujeito do participio fica FORA do
  casamento, entao o objeto e' checado na janela a esquerda: "A autorizacao e emitida pela
  operadora e a guia TISS pelo prestador" e' a formulacao CORRETA desta regra e fica verde. Nos
  padroes de ator-primeiro o objeto casado JA' E' a guia, e aplicar a checagem ali suprimia
  defeitos reais ("a operadora emite a autorizacao e a guia TISS") — regressao encontrada pelo
  verificador (§Delta-2 G1) e removida.

Duas decisoes de varredura sustentam essas guardas:

* a lacuna ATOR -> VERBO e' GULOSA, isto e', escolhe o verbo MAIS PROXIMO do objeto. Sem isso, em
  "a operadora nao emite o parecer, mas emite a guia TISS" o casamento pegaria o PRIMEIRO verbo,
  negado, e a guarda de negacao mascararia o defeito real do segundo;
* uma rejeicao por guarda NAO encerra a busca naquele padrao: a varredura recomeca UM CARACTERE
  adiante do inicio do candidato rejeitado, nunca depois do seu fim, de modo que um candidato
  descartado nunca esconde um achado posterior.

LIMITE DECLARADO (o que esta cerca NAO ve, e onde ela erra) — medido, nao estimado
----------------------------------------------------------------------------------
`test_o_limite_declarado_da_cerca_esta_pinado` fixa a classe de FALSO NEGATIVO: verbo fora do
conjunto enumerado (p.ex. "libera"/"disponibiliza"), sujeito pagador separado do verbo por `.`/`;`
ou a mais de 80 caracteres, idioma alem de pt-BR/ingles, e qualquer afirmacao semantica que nao
use a palavra "guia" — mais a **CLASSE A**, que e' GERATIVA e nao uma lista de casos: o ator
pagador vem de um LEXICO ENUMERADO (`_PAGADOR`), nao de uma ontologia, entao todo termo de pagador
que a lista nao nomeia e' invisivel. "autogestao", "cooperativa medica" e "administradora de
beneficios" sao os exemplos pinados; a resposta certa nao e' persegui-los um a um, e' saber que a
lista E' a fronteira e amplia-la quando um termo passar a importar — foi o que se fez em H1 com
"plano de saude", "seguradora" e "convenio".

`test_os_falsos_positivos_conhecidos_estao_pinados` fixa a classe de FALSO POSITIVO que sobra,
RE-MEDIDA apos as guardas de §Delta-2 G2: a negacao afastada do verbo ("a operadora nao e quem
emite a guia"), a negacao separada do verbo por uma incisa ("a operadora jamais, em nenhuma
hipotese, emite a guia") e um objeto emitido fora da lista enumerada de substantivos ("o laudo e
emitido pela operadora junto com a guia"). Os tres ficam vermelhos hoje; o pino existe para que a
lacuna seja fato verificavel e para que quem esbarrar nela saiba que e' conhecida — nunca para
licenciar reintroduzir o defeito. As tres construcoes que o verificador reportou como falso
positivo NAO DECLARADO ("cada prestador", "o proprio prestador", "prestadores credenciados") NAO
estao nesta lista porque deixaram de ser falso positivo: entraram nas formas PERMITIDAS.

A esses soma-se a **CLASSE B**, tambem GERATIVA: o reconhecedor de sujeito-prestador e' de
SUPERFICIE — casa uma FORMA de sintagma adjacente ao verbo, nao analisa a frase. Logo, toda
construcao em que o prestador e' o sujeito real mas NAO esta adjacente ao verbo continua
acusando: relativa com material interposto ("O prestador, QUE ATENDE PELA OPERADORA, emite a guia
TISS") e sujeito pronominal ("A operadora informa ao prestador que ELE emite a guia"). As duas
estao pinadas como VERMELHAS medidas. Fechar essa classe exigiria analise sintatica, nao mais
regex; ate' la, quem escrever uma dessas frases num artefato varrido reformula ou trata o achado
como conhecido — nunca alarga a cerca em silencio.

Esta e' uma cerca LEXICAL: ela impede a REGRESSAO das formulacoes conhecidas, nao substitui
revisao. Declarar e pinar o limite (nos dois sentidos) e' o mesmo formato que
`maezo.platform.validation.perspective` adota para os seus.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]

# Raizes NORMATIVAS. `docs/evidence-ledger.md` e `docs/review-queue.md` ficam fora por
# construcao (nao ha raiz `docs/` inteira) — nao ha, e nao pode haver, allowlist por caminho.
_ROOTS: tuple[str, ...] = ("spec", "src", "tests/evals", "docs/processes", "docs/runbooks")
_TEXT_SUFFIXES = frozenset({".bpmn", ".dmn", ".yaml", ".yml", ".md", ".py", ".json", ".xsd"})


def _frag(*partes: str) -> str:
    """Monta uma citacao do defeito por concatenacao: nenhum literal deste arquivo casa a cerca."""
    return "".join(partes)


# --- vocabulario -----------------------------------------------------------------------------
# Lexico ENUMERADO do ator pagador. E' uma lista, nao uma ontologia — ver LIMITE DECLARADO
# (classe A): um termo de pagador fora desta lista e' invisivel para a cerca.
_PAGADOR = (
    r"(?:operadoras?|processos?|payer|health\s+plan"
    r"|planos?\s+de\s+sa[uú]de|seguradoras?|conv[eê]nios?"
    r"|SP-OP-[A-Za-z]+-\d{3})\b"
)
_EMITIR = r"emit(?:e|em|es|ir|ida|idas|ido|idos|indo|iu|iram|ir[aá]|ir[aã]o)"
_SINONIMO = r"(?:ger(?:a|am|ar|ada|adas|ando)|exped(?:e|em|ir|ida|idas|indo)|produz(?:em|ir)?)"
_PARTICIPIO = r"(?:emitid|expedid|gerad)(?:a|as|o|os)"
_ARTIGO = r"(?:a|as|uma|umas)"
_GUIA = r"gui" + r"as?\b"
_GAP = r"[^.;]{0,80}?"
# GULOSO de proposito nos padroes de ator-primeiro: escolhe o verbo MAIS PROXIMO do objeto,
# nao o primeiro da frase. Sem isso, "a operadora nao emite o parecer, mas emite a guia"
# casaria o PRIMEIRO "emite" (negado) e a guarda de negacao mascararia o defeito real.
_GAP_ATOR = r"[^.;]{0,80}"
_PERTO = r"[^.;]{0,40}?"
_NOMINAL = r"(?:emiss[aã]o|expedi[cç][aã]o)"

_PADROES: dict[str, re.Pattern[str]] = {
    "P1-ptbr-ativo": re.compile(
        rf"{_PAGADOR}{_GAP_ATOR}\b(?P<verbo>{_EMITIR}|{_SINONIMO})\b"
        rf"(?P<meio>{_PERTO})\b{_ARTIGO}\s+{_GUIA}",
        re.IGNORECASE,
    ),
    "P2-en-ativo": re.compile(
        rf"{_PAGADOR}{_GAP_ATOR}\b(?P<verbo>issues?|issued|issuing)\b(?P<meio>{_PERTO})"
        rf"\b(?:the\s+)?(?:TISS\s+)?(?:guides?\b|{_GUIA})",
        re.IGNORECASE,
    ),
    "P3-ptbr-passivo": re.compile(
        rf"\b{_GUIA}(?P<meio>{_GAP})\b(?P<verbo>{_PARTICIPIO})\s+pel[ao]s?\s+{_PAGADOR}\b",
        re.IGNORECASE,
    ),
    "P4-guia-passivo": re.compile(
        rf"\b(?P<verbo>{_PARTICIPIO})\s+pel[ao]s?\s+{_PAGADOR}\b"
        rf"(?P<meio>{_PERTO})\b{_ARTIGO}\s+{_GUIA}",
        re.IGNORECASE,
    ),
    "P5-ptbr-nominal": re.compile(
        rf"{_PAGADOR}{_GAP_ATOR}\b(?P<verbo>{_NOMINAL})\s+d(?:a|as)\s+(?P<meio>){_GUIA}",
        re.IGNORECASE,
    ),
    "P7-ptbr-nominal-posposto": re.compile(
        rf"\b(?P<verbo>{_NOMINAL})\s+d(?:a|as)\s+{_GUIA}(?P<meio>{_GAP})"
        rf"\b(?:d[ao]s?|pel[ao]s?)\s+{_PAGADOR}\b",
        re.IGNORECASE,
    ),
    "P8-en-passivo": re.compile(
        rf"\b(?:the\s+)?(?:TISS\s+)?(?:guides?\b|{_GUIA})\s+"
        rf"(?:is|are|was|were|gets?|will\s+be|has\s+been|have\s+been|be)\s+"
        rf"(?P<verbo>issued)\s+by\s+(?:the\s+)?(?P<meio>){_PAGADOR}\b",
        re.IGNORECASE,
    ),
}

# So' para atributos `name=` de arquivos .bpmn (ator pagador estrutural — ver docstring).
_PADRAO_ROTULO_BPMN = re.compile(
    rf"\b(?:{_EMITIR}|{_SINONIMO})\b[^.;]{{0,60}}?(?:\(\s*|\b{_ARTIGO}\s+){_GUIA}",
    re.IGNORECASE,
)
_BPMN_NAME_ATTR = re.compile(r'\bname="([^"]*)"')

# --- guardas ---------------------------------------------------------------------------------
_NEGACOES = frozenset({"nao", "não", "nunca", "jamais", "never", "not", "n't", "doesn't", "don't"})
_COPULAS = frozenset(
    {
        "e",
        "é",
        "foi",
        "sera",
        "será",
        "sao",
        "são",
        "serao",
        "serão",
        "eram",
        "era",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "gets",
        "get",
        "got",
        "will",
        "does",
        "do",
    }
)
# Sintagma do PRESTADOR como sujeito local do verbo: determinante e modificador opcionais
# ("o prestador", "cada prestador", "o proprio prestador", "prestadores credenciados"), sem
# virgula entre o sintagma e o verbo — ou a forma relativa "prestador, que emite".
_NP_PRESTADOR = re.compile(
    r"(?:\b(?:o|os|um|uns|cada|todo|toda|todos|todas|qualquer|quaisquer)\s+)?"
    r"(?:(?:pr[oó]pri[oa]s?|mesm[oa]s?)\s+)?"
    r"\bprestador(?:a|es|as)?\b(?P<adj>(?:\s+[A-Za-zÀ-ÿ]+){0,2})\s+$",
    re.IGNORECASE,
)
_REL_PRESTADOR = re.compile(r"\bprestador(?:a|es|as)?\b\s*,\s*que\s+$", re.IGNORECASE)
# Preposicao antes do sintagma torna o prestador OBLIQUO, nao sujeito ("diferentemente DO
# prestador, emite a guia" continua sendo defeito).
_PREP_ANTES = re.compile(
    r"\b(?:d[eoa]s?|ao|aos|[aà]s?|pel[oa]s?|com|para|entre|sobre|por|contra|sem)\s*$",
    re.IGNORECASE,
)
# Palavras funcionais que NAO podem contar como modificador do sintagma (senao "paga o prestador
# e emite a guia" seria lido como prestador-sujeito).
_NAO_MODIFICADOR = frozenset(
    {
        "e",
        "ou",
        "mas",
        "que",
        "nem",
        "nao",
        "não",
        "se",
        "ja",
        "já",
        "so",
        "só",
        "tambem",
        "também",
        "entao",
        "então",
        "porem",
        "porém",
        "logo",
        "pois",
        "quando",
        "onde",
    }
)


def _prestador_e_sujeito(antes: str) -> bool:
    """O prestador e' o sujeito local do verbo? Entao e' a voz CORRETA e nao ha achado."""
    if _REL_PRESTADOR.search(antes):
        return True
    m = _NP_PRESTADOR.search(antes)
    if m is None:
        return False
    if any(tok.lower() in _NAO_MODIFICADOR for tok in (m.group("adj") or "").split()):
        return False
    return not _PREP_ANTES.search(antes[: m.start()])


# Objetos que a operadora PODE emitir corretamente — se o objeto emitido e' um destes e nao a
# guia, nao ha defeito ("A autorizacao e emitida pela operadora e a guia TISS pelo prestador").
_OUTRO_OBJETO = re.compile(
    r"\b(?:autoriza[cç][aã]o|autoriza[cç][oõ]es|negativas?|demonstrativos?|parecer(?:es)?"
    r"|glosas?|recibos?|comprovantes?|notas?\s+fisca\w*|carteirinhas?|protocolos?)\b",
    re.IGNORECASE,
)


def _negado(antes: str) -> bool:
    """A palavra imediatamente antes do verbo (ou, se copula, a anterior) e' uma negacao?"""
    toks = antes.split()
    if not toks:
        return False
    if toks[-1].lower() in _NEGACOES:
        return True
    return len(toks) >= 2 and toks[-1].lower() in _COPULAS and toks[-2].lower() in _NEGACOES


def _objeto_nao_e_a_guia(trecho: str) -> bool:
    """Ha um objeto emitido DIFERENTE da guia entre o inicio do trecho e o verbo/objeto?"""
    ultimo = None
    for m in _OUTRO_OBJETO.finditer(trecho):
        ultimo = m
    if ultimo is None:
        return False
    return "gui" + "a" not in trecho[ultimo.end() :].lower()


def _tracked_files(repo: Path = _REPO, roots: tuple[str, ...] = _ROOTS) -> list[Path]:
    """Enumera SO' arquivos rastreados pelo git (hermetico: ignorados nao entram)."""
    saida = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--", *roots],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    arquivos: list[Path] = []
    for relativo in saida.split("\0"):
        if not relativo:
            continue
        caminho = repo / relativo
        if caminho.suffix.lower() not in _TEXT_SUFFIXES or not caminho.is_file():
            continue
        arquivos.append(caminho)
    return arquivos


def _normalise(texto: str) -> tuple[str, list[int]]:
    """Colapsa todo espaco em branco num unico espaco; devolve tambem a linha de cada caractere."""
    partes: list[str] = []
    linhas: list[int] = []
    linha = 1
    espaco_anterior = True
    for ch in texto:
        if ch.isspace():
            if not espaco_anterior:
                partes.append(" ")
                linhas.append(linha)
                espaco_anterior = True
        else:
            partes.append(ch)
            linhas.append(linha)
            espaco_anterior = False
        if ch == "\n":
            linha += 1
    return "".join(partes), linhas


def _hits_no_texto(texto: str, rotulo: str = "<texto>") -> list[str]:
    """Aplica o conjunto de padroes de prosa ao texto normalizado, com as tres guardas."""
    normalizado, linhas = _normalise(texto)
    achados: list[str] = []
    for nome, padrao in _PADROES.items():
        pos = 0
        while pos <= len(normalizado):
            m = padrao.search(normalizado, pos)
            if m is None:
                break
            inicio_verbo = m.start("verbo")
            antes = normalizado[max(0, inicio_verbo - 60) : inicio_verbo]
            meio = m.group("meio") or ""
            # Uma rejeicao NAO pode mascarar um achado posterior: a varredura recomeca um
            # caractere adiante do inicio do candidato rejeitado, nunca depois do seu fim.
            if _negado(antes) or _prestador_e_sujeito(antes) or _prestador_e_sujeito(meio):
                pos = m.start() + 1
                continue
            # SO' as passivas precisam checar o objeto: nelas o sujeito do participio esta FORA
            # do casamento. Nos padroes de ator-primeiro o objeto casado JA' E' a guia, e aplicar
            # a checagem ao trecho `meio` suprimia defeitos reais ("a operadora emite a
            # autorizacao e a guia TISS") — regressao encontrada pelo verificador (§Delta-2 G1).
            if nome in {"P4-guia-passivo", "P8-en-passivo"} and _objeto_nao_e_a_guia(antes):
                pos = m.start() + 1
                continue
            ini = linhas[m.start()] if m.start() < len(linhas) else 0
            fim = linhas[min(m.end(), len(linhas)) - 1] if linhas else 0
            onde = f"{ini}" if ini == fim else f"{ini}-{fim}"
            achados.append(f"{rotulo}:{onde}: [{nome}] {m.group().strip()}")
            pos = max(m.end(), m.start() + 1)
    return achados


def _hits_rotulos_bpmn(texto: str, rotulo: str = "<texto>") -> list[str]:
    """Aplica o padrao de objeto direto aos atributos `name=` de um BPMN."""
    achados: list[str] = []
    for m in _BPMN_NAME_ATTR.finditer(texto):
        nome_tarefa = " ".join(m.group(1).split())
        if _PADRAO_ROTULO_BPMN.search(nome_tarefa):
            linha = texto.count("\n", 0, m.start()) + 1
            achados.append(f"{rotulo}:{linha}: [P6-bpmn-rotulo] name={nome_tarefa!r}")
    return achados


def _hits(path: Path, repo: Path = _REPO) -> list[str]:
    try:
        texto = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        rotulo = path.relative_to(repo).as_posix()
    except ValueError:
        rotulo = str(path)
    achados = _hits_no_texto(texto, rotulo)
    if path.suffix.lower() == ".bpmn":
        achados.extend(_hits_rotulos_bpmn(texto, rotulo))
    return achados


def varrer(repo: Path = _REPO, roots: tuple[str, ...] = _ROOTS) -> list[str]:
    """Entrada parametrizada por RAIZ: permite apontar a cerca para OUTRO checkout (prova F1)."""
    achados: list[str] = []
    for path in _tracked_files(repo, roots):
        achados.extend(_hits(path, repo))
    return achados


# --- provas ----------------------------------------------------------------------------------


def test_nenhum_artefato_atribui_ao_pagador_a_emissao_da_guia() -> None:
    achados = varrer()
    assert achados == [], (
        "GAP PERSP-AUTH-VOICE: voz de prestador (o lado PAGADOR emitindo a guia TISS) "
        "reintroduzida num artefato normativo. Apenas o PRESTADOR emite a guia TISS; a "
        "operadora emite a AUTORIZACAO (numero de autorizacao). Achados:\n" + "\n".join(achados)
    )


def test_a_enumeracao_e_hermetica_so_le_o_indice_do_git(tmp_path: Path) -> None:
    """F1/D4: a cerca so' enxerga arquivos RASTREADOS — provado num repo git DESCARTAVEL.

    Nada e' escrito na arvore de trabalho real (o teste anterior plantava uma sonda em `spec/`).
    Parte (a): num repo de `tmp_path` com dois arquivos, so' o RASTREADO e' varrido, e o arquivo
    NAO rastreado com o defeito fica invisivel. Parte (b): ao entrar no indice, o MESMO arquivo,
    com o MESMO texto, passa a acusar. Parte (c): no repo real, o conjunto varrido e' exatamente
    o que `git ls-files` devolve (leitura pura, sem escrita).
    """
    defeito = _frag("A ", "operadora ", "emite ", "a ", "guia", " TISS neste artefato.\n")
    assert _hits_no_texto(defeito, "sonda") != [], "sonda vacua: o texto teria de casar a cerca"

    repo = tmp_path / "repo-descartavel"
    (repo / "spec").mkdir(parents=True)
    (repo / "spec" / "rastreado.md").write_text("Texto limpo, sem o padrao.\n", encoding="utf-8")
    (repo / "spec" / "fora-do-indice.md").write_text(defeito, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "spec/rastreado.md"], check=True)

    roots = ("spec",)
    assert {p.name for p in _tracked_files(repo, roots)} == {"rastreado.md"}
    assert varrer(repo, roots) == [], "arquivo fora do indice entrou: cerca nao e' hermetica"

    subprocess.run(["git", "-C", str(repo), "add", "spec/fora-do-indice.md"], check=True)
    achados = varrer(repo, roots)
    assert len(achados) == 1 and "fora-do-indice.md" in achados[0], achados

    rastreados = {p.resolve() for p in _tracked_files()}
    saida = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z", "--", *_ROOTS],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    do_indice = {
        (_REPO / r).resolve()
        for r in saida.split("\0")
        if r and (_REPO / r).suffix.lower() in _TEXT_SUFFIXES and (_REPO / r).is_file()
    }
    assert rastreados == do_indice


def test_o_escopo_e_estrutural_e_nao_ha_allowlist_por_caminho() -> None:
    """F2: as raizes sao normativas; ledger/review-queue caem fora por CONSTRUCAO, sem excecao."""
    assert _ROOTS == ("spec", "src", "tests/evals", "docs/processes", "docs/runbooks")
    varridos = {p.relative_to(_REPO).as_posix() for p in _tracked_files()}
    for registro in ("docs/evidence-ledger.md", "docs/review-queue.md"):
        assert (_REPO / registro).is_file(), f"{registro} deveria existir no repo"
        assert registro not in varridos, f"{registro} entrou na varredura"


def test_a_cerca_acusa_o_rotulo_bpmn_pre_fix_da_tarefa_emitir_autorizacao_auto() -> None:
    """F3(a): MUT-E — o rotulo BPMN exato que o GAP-REGISTER cita tem de ficar VERMELHO."""
    pre_fix = _frag("Emitir ", "autorizacao ", "(guia", " TISS)")
    pos_fix = "Emitir autorizacao (numero de autorizacao na guia TISS)"
    assert _hits_rotulos_bpmn(f'<bpmn:serviceTask name="{pre_fix}" />', "pre") != []
    assert _hits_rotulos_bpmn(f'<bpmn:serviceTask name="{pos_fix}" />', "pos") == []
    for nome in (
        "Emitir autorizacao (decisao do auditor)",
        "Lote de guias TISS recebido do prestador",
        "Decidir destino da guia com pendencia expirada",
    ):
        assert _hits_rotulos_bpmn(f'<x name="{nome}" />', "ctrl") == [], nome


def test_a_cerca_acusa_a_passiva_invertida_no_system_prompt() -> None:
    """F3(b): MUT-C — inverter a propria frase reparada ("... pela operadora") fica VERMELHO."""
    reparada = (
        "nesse caso o PROCESSO (nao voce) emite a autorizacao (o numero de autorizacao que "
        "consta na guia TISS; a guia em si e emitida pelo prestador, nunca pela operadora)."
    )
    invertida = (
        "nesse caso o PROCESSO (nao voce) emite a autorizacao (o numero de autorizacao que "
        "consta na guia TISS; "
        + _frag("a guia", " em si e ", "emitida ", "pela ", "operadora", ", nunca pelo prestador).")
    )
    assert _hits_no_texto(reparada, "reparada") == [], "a frase reparada nao pode acusar"
    achados = _hits_no_texto(invertida, "invertida")
    assert achados != [], "a inversao da frase reparada TEM de acusar"
    assert any("P3-ptbr-passivo" in a for a in achados), achados


def test_a_cerca_acusa_o_comentario_e_o_prompt_pre_fix() -> None:
    """F3(c): MUT-A/MUT-B — as formulacoes historicas reais (pt-BR e ingles) ficam VERMELHAS."""
    comentario = _frag("# SP-OP-AUTH-001 ", "emite ", "a ", "guia", "; Rafael apenas inicia o processo.\n")
    prompt = _frag(
        "valor esta dentro do teto do tenant — nesse caso o PROCESSO (nao voce) ",
        "emite ",
        "a ",
        "guia",
        '."""\n',
    )
    golden_en = _frag(
        '"description": "SP-OP-AUTH-001',
        "'s own automatic path ",
        "issues ",
        "the TISS guide",
        ', never Rafael directly."\n',
    )
    for rotulo, texto in (("yaml", comentario), ("prompt", prompt), ("golden-en", golden_en)):
        assert _hits_no_texto(texto, rotulo) != [], f"{rotulo} tinha de acusar"


# Formas PERMITIDAS: a voz correta do prestador, mais as 5 formas novas (B3-B7) e as 4 sondas
# adversariais (C1-C4) do verificador — todas afirmacoes CORRETAS, todas tem de ficar verdes.
_FORMAS_PERMITIDAS: tuple[tuple[str, str], ...] = (
    ("B0-prestador-ativo", _frag("o prestador ", "emite ", "a ", "guia", " TISS.")),
    ("B1-prestador-passivo", _frag("a ", "guia", " TISS e ", "emitida ", "pelo prestador", ".")),
    ("B2-contrastiva", "a operadora emite a autorizacao; o prestador emite a guia TISS."),
    ("B2b-quando", "quando o prestador emite a guia, a operadora autoriza."),
    ("B2c-locativo", "o numero de autorizacao consta na guia TISS emitida pelo prestador."),
    ("B2d-rotulo", "a operadora emite a autorizacao (numero de autorizacao na guia TISS)."),
    ("B3-outro-objeto", "A autorizacao e emitida pela operadora e a guia TISS pelo prestador."),
    ("B4-prestador-expede", "O prestador expede a guia TISS e a operadora devolve o numero."),
    ("B5-demonstrativo", "A operadora emite o demonstrativo de pagamento ao prestador."),
    ("B6-processa", "A operadora processa a guia TISS emitida pelo prestador."),
    ("B7-lote", "Lote de guias TISS recebido do prestador pela operadora."),
    ("C1-segundo", "Segundo a operadora, o prestador emite a guia TISS."),
    ("C2-informa-que", "A operadora informa que o prestador emite a guia TISS."),
    ("C3-negacao", "A operadora nao emite a guia TISS: quem emite e o prestador."),
    (
        "C4-relativa",
        "Na operadora o numero de autorizacao e devolvido ao prestador, que emite a guia TISS.",
    ),
    ("E1-cada-prestador", "Segundo a operadora, cada prestador emite a guia TISS."),
    ("E2-proprio-prestador", "A operadora esclarece: o proprio prestador emite a guia TISS."),
    ("E3-prestadores-credenciados", "Na operadora, prestadores credenciados emitem a guia TISS."),
    ("E7-elipse", "A operadora emite a autorizacao e o prestador a guia TISS."),
)


def test_a_cerca_nao_acusa_a_voz_correta_do_prestador() -> None:
    """D2: 0 falsos positivos nas formas PERMITIDAS, incluindo as sondas adversariais C1-C4."""
    falsos = [(n, _hits_no_texto(f, n)) for n, f in _FORMAS_PERMITIDAS if _hits_no_texto(f, n)]
    assert falsos == [], f"falsos positivos: {falsos}"


# As 10 frases do verificador (VERIFY §F4), VERBATIM — A6 e A10 tal como ele as escreveu
# (VERIFY §Delta.2 D1); montadas por fragmentos para nao casarem a cerca por acidente.
_SENTENCAS_QUE_DEVEM_ACUSAR: tuple[tuple[str, str], ...] = (
    ("A1-passiva-pagadora", _frag("A ", "guia", " TISS e ", "emitida ", "pela ", "operadora", ".")),
    ("A2-plural", _frag("As ", "operadoras", " ", "emitem ", "as ", "guias", " TISS.")),
    (
        "A3-quebrada-em-linhas",
        _frag("a ", "operadora", ", conforme o contrato,\n", "emite\n", "a ", "guia", " TISS.\n"),
    ),
    (
        "A4-contrastiva",
        _frag("a ", "operadora", ", diferentemente do prestador, ", "emite ", "a ", "guia", " TISS."),
    ),
    (
        "A5-ingles",
        _frag(
            "SP-OP-AUTH-001",
            "'s own automatic path ",
            "issues ",
            "the TISS guide",
            ", never Rafael directly.",
        ),
    ),
    (
        "A6-nominal-posposto",
        _frag("a ", "emissao ", "da ", "guia", " TISS e responsabilidade da ", "operadora", "."),
    ),
    ("A7-sinonimo-gera", _frag("O ", "processo ", "da operadora ", "gera ", "a ", "guia", " TISS.")),
    ("A8-sinonimo-expede", _frag("A ", "operadora ", "expede ", "a ", "guia", " TISS.")),
    ("A9-futuro-acentuado", _frag("A ", "operadora ", "emitirá ", "a ", "guia", " TISS.")),
    (
        "A10-ator-distante",
        _frag(
            "o resultado da decisao automatica do motor de regras da ",
            "operadora",
            " determina que ela ",
            "emite ",
            "a ",
            "guia",
            " TISS.",
        ),
    ),
)

# As 5 frases NOVAS do verificador (VERIFY §Delta.2); N2 e' verbatim (o falso negativo D3).
_SENTENCAS_NOVAS_QUE_DEVEM_ACUSAR: tuple[tuple[str, str], ...] = (
    ("N1-passiva-futura", _frag("A ", "guia", " TISS sera ", "expedida ", "pela ", "operadora", ".")),
    (
        "N2-ingles-passivo",
        _frag("The TISS guide", " is ", "issued ", "by the ", "operadora", ", not by the provider."),
    ),
    ("N3-verbo-virgula", _frag("A ", "operadora ", "emite", ", mensalmente, ", "a ", "guia", ".")),
    ("N4-perifrase", _frag("A ", "operadora ", "passa a ", "emitir ", "a ", "guia", " TISS.")),
    ("N5-ingles-gerundio", _frag("The ", "operadora ", "is ", "issuing ", "the TISS guide", ".")),
)


# As 3 regressoes achadas pelo verificador em §Delta-2 G1 (defeitos reais que a rodada 2
# deixara passar): objeto coordenado com a guia, e clausula negada MASCARANDO um defeito real.
_SENTENCAS_G1_QUE_DEVEM_ACUSAR: tuple[tuple[str, str], ...] = (
    ("E4-objeto-coordenado", _frag("A ", "operadora ", "emite ", "a autorizacao e ", "a ", "guia", " TISS.")),
    (
        "E5-clausula-negada-mascarando",
        _frag("A ", "operadora ", "nao emite o parecer, mas ", "emite ", "a ", "guia", " TISS."),
    ),
    (
        "E6-lista-coordenada",
        _frag("A ", "operadora ", "emite ", "a autorizacao, o protocolo e tambem ", "a ", "guia", " TISS."),
    ),
)


def test_as_tres_regressoes_g1_do_gatekeeper_ficam_vermelhas() -> None:
    """G1: objeto coordenado com a guia e clausula negada que mascarava um defeito posterior."""
    perdidas = [nome for nome, frase in _SENTENCAS_G1_QUE_DEVEM_ACUSAR if not _hits_no_texto(frase)]
    assert perdidas == [], f"falsos negativos (regressao G1): {perdidas}"


# H1: termos de pagador em pt-BR que o lexico nao tinha. NOTA: "plano de saude" ocorre no proprio
# SYSTEM_PROMPT de Rafael ("...para um plano de saude brasileiro"), numa frase de voz CORRETA — a
# arvore real segue com 0 achados em 501 arquivos rastreados depois desta ampliacao.
_SENTENCAS_H1_QUE_DEVEM_ACUSAR: tuple[tuple[str, str], ...] = (
    ("H1a-plano-de-saude", _frag("O ", "plano de saude ", "emite ", "a ", "guia", " TISS.")),
    ("H1b-seguradora", _frag("A ", "seguradora ", "emite ", "a ", "guia", " TISS.")),
    ("H1c-convenio", _frag("O ", "convenio ", "emite ", "a ", "guia", " TISS.")),
    ("H1d-plural", _frag("Os ", "planos de saude ", "emitem ", "as ", "guias", " TISS.")),
    ("H1e-passiva", _frag("A ", "guia", " TISS e ", "emitida ", "pela ", "seguradora", ".")),
)


def test_os_termos_de_pagador_em_ptbr_ficam_vermelhos() -> None:
    """H1: plano de saude / seguradora / convenio sao o mesmo ator pagador que "operadora"."""
    perdidas = [nome for nome, frase in _SENTENCAS_H1_QUE_DEVEM_ACUSAR if not _hits_no_texto(frase)]
    assert perdidas == [], f"falsos negativos (lexico de pagador): {perdidas}"


def test_as_dez_frases_construidas_pelo_gatekeeper_ficam_vermelhas() -> None:
    """F4/D1: as 10 frases VERBATIM do verificador tem de acusar — todas as 10."""
    perdidas = [nome for nome, frase in _SENTENCAS_QUE_DEVEM_ACUSAR if not _hits_no_texto(frase)]
    assert perdidas == [], f"falsos negativos: {perdidas}"


def test_as_cinco_frases_novas_do_gatekeeper_ficam_vermelhas() -> None:
    """D3: as 5 sondas novas do §Delta, incluindo a passiva INGLESA que faltava."""
    perdidas = [nome for nome, frase in _SENTENCAS_NOVAS_QUE_DEVEM_ACUSAR if not _hits_no_texto(frase)]
    assert perdidas == [], f"falsos negativos: {perdidas}"


def test_o_conjunto_de_padroes_esta_pinado() -> None:
    """Um PR futuro nao pode remover um padrao em silencio (equivalente a estreitar a cerca)."""
    assert set(_PADROES) == {
        "P1-ptbr-ativo",
        "P2-en-ativo",
        "P3-ptbr-passivo",
        "P4-guia-passivo",
        "P5-ptbr-nominal",
        "P7-ptbr-nominal-posposto",
        "P8-en-passivo",
    }
    assert _PADRAO_ROTULO_BPMN.pattern


def test_o_limite_declarado_da_cerca_esta_pinado() -> None:
    """LIMITE DECLARADO (falsos NEGATIVOS): a classe residual e' MEDIDA aqui, nao afirmada."""
    residual = (
        _frag("A ", "operadora ", "libera ", "a ", "guia", " TISS."),
        _frag("A ", "operadora ", "disponibiliza ", "a ", "guia", " TISS."),
        _frag("A ", "operadora ", "e o elo final. ", "Emite ", "a ", "guia", " TISS."),
        _frag("A ", "operadora ", "emite ", "o documento de cobranca do prestador", "."),
        _frag("La ", "aseguradora ", "emite ", "la ", "guia", " TISS."),
        # CLASSE A (generativa): termo de pagador FORA do lexico enumerado `_PAGADOR`. Nao ha
        # ontologia de pagador aqui, so' uma lista; toda modalidade da ANS que ela nao nomeia e'
        # invisivel — por construcao, nao por acidente.
        _frag("A ", "autogestao ", "emite ", "a ", "guia", " TISS."),
        _frag("A ", "cooperativa medica ", "emite ", "a ", "guia", " TISS."),
        _frag("A ", "administradora de beneficios ", "emite ", "a ", "guia", " TISS."),
    )
    for frase in residual:
        assert _hits_no_texto(frase, "residual") == [], (
            "esta construcao passou a ser vista pela cerca — atualize o LIMITE DECLARADO do "
            f"docstring e este pino: {frase!r}"
        )


def test_os_falsos_positivos_conhecidos_estao_pinados() -> None:
    """LIMITE DECLARADO (falsos POSITIVOS): o que a cerca ainda acusa por engano, medido."""
    conhecidos = (
        ("negacao afastada do verbo", "A operadora nao e quem emite a guia TISS."),
        (
            "negacao separada do verbo por uma incisa",
            "A operadora jamais, em nenhuma hipotese, emite a guia TISS.",
        ),
        (
            "objeto emitido fora da lista enumerada",
            "O laudo e emitido pela operadora junto com a guia TISS.",
        ),
        # CLASSE B (generativa): o reconhecedor de sujeito-prestador e' de SUPERFICIE — casa uma
        # FORMA de sintagma adjacente ao verbo, nao analisa a frase. Dai as duas construcoes
        # abaixo, em que o prestador E' o sujeito real mas nao esta junto ao verbo.
        (
            "relativa com material interposto",
            "O prestador, que atende pela operadora, emite a guia TISS.",
        ),
        (
            "sujeito pronominal retomando o prestador",
            "A operadora informa ao prestador que ele emite a guia.",
        ),
    )
    for rotulo, frase in conhecidos:
        assert _hits_no_texto(frase, rotulo) != [], (
            "este falso positivo conhecido deixou de ocorrer — atualize o LIMITE DECLARADO do "
            f"docstring e este pino: {rotulo}"
        )
