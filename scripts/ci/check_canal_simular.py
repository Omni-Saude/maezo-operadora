#!/usr/bin/env python3
"""Cerca de CI: a rota `/receptor/simular` do Canal de Teste so' pode existir em dev, e as
tres cercas que a contem nao podem desaparecer.

Por que esta cerca existe
--------------------------
`src/maezo/platform/testchannel/server.py::_simular_whatsapp` assina um envelope sintetico de
WhatsApp com `WHATSAPP_APP_SECRET` e o encaminha ao receptor. A assinatura que ela produz e'
INDISTINGUIVEL da da Meta — e' o mesmo HMAC sobre os mesmos bytes. Quem alcanca o canal
consegue, portanto, fabricar uma mensagem de beneficiario.

Isso e' aceitavel em `dev-sa-east-1`, atras do Cloudflare Access, com telefone restrito a faixa
de teste, porque o alternativo e' o time nao conseguir testar a triagem sem CLI. Nao e'
aceitavel em nenhum outro ambiente, e a decisao de onde ligar nao pode depender de alguem
lembrar: e' isto que a cerca mecaniza.

O que ela reprova
------------------
  1. Capacidade de assinar (`CANAL_SIMULAR_RECEPTOR` ligado) em qualquer `deploy/**` que nao
     seja o ambiente `dev-sa-east-1`. Ligar em staging, prod ou DR e' erro de CI, nao conversa
     de review.
  2. `WHATSAPP_APP_SECRET` entregue ao container do CANAL fora de dev — o segredo e' a metade
     que transforma a rota de inerte em capaz de assinar.
  3. A remocao de qualquer uma das cercas do proprio `server.py`: o portao `SIMULAR_LIGADO`, a
     faixa `FAIXA_TESTE` ancorada na FAIXA SINTETICA de teste, o uso das duas no caminho da
     requisicao, e o destino unico da requisicao assinada (o webhook do receptor). Uma cerca
     que pode ser apagada num refactor silencioso nao e' uma cerca.

Ela NAO tenta decidir se a rota deve existir. Essa decisao e' do dono e esta registrada no PR
que a introduziu; aqui so' se cobra o perimetro que aquela decisao assumiu.

Por que ela avalia a CONFIGURACAO EFETIVA, e nao o texto
---------------------------------------------------------
A primeira versao desta cerca casava expressoes regulares contra o texto dos `.tf`. Uma revisao
adversarial (RV-371) mostrou que 6 de 7 evasoes passavam: bastava quebrar o objeto em duas
linhas, trocar `value = "1"` por `value = local.ligar`, ou tirar o literal `"canal-teste"` do
nome do container para um canal COM capacidade de assinar passar limpo em producao. Uma cerca
que casa texto reprova quem a escreve do jeito esperado e absolve quem nao o faz — que e' o
inverso do que se quer.

Entao aqui os `.tf` sao analisados: comentarios e heredocs sao removidos, objetos e atributos
sao delimitados por casamento de chaves, e `local.x` / `var.x` sao RESOLVIDOS como o Terraform
os resolveria. A regra que fecha o resto: **indireção nao resolvivel e' ACHADO, nao silencio**.
Se a cerca nao consegue provar que o portao esta desligado e que o segredo nao chega ao canal,
ela reprova. `server.py` e' lido por AST pelo mesmo motivo — renomear a funcao ou trocar aspas
nao pode fazer uma cerca evaporar.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: O UNICO ambiente onde a rota pode ser ligada. Um caminho, nao um padrao: um padrao como
#: `dev*` passaria a aceitar `dev-us-east-1` amanha sem ninguem decidir isso.
AMBIENTE_PERMITIDO = "dev-sa-east-1"

#: Nome do container do canal nas task definitions — um dos SINAIS de identificacao, nao o
#: unico: a cerca 2 tambem reconhece o canal pelo rotulo do `aws_ecs_task_definition` e pelo
#: modulo que o container executa, porque o literal pode vir de um `local`.
CONTAINER_CANAL = "canal-teste"

#: O modulo que o container do canal executa. Renomear o container nao renomeia isto.
MODULO_CANAL = "maezo.platform.testchannel"

VAR_PORTAO = "CANAL_SIMULAR_RECEPTOR"
VAR_SEGREDO = "WHATSAPP_APP_SECRET"
VAR_RECEPTOR = "RECEPTOR_URL"

#: ESCOPO de um portao de capacidade.
SOMENTE_DEV = "somente-dev"
QUALQUER_AMBIENTE = "qualquer-ambiente"

#: Familias de variaveis cujos portoes esta cerca governa. Um nome que casa um destes prefixos
#: e NAO esta em `POLITICAS_DE_PORTAO` e' REPROVADO: a decisao de onde um portao novo pode ser
#: ligado tem de ser declarada por quem o cria, nao descoberta por quem o revisar depois.
PREFIXOS_VIGIADOS = ("WHATSAPP_WEBHOOK_",)

#: Valores que o receptor le' como "ligado" (pydantic-settings converte todos para `True`).
#: `value = "0"` ou ausencia sao os unicos jeitos de estar desligado.
VALORES_LIGADOS = frozenset({"1", "true", "t", "yes", "y", "on"})


@dataclass(frozen=True)
class PoliticaPortao:
    """Onde um portao de capacidade pode estar ligado, e POR QUE.

    A justificativa nao e' decoracao: ela e' o que um revisor le' quando a cerca reprova, e o
    que obriga quem acrescenta um portao a dizer que capacidade esta' abrindo.
    """

    escopo: str
    justificativa: str


#: Os portoes que esta cerca conhece. Acrescentar um portao da familia vigiada SEM acrescentar
#: uma linha aqui reprova o CI — e' a diferenca entre uma cerca que enumera variaveis (e fica
#: para tras a cada porteira nova) e uma que governa a familia.
POLITICAS_DE_PORTAO: dict[str, PoliticaPortao] = {
    VAR_PORTAO: PoliticaPortao(
        SOMENTE_DEV,
        "liga `/receptor/simular`, que assina um envelope INDISTINGUIVEL do da Meta: quem "
        "alcanca o canal fabrica mensagem de beneficiario.",
    ),
    "WHATSAPP_WEBHOOK_DEVOLVE_TURNO": PoliticaPortao(
        SOMENTE_DEV,
        "faz o ack de `/webhook` devolver `resposta` (texto voltado ao beneficiario, redigido "
        "pela Helena) e `conversation_id` — egresso de conteudo conversacional no corpo da "
        "resposta HTTP. Em producao quem recebe o ack e' a Meta.",
    ),
    "WHATSAPP_WEBHOOK_MEMORIA_CLINICA": PoliticaPortao(
        QUALQUER_AMBIENTE,
        "memoria clinica entre turnos (Frente 2.1): faz `population` e as idades sobreviverem de "
        "um turno para o outro DENTRO da mesma conversa, por uma janela de horas. NAO abre egresso "
        "nenhum — nada sai do processo que ja nao saia, e o dado lembrado vem do CHECKPOINT da "
        "propria conversa, nunca do que o chamador mandou (a validacao na fronteira de `receive` "
        "recusa memoria malformada, sem carimbo ou fora da janela, degradando para o "
        "comportamento de hoje). O que ele muda e' QUAL TABELA de red flag e' consultada no "
        "segundo turno — legitimo em qualquer ambiente, e DESLIGA-LO e' que precisa de "
        "justificativa: sem ele um bebe de 11 meses volta a ser triado pela tabela de adulto, "
        "medido tres vezes em 13/09/2026.",
    ),
    "WHATSAPP_WEBHOOK_ACK_THEN_QUEUE": PoliticaPortao(
        QUALQUER_AMBIENTE,
        "modo ack-then-queue (decisao do dono R-072): muda QUANDO o turno roda, nao O QUE sai "
        "do processo. Nao abre egresso, entao e' legitimo em qualquer ambiente.",
    ),
}

#: Casa qualquer nome governado explicitamente ou por familia, para a regra de precaucao.
_NOME_VIGIADO = re.compile(
    "|".join(
        [re.escape(n) for n in POLITICAS_DE_PORTAO] + [re.escape(p) + "[A-Z0-9_]+" for p in PREFIXOS_VIGIADOS]
    )
)

NOMES_EXIGIDOS = ("SIMULAR_LIGADO", "FAIXA_TESTE")

#: A faixa SINTETICA de teste. A cerca antiga so' exigia `^`...`$`; isso deixava passar
#: `^\d+$`, que e' ancorado nas duas pontas e aceita o telefone de qualquer beneficiario real.
#: O que precisa continuar valendo e' a faixa, nao a ancora.
PREFIXO_FAIXA_TESTE = "^55119000000"

ROTA_SIMULAR = "/receptor/simular"

#: O UNICO destino legitimo do envelope assinado.
NOME_BASE_SINK = "RECEPTOR"
CAMINHO_SINK = "/webhook"

#: Chamadas que constroem uma requisicao HTTP de saida. Se alguma delas aparecer numa funcao
#: que assina, o alvo tem de ser o webhook do receptor.
CONSTRUTORES_HTTP = frozenset(
    {"Request", "urlopen", "urlretrieve", "post", "put", "patch", "request", "send"}
)

#: Funcoes de `hmac` que PRODUZEM assinatura. `compare_digest` verifica, e verificar e'
#: legitimo em qualquer lugar.
HMAC_QUE_ASSINA = frozenset({"new", "digest"})

PROFUNDIDADE_MAX = 8

_HEREDOC = re.compile(r"<<-?([A-Za-z_][A-Za-z0-9_]*)[^\S\n]*\n")
_CHAVE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*=(?!=)")
_RECURSO_TASKDEF = re.compile(r'resource\s+"aws_ecs_task_definition"\s+"([^"]+)"\s*\{')
_REF_LOCAL = re.compile(r"local\.([A-Za-z_][A-Za-z0-9_-]*)")
_REF_VAR = re.compile(r"var\.([A-Za-z_][A-Za-z0-9_-]*)")


# ---------------------------------------------------------------------------------------
# Lexer minimo de HCL: mascara de string + comentarios apagados.
# ---------------------------------------------------------------------------------------
def _mascarar(texto: str) -> tuple[str, list[bool]]:
    """Devolve `(texto sem comentarios, mascara "este caractere esta dentro de string")`.

    O texto devolvido tem o MESMO comprimento do original (comentario vira espaco), entao
    qualquer deslocamento calculado sobre ele vale para o original — e' assim que o numero da
    linha citada no achado continua sendo o numero real do arquivo.

    Heredocs (`<<-EOT ... EOT`) contam como string: eles aparecem em `deploy/**` e trazem
    chaves e aspas que destruiriam o casamento de delimitadores.
    """
    saida = list(texto)
    e_string = [False] * len(texto)
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c == '"':
            e_string[i] = True
            j = i + 1
            while j < n:
                if texto[j] == "\\" and j + 1 < n:
                    e_string[j] = e_string[j + 1] = True
                    j += 2
                    continue
                e_string[j] = True
                j += 1
                if texto[j - 1] == '"':
                    break
                if texto[j - 1] == "\n":  # string nao fechada: nao engolir o arquivo inteiro
                    break
            i = j
            continue
        if c == "#" or texto.startswith("//", i):
            fim = texto.find("\n", i)
            fim = n if fim < 0 else fim
            saida[i:fim] = " " * (fim - i)
            i = fim
            continue
        if texto.startswith("/*", i):
            fim = texto.find("*/", i + 2)
            fim = n if fim < 0 else fim + 2
            for k in range(i, fim):
                if texto[k] != "\n":
                    saida[k] = " "
            i = fim
            continue
        casamento = _HEREDOC.match(texto, i)
        if casamento:
            fim = _fim_heredoc(texto, casamento.end(), casamento.group(1))
            for k in range(i, fim):
                e_string[k] = True
            i = fim
            continue
        i += 1
    return "".join(saida), e_string


def _fim_heredoc(texto: str, inicio: int, marcador: str) -> int:
    fechamento = re.compile(rf"^[^\S\n]*{re.escape(marcador)}[^\S\n]*$", re.MULTILINE)
    achado = fechamento.search(texto, inicio)
    return achado.end() if achado else len(texto)


def _fecha(texto: str, e_string: list[bool], i: int, abre: str, fecha: str) -> int:
    """Indice logo APOS o delimitador que fecha o `abre` em `i`; -1 se nao fecha."""
    profundidade = 0
    for j in range(i, len(texto)):
        if e_string[j]:
            continue
        if texto[j] == abre:
            profundidade += 1
        elif texto[j] == fecha:
            profundidade -= 1
            if profundidade == 0:
                return j + 1
    return -1


def _fim_expressao(texto: str, e_string: list[bool], inicio: int, limite: int) -> int:
    """Fim da expressao que comeca em `inicio`: a primeira virgula ou quebra de linha de
    nivel 0. E' isto que faz o objeto multi-linha SEM virgula (evasao A) ser lido igual ao
    de uma linha so'."""
    i = inicio
    while i < limite and not e_string[i] and texto[i] in " \t\r\n":
        i += 1
    profundidade = 0
    while i < limite:
        if e_string[i]:
            i += 1
            continue
        c = texto[i]
        if c in "{[(":
            profundidade += 1
        elif c in "}])":
            if profundidade == 0:
                return i
            profundidade -= 1
        elif profundidade == 0 and c in ",\n":
            return i
        i += 1
    return limite


