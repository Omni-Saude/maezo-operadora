"""BASE DE CONHECIMENTO DE SAUDE da Helena (ADR-0064, DL-0118) — atras da chave `MAEZO_HELENA_BASE_SAUDE`.

O QUE ESTE MODULO E'. O MECANISMO da base: o schema dos arquivos
`spec/conhecimento/saude/<categoria>/<id>.yaml`,
o carregador (que valida tudo e falha FECHADO), o indice por id, categoria e sinonimo normalizado, a busca
DETERMINISTICA (lexico e sinonimos, com tolerancia a acento e a erro leve de digitacao), a CLASSIFICACAO pelo
modelo restrita a uma lista FECHADA de ids candidatos (o modelo so' escolhe um id da lista ou "nenhum"; nunca
escreve conteudo) e a montagem das respostas. O CONTEUDO (os YAML) e' escrito por outra frente e entra por PR;
os gates de CI (`tests/unit/agents/test_helena_base_saude_conteudo.py`) reprovam o que sair das regras abaixo.

CHAVE DESLIGADA (o default) = a Helena de antes, byte a byte: `graph.py` so' chega aqui com
`self._base_saude`.

A REGRA DE OURO (ADR-0064): a Helena INFORMA, nao orienta. O texto que vai a pessoa e' o `texto` do arquivo,
literal, mais a linha de credito, o link de leitura (quando houver) e o aviso padrao. Nada de dose, nada de
"tome", nada de "nao e' grave", nada de diagnostico, nada de promessa de cobertura — e o gate de CI confere.

AS FONTES SAO UMA LISTA FECHADA (`spec/conhecimento/saude/FONTES.md`, ADR-0064): `classificar_fonte` diz se
uma URL e' de RESPONDER (Saude de A a Z, paginas web do INCA, Calendario Nacional de Vacinacao, atos da ANS),
de LINK (bula do paciente da ANVISA), so' de VOCABULARIO (TUSS, dados abertos da ANVISA, SIGTAP, CBO, CID-11)
ou PROIBIDA (BVS Dicas, Caderno de Atencao Basica 28, PDFs/cartilhas, OPAS/OMS, Fiocruz, Manual MSD, DeCS).
Fonte fora da lista e' reprovada: a lista e' fechada justamente para que ninguem acrescente uma "de passagem".

Nada aqui chama rede ou motor. A unica chamada externa e' `classificar_com_modelo`, que recebe o provedor
injetado por quem chama (`graph.py`) e devolve so' um id da lista fechada.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# --- Schema --------------------------------------------------------------------------------------------
#: Categoria -> prefixo do id. A lista e' FECHADA: categoria nova entra por PR, com ADR/DL.
CATEGORIAS: Final[Mapping[str, str]] = {
    "especialidade": "esp-",
    "medicamento": "med-",
    "exame": "exa-",
    "procedimento": "pro-",
    "cirurgia": "cir-",
    "material": "mat-",
    "condicao": "con-",
    "vacina": "vac-",
    "sintoma": "sin-",
}
CATEGORIA_SINTOMA: Final[str] = "sintoma"
LIMITE_DO_TEXTO: Final[int] = 700
TIPO_PROPRIO: Final[str] = "proprio"
TIPO_CITACAO_LITERAL: Final[str] = "citacao_literal"
STATUS_DE_REVISAO_DEV: Final[str] = "rascunho_dono_aprovado_dev"
APROVADO_POR_DEV: Final[str] = "dono (17/10/2026, dev)"
_ID: Final[re.Pattern[str]] = re.compile(
    r"^(?:esp|med|exa|pro|cir|mat|con|vac|sin)-[a-z0-9]+(?:-[a-z0-9]+)*$"
)
_SHA256: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Fonte(_Modelo):
    """De onde o texto saiu (ou em que se apoia). `url` https; `licenca` e `coletado_em` obrigatorios."""

    nome: str = Field(min_length=3, max_length=300)
    url: str = Field(min_length=12, max_length=500)
    licenca: str = Field(min_length=3, max_length=80)
    coletado_em: date
    sha256_trecho: str | None = None

    @field_validator("url")
    @classmethod
    def _https(cls, valor: str) -> str:
        partes = urlparse(valor)
        if partes.scheme != "https" or not partes.netloc:
            raise ValueError("a url da fonte tem de ser https://")
        return valor

    @field_validator("sha256_trecho")
    @classmethod
    def _sha(cls, valor: str | None) -> str | None:
        if valor is not None and not _SHA256.fullmatch(valor):
            raise ValueError("sha256_trecho tem de ser 64 caracteres hexadecimais minusculos")
        return valor


class Revisao(_Modelo):
    status: Literal["rascunho_dono_aprovado_dev"]
    autor: str = Field(min_length=2, max_length=80)
    aprovado_por: Literal["dono (17/10/2026, dev)"]
    revisao_medica: Literal["pendente", "aprovada"]
    revisar_ate: date


class Assunto(_Modelo):
    """Um arquivo da base. `sintoma` tem tres campos a mais (perguntas de alerta, sinais de alarme,
    populacao); as outras categorias nao podem te-los."""

    id: str
    categoria: Literal[
        "especialidade",
        "medicamento",
        "exame",
        "procedimento",
        "cirurgia",
        "material",
        "condicao",
        "vacina",
        "sintoma",
    ]
    titulo: str = Field(min_length=2, max_length=80)
    sinonimos: tuple[str, ...] = Field(min_length=1)
    texto: str = Field(min_length=20, max_length=LIMITE_DO_TEXTO)
    tipo_texto: Literal["proprio", "citacao_literal"]
    fontes: tuple[Fonte, ...] = Field(min_length=1)
    link_leitura: str | None = None
    revisao: Revisao
    perguntas_alerta: tuple[str, ...] | None = None
    sinais_alarme: tuple[str, ...] | None = None
    populacao: Literal["adulto", "crianca", "gestante", "todos"] | None = None

    @field_validator("texto")
    @classmethod
    def _texto(cls, valor: str) -> str:
        return " ".join(valor.split())

    @field_validator("sinonimos")
    @classmethod
    def _sinonimos(cls, valor: tuple[str, ...]) -> tuple[str, ...]:
        for s in valor:
            if not s or s != s.lower() or len(normalizar(s)) < 2:
                raise ValueError(f"sinonimo invalido (vazio ou com maiuscula): {s!r}")
        return valor

    @field_validator("link_leitura")
    @classmethod
    def _link(cls, valor: str | None) -> str | None:
        if valor is not None:
            partes = urlparse(valor)
            if partes.scheme != "https" or not partes.netloc:
                raise ValueError("link_leitura tem de ser https://")
        return valor

    @model_validator(mode="after")
    def _coerente(self) -> Assunto:
        if not _ID.fullmatch(self.id):
            raise ValueError(f"id fora do formato kebab com prefixo de categoria: {self.id!r}")
        if not self.id.startswith(CATEGORIAS[self.categoria]):
            raise ValueError(f"id {self.id!r} nao tem o prefixo da categoria {self.categoria!r}")
        extras = (self.perguntas_alerta, self.sinais_alarme, self.populacao)
        if self.categoria == CATEGORIA_SINTOMA:
            if not self.perguntas_alerta or not 1 <= len(self.perguntas_alerta) <= 3:
                raise ValueError("sintoma precisa de 1 a 3 perguntas_alerta")
            for p in self.perguntas_alerta:
                if not p.strip().endswith("?"):
                    raise ValueError(f"pergunta de alerta sem '?': {p!r}")
            if not self.sinais_alarme:
                raise ValueError("sintoma precisa de pelo menos um sinal_alarme")
            if self.populacao is None:
                raise ValueError("sintoma precisa de populacao")
        elif any(x is not None for x in extras):
            raise ValueError("perguntas_alerta, sinais_alarme e populacao so' existem em `sintoma`")
        return self


# --- Normalizacao --------------------------------------------------------------------------------------
def normalizar(texto: str) -> str:
    """Minusculas, sem acento, sem caractere de formato, pontuacao virando espaco, espacos colapsados."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_marca = "".join(c for c in decomposto if unicodedata.category(c) not in ("Mn", "Cf"))
    return " ".join(re.sub(r"[^\w\s]", " ", sem_marca.lower()).split())


