"""E-SF-06r — suporte A5 do prospect, variante (a): estado fechado afirmado, nada ligado.

O que esta linha NÃO faz: não cria parâmetro de prazo do lead não convertido em `src/` (nem vazio,
nem com valor), não liga nada do caminho de suporte, não muda vocabulário de recusa e não toca
`src/`, `spec/` nem contrato. Enquanto o registro de admissão A5 não existir e a matriz de retenção
não for ratificada, o estado abaixo é o que vale, e qualquer desvio quebra um teste daqui.

Cada detector estático tem autoteste: a mesma função que varre o estado real é exercitada contra uma
entrada sintética violadora e tem de acusá-la. Guardas que já pinam partes deste estado noutros
arquivos são referenciadas, não duplicadas (`test_e_sf_06r_reused_guards_are_still_present`).

Caminho do prospect = conjunto DERIVADO, não lista à mão: todo `src/**/*.py` versionado cujo texto
cita prospect/lead ou cujo caminho é comercial/lead/prospect, mais `spec/agents/comercial/**` e todo
YAML de `spec/` que cita prospect. Nenhum deles cita a RN 623/2024 (norma de atendimento ao
BENEFICIÁRIO), salvo a exceção exata e justificada em `_RN_623_PERMITIDAS`.

Exceção documentada de retenção: `lead_custody.DEFAULT_TTL_DAYS = 30` é o TTL PROPOSTO da custódia
do telefone do lead (ADR-0061, sujeito ao DPO) — não é o prazo do lead não convertido. É o ÚNICO
valor de retenção/TTL ligado a lead que o detector aceita, e o valor fica pinado em 30.

RN 623 aplicada ao prospect: a maquinaria específica do prazo de resposta em dias úteis da
RN 623/2024 art. 12 é o `ComputeResponseDeadlineWorker` (`maezo.tools.workers.auth`, tópico
`operadora.auth.compute_response_deadline`) alimentado pela DMN `auth_sla`
(`prazo_resposta_dias_uteis`). Nenhum módulo do caminho do prospect a importa nem a nomeia
(`_usos_da_maquinaria_rn_623`). O calendário de dias úteis (`maezo.platform.calendario`) é
genérico — `campanha.py` e `vendor_suppression.py` o importam legitimamente — e NÃO é proibido;
GAP: um prazo de prospect calculado só com o calendário genérico não é distinguível estaticamente,
e a checagem comportamental disso segue na guarda reusada `test_prospect_no_sla_claim_before_q1`.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import re
import subprocess
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest
import yaml

from maezo.gateway.commercial_support_admission import A5_ADMISSION_ID, ADMISSION_RECORD_SCHEMA
from maezo.gateway.communications import lead_custody
from maezo.platform.lifecycle.legal_bases_matrix import LEAD_CATEGORIES

_REPO = Path(__file__).resolve().parents[3]
_TEMPLATE_RETENCAO = _REPO / "spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml"

#: Texto que coloca um módulo de `src/` no caminho do prospect.
_CITA_PROSPECT_OU_LEAD = re.compile(r"(?i)\bprospect|\blead_|\blead\b")
#: Caminho (relativo) que coloca um módulo de `src/` no caminho do prospect, cite ou não o termo.
_CAMINHO_COMERCIAL = re.compile(r"(?i)commercial|comercial|\blead|prospect|communications/recovery_")
#: YAML de `spec/` que entra no conjunto quando cita prospect.
_CITA_PROSPECT = re.compile(r"(?i)prospect")

#: Referência à norma de atendimento que não alcança o prospect (RN 623/2024), em qualquer grafia:
#: "RN 623", "RN nº 623", "RN n. 623", "RN-623", "Resolução Normativa nº 623", "623/2024".
_RN_623 = re.compile(
    r"(?i)\b(?:RN|resolu[çc][ãa]o\s+normativa)[\s._-]*(?:n[\s.ºo°]*)?[º°]?\s*623\b"
    r"|\b623[/_-]2024\b"
)

#: Citações da RN 623 permitidas no conjunto derivado: (arquivo, texto exato da linha sem margens).
#: `L0-core.yaml` entra no conjunto por citar o PROSPECT noutra linha (`send_prospect_message`);
#: a linha abaixo é a de `authorization_denial` — negativa de cobertura ao BENEFICIÁRIO (item `hard`,
#: médico auditor), sem relação com o prospect.
_RN_623_PERMITIDAS: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "spec/policies/autonomy/L0-core.yaml",
            "authorization_denial:         { level: L0, hard: true }   # negativa: medico auditor "
            "(ADR-0005; RN 259 revogada, errata ADR-0073 §B: Lei 12.842 art. 5 II, RN 424, "
            "RN 623 arts. 14/16, LGPD art. 20)",
        ),
    }
)

#: Maquinaria específica do prazo de resposta da RN 623/2024 art. 12 (ver docstring do módulo).
_MODULOS_RN_623: frozenset[str] = frozenset({"maezo.tools.workers.auth"})
_NOMES_RN_623: frozenset[str] = frozenset(
    {
        "ComputeResponseDeadlineWorker",
        "COMPUTE_RESPONSE_DEADLINE_TOPIC",
        "COMPUTE_RESPONSE_DEADLINE_VARIABLES",
    }
)
_TEXTOS_RN_623: tuple[str, ...] = (
    "compute_response_deadline",
    "ComputeResponseDeadline",
    "prazo_resposta_dias_uteis",
    "prazo_resposta_duracao_util",
    "prazo_resposta_vencimento_util",
    "auth_sla",
)

#: Tokens (de identificador) que tornam um nome ligado a lead/prospect.
_TOKENS_DE_LEAD: frozenset[str] = frozenset(
    {"lead", "leads", "prospect", "prospects", "prospecto", "prospectos", "unconverted"}
)
_PARES_DE_LEAD: frozenset[tuple[str, str]] = frozenset(
    {("nao", "convertido"), ("nao", "convertidos"), ("not", "converted")}
)
#: Tokens que tornam um nome um prazo/retenção.
_TOKENS_DE_PRAZO: frozenset[str] = frozenset(
    {"prazo", "prazos", "ttl", "dias", "days", "meses", "months", "anos", "years"}
)
_PREFIXOS_DE_PRAZO: tuple[str, ...] = ("reten", "expir")

#: Únicos valores de retenção/TTL ligados a lead aceitos em `src/`: (módulo, nome).
_PRAZOS_DE_LEAD_PERMITIDOS: frozenset[tuple[str, str]] = frozenset(
    {("src/maezo/gateway/communications/lead_custody.py", "DEFAULT_TTL_DAYS")}
)

#: Palavras numéricas/de unidade que não podem aparecer em campo de linha de lead do template.
_PALAVRAS_NUMERICAS: frozenset[str] = frozenset(
    [
        "um",
        "uma",
        "dois",
        "duas",
        "tres",
        "três",
        "quatro",
        "cinco",
        "seis",
        "sete",
        "oito",
        "nove",
        "dez",
        "onze",
        "doze",
        "treze",
        "quatorze",
        "catorze",
        "quinze",
        "dezesseis",
        "dezessete",
        "dezoito",
        "dezenove",
        "vinte",
        "trinta",
        "quarenta",
        "cinquenta",
        "sessenta",
        "setenta",
        "oitenta",
        "noventa",
        "cem",
        "cento",
        "mil",
        "dia",
        "dias",
        "mes",
        "mês",
        "meses",
        "ano",
        "anos",
        "semana",
        "semanas",
    ]
)
_CHAVES_DA_LINHA_DE_LEAD: frozenset[str] = frozenset({"categoria", "base_legal", "retencao", "acao"})

#: Teto de tamanho na varredura de registros A5 (arquivo maior é ignorado).
_TETO_DE_BYTES = 2 * 1024 * 1024


# --------------------------------------------------------------------------- utilidades


def _versionados(*padroes: str, raiz: Path = _REPO) -> list[str]:
    saida = subprocess.run(
        ["git", "ls-files", "-z", "--", *padroes],
        cwd=raiz,
        check=True,
        capture_output=True,
    ).stdout
    return [p for p in saida.decode("utf-8").split("\0") if p]


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8")


def _tokens(nome: str) -> list[str]:
    """Tokens de identificador: separa em `_`/não alfanumérico e em fronteira de caixa (camelCase)."""
    partes = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", nome)
    return [t for t in re.split(r"[^0-9A-Za-z]+", partes.lower()) if t]


def _e_de_lead(tokens: list[str]) -> bool:
    return bool(_TOKENS_DE_LEAD.intersection(tokens)) or any(
        par in _PARES_DE_LEAD for par in zip(tokens, tokens[1:], strict=False)
    )


def _e_de_prazo(tokens: list[str]) -> bool:
    return any(t in _TOKENS_DE_PRAZO or t.startswith(_PREFIXOS_DE_PRAZO) for t in tokens)


# --------------------------------------------------------------------------- detectores


@functools.cache
def _caminho_do_prospect(raiz: Path = _REPO) -> tuple[str, ...]:
    """Conjunto derivado do caminho do prospect (relativos à raiz, ordenados; lido uma vez)."""
    achados: set[str] = set()
    for relativo in _versionados("src/*.py", raiz=raiz):
        caminho = raiz / relativo
        if not caminho.is_file():
            continue
        if _CAMINHO_COMERCIAL.search(relativo) or _CITA_PROSPECT_OU_LEAD.search(_ler(caminho)):
            achados.add(relativo)
    achados.update(p for p in _versionados("spec/agents/comercial/*", raiz=raiz) if (raiz / p).is_file())
    for relativo in _versionados("spec/*.yaml", "spec/*.yml", raiz=raiz):
        caminho = raiz / relativo
        if caminho.is_file() and _CITA_PROSPECT.search(_ler(caminho)):
            achados.add(relativo)
    return tuple(sorted(achados))


def _citacoes_rn_623(
    raiz: Path,
    relativos: Iterable[str],
    permitidas: frozenset[tuple[str, str]] = frozenset(),
) -> list[str]:
    problemas: list[str] = []
    for relativo in relativos:
        for numero, linha in enumerate(_ler(raiz / relativo).splitlines(), start=1):
            if _RN_623.search(linha) and (relativo, linha.strip()) not in permitidas:
                problemas.append(f"{relativo}:{numero}")
    return problemas


def _usos_da_maquinaria_rn_623(fonte: str, python: bool = True) -> list[str]:
    """Importações/menções da maquinaria do prazo de resposta da RN 623 numa fonte."""
    achados = [f"texto:{t}" for t in _TEXTOS_RN_623 if t in fonte]
    if not python:
        return achados
    for no in ast.walk(ast.parse(fonte)):
        if isinstance(no, ast.Import):
            achados.extend(f"import:{a.name}" for a in no.names if a.name in _MODULOS_RN_623)
        elif isinstance(no, ast.ImportFrom) and no.module:
            if no.module in _MODULOS_RN_623:
                achados.append(f"import:{no.module}")
            for alias in no.names:
                if f"{no.module}.{alias.name}" in _MODULOS_RN_623 or alias.name in _NOMES_RN_623:
                    achados.append(f"import:{no.module}.{alias.name}")
    return achados


def _nomes_de_alvo(alvo: ast.expr) -> list[str]:
    if isinstance(alvo, ast.Name):
        return [alvo.id]
    if isinstance(alvo, ast.Attribute):
        return [alvo.attr]
    if isinstance(alvo, ast.Starred):
        return _nomes_de_alvo(alvo.value)
    if isinstance(alvo, ast.Tuple | ast.List):
        return [n for e in alvo.elts for n in _nomes_de_alvo(e)]
    return []


def _e_numero(valor: ast.expr | None) -> bool:
    if isinstance(valor, ast.UnaryOp) and isinstance(valor.op, ast.USub | ast.UAdd):
        valor = valor.operand
    return (
        isinstance(valor, ast.Constant)
        and isinstance(valor.value, int | float)
        and not isinstance(valor.value, bool)
    )


def _e_timedelta(valor: ast.expr | None) -> bool:
    return isinstance(valor, ast.Call) and ast.unparse(valor.func).split(".")[-1] == "timedelta"


def _chave_de_lead(chave: ast.expr | None) -> str | None:
    if not (isinstance(chave, ast.Constant) and isinstance(chave.value, str)):
        return None
    lead = chave.value in LEAD_CATEGORIES or _e_de_lead(_tokens(chave.value))
    return chave.value if lead else None


def _carrega_valor(valor: ast.expr | None) -> bool:
    """Número literal, ou `timedelta(...)` com número literal entre os argumentos."""
    if _e_numero(valor):
        return True
    if isinstance(valor, ast.Call) and _e_timedelta(valor):
        return any(_e_numero(a) for a in [*valor.args, *(k.value for k in valor.keywords)])
    return False


def _prazos_do_lead(fonte: str, modulo: str = "") -> list[str]:
    """Prazos/retenções ligados a lead em toda a AST (módulo, classes, defaults, alvos compostos).

    Conta: nome de lead E de prazo (qualquer valor, até sem valor); nome de prazo que recebe valor
    literal num módulo cujo caminho é de lead/prospect; `timedelta(...)` atribuído a alvo de lead;
    dict literal `{"<categoria ou chave de lead>": <número ou timedelta>}` (e `dict(chave=número)`).
    """
    contexto_de_lead = _e_de_lead(_tokens(modulo))

    def acusa(nome: str, valor: ast.expr | None) -> bool:
        tokens = _tokens(nome)
        if _e_de_lead(tokens):
            return _e_de_prazo(tokens) or _e_timedelta(valor)
        return contexto_de_lead and _e_de_prazo(tokens) and _carrega_valor(valor)

    achados: list[str] = []
    for no in ast.walk(ast.parse(fonte)):
        ligacoes: list[tuple[str, ast.expr | None]] = []
        if isinstance(no, ast.Assign):
            ligacoes = [(n, no.value) for alvo in no.targets for n in _nomes_de_alvo(alvo)]
        elif isinstance(no, ast.AnnAssign | ast.AugAssign):
            ligacoes = [(n, no.value) for n in _nomes_de_alvo(no.target)]
        elif isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            posicionais = [*no.args.posonlyargs, *no.args.args]
            com_default = posicionais[len(posicionais) - len(no.args.defaults) :]
            ligacoes = [(a.arg, d) for a, d in zip(com_default, no.args.defaults, strict=True)]
            ligacoes += [
                (a.arg, d)
                for a, d in zip(no.args.kwonlyargs, no.args.kw_defaults, strict=True)
                if d is not None
            ]
        elif isinstance(no, ast.Dict):
            for chave, item in zip(no.keys, no.values, strict=True):
                rotulo = _chave_de_lead(chave)
                if rotulo is not None and (_e_numero(item) or _e_timedelta(item)):
                    achados.append(f"dict[{rotulo!r}]")
        elif isinstance(no, ast.Call) and ast.unparse(no.func) == "dict":
            for kw in no.keywords:
                if kw.arg and _chave_de_lead(ast.Constant(kw.arg)) and _e_numero(kw.value):
                    achados.append(f"dict[{kw.arg!r}]")
        achados.extend(nome for nome, valor in ligacoes if acusa(nome, valor))
    return achados


def _prazos_do_lead_em_src(raiz: Path = _REPO) -> set[tuple[str, str]]:
    return {
        (relativo, nome)
        for relativo in _versionados("src/*.py", raiz=raiz)
        if (raiz / relativo).is_file()
        for nome in _prazos_do_lead(_ler(raiz / relativo), relativo)
    }


def _linha_de_lead(linha: Mapping[str, object]) -> bool:
    categoria = str(linha.get("categoria", ""))
    sufixo = categoria.removeprefix("PLACEHOLDER-CATEGORIA-")
    return sufixo in LEAD_CATEGORIES or _e_de_lead(_tokens(sufixo))


def _campo_com_valor(texto: str) -> bool:
    palavras = {p.lower() for p in re.split(r"[\W_]+", texto) if p}
    return (
        not texto.startswith("PLACEHOLDER-")
        or bool(re.search(r"\d", texto))
        or bool(palavras & _PALAVRAS_NUMERICAS)
    )


def _linhas_de_lead_com_valor(linhas: Iterable[Mapping[str, object]]) -> list[str]:
    """Linhas de lead com chave fora do formato ou campo (salvo `categoria`) que não é marcador vazio."""
    problemas: list[str] = []
    for linha in linhas:
        if not _linha_de_lead(linha):
            continue
        categoria = str(linha.get("categoria", ""))
        if set(linha) != _CHAVES_DA_LINHA_DE_LEAD:
            problemas.append(f"{categoria}:chaves")
        for chave, valor in linha.items():
            if chave != "categoria" and _campo_com_valor(str(valor)):
                problemas.append(f"{categoria}:{chave}")
    return problemas


def _linhas_do_template(texto: str) -> list[Mapping[str, object]]:
    dados = yaml.safe_load(texto)
    encontradas: list[Mapping[str, object]] = []

    def visitar(no: object) -> None:
        if isinstance(no, dict):
            if "categoria" in no:
                encontradas.append(no)
            for valor in no.values():
                visitar(valor)
        elif isinstance(no, list):
            for item in no:
                visitar(item)

    visitar(dados)
    return encontradas


def _contem_registro_a5(objeto: object) -> bool:
    if isinstance(objeto, dict):
        if objeto.get("admission_id") == A5_ADMISSION_ID:
            return True
        return any(_contem_registro_a5(v) for v in objeto.values())
    if isinstance(objeto, list):
        return any(_contem_registro_a5(v) for v in objeto)
    return False


def _registros_a5_em(caminhos: Iterable[Path]) -> list[Path]:
    """Arquivos que são JSON (qualquer extensão, com ou sem BOM) contendo `admission_id == "A5"`."""
    achados: list[Path] = []
    for caminho in caminhos:
        try:
            if not caminho.is_file() or os.path.getsize(caminho) > _TETO_DE_BYTES:
                continue
            dados = caminho.read_bytes()
        except OSError:
            continue
        if b"\0" in dados:
            continue
        # Registro A5 é objeto: topo JSON tem de ser `{` ou `[` (após BOM/espaços).
        if dados.removeprefix(b"\xef\xbb\xbf").lstrip()[:1] not in (b"{", b"["):
            continue
        try:
            objeto = json.loads(dados)
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        if _contem_registro_a5(objeto):
            achados.append(caminho)
    return achados


# --------------------------------------------------------------------------- autotestes


def test_detector_de_rn_623_acusa_citacao(tmp_path: Path) -> None:
    (tmp_path / "v.py").write_text(
        'X = "prazo da RN 623"\nY = "RN nº 623/2024"\nZ = "rn-623-2024.md"\n'
        'A = "Resolução Normativa nº 623"\nB = "Resolucao Normativa n. 623"\nC = "RN-623"\n'
        'D = "RN n.º 623"\nE = "RN623"\n',
        encoding="utf-8",
    )
    (tmp_path / "l.py").write_text(
        'A = "RN 413"\nB = 16230\nC = "ARN 623"\nD = "RN 6230"\n', encoding="utf-8"
    )
    assert _citacoes_rn_623(tmp_path, ["v.py"]) == [f"v.py:{n}" for n in range(1, 9)]
    assert _citacoes_rn_623(tmp_path, ["l.py"]) == []
    permitida = frozenset({("v.py", 'X = "prazo da RN 623"')})
    assert _citacoes_rn_623(tmp_path, ["v.py"], permitida)[0] == "v.py:2"


def test_conjunto_derivado_acusa_modulo_novo_do_prospect(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    arquivos = {
        "src/pkg/novo.py": "# atende o prospect\n",
        "src/pkg/lead_x.py": "X = 1\n",
        "src/pkg/neutro.py": "LEADER = 'misleading'\n",
        "spec/agents/comercial/prompt.md": "texto\n",
        "spec/outra/p.yaml": "alvo: prospect\n",
        "spec/outra/q.yaml": "alvo: beneficiario\n",
    }
    for relativo, texto in arquivos.items():
        (tmp_path / relativo).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relativo).write_text(texto, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    assert _caminho_do_prospect(tmp_path) == (
        "spec/agents/comercial/prompt.md",
        "spec/outra/p.yaml",
        "src/pkg/lead_x.py",
        "src/pkg/novo.py",
    )


def test_detector_de_maquinaria_rn_623_acusa_importacao() -> None:
    fonte = (
        "from maezo.tools.workers.auth import ComputeResponseDeadlineWorker\n"
        "from maezo.tools.workers import auth\n"
        "import maezo.tools.workers.auth\n"
        "def f():\n    from x import COMPUTE_RESPONSE_DEADLINE_TOPIC\n"
        "T = 'operadora.auth.compute_response_deadline'\n"
    )
    assert _usos_da_maquinaria_rn_623(fonte) == [
        "texto:compute_response_deadline",
        "texto:ComputeResponseDeadline",
        "import:maezo.tools.workers.auth",
        "import:maezo.tools.workers.auth.ComputeResponseDeadlineWorker",
        "import:maezo.tools.workers.auth",
        "import:maezo.tools.workers.auth",
        "import:x.COMPUTE_RESPONSE_DEADLINE_TOPIC",
    ]
    generico = "from maezo.platform.calendario import eh_dia_util, vencimento_em_dias_uteis\n"
    assert _usos_da_maquinaria_rn_623(generico) == []
    assert _usos_da_maquinaria_rn_623("dmn: auth_sla\n", python=False) == ["texto:auth_sla"]


def test_detector_de_prazo_do_lead_acusa_cada_forma() -> None:
    fonte = (
        "PRAZO_LEAD_NAO_CONVERTIDO = None\n"
        "LEAD_RETENTION_DAYS: int = 90\n"
        "DIAS_DE_GUARDA_DO_PROSPECT = 1\n"
        "class C:\n    LEAD_RETENTION_DAYS = 90\n"
        "def f(x, lead_ttl=30, *, prospect_days=5): ...\n"
        "def g():\n    leadExpiry = 1\n"
        "A, (B, PRAZO_NAO_CONVERTIDO) = 1, (2, 3)\n"
        "obj.lead_ttl_dias = 7\n"
        "M = {'lead_intake': 90}\n"
        "N = {'prospect_x': timedelta(days=1)}\n"
        "O = dict(supressao_lead=-1)\n"
        "LEAD_JANELA = dt.timedelta(days=3)\n"
    )
    assert _prazos_do_lead(fonte) == [
        "PRAZO_LEAD_NAO_CONVERTIDO",
        "LEAD_RETENTION_DAYS",
        "DIAS_DE_GUARDA_DO_PROSPECT",
        "lead_ttl",
        "prospect_days",
        "PRAZO_NAO_CONVERTIDO",
        "lead_ttl_dias",
        "LEAD_JANELA",
        "LEAD_RETENTION_DAYS",
        "leadExpiry",
        "dict['lead_intake']",
        "dict['prospect_x']",
        "dict['supressao_lead']",
    ]
    limpa = (
        "LEAD_CATEGORIES = ()\n"
        "GUARDA_DE_PROSPECT_NO_ACESSO = False\n"
        "LEADER_TTL_DAYS = 3\n"
        "MISLEADING_PRAZO = 1\n"
        "RETENCAO_BENEFICIARIO_DIAS = 30\n"
        "M = {'lead_intake': 'PLACEHOLDER', 'beneficiario': 90, 'leader': 1}\n"
        "def f(lead_ref='x', prazo=1): ...\n"
        "JANELA = timedelta(days=3)\n"
    )
    assert _prazos_do_lead(limpa) == []
    contexto = (
        "DEFAULT_TTL_DAYS = 30\nROTULO = 'x'\n"
        "class R:\n    expires_at: datetime\n"
        "def f(ttl: timedelta = timedelta(days=DEFAULT_TTL_DAYS), ttl_days: int = DEFAULT_TTL_DAYS): ...\n"
        "def g(retencao_dias: int = 90, janela=timedelta(days=7)): ...\n"
    )
    assert _prazos_do_lead(contexto, "src/a/lead_custody.py") == ["DEFAULT_TTL_DAYS", "retencao_dias"]
    assert _prazos_do_lead(contexto, "src/a/leader_custody.py") == []


def test_detector_de_template_acusa_valor_ou_formato() -> None:
    marcador = "PLACEHOLDER-X-jurídico-must-confirm"
    vazia = {"base_legal": marcador, "retencao": marcador, "acao": marcador}

    def linha(sufixo: str, **campos: str) -> dict[str, str]:
        return {"categoria": f"PLACEHOLDER-CATEGORIA-{sufixo}", **vazia, **campos}

    linhas = [
        linha("lead_intake"),
        linha("lead_cotacao_jornada", retencao="90 dias"),
        linha("supressao_lead", retencao="PLACEHOLDER-90"),
        linha("lead_permissao_contato", acao="apagar em 90 dias"),
        linha("lead_registro_atendimento", acao="PLACEHOLDER-trinta"),
        linha("lead_dado_saude_escapado", base_legal="PLACEHOLDER-um-ano"),
        linha("lead_intake", prazo=marcador),
        {"categoria": "PLACEHOLDER-CATEGORIA-lead_intake", "base_legal": marcador, "retencao": marcador},
        linha("evidencia_fraude", retencao="5 anos"),
    ]
    assert _linhas_de_lead_com_valor(linhas) == [
        "PLACEHOLDER-CATEGORIA-lead_cotacao_jornada:retencao",
        "PLACEHOLDER-CATEGORIA-supressao_lead:retencao",
        "PLACEHOLDER-CATEGORIA-lead_permissao_contato:acao",
        "PLACEHOLDER-CATEGORIA-lead_registro_atendimento:acao",
        "PLACEHOLDER-CATEGORIA-lead_dado_saude_escapado:base_legal",
        "PLACEHOLDER-CATEGORIA-lead_intake:chaves",
        "PLACEHOLDER-CATEGORIA-lead_intake:chaves",
    ]


def test_detector_de_registro_a5_acusa_arquivo_violador(tmp_path: Path) -> None:
    registro = {"schema": ADMISSION_RECORD_SCHEMA, "admission_id": A5_ADMISSION_ID}
    aninhado = tmp_path / "a.json"
    aninhado.write_text(json.dumps({"x": [registro]}), encoding="utf-8")
    extensao = tmp_path / "A5.record"
    extensao.write_text(json.dumps(registro), encoding="utf-8")
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(registro).encode("utf-8"))
    limpo = tmp_path / "b.json"
    limpo.write_text(json.dumps({"a_id": A5_ADMISSION_ID, "admission_id": "A6"}), encoding="utf-8")
    ilegivel = tmp_path / "c.json"
    ilegivel.write_text("{", encoding="utf-8")
    binario = tmp_path / "d.bin"
    binario.write_bytes(json.dumps(registro).encode("utf-8") + b"\0")
    grande = tmp_path / "e.json"
    grande.write_text(json.dumps([registro, "x" * _TETO_DE_BYTES]), encoding="utf-8")
    ausente = tmp_path / "nao_existe.json"
    caminhos = [aninhado, extensao, bom, limpo, ilegivel, binario, grande, ausente]
    assert _registros_a5_em(caminhos) == [aninhado, extensao, bom]


# --------------------------------------------------------------------------- estado fechado


def test_caminho_do_prospect_existe_na_varredura() -> None:
    nomes = {Path(p).name for p in _caminho_do_prospect()}
    assert {
        "rota_comercial.py",
        "commercial_admission.py",
        "commercial_support_admission.py",
        "lead_custody.py",
        "recovery_eligibility.py",
        "recovery_dispatch.py",
        "agent.yaml",
    } <= nomes


def test_caminho_do_prospect_nao_cita_rn_623() -> None:
    assert _citacoes_rn_623(_REPO, _caminho_do_prospect(), _RN_623_PERMITIDAS) == []


def test_excecoes_de_rn_623_seguem_existindo() -> None:
    # Exceção que perdeu o alvo é exceção morta: some daqui junto com a linha.
    for relativo, linha in _RN_623_PERMITIDAS:
        assert relativo in _caminho_do_prospect()
        assert linha in {t.strip() for t in _ler(_REPO / relativo).splitlines()}


def test_caminho_do_prospect_nao_usa_prazo_de_resposta_da_rn_623() -> None:
    achados = {
        relativo: usos
        for relativo in _caminho_do_prospect()
        if (usos := _usos_da_maquinaria_rn_623(_ler(_REPO / relativo), relativo.endswith(".py")))
    }
    assert achados == {}


def test_nenhum_prazo_do_lead_nao_convertido_em_src() -> None:
    assert _versionados("src/*.py"), "git ls-files não listou src/"
    assert _prazos_do_lead_em_src() == _PRAZOS_DE_LEAD_PERMITIDOS


def test_ttl_da_custodia_do_lead_e_a_excecao_proposta_de_30_dias() -> None:
    assert lead_custody.DEFAULT_TTL_DAYS == 30
    fonte = _ler(_REPO / "src/maezo/gateway/communications/lead_custody.py")
    assert "DEFAULT_TTL_DAYS: Final[int] = 30\n" in fonte


def test_template_de_retencao_sem_prazo_para_lead() -> None:
    linhas = _linhas_do_template(_ler(_TEMPLATE_RETENCAO))
    categorias = {str(linha["categoria"]) for linha in linhas}
    for categoria in LEAD_CATEGORIES:
        assert f"PLACEHOLDER-CATEGORIA-{categoria}" in categorias
    assert sum(_linha_de_lead(linha) for linha in linhas) == len(LEAD_CATEGORIES)
    assert _linhas_de_lead_com_valor(linhas) == []


def test_nenhum_registro_de_admissao_a5_versionado() -> None:
    arquivos = [_REPO / p for p in _versionados()]
    assert arquivos, "git ls-files não listou nada"
    inicio = time.monotonic()
    assert _registros_a5_em(arquivos) == []
    assert time.monotonic() - inicio < 10.0


# --------------------------------------------------------------------------- reuso

#: Guardas que já pinam partes deste estado (arquivo -> nomes). Referenciadas, não duplicadas.
_REUSED: Mapping[str, frozenset[str]] = {
    "tests/unit/gateway/test_esf06_commercial_support_protocol.py": frozenset(
        {
            "test_protocol_ref_exists_only_with_op07_receipt",
            "test_prospect_contact_protocol_first_is_gap_at_c64_pin",
            "test_prospect_no_sla_claim_before_q1",
        }
    ),
    "tests/unit/gateway/test_esf06_refusal_vocabulary.py": frozenset(
        {
            "test_refusal_vocabulary_closed_and_non_clinical",
            "test_refusal_enums_have_no_health_age_disability_tokens",
            "test_admission_refusal_literals_have_no_health_age_disability_tokens",
        }
    ),
    "tests/unit/gateway/test_esf06_refusal_autonomy.py": frozenset(
        {"test_merit_refusal_requires_human_review"}
    ),
    "tests/unit/test_commercial_admission_a5.py": frozenset(
        {
            "test_default_and_support_only_compositions_never_bind",
            "test_without_a5_no_support_or_work_command_reaches_the_source",
        }
    ),
    "tests/unit/platform/test_legal_bases_matrix.py": frozenset(
        {
            "test_intake_refused_without_ratified_retention_matrix",
            "test_template_lists_the_six_lead_categories_as_placeholders",
        }
    ),
}

_MARCA_QUE_DESLIGA = re.compile(r"\bmark\.(?:skip|skipif|xfail)\b")
_CHAMADAS_QUE_DESLIGAM: frozenset[str] = frozenset(
    {"pytest.skip", "pytest.xfail", "pytest.importorskip", "skip", "xfail", "importorskip"}
)


def _desligada(funcao: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorador in funcao.decorator_list:
        if _MARCA_QUE_DESLIGA.search(ast.unparse(decorador)):
            return True
    return any(
        isinstance(no, ast.Call) and ast.unparse(no.func) in _CHAMADAS_QUE_DESLIGAM for no in ast.walk(funcao)
    )


def _modulo_desligado(arvore: ast.Module) -> bool:
    for no in arvore.body:
        alvos = (
            no.targets if isinstance(no, ast.Assign) else [no.target] if isinstance(no, ast.AnnAssign) else []
        )
        if any(isinstance(a, ast.Name) and a.id == "pytestmark" for a in alvos):
            valor = no.value if isinstance(no, ast.Assign | ast.AnnAssign) else None
            if valor is not None and _MARCA_QUE_DESLIGA.search(ast.unparse(valor)):
                return True
    return False


def _testes_ativos(caminho: Path) -> frozenset[str]:
    arvore = ast.parse(_ler(caminho))
    if _modulo_desligado(arvore):
        return frozenset()
    return frozenset(
        n.name
        for n in arvore.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and n.name.startswith("test_")
        and not _desligada(n)
    )


def test_detector_de_reuso_acusa_guarda_ausente_ou_desligada(tmp_path: Path) -> None:
    arquivo = tmp_path / "t.py"
    arquivo.write_text(
        "import pytest\n"
        "pytestmark = [pytest.mark.integration]\n"
        "def test_a() -> None:\n    def test_aninhado() -> None: ...\n"
        "@pytest.mark.skip(reason='x')\ndef test_b() -> None: ...\n"
        "@pytest.mark.xfail\nasync def test_c() -> None: ...\n"
        "@pytest.mark.parametrize('v', [1])\ndef test_d(v: int) -> None: ...\n"
        "def test_e() -> None:\n    if True:\n        pytest.skip('x')\n"
        "def test_f() -> None:\n    pytest.xfail('x')\n",
        encoding="utf-8",
    )
    assert _testes_ativos(arquivo) == {"test_a", "test_d"}
    for marca in (
        "pytestmark = pytest.mark.skip(reason='x')",
        "pytestmark = [pytest.mark.integration, pytest.mark.skipif(True, reason='x')]",
        "pytestmark: list[object] = [pytest.mark.xfail]",
    ):
        desligado = tmp_path / "m.py"
        desligado.write_text(f"import pytest\n{marca}\ndef test_a() -> None: ...\n", encoding="utf-8")
        assert _testes_ativos(desligado) == frozenset(), marca


@pytest.mark.parametrize("relativo", sorted(_REUSED))
def test_e_sf_06r_reused_guards_are_still_present(relativo: str) -> None:
    faltando = _REUSED[relativo] - _testes_ativos(_REPO / relativo)
    assert not faltando, f"{relativo}: guardas reusadas sumiram ou foram desligadas: {sorted(faltando)}"