def _atributos(texto: str, e_string: list[bool], inicio: int, fim: int) -> dict[str, str]:
    """`chave = expressao` no nivel 0 do corpo `texto[inicio:fim]`."""
    achados: dict[str, str] = {}
    i = inicio
    profundidade = 0
    while i < fim:
        if e_string[i]:
            i += 1
            continue
        c = texto[i]
        if c in "{[(":
            profundidade += 1
            i += 1
            continue
        if c in "}])":
            profundidade -= 1
            i += 1
            continue
        if profundidade == 0 and (i == inicio or not (texto[i - 1].isalnum() or texto[i - 1] in "._-")):
            casamento = _CHAVE.match(texto, i)
            if casamento:
                fim_expr = _fim_expressao(texto, e_string, casamento.end(), fim)
                achados.setdefault(casamento.group(1), texto[casamento.end() : fim_expr].strip())
                i = fim_expr
                continue
        i += 1
    return achados


def _objetos(texto: str, e_string: list[bool]) -> list[tuple[int, int]]:
    """Todos os `{ ... }` do arquivo, como `(inicio_do_corpo, fim_do_corpo)`."""
    spans: list[tuple[int, int]] = []
    for i, c in enumerate(texto):
        if c == "{" and not e_string[i]:
            fim = _fecha(texto, e_string, i, "{", "}")
            if fim > 0:
                spans.append((i + 1, fim - 1))
    return spans