# --- As fontes: a lista FECHADA ------------------------------------------------------------------------
CLASSE_RESPONDER: Final[str] = "responder"
CLASSE_LINK: Final[str] = "link"
CLASSE_VOCABULARIO: Final[str] = "vocabulario"
CLASSE_PROIBIDA: Final[str] = "proibida"
CLASSE_FORA_DA_LISTA: Final[str] = "fora_da_lista"
#: Referencia INTERNA da operadora (as tabelas de alarme `triage_redflag_*` e o catalogo no proprio repo):
#: rastreabilidade do mapeamento dos sinais de alarme, como o vocabulario. Nunca e' a fonte creditada e nunca
#: sustenta `citacao_literal`.
CLASSE_INTERNA: Final[str] = "interna"
#: ATO OFICIAL que nao esta' na lista de responder (resolucao do CFM sobre especialidades, lei federal de uma
#: profissao): APOIO de texto PROPRIO (a linha de credito o cita), nunca `citacao_literal`. Textos de atos
#: oficiais nao sao protegidos por direito autoral (Lei 9.610/1998, art. 8, IV).
CLASSE_ATO_OFICIAL: Final[str] = "ato_oficial"
#: O repositorio da operadora (unico endereco aceito como referencia interna).
_REPO_INTERNO: Final[str] = "/omni-saude/maezo-operadora/"


