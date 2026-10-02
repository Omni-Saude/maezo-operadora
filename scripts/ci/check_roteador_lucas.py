#!/usr/bin/env python3
"""Cerca de CI: o roteador Helena -> Lucas do numero unico so' liga em dev, e so' por um `if`.

Por que esta cerca existe
--------------------------
`MAEZO_ROTEADOR_LUCAS` (ADR-0062, plano `docs/plans/lucas-numero-unico.md` §4) liga o roteamento
por conversa no receptor do WhatsApp: com ele ligado, a conversa pode sair da Helena e ir para o
Lucas, que atende cobranca com uma fonte de fatos SIMULADA (`MAEZO_LUCAS_FONTE_COBRANCA`). Isso e'
aceitavel em `dev-sa-east-1`, com o time testando, e em nenhum outro lugar enquanto nao existir
fonte real, DMN aprovada e DPA do provedor do Lucas. A decisao de onde ligar nao pode depender de
alguem lembrar: e' isto que a cerca mecaniza.

O que ela reprova (itens 1-7 de §4; 6-7 desde a onda (f))
---------------------------------------------------------
  1. `MAEZO_ROTEADOR_LUCAS` ligado, ou `MAEZO_LUCAS_FONTE_COBRANCA` presente, em qualquer
     `deploy/**` que nao seja `dev-sa-east-1`.
  2. Qualquer um dos tres nomes (os dois acima e `MAEZO_LUCAS_INATIVIDADE_MINUTOS`) entregue por
     `valueFrom`, ou com valor que o analisador nao consegue resolver, em QUALQUER ambiente.
  3. Qualquer um dos tres declarado fora do Terraform que esta cerca analisa (Helm, compose, k8s),
     ou mencionado num `.tf` em forma que nao vira entrada de `environment`.
  4. O default de `WhatsAppWebhookSettings.roteador_lucas_enabled` diferente do LITERAL `False`
     (direto ou como `Field(default=False, ...)`).
  5. Construcao de `ConversaRouter` ou `LucasTurno` (inclusive por alias de import ou atributo de
     modulo) em qualquer arquivo de `src/maezo/` fora do CORPO de um
     `if <algo>.roteador_lucas_enabled:` — nem no `else`, nem sob um `if` negado ou de outro nome.
  6. Um handoff para o Lucas construido fora do no' `handoff_cobranca` de
     `agents/helena/graph.py` — `HandoffCobranca(...)`, `x: HandoffCobranca = ...`,
     `cast(HandoffCobranca, ...)` ou um `dict` com as chaves de um handoff. Esse no' so' e'
     alcancado depois de `_handoff_recusado` aprovar (classify + lexicos de saude e de pessoa).
  7. `LucasTurno.executar` chamado fora de `HelenaDispatcher._turno_do_lucas`, ou esse metodo
     chamado fora do `dispatch`, mais de uma vez, ANTES dos lexicos (`self._pre_rotear`) ou do
     `ainvoke` da Helena, fora do `if` que le' `result["handoff"]`, ou entregando outro handoff
     que nao `result["handoff"]`. A `ConversaDoTurno` tambem so' nasce em `_turno_do_lucas`, e o
     estado da Helena tem de receber `sinal_saude_lexico` derivado de `sinais`.

O valor EFETIVO, e nao so' o default (onda g)
---------------------------------------------
O receptor declara os tres como `tostring(var.x)` / `var.x`, e o valor de dev mora em
`lucas.auto.tfvars`. O resolvedor compartilhado so' conhece string literal, `local.x` e o
`default` de `var.x`: sozinho, ele reprovaria o `tostring()` (item 2) e, pior, leria o default
`false` de um diretorio cujo `*.auto.tfvars` diz `true`. Por isso esta cerca resolve as entradas
VIGIADAS com o que o Terraform carrega sozinho em cada diretorio de ambiente: `terraform.tfvars`,
`terraform.tfvars.json`, `*.auto.tfvars` e `*.auto.tfvars.json` (nessa ordem; o ultimo vence),
depois o `default` da variavel; `tostring()` de um escalar (`true`/`false`/numero) vira a
string que o Terraform produziria. Continua valendo que o que nao se resolve REPROVA. O que
nenhuma analise estatica ve' e' `-var` na linha de comando — por isso o valor mora versionado.

Por que ela reaproveita o analisador de `check_canal_simular`
-------------------------------------------------------------
Pelo mesmo motivo de `check_portal_direct_completion.py`: o analisador de HCL de la' passou pela
revisao adversarial RV-371 (6 de 7 evasoes passavam quando se casava regex contra o texto dos
`.tf`) e resolve `local.x`/`var.x` como o Terraform, tratando indirecao nao resolvivel como
ACHADO. Um segundo parser aqui repetiria os seis bugs. O que e' proprio desta cerca e' a POLITICA
e as cercas de codigo dos itens 4 e 5.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# scripts/ci/<file> -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.check_canal_simular import (  # noqa: E402
    AMBIENTE_PERMITIDO,
    PROFUNDIDADE_MAX,
    Entrada,
    Escopo,
    _atributos,
    _escopo_do_diretorio,
    _inventario,
    _ligado,
    _mascarar,
)

SOMENTE_DEV = "somente-dev"


@dataclass(frozen=True)
class Politica:
    #: `"booleano"`: `_ligado()` decide. `"presenca"`: qualquer valor nao vazio conta como ligado.
    #: `"visivel"`: nunca "ligado" (o valor e' inerte), mas os itens 2 e 3 valem igual.
    forma: str
    justificativa: str


POLITICAS: dict[str, Politica] = {
    "MAEZO_ROTEADOR_LUCAS": Politica(
        "booleano",
        "liga o roteamento por conversa do numero unico: a conversa pode sair da Helena para o "
        "Lucas, que atende cobranca com fatos SIMULADOS (ADR-0062).",
    ),
    "MAEZO_LUCAS_FONTE_COBRANCA": Politica(
        "presenca",
        "escolhe a fonte dos fatos de cobranca do Lucas; so' existe a simulada, e presente fora "
        "de dev significa que alguem montou o Lucas onde ele nao pode existir.",
    ),
    "MAEZO_LUCAS_INATIVIDADE_MINUTOS": Politica(
        "visivel",
        "janela de inatividade da conversa com o Lucas; inerte sozinha, mas tem de ser legivel "
        "na task definition como as outras duas.",
    ),
}

SETTINGS = Path("src/maezo/platform/webhooks/whatsapp/settings.py")
CLASSE_SETTINGS = "WhatsAppWebhookSettings"
CAMPO_INTERRUPTOR = "roteador_lucas_enabled"
ROTEAMENTO = Path("src/maezo/platform/webhooks/whatsapp/roteamento.py")
CLASSE_ROTEADOR = "ConversaRouter"
#: As classes cuja CONSTRUCAO so' pode acontecer com o interruptor ligado.
CONSTRUCOES_GOVERNADAS = frozenset({CLASSE_ROTEADOR, "LucasTurno"})
CODIGO = Path("src/maezo")


# ---------------------------------------------------------------------------------------
# O valor efetivo de uma entrada vigiada: tfvars do diretorio > default > nada.
# ---------------------------------------------------------------------------------------
_TOSTRING = re.compile(r"tostring\(\s*(.+?)\s*\)", re.DOTALL)
_ESCALAR = re.compile(r"true|false|-?[0-9]+(?:\.[0-9]+)?")
_REF_LOCAL = re.compile(r"local\.([A-Za-z_][A-Za-z0-9_-]*)")
_REF_VAR = re.compile(r"var\.([A-Za-z_][A-Za-z0-9_-]*)")
#: Marca de "um tfvars deste diretorio nao foi lido": nenhuma variavel dele se resolve.
_ILEGIVEL = "*"


def _do_json(valor: object) -> str | None:
    """Um valor de `*.tfvars.json` reescrito como a expressao HCL equivalente (so' escalares)."""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, float, str)):
        return json.dumps(valor)
    return None


def _tfvars_do_diretorio(diretorio: Path) -> dict[str, str | None]:
    """O que o Terraform carrega SOZINHO do diretorio, na ordem dele (o ultimo vence).

    Um `.tfvars.json` ilegivel marca o diretorio inteiro como nao resolvivel; um valor nao
    escalar entra como `None`. Nos dois casos quem consulta recebe "nao resolvivel", que REPROVA.
    """
    arquivos = [diretorio / "terraform.tfvars", diretorio / "terraform.tfvars.json"]
    arquivos += sorted(diretorio.glob("*.auto.tfvars")) + sorted(diretorio.glob("*.auto.tfvars.json"))
    valores: dict[str, str | None] = {}
    for arquivo in arquivos:
        if not arquivo.is_file():
            continue
        bruto = arquivo.read_text(encoding="utf-8", errors="replace")
        if arquivo.suffix == ".json":
            try:
                dados = json.loads(bruto)
            except ValueError:
                dados = None
            if not isinstance(dados, dict):
                valores[_ILEGIVEL] = None
                continue
            valores.update({str(k): _do_json(v) for k, v in dados.items()})
            continue
        texto, e_string = _mascarar(bruto)
        valores.update(_atributos(texto, e_string, 0, len(texto)))
    return valores


@dataclass
class _Diretorio:
    escopo: Escopo
    tfvars: dict[str, str | None]

    def resolver(self, expressao: str, profundidade: int = 0) -> str | None:
        """Como `Escopo.resolver`, mais `tostring(<escalar>)`, escalar nu e tfvars do diretorio."""
        e = expressao.strip()
        if not e or profundidade > PROFUNDIDADE_MAX:
            return None
        casamento = _TOSTRING.fullmatch(e)
        if casamento:
            return self.resolver(casamento.group(1), profundidade + 1)
        if _ESCALAR.fullmatch(e):
            return e
        if e.startswith('"') and e.endswith('"') and len(e) >= 2:
            corpo = e[1:-1]
            if "${" not in corpo and "\\" not in corpo and '"' not in corpo:
                return corpo
            interpolacao = re.fullmatch(r"\$\{([^{}]+)\}", corpo)
            return self.resolver(interpolacao.group(1), profundidade + 1) if interpolacao else None
        referencia = _REF_LOCAL.fullmatch(e)
        if referencia:
            alvo = self.escopo.locais.get(referencia.group(1))
            return self.resolver(alvo, profundidade + 1) if alvo is not None else None
        referencia = _REF_VAR.fullmatch(e)
        if referencia:
            if _ILEGIVEL in self.tfvars:
                return None
            nome = referencia.group(1)
            alvo = self.tfvars[nome] if nome in self.tfvars else self.escopo.variaveis.get(nome)
            return self.resolver(alvo, profundidade + 1) if alvo is not None else None
        return None


def _valor_efetivo(entrada: Entrada, diretorios: dict[Path, _Diretorio]) -> str | None:
    diretorio = entrada.arquivo.parent
    if diretorio not in diretorios:
        diretorios[diretorio] = _Diretorio(_escopo_do_diretorio(diretorio), _tfvars_do_diretorio(diretorio))
    return diretorios[diretorio].resolver(entrada.valor_cru)


# ---------------------------------------------------------------------------------------
# Itens 1, 2 e 3 — a configuracao efetiva das task definitions.
# ---------------------------------------------------------------------------------------
def checar_ligacao_fora_de_dev(raiz: Path = REPO_ROOT) -> list[str]:
    achados: list[str] = []
    _, entradas, textos = _inventario(raiz)
    diretorios: dict[Path, _Diretorio] = {}

    avaliadas: dict[tuple[Path, str], int] = {}
    for entrada in entradas:
        for vigiado in POLITICAS:
            if vigiado in entrada.nome_cru or entrada.nome == vigiado:
                avaliadas[(entrada.arquivo, vigiado)] = avaliadas.get((entrada.arquivo, vigiado), 0) + 1
        nome = entrada.nome
        politica = POLITICAS.get(nome) if nome is not None else None
        if nome is None or politica is None:
            continue
        onde = entrada.onde(raiz)
        if entrada.de_segredo:
            achados.append(
                f"{onde}: {nome} entregue por `valueFrom` — este interruptor existe para ser "
                f"VISIVEL na task definition; vindo de segredo, nem a cerca nem o review o veem. "
                f"({politica.justificativa})"
            )
            continue
        valor = _valor_efetivo(entrada, diretorios)
        if valor is None:
            achados.append(
                f"{onde}: {nome} = {entrada.valor_cru!r} nao e' um literal que a cerca consiga "
                f"resolver — indirecao nao resolvivel e' REPROVACAO, nao silencio. "
                f"({politica.justificativa})"
            )
            continue
        if politica.forma == "booleano":
            ligado = _ligado(valor)
        elif politica.forma == "presenca":
            ligado = bool(valor.strip())
        else:
            ligado = False
        if ligado and entrada.ambiente != AMBIENTE_PERMITIDO:
            achados.append(
                f"{onde}: {nome}={valor!r} no ambiente "
                f"{entrada.ambiente or '<fora de envs/>'} — interruptor de escopo {SOMENTE_DEV}, "
                f"so' pode ser ligado em {AMBIENTE_PERMITIDO}. ({politica.justificativa})"
            )

    for arquivo, texto in textos.items():
        for nome in POLITICAS:
            ocorrencias = texto.count(nome)
            if ocorrencias > avaliadas.get((arquivo, nome), 0):
                achados.append(
                    f"{arquivo.relative_to(raiz)}: {nome} aparece em forma que esta cerca nao "
                    f"consegue avaliar como entrada de `environment` ({ocorrencias} mencao(oes), "
                    f"{avaliadas.get((arquivo, nome), 0)} avaliada(s)) — reprovado por precaucao."
                )
    achados.extend(_fora_do_terraform(raiz))
    return achados


def _fora_do_terraform(raiz: Path) -> list[str]:
    achados: list[str] = []
    deploy = raiz / "deploy"
    if not deploy.is_dir():
        return achados
    for caminho in sorted(deploy.rglob("*")):
        if not caminho.is_file() or caminho.suffix == ".tf":
            continue
        # Um `.tftest.hcl` nao deploya nada: so' afirma o que o `.tf` (ja' analisado) produz.
        if caminho.name.endswith(".tftest.hcl"):
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for nome in POLITICAS:
            if nome in texto:
                achados.append(
                    f"{caminho.relative_to(raiz)}: {nome} declarado fora do Terraform que esta "
                    f"cerca analisa — um template nao tem ambiente provavel."
                )
    return achados


# ---------------------------------------------------------------------------------------
# Item 4 — o default no codigo.
# ---------------------------------------------------------------------------------------
def _arvore(caminho: Path) -> ast.Module | None:
    if not caminho.is_file():
        return None
    try:
        return ast.parse(caminho.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def _default_literal(valor: ast.expr | None) -> ast.expr | None:
    """O `default` de `campo: T = <valor>`: o proprio valor, ou o `default=` de `Field(...)`."""
    if isinstance(valor, ast.Call):
        nome = valor.func.id if isinstance(valor.func, ast.Name) else getattr(valor.func, "attr", "")
        if nome != "Field":
            return None
        for palavra in valor.keywords:
            if palavra.arg == "default":
                return palavra.value
        return valor.args[0] if valor.args else None
    return valor


def checar_default_desligado(raiz: Path = REPO_ROOT) -> list[str]:
    arvore = _arvore(raiz / SETTINGS)
    if arvore is None:
        return [f"{SETTINGS}: nao foi possivel analisar — a cerca precisa deste arquivo."]
    classe = next(
        (no for no in ast.walk(arvore) if isinstance(no, ast.ClassDef) and no.name == CLASSE_SETTINGS),
        None,
    )
    if classe is None:
        return [f"{SETTINGS}: classe {CLASSE_SETTINGS} nao encontrada — cerca cega."]
    campos = [
        no
        for no in classe.body
        if isinstance(no, ast.AnnAssign)
        and isinstance(no.target, ast.Name)
        and no.target.id == CAMPO_INTERRUPTOR
    ]
    if len(campos) != 1:
        return [
            f"{SETTINGS}: {CLASSE_SETTINGS}.{CAMPO_INTERRUPTOR} deveria ser declarado exatamente "
            f"uma vez ({len(campos)} encontrada(s))."
        ]
    default = _default_literal(campos[0].value)
    if not (isinstance(default, ast.Constant) and default.value is False):
        return [
            f"{SETTINGS}: {CLASSE_SETTINGS}.{CAMPO_INTERRUPTOR} tem de ter default LITERAL `False` "
            f"(ADR-0062): um default ligado abre o roteador em todo ambiente que NAO o declara, "
            f"que e' o oposto do que a cerca de `deploy/**` consegue ver."
        ]
    return []


# ---------------------------------------------------------------------------------------
# Item 5 — construcao so' dentro do `if` do interruptor.
# ---------------------------------------------------------------------------------------
def _nomes_governados(arvore: ast.Module) -> set[str]:
    """Os nomes pelos quais este modulo alcanca uma classe governada (inclusive `as` alias)."""
    nomes = set(CONSTRUCOES_GOVERNADAS)
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom):
            for alias in no.names:
                if alias.name in CONSTRUCOES_GOVERNADAS and alias.asname:
                    nomes.add(alias.asname)
    return nomes


def _construcoes(arvore: ast.Module) -> list[ast.Call]:
    nomes = _nomes_governados(arvore)
    achadas: list[ast.Call] = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        if (isinstance(no.func, ast.Name) and no.func.id in nomes) or (
            isinstance(no.func, ast.Attribute) and no.func.attr in CONSTRUCOES_GOVERNADAS
        ):
            achadas.append(no)
    return achadas


def _e_teste_do_interruptor(teste: ast.expr) -> bool:
    return isinstance(teste, ast.Attribute) and teste.attr == CAMPO_INTERRUPTOR


def _protegidas(arvore: ast.Module) -> set[int]:
    """`id()` de toda chamada que mora no CORPO (nao no `else`) de um `if <x>.roteador_lucas_enabled`."""
    dentro: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.If) and _e_teste_do_interruptor(no.test):
            for comando in no.body:
                for filho in ast.walk(comando):
                    dentro.add(id(filho))
    return dentro