def _linha(texto: str, deslocamento: int) -> int:
    return texto.count("\n", 0, deslocamento) + 1


# ---------------------------------------------------------------------------------------
# Resolucao de locals / variables — "a configuracao efetiva", nao o texto.
# ---------------------------------------------------------------------------------------
@dataclass
class Escopo:
    """Os `locals` e os `default` de `variable` visiveis num diretorio de modulo Terraform."""

    locais: dict[str, str] = field(default_factory=dict)
    variaveis: dict[str, str] = field(default_factory=dict)

    def resolver(self, expressao: str, profundidade: int = 0) -> str | None:
        """Valor efetivo da expressao, ou `None` quando a cerca NAO consegue prova-lo.

        `None` nunca significa "tudo bem": quem chama trata indireção nao resolvivel como
        achado. E' esta inversao que fecha as evasoes D e F.
        """
        e = expressao.strip()
        if not e or profundidade > PROFUNDIDADE_MAX:
            return None
        if e.startswith('"') and e.endswith('"') and len(e) >= 2:
            corpo = e[1:-1]
            if "${" not in corpo and "\\" not in corpo and '"' not in corpo:
                return corpo
            interpolacao = re.fullmatch(r"\$\{([^{}]+)\}", corpo)
            if interpolacao:
                return self.resolver(interpolacao.group(1), profundidade + 1)
            return None
        referencia = _REF_LOCAL.fullmatch(e)
        if referencia:
            alvo = self.locais.get(referencia.group(1))
            return self.resolver(alvo, profundidade + 1) if alvo is not None else None
        referencia = _REF_VAR.fullmatch(e)
        if referencia:
            alvo = self.variaveis.get(referencia.group(1))
            return self.resolver(alvo, profundidade + 1) if alvo is not None else None
        return None