@dataclass(frozen=True)
class ClasseDaFonte:
    classe: str
    rotulo: str


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def classificar_fonte(nome: str, url: str) -> ClasseDaFonte:
    """A classe de uma fonte pela URL (e pelo nome, para TUSS/Caderno). A ORDEM importa: o que e' PROIBIDO
    e' conferido primeiro, depois vocabulario, depois as de responder. Tudo o que nao casa e' FORA DA LISTA
    (reprovado pelo gate)."""
    host = _host(url)
    caminho = (urlparse(url).path or "").lower()
    nome_n = normalizar(nome)
    junto = f"{host}{caminho}"
    # --- NAO USAR ---
    if "caderno" in nome_n or "cadernos_atencao_basica" in caminho or "caderno" in caminho:
        return ClasseDaFonte(CLASSE_PROIBIDA, "Caderno de Atencao Basica")
    if host == "bvsms.saude.gov.br" and "/saudelegis/" not in caminho:
        return ClasseDaFonte(CLASSE_PROIBIDA, "BVS (Dicas em Saude e publicacoes)")
    if "dicas" in caminho and "bvs" in junto:
        return ClasseDaFonte(CLASSE_PROIBIDA, "BVS Dicas em Saude")
    if host.endswith("paho.org") or (host.endswith("who.int") and host != "icd.who.int"):
        return ClasseDaFonte(CLASSE_PROIBIDA, "OPAS/OMS")
    if host.endswith("fiocruz.br"):
        return ClasseDaFonte(CLASSE_PROIBIDA, "Fiocruz")
    if "msdmanuals" in host:
        return ClasseDaFonte(CLASSE_PROIBIDA, "Manual MSD")
    if host.endswith("bvsalud.org") or "decs" in host:
        return ClasseDaFonte(CLASSE_PROIBIDA, "DeCS (licenca pendente)")
    if "cartilha" in caminho or "cartilha" in nome_n:
        return ClasseDaFonte(CLASSE_PROIBIDA, "cartilha (nao comercial)")
    do_inca_ou_ms = host in ("inca.gov.br", "saude.gov.br") or (
        host == "gov.br" and (caminho.startswith("/inca/") or caminho.startswith("/saude/"))
    )
    if do_inca_ou_ms and (caminho.endswith(".pdf") or "/arquivos/" in caminho or "/media/" in caminho):
        return ClasseDaFonte(CLASSE_PROIBIDA, "PDF do INCA/Ministerio da Saude (nao comercial)")
    # --- SO' VOCABULARIO ---
    if "tuss" in nome_n or "tuss" in caminho:
        return ClasseDaFonte(CLASSE_VOCABULARIO, "TUSS (ANS)")
    if host == "dados.anvisa.gov.br" or ("dados" in host and "anvisa" in junto):
        return ClasseDaFonte(CLASSE_VOCABULARIO, "dados abertos ANVISA")
    if "sigtap" in host:
        return ClasseDaFonte(CLASSE_VOCABULARIO, "SIGTAP")
    if host.startswith("cbo.") or "cbo" in nome_n.split():
        return ClasseDaFonte(CLASSE_VOCABULARIO, "CBO")
    if host == "icd.who.int":
        return ClasseDaFonte(CLASSE_VOCABULARIO, "CID-11 (OMS)")
    if host == "github.com" and caminho.startswith(_REPO_INTERNO):
        return ClasseDaFonte(CLASSE_INTERNA, "referencia interna da operadora (repo)")
    # --- LINK (bula do paciente ANVISA: reproducao pendente no juridico) ---
    if host.endswith("anvisa.gov.br") or caminho.startswith("/anvisa/"):
        return ClasseDaFonte(CLASSE_LINK, "bula do paciente (ANVISA)")
    # --- ATO OFICIAL (apoio de texto proprio) ---
    if host == "planalto.gov.br" and caminho.startswith("/ccivil_03/"):
        return ClasseDaFonte(CLASSE_ATO_OFICIAL, "lei federal (Planalto)")
    if host in ("sistemas.cfm.org.br", "portal.cfm.org.br", "cfm.org.br") and (
        caminho.startswith("/normas/") or host != "sistemas.cfm.org.br"
    ):
        return ClasseDaFonte(CLASSE_ATO_OFICIAL, "resolucao do CFM")
    # --- RESPONDER ---
    if host == "gov.br" and caminho.startswith("/saude/pt-br/assuntos/saude-de-a-a-z"):
        return ClasseDaFonte(CLASSE_RESPONDER, "Saude de A a Z (Ministerio da Saude)")
    if (
        host == "gov.br"
        and caminho.startswith("/saude/")
        and ("vacinacao" in caminho or "calendario" in caminho)
    ):
        return ClasseDaFonte(CLASSE_RESPONDER, "Calendario Nacional de Vacinacao (Ministerio da Saude)")
    if host == "gov.br" and caminho.startswith("/inca/"):
        return ClasseDaFonte(CLASSE_RESPONDER, "INCA (pagina web)")
    if host == "ans.gov.br" or (host == "gov.br" and caminho.startswith("/ans/")):
        return ClasseDaFonte(CLASSE_RESPONDER, "ANS (ato oficial: RN 566/2022, Rol)")
    if host == "bvsms.saude.gov.br" and "/saudelegis/ans/" in caminho:
        return ClasseDaFonte(CLASSE_RESPONDER, "ANS (ato oficial publicado no Saude Legis)")
    return ClasseDaFonte(CLASSE_FORA_DA_LISTA, "fora da lista fechada")


def licenca_permite_citacao(licenca: str) -> bool:
    """Citacao literal so' de CC BY-ND (gov.br) ou de ato oficial (ANS)."""
    n = normalizar(licenca)
    return "cc by nd" in n or "ato oficial" in n


# --- Termos proibidos no `texto` -----------------------------------------------------------------------
#: (rotulo, padrao sobre o texto NORMALIZADO). Dose/posologia, "tome", "pode tomar", minimizacao ("nao e'
#: grave", "e' normal"), diagnostico dirigido a pessoa e promessa de cobertura. O gate reprova; o runtime
#: tambem nao responde um assunto que case (defesa em profundidade).
TERMOS_PROIBIDOS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("dose/posologia", re.compile(r"\b(?:doses?|dosagem|posologia)\b")),
    (
        "quantidade de medicamento",
        re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|ml|g|ui|gotas?|comprimidos?|capsulas?)\b|\bmg\b"),
    ),
    ("frequencia de uso", re.compile(r"\bde \d+ em \d+ horas\b|\ba cada \d+ horas\b|\bvezes ao dia\b")),
    ("'tome'", re.compile(r"\btome\b|\btomem\b")),
    ("'pode tomar'", re.compile(r"\bpode(?:m)? tomar\b|\bdeve(?:m)? tomar\b|\btomar\b")),
    ("'nao e grave'", re.compile(r"\bnao e grave\b|\bnao eh grave\b")),
    ("'e normal'", re.compile(r"\be normal\b|\beh normal\b")),
    ("minimizacao", re.compile(r"\bnao se preocupe\b|\bnao precisa (?:ir|procurar|de) (?:ao )?medico\b")),
    ("diagnostico", re.compile(r"(?<!\bse )\bvoce (?:tem|esta com|deve estar com)\b|\bo seu diagnostico\b")),
    (
        "promessa de cobertura",
        re.compile(
            r"\b(?:o|seu) plano cobre\b|\bcoberto pelo (?:seu )?plano\b|\bcobertura garantida\b"
            r"|\btem cobertura\b|\besta coberto\b"
        ),
    ),
)


