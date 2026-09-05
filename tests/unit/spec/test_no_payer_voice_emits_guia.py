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
   escolhendo o que le (Tier A so' `spec/processes/*.bpmn|*.dmn`, Tier B so' nos YAML parseados) e
   declara, em texto, que NAO ha mecanismo de excecao — nem por arquivo nem por bloco `historico:`
   (ADR-0040 D7). CORRECAO DE UMA AFIRMACAO ANTERIOR DESTE MESMO ARQUIVO: a versao inicial desta
   cerca varria `docs/` inteiro e recortava aqueles dois arquivos por caminho, dizendo seguir "a
   mesma convencao" de `perspective`. Era o contrario: `perspective` RECUSA allowlist por arquivo
   por nome. O recorte foi removido, junto com a excecao implicita do proprio arquivo de teste.
   A secao "Where historical references go" de `perspective` tambem nomeia `docs/processes/` como
   lugar de narrativa; aqui `docs/processes/` E' varrido, porque a classe de defeito desta cerca e'
   vocabulario de contrato ("quem emite a guia"), e um contrato de processo e' artefato normativo,
   nao registro historico. A narrativa de remocao desta cadeia pertence ao ledger/review-queue.

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
* `P4-guia-passivo`   — "emitida pela operadora ... guia" (passiva com a guia POSPOSTA)
* `P5-ptbr-nominal`   — ator pagador ... "a emissao da guia" (forma nominal)
* `P6-bpmn-rotulo`    — SO' em atributos `name=` de `.bpmn`: emitir/gerar/expedir tendo a guia
  como OBJETO DIRETO ("Emitir autorizacao (guia TISS)"). Num BPMN de `spec/processes/` o sujeito
  de toda tarefa e' o processo da operadora: o ator pagador e' ESTRUTURAL, nao lexical, e por isso
  este padrao nao exige token de ator. "Emitir autorizacao (numero de autorizacao NA guia TISS)"
  nao casa, porque ali a guia e' locativo, nao objeto.

`ATOR PAGADOR` = operadora | processo | payer | health plan | `SP-OP-<CHAVE>-<NNN>`. O prestador
como sujeito e' a voz CORRETA e nunca deve acusar ("o prestador emite a guia", "guia emitida pelo
prestador"): nenhum padrao casa sem um token de ator pagador (ou, em `P6`, sem o contexto
estrutural do BPMN).

LIMITE DECLARADO (o que esta cerca NAO ve) — medido, nao estimado
------------------------------------------------------------------
`test_o_limite_declarado_da_cerca_esta_pinado` fixa a classe residual: parafrase sem os verbos
enumerados (p.ex. "libera"/"disponibiliza"), sujeito pagador a mais de 80 caracteres do verbo ou
separado dele por `.`/`;`, outros idiomas alem de pt-BR/ingles, e qualquer afirmacao semantica que
nao use a palavra "guia". Esta e' uma cerca LEXICAL: ela impede a REGRESSAO das formulacoes
conhecidas, nao substitui revisao. Uma cerca que declara e pina seu limite e' o mesmo formato que
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
_PAGADOR = r"(?:operadoras?|processos?|payer|health\s+plan|SP-OP-[A-Za-z]+-\d{3})"
_EMITIR = r"emit(?:e|em|es|ir|ida|idas|ido|idos|indo|iu|iram|ir[aá]|ir[aã]o)"
_SINONIMO = r"(?:ger(?:a|am|ar|ada|adas|ando)|exped(?:e|em|ir|ida|idas|indo)|produz(?:em|ir)?)"
_PASSIVO = r"(?:emitid|expedid|gerad)(?:a|as|o|os)\s+pel[ao]s?\s+" + _PAGADOR
_ARTIGO = r"(?:a|as|uma|umas)"
_GUIA = r"gui" + r"as?\b"
_GAP = r"[^.;]{0,80}?"
_PERTO = r"[^.;]{0,40}?"

