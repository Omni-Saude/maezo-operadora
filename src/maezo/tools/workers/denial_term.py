"""E-SF-24 (EP-PROC-07a) — render puro do termo da negativa (RN 623/2024 art. 14 e 16).

O art. 14 manda reduzir a negativa a termo, com o motivo e a cláusula contratual ou o dispositivo
legal que a justifique; o art. 16, § 3º, manda que o mesmo documento traga, em destaque, os
elementos mínimos da reanálise pela Ouvidoria. Este módulo só COMPÕE o texto desse termo a partir
de um template versionado. Não envia, não grava e não decide nada.

E-SF-24b (parcial, CD-231): o template vigente é o v1. Ele traz o caput do art. 16 e, num bloco
próprio com título, os incisos I, II e III do § 1º verbatim do texto-base versionado. O fundamento
do art. 14 (motivo, cláusula contratual ou dispositivo legal) vem só de um `FundamentoDoTermo` de
um catálogo pinado. O catálogo ainda não existe (`CATALOGO_DE_FUNDAMENTOS_SHA256 is None`), então
a composição recusa sempre: porta fechada por construção. `fundamentacao_dut` nunca é lida aqui
nem substitui o fundamento; a `justificativa_clinica` do auditor entra só como complemento do motivo.

- O template mora em `spec/policies/ans/templates/` (diretório com dono no CODEOWNERS) e é
  aceito só se o sha256 dos bytes bater com o pino de `DENIAL_TERM_TEMPLATE` (versão + sha256).
  Qualquer outro texto é recusado com `ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO`. O template
  não tem prosa própria: só rótulos tirados da própria norma, os campos do registro humano e o
  art. 16, caput, verbatim. Linhas que começam com `# ` são metadados do template (entram no
  sha256, não entram no termo); a primeira diz que o mapeamento não está confirmado.
- A negativa é decisão humana (item `hard` `authorization_denial`). O render não decide, não lê
  nem escreve `decisao_auditor` para produzir nada: só compõe o termo de uma NEGAR já registrada
  com proveniência humana (`auditor_id` não vazio ou `human_approved is True`); sem isso, recusa
  com `ERR_DENIAL_NOT_HUMAN`. O critério é o do guard do worker, porém mais estrito: aqui um
  `auditor_id` só de caracteres de largura zero conta como vazio (`is_blank`).
- Fundamento: sem catálogo pinado, `ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO`; com pino, um
  `FundamentoDoTermo` de tipo errado, com campo vazio ou quebra de linha, sem cláusula nem
  dispositivo, ou com `hash_do_catalogo` diferente do pino é `ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO`.
  Sem `justificativa_clinica` (o complemento do auditor), `ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE`.
  Os canais da Ouvidoria e o prazo da
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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from maezo.gateway.required_text import is_blank
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN

__all__ = [
    "CATALOGO_DE_FUNDAMENTOS_SHA256",
    "DENIAL_TERM_TEMPLATE",
    "ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO",
    "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE",
    "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO",
    "ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE",
    "ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO",
    "DenialTermRefusalError",
    "DenialTermTemplate",
    "FundamentoDoTermo",
    "RenderedDenialTerm",
    "TemplatePin",
    "load_denial_term_template",
    "render_denial_term",
]

ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO = "ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO"
ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE = "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE"
ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE = "ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE"
ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO = "ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO"
ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO = "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO"

#: Teto do art. 16, § 1º, III: "não superior a sete dias úteis".
_PRAZO_REANALISE_MAX_DIAS_UTEIS = 7

#: Único campo do registro humano que a composição lê: o complemento clínico do motivo.
_COMPLEMENTO_DO_MOTIVO = "justificativa_clinica"

#: sha256 do catálogo pinado de fundamentos (virá do repositório de regras). `None`: não há
#: catálogo, e a composição recusa todo fundamento (porta fechada por construção).
CATALOGO_DE_FUNDAMENTOS_SHA256: str | None = None

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


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


@dataclass(frozen=True, slots=True)
class FundamentoDoTermo:
    """Fundamento do art. 14 vindo de uma regra de um catálogo pinado (nunca texto livre).

    O art. 14 pede "a cláusula contratual ou o dispositivo legal": ao menos um dos dois.
    """

    id_regra: str
    versao_regra: str
    motivo_texto: str
    clausula_contratual: str | None
    dispositivo_legal: str | None
    hash_do_catalogo: str


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
    texto: str
    sha256: str
    template_versao: str
    template_sha256: str


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


def _linha_valida(value: Any) -> bool:
    return isinstance(value, str) and not is_blank(value) and "\n" not in value and "\r" not in value


def _fundamento(value: Any) -> tuple[str, str]:
    """Confere a porta e devolve (motivo, fundamento) do catálogo; recusa tipada em qualquer falha."""
    pino = CATALOGO_DE_FUNDAMENTOS_SHA256
    if pino is None:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_CATALOGO_NAO_PINADO)
    if not isinstance(value, FundamentoDoTermo):
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, ("fundamento",))
    invalidos = [
        nome
        for nome in ("id_regra", "versao_regra", "motivo_texto", "hash_do_catalogo")
        if not _linha_valida(getattr(value, nome))
    ]
    citados = [
        nome for nome in ("clausula_contratual", "dispositivo_legal") if getattr(value, nome) is not None
    ]
    invalidos += [nome for nome in citados if not _linha_valida(getattr(value, nome))]
    if not citados:
        invalidos.append("clausula_contratual|dispositivo_legal")
    if invalidos:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, invalidos)
    if not _SHA256_HEX.fullmatch(pino) or value.hash_do_catalogo != pino:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_INVALIDO, ("hash_do_catalogo",))
    fundamento = "\n".join(str(getattr(value, nome)).strip() for nome in citados)
    return value.motivo_texto.strip(), fundamento


def _canais(value: Any) -> str | None:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return None
    # Uma quebra de linha dentro de um canal forjaria itens a mais na lista do termo.
    canais = [c.strip() for c in value if not is_blank(c) and "\n" not in c and "\r" not in c]
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

    motivo, fundamento_texto = _fundamento(fundamento)

    complemento = record.get(_COMPLEMENTO_DO_MOTIVO)
    if is_blank(complemento):
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE, (_COMPLEMENTO_DO_MOTIVO,))

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
        "motivo": f"{motivo}\n{str(complemento).strip()}",
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
    )