def termos_proibidos_no_texto(texto: str) -> list[str]:
    n = normalizar(texto)
    return [rotulo for rotulo, padrao in TERMOS_PROIBIDOS if padrao.search(n)]


def problemas_de_politica(assunto: Assunto, *, hoje: date) -> list[str]:
    """Tudo o que reprova um assunto pelas regras de manutencao do ADR-0064 (alem do schema). Lista vazia =
    pode ir a pessoa."""
    problemas: list[str] = []
    if assunto.revisao.revisar_ate < hoje:
        problemas.append(f"revisar_ate vencido ({assunto.revisao.revisar_ate.isoformat()})")
    classes = []
    for fonte in assunto.fontes:
        classe = classificar_fonte(fonte.nome, fonte.url)
        classes.append(classe.classe)
        if classe.classe == CLASSE_PROIBIDA:
            problemas.append(f"fonte da lista NAO USAR: {classe.rotulo} ({fonte.url})")
        elif classe.classe == CLASSE_FORA_DA_LISTA:
            problemas.append(f"fonte fora da lista fechada: {fonte.url}")
        if fonte.coletado_em > hoje:
            problemas.append(f"coletado_em no futuro ({fonte.coletado_em.isoformat()})")
        if assunto.tipo_texto == TIPO_CITACAO_LITERAL and classe.classe in (
            CLASSE_RESPONDER,
            CLASSE_LINK,
            CLASSE_INTERNA,
            CLASSE_ATO_OFICIAL,
        ):
            if classe.classe in (CLASSE_INTERNA, CLASSE_ATO_OFICIAL):
                problemas.append(f"citacao literal de {classe.rotulo} (so' apoio de texto proprio)")
            elif classe.classe == CLASSE_LINK:
                problemas.append("citacao literal de bula ANVISA (por ora so' LINK: licenca pendente)")
            elif not licenca_permite_citacao(fonte.licenca):
                problemas.append(
                    f"citacao literal com licenca que nao e' CC BY-ND/ato oficial: {fonte.licenca!r}"
                )
    apoio_do_proprio = {CLASSE_LINK, CLASSE_ATO_OFICIAL}
    if assunto.categoria == CATEGORIA_SINTOMA:
        # O texto de orientacao do sintoma (SEM alarme) e' da operadora, mapeado das tabelas de alarme.
        apoio_do_proprio.add(CLASSE_INTERNA)
    if CLASSE_RESPONDER not in classes and not (
        assunto.tipo_texto == TIPO_PROPRIO and apoio_do_proprio.intersection(classes)
    ):
        problemas.append(
            "nenhuma fonte de apoio (responder; texto proprio aceita bula e ato oficial; "
            "sintoma, a tabela interna)"
        )
    if assunto.link_leitura is not None:
        classe_link = classificar_fonte("", assunto.link_leitura)
        if classe_link.classe not in (CLASSE_RESPONDER, CLASSE_LINK):
            problemas.append(f"link_leitura fora das fontes de responder/link: {assunto.link_leitura}")
    for rotulo in termos_proibidos_no_texto(assunto.texto):
        problemas.append(f"termo proibido no texto: {rotulo}")
    return problemas


def fonte_creditada(assunto: Assunto) -> Fonte | None:
    """A fonte que vai na linha de credito: a PRIMEIRA de responder; senao a de ato oficial; senao a bula.
    `None` = so' referencia interna (sintoma): o credito diz apenas que o texto e' da operadora."""
    for classe_alvo in (CLASSE_RESPONDER, CLASSE_ATO_OFICIAL, CLASSE_LINK):
        for fonte in assunto.fontes:
            if classificar_fonte(fonte.nome, fonte.url).classe == classe_alvo:
                return fonte
    return None


#: O nome da fonte na linha de credito, CURTO (regua de tom): ate' este numero de palavras vai o `nome` do
#: arquivo; acima, o rotulo fixo da classe.
_MAX_PALAVRAS_DO_NOME_NO_CREDITO: Final[int] = 10
_ROTULO_DE_CREDITO: Final[Mapping[str, str]] = {
    CLASSE_RESPONDER: "fonte oficial do Ministério da Saúde, do INCA ou da ANS",
    CLASSE_ATO_OFICIAL: "ato oficial",
    CLASSE_LINK: "a bula do paciente (ANVISA)",
}


def _nome_no_credito(fonte: Fonte) -> str:
    nome = fonte.nome.rstrip(". ")
    if len(nome.split()) <= _MAX_PALAVRAS_DO_NOME_NO_CREDITO:
        return nome
    return _ROTULO_DE_CREDITO.get(classificar_fonte(fonte.nome, fonte.url).classe, "fonte oficial")


# --- O carregador ---------------------------------------------------------------------------------------
class BaseSaudeInvalidaError(ValueError):
    """A base nao carregou: schema invalido, arquivo fora do lugar, id/sinonimo duplicado. FALHA FECHADA."""

    def __init__(self, erros: Sequence[str]) -> None:
        self.erros = tuple(erros)
        super().__init__(f"base de saude invalida ({len(self.erros)} erro(s)): " + "; ".join(self.erros[:5]))