_PADROES: dict[str, re.Pattern[str]] = {
    "P1-ptbr-ativo": re.compile(
        rf"{_PAGADOR}{_GAP}\b(?:{_EMITIR}|{_SINONIMO})\b{_PERTO}\b{_ARTIGO}\s+{_GUIA}",
        re.IGNORECASE,
    ),
    "P2-en-ativo": re.compile(
        rf"{_PAGADOR}{_GAP}\b(?:issues?|issued|issuing)\b{_PERTO}"
        rf"\b(?:the\s+)?(?:TISS\s+)?(?:guides?\b|{_GUIA})",
        re.IGNORECASE,
    ),
    "P3-ptbr-passivo": re.compile(rf"\b{_GUIA}{_GAP}\b{_PASSIVO}\b", re.IGNORECASE),
    "P4-guia-passivo": re.compile(rf"\b{_PASSIVO}{_PERTO}\b{_ARTIGO}\s+{_GUIA}", re.IGNORECASE),
    "P5-ptbr-nominal": re.compile(
        rf"{_PAGADOR}{_GAP}\b(?:emiss[aã]o|expedi[cç][aã]o)\s+d(?:a|as)\s+{_GUIA}",
        re.IGNORECASE,
    ),
}

# So' para atributos `name=` de arquivos .bpmn (ator pagador estrutural — ver docstring).
_PADRAO_ROTULO_BPMN = re.compile(
    rf"\b(?:{_EMITIR}|{_SINONIMO})\b[^.;]{{0,60}}?(?:\(\s*|\b{_ARTIGO}\s+){_GUIA}",
    re.IGNORECASE,
)
_BPMN_NAME_ATTR = re.compile(r'\bname="([^"]*)"')


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
    """Aplica o conjunto de padroes de prosa ao texto normalizado."""
    normalizado, linhas = _normalise(texto)
    achados: list[str] = []
    for nome, padrao in _PADROES.items():
        for m in padrao.finditer(normalizado):
            ini = linhas[m.start()] if m.start() < len(linhas) else 0
            fim = linhas[min(m.end(), len(linhas)) - 1] if linhas else 0
            onde = f"{ini}" if ini == fim else f"{ini}-{fim}"
            achados.append(f"{rotulo}:{onde}: [{nome}] {m.group().strip()}")
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


def test_a_enumeracao_e_hermetica_so_le_o_indice_do_git() -> None:
    """F1: a cerca so' enxerga arquivos RASTREADOS — nunca conteudo ignorado/local da maquina.

    Pino em duas partes: (a) o conjunto varrido e' exatamente o que `git ls-files` devolve para as
    raizes (nenhum arquivo a mais vindo do sistema de arquivos), e (b) um arquivo NAO rastreado
    plantado dentro de uma raiz varrida, com o defeito, continua invisivel.
    """
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

    plantado = _REPO / "spec" / "_persp_auth_voice_untracked_probe.md"
    assert not plantado.exists(), "arquivo de sonda ja existe — teste abortado por seguranca"
    antes = varrer()
    plantado.write_text(_frag("A ", "operadora ", "emite ", "a ", "guia", " TISS.\n"), "utf-8")
    try:
        ignorado = subprocess.run(
            ["git", "-C", str(_REPO), "ls-files", "--error-unmatch", str(plantado)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert ignorado.returncode != 0, "a sonda ficou rastreada; o teste nao provaria nada"
        assert _hits_no_texto(plantado.read_text("utf-8"), "sonda") != [], (
            "o texto da sonda TEM de casar a cerca — senao a parte (b) e' vacua"
        )
        assert plantado.resolve() not in {p.resolve() for p in _tracked_files()}
        assert varrer() == antes, "arquivo NAO rastreado entrou na varredura: cerca nao e' hermetica"
    finally:
        plantado.unlink()


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
    bpmn_pre = f'<bpmn:serviceTask id="ST_EmitirAutorizacaoAuto" name="{pre_fix}" />\n'
    bpmn_pos = f'<bpmn:serviceTask id="ST_EmitirAutorizacaoAuto" name="{pos_fix}" />\n'
    assert _hits_rotulos_bpmn(bpmn_pre, "pre") != [], "o rotulo pre-fix tem de acusar"
    assert _hits_rotulos_bpmn(bpmn_pos, "pos") == [], "o rotulo corrigido nao pode acusar"
    outros = [
        "Emitir autorizacao (decisao do auditor)",
        "Lote de guias TISS recebido do prestador",
        "Decidir destino da guia com pendencia expirada",
    ]
    for nome in outros:
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
    """F3(c): MUT-A/MUT-B — as duas formulacoes historicas reais ficam VERMELHAS."""
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


def test_a_cerca_nao_acusa_a_voz_correta_do_prestador() -> None:
    """0 falsos positivos nas formas PERMITIDAS (prestador como sujeito)."""
    permitidas = (
        _frag("o prestador ", "emite ", "a ", "guia", " TISS."),
        _frag("a ", "guia", " TISS e ", "emitida ", "pelo prestador", "."),
        "a operadora emite a autorizacao; o prestador emite a guia TISS.",
        "quando o prestador emite a guia, a operadora autoriza.",
        "o numero de autorizacao consta na guia TISS emitida pelo prestador.",
        "a operadora emite a autorizacao (numero de autorizacao na guia TISS).",
    )
    for frase in permitidas:
        assert _hits_no_texto(frase, "permitida") == [], frase


# As 10 frases construidas pelo gatekeeper (VERIFY-PERSP-AUTH-VOICE §F4), montadas por fragmentos.
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
        _frag("SP-OP-AUTH-001", "'s own automatic path ", "issues ", "the TISS guide", "."),
    ),
    ("A6-nominal", _frag("A ", "operadora", " responde pela ", "emissao ", "da ", "guia", " TISS.")),
    ("A7-sinonimo-gera", _frag("O ", "processo ", "da operadora ", "gera ", "a ", "guia", " TISS.")),
    ("A8-sinonimo-expede", _frag("A ", "operadora ", "expede ", "a ", "guia", " TISS.")),
    ("A9-futuro-acentuado", _frag("A ", "operadora ", "emitirá ", "a ", "guia", " TISS.")),
    (
        "A10-ator-distante",
        _frag(
            "A ",
            "operadora",
            ", no caminho automatico do contrato de autorizacao previa, ",
            "emite ",
            "a ",
            "guia",
            " TISS.",
        ),
    ),
)