def checar_construcao_condicional(raiz: Path = REPO_ROOT) -> list[str]:
    achados: list[str] = []
    roteamento = _arvore(raiz / ROTEAMENTO)
    if roteamento is None or not any(
        isinstance(no, ast.ClassDef) and no.name == CLASSE_ROTEADOR for no in ast.walk(roteamento)
    ):
        achados.append(
            f"{ROTEAMENTO}: classe {CLASSE_ROTEADOR} nao encontrada — com o nome trocado, esta "
            f"cerca procuraria uma construcao que nao existe mais (cerca cega)."
        )
    codigo = raiz / CODIGO
    for caminho in sorted(codigo.rglob("*.py")) if codigo.is_dir() else []:
        arvore = _arvore(caminho)
        if arvore is None:
            continue
        protegidas = _protegidas(arvore)
        for chamada in _construcoes(arvore):
            if id(chamada) not in protegidas:
                nome = ast.unparse(chamada.func)
                achados.append(
                    f"{caminho.relative_to(raiz)}:{chamada.lineno}: `{nome}(...)` construido fora do "
                    f"corpo de um `if settings.{CAMPO_INTERRUPTOR}:` — desligado, o roteador nem pode "
                    f"existir (ADR-0062)."
                )
    return achados


# ---------------------------------------------------------------------------------------
# Item 6 — `HandoffCobranca` so' nasce no no' `handoff_cobranca` da Helena.
# ---------------------------------------------------------------------------------------
GRAFO_HELENA = Path("src/maezo/agents/helena/graph.py")
CLASSE_HANDOFF = "HandoffCobranca"
NO_DO_HANDOFF = "handoff_cobranca"
#: As chaves que fazem de um `dict` um handoff, com ou sem o tipo: um literal com as tres (mais
#: `competencia`) e' um `HandoffCobranca` disfarcado de `dict`.
CHAVES_QUE_FAZEM_UM_HANDOFF = frozenset({"para", "cobranca_subtipo", "message_ref"})