@dataclass(frozen=True)
class BaseSaude:
    assuntos: Mapping[str, Assunto]
    #: sinonimo NORMALIZADO (e o titulo normalizado) -> id
    por_sinonimo: Mapping[str, str]
    por_categoria: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.assuntos)

    def get(self, assunto_id: str) -> Assunto | None:
        return self.assuntos.get(assunto_id)

    def sintomas(self) -> tuple[Assunto, ...]:
        return tuple(self.assuntos[i] for i in self.por_categoria.get(CATEGORIA_SINTOMA, ()))


BASE_VAZIA: Final[BaseSaude] = BaseSaude(assuntos={}, por_sinonimo={}, por_categoria={})


def _ler_yaml(caminho: Path) -> Any:
    with caminho.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def carregar_base(raiz: Path) -> BaseSaude:
    """Carrega e VALIDA a base inteira de `raiz` (`spec/conhecimento/saude`). Qualquer erro reprova tudo
    (`BaseSaudeInvalidaError`, com a lista de erros). Diretorio sem nenhum YAML = base vazia (valida)."""
    erros: list[str] = []
    assuntos: dict[str, Assunto] = {}
    por_sinonimo: dict[str, str] = {}
    por_categoria: dict[str, list[str]] = {}
    if not raiz.is_dir():
        raise BaseSaudeInvalidaError([f"diretorio da base nao existe: {raiz}"])
    for caminho in sorted(raiz.rglob("*")):
        if caminho.is_dir() or caminho.suffix.lower() not in (".yaml", ".yml"):
            continue
        relativo = caminho.relative_to(raiz).as_posix()
        partes = caminho.relative_to(raiz).parts
        if len(partes) != 2 or partes[0] not in CATEGORIAS:
            erros.append(f"{relativo}: fora de <categoria>/<id>.yaml (categorias: {', '.join(CATEGORIAS)})")
            continue
        try:
            dados = _ler_yaml(caminho)
        except (OSError, yaml.YAMLError) as exc:
            erros.append(f"{relativo}: YAML ilegivel ({type(exc).__name__})")
            continue
        if not isinstance(dados, dict):
            erros.append(f"{relativo}: o arquivo nao e' um mapeamento YAML")
            continue
        try:
            assunto = Assunto.model_validate(dados)
        except ValidationError as exc:
            detalhes = "; ".join(
                f"{'.'.join(str(x) for x in e['loc']) or '-'}: {e['msg']}" for e in exc.errors()[:6]
            )
            erros.append(f"{relativo}: schema invalido ({detalhes})")
            continue
        if assunto.categoria != partes[0]:
            erros.append(f"{relativo}: categoria {assunto.categoria!r} no diretorio {partes[0]!r}")
            continue
        if caminho.stem != assunto.id:
            erros.append(f"{relativo}: o nome do arquivo tem de ser o id ({assunto.id}.yaml)")
            continue
        if assunto.id in assuntos:
            erros.append(f"{relativo}: id duplicado {assunto.id!r}")
            continue
        assuntos[assunto.id] = assunto
        por_categoria.setdefault(assunto.categoria, []).append(assunto.id)
        for termo in (*assunto.sinonimos, assunto.titulo):
            chave = normalizar(termo)
            dono = por_sinonimo.get(chave)
            if dono is not None and dono != assunto.id:
                erros.append(f"{relativo}: sinonimo {termo!r} ja' pertence a {dono!r}")
                continue
            por_sinonimo[chave] = assunto.id
    if erros:
        raise BaseSaudeInvalidaError(erros)
    return BaseSaude(
        assuntos=assuntos,
        por_sinonimo=por_sinonimo,
        por_categoria={k: tuple(v) for k, v in por_categoria.items()},
    )


def raiz_padrao() -> Path:
    """`spec/conhecimento/saude` resolvido pelo mesmo ponto unico de `spec/` (`resolve_spec_dir`)."""
    from maezo.agents import resolve_spec_dir  # import tardio: evita ciclo no import do pacote

    return resolve_spec_dir() / "conhecimento" / "saude"


def base_aplicavel(base: BaseSaude, *, hoje: date) -> BaseSaude:
    """So' os assuntos SEM problema de politica hoje (revisar_ate vencido, fonte proibida, termo proibido):
    defesa em profundidade do runtime. O CI ja' reprova esses casos; aqui eles simplesmente nao respondem."""
    validos = {i: a for i, a in base.assuntos.items() if not problemas_de_politica(a, hoje=hoje)}
    if len(validos) == len(base.assuntos):
        return base
    return BaseSaude(
        assuntos=validos,
        por_sinonimo={k: v for k, v in base.por_sinonimo.items() if v in validos},
        por_categoria={c: tuple(i for i in ids if i in validos) for c, ids in base.por_categoria.items()},
    )


