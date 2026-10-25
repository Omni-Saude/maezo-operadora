"""PROTOCOLOS CLINICOS da Helena como DADOS (decisao do dono, 25/10/2026): carregador e validador ESTRITO.

"O MODELO conversa, o PROTOCOLO diz o que fazer, o CODIGO garante a seguranca." Cada protocolo e' um YAML
(`spec/agents/helena/pacote/protocolos/<tema>/<id>.yaml`, esquema do `MODELO.md` do levantamento) com as
perguntas de alerta em linguagem leiga, o DESTINO de cada sinal e o destino quando nenhum sinal aparece. O
mapa queixa -> especialista e as frases dele ficam em `especialista/`.

ONDE OS ARQUIVOS MORAM E POR QUE. Dentro de `spec/agents/helena/pacote/`: (1) `spec/agents` ja' vai na
imagem (force-include do pyproject + COPY do deploy/Dockerfile), entao o carregador acha os arquivos no boot
sem mudar caminho protegido; (2) o publicador do pacote (`scripts/agentes/publicar_pacote.py`) ja' leva todo
arquivo do diretorio, entao uma revisao medica dos textos pode, numa etapa seguinte, valer sem imagem nova.
O validador do pacote (`agents/helena/pacote.py`) NAO olha estes arquivos: um protocolo ruim nunca recusa o
pacote inteiro.

FALHA FECHADA POR PROTOCOLO: um arquivo invalido e' EXCLUIDO e registrado (`helena_protocolo_invalido`, so'
o id e o motivo), e os demais carregam. Sem nenhum protocolo de triagem valido, o catalogo e' `None` e a
Helena segue pelo caminho de antes (v4/base).

Regras (todas no codigo, testadas em `tests/unit/conhecimento/test_protocolos.py`):
- destinos FECHADOS (`DESTINOS`); `destino_se_nenhum_sinal` tambem;
- no maximo 4 perguntas de alerta, cada uma com `sinal`, `destino_se_sim` e `fonte`;
- `publico` no conjunto fechado (`PUBLICOS`);
- nenhum link nos textos que a pessoa le' (e nenhuma URL em lugar nenhum), nenhuma dose, nenhum prazo da ANS
  ("dias uteis", "ANS");
- `revisao_medica: pendente` e' aceito (rascunho de dev, decisao do dono);
- ids unicos (e o nome do arquivo e' o id);
- as entradas do mapa citam especialidades conhecidas (nome do CFM com nome leigo em `textos.yaml` e, quando
  ha', o arquivo `esp-*` da base de saude).
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = structlog.get_logger(__name__)

#: Os destinos (vocabulario FECHADO do MODELO.md), do MAIS grave para o MENOS grave.
DESTINOS: Final[tuple[str, ...]] = (
    "LIGAR_192_AGORA",
    "CVV_188_E_192",
    "PRONTO_SOCORRO_AGORA",
    "ATENDIMENTO_HOJE",
    "CONSULTA_EM_BREVE",
    "CONSULTA_ROTINA",
)
GRAVIDADE: Final[Mapping[str, int]] = {d: len(DESTINOS) - i for i, d in enumerate(DESTINOS)}
Destino = Literal[
    "LIGAR_192_AGORA",
    "CVV_188_E_192",
    "PRONTO_SOCORRO_AGORA",
    "ATENDIMENTO_HOJE",
    "CONSULTA_EM_BREVE",
    "CONSULTA_ROTINA",
]
PUBLICOS: Final[tuple[str, ...]] = (
    "adulto",
    "crianca_0_2m",
    "crianca_2m_5a",
    "crianca_5_12a",
    "adolescente",
    "gestante",
    "puerpera",
)
PUBLICOS_DE_CRIANCA: Final[frozenset[str]] = frozenset(
    {"crianca_0_2m", "crianca_2m_5a", "crianca_5_12a", "adolescente"}
)
Publico = Literal[
    "adulto", "crianca_0_2m", "crianca_2m_5a", "crianca_5_12a", "adolescente", "gestante", "puerpera"
]
#: tema -> prefixos de id permitidos. A prevencao e' tabela de referencia (sem perguntas de alerta).
TEMAS: Final[Mapping[str, tuple[str, ...]]] = {
    "triagem_adultos": ("tri-adulto-", "uni-"),
    "triagem_criancas": ("tri-crianca-", "uni-"),
    "gestacao": ("gest-",),
    "saude_mental": ("mental-",),
    "cronicos": ("cron-",),
    "prevencao": ("prev-",),
}
TEMAS_DE_TRIAGEM: Final[frozenset[str]] = frozenset(TEMAS) - {"prevencao"}
#: Os protocolos de "alerta universal": sem protocolo proprio para a queixa, vale este.
UNIVERSAL_ADULTO: Final[str] = "uni-alerta-universal"
UNIVERSAL_CRIANCA: Final[str] = "uni-alerta-universal-crianca"

MAX_PERGUNTAS: Final[int] = 4
_ID: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
#: URL de verdade (em QUALQUER campo, inclusive fontes).
_URL: Final[re.Pattern[str]] = re.compile(r"(?i)\bhttps?://|\bwww\.")
#: Link em texto que a pessoa le' (o mesmo padrao da trava de saida `sem_link`).
_LINK: Final[re.Pattern[str]] = re.compile(
    r"(?i)\bhttps?://\S+|\bwww\.\S+|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|br|org|net|gov|saude|info|io)"
    r"(?:\.br)?\b(?:/\S*)?"
)
#: Prazo da ANS (regra do dono: nenhum texto da Helena fala em prazo).
_PRAZO_ANS: Final[re.Pattern[str]] = re.compile(r"\bANS\b|(?i:\bdias? [uú]teis\b)")
#: Dose de remedio (quantidade com unidade, ou intervalo de horas). "Nao tome dose dobrada" NAO e' dose.
_DOSE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|ml|gotas?|comprimidos?|c[aá]psulas?|ui|g)\b"
    r"|(?i:\bde \d+ em \d+ horas\b)"
)
#: As chaves de TEXTO que a pessoa le' (o resto e' referencia interna: fontes, licenca).
_CHAVES_LIDAS: Final[frozenset[str]] = frozenset(
    {"titulo", "pergunta", "orientacao_final", "texto", "queixa", "resposta", "antes_de_indicar"}
)


class ProtocoloInvalidoError(ValueError):
    """Um arquivo de protocolo fora das regras (o motivo nunca traz o texto do arquivo)."""


def _strings(dado: Any, chave: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(dado, str):
        yield chave, dado
    elif isinstance(dado, Mapping):
        for k, v in dado.items():
            yield from _strings(v, str(k))
    elif isinstance(dado, list | tuple):
        for v in dado:
            yield from _strings(v, chave)


def problemas_de_texto(dado: Any) -> list[str]:
    """As regras de texto sobre o documento INTEIRO: URL em qualquer campo; link, dose e prazo da ANS nos
    textos lidos (e prazo da ANS em qualquer campo). Devolve so' rotulos (campo e regra)."""
    erros: list[str] = []
    for chave, texto in _strings(dado):
        if _URL.search(texto):
            erros.append(f"{chave}: url")
        if _PRAZO_ANS.search(texto):
            erros.append(f"{chave}: prazo_ans")
        if chave in _CHAVES_LIDAS or chave == "como_a_pessoa_fala":
            if _LINK.search(texto):
                erros.append(f"{chave}: link")
            if _DOSE.search(texto):
                erros.append(f"{chave}: dose")
    return sorted(set(erros))