def _escopo_do_diretorio(diretorio: Path) -> Escopo:
    escopo = Escopo()
    for arquivo in sorted(diretorio.glob("*.tf")):
        if not arquivo.is_file():
            continue
        texto, e_string = _mascarar(arquivo.read_text(encoding="utf-8", errors="replace"))
        for casamento in re.finditer(r"\blocals\s*\{", texto):
            if e_string[casamento.start()]:
                continue
            fim = _fecha(texto, e_string, casamento.end() - 1, "{", "}")
            if fim > 0:
                escopo.locais.update(_atributos(texto, e_string, casamento.end(), fim - 1))
        for casamento in re.finditer(r'\bvariable\s+"([^"]+)"\s*\{', texto):
            fim = _fecha(texto, e_string, casamento.end() - 1, "{", "}")
            if fim <= 0:
                continue
            corpo = _atributos(texto, e_string, casamento.end(), fim - 1)
            if "default" in corpo:
                escopo.variaveis[casamento.group(1)] = corpo["default"]
    return escopo


# ---------------------------------------------------------------------------------------
# Modelo do que foi lido dos `.tf`.
# ---------------------------------------------------------------------------------------
@dataclass
class Entrada:
    """Uma entrada de `environment` ou `secrets` de um container."""

    arquivo: Path
    ambiente: str
    linha: int
    inicio: int
    nome: str | None
    nome_cru: str
    valor_cru: str
    valor: str | None
    de_segredo: bool

    def onde(self, raiz: Path) -> str:
        return f"{self.arquivo.relative_to(raiz)}:{self.linha}"


@dataclass
class Container:
    arquivo: Path
    ambiente: str
    linha: int
    inicio: int
    fim: int
    rotulo: str
    nome: str | None
    imagem: str
    corpo: str
    entradas: list[Entrada] = field(default_factory=list)

    @property
    def e_canal(self) -> bool:
        """O container e' o canal (ou a cerca nao consegue provar que NAO e').

        Quatro sinais independentes, porque cada um deles some sozinho com um `local`: o
        rotulo do `aws_ecs_task_definition`, o nome resolvido do container, o modulo que ele
        executa (`maezo.platform.testchannel`) e a imagem que ele puxa. A evasao F tirou
        apenas o primeiro deles.

        E mais um, fail-closed: um container cujo nome a cerca NAO resolve e que carrega o
        segredo de assinatura ou o portao. Nao da' para absolver o que nao se consegue
        identificar quando o que ele carrega e' exatamente a capacidade vigiada.
        """
        if "canal" in self.rotulo.lower():
            return True
        if self.nome is not None and "canal" in self.nome.lower():
            return True
        if MODULO_CANAL in self.corpo or "canal" in self.imagem.lower():
            return True
        return self.nome is None and (VAR_SEGREDO in self.corpo or VAR_PORTAO in self.corpo)

    def descricao(self, raiz: Path) -> str:
        nome = self.nome if self.nome is not None else f"<nao resolvivel: {self.rotulo or '?'}>"
        return f"{self.arquivo.relative_to(raiz)}:{self.linha} container {nome!r}"


def _arquivos_deploy(raiz: Path) -> list[Path]:
    return sorted(p for p in (raiz / "deploy").rglob("*.tf") if p.is_file())


def _ambiente_de(caminho: Path, raiz: Path) -> str:
    """Nome do diretorio de ambiente, ou "" quando o arquivo nao mora em `envs/<nome>/`."""
    partes = caminho.relative_to(raiz).parts
    return partes[partes.index("envs") + 1] if "envs" in partes else ""


