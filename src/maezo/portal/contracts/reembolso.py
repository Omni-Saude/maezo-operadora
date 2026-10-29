"""INTERIM reembolso contracts (DL-0159): o contexto da tarefa e a conclusao das quatro telas do reembolso.

CONTEXTO — `GET /api/v1/portal/tasks/{task_id}/reembolso`. Os dados FECHADOS do pedido (enums, datas, valores
em centavos, protocolo) e a lista de anexos (tipo + referencia opaca). Nenhum texto livre, nenhum nome de
arquivo, nenhuma referencia da beneficiaria. Os rotulos dos codigos vivem no cliente web.

ANEXO — `GET /api/v1/portal/tasks/{task_id}/reembolso/anexos/{document_ref}`. Bytes, `no-store`, sem nome de
arquivo; so' sai se a referencia estiver no `documentos_refs` da instancia da tarefa e o sha256 conferir.

CONCLUSAO — `POST /api/v1/portal/tasks/{task_id}/completion` (a MESMA rota interina da DL-0049) com um
corpo por
formulario. Cada corpo revalida pelo contrato canonico do formulario (`models.py`), uma regra num lugar so'. O
servidor acrescenta `analista_id`/`auditor_id` (a referencia pseudonima de quem concluiu) — o navegador nunca
escolhe quem decidiu.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from .models import (
    Centavos,
    OpaqueRef,
    ReembolsoAuditorInputs,
    ReembolsoDecisionInputs,
    ReembolsoPagamentoInputs,
    ReembolsoPendingInputs,
)
from .queues import ClosedRead, UTCDateTime

#: Teto de um texto do analista (justificativa, fundamentacao, info pedida, texto ao beneficiario). O mesmo
#: numero da nota da DL-0049.
MAX_TEXTO = 2000
_Texto = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_TEXTO)]
_Data = Annotated[str, StringConstraints(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$")]
_Protocolo = Annotated[str, StringConstraints(strict=True, pattern=r"^REE-[0-9A-F]{4}-[0-9A-F]{4}$")]
_DocRef = Annotated[str, StringConstraints(strict=True, pattern=r"^rdoc_[0-9a-f]{32}$")]

ReembolsoTask = Literal[
    "UT_AnaliseReembolso",
    "UT_CoordenacaoReembolso",
    "UT_RevisaoAuditorMedico",
    "UT_DecidirPendenciaExpirada",
    "UT_RegistrarPagamento",
]
Relacao = Literal["titular", "dependente"]
Categoria = Literal["consulta", "exame", "terapia"]
TipoReembolso = Literal["livre_escolha", "fora_rede", "urgencia_emergencia", "indisponibilidade_rede"]
MotivoNaoRede = Literal["urgencia", "sem_prestador_no_prazo", "preferencia_fora_rede"]
DecisaoReembolso = Literal["APROVAR", "APROVAR_PARCIAL", "NEGAR", "SOLICITAR_INFO", "SOLICITAR_AUDITOR"]
TipoAnexo = Literal["pedido_medico", "nota_fiscal", "complemento"]
SituacaoPrazo = Literal["DENTRO", "FORA", "NAO_AVALIADO"]
_Resposta = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]


class ReembolsoAnexoRef(ClosedRead):
    tipo: TipoAnexo
    document_ref: _DocRef


class TaskReembolsoContextResponse(ClosedRead):
    schema_: Literal["portal-task-reembolso.v1"] = Field(default="portal-task-reembolso.v1", alias="schema")
    task_id: OpaqueRef
    task_definition_key: ReembolsoTask
    protocolo: _Protocolo | None
    relacao_com_titular: Relacao | None
    categoria_procedimento: Categoria | None
    tipo_reembolso: TipoReembolso | None
    motivo_nao_rede: MotivoNaoRede | None
    data_atendimento: _Data | None
    data_solicitacao: _Data | None
    #: Aritmetica de datas do worker; `-1` = nao calculavel. Nao e' regra de prazo.
    dias_desde_atendimento: int | None = Field(ge=-1)
    valor_solicitado_cents: Centavos | None
    #: So' na tarefa do pagamento faz sentido mostrar: a decisao ja' tomada e o valor aprovado.
    decisao_reembolso: DecisaoReembolso | None
    valor_reembolso_aprovado_cents: Centavos | None
    anexos: list[ReembolsoAnexoRef] = Field(max_length=60)
    #: A DMN informativa de prazo (nao roteia nada; hoje sempre `NAO_AVALIADO`).
    situacao_prazo: SituacaoPrazo | None = None
    #: A ultima resposta da beneficiaria a um pedido de informacao, como ela escreveu (ja' sem CPF, telefone e
    #: afins). PHI: so' exibida, nunca guardada pelo navegador.
    resposta_beneficiario: _Resposta | None = None
    observed_at: UTCDateTime


# ---------------------------------------------------------------------------------------------
# Conclusao (DL-0049 + DL-0159): um corpo por formulario, disjuntos (`extra=forbid` + `kind`).
# ---------------------------------------------------------------------------------------------


class ReembolsoDecisaoCompletionSubmission(ClosedRead):
    """Analise e coordenacao (`reembolso_decisao`)."""

    kind: Literal["reembolso_decisao"]
    decisao_reembolso: DecisaoReembolso
    valor_reembolso_aprovado_cents: Centavos | None = None
    justificativa: _Texto | None = None
    fundamentacao_contratual: _Texto | None = None
    info_solicitada: _Texto | None = None
    texto_ao_beneficiario: _Texto | None = None

    @model_validator(mode="after")
    def canonical_contract(self) -> Self:
        ReembolsoDecisionInputs.model_validate(self.model_dump(mode="json", exclude_none=True))
        return self


class ReembolsoAuditorCompletionSubmission(ClosedRead):
    """Medico auditor (`reembolso_auditor`)."""

    kind: Literal["reembolso_auditor"]
    decisao_reembolso: Literal["APROVAR", "NEGAR", "APROVAR_PARCIAL"]
    valor_reembolso_aprovado_cents: Centavos | None = None
    justificativa: _Texto | None = None
    fundamentacao_contratual: _Texto | None = None
    cid10_referencia: _Texto | None = None
    parecer_auditor: _Texto | None = None
    texto_ao_beneficiario: _Texto | None = None

    @model_validator(mode="after")
    def canonical_contract(self) -> Self:
        ReembolsoAuditorInputs.model_validate(self.model_dump(mode="json", exclude_none=True))
        return self


class ReembolsoPendenciaCompletionSubmission(ClosedRead):
    """Pendencia expirada (`reembolso_pendencia`)."""

    kind: Literal["reembolso_pendencia"]
    decisao_pendencia: Literal["cancelar_solicitacao", "conceder_prazo_extra", "seguir_analise"]

    @model_validator(mode="after")
    def canonical_contract(self) -> Self:
        ReembolsoPendingInputs.model_validate(self.model_dump(mode="json"))
        return self


class ReembolsoPagamentoCompletionSubmission(ClosedRead):
    """Registro do pagamento feito fora do sistema (`reembolso_pagamento`)."""

    kind: Literal["reembolso_pagamento"]
    data_pagamento: _Data

    @model_validator(mode="after")
    def canonical_contract(self) -> Self:
        ReembolsoPagamentoInputs.model_validate(self.model_dump(mode="json"))
        return self


ReembolsoCompletionSubmission = (
    ReembolsoDecisaoCompletionSubmission
    | ReembolsoAuditorCompletionSubmission
    | ReembolsoPendenciaCompletionSubmission
    | ReembolsoPagamentoCompletionSubmission
)