# --- A pergunta: informativa, resultado pessoal, queixa -----------------------------------------------
#: As formas de PERGUNTA INFORMATIVA ("o que e' PSA?", "pra que serve a dipirona?"), sobre o texto
#: normalizado.
_PERGUNTA_INFORMATIVA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:o que (?:e|eh|sao|significa|significam|quer dizer|faz|fazem)"
    r"|o q (?:e|eh)|oque (?:e|eh)|que e|que eh"
    r"|(?:pra|para|serve pra|serve para) (?:que|q) (?:serve|servem|e|eh|usa|usam|e usad[oa])"
    r"|serve (?:pra|para) (?:que|q)|qual (?:a|e a) (?:funcao|finalidade)"
    r"|como funciona|me explica|me explique|explica o que|o que voce sabe sobre|me fala sobre"
    r"|informac\w* sobre)\b"
)
#: O que sobra do comeco da pergunta (para isolar o ASSUNTO): verbos, artigos e palavras de pergunta.
_PREFIXOS_DA_PERGUNTA: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:oi|ola|bom dia|boa tarde|boa noite|helena|por favor|me diz|me diga|queria saber|quero saber"
    r"|gostaria de saber|voce sabe|sabe me dizer|uma duvida|duvida)\s+)*"
)
_PALAVRAS_DE_PERGUNTA: Final[frozenset[str]] = frozenset(
    {
        *("o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das", "que", "q"),
        *("e", "eh", "sao", "significa", "significam", "quer", "dizer", "faz", "fazem", "serve", "servem"),
        *("pra", "para", "usa", "usam", "usado", "usada", "como", "funciona", "me", "explica", "explique"),
        *("qual", "funcao", "finalidade", "oque", "sobre", "informacao", "informacoes", "voce", "sabe"),
        *("fala", "isso", "esse", "essa", "este", "esta", "exame", "remedio", "medicamento", "cirurgia"),
        *("procedimento", "doenca", "vacina", "especialidade", "medico", "chamado", "chamada", "tal"),
        *("aquele", "aquela", "e", "ai", "ne", "tipo"),
    }
)
#: RESULTADO ou MEDIDA PESSOAL ("meu PSA deu alto", "minha pressao esta 15 por 9", "o resultado do meu
#: exame"): fala de um numero/resultado SEU, nao de uma duvida geral.
_RESULTADO_PESSOAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:meu|minha|meus|minhas)\b[^.?!]{0,60}"
    r"\b(?:deu|deram|veio|vieram|saiu|sairam|esta|estao|ta|tao|resultado|resultados|alto|alta|altos|altas"
    r"|baixo|baixa|alterad\w*|fora|parametros?|referencia|aument\w*|subiu|\d+)\b"
    r"|\bresultado do meu\b|\bmeu resultado\b|\bmeu exame\b"
)
#: Primeira pessoa / paciente: com ela, um sintoma e' QUEIXA (nao duvida geral).
_PRIMEIRA_PESSOA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:estou|to|tou|estava|tenho|tive|sinto|senti|sentindo|meu|minha|me|comigo|eu)\b"
)


def pergunta_informativa(plano: str) -> bool:
    return bool(_PERGUNTA_INFORMATIVA.search(plano))


def resultado_pessoal(plano: str) -> bool:
    return bool(_RESULTADO_PESSOAL.search(plano))


def primeira_pessoa(plano: str) -> bool:
    return bool(_PRIMEIRA_PESSOA.search(plano))


def assunto_da_pergunta(plano: str) -> str:
    """O pedaco da mensagem que e' o ASSUNTO ("pra que serve a dipirona" -> "dipirona")."""
    resto = _PREFIXOS_DA_PERGUNTA.sub("", plano)
    m = _PERGUNTA_INFORMATIVA.search(resto)
    if m is not None:
        resto = resto[m.end() :]
    palavras = resto.split()
    while palavras and palavras[0] in _PALAVRAS_DE_PERGUNTA:
        palavras.pop(0)
    while palavras and palavras[-1] in _PALAVRAS_DE_PERGUNTA:
        palavras.pop()
    return " ".join(palavras)


# --- A busca deterministica -----------------------------------------------------------------------------
def _distancia(a: str, b: str, teto: int) -> int:
    """Levenshtein com corte (`teto + 1` assim que passa do teto)."""
    if abs(len(a) - len(b)) > teto:
        return teto + 1
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        atual = [i]
        menor = i
        for j, cb in enumerate(b, start=1):
            custo = 0 if ca == cb else 1
            valor = min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + custo)
            atual.append(valor)
            menor = min(menor, valor)
        if menor > teto:
            return teto + 1
        anterior = atual
    return anterior[-1]


def _teto_de_erro(termo: str) -> int:
    """Erro de digitacao LEVE: termos curtos (siglas como PSA) so' casam exatos."""
    n = len(termo.replace(" ", ""))
    if n < 5:
        return 0
    if n < 9:
        return 1
    return 2


def _ngramas(palavras: Sequence[str], n: int) -> Iterable[str]:
    for i in range(len(palavras) - n + 1):
        yield " ".join(palavras[i : i + n])


@dataclass(frozen=True)
class Busca:
    """`achado`: o id escolhido deterministicamente (ou `None`); `candidatos`: ids para o modelo escolher
    quando a busca ficou AMBIGUA ou so' achou parecidos (lista fechada, no maximo 5)."""

    achado: str | None
    candidatos: tuple[str, ...] = ()
    como: str = ""


_MAX_CANDIDATOS: Final[int] = 5