def _ler_arquivo(arquivo: Path, raiz: Path, escopo: Escopo) -> tuple[list[Container], list[Entrada], str]:
    """Containers, entradas de ambiente/segredo e texto mascarado de um `.tf`."""
    bruto = arquivo.read_text(encoding="utf-8", errors="replace")
    texto, e_string = _mascarar(bruto)
    ambiente = _ambiente_de(arquivo, raiz)

    rotulos: list[tuple[int, int, str]] = []
    for casamento in _RECURSO_TASKDEF.finditer(texto):
        fim = _fecha(texto, e_string, casamento.end() - 1, "{", "}")
        if fim > 0:
            rotulos.append((casamento.start(), fim, casamento.group(1)))

    containers: list[Container] = []
    entradas: list[Entrada] = []
    for inicio, fim in _objetos(texto, e_string):
        atributos = _atributos(texto, e_string, inicio, fim)
        tem_nome = "name" in atributos
        if tem_nome and "image" in atributos:
            rotulo = next((r for (a, b, r) in rotulos if a <= inicio <= b), "")
            containers.append(
                Container(
                    arquivo=arquivo,
                    ambiente=ambiente,
                    linha=_linha(bruto, inicio),
                    inicio=inicio,
                    fim=fim,
                    rotulo=rotulo,
                    nome=escopo.resolver(atributos["name"]),
                    imagem=atributos["image"],
                    corpo=texto[inicio:fim],
                )
            )
        elif tem_nome and ("value" in atributos or "valueFrom" in atributos):
            de_segredo = "valueFrom" in atributos
            valor_cru = atributos.get("value", atributos.get("valueFrom", ""))
            entradas.append(
                Entrada(
                    arquivo=arquivo,
                    ambiente=ambiente,
                    linha=_linha(bruto, inicio),
                    inicio=inicio,
                    nome=escopo.resolver(atributos["name"]),
                    nome_cru=atributos["name"],
                    valor_cru=valor_cru,
                    valor=None if de_segredo else escopo.resolver(valor_cru),
                    de_segredo=de_segredo,
                )
            )

    # Cada entrada pertence ao container MAIS INTERNO cujo corpo a contem.
    for entrada in entradas:
        donos = [c for c in containers if c.inicio <= entrada.inicio < c.fim]
        if donos:
            min(donos, key=lambda c: c.fim - c.inicio).entradas.append(entrada)
    return containers, entradas, texto


def _inventario(raiz: Path) -> tuple[list[Container], list[Entrada], dict[Path, str]]:
    containers: list[Container] = []
    entradas: list[Entrada] = []
    textos: dict[Path, str] = {}
    escopos: dict[Path, Escopo] = {}
    for arquivo in _arquivos_deploy(raiz):
        escopo = escopos.setdefault(arquivo.parent, _escopo_do_diretorio(arquivo.parent))
        c, e, texto = _ler_arquivo(arquivo, raiz, escopo)
        containers.extend(c)
        entradas.extend(e)
        textos[arquivo] = texto
    return containers, entradas, textos


def _politica_de(nome: str) -> PoliticaPortao | None:
    return POLITICAS_DE_PORTAO.get(nome)


def _da_familia_vigiada(nome: str) -> bool:
    return any(nome.startswith(prefixo) for prefixo in PREFIXOS_VIGIADOS)


def _ligado(valor: str) -> bool:
    return valor.strip().lower() in VALORES_LIGADOS


