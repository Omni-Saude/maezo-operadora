"""E-SF-24 (EP-PROC-07a, parte a) — render puro do termo da negativa (RN 623/2024 art. 14 e 16).

O art. 14 manda reduzir a negativa a termo, com o motivo e a cláusula contratual ou o dispositivo
legal que a justifique; o art. 16, § 3º, manda que o mesmo documento traga, em destaque, os
elementos mínimos da reanálise pela Ouvidoria. Este módulo só COMPÕE o texto desse termo a partir
de um template versionado. Não envia, não grava e não decide nada. O template v0 cita o caput do
art. 16 e os elementos II (canais) e III (prazo) do § 1º; NÃO cita o elemento I (segunda instância
na Ouvidoria) nem afirma completude do § 1º — isso também fica na pré-condição jurídica para
ligar a bandeira.

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
- Mapeamento VIGENTE do contrato SP-OP-AUTH-001 (a confirmar pelo jurídico): motivo =
  `justificativa_clinica`; cláusula/dispositivo = `fundamentacao_dut`. Faltando qualquer um, a
  recusa é `ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE`. Os canais da Ouvidoria e o prazo da
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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from maezo.gateway.required_text import is_blank
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN

__all__ = [
    "DENIAL_TERM_TEMPLATE",
    "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE",
    "ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE",
    "ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO",
    "DenialTermRefusalError",
    "DenialTermTemplate",
    "RenderedDenialTerm",
    "TemplatePin",
    "load_denial_term_template",
    "render_denial_term",
]

ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO = "ERR_AUTH_DENIAL_TERM_TEMPLATE_NAO_VERSIONADO"
ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE = "ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE"
ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE = "ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE"

#: Teto do art. 16, § 1º, III: "não superior a sete dias úteis".
_PRAZO_REANALISE_MAX_DIAS_UTEIS = 7

#: Mapeamento vigente do contrato SP-OP-AUTH-001 (art. 14): slot do template -> campo do registro.
_FUNDAMENTO_FIELDS: tuple[tuple[str, str], ...] = (
    ("motivo", "justificativa_clinica"),
    ("fundamento", "fundamentacao_dut"),
)


@dataclass(frozen=True, slots=True)
class TemplatePin:
    """Versão e sha256 dos bytes de um template, relativos à raiz do repositório."""

    path: str
    versao: str
    sha256: str


#: O único template de termo de negativa aceito. Trocar o texto exige nova versão e novo pino.
DENIAL_TERM_TEMPLATE = TemplatePin(
    path="spec/policies/ans/templates/termo-negativa.v0.txt",
    versao="v0",
    sha256="536162999723833e90f113df684bce8489bda116bcbd9af65a7a4dc17dcacb87",
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

    missing = [field for _, field in _FUNDAMENTO_FIELDS if is_blank(record.get(field))]
    if missing:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_FUNDAMENTO_AUSENTE, missing)

    canais = _canais(canais_ouvidoria)
    prazo = _prazo(prazo_reanalise_dias_uteis)
    faltam = [
        name
        for name, ok in (("canais_ouvidoria", canais), ("prazo_reanalise_dias_uteis", prazo))
        if ok is None
    ]
    if faltam:
        raise DenialTermRefusalError(ERR_AUTH_DENIAL_TERM_OUVIDORIA_AUSENTE, faltam)

    values = {slot: str(record[field]).strip() for slot, field in _FUNDAMENTO_FIELDS}
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