def buscar(base: BaseSaude, texto: str, *, categorias: Iterable[str] | None = None) -> Busca:
    """A busca DETERMINISTICA: (1) o assunto isolado da pergunta e' um sinonimo -> achado; (2) sinonimos
    que aparecem inteiros na mensagem -> o MAIS LONGO (empate entre ids diferentes = candidatos); (3) erro
    leve de digitacao (distancia de edicao 1 ou 2, nunca em sigla curta) -> achado se unico, senao
    candidatos. `categorias` restringe (ex.: so' `sintoma`)."""
    if not base.assuntos:
        return Busca(None)
    permitidas = set(categorias) if categorias is not None else None

    def _ok(assunto_id: str) -> bool:
        return permitidas is None or base.assuntos[assunto_id].categoria in permitidas

    plano = normalizar(texto)
    alvo = assunto_da_pergunta(plano)
    if alvo and alvo in base.por_sinonimo and _ok(base.por_sinonimo[alvo]):
        return Busca(base.por_sinonimo[alvo], como="exato")
    palavras = plano.split()
    contidos: dict[str, int] = {}
    for termo, assunto_id in base.por_sinonimo.items():
        if not _ok(assunto_id):
            continue
        if re.search(rf"(?<!\w){re.escape(termo)}(?!\w)", plano):
            contidos[assunto_id] = max(contidos.get(assunto_id, 0), len(termo))
    if contidos:
        maior = max(contidos.values())
        melhores = sorted(i for i, n in contidos.items() if n == maior)
        if len(melhores) == 1:
            return Busca(melhores[0], como="contido")
        return Busca(None, tuple(melhores[:_MAX_CANDIDATOS]), como="ambiguo")
    parecidos: dict[str, int] = {}
    for termo, assunto_id in base.por_sinonimo.items():
        if not _ok(assunto_id):
            continue
        teto = _teto_de_erro(termo)
        if teto == 0:
            continue
        n = len(termo.split())
        for grama in _ngramas(palavras, n):
            d = _distancia(grama, termo, teto)
            if d <= teto:
                parecidos[assunto_id] = min(parecidos.get(assunto_id, teto + 1), d)
    if not parecidos:
        return Busca(None)
    ordem = sorted(parecidos, key=lambda i: (parecidos[i], i))
    if len(ordem) == 1:
        return Busca(ordem[0], como="digitacao")
    return Busca(None, tuple(ordem[:_MAX_CANDIDATOS]), como="ambiguo")


# --- A classificacao pelo modelo, RESTRITA a uma lista fechada -------------------------------------------
NENHUM: Final[str] = "nenhum"
PROMPT_CLASSIFICACAO_VERSAO: Final[str] = "base-saude-classifica-v1"


def prompt_de_classificacao(base: BaseSaude, candidatos: Sequence[str], bloco_da_mensagem: str) -> str:
    """O prompt do modelo: escolhe UM id da lista ou "nenhum". O modelo nao escreve conteudo nenhum."""
    linhas = [f'- "{i}": {base.assuntos[i].titulo}' for i in candidatos if i in base.assuntos]
    return (
        f"[{PROMPT_CLASSIFICACAO_VERSAO}] Voce classifica o ASSUNTO de uma pergunta de saude. "
        'Escolha UM id da lista abaixo que seja exatamente o assunto da mensagem, ou "nenhum" se nenhum for. '
        "Nao responda a pergunta, nao explique, nao escreva texto. "
        'Responda SO com JSON: {"id": "<id da lista ou nenhum>"}.\n'
        "Lista fechada de ids:\n" + "\n".join(linhas) + "\n" + bloco_da_mensagem
    )


def ler_classificacao(resposta: str, candidatos: Sequence[str]) -> str | None:
    """O id escolhido, SO' se estiver na lista fechada; qualquer outra coisa (texto, id de fora, JSON
    quebrado, "nenhum") -> `None`."""
    texto = (resposta or "").strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return None
    try:
        dados = json.loads(texto[inicio : fim + 1])
    except ValueError:
        return None
    if not isinstance(dados, dict):
        return None
    escolhido = dados.get("id")
    if not isinstance(escolhido, str) or escolhido == NENHUM or escolhido not in candidatos:
        return None
    return escolhido


# --- As respostas -----------------------------------------------------------------------------------------
#: O aviso padrao do ADR-0064 (texto do dono, 17/10/2026).
AVISO_PADRAO: Final[str] = (
    "Esta informação é geral e não substitui avaliação médica. "
    "Se quiser, posso te passar para a equipe de saúde."
)
RESPOSTA_NAO_ENCONTRADO: Final[str] = (
    "Não tenho uma informação aprovada sobre isso por aqui. "
    "Para saber mais, converse com o seu médico ou procure o Saúde de A a Z, do Ministério da Saúde. "
    "Se quiser, posso te passar para a equipe de saúde."
)
RESPOSTA_RESULTADO_NAO_ENCONTRADO: Final[str] = (
    "Não tenho uma informação aprovada sobre isso por aqui. "
    "Quem pode avaliar o seu resultado é o seu médico: leve o resultado a ele. "
    "Se quiser, posso te passar para a equipe de saúde."
)
FRASE_LEVE_AO_MEDICO: Final[str] = "Quem pode avaliar o seu resultado é o seu médico: leve o resultado a ele."
_ABERTURA_RESULTADO: Final[str] = "Posso te passar a informação geral sobre"


def linha_de_credito(assunto: Assunto) -> str:
    fonte = fonte_creditada(assunto)
    if fonte is None:
        return "Texto da operadora."
    if assunto.tipo_texto == TIPO_CITACAO_LITERAL:
        return f"Fonte: {_nome_no_credito(fonte)}."
    return f"Texto da operadora com base em {_nome_no_credito(fonte)}."


def _bloco(assunto: Assunto) -> list[str]:
    partes = [assunto.texto, linha_de_credito(assunto)]
    if assunto.link_leitura:
        partes.append(f"Para ler mais: {assunto.link_leitura}")
    return partes


def resposta_informativa(assunto: Assunto) -> str:
    """(i) Pergunta informativa: texto + credito + link + aviso."""
    return "\n".join([*_bloco(assunto), AVISO_PADRAO])