class _Estrito(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PerguntaDeAlerta(_Estrito):
    pergunta: str = Field(min_length=10, max_length=400)
    sinal: str = Field(pattern=r"^[a-z0-9_]+$", max_length=80)
    destino_se_sim: Destino
    fonte: str = Field(min_length=3)


class Especialista(_Estrito):
    primeiro: str = Field(min_length=3)
    depois: str = Field(min_length=3)
    fonte: str = Field(min_length=3)


class Fonte(_Estrito):
    nome: str = Field(min_length=3)
    secao: str = ""


class _Comum(_Estrito):
    id: str
    tema: str
    titulo: str = Field(min_length=3, max_length=120)
    como_a_pessoa_fala: tuple[str, ...] = Field(min_length=1, max_length=20)
    especialista: Especialista
    orientacao_final: str = Field(min_length=10, max_length=900)
    fontes: tuple[Fonte, ...] = Field(min_length=1)
    lacuna: bool
    revisao_medica: Literal["pendente", "aprovada"]
    licenca_atencao: str = ""

    @field_validator("id")
    @classmethod
    def _id_valido(cls, valor: str) -> str:
        if not _ID.match(valor):
            raise ValueError("id fora do formato kebab")
        return valor

    @model_validator(mode="after")
    def _prefixo_do_tema(self) -> _Comum:
        prefixos = TEMAS.get(self.tema)
        if prefixos is None:
            raise ValueError(f"tema fora de {sorted(TEMAS)}")
        if not self.id.startswith(prefixos):
            raise ValueError(f"id sem o prefixo do tema ({', '.join(prefixos)})")
        return self


class Protocolo(_Comum):
    """Protocolo de TRIAGEM: perguntas de alerta (do mais grave ao menos grave) e destinos."""

    publico: Publico
    perguntas_de_alerta: tuple[PerguntaDeAlerta, ...] = Field(min_length=1, max_length=MAX_PERGUNTAS)
    destino_se_nenhum_sinal: Destino

    @model_validator(mode="after")
    def _sinais_unicos(self) -> Protocolo:
        sinais = [p.sinal for p in self.perguntas_de_alerta]
        if len(set(sinais)) != len(sinais):
            raise ValueError("sinal repetido nas perguntas de alerta")
        if self.tema == "prevencao":
            raise ValueError("prevencao nao tem perguntas de alerta")
        return self

    @property
    def rascunho(self) -> bool:
        return self.revisao_medica != "aprovada"


class Recomendacao(_Estrito):
    item: str
    faixa_etaria: str
    sexo: str
    frequencia: str
    fonte: str
    conferir_atualizacao: bool = False
    observacao: str = ""


class ProtocoloDeReferencia(_Comum):
    """Tabela de referencia da prevencao (exames e vacinas por idade): sem triagem, nao usada pela habilidade
    de sintoma. Validada pelas mesmas regras de texto."""

    publico: Literal[
        "adulto",
        "crianca_0_2m",
        "crianca_2m_5a",
        "crianca_5_12a",
        "adolescente",
        "gestante",
        "puerpera",
        "todas_as_idades",
    ]
    recomendacoes: tuple[Recomendacao, ...] = Field(min_length=1)


# --- O mapa queixa -> especialista e as frases dele ---------------------------------------------------
class EspecialidadeDoMapa(BaseModel):
    """Uma especialidade do CFM (`cfm`) ou uma profissao nao medica (`profissao`: dentista, psicologo...)."""

    model_config = ConfigDict(extra="allow", frozen=True)

    cfm: str | None = Field(default=None, min_length=3)
    profissao: str | None = Field(default=None, min_length=3)
    esp_id: str | None = None

    @model_validator(mode="after")
    def _um_nome(self) -> EspecialidadeDoMapa:
        if (self.cfm is None) == (self.profissao is None):
            raise ValueError("cada especialidade tem `cfm` OU `profissao`")
        return self


class PortaDeEntrada(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    nome_leigo: str = Field(min_length=3)
    cfm: tuple[str, ...] = ()
    esp_id: tuple[str, ...] = ()


class EntradaDoMapa(BaseModel):
    """Uma queixa do mapa. Os campos de USO INTERNO (quando ir direto, sinais citados) ficam como vieram."""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str = Field(pattern=r"^esp-queixa-[a-z0-9-]+$")
    queixa: str = Field(min_length=3, max_length=160)
    como_a_pessoa_fala: tuple[str, ...] = Field(min_length=1, max_length=20)
    especialista_primeiro: PortaDeEntrada
    especialista_depois: tuple[EspecialidadeDoMapa, ...] = ()
    lacuna: bool | Literal["parcial"] = False


class RegraDoMapa(BaseModel):
    """Uma regra por publico (crianca, idoso, gestante) que vale ANTES do mapa por parte do corpo."""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str = Field(pattern=r"^esp-regra-[a-z0-9-]+$")
    publico: tuple[str, ...] = Field(min_length=1)
    regra: str = Field(min_length=10)
    especialista_primeiro: PortaDeEntrada
    especialista_depois: tuple[EspecialidadeDoMapa, ...] = ()

    @field_validator("publico")
    @classmethod
    def _publicos_fechados(cls, valor: tuple[str, ...]) -> tuple[str, ...]:
        fora = [p for p in valor if p not in PUBLICOS]
        if fora:
            raise ValueError(f"publico fora do conjunto fechado: {fora}")
        return valor


@dataclass(frozen=True)
class TextosDoEspecialista:
    """As frases do `especialista/textos.yaml` usadas pela Helena (variaveis `{queixa}`, `{especialista}`)."""

    modelos: Mapping[str, str]
    como_marcar: str
    seguranca: Mapping[str, str]
    nomes_leigos: Mapping[str, str]

    def nome_leigo(self, cfm: str) -> str | None:
        """O nome leigo (com artigo) de um nome do CFM; parenteses e alternativas ("ou ...") sao ignorados."""
        base = cfm.split(" (")[0].split(" ou ")[0].split(" / ")[0].split(",")[0].strip()
        if base in self.nomes_leigos:
            return self.nomes_leigos[base]
        chave = _sem_acento(base)
        for nome, leigo in self.nomes_leigos.items():
            if _sem_acento(nome) == chave:
                return leigo
        if chave in ("clinico geral", "clinica geral"):
            return "o clínico geral"
        return None


@dataclass(frozen=True)
class Catalogo:
    """Os protocolos VALIDOS, o mapa e as frases. `excluidos` = id/arquivo -> motivo (rotulos, sem texto)."""

    protocolos: Mapping[str, Protocolo]
    referencias: Mapping[str, ProtocoloDeReferencia]
    mapa: Mapping[str, EntradaDoMapa]
    regras: Mapping[str, RegraDoMapa]
    textos: TextosDoEspecialista | None
    excluidos: Mapping[str, str]

    def do_publico(self, publicos: frozenset[str]) -> list[Protocolo]:
        return [p for p in self.protocolos.values() if p.publico in publicos]

    def universal(self, publico: str) -> Protocolo | None:
        return self.protocolos.get(UNIVERSAL_CRIANCA if publico in PUBLICOS_DE_CRIANCA else UNIVERSAL_ADULTO)


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn"
    ).lower()


def _ler(caminho: Path) -> Any:
    with caminho.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _motivo(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in e['loc']) or '-'}: {e['msg']}" for e in exc.errors()[:4])