def _pais(arvore: ast.Module) -> dict[int, ast.AST]:
    pais: dict[int, ast.AST] = {}
    for no in ast.walk(arvore):
        for filho in ast.iter_child_nodes(no):
            pais[id(filho)] = no
    return pais


def _funcao_que_contem(no: ast.AST, pais: dict[int, ast.AST]) -> ast.AST | None:
    """A funcao (ou `lambda`) MAIS INTERNA que contem `no`: uma `def` aninhada nao herda a
    permissao da de fora."""
    atual = pais.get(id(no))
    while atual is not None:
        if isinstance(atual, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return atual
        atual = pais.get(id(atual))
    return None


def _nome_da_funcao(funcao: ast.AST | None) -> str | None:
    return funcao.name if isinstance(funcao, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _nomes_do_tipo(arvore: ast.Module, classe: str) -> set[str]:
    nomes = {classe}
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom):
            nomes.update(a.asname for a in no.names if a.name == classe and a.asname)
    return nomes


def _menciona(expressao: ast.AST | None, nomes: set[str], classe: str) -> bool:
    if expressao is None:
        return False
    for no in ast.walk(expressao):
        if isinstance(no, ast.Name) and no.id in nomes:
            return True
        if isinstance(no, ast.Attribute) and no.attr == classe:
            return True
        # `x: "HandoffCobranca" = {...}` — a anotacao em string tambem constroi o tipo.
        if isinstance(no, ast.Constant) and isinstance(no.value, str) and classe in no.value:
            return True
    return False


def _chaves_literais(no: ast.AST) -> frozenset[str]:
    if isinstance(no, ast.Dict):
        return frozenset(k.value for k in no.keys if isinstance(k, ast.Constant) and isinstance(k.value, str))
    if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id == "dict":
        return frozenset(k.arg for k in no.keywords if k.arg)
    return frozenset()


def _construcoes_de_handoff(arvore: ast.Module) -> list[tuple[ast.AST, str]]:
    """Toda forma de CONSTRUIR um handoff: `HandoffCobranca(...)`, `x: HandoffCobranca = ...`,
    `cast(HandoffCobranca, ...)` e um `dict` com as chaves de um handoff. Anotar um campo sem
    valor (`handoff: HandoffCobranca | None` no `HelenaState`) nao constroi nada."""
    nomes = _nomes_do_tipo(arvore, CLASSE_HANDOFF)
    achadas: list[tuple[ast.AST, str]] = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            if _menciona(no.func, nomes, CLASSE_HANDOFF):
                achadas.append((no, f"{ast.unparse(no.func)}(...)"))
            elif (
                isinstance(no.func, (ast.Name, ast.Attribute))
                and (no.func.id if isinstance(no.func, ast.Name) else no.func.attr) == "cast"
                and no.args
                and _menciona(no.args[0], nomes, CLASSE_HANDOFF)
            ):
                achadas.append((no, f"cast({CLASSE_HANDOFF}, ...)"))
        elif (
            isinstance(no, ast.AnnAssign)
            and no.value is not None
            and _menciona(no.annotation, nomes, CLASSE_HANDOFF)
        ):
            achadas.append((no, f"<nome>: {CLASSE_HANDOFF} = ..."))
        if _chaves_literais(no) >= CHAVES_QUE_FAZEM_UM_HANDOFF:
            achadas.append((no, "dict com as chaves de um handoff"))
    return achadas


def checar_handoff_so_na_helena(raiz: Path = REPO_ROOT) -> list[str]:
    """Item 6: o UNICO lugar que constroi um handoff para o Lucas e' o no' `handoff_cobranca` do
    grafo da Helena, que so' e' alcancado depois de `_handoff_recusado` aprovar (classify +
    lexicos). Um handoff montado em qualquer outro lugar pularia essa precondicao."""
    achados: list[str] = []
    grafo = _arvore(raiz / GRAFO_HELENA)
    if grafo is None or not any(
        isinstance(no, ast.ClassDef) and no.name == CLASSE_HANDOFF for no in ast.walk(grafo)
    ):
        return [
            f"{GRAFO_HELENA}: classe {CLASSE_HANDOFF} nao encontrada — sem ela, esta cerca "
            f"procuraria uma construcao que nao existe mais (cerca cega)."
        ]
    no_handoff = [
        no
        for no in ast.walk(grafo)
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.name == NO_DO_HANDOFF
    ]
    if len(no_handoff) != 1:
        achados.append(
            f"{GRAFO_HELENA}: o no' `{NO_DO_HANDOFF}` deveria existir exatamente uma vez "
            f"({len(no_handoff)} encontrado(s)) — cerca cega."
        )
    codigo = raiz / CODIGO
    no_grafo = 0
    for caminho in sorted(codigo.rglob("*.py")) if codigo.is_dir() else []:
        arvore = _arvore(caminho)
        if arvore is None:
            continue
        e_o_grafo = caminho.resolve() == (raiz / GRAFO_HELENA).resolve()
        pais = _pais(arvore)
        for no, forma in _construcoes_de_handoff(arvore):
            if e_o_grafo and _nome_da_funcao(_funcao_que_contem(no, pais)) == NO_DO_HANDOFF:
                no_grafo += 1
                continue
            achados.append(
                f"{caminho.relative_to(raiz)}:{getattr(no, 'lineno', 0)}: handoff construido "
                f"({forma}) fora de `{GRAFO_HELENA.as_posix()}::{NO_DO_HANDOFF}` — so' ele vem "
                f"depois da precondicao `_handoff_recusado` (ADR-0062, plano §4 item 6)."
            )
    if no_handoff and no_grafo == 0:
        achados.append(
            f"{GRAFO_HELENA}: `{NO_DO_HANDOFF}` nao constroi um `{CLASSE_HANDOFF}` anotado — a "
            f"cerca nao reconhece mais a construcao legitima (cerca cega)."
        )
    return achados


# ---------------------------------------------------------------------------------------
# Item 7 — o turno do Lucas so' roda DEPOIS do lexico e do `ainvoke` da Helena.
# ---------------------------------------------------------------------------------------
DESPACHANTE = Path("src/maezo/platform/webhooks/whatsapp/dispatch.py")
LUCAS_TURNO = Path("src/maezo/platform/webhooks/whatsapp/lucas_turno.py")
CLASSE_DESPACHANTE = "HelenaDispatcher"
METODO_DISPATCH = "dispatch"
METODO_TURNO_DO_LUCAS = "_turno_do_lucas"
METODO_PRE_ROTEAR = "_pre_rotear"
CLASSE_CONVERSA_DO_TURNO = "ConversaDoTurno"


def _chamadas_de_atributo(arvore: ast.AST, atributo: str) -> list[ast.Call]:
    return [
        no
        for no in ast.walk(arvore)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute) and no.func.attr == atributo
    ]