def resposta_de_resultado_pessoal(assunto: Assunto) -> str:
    """(ii) Resultado/queixa pessoal: o texto GERAL do assunto + "leve o resultado ao seu medico" + oferta."""
    return "\n".join(
        [f"{_ABERTURA_RESULTADO} {assunto.titulo}.", *_bloco(assunto), FRASE_LEVE_AO_MEDICO, AVISO_PADRAO]
    )


def texto_final_do_sintoma_com_orientacao(eco: str, assunto: Assunto) -> str:
    """(iv) O "nao" as perguntas de alerta de um sintoma da base: o texto de orientacao do arquivo (SEM
    alarme), o credito e as mesmas opcoes 1/2 da Helena v4 (palavra por palavra) com o 192."""
    return "\n".join(
        [
            f"Entendi: {eco}.",
            assunto.texto,
            linha_de_credito(assunto),
            "Eu não avalio sintomas, mas te ajudo com o caminho. "
            "Você prefere: 1) saber onde marcar consulta pelo plano (clínico geral ou pediatra: "
            "até 7 dias úteis pela ANS); ou 2) falar com a nossa equipe de saúde? "
            "Se piorar, procure um pronto-atendimento; se for grave, ligue 192.",
        ]
    )


def textos_da_base(base: BaseSaude) -> frozenset[str]:
    """Os textos de assunto (o `texto` de cada arquivo): `e_texto_da_base` confere contra eles."""
    return frozenset(a.texto for a in base.assuntos.values())


def e_texto_da_base(texto: object, base: BaseSaude) -> bool:
    """O texto que `classify` deixou pronto e `inform` pode enviar como esta': um texto fixo deste modulo, ou
    uma resposta montada daqui (contem o `texto` LITERAL de um assunto e a linha de credito dele)."""
    if not isinstance(texto, str):
        return False
    if texto in (RESPOSTA_NAO_ENCONTRADO, RESPOSTA_RESULTADO_NAO_ENCONTRADO):
        return True
    for assunto in base.assuntos.values():
        if assunto.texto in texto and linha_de_credito(assunto) in texto:
            return True
    return False


# --- Sintoma: sinais de alarme da base --------------------------------------------------------------------
def _contem(plano: str, termo: str) -> bool:
    return bool(termo) and re.search(rf"(?<!\w){re.escape(termo)}(?!\w)", plano) is not None


def sintomas_citados(base: BaseSaude, texto: str) -> tuple[Assunto, ...]:
    """Os arquivos `sin-*` cujo titulo ou algum sinonimo aparece INTEIRO na mensagem (normalizada)."""
    plano = normalizar(texto)
    return tuple(
        a for a in base.sintomas() if any(_contem(plano, normalizar(t)) for t in (*a.sinonimos, a.titulo))
    )


def sinais_de_alarme(base: BaseSaude, texto: str) -> tuple[str, ...]:
    """Os termos de alarme (normalizados) dos arquivos `sin-*` DESTA queixa (`sintomas_citados`): a lista do
    arquivo do sintoma, como pede o ADR-0064 — nao a uniao de todos (um "dor muito forte" que e' alarme na
    dor nas costas nao vira alarme da dor de cabeca). Sem arquivo citado, nenhum: a tabela de alarme e o
    lexico de emergencia continuam valendo (rodam antes)."""
    termos = {normalizar(t) for a in sintomas_citados(base, texto) for t in (a.sinais_alarme or ())}
    return tuple(sorted(t for t in termos if t))


def alarme_no_texto(base: BaseSaude, texto: str) -> bool:
    plano = normalizar(texto)
    return any(_contem(plano, t) for t in sinais_de_alarme(base, texto))


__all__ = [
    "APROVADO_POR_DEV",
    "AVISO_PADRAO",
    "BASE_VAZIA",
    "CATEGORIAS",
    "CATEGORIA_SINTOMA",
    "CLASSE_FORA_DA_LISTA",
    "CLASSE_ATO_OFICIAL",
    "CLASSE_INTERNA",
    "CLASSE_LINK",
    "CLASSE_PROIBIDA",
    "CLASSE_RESPONDER",
    "CLASSE_VOCABULARIO",
    "FRASE_LEVE_AO_MEDICO",
    "LIMITE_DO_TEXTO",
    "NENHUM",
    "PROMPT_CLASSIFICACAO_VERSAO",
    "RESPOSTA_NAO_ENCONTRADO",
    "RESPOSTA_RESULTADO_NAO_ENCONTRADO",
    "STATUS_DE_REVISAO_DEV",
    "TERMOS_PROIBIDOS",
    "Assunto",
    "BaseSaude",
    "BaseSaudeInvalidaError",
    "Busca",
    "ClasseDaFonte",
    "Fonte",
    "Revisao",
    "alarme_no_texto",
    "assunto_da_pergunta",
    "base_aplicavel",
    "buscar",
    "carregar_base",
    "classificar_fonte",
    "e_texto_da_base",
    "fonte_creditada",
    "ler_classificacao",
    "licenca_permite_citacao",
    "linha_de_credito",
    "normalizar",
    "pergunta_informativa",
    "primeira_pessoa",
    "problemas_de_politica",
    "prompt_de_classificacao",
    "raiz_padrao",
    "resposta_de_resultado_pessoal",
    "resposta_informativa",
    "resultado_pessoal",
    "sinais_de_alarme",
    "sintomas_citados",
    "termos_proibidos_no_texto",
    "texto_final_do_sintoma_com_orientacao",
    "textos_da_base",
]