def validar_protocolo(dados: Any, *, tema: str, nome_do_arquivo: str) -> Protocolo | ProtocoloDeReferencia:
    """UM protocolo, VALIDADO. Levanta `ProtocoloInvalidoError` com o motivo (rotulos, sem o texto)."""
    if not isinstance(dados, dict):
        raise ProtocoloInvalidoError("o arquivo nao e' um mapeamento YAML")
    texto = problemas_de_texto(dados)
    if texto:
        raise ProtocoloInvalidoError("regras de texto: " + ", ".join(texto))
    modelo: type[Protocolo] | type[ProtocoloDeReferencia] = (
        ProtocoloDeReferencia if tema == "prevencao" else Protocolo
    )
    try:
        protocolo = modelo.model_validate(dados)
    except ValidationError as exc:
        raise ProtocoloInvalidoError(f"schema: {_motivo(exc)}") from None
    if protocolo.tema != tema:
        raise ProtocoloInvalidoError(f"tema {protocolo.tema!r} no diretorio {tema!r}")
    if protocolo.id != nome_do_arquivo:
        raise ProtocoloInvalidoError("o nome do arquivo tem de ser o id")
    return protocolo


def validar_textos(dados: Any) -> TextosDoEspecialista:
    if not isinstance(dados, dict):
        raise ProtocoloInvalidoError("textos.yaml nao e' um mapeamento")
    # `proibido` e `como_marcar.proibido_acrescentar` LISTAM o que nunca se diz (citam "ANS" de proposito).
    lidos = {k: v for k, v in dados.items() if k not in ("proibido", "como_marcar")}
    lidos["como_marcar"] = {"texto": (dados.get("como_marcar") or {}).get("texto")}
    erros = problemas_de_texto(lidos)
    if erros:
        raise ProtocoloInvalidoError("textos.yaml: regras de texto: " + ", ".join(erros))
    modelos = {
        k: str(v["texto"])
        for k, v in (dados.get("modelos") or {}).items()
        if isinstance(v, dict) and "texto" in v
    }
    como_marcar = (dados.get("como_marcar") or {}).get("texto")
    seguranca = dados.get("seguranca") or {}
    nomes = dados.get("nomes_leigos") or {}
    faltando = [
        m for m in ("padrao", "clinico_primeiro", "nao_sei", "crianca", "gestante") if m not in modelos
    ]
    if faltando or not isinstance(como_marcar, str) or "padrao" not in seguranca or not nomes:
        raise ProtocoloInvalidoError(f"textos.yaml incompleto (modelos faltando: {faltando})")
    return TextosDoEspecialista(
        modelos=modelos,
        como_marcar=como_marcar,
        seguranca={str(k): str(v) for k, v in seguranca.items()},
        nomes_leigos={str(k): str(v) for k, v in nomes.items()},
    )