def _e_ref_de_handoff_do_resultado(expressao: ast.AST, resultado: str) -> bool:
    """`result["handoff"]` ou `result.get("handoff")` — a saida do `ainvoke` DESTE request."""
    if isinstance(expressao, ast.Subscript):
        chave = expressao.slice
        return (
            isinstance(expressao.value, ast.Name)
            and expressao.value.id == resultado
            and isinstance(chave, ast.Constant)
            and chave.value == "handoff"
        )
    if isinstance(expressao, ast.Call) and isinstance(expressao.func, ast.Attribute):
        return (
            expressao.func.attr == "get"
            and isinstance(expressao.func.value, ast.Name)
            and expressao.func.value.id == resultado
            and bool(expressao.args)
            and isinstance(expressao.args[0], ast.Constant)
            and expressao.args[0].value == "handoff"
        )
    return False


def _sob_if_do_handoff(no: ast.AST, pais: dict[int, ast.AST], resultado: str) -> bool:
    """`no` mora no CORPO de um `if` cujo teste le' o `handoff` do resultado da Helena."""
    filho: ast.AST = no
    atual = pais.get(id(no))
    while atual is not None and not isinstance(atual, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if (
            isinstance(atual, ast.If)
            and any(filho is c for c in atual.body)
            and any(_e_ref_de_handoff_do_resultado(n, resultado) for n in ast.walk(atual.test))
        ):
            return True
        filho, atual = atual, pais.get(id(atual))
    return False


def _checar_ordem_no_dispatch(dispatch: ast.AST, pais: dict[int, ast.AST]) -> list[str]:
    onde = f"{DESPACHANTE.as_posix()}::{CLASSE_DESPACHANTE}.{METODO_DISPATCH}"
    achados: list[str] = []
    # O `ainvoke` da Helena: `result = await <compilado>.ainvoke(initial_state, ...)`.
    invocacoes = [
        no
        for no in ast.walk(dispatch)
        if isinstance(no, ast.Assign)
        and len(no.targets) == 1
        and isinstance(no.targets[0], ast.Name)
        and isinstance(no.value, ast.Await)
        and isinstance(no.value.value, ast.Call)
        and isinstance(no.value.value.func, ast.Attribute)
        and no.value.value.func.attr == "ainvoke"
    ]
    if len(invocacoes) != 1:
        return [
            f"{onde}: esperado exatamente um `result = await <grafo>.ainvoke(...)` da Helena "
            f"({len(invocacoes)} encontrado(s)) — sem ele a cerca nao sabe o que vem antes (cerca cega)."
        ]
    ainvoke = invocacoes[0]
    alvo = ainvoke.targets[0]
    assert isinstance(alvo, ast.Name)
    resultado = alvo.id
    lexicos = [
        c
        for c in _chamadas_de_atributo(dispatch, METODO_PRE_ROTEAR)
        if isinstance(c.func, ast.Attribute)
        and isinstance(c.func.value, ast.Name)
        and c.func.value.id == "self"
    ]
    if not lexicos:
        achados.append(
            f"{onde}: `self.{METODO_PRE_ROTEAR}(...)` (os lexicos de §2.3) nao e' chamado — a triagem "
            f"deterministica tem de rodar antes de qualquer turno do Lucas."
        )
    elif min(c.lineno for c in lexicos) > ainvoke.lineno:
        achados.append(
            f"{onde}:{lexicos[0].lineno}: os lexicos rodam DEPOIS do `ainvoke` da Helena — o sinal "
            f"de saude tem de chegar a Helena antes de ela decidir o handoff (§2.3)."
        )
    entrada_lexica = [
        k
        for no in ast.walk(dispatch)
        if isinstance(no, ast.Call)
        for k in no.keywords
        if k.arg == "sinal_saude_lexico"
    ]
    if not entrada_lexica or any(
        not any(isinstance(n, ast.Name) and n.id == "sinais" for n in ast.walk(k.value))
        for k in entrada_lexica
    ):
        achados.append(
            f"{onde}: o estado inicial da Helena nao recebe `sinal_saude_lexico` derivado de "
            f"`sinais` (a saida dos lexicos) — o bloqueio por sinal de saude ficaria sem entrada."
        )
    chamadas = _chamadas_de_atributo(dispatch, METODO_TURNO_DO_LUCAS)
    for chamada in chamadas:
        if _funcao_que_contem(chamada, pais) is not dispatch:
            achados.append(
                f"{onde}:{chamada.lineno}: `{METODO_TURNO_DO_LUCAS}` chamado de uma funcao aninhada — "
                f"a ordem estatica nao vale para ela."
            )
            continue
        if chamada.lineno <= ainvoke.lineno:
            achados.append(
                f"{onde}:{chamada.lineno}: turno do Lucas ANTES do `ainvoke` da Helena — ir ao Lucas "
                f"exige a saida tipada da Helena neste mesmo request (§2.3)."
            )
        if not _sob_if_do_handoff(chamada, pais, resultado):
            achados.append(
                f"{onde}:{chamada.lineno}: turno do Lucas fora do `if` que le' `{resultado}` "
                f"['handoff'] — sem handoff da Helena neste turno, nao ha' Lucas."
            )
        handoff = next((k.value for k in chamada.keywords if k.arg == "handoff"), None)
        if handoff is None or not _e_ref_de_handoff_do_resultado(handoff, resultado):
            achados.append(
                f"{onde}:{chamada.lineno}: o `handoff` entregue ao Lucas nao e' "
                f"`{resultado}['handoff']` — so' a saida do `ainvoke` deste request vale."
            )
    return achados


def checar_lucas_depois_da_helena(raiz: Path = REPO_ROOT) -> list[str]:
    """Item 7: `LucasTurno.executar` so' e' chamado em `HelenaDispatcher._turno_do_lucas`, e esse
    metodo so' e' chamado UMA vez, no `dispatch`, depois dos lexicos e do `ainvoke` da Helena, sob
    o `if` que le' o `handoff` que ela emitiu, entregando exatamente esse `handoff`. A
    `ConversaDoTurno` (a entrada do turno) tambem so' nasce em `_turno_do_lucas`."""
    achados: list[str] = []
    despachante = _arvore(raiz / DESPACHANTE)
    classe = (
        next(
            (
                n
                for n in ast.walk(despachante)
                if isinstance(n, ast.ClassDef) and n.name == CLASSE_DESPACHANTE
            ),
            None,
        )
        if despachante is not None
        else None
    )
    metodos = {
        n.name: n
        for n in (classe.body if classe else [])
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if despachante is None or METODO_DISPATCH not in metodos or METODO_TURNO_DO_LUCAS not in metodos:
        return [
            f"{DESPACHANTE}: {CLASSE_DESPACHANTE}.{METODO_DISPATCH} ou .{METODO_TURNO_DO_LUCAS} nao "
            f"encontrado — com o nome trocado, esta cerca nao prova a ordem (cerca cega)."
        ]
    pais = _pais(despachante)
    turno_do_lucas = metodos[METODO_TURNO_DO_LUCAS]
    dispatch = metodos[METODO_DISPATCH]

    codigo = raiz / CODIGO
    for caminho in sorted(codigo.rglob("*.py")) if codigo.is_dir() else []:
        if caminho.resolve() == (raiz / LUCAS_TURNO).resolve():
            continue
        e_o_despachante = caminho.resolve() == (raiz / DESPACHANTE).resolve()
        arvore = despachante if e_o_despachante else _arvore(caminho)
        if arvore is None:
            continue
        mapa = pais if e_o_despachante else _pais(arvore)
        relativo = caminho.relative_to(raiz)
        # `.executar(...)`: no despachante, QUALQUER uma fora de `_turno_do_lucas` (um alias
        # `lt = self.lucas_turno` nao escapa); fora dele, a que tem "lucas" no receptor.
        for chamada in _chamadas_de_atributo(arvore, "executar"):
            assert isinstance(chamada.func, ast.Attribute)
            dentro = e_o_despachante and _funcao_que_contem(chamada, mapa) is turno_do_lucas
            if dentro:
                continue
            if e_o_despachante or "lucas" in ast.unparse(chamada.func.value).casefold():
                achados.append(
                    f"{relativo}:{chamada.lineno}: `{ast.unparse(chamada.func)}(...)` fora de "
                    f"`{CLASSE_DESPACHANTE}.{METODO_TURNO_DO_LUCAS}` — o turno do Lucas so' roda "
                    f"depois da Helena (ADR-0062, plano §4 item 7)."
                )
        nomes_conversa = _nomes_do_tipo(arvore, CLASSE_CONVERSA_DO_TURNO)
        for no in ast.walk(arvore):
            if not (
                isinstance(no, ast.Call) and _menciona(no.func, nomes_conversa, CLASSE_CONVERSA_DO_TURNO)
            ):
                continue
            if e_o_despachante and _funcao_que_contem(no, mapa) is turno_do_lucas:
                continue
            achados.append(
                f"{relativo}:{no.lineno}: `{CLASSE_CONVERSA_DO_TURNO}(...)` fora de "
                f"`{CLASSE_DESPACHANTE}.{METODO_TURNO_DO_LUCAS}` — a entrada do turno do Lucas "
                f"so' nasce no ramo posterior a Helena."
            )
        for no in ast.walk(arvore):
            if not (isinstance(no, ast.Attribute) and no.attr == METODO_TURNO_DO_LUCAS):
                continue
            if e_o_despachante and _funcao_que_contem(no, mapa) is dispatch:
                continue
            achados.append(
                f"{relativo}:{no.lineno}: `{METODO_TURNO_DO_LUCAS}` referenciado fora de "
                f"`{CLASSE_DESPACHANTE}.{METODO_DISPATCH}` — o unico caminho ao Lucas e' o do "
                f"`dispatch`, depois da Helena."
            )

    if not any(
        _funcao_que_contem(c, pais) is turno_do_lucas
        for c in _chamadas_de_atributo(turno_do_lucas, "executar")
    ):
        achados.append(
            f"{DESPACHANTE}: `{METODO_TURNO_DO_LUCAS}` nao chama `.executar(...)` — a cerca nao "
            f"reconhece mais o turno do Lucas (cerca cega)."
        )
    chamadas = _chamadas_de_atributo(dispatch, METODO_TURNO_DO_LUCAS)
    if len(chamadas) != 1:
        achados.append(
            f"{DESPACHANTE}: `{METODO_DISPATCH}` deveria chamar `{METODO_TURNO_DO_LUCAS}` exatamente "
            f"uma vez ({len(chamadas)} encontrada(s))."
        )
    achados.extend(_checar_ordem_no_dispatch(dispatch, pais))
    return achados


LEXICO_DIR = "spec/policies/roteamento"


def checar_lexico_empacotado(raiz: Path) -> list[str]:
    """Item 8: o lexico do pre-roteamento CHEGA ao container.

    Medido em 02/10/2026: com `MAEZO_ROTEADOR_LUCAS=true`, o receptor em dev subiu sem
    `spec/policies/roteamento/` na imagem, `carregar()` levantou `LexicoInvalidoError(FileNotFoundError)`
    dentro de `_build_dispatcher` e `/webhook` ficou degradado a 501 por 4 minutos. A imagem embarca o
    `spec/` pelo force-include do pyproject (copia para `maezo/spec/`, que `resolve_spec_dir()` acha) e
    pela COPY correspondente no Dockerfile; os dois tem de citar o diretorio do lexico. Arvore sem
    pyproject/Dockerfile (clones sinteticos dos outros itens) nao e' julgada por este item.
    """
    pyproject_path = raiz / "pyproject.toml"
    dockerfile_path = raiz / "deploy" / "Dockerfile"
    if not pyproject_path.is_file() and not dockerfile_path.is_file():
        return []
    achados: list[str] = []
    if pyproject_path.is_file() and f'"{LEXICO_DIR}" = "maezo/{LEXICO_DIR}"' not in pyproject_path.read_text(
        encoding="utf-8"
    ):
        achados.append(
            f'item 8: pyproject.toml sem o force-include `"{LEXICO_DIR}" = "maezo/{LEXICO_DIR}"`: '
            "o lexico do pre-roteamento nao chega ao container e o receptor morre no boot com o roteador "
            "ligado"
        )
    if dockerfile_path.is_file() and f"COPY {LEXICO_DIR} ./{LEXICO_DIR}" not in dockerfile_path.read_text(
        encoding="utf-8"
    ):
        achados.append(
            f"item 8: deploy/Dockerfile sem `COPY {LEXICO_DIR} ./{LEXICO_DIR}`: o force-include falha "
            "fechado no `uv sync --no-editable`"
        )
    return achados


def executar(raiz: Path = REPO_ROOT) -> list[str]:
    return (
        checar_ligacao_fora_de_dev(raiz)
        + checar_default_desligado(raiz)
        + checar_construcao_condicional(raiz)
        + checar_handoff_so_na_helena(raiz)
        + checar_lucas_depois_da_helena(raiz)
        + checar_lexico_empacotado(raiz)
    )


def main() -> int:
    achados = executar(REPO_ROOT)
    if achados:
        print("check_roteador_lucas: REPROVADO", file=sys.stderr)
        for a in achados:
            print(f"  - {a}", file=sys.stderr)
        return 1
    print(
        "check_roteador_lucas: ok — roteador desligado por padrao, ligavel so' em "
        f"{AMBIENTE_PERMITIDO}, visivel na task definition e construido so' dentro do `if`; "
        "handoff so' na Helena e turno do Lucas so' depois dos lexicos e do `ainvoke` dela."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