# ---------------------------------------------------------------------------------------
# Cerca 1 — o portao.
# ---------------------------------------------------------------------------------------
def checar_ligacao_fora_de_dev(raiz: Path = REPO_ROOT) -> list[str]:
    """Cerca 1: os portoes de CAPACIDADE so' podem estar ligados onde a politica permite.

    Nao e' mais uma checagem de UMA variavel. `POLITICAS_DE_PORTAO` declara, por portao, o
    escopo e a razao; `PREFIXOS_VIGIADOS` declara as FAMILIAS cujos membros novos precisam de
    uma linha la'. A revisao RV-371 §Δ2 mostrou por que: tres commits depois de esta cerca ser
    reescrita por deixar um portao sem cerca, um segundo portao (`WHATSAPP_WEBHOOK_DEVOLVE_TURNO`,
    que faz o ack devolver o texto da Helena) nasceu igualmente sem cerca. Uma cerca que enumera
    variaveis fica para tras a cada porteira nova; esta governa a familia.

    Reprova: portao de escopo `somente-dev` ligado fora de `dev-sa-east-1`; valor que a cerca
    nao consegue resolver (em qualquer ambiente — um portao invisivel nao e' um portao); portao
    entregue por `valueFrom`; membro novo da familia sem politica declarada; mencao que o parser
    nao conseguiu avaliar; e portao `somente-dev` declarado fora do Terraform analisado.
    """
    achados: list[str] = []
    _, entradas, textos = _inventario(raiz)

    vistas: dict[tuple[Path, str], int] = {}
    for entrada in entradas:
        for token in _NOME_VIGIADO.findall(entrada.nome_cru) + (
            [entrada.nome] if entrada.nome and _NOME_VIGIADO.fullmatch(entrada.nome) else []
        ):
            chave = (entrada.arquivo, token)
            vistas[chave] = vistas.get(chave, 0) + 1

        nome = entrada.nome
        if nome is None:
            continue
        politica = _politica_de(nome)
        if politica is None:
            if _da_familia_vigiada(nome):
                achados.append(
                    f"{entrada.onde(raiz)}: {nome} e' da familia vigiada "
                    f"{PREFIXOS_VIGIADOS} mas nao tem politica declarada em "
                    f"POLITICAS_DE_PORTAO — quem acrescenta um portao declara que capacidade "
                    f"ele abre e onde pode estar ligado; a cerca nao adivinha."
                )
            continue
        if politica.escopo != SOMENTE_DEV:
            continue

        onde = entrada.onde(raiz)
        if entrada.de_segredo:
            achados.append(
                f"{onde}: {nome} entregue por `valueFrom` — o portao existe para ser VISIVEL "
                f"na task definition; vindo de segredo, nem a cerca nem o review o veem. "
                f"({politica.justificativa})"
            )
            continue
        if entrada.valor is None:
            achados.append(
                f"{onde}: {nome} = {entrada.valor_cru!r} nao e' um literal que a cerca consiga "
                f"resolver — indireção nao resolvivel e' REPROVACAO, nao silencio: sem resolve-la "
                f"nao ha como provar que o portao esta desligado. ({politica.justificativa})"
            )
            continue
        if _ligado(entrada.valor) and entrada.ambiente != AMBIENTE_PERMITIDO:
            achados.append(
                f"{onde}: {nome}={entrada.valor!r} no ambiente "
                f"{entrada.ambiente or '<fora de envs/>'} — portao de escopo {SOMENTE_DEV}, so' "
                f"pode ser ligado em {AMBIENTE_PERMITIDO}. ({politica.justificativa})"
            )

    # Declaracao que a cerca NAO conseguiu ler como entrada de ambiente: reprova em vez de
    # ignorar. Comentarios ja sairam do texto mascarado, entao mencao aqui e' codigo.
    for arquivo, texto in textos.items():
        for token in sorted(set(_NOME_VIGIADO.findall(texto))):
            ocorrencias = texto.count(token)
            avaliadas = vistas.get((arquivo, token), 0)
            if ocorrencias > avaliadas:
                achados.append(
                    f"{arquivo.relative_to(raiz)}: {token} aparece em forma que esta cerca nao "
                    f"consegue avaliar como entrada de `environment` ({ocorrencias} mencao(oes), "
                    f"{avaliadas} avaliada(s)) — reprovado por precaucao."
                )

    achados.extend(_portao_fora_do_terraform(raiz))
    return achados


def _portao_fora_do_terraform(raiz: Path) -> list[str]:
    """Portao `somente-dev` declarado num manifesto que esta cerca nao analisa (Helm, k8s...).

    Nao ha como provar o ambiente de um template, entao qualquer ocorrencia reprova. Portoes de
    escopo `qualquer-ambiente` (ack-then-queue) nao caem aqui: eles podem estar em qualquer
    lugar por definicao.
    """
    achados: list[str] = []
    deploy = raiz / "deploy"
    if not deploy.is_dir():
        return achados
    somente_dev = sorted(n for n, p in POLITICAS_DE_PORTAO.items() if p.escopo == SOMENTE_DEV)
    for caminho in sorted(deploy.rglob("*")):
        if not caminho.is_file() or caminho.suffix == ".tf":
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for nome in somente_dev:
            if nome in texto:
                achados.append(
                    f"{caminho.relative_to(raiz)}: {nome} declarado fora do Terraform que esta "
                    f"cerca analisa — um template nao tem ambiente provavel, entao um portao "
                    f"{SOMENTE_DEV} nao pode ser ligado por ele."
                )
        for nome in sorted(set(_NOME_VIGIADO.findall(texto))):
            if nome not in POLITICAS_DE_PORTAO:
                achados.append(
                    f"{caminho.relative_to(raiz)}: {nome} e' da familia vigiada e nao tem "
                    f"politica declarada — reprovado por precaucao, fora do Terraform analisado."
                )
    return achados