def validar_entrada_do_mapa(
    dados: Any, textos: TextosDoEspecialista | None, esp_ids: frozenset[str] | None
) -> EntradaDoMapa | RegraDoMapa:
    """UMA entrada (ou regra) do mapa: schema, regras de texto e especialidades CONHECIDAS (nome leigo do CFM
    em `textos.yaml` e arquivo `esp-*` da base de saude)."""
    if not isinstance(dados, dict):
        raise ProtocoloInvalidoError("entrada do mapa nao e' um mapeamento")
    lidos = {k: dados[k] for k in ("queixa", "como_a_pessoa_fala", "regra") if k in dados}
    texto = problemas_de_texto(lidos) + [
        e for e in problemas_de_texto(dados) if e.endswith(("url", "prazo_ans"))
    ]
    if texto:
        raise ProtocoloInvalidoError("regras de texto: " + ", ".join(sorted(set(texto))))
    modelo: type[EntradaDoMapa] | type[RegraDoMapa] = (
        RegraDoMapa if str(dados.get("id", "")).startswith("esp-regra-") else EntradaDoMapa
    )
    try:
        entrada = modelo.model_validate(dados)
    except ValidationError as exc:
        raise ProtocoloInvalidoError(f"schema: {_motivo(exc)}") from None
    desconhecidas: list[str] = []
    for esp in entrada.especialista_depois:
        if textos is not None and esp.cfm is not None and textos.nome_leigo(esp.cfm) is None:
            desconhecidas.append(f"cfm:{esp.cfm.split(' (')[0]}")
        if esp_ids is not None and esp.esp_id is not None and esp.esp_id not in esp_ids:
            desconhecidas.append(f"esp_id:{esp.esp_id}")
    if esp_ids is not None:
        desconhecidas += [f"esp_id:{e}" for e in entrada.especialista_primeiro.esp_id if e not in esp_ids]
    if desconhecidas:
        raise ProtocoloInvalidoError("especialidade desconhecida: " + ", ".join(desconhecidas))
    return entrada


