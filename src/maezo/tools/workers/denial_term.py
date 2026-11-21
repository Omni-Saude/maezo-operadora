"""E-SF-24 (EP-PROC-07a) — render puro do termo da negativa (RN 623/2024 art. 14 e 16).

O art. 14 manda reduzir a negativa a termo, com o motivo e a cláusula contratual ou o dispositivo
legal que a justifique; o art. 16, § 3º, manda que o mesmo documento traga, em destaque, os
elementos mínimos da reanálise pela Ouvidoria. Este módulo só COMPÕE o texto desse termo a partir
de um template versionado. Não envia, não grava e não decide nada.

E-SF-24b (parcial, CD-231): o template vigente é o v1. Ele traz o caput do art. 16 e, num bloco
próprio com título, os incisos I, II e III do § 1º verbatim do texto-base versionado. O fundamento
do art. 14 (motivo, cláusula contratual e/ou dispositivos legais) vem só de um `FundamentoDoTermo`
de um catálogo pinado, que o repositório de regras vai publicar. O catálogo ainda não existe
(`CATALOGO_DE_FUNDAMENTOS_SHA256 is None`), então a composição recusa sempre: porta fechada por
construção. `fundamentacao_dut` nunca é lida aqui nem substitui o fundamento; a
`justificativa_clinica` do auditor entra só como complemento do motivo.

Dois modos, pelo `status_juridico` do fundamento (com os dois hashes conferidos):
`aprovado_juridico` compõe o termo para ENTREGA; `pendente` compõe só um RASCUNHO para a pessoa
que entrega à mão, com a marca `MARCA_RASCUNHO` fora do texto (nunca no texto ao beneficiário);
`devolvido` ou hash divergente é recusa tipada. O render não registra entrega em nenhum modo.
Os nomes de campo do catálogo são PROVISÓRIOS até o contrato-k2 do repositório de regras sair:
ficam todos no bloco "Mapeamento provisório do contrato-k2" abaixo, e só nele.

- O template mora em `spec/policies/ans/templates/` (diretório com dono no CODEOWNERS) e é
  aceito só se o sha256 dos bytes bater com o pino de `DENIAL_TERM_TEMPLATE` (versão + sha256).
  Qualquer outro texto é recusado com `ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO`. O template
  não tem prosa própria: só rótulos tirados da própria norma, os slots da composição, o art. 16,
  caput, e os incisos I a III do § 1º, verbatim. Linhas que começam com `# ` são metadados do
  template (entram no sha256, não entram no termo).
- A negativa é decisão humana (item `hard` `authorization_denial`). O render não decide, não lê
  nem escreve `decisao_auditor` para produzir nada: só compõe o termo de uma NEGAR já registrada
  com proveniência humana (`auditor_id` não vazio ou `human_approved is True`); sem isso, recusa
  com `ERR_DENIAL_NOT_HUMAN`. O critério é o do guard do worker, porém mais estrito: aqui um
  `auditor_id` só de caracteres de largura zero conta como vazio (`is_blank`).
- Fundamento: sem catálogo pinado, `ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO`; com pino, um
  `FundamentoDoTermo` de tipo errado, com campo vazio ou quebra de linha, sem cláusula nem
  dispositivo, com status desconhecido, com `hash_do_catalogo` diferente do pino ou com
  `hash_entrada` diferente do hash da própria entrada é `ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO`;
  status `devolvido` é `ERR_AUTH_DENIAL_TERM_FUNDAMENTO_DEVOLVIDO`. Sem `justificativa_clinica`
  (o complemento do auditor), `ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE`. Os canais da Ouvidoria e o prazo da
  reanálise (art. 16, § 1º, II e III) são dados da operadora, não do processo; faltando, a recusa
  é `ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE`. Nada aqui inventa texto para preencher a falta.
- Puro e determinístico: o mesmo input dá o mesmo texto e o mesmo sha256. Sem relógio, sem acaso,
  sem I/O (a leitura do arquivo fica em `load_denial_term_template`, separada).
- O texto composto carrega PHI (o motivo clínico). Ele nunca entra em variável de engine: o
  registro da negativa leva só a versão e o sha256 do template (ver `SendDenialNoticeWorker`).
- Quem chama: ninguém em produção. A entrega segue manual e pendente
  (`entrega_manual_pendente`), e a bandeira `MAEZO_AUTH_DENIAL_WRITTEN_NOTICE` segue desligada.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from string import Template
from typing import Any

from maezo.gateway.required_text import ZERO_WIDTH_TRANSLATION, is_blank
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN

__all__ = [
    "CATALOGO_DE_FUNDAMENTOS_SHA256",
    "DENIAL_TERM_TEMPLATE",
    "ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO",
    "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE",
    "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_DEVOLVIDO",
    "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO",
    "ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE",
    "ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO",
    "DenialTermRefusalError",
    "MARCA_RASCUNHO",
    "MODO_ENTREGA",
    "MODO_RASCUNHO",
    "STATUS_APROVADO_JURIDICO",
    "STATUS_DEVOLVIDO",
    "STATUS_PENDENTE",
    "ClausulaContratual",
    "DenialTermTemplate",
    "DispositivoLegal",
    "FundamentoDoTermo",
    "RenderedDenialTerm",
    "TemplatePin",
    "hash_da_entrada",
    "load_denial_term_template",
    "render_denial_term",
]

ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO = "ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO"
ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE = "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE"
ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE = "ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE"
ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO = "ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO"
ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO = "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO"
ERR_AUTH_DENIAL_TERM_FUNDAMENTO_DEVOLVIDO = "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_DEVOLVIDO"

#: Teto do art. 16, § 1º, III: "não superior a sete dias úteis".
_PRAZO_REANALISE_MAX_DIAS_UTEIS = 7

#: Único campo do registro humano que a composição lê: o complemento clínico do motivo.
_COMPLEMENTO_DO_MOTIVO = "justificativa_clinica"

#: sha256 do catálogo pinado de fundamentos (virá do repositório de regras). `None`: não há
#: catálogo, e a composição recusa todo fundamento (porta fechada por construção).
CATALOGO_DE_FUNDAMENTOS_SHA256: str | None = None

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

#: Prefixo de todo rótulo do template ("RN 623/2024, art. ..."), tolerante a espaço e a caixa.
#: Nenhum valor que entra no termo pode trazê-lo: seria um rótulo forjado no meio do termo.
_ROTULO_DO_TERMO = re.compile(r"rn\s*623\s*/\s*2024\s*,\s*art\s*\.")


@dataclass(frozen=True, slots=True)
class TemplatePin:
    """Versão e sha256 dos bytes de um template, relativos à raiz do repositório."""

    path: str
    versao: str
    sha256: str


#: O único template de termo de negativa aceito. Trocar o texto exige nova versão e novo pino.
#: O v0 fica versionado e intacto, mas a composição não o aceita mais.
DENIAL_TERM_TEMPLATE = TemplatePin(
    path="spec/policies/ans/templates/termo-negativa.v1.txt",
    versao="v1",
    sha256="aad9b75e2b824440cf7a1e7bee04e93fbaea42ed52c4179afeb657907f66c5c0",
)


class DenialTermRefusalError(ValueError):
    """Recusa tipada do render: o termo não é composto, e a recusa diz por quê."""

    def __init__(self, code: str, missing: Sequence[str] = ()) -> None:
        self.code = code
        self.missing = tuple(missing)
        detail = f": {list(self.missing)}" if self.missing else ""
        super().__init__(f"{code}{detail}")


@dataclass(frozen=True, slots=True)
class DenialTermTemplate:
    """Template já conferido contra o pino (só `load_denial_term_template` deve construir)."""

    versao: str
    sha256: str
    texto: str


@dataclass(frozen=True, slots=True)
class RenderedDenialTerm:
    """Termo composto. `modo` diz se é para ENTREGA ou só RASCUNHO de quem entrega à mão; a marca
    do rascunho fica em `marca`, fora de `texto` (o texto nunca carrega a marca)."""

    texto: str
    sha256: str
    template_versao: str
    template_sha256: str
    modo: str
    marca: str | None

    def __post_init__(self) -> None:
        # Invariante: só dois modos, e a marca existe se e só se for rascunho.
        esperado = {MODO_ENTREGA: None, MODO_RASCUNHO: MARCA_RASCUNHO}
        if self.modo not in esperado or self.marca != esperado[self.modo]:
            raise ValueError(f"RenderedDenialTerm inconsistente: modo={self.modo!r}, marca={self.marca!r}")


def _linha_valida(value: Any) -> bool:
    """Uma linha só, de `str` exato: sem nenhum separador de linha (os de `str.splitlines`, como
    U+2028), sem surrogate solto e sem rótulo do template ("RN 623/2024, art.", mesmo com
    caracteres de largura zero no meio), para que nenhum valor forje rótulos ou itens no termo."""
    if type(value) is not str or is_blank(value) or value.splitlines() != [value]:
        return False
    if _ROTULO_DO_TERMO.search(value.translate(ZERO_WIDTH_TRANSLATION).casefold()):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


# --- Mapeamento provisório do contrato-k2 -----------------------------------------------------
# ÚNICO ponto que conhece a forma da entrada do catálogo de fundamentos (bloco k2/termo-negativa
# do repositório de regras, chave id_regra + versão × modelo de contrato). Os nomes são
# PROVISÓRIOS até o contrato-k2 sair; quando sair, muda-se só este bloco.

STATUS_PENDENTE = "pendente"
STATUS_APROVADO_JURIDICO = "aprovado_juridico"
STATUS_DEVOLVIDO = "devolvido"
_STATUS_JURIDICOS = frozenset({STATUS_PENDENTE, STATUS_APROVADO_JURIDICO, STATUS_DEVOLVIDO})

MODO_ENTREGA = "entrega"
MODO_RASCUNHO = "rascunho"

#: Marca do rascunho de fundamento pendente. Vai em `RenderedDenialTerm.marca`, nunca no texto.
MARCA_RASCUNHO = "fundamento sem revisão jurídica"


@dataclass(frozen=True, slots=True)
class DispositivoLegal:
    id_fonte: str
    localizador: str
    rotulo: str


@dataclass(frozen=True, slots=True)
class ClausulaContratual:
    ancora: str
    secao: str
    texto: str
    versao_contrato: str


@dataclass(frozen=True, slots=True)
class FundamentoDoTermo:
    """Entrada do catálogo pinado de fundamentos do art. 14 (nunca texto livre).

    O art. 14 pede "a cláusula contratual ou o dispositivo legal": ao menos uma cláusula ou um
    dispositivo. `hash_entrada` é o sha256 da própria entrada (`hash_da_entrada`);
    `hash_do_catalogo`, o do catálogo inteiro, conferido contra `CATALOGO_DE_FUNDAMENTOS_SHA256`.
    """

    id_regra: str
    versao_regra: str
    modelo_contrato: str
    motivo_texto: str
    clausula_contratual: ClausulaContratual | None
    dispositivos_legais: tuple[DispositivoLegal, ...]
    status_juridico: str
    hash_do_catalogo: str
    hash_entrada: str


_CAMPOS_DE_TEXTO = ("id_regra", "versao_regra", "modelo_contrato", "motivo_texto", "status_juridico")
_HASHES = ("hash_do_catalogo", "hash_entrada")


def hash_da_entrada(fundamento: FundamentoDoTermo) -> str:
    """sha256 da forma canônica da entrada (todos os campos menos os dois hashes)."""
    entrada = {k: v for k, v in asdict(fundamento).items() if k not in _HASHES}
    canonica = json.dumps(entrada, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256(canonica.encode("utf-8"))


def _linhas_do_fundamento(fundamento: FundamentoDoTermo) -> list[str]:
    linhas = []
    if fundamento.clausula_contratual is not None:
        clausula = fundamento.clausula_contratual
        linhas.append(f"{clausula.secao.strip()} — {clausula.texto.strip()}")
    linhas.extend(d.rotulo.strip() for d in fundamento.dispositivos_legais)
    return linhas


def _campos_invalidos(fundamento: FundamentoDoTermo) -> list[str]:
    invalidos = [
        nome for nome in (*_CAMPOS_DE_TEXTO, *_HASHES) if not _linha_valida(getattr(fundamento, nome))
    ]
    if "status_juridico" not in invalidos and fundamento.status_juridico not in _STATUS_JURIDICOS:
        invalidos.append("status_juridico")
    clausula = fundamento.clausula_contratual
    if clausula is not None and (
        type(clausula) is not ClausulaContratual
        or not all(_linha_valida(getattr(clausula, f.name)) for f in fields(ClausulaContratual))
    ):
        invalidos.append("clausula_contratual")
    dispositivos = fundamento.dispositivos_legais
    if type(dispositivos) is not tuple or not all(
        type(d) is DispositivoLegal
        and all(_linha_valida(getattr(d, f.name)) for f in fields(DispositivoLegal))
        for d in dispositivos
    ):
        invalidos.append("dispositivos_legais")
    elif clausula is None and not dispositivos:
        invalidos.append("clausula_contratual|dispositivos_legais")
    return invalidos


def _fundamento(value: Any) -> tuple[str, str, str]:
    """Confere a porta e devolve (motivo, fundamento, modo); recusa tipada em qualquer falha."""
    pino = CATALOGO_DE_FUNDAMENTOS_SHA256
    if pino is None:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO)
    if type(value) is not FundamentoDoTermo:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, ("fundamento",))
    invalidos = _campos_invalidos(value)
    if invalidos:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, invalidos)
    if not _SHA256_HEX.fullmatch(pino) or value.hash_do_catalogo != pino:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, ("hash_do_catalogo",))
    try:
        hash_calculado = hash_da_entrada(value)
    except (TypeError, ValueError) as exc:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, ("hash_entrada",)) from exc
    if value.hash_entrada != hash_calculado:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, ("hash_entrada",))
    if value.status_juridico == STATUS_DEVOLVIDO:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_DEVOLVIDO)
    modo = MODO_ENTREGA if value.status_juridico == STATUS_APROVADO_JURIDICO else MODO_RASCUNHO
    return value.motivo_texto.strip(), "\n".join(_linhas_do_fundamento(value)), modo


# --- fim do mapeamento provisório ---------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_denial_term_template(path: Path, pin: TemplatePin = DENIAL_TERM_TEMPLATE) -> DenialTermTemplate:
    """Lê o template e confere o sha256 contra o pino; arquivo ausente ou divergente é recusado."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO) from exc
    if _sha256(data) != pin.sha256:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO)
    return DenialTermTemplate(versao=pin.versao, sha256=pin.sha256, texto=data.decode("utf-8"))