# ---------------------------------------------------------------------------------------
# Cerca 2 — o segredo no container do canal.
# ---------------------------------------------------------------------------------------
def checar_segredo_no_canal(raiz: Path = REPO_ROOT) -> list[str]:
    """Cerca 2: `WHATSAPP_APP_SECRET` no container do CANAL so' em `dev-sa-east-1`.

    O canal e' identificado pelo RECURSO (`aws_ecs_task_definition.<...canal...>`), pelo nome
    resolvido e pelo modulo que ele executa — nao pelo literal `"canal-teste"` no texto, que
    some com um `local` (evasao F). O mesmo segredo no container do RECEPTOR e' legitimo em
    todo ambiente: la' ele VERIFICA a assinatura da Meta, nao produz uma.
    """
    achados: list[str] = []
    containers, _, _ = _inventario(raiz)
    for container in containers:
        if not container.e_canal:
            continue
        fora_de_dev = container.ambiente != AMBIENTE_PERMITIDO
        for entrada in container.entradas:
            if entrada.nome is None:
                achados.append(
                    f"{entrada.onde(raiz)}: variavel de ambiente com nome nao resolvivel "
                    f"({entrada.nome_cru!r}) no {container.descricao(raiz)} — sem resolve-lo a "
                    f"cerca nao consegue provar que {VAR_SEGREDO} nao chega ao canal."
                )
                continue
            if entrada.nome == VAR_SEGREDO and fora_de_dev:
                achados.append(
                    f"{entrada.onde(raiz)}: {VAR_SEGREDO} entregue ao {container.descricao(raiz)} "
                    f"no ambiente {container.ambiente or '<fora de envs/>'} — e' a metade que "
                    f"torna a rota capaz de assinar."
                )
        vistas = sum(1 for e in container.entradas if e.nome == VAR_SEGREDO or VAR_SEGREDO in e.nome_cru)
        if fora_de_dev and container.corpo.count(VAR_SEGREDO) > vistas:
            achados.append(
                f"{container.descricao(raiz)}: {VAR_SEGREDO} aparece no container do canal em "
                f"forma que a cerca nao consegue avaliar, no ambiente "
                f"{container.ambiente or '<fora de envs/>'} — reprovado por precaucao."
            )
    return achados


# ---------------------------------------------------------------------------------------
# Cerca 3 — as cercas dentro do `server.py`.
# ---------------------------------------------------------------------------------------
def _servidor(raiz: Path) -> Path:
    return raiz / "src" / "maezo" / "platform" / "testchannel" / "server.py"