def _esp_ids_da_base() -> frozenset[str] | None:
    try:
        from maezo.conhecimento import base_saude as _bs

        pasta = _bs.raiz_padrao() / "especialidade"
    except (FileNotFoundError, RuntimeError, OSError):
        return None
    if not pasta.is_dir():
        return None
    return frozenset(p.stem for p in pasta.glob("esp-*.yaml"))


def carregar(raiz: Path, *, esp_ids: frozenset[str] | None = None) -> Catalogo:
    """Carrega `raiz` (`.../protocolos`). Cada arquivo invalido e' EXCLUIDO e registrado; os outros ficam.
    `esp_ids`: os arquivos `esp-*` da base de saude (para conferir o mapa); `None` = nao confere."""
    protocolos: dict[str, Protocolo] = {}
    referencias: dict[str, ProtocoloDeReferencia] = {}
    excluidos: dict[str, str] = {}
    if not raiz.is_dir():
        raise FileNotFoundError(f"diretorio dos protocolos nao existe: {raiz}")
    for tema in sorted(TEMAS):
        pasta = raiz / tema
        if not pasta.is_dir():
            continue
        for caminho in sorted(pasta.glob("*.yaml")):
            relativo = f"{tema}/{caminho.name}"
            try:
                protocolo = validar_protocolo(_ler(caminho), tema=tema, nome_do_arquivo=caminho.stem)
            except (ProtocoloInvalidoError, yaml.YAMLError, OSError) as exc:
                motivo = str(exc) if isinstance(exc, ProtocoloInvalidoError) else type(exc).__name__
                excluidos[relativo] = motivo
                continue
            if protocolo.id in protocolos or protocolo.id in referencias:
                excluidos[relativo] = "id duplicado"
                continue
            if isinstance(protocolo, Protocolo):
                protocolos[protocolo.id] = protocolo
            else:
                referencias[protocolo.id] = protocolo
    textos: TextosDoEspecialista | None = None
    try:
        textos = validar_textos(_ler(raiz / "especialista" / "textos.yaml"))
    except (ProtocoloInvalidoError, yaml.YAMLError, OSError) as exc:
        excluidos["especialista/textos.yaml"] = (
            str(exc) if isinstance(exc, ProtocoloInvalidoError) else type(exc).__name__
        )
    mapa: dict[str, EntradaDoMapa] = {}
    regras: dict[str, RegraDoMapa] = {}
    try:
        bruto = _ler(raiz / "especialista" / "mapa_queixa_especialista.yaml")
    except (yaml.YAMLError, OSError) as exc:
        excluidos["especialista/mapa_queixa_especialista.yaml"] = type(exc).__name__
        bruto = {}
    entradas = (
        [*(bruto.get("regras_por_publico") or []), *(bruto.get("mapa") or [])]
        if isinstance(bruto, dict)
        else []
    )
    for item in entradas:
        ident = str(item.get("id")) if isinstance(item, dict) else "?"
        try:
            entrada = validar_entrada_do_mapa(item, textos, esp_ids)
        except ProtocoloInvalidoError as exc:
            excluidos[f"mapa/{ident}"] = str(exc)
            continue
        if entrada.id in mapa or entrada.id in regras:
            excluidos[f"mapa/{ident}"] = "id duplicado"
            continue
        if isinstance(entrada, RegraDoMapa):
            regras[entrada.id] = entrada
        else:
            mapa[entrada.id] = entrada
    for onde, motivo in excluidos.items():
        logger.warning("helena_protocolo_invalido", arquivo=onde, motivo=motivo[:200])
    return Catalogo(
        protocolos=protocolos,
        referencias=referencias,
        mapa=mapa,
        regras=regras,
        textos=textos,
        excluidos=excluidos,
    )