def _human_denial(record: Mapping[str, Any]) -> bool:
    """NEGAR registrada por humano: o critério do guard do worker, mais estrito no vazio."""
    if record.get("decisao_auditor") != "NEGAR":
        return False
    auditor_id = record.get("auditor_id")
    return record.get("human_approved") is True or (isinstance(auditor_id, str) and not is_blank(auditor_id))


def _canais(value: Any) -> str | None:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return None
    # Uma quebra de linha dentro de um canal forjaria itens a mais na lista do termo.
    canais = [c.strip() for c in value if _linha_valida(c)]
    if not canais or len(canais) != len(value):
        return None
    return "\n".join(f"- {c}" for c in canais)


def _prazo(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= _PRAZO_REANALISE_MAX_DIAS_UTEIS:
        return None
    return str(value)


def render_denial_term(
    record: Mapping[str, Any],
    template: DenialTermTemplate,
    *,
    fundamento: FundamentoDoTermo,
    canais_ouvidoria: Sequence[str],
    prazo_reanalise_dias_uteis: int,
) -> RenderedDenialTerm:
    """Compõe o termo. Puro: nenhum relógio, acaso ou I/O; mesmo input, mesmo sha256."""
    if (
        not isinstance(template, DenialTermTemplate)
        or _sha256(template.texto.encode("utf-8")) != template.sha256
    ):
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO)
    if (template.versao, template.sha256) != (DENIAL_TERM_TEMPLATE.versao, DENIAL_TERM_TEMPLATE.sha256):
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO)

    if not _human_denial(record):
        raise DenialTermRefusalError(ERR_DENIAL_NOT_HUMAN)

    motivo, fundamento_texto, modo = _fundamento(fundamento)

    complemento = record.get(_COMPLEMENTO_DO_MOTIVO)
    if is_blank(complemento):
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE, (_COMPLEMENTO_DO_MOTIVO,))
    # Texto livre do auditor: uma linha só, senão forjaria rótulos do art. 14 no termo de entrega.
    if not _linha_valida(complemento):
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, (_COMPLEMENTO_DO_MOTIVO,))

    canais = _canais(canais_ouvidoria)
    prazo = _prazo(prazo_reanalise_dias_uteis)
    faltam = [
        name
        for name, ok in (("canais_ouvidoria", canais), ("prazo_reanalise_dias_uteis", prazo))
        if ok is None
    ]
    if faltam:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE, faltam)

    values = {
        # O complemento do auditor vem na MESMA linha do motivo do catálogo: nunca abre linha, e
        # por isso nunca se passa por rótulo ou por fundamento do termo.
        "motivo": f"{motivo} — {str(complemento).strip()}",
        "fundamento": fundamento_texto,
    }
    values["canais_ouvidoria"] = canais or ""
    values["prazo_reanalise_dias_uteis"] = prazo or ""
    # `substitute` (estrito): um slot do template sem valor levanta, nunca vira texto vazio.
    body = "".join(line for line in template.texto.splitlines(keepends=True) if not line.startswith("# "))
    texto = Template(body).substitute(values)
    return RenderedDenialTerm(
        texto=texto,
        sha256=_sha256(texto.encode("utf-8")),
        template_versao=template.versao,
        template_sha256=template.sha256,
        modo=modo,
        marca=MARCA_RASCUNHO if modo == MODO_RASCUNHO else None,
    )