def _funcoes(arvore: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {no.name: no for no in ast.walk(arvore) if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _nomes_usados(no: ast.AST) -> set[str]:
    return {filho.id for filho in ast.walk(no) if isinstance(filho, ast.Name)}


def _atribuicoes(arvore: ast.AST) -> dict[str, ast.expr]:
    return {
        alvo.id: no.value
        for no in ast.walk(arvore)
        if isinstance(no, ast.Assign)
        for alvo in no.targets
        if isinstance(alvo, ast.Name)
    }


def _constantes(no: ast.AST) -> set[object]:
    return {filho.value for filho in ast.walk(no) if isinstance(filho, ast.Constant)}


def _tratadores_da_rota(arvore: ast.AST) -> set[str]:
    """Funcoes chamadas no ramo que despacha `/receptor/simular`.

    Resolver pelo DESPACHO, e nao pelo nome `_simular_whatsapp`, e' o que impede a evasao I:
    renomear a funcao nao faz a cerca perder o alvo.
    """
    tratadores: set[str] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, ast.If):
            continue
        if ROTA_SIMULAR not in _constantes(no.test):
            continue
        for filho in ast.walk(no):
            if isinstance(filho, ast.Call):
                if isinstance(filho.func, ast.Attribute):
                    tratadores.add(filho.func.attr)
                elif isinstance(filho.func, ast.Name):
                    tratadores.add(filho.func.id)
    return tratadores


def _funcoes_que_assinam(arvore: ast.AST) -> set[str]:
    """Funcoes que PRODUZEM um HMAC. Sao elas que precisam estar cercadas."""
    assinantes: set[str] = set()
    for nome, funcao in _funcoes(arvore).items():
        for no in ast.walk(funcao):
            if (
                isinstance(no, ast.Call)
                and isinstance(no.func, ast.Attribute)
                and no.func.attr in HMAC_QUE_ASSINA
                and isinstance(no.func.value, ast.Name)
                and no.func.value.id == "hmac"
            ):
                assinantes.add(nome)
                break
    return assinantes


def _e_sink_do_receptor(no: ast.expr) -> bool:
    """`RECEPTOR + "/webhook"` — exatamente isso, nada mais."""
    return (
        isinstance(no, ast.BinOp)
        and isinstance(no.op, ast.Add)
        and isinstance(no.left, ast.Name)
        and no.left.id == NOME_BASE_SINK
        and isinstance(no.right, ast.Constant)
        and no.right.value == CAMINHO_SINK
    )


def _padrao_da_faixa(valor: ast.expr) -> str | None:
    """O padrao literal de `re.compile("...")`, ou `None` se nao for literal.

    Lido por AST: aspas simples, `r"..."` e `'''...'''` dao o MESMO resultado — e' por isso
    que a evasao G (trocar o estilo de aspas) deixa de funcionar.
    """
    if not isinstance(valor, ast.Call) or not valor.args:
        return None
    alvo = valor.func
    nome = alvo.attr if isinstance(alvo, ast.Attribute) else getattr(alvo, "id", "")
    if nome != "compile":
        return None
    primeiro = valor.args[0]
    if isinstance(primeiro, ast.Constant) and isinstance(primeiro.value, str):
        return primeiro.value
    return None


def checar_cercas_no_codigo(raiz: Path = REPO_ROOT) -> list[str]:
    """Cerca 3: portao, faixa, uso no caminho da requisicao e destino unico."""
    achados: list[str] = []
    servidor = _servidor(raiz)
    if not servidor.is_file():
        return [f"{servidor.relative_to(raiz)} nao existe — a cerca perdeu o alvo."]
    fonte = servidor.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    atribuidos = _atribuicoes(arvore)

    rota_existe = ROTA_SIMULAR in {c for c in _constantes(arvore) if isinstance(c, str)}
    if not rota_existe and not _funcoes_que_assinam(arvore):
        # A rota sumir e' legitimo — e' a alternativa que o proprio dono ofereceu. Sem rota e
        # sem ninguem assinando, nao ha perimetro a cobrar.
        return achados

    for nome in NOMES_EXIGIDOS:
        if nome not in atribuidos:
            achados.append(f"server.py: a constante {nome} desapareceu — era uma das cercas da rota.")

    portao = atribuidos.get("SIMULAR_LIGADO")
    if portao is not None:
        constantes = _constantes(portao)
        if VAR_PORTAO not in constantes or "1" not in constantes:
            achados.append(
                f'server.py: SIMULAR_LIGADO nao e\' mais {VAR_PORTAO}=="1" lido do ambiente — '
                f"o portao deixou de ser o que a task definition liga e a cerca de CI vigia."
            )
    receptor = atribuidos.get(NOME_BASE_SINK)
    if receptor is not None and VAR_RECEPTOR not in _constantes(receptor):
        achados.append(
            f"server.py: {NOME_BASE_SINK} nao vem mais de {VAR_RECEPTOR} — o destino do "
            f"envelope assinado deixou de ser o receptor configurado."
        )

    faixa = atribuidos.get("FAIXA_TESTE")
    if faixa is not None:
        padrao = _padrao_da_faixa(faixa)
        if padrao is None:
            achados.append(
                "server.py: FAIXA_TESTE nao e' mais `re.compile(<literal>)` — a cerca do "
                "telefone tem de ser legivel no codigo, nao montada em tempo de execucao."
            )
        elif not (padrao.startswith(PREFIXO_FAIXA_TESTE) and padrao.endswith("$")):
            achados.append(
                f"server.py: FAIXA_TESTE={padrao!r} nao esta ancorada na faixa sintetica de "
                f"teste ({PREFIXO_FAIXA_TESTE}...$) — ancorar em ^...$ nao basta: `^\\d+$` "
                f"tambem e' ancorado e aceita o telefone de um beneficiario real."
            )

    funcoes = _funcoes(arvore)
    assinantes = _funcoes_que_assinam(arvore)
    tratadores = {nome for nome in _tratadores_da_rota(arvore) if nome in funcoes}
    if rota_existe and not tratadores and not assinantes:
        achados.append(
            f"server.py: {ROTA_SIMULAR} e' despachada mas a cerca nao encontrou o tratador nem "
            f"nenhuma funcao que assine — sem alvo nao ha como provar que as cercas valem."
        )

    for nome in sorted(tratadores | assinantes):
        funcao = funcoes[nome]
        usados = _nomes_usados(funcao)
        for exigido in NOMES_EXIGIDOS:
            if exigido not in usados:
                achados.append(
                    f"server.py: {nome} assina/atende {ROTA_SIMULAR} sem usar {exigido} — a "
                    f"cerca nao esta no caminho da requisicao."
                )

    for nome in sorted(assinantes):
        achados.extend(_checar_destino(funcoes[nome], nome))
    return achados


def _checar_destino(funcao: ast.AST, nome: str) -> list[str]:
    """O envelope assinado so' pode sair para `RECEPTOR + "/webhook"`."""
    achados: list[str] = []
    encontrou = False
    for no in ast.walk(funcao):
        if not isinstance(no, ast.Call) or not no.args:
            continue
        alvo = no.func
        chamada = alvo.attr if isinstance(alvo, ast.Attribute) else getattr(alvo, "id", "")
        if chamada not in CONSTRUTORES_HTTP:
            continue
        primeiro = no.args[0]
        if _e_sink_do_receptor(primeiro):
            encontrou = True
        elif isinstance(primeiro, ast.Name):
            continue  # referencia a uma requisicao ja construida e ja conferida acima
        else:
            achados.append(
                f"server.py: {nome} envia o envelope assinado para "
                f"`{ast.unparse(primeiro)}` — o unico destino permitido e' "
                f'`{NOME_BASE_SINK} + "{CAMINHO_SINK}"` (o webhook do receptor).'
            )
    if not encontrou:
        achados.append(
            f"server.py: {nome} assina um envelope mas a cerca nao viu a requisicao sair para "
            f'`{NOME_BASE_SINK} + "{CAMINHO_SINK}"` — o destino unico deixou de ser provavel.'
        )
    return achados


def executar(raiz: Path = REPO_ROOT) -> list[str]:
    return checar_ligacao_fora_de_dev(raiz) + checar_segredo_no_canal(raiz) + checar_cercas_no_codigo(raiz)


def main() -> int:
    achados = executar(REPO_ROOT)
    if achados:
        print("check_canal_simular: REPROVADO", file=sys.stderr)
        for a in achados:
            print(f"  - {a}", file=sys.stderr)
        return 1
    print(
        "check_canal_simular: ok — rota ligada so' em "
        f"{AMBIENTE_PERMITIDO}, segredo so' la', e as cercas (portao, faixa sintetica, uso na "
        f"requisicao e destino unico) estao no caminho da requisicao."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