def raiz_padrao() -> Path:
    """`spec/agents/helena/pacote/protocolos`, pelo ponto unico de `spec/agents`."""
    from maezo.agents import resolve_spec_agents_dir  # import tardio: evita ciclo no import do pacote

    return resolve_spec_agents_dir() / "helena" / "pacote" / "protocolos"


#: Chave de DESLIGAR por ambiente (padrao: ligada onde a composicao liga): `0`, `false`, `off` desligam.
CHAVE_DO_AMBIENTE: Final[str] = "MAEZO_HELENA_PROTOCOLOS"
_CACHE: dict[str, Catalogo | None] = {}


def catalogo_do_ambiente() -> Catalogo | None:
    """O catalogo da imagem, carregado UMA vez por processo; `None` (o caminho de antes) quando a chave de
    ambiente desliga, o diretorio nao existe ou nenhum protocolo de triagem e' valido."""
    if os.environ.get(CHAVE_DO_AMBIENTE, "1").strip().lower() in ("0", "false", "off", "nao", "no"):
        return None
    if "padrao" not in _CACHE:
        try:
            catalogo: Catalogo | None = carregar(raiz_padrao(), esp_ids=_esp_ids_da_base())
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            logger.error("helena_protocolos_indisponiveis", erro=type(exc).__name__)
            catalogo = None
        if catalogo is not None and (not catalogo.protocolos or catalogo.textos is None):
            logger.error("helena_protocolos_indisponiveis", erro="vazio")
            catalogo = None
        if catalogo is not None:
            logger.info(
                "helena_protocolos_carregados",
                protocolos=len(catalogo.protocolos),
                mapa=len(catalogo.mapa),
                excluidos=len(catalogo.excluidos),
            )
        _CACHE["padrao"] = catalogo
    return _CACHE["padrao"]


__all__ = [
    "CHAVE_DO_AMBIENTE",
    "DESTINOS",
    "GRAVIDADE",
    "MAX_PERGUNTAS",
    "PUBLICOS",
    "PUBLICOS_DE_CRIANCA",
    "TEMAS",
    "UNIVERSAL_ADULTO",
    "UNIVERSAL_CRIANCA",
    "Catalogo",
    "EntradaDoMapa",
    "PerguntaDeAlerta",
    "Protocolo",
    "ProtocoloDeReferencia",
    "ProtocoloInvalidoError",
    "RegraDoMapa",
    "TextosDoEspecialista",
    "carregar",
    "catalogo_do_ambiente",
    "problemas_de_texto",
    "raiz_padrao",
    "validar_entrada_do_mapa",
    "validar_protocolo",
    "validar_textos",
]
