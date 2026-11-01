"""Portal do reembolso (DL-0163): custodia S3 dos anexos, extras da conclusao, contexto e corpos."""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

import pytest
from pydantic import ValidationError

from maezo.gateway.human.completion import (
    COMPLETION_FORMS,
    CompletionExtras,
    NotesTooLongError,
    build_completion_extras,
)
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.reembolso_context import (
    REEMBOLSO_TASKS,
    ler_contexto_do_reembolso,
    parse_anexos,
)
from maezo.gateway.reembolso_anexos import AnexoIndisponivelError, CustodiaDeAnexosS3, nova_ref
from maezo.portal.contracts.reembolso import (
    ReembolsoAuditorCompletionSubmission,
    ReembolsoDecisaoCompletionSubmission,
    ReembolsoPagamentoCompletionSubmission,
    ReembolsoPendenciaCompletionSubmission,
)

CONV = "wa:amh:hk1_abc"
PDF = b"%PDF-1.4 conteudo sintetico"


# ---------------------------------------------------------------------------------------------
# Custodia S3
# ---------------------------------------------------------------------------------------------
class _S3:
    def __init__(self) -> None:
        self.objetos: dict[str, dict[str, Any]] = {}
        self.falhar = False

    def put_object(self, **kw: Any) -> Any:
        if self.falhar:
            raise RuntimeError("s3://bucket-secreto/reembolso/amh/x AccessDenied")
        self.objetos[kw["Key"]] = kw
        return {}

    def get_object(self, **kw: Any) -> Any:
        if self.falhar or kw["Key"] not in self.objetos:
            raise RuntimeError("NoSuchKey s3://bucket-secreto")
        obj = self.objetos[kw["Key"]]
        return {"Body": io.BytesIO(obj["Body"]), "ContentType": obj["ContentType"]}


def _custodia(s3: _S3, *, kms: str | None = "arn:aws:kms:sa-east-1:1:key/k") -> CustodiaDeAnexosS3:
    return CustodiaDeAnexosS3(bucket="b", kms_key_arn=kms, tenant="amh", cliente=s3)


@pytest.mark.asyncio
async def test_grava_com_sse_kms_chave_aleatoria_e_sem_nome_de_arquivo() -> None:
    s3 = _S3()
    guardado = await _custodia(s3).guardar_anexo(conversation_id=CONV, mime="application/pdf", conteudo=PDF)
    assert guardado.sha256 == hashlib.sha256(PDF).hexdigest()
    ((chave, kw),) = s3.objetos.items()
    assert chave == f"reembolso/amh/{guardado.document_ref}"
    assert kw["ServerSideEncryption"] == "aws:kms" and kw["SSEKMSKeyId"].startswith("arn:aws:kms")
    assert kw["Metadata"]["sha256"] == guardado.sha256 and kw["Metadata"]["conversa"] == CONV
    assert "filename" not in json.dumps(kw["Metadata"]).lower()


@pytest.mark.asyncio
async def test_le_e_confere_o_sha256_registrado() -> None:
    s3 = _S3()
    custodia = _custodia(s3)
    guardado = await custodia.guardar_anexo(conversation_id=CONV, mime="application/pdf", conteudo=PDF)
    anexo = await custodia.abrir(guardado.document_ref, sha256_esperado=guardado.sha256)
    assert anexo.conteudo == PDF and anexo.mime == "application/pdf"
    # objeto trocado no bucket: recusado, nunca entregue
    s3.objetos[f"reembolso/amh/{guardado.document_ref}"]["Body"] = b"%PDF trocado"
    with pytest.raises(AnexoIndisponivelError):
        await custodia.abrir(guardado.document_ref, sha256_esperado=guardado.sha256)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conversa", "mime", "conteudo"),
    [
        ("wa:outro:hk1_x", "application/pdf", PDF),
        (CONV, "text/html", PDF),
        (CONV, "application/pdf", b""),
        (CONV, "image/png", b"x" * (10 * 1024 * 1024 + 1)),
    ],
    ids=["outro_tenant", "mime", "vazio", "grande"],
)
async def test_recusa_antes_de_gravar(conversa: str, mime: str, conteudo: bytes) -> None:
    s3 = _S3()
    with pytest.raises(AnexoIndisponivelError):
        await _custodia(s3).guardar_anexo(conversation_id=conversa, mime=mime, conteudo=conteudo)
    assert s3.objetos == {}


