"""E-SF-37r (escopo (a) aprovado): a entrada por CPF no comercial segue FECHADA, e nada em `src` muda.

O que esta linha fixa, cada cerca com controle positivo do detector e caso sem falso positivo:

(i)   Nenhum comando do contrato C6-4 pinado (`x-commands`) tem nome de consulta/busca por
      documento ou CPF, e nenhuma carga (resolvida por `$ref`, recursiva) tem chave de documento,
      salvo a exceção pinada `CatalogRow.document_refs` (refs a documentos de PRODUTO no catálogo,
      não do titular). As chaves de `OfferQuotePayload` (`OFFER_QUOTE_KEYS`) não têm chave de
      documento.
(ii)  Nenhum módulo do comercial (`gateway/commercial*.py`, `adapters/amh/commercial*.py`,
      `portal/api/commercial.py`, `ports/commercial.py`) importa `maezo.adapters.amh.document_resolution`,
      `maezo.adapters.amh.subject_verification` nem `maezo.gateway.amh_interop`: import estático
      por AST, relativo resolvido contra o pacote do arquivo, `from X import Y` lido como `X.Y`.
(iii) O contrato ADMITE `value_type: "document"` em `IntakeField`; com o codec padrão (lista de
      finalidades vazia, `CommercialWire` sem `intake_purpose_allowlist`) um descritor de
      `prospect` com campo `document` é recusado, e `prospect.save` com valor é recusado. Desde a
      E-SF-37r (b) a cerca do descritor tem regra PRÓPRIA contra `document` no `prospect`: com a
      lista sintética o campo `document` segue recusado, e um campo `choice` vizinho passa.
(iv)  `IntakeValue` no contrato pinado não tem `origin` (`additionalProperties: false`). Este teste
      TEM de ficar vermelho quando um contrato sucessor acrescentar o campo: aí a REG-28b passa a
      ser representável e exige linha nova de plano; hoje não é representável.
(v)   `portal/api/production.py` não importa nem monta gateway comercial (AST: imports, keywords
      da composição e atributos `app.state`).

Limites declarados: import dinâmico (`importlib.import_module`, `__import__`), montagem por
reflexão (`setattr` com nome calculado) e acesso por atributo a submódulo de um pacote importado
(`import maezo.gateway as g; g.amh_interop`) não são vistos por uma cerca de AST. O contrato
`tests/candidate_artifacts/commercial/contract-c6-4.json` é só lido; "sucessores" são cópias em
memória. Fixtures sintéticas; nenhum literal com forma de CPF.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.adapters.amh.commercial_fences import OFFER_QUOTE_KEYS, validate_intake_descriptor
from tests.unit.gateway.test_esf04_commercial_fences import (
    PURPOSES,
    descriptor,
    field,
    refused,
    wire_refuses_command,
    wire_refuses_result,
)
from tests.unit.test_commercial_c6 import encode, envelope, parse
from tests.unit.test_commercial_http import schema_bytes as schema_bytes

_REPO = Path(__file__).resolve().parents[3]
_SRC = _REPO / "src" / "maezo"
_CONTRATO = _REPO / "tests" / "candidate_artifacts" / "commercial" / "contract-c6-4.json"
_PRODUCAO = _SRC / "portal" / "api" / "production.py"

#: Módulos que não podem chegar à resolução de documento nem à verificação de titular.
_ALVOS_VEDADOS = frozenset(
    {
        "maezo.adapters.amh.document_resolution",
        "maezo.adapters.amh.subject_verification",
        "maezo.gateway.amh_interop",
    }
)

#: Tokens de nome de comando que indicariam consulta/busca/verificação por documento.
_TOKENS_DE_CONSULTA = frozenset(
    {
        "cpf",
        "document",
        "documento",
        "doc",
        "lookup",
        "consult",
        "consulta",
        "search",
        "busca",
        "find",
        "resolve",
        "verify",
        "verification",
        "identify",
    }
)
#: Tokens de chave de carga que indicariam documento do titular.
_TOKENS_DE_DOCUMENTO = frozenset({"cpf", "document", "documento", "doc", "rg", "identidade", "tax"})
#: Exceção pinada exatamente: refs a documentos de produto na linha de catálogo (`catalog.draft`/
#: `catalog.import`), um array de `Ref`. Qualquer acerto NOVO derruba a cerca.
_EXCECAO_PINADA = frozenset({("CatalogRow", "document_refs")})


def _contrato() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads(_CONTRATO.read_text(encoding="utf-8"))
    return dados


def _tokens(nome: str) -> set[str]:
    # camelCase também separa (`holderCpf` -> holder, cpf): o contrato usa os dois estilos.
    separado = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", nome)
    return {t for t in re.split(r"[^a-z0-9]+", separado.lower()) if t}


# --- (i) Comandos e cargas do C6-4 ----------------------------------------------------------------


def _comandos_de_consulta(contrato: dict[str, Any]) -> list[str]:
    return sorted(n for n in contrato["x-commands"] if _tokens(n) & _TOKENS_DE_CONSULTA)


def _chaves_de_carga(contrato: dict[str, Any]) -> set[tuple[str, str]]:
    """(def, chave) de toda propriedade alcançável das cargas de `x-commands`, por `$ref`."""
    defs: dict[str, Any] = contrato["$defs"]
    achadas: set[tuple[str, str]] = set()
    vistos: set[str] = set()

    def andar(no: object, dono: str) -> None:
        if isinstance(no, dict):
            ref = no.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                nome = ref.rsplit("/", 1)[1]
                if nome not in vistos:
                    vistos.add(nome)
                    andar(defs[nome], nome)
            propriedades = no.get("properties")
            if isinstance(propriedades, dict):
                achadas.update((dono, chave) for chave in propriedades)
            for valor in no.values():
                andar(valor, dono)
        elif isinstance(no, list):
            for valor in no:
                andar(valor, dono)

    for comando in contrato["x-commands"].values():
        andar({"$ref": comando["payload_schema"]}, "")
    return achadas


def _chaves_de_documento(contrato: dict[str, Any]) -> set[tuple[str, str]]:
    return {(d, c) for d, c in _chaves_de_carga(contrato) if _tokens(c) & _TOKENS_DE_DOCUMENTO}


def test_nenhum_comando_do_c6_4_consulta_por_documento() -> None:
    pinado = _contrato()
    assert pinado["x-commands"]  # controle: a varredura vê os comandos
    assert _comandos_de_consulta(pinado) == []
    # Controle positivo: um sucessor com consulta por CPF é visto.
    sucessor = copy.deepcopy(pinado)
    sucessor["x-commands"]["subject.lookup_by_cpf"] = dict(pinado["x-commands"]["operator.get"])
    sucessor["x-commands"]["document.consult"] = dict(pinado["x-commands"]["operator.get"])
    assert _comandos_de_consulta(sucessor) == ["document.consult", "subject.lookup_by_cpf"]
    # Sem falso positivo: nomes próximos que não são consulta por documento.
    assert not _tokens("operator.proposal.get") & _TOKENS_DE_CONSULTA
    assert not _tokens("offer.terms") & _TOKENS_DE_CONSULTA


def test_nenhuma_carga_do_c6_4_tem_chave_de_documento() -> None:
    pinado = _contrato()
    chaves = _chaves_de_carga(pinado)
    assert ("ProspectSavePayload", "values") in chaves  # controle: a varredura desce às cargas
    assert ("IntakeValue", "field_ref") in chaves  # e segue `$ref` até os itens
    assert _chaves_de_documento(pinado) == _EXCECAO_PINADA
    # Controle positivo: chave de documento plantada, direta e aninhada, é vista.
    sucessor = copy.deepcopy(pinado)
    sucessor["$defs"]["ProspectSavePayload"]["properties"]["cpf"] = {"type": "string"}
    sucessor["$defs"]["IntakeValue"]["properties"]["subject_document"] = {"type": "string"}
    assert _chaves_de_documento(sucessor) == _EXCECAO_PINADA | {
        ("ProspectSavePayload", "cpf"),
        ("IntakeValue", "subject_document"),
    }
    # Sem falso positivo: refs comuns não casam por substring (`descriptor_ref`, `product_ref`).
    assert _tokens("holderCpf") & _TOKENS_DE_DOCUMENTO
    assert _tokens("documentRef") & _TOKENS_DE_DOCUMENTO
    assert not _tokens("descriptor_revision") & _TOKENS_DE_DOCUMENTO
    assert not _tokens("product_ref") & _TOKENS_DE_DOCUMENTO


def test_offer_quote_sem_chave_de_documento() -> None:
    pinado = _contrato()
    ref = pinado["x-commands"]["offer.quote"]["payload_schema"].rsplit("/", 1)[1]
    assert frozenset(pinado["$defs"][ref]["properties"]) == OFFER_QUOTE_KEYS
    assert not [k for k in OFFER_QUOTE_KEYS if _tokens(k) & _TOKENS_DE_DOCUMENTO]
    # Controle positivo: o mesmo filtro acha uma chave de documento num conjunto plantado.
    plantado = OFFER_QUOTE_KEYS | {"holder_cpf"}
    assert [k for k in plantado if _tokens(k) & _TOKENS_DE_DOCUMENTO] == ["holder_cpf"]


# --- (ii) O comercial não importa resolução de documento nem verificação de titular ---------------


def _pacote_de(path: Path) -> str:
    """Pacote Python do arquivo, para resolver imports relativos (`maezo.gateway`, ...)."""
    partes = path.resolve().relative_to(_SRC.parent).with_suffix("").parts
    return ".".join(partes[:-1])


def _modulos_importados(fonte: str, pacote: str = "") -> set[str]:
    """Imports estáticos, relativos resolvidos contra `pacote`; `from X import Y` rende X e X.Y."""
    modulos: set[str] = set()
    for n in ast.walk(ast.parse(fonte)):
        if isinstance(n, ast.ImportFrom):
            modulo = n.module or ""
            if n.level:
                base = pacote.split(".") if pacote else []
                base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                modulo = ".".join([*base, *([modulo] if modulo else [])])
            modulos.add(modulo)
            modulos.update(f"{modulo}.{a.name}" for a in n.names)
        elif isinstance(n, ast.Import):
            modulos.update(a.name for a in n.names)
    return modulos


def _vedados_em(fonte: str, pacote: str = "") -> set[str]:
    return {
        m
        for m in _modulos_importados(fonte, pacote)
        if any(m == alvo or m.startswith(alvo + ".") for alvo in _ALVOS_VEDADOS)
    }


def _modulos_do_comercial() -> list[Path]:
    candidatos = [
        *sorted((_SRC / "gateway").glob("commercial*.py")),
        *sorted((_SRC / "adapters" / "amh").glob("commercial*.py")),
        _SRC / "portal" / "api" / "commercial.py",
        _SRC / "ports" / "commercial.py",
    ]
    return [p for p in candidatos if p.is_file()]


def _importadores_vedados(arquivos: Iterable[Path], pacote: str | None = None) -> dict[str, set[str]]:
    """`pacote` só para arquivo fora de `src` (cópia plantada); em `src` vem do caminho."""
    achados: dict[str, set[str]] = {}
    for p in arquivos:
        vedados = _vedados_em(p.read_text(encoding="utf-8"), pacote or _pacote_de(p))
        if vedados:
            achados[p.name] = vedados
    return achados


def test_o_comercial_nao_importa_resolucao_de_documento_nem_interop() -> None:
    arquivos = _modulos_do_comercial()
    nomes = {p.name for p in arquivos}
    # Controle: a varredura vê o gateway, o adaptador (mesmo pacote da resolução de documento), a
    # rota e a porta do comercial.
    assert {"commercial.py", "commercial_admission.py", "commercial_fences.py", "commercial_http.py"} <= nomes
    assert len([p for p in arquivos if p.name == "commercial.py"]) == 4
    assert _importadores_vedados(arquivos) == {}


@pytest.mark.parametrize(
    ("fonte", "pacote", "esperado"),
    [
        (
            "from maezo.adapters.amh.document_resolution import x",
            "",
            "maezo.adapters.amh.document_resolution",
        ),
        (
            "from maezo.adapters.amh import subject_verification",
            "",
            "maezo.adapters.amh.subject_verification",
        ),
        ("import maezo.gateway.amh_interop as interop", "", "maezo.gateway.amh_interop"),
        ("from maezo.gateway.amh_interop.sub import y", "", "maezo.gateway.amh_interop.sub"),
        ("from .amh_interop import Algo", "maezo.gateway", "maezo.gateway.amh_interop"),
        ("from . import amh_interop", "maezo.gateway", "maezo.gateway.amh_interop"),
        (
            "from ...adapters.amh import document_resolution",
            "maezo.portal.api",
            "maezo.adapters.amh.document_resolution",
        ),
    ],
)
def test_detector_de_import_vedado_acha_o_plantado(fonte: str, pacote: str, esperado: str) -> None:
    assert esperado in _vedados_em(fonte, pacote)


@pytest.mark.parametrize(
    ("fonte", "pacote"),
    [
        ("from maezo.adapters.amh import commercial_fences", ""),
        ("from maezo.adapters.amh.document_resolution_v2 import x", ""),
        ("from . import amh_interop", "maezo.portal.api"),
        ("from .commercial_admission import x", "maezo.gateway"),
        ("x = 'maezo.gateway.amh_interop'", ""),
    ],
)
def test_detector_de_import_vedado_sem_falso_positivo(fonte: str, pacote: str) -> None:
    assert _vedados_em(fonte, pacote) == set()


def test_detector_de_import_vedado_le_um_arquivo_plantado(tmp_path: Path) -> None:
    """Controle de ponta a ponta: o mesmo caminho por arquivo acha um importador plantado."""
    plantado = tmp_path / "commercial_plantado.py"
    plantado.write_text("from maezo.gateway import amh_interop\n", encoding="utf-8")
    limpo = tmp_path / "commercial_limpo.py"
    limpo.write_text("from maezo.gateway import commercial_admission\n", encoding="utf-8")
    achados = _importadores_vedados([plantado, limpo], "maezo.gateway")
    assert achados == {"commercial_plantado.py": {"maezo.gateway.amh_interop"}}


# --- (iii) Campo `document` no prospect e valor no prospect.save, com o codec padrão ---------------


def _campo_documento() -> dict[str, Any]:
    return field(
        field_ref="SYN-FIELD-DOC",
        label="Documento sintético",
        purpose_ref="SYN-PURPOSE-CONTACT",
        value_type="document",
        choices=[],
    )


def test_o_contrato_admite_value_type_document() -> None:
    """Por que a cerca existe: o C6-4 aceita `document` como `value_type` de `IntakeField`."""
    tipos = _contrato()["$defs"]["IntakeField"]["properties"]["value_type"]["enum"]
    assert "document" in tipos


def test_descritor_de_prospect_com_campo_document_recusado_no_padrao(schema_bytes: bytes) -> None:
    doc = descriptor("prospect", [_campo_documento()])
    # Cerca direta: lista de finalidades vazia (o padrão) recusa o campo `document`.
    refused(lambda: validate_intake_descriptor(doc, purpose_allowlist=frozenset()))
    # Wire real: o codec padrão recusa o resultado de `intake.describe` com esse descritor.
    wire_refuses_result(
        CommercialWire(schema_bytes),
        "intake.describe",
        {"stage": "prospect"},
        {"kind": "intake_descriptor", "descriptor": doc},
    )
    # Regra própria (E-SF-37r (b)): com a lista sintética publicada o campo `document` SEGUE
    # recusado, na cerca e no wire; a recusa não depende mais só da lista vazia.
    refused(lambda: validate_intake_descriptor(doc, purpose_allowlist=PURPOSES))
    admitido = CommercialWire(schema_bytes, intake_purpose_allowlist=PURPOSES)
    wire_refuses_result(
        admitido,
        "intake.describe",
        {"stage": "prospect"},
        {"kind": "intake_descriptor", "descriptor": doc},
    )
    # Sem falso positivo: o mesmo campo como `choice` passa com a lista sintética.
    vizinho = descriptor("prospect", [_campo_documento() | {"value_type": "choice", "choices": ["SYN-A"]}])
    validate_intake_descriptor(vizinho, purpose_allowlist=PURPOSES)
    parse(
        admitido,
        envelope("intake.describe", {"stage": "prospect"}),
        {"kind": "intake_descriptor", "descriptor": vizinho},
    )


def test_document_fora_do_prospect_nao_e_afetado_pela_regra_do_prospect() -> None:
    """A regra (b) é só do estágio `prospect`; os demais estágios pré-DS seguem a lista."""
    from maezo.adapters.amh.commercial_fences import PRE_DS_STAGES

    outros = sorted(s for s in PRE_DS_STAGES if s != "prospect")
    for estagio in outros:
        validate_intake_descriptor(descriptor(estagio, [_campo_documento()]), purpose_allowlist=PURPOSES)


def test_prospect_save_com_valor_de_documento_recusado_no_padrao(schema_bytes: bytes) -> None:
    salvar = {
        "prospect_ref": None,
        "descriptor_ref": "SYN-DESCRIPTOR",
        "descriptor_revision": 1,
        "values": [{"field_ref": "SYN-FIELD-DOC", "value": "SYN-DOC-VALOR"}],
    }
    padrao = CommercialWire(schema_bytes)
    wire_refuses_command(padrao, "prospect.save", salvar)
    # Sem falso positivo: o codec padrão aceita o `prospect.save` sem valores.
    padrao.parse_command(encode(envelope("prospect.save", salvar | {"values": []})))
    # Controle: a recusa é da lista vazia; com a lista sintética o mesmo valor é aceito.
    CommercialWire(schema_bytes, intake_purpose_allowlist=PURPOSES).parse_command(
        encode(envelope("prospect.save", salvar))
    )


# --- (iv) `IntakeValue` sem `origin` (REG-28b não representável) -----------------------------------


def _intake_value_tem_origin(contrato: dict[str, Any]) -> bool:
    return "origin" in contrato["$defs"]["IntakeValue"].get("properties", {})


def test_intake_value_sem_origin_no_contrato_pinado() -> None:
    """TEM de ficar vermelho quando um sucessor do C6-4 acrescentar `origin` a `IntakeValue`.

    Hoje a REG-28b (origem do valor de intake) não é representável no wire: o schema é fechado e
    só tem `field_ref` e `value`. Quando o campo existir, esta linha cai e força uma linha nova
    de plano; não se afrouxa este teste para acomodar o sucessor.
    """
    pinado = _contrato()
    valor = pinado["$defs"]["IntakeValue"]
    assert valor["additionalProperties"] is False
    assert set(valor["properties"]) == {"field_ref", "value"}
    assert not _intake_value_tem_origin(pinado)
    # Controle positivo: um sucessor com `origin` é visto.
    sucessor = copy.deepcopy(pinado)
    sucessor["$defs"]["IntakeValue"]["properties"]["origin"] = {"enum": ["SYN-ORIGEM"]}
    assert _intake_value_tem_origin(sucessor)
    # Sem falso positivo: `origin` em outro def não conta.
    outro = copy.deepcopy(pinado)
    outro["$defs"]["IntakeField"]["properties"]["origin"] = {"type": "string"}
    assert not _intake_value_tem_origin(outro)


# --- (v) A produção do portal não monta gateway comercial ---------------------------------------------


def _montagem_comercial(fonte: str) -> list[str]:
    """Imports, keywords e atributos com `commercial`/`public_catalog` no fonte (AST, sem docstring)."""
    marcas = ("commercial", "public_catalog")
    achados: list[str] = []
    for n in ast.walk(ast.parse(fonte)):
        nomes: list[str] = []
        if isinstance(n, ast.ImportFrom):
            nomes = [n.module or "", *(a.name for a in n.names)]
        elif isinstance(n, ast.Import):
            nomes = [a.name for a in n.names]
        elif isinstance(n, ast.keyword) and n.arg:
            nomes = [n.arg]
        elif isinstance(n, ast.Attribute):
            nomes = [n.attr]
        elif isinstance(n, ast.Name):
            nomes = [n.id]
        achados.extend(x for x in nomes if any(m in x.lower() for m in marcas))
    return achados


def test_producao_do_portal_nao_monta_gateway_comercial() -> None:
    fonte = _PRODUCAO.read_text(encoding="utf-8")
    importados = _modulos_importados(fonte, "maezo.portal.api")
    assert "maezo.portal.api.app.create_app" in importados  # controle: a varredura vê a composição
    assert _montagem_comercial(fonte) == []
    assert not [m for m in importados if "commercial" in m]
    # Controle positivo: import, keyword de composição e `app.state` plantados são vistos.
    plantado = (
        "from maezo.gateway.commercial_admission import compose_commercial_gateway\n"
        "app = create_app(identity, commercial_gateway=g)\n"
        "app.state.public_catalog_gateway = p\n"
    )
    assert set(_montagem_comercial(plantado)) >= {
        "maezo.gateway.commercial_admission",
        "compose_commercial_gateway",
        "commercial_gateway",
        "public_catalog_gateway",
    }
    # Sem falso positivo: a palavra só em docstring/comentário não conta.
    assert _montagem_comercial('"""sem gateway commercial aqui."""\n# commercial\nx = 1\n') == []