def test_as_dez_frases_construidas_pelo_gatekeeper_ficam_vermelhas() -> None:
    """F4: >= 9/10 das frases de voz-de-prestador do VERIFY §F4 tem de acusar."""
    perdidas = [nome for nome, frase in _SENTENCAS_QUE_DEVEM_ACUSAR if not _hits_no_texto(frase)]
    acusadas = len(_SENTENCAS_QUE_DEVEM_ACUSAR) - len(perdidas)
    assert acusadas >= 9, f"apenas {acusadas}/10 acusadas; falsos negativos: {perdidas}"


def test_o_conjunto_de_padroes_esta_pinado() -> None:
    """Um PR futuro nao pode remover um padrao em silencio (equivalente a estreitar a cerca)."""
    assert set(_PADROES) == {
        "P1-ptbr-ativo",
        "P2-en-ativo",
        "P3-ptbr-passivo",
        "P4-guia-passivo",
        "P5-ptbr-nominal",
    }
    assert _PADRAO_ROTULO_BPMN.pattern


def test_o_limite_declarado_da_cerca_esta_pinado() -> None:
    """LIMITE DECLARADO: a classe residual e' MEDIDA aqui, nao afirmada na prosa.

    Estas construcoes NAO sao vistas pela cerca. O pino existe para que a lacuna seja um fato
    verificavel (e para que fechar qualquer uma delas quebre este teste e obrigue a atualizar a
    declaracao), nunca uma licenca para reintroduzir o defeito.
    """
    residual = (
        # verbo fora do conjunto enumerado
        _frag("A ", "operadora ", "libera ", "a ", "guia", " TISS."),
        _frag("A ", "operadora ", "disponibiliza ", "a ", "guia", " TISS."),
        # sujeito pagador separado do verbo por fim de frase
        _frag("A ", "operadora ", "e o elo final. ", "Emite ", "a ", "guia", " TISS."),
        # afirmacao semantica sem a palavra "guia"
        _frag("A ", "operadora ", "emite ", "o documento de cobranca do prestador", "."),
        # terceiro idioma
        _frag("La ", "aseguradora ", "emite ", "la ", "guia", " TISS."),
    )
    for frase in residual:
        assert _hits_no_texto(frase, "residual") == [], (
            "esta construcao passou a ser vista pela cerca — atualize o LIMITE DECLARADO do "
            f"docstring e este pino: {frase!r}"
        )