@pytest.mark.asyncio
async def test_o_portal_sem_chave_kms_nao_grava_e_falha_nao_vaza_bucket() -> None:
    s3 = _S3()
    with pytest.raises(AnexoIndisponivelError):
        await _custodia(s3, kms=None).guardar_anexo(
            conversation_id=CONV, mime="application/pdf", conteudo=PDF
        )
    s3.falhar = True
    with pytest.raises(AnexoIndisponivelError) as erro:
        await _custodia(s3).guardar_anexo(conversation_id=CONV, mime="application/pdf", conteudo=PDF)
    assert "bucket-secreto" not in str(erro.value) and erro.value.__cause__ is None
    with pytest.raises(AnexoIndisponivelError):
        await _custodia(s3).abrir(nova_ref(), sha256_esperado="a" * 64)
    with pytest.raises(AnexoIndisponivelError):
        await _custodia(_S3()).abrir("../x", sha256_esperado="a" * 64)


# ---------------------------------------------------------------------------------------------
# Extras da conclusao
# ---------------------------------------------------------------------------------------------
def test_extras_tipam_e_passam_os_textos_pelo_scrub() -> None:
    form = COMPLETION_FORMS["reembolso_decisao"]
    extras = build_completion_extras(
        form,
        {
            "valor_reembolso_aprovado_cents": 8000,
            "justificativa": "CPF 123.456.789-01 informado",
            "fundamentacao_contratual": "Clausula 5",
            "texto_ao_beneficiario": "Reducao conforme o contrato",
        },
    )
    valores = {nome: (tipo, valor) for nome, tipo, valor in extras.values}
    assert valores["valor_reembolso_aprovado_cents"] == ("Long", 8000)
    assert "123.456.789-01" not in str(valores["justificativa"][1])
    com_quem = extras.with_principal(form.principal_variable, "prn_analista")
    assert ("analista_id", "String", "prn_analista") in com_quem.values
    assert com_quem.digest != extras.digest and len(extras.digest) == 64


@pytest.mark.parametrize(
    "raw",
    [
        {"nao_existe": "x"},
        {"valor_reembolso_aprovado_cents": "8000"},
        {"valor_reembolso_aprovado_cents": -1},
        {"justificativa": "   "},
    ],
)
def test_extras_fora_do_formulario_sao_recusados(raw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        build_completion_extras(COMPLETION_FORMS["reembolso_decisao"], raw)


def test_data_de_pagamento_e_texto_longo() -> None:
    pagamento = COMPLETION_FORMS["reembolso_pagamento"]
    assert pagamento.outcome_variable is None and pagamento.variables == ("data_pagamento",)
    assert build_completion_extras(pagamento, {"data_pagamento": "2026-10-27"}).values == (
        ("data_pagamento", "String", "2026-10-27"),
    )
    with pytest.raises(ValueError):
        build_completion_extras(pagamento, {"data_pagamento": "27/10/2026"})
    with pytest.raises(NotesTooLongError):
        build_completion_extras(COMPLETION_FORMS["reembolso_decisao"], {"justificativa": "x" * 2001})
    assert isinstance(CompletionExtras(()).with_principal(None, "p"), CompletionExtras)


def test_o_navegador_nunca_preenche_quem_decidiu() -> None:
    for chave in ("reembolso_decisao", "reembolso_auditor"):
        form = COMPLETION_FORMS[chave]
        assert form.principal_variable not in form.variables
        with pytest.raises(ValueError):
            build_completion_extras(form, {form.principal_variable or "": "prn_forjado"})


# ---------------------------------------------------------------------------------------------
# Corpos de conclusao (revalidam pelo contrato canonico)
# ---------------------------------------------------------------------------------------------
def test_corpos_validos_do_reembolso() -> None:
    ReembolsoDecisaoCompletionSubmission.model_validate(
        {
            "kind": "reembolso_decisao",
            "decisao_reembolso": "APROVAR",
            "valor_reembolso_aprovado_cents": "15000",
        }
    )
    ReembolsoDecisaoCompletionSubmission.model_validate(
        {"kind": "reembolso_decisao", "decisao_reembolso": "SOLICITAR_INFO", "info_solicitada": "Envie a NF"}
    )
    ReembolsoAuditorCompletionSubmission.model_validate(
        {
            "kind": "reembolso_auditor",
            "decisao_reembolso": "NEGAR",
            "justificativa": "j",
            "fundamentacao_contratual": "f",
            "cid10_referencia": "Z00.0",
            "parecer_auditor": "p",
            "texto_ao_beneficiario": "t",
        }
    )
    ReembolsoPendenciaCompletionSubmission.model_validate(
        {"kind": "reembolso_pendencia", "decisao_pendencia": "seguir_analise"}
    )
    ReembolsoPagamentoCompletionSubmission.model_validate(
        {"kind": "reembolso_pagamento", "data_pagamento": "2026-10-27"}
    )


@pytest.mark.parametrize(
    "corpo",
    [
        {"kind": "reembolso_decisao", "decisao_reembolso": "NEGAR", "justificativa": "j"},
        {"kind": "reembolso_decisao", "decisao_reembolso": "SOLICITAR_INFO"},
        {"kind": "reembolso_decisao", "decisao_reembolso": "APROVAR"},
        {
            "kind": "reembolso_decisao",
            "decisao_reembolso": "APROVAR",
            "valor_reembolso_aprovado_cents": "1",
            "analista_id": "prn_forjado",
        },
        {"kind": "reembolso_decisao", "decisao_reembolso": "TALVEZ"},
    ],
)
def test_corpos_invalidos_sao_recusados(corpo: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReembolsoDecisaoCompletionSubmission.model_validate(corpo)


# ---------------------------------------------------------------------------------------------
# Contexto do reembolso (leitor sobre o cliente fixo do motor)
# ---------------------------------------------------------------------------------------------
_SHA = "b" * 64
_REF = "rdoc_" + "1" * 32


class _Motor:
    def __init__(self, *, processo: str = "SP-OP-REEMBOLSO-001", tarefa: str = "UT_AnaliseReembolso") -> None:
        self.processo = processo
        self.tarefa = tarefa
        self.pedidos: list[str] = []
        self.variaveis: dict[str, tuple[str, Any]] = {
            "protocolo_reembolso": ("String", "REE-AB12-CD34"),
            "relacao_com_titular": ("String", "titular"),
            "categoria_procedimento": ("String", "exame"),
            "tipo_reembolso": ("String", "fora_rede"),
            "motivo_nao_rede": ("String", "urgencia"),
            "data_atendimento": ("String", "2026-10-25"),
            "valor_solicitado_cents": ("Long", 15000),
            "documentos_refs": (
                "Json",
                json.dumps(
                    [
                        {"tipo": "nota_fiscal", "document_ref": _REF, "sha256": _SHA},
                        {"tipo": "nota_fiscal", "document_ref": "../x", "sha256": _SHA},
                    ]
                ),
            ),
            "resposta_beneficiario": ("String", "O atendimento foi de urgencia"),
            "situacao_prazo": ("String", "NAO_AVALIADO"),
            "texto_ao_beneficiario": ("String", "NUNCA deve ser lido"),
        }

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        if path.startswith("/task/"):
            return {"tenantId": "amh", "taskDefinitionKey": self.tarefa, "processInstanceId": "inst-1"}
        if path.startswith("/history/process-instance/"):
            return {"tenantId": "amh", "processDefinitionKey": self.processo}
        assert params is not None
        nome = params["variableName"]
        self.pedidos.append(nome)
        if nome not in self.variaveis:
            return []
        tipo, valor = self.variaveis[nome]
        return [{"name": nome, "type": tipo, "value": valor}]

    def same_tenant(self, payload: Any) -> None:
        if payload.get("tenantId") != "amh":
            raise GatewayRefusalError("task_unavailable")


@pytest.mark.asyncio
async def test_contexto_le_so_variaveis_de_nome_fixo_e_descarta_anexo_fora_da_forma() -> None:
    motor = _Motor()
    contexto = await ler_contexto_do_reembolso(motor, task_id="task-1")
    assert contexto.protocolo == "REE-AB12-CD34" and contexto.valor_solicitado_cents == 15000
    assert [a.document_ref for a in contexto.anexos] == [_REF]
    assert contexto.resposta_beneficiario == "O atendimento foi de urgencia"
    assert contexto.situacao_prazo == "NAO_AVALIADO"
    assert "texto_ao_beneficiario" not in motor.pedidos and "justificativa" not in motor.pedidos
    assert contexto.anexo(_REF) is not None and contexto.anexo("rdoc_" + "2" * 32) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("processo", "tarefa"), [("SP-OP-CANCEL-001", "UT_AnaliseReembolso"), ("SP-OP-REEMBOLSO-001", "UT_Outra")]
)
async def test_contexto_recusa_outro_processo_ou_tarefa(processo: str, tarefa: str) -> None:
    with pytest.raises(GatewayRefusalError):
        await ler_contexto_do_reembolso(_Motor(processo=processo, tarefa=tarefa), task_id="task-1")


def test_parse_anexos_e_tarefas() -> None:
    assert parse_anexos("nao e json") == ()
    assert (
        parse_anexos([{"tipo": "complemento", "document_ref": _REF, "sha256": _SHA}])[0].tipo == "complemento"
    )
    assert parse_anexos([{"tipo": "outro", "document_ref": _REF, "sha256": _SHA}]) == ()
    assert "UT_RegistrarPagamento" in REEMBOLSO_TASKS
