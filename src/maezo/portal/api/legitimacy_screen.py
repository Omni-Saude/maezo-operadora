"""REG-1.1 — OP21 `legitimacy.screen`: a superfície staff interna da triagem de legitimidade.

Molde D-36 (`commission_policies.py`, #987): router DARK com `ROUTE_PATH = PREFIX +
"/legitimacy-screens"`, fábrica SEM default e recusa 403 flag / 503 slot antes de qualquer
efeito. A tela registra a verificação de legitimidade da PJ contratante em planos coletivos
sobre o checklist FECHADO do parecer do Diretor Jurídico (2026-10-26, snapshot canônico
`docs/compliance/legal/textos-base/PARECER-OP21-2026-10-26-LUCAS.txt` sha256 `d80740cc…`):
os campos são EXATAMENTE os cinco critérios do §5, com o rótulo VERBATIM de cada um exposto
pelo manifest `GET .../checklist`; a vedação do art. 29 §1º é EXIBIDA e nunca é campo
(desenho §5: é o critério que o ESTADO bloqueante aplica — e o estado bloqueante é postura
sucessora, desenho §4, não esta superfície).

Três fechamentos estruturais desta superfície:

- **Âncora é propriedade do critério, nunca input.** A tabela `_CAMPO_ANCORAS` fixa, por
  campo, o artigo PRÓPRIO da RN 557/2022 (parecer §4 — desdobro por assunto); o payload não
  tem slot para âncora (`norm_anchor` é nome reservado, 403). A citação da PENALIDADE na
  tela é a forma CURTA "RN 489/2022, art. 23-A" (parecer §1) — as formas imprecisas
  ("RN 659/2025, art. 23-A", "RN 557/659") não existem em texto nenhum da superfície, e o
  substrato recusa qualquer âncora persistida que as carregue (PR #1022).
- **O recibo é o modelo do SUBSTRATO (#1022)** — esta superfície chama o store, não duplica
  modelo: `_screen_from_submission` monta o `LegitimacyScreenRecord` fechado (extra="forbid")
  e o `LegitimacyScreenStore` persiste. Nenhum critério declarado = recusa (a verificação
  sem critério não é verificação); `status` é a PROJEÇÃO dos resultados declarados
  (`not_proven` → `invalid`; senão `pending` se houver pendente; senão `valid`) — rotulagem
  do registro, nunca consequência comercial: `would_block` é `False` constante aqui (quem
  escreve o que o enforcing decidiria é o guard, PR 3) e a sinalização do art. 39 não é
  input de operador (é da postura, PRs 3–4).
- **Zero caracterização jurídica do canal** (parecer §3, recomendação técnica): nenhum texto
  exibido descreve, qualifica ou caracteriza o papel do canal; a custódia de evidência é
  atribuída só a `operadora`/`administradora` (domínio fechado do substrato, art. 12).

Revalidação (desenho §3): renovação, reajuste ou troca de contratante é NOVA
`screen_revision` — o POST de revalidação exige a revisão conhecida (molde D-36 `revision`)
e o servidor minta `+1`; a linha anterior é imutável, nunca update.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any, Final, Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.routing import Match

from maezo.gateway.commercial_legitimacy_screen import (
    CriterionResultKind,
    LegitimacyCriterion,
    LegitimacyScreenRecord,
    LegitimacyScreenStatus,
    LegitimacyScreenStore,
    LegitimacyScreenStoreError,
)
from maezo.gateway.intake.models import IntakeError
from maezo.gateway.intake.service import session_ceiling
from maezo.portal.api.auth import AuthenticationError
from maezo.portal.api.decisions import _body, _nonfinite, _pairs
from maezo.portal.api.intakes import PREFIX
from maezo.portal.api.session import HumanSessionResolver, ResolvedHumanSession
from maezo.portal.contracts.legitimacy_screens import (
    ChecklistCriterion,
    ChecklistField,
    ChecklistOption,
    LegitimacyChecklistManifest,
    LegitimacyScreenRevalidation,
    LegitimacyScreenSubmission,
    PortalLegitimacyScreenError,
)

LegitimacyScreenServiceFactory = Callable[[HumanSessionResolver], "LegitimacyScreenService"]
ROUTE_PATH = PREFIX + "/legitimacy-screens"

#: The closed refusal vocabulary of this boundary — the wire error's Literal and this typed
#: error's argument are the SAME closed set (molde D-36). `screen_conflict` é a violação da
#: unicidade de instância do substrato (mesma oportunidade + revisão sob outro `screen_ref`,
#: `contract_mismatch` do store) traduzida para a fronteira HTTP — recusa LOUD, nunca
#: overwrite silencioso.
LegitimacyScreenCode = Literal[
    "invalid_request",
    "authentication_unavailable",
    "operation_forbidden",
    "field_not_client_owned",
    "resource_unavailable",
    "screen_conflict",
    "stale_revision",
    "dependency_unavailable",
]

STATUS: dict[str, int] = {
    "invalid_request": 400,
    "authentication_unavailable": 401,
    "operation_forbidden": 403,
    "field_not_client_owned": 403,
    "resource_unavailable": 404,
    "screen_conflict": 409,
    "stale_revision": 409,
    "dependency_unavailable": 503,
}
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": PortalLegitimacyScreenError} for status in STATUS.values()
}


class LegitimacyScreenBoundaryError(RuntimeError):
    """One refusal of this boundary, carrying its closed code; cause text never travels."""

    def __init__(self, code: LegitimacyScreenCode = "dependency_unavailable") -> None:
        self.code = code
        super().__init__(code)


def legitimacy_screen_error(code: str = "dependency_unavailable") -> JSONResponse:
    if code not in STATUS:
        code = "dependency_unavailable"
    return JSONResponse({"code": code}, status_code=STATUS[code])


#: Field names this boundary refuses ON SIGHT (403 `field_not_client_owned`, before the
#: closed DTO): the facts the SERVER owns — the screen identity (`screen_ref` is
#: server-minted), the record state (`status`, `would_block`, `art39_signaled`,
#: `art39_anchor` — a sinalização do art. 39 é da postura, nunca de operador), a
#: MATERIALIZAÇÃO do checklist (`criteria` e `norm_anchor` são derivados dos campos
#: fechados; a âncora é propriedade do critério) and the audit/tenant columns. Deliberately
#: NOT here: `screen_revision` on the revalidation path — it is the caller's ECHOED
#: EXPECTATION (the house `business_revision` mold, D-36 `revision`); on the create path the
#: create shape simply has no such field and it dies in the DTO.
_RESERVED_FIELDS: frozenset[str] = frozenset(
    {
        "tenant",
        "operation",
        "screen_ref",
        "status",
        "would_block",
        "art39_signaled",
        "art39_anchor",
        "criteria",
        "norm_anchor",
        "created_by",
        "created_at",
        "updated_at",
    }
)

_SCREEN_REF_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SCREEN_REF_PREFIX = "scr-"

# ----------------------------------------------------------------------------------
# As âncoras normativas — propriedade do critério, fixadas aqui, nunca digitadas
# (parecer §4: contratação cita RN 557/2022 por artigo PRÓPRIO, separada da penalidade).
# ----------------------------------------------------------------------------------

_ANCHOR_CONTRATACAO: Final = "RN 557/2022, art. 29"
_ANCHOR_VINCULO: Final = "RN 557/2022, art. 5º"
_ANCHOR_LISTA: Final = "RN 557/2022, art. 15"
_ANCHOR_UM_ANO: Final = "RN 557/2022, art. 16"
_ANCHOR_DOCS: Final = "RN 557/2022, arts. 9º a 14"
_ANCHOR_RECONFIRMACAO: Final = "RN 557/2022, art. 10"

#: Campo do checklist → âncora normativa EXIBIDA e PERSISTIDA. A vedação do art. 29 §1º não
#: é campo (é critério estrutural do estado bloqueante) e a penalidade não é âncora de
#: critério de contratação — as duas formas de citação nunca se juntam (parecer §1/§4).
_CAMPO_ANCORAS: Final[dict[str, str]] = {
    "adesao_lista": _ANCHOR_LISTA,
    "vinculo_beneficiario_contratante": _ANCHOR_VINCULO,
    "constituida_um_ano": _ANCHOR_UM_ANO,
    "documentos_art9": _ANCHOR_DOCS,
    "reconfirmacao_anual": _ANCHOR_RECONFIRMACAO,
}

# ----------------------------------------------------------------------------------
# O manifest fechado que a tela renderiza — os 5 critérios do parecer §5, VERBATIM.
# Textos de UI FIXADOS aqui (a tabela do gate cego é a tabela abaixo + o PR).
# ----------------------------------------------------------------------------------

_RESULTADOS: Final = (
    ChecklistOption(valor="proven", rotulo="comprovado"),
    ChecklistOption(valor="not_proven", rotulo="não comprovado"),
    ChecklistOption(valor="not_applicable", rotulo="não se aplica"),
    ChecklistOption(valor="pending", rotulo="pendente de verificação"),
)

CHECKLIST: Final[LegitimacyChecklistManifest] = LegitimacyChecklistManifest(
    resultados=_RESULTADOS,
    criterios=(
        ChecklistCriterion(
            criterion_id="contratacao_tipo_forma",
            rotulo=(
                "tipo de contratação (empresarial ou adesão) e forma (direta, com "
                "administradora ou com administradora coestipulante — art. 29)"
            ),
            norm_anchor=_ANCHOR_CONTRATACAO,
            campos=(
                ChecklistField(campo_id="contract_kind", norm_anchor=_ANCHOR_CONTRATACAO),
                ChecklistField(campo_id="contracting_form", norm_anchor=_ANCHOR_CONTRATACAO),
            ),
            opcoes=(
                ChecklistOption(valor="empresarial", rotulo="empresarial"),
                ChecklistOption(valor="adesao", rotulo="adesão"),
                ChecklistOption(valor="empresario_individual", rotulo="empresário individual"),
                ChecklistOption(valor="direta", rotulo="direta — art. 29, I"),
                ChecklistOption(
                    valor="administradora", rotulo="com administradora de benefícios — art. 29, II"
                ),
                ChecklistOption(
                    valor="coestipulante", rotulo="com administradora coestipulante — art. 29, III"
                ),
            ),
        ),
        ChecklistCriterion(
            criterion_id="vinculo_beneficiario_contratante",
            rotulo="empresarial: vínculo do beneficiário com a contratante (art. 5º)",
            norm_anchor=_ANCHOR_VINCULO,
            campos=(
                ChecklistField(campo_id="vinculo_beneficiario_contratante", norm_anchor=_ANCHOR_VINCULO),
            ),
        ),
        ChecklistCriterion(
            criterion_id="adesao_lista_constituicao",
            rotulo=(
                "adesão: contratante na lista do art. 15 e constituída há pelo menos um ano "
                "(art. 16, salvo conselhos, entidades de classe e sindicatos)"
            ),
            norm_anchor=_ANCHOR_LISTA,
            campos=(
                ChecklistField(campo_id="contratante_lista_art15", norm_anchor=_ANCHOR_LISTA),
                ChecklistField(
                    campo_id="constituida_um_ano",
                    norm_anchor=_ANCHOR_UM_ANO,
                    condicao_estrutural=(
                        "exceto as pessoas jurídicas previstas nos incisos I e II do art. 15 "
                        "(conselhos profissionais, entidades de classe e sindicatos) — a "
                        "isenção é derivada do inciso, nunca digitada"
                    ),
                ),
            ),
            opcoes=(
                ChecklistOption(
                    valor="inciso_i",
                    rotulo="conselhos profissionais e entidades de classe, com registro "
                    "necessário ao exercício da profissão (inciso I)",
                ),
                ChecklistOption(
                    valor="inciso_ii",
                    rotulo="sindicatos, centrais sindicais e respectivas federações e "
                    "confederações (inciso II)",
                ),
                ChecklistOption(
                    valor="inciso_iii",
                    rotulo="associações profissionais legalmente constituídas (inciso III)",
                ),
                ChecklistOption(
                    valor="inciso_iv",
                    rotulo="cooperativas que congreguem membros de categorias ou classes de "
                    "profissões regulamentadas (inciso IV)",
                ),
                ChecklistOption(
                    valor="inciso_v",
                    rotulo="caixas de assistência e fundações de direito privado (inciso V)",
                ),
                ChecklistOption(
                    valor="inciso_vi",
                    rotulo="entidades previstas nas Leis nº 7.395/1985 e nº 7.398/1985 (inciso VI)",
                ),
            ),
        ),
        ChecklistCriterion(
            criterion_id="empresario_documentos_reconfirmacao",
            rotulo="empresário individual: documentos do art. 9º e reconfirmação anual (art. 10)",
            norm_anchor=_ANCHOR_DOCS,
            campos=(
                ChecklistField(campo_id="documentos_art9", norm_anchor=_ANCHOR_DOCS),
                ChecklistField(campo_id="reconfirmacao_anual", norm_anchor=_ANCHOR_RECONFIRMACAO),
            ),
        ),
        ChecklistCriterion(
            criterion_id="vedacao_inclusao_sem_pj",
            rotulo="nenhuma inclusão de beneficiário sem a pessoa jurídica legitimada (art. 29, § 1º)",
            norm_anchor="RN 557/2022, art. 29, § 1º",
            estrutural=True,
        ),
    ),
)

#: Os incisos com isenção estrutural do art. 16 vivem no CONTRATO
#: (`INCISOS_ISENTOS_ART16`) — a derivação é da forma, não da superfície, e o teste pina
#: que a constante do contrato é a mesma que o `ChecklistField.condicao_estrutural` descreve.


def _new_screen_ref() -> str:
    """`scr-` + 128 bits of server randomness — matches `ScreenRef` and the table CHECK."""
    return _SCREEN_REF_PREFIX + secrets.token_hex(16)


def _status_consolidado(resultados: tuple[CriterionResultKind, ...]) -> LegitimacyScreenStatus:
    """A PROJEÇÃO dos resultados declarados no pin do substrato — rótulo, não veredito.

    `not_proven` → `invalid` (o mais severo primeiro: um critério não comprovado não é
    suavizado por outro pendente); senão `pending` se houver pendente (PENDENTE ≠ APROVADO
    por omissão — registry OP21); senão `valid`. O que dali decorre no caminho comercial é
    postura sucessora (desenho §4), nunca esta função.
    """
    if "not_proven" in resultados:
        return "invalid"
    if "pending" in resultados:
        return "pending"
    return "valid"


def _screen_from_submission(
    submission: LegitimacyScreenSubmission | LegitimacyScreenRevalidation,
    *,
    tenant: str,
    screen_ref: str,
    screen_revision: int,
    author: str,
) -> LegitimacyScreenRecord:
    """Materializa o recibo do SUBSTRATO a partir do checklist fechado — sem modelo novo.

    As âncoras vêm da tabela fechada (`_CAMPO_ANCORAS`), nunca do payload; o critério da
    lista do art. 15 é registrado com o inciso declarado; `would_block` é `False` constante
    (quem registra o que o enforcing decidiria é o guard, PR 3) e a sinalização do art. 39
    não é input de operador.
    """
    criteria: list[LegitimacyCriterion] = []
    if submission.contratante_lista_art15 is not None:
        criteria.append(
            LegitimacyCriterion(
                criterion_id="adesao_lista",
                result="proven",
                norm_anchor=_CAMPO_ANCORAS["adesao_lista"],
            )
        )
    for campo_id in (
        "vinculo_beneficiario_contratante",
        "constituida_um_ano",
        "documentos_art9",
        "reconfirmacao_anual",
    ):
        check = getattr(submission, campo_id)
        if check is None:
            continue
        criteria.append(
            LegitimacyCriterion(
                criterion_id=campo_id,
                result=check.result,
                norm_anchor=_CAMPO_ANCORAS[campo_id],
                evidence_refs=check.evidence_refs,
            )
        )
    now = datetime.now(UTC)
    return LegitimacyScreenRecord(
        tenant=tenant,
        screen_ref=screen_ref,
        screen_revision=screen_revision,
        legal_entity_ref=submission.legal_entity_ref,
        opportunity_ref=submission.opportunity_ref,
        contract_kind=submission.contract_kind,
        contracting_form=submission.contracting_form,
        criteria=tuple(criteria),
        status=_status_consolidado(tuple(criterion.result for criterion in criteria)),
        would_block=False,
        art39_signaled=False,
        reconfirmation_due_at=submission.reconfirmation_due_at,
        created_by=author,
        created_at=now,
        updated_at=now,
    )


class LegitimacyScreenService:
    """Session resolution + the flag/staff gates + the checklist machine; no blocking here.

    O portão de superfície é o mesmo da D-36 (audiência `staff`); o PERFIL operacional da
    tela (operadora/administradora — desenho §9 E-3) é decisão de operação ainda aberta e
    chega como aperto sucessor, nunca por esta chave.
    """

    def __init__(self, resolver: HumanSessionResolver, store: LegitimacyScreenStore) -> None:
        self.resolver, self.store = resolver, store

    def _surface_enabled(self) -> bool:
        return self.resolver.settings.collective_legitimacy_screen

    async def _authorize(
        self, session_secret: str, *, csrf: str | None = None, origin: str | None = None
    ) -> ResolvedHumanSession:
        session = await self.resolver.resolve(session_secret)
        if datetime.now(UTC) >= session_ceiling(session):
            raise AuthenticationError()
        if not self._surface_enabled():
            # The surface is not enabled in THIS deployment; the session proves nothing about it.
            raise LegitimacyScreenBoundaryError("operation_forbidden")
        if session.membership.audience != "staff":
            # An internal staff surface: a beneficiary/provider/vendor session never reaches
            # the store, with the flag ON or OFF.
            raise LegitimacyScreenBoundaryError("operation_forbidden")
        if (csrf is not None or origin is not None) and (
            origin != self.resolver.settings.public_origin
            or not csrf
            or not secrets.compare_digest(csrf, session.record.csrf_token)
        ):
            raise LegitimacyScreenBoundaryError("authentication_unavailable")
        current = await self.resolver.resolve(session_secret)
        if current.principal != session.principal or datetime.now(UTC) >= session_ceiling(current):
            raise AuthenticationError()
        return current

    async def checklist(self, session_secret: str) -> LegitimacyChecklistManifest:
        """The CLOSED checklist definition the staff UI renders — the five §5 criteria."""
        await self._authorize(session_secret)
        return CHECKLIST

    async def register(
        self,
        session_secret: str,
        *,
        csrf: str | None,
        origin: str | None,
        submission: LegitimacyScreenSubmission,
    ) -> LegitimacyScreenRecord:
        session = await self._authorize(session_secret, csrf=csrf, origin=origin)
        record = _screen_from_submission(
            submission,
            tenant=self.resolver.settings.tenant,
            screen_ref=_new_screen_ref(),
            screen_revision=1,
            author=session.principal.principal_ref,
        )
        return await self._save(record)

    async def revalidate(
        self,
        session_secret: str,
        *,
        csrf: str | None,
        origin: str | None,
        screen_ref: str,
        submission: LegitimacyScreenRevalidation,
    ) -> LegitimacyScreenRecord:
        session = await self._authorize(session_secret, csrf=csrf, origin=origin)
        if not _SCREEN_REF_PATTERN.fullmatch(screen_ref):
            raise LegitimacyScreenBoundaryError("invalid_request")
        current = await self._read_latest_of(screen_ref, submission.legal_entity_ref)
        if submission.screen_revision != current.screen_revision:
            raise LegitimacyScreenBoundaryError("stale_revision")
        record = _screen_from_submission(
            submission,
            tenant=self.resolver.settings.tenant,
            screen_ref=current.screen_ref,
            screen_revision=current.screen_revision + 1,
            author=session.principal.principal_ref,
        )
        return await self._save(record)

    async def read(
        self, session_secret: str, screen_ref: str, screen_revision: int
    ) -> LegitimacyScreenRecord:
        await self._authorize(session_secret)
        if not _SCREEN_REF_PATTERN.fullmatch(screen_ref) or screen_revision < 1:
            raise LegitimacyScreenBoundaryError("invalid_request")
        record = await self.store.read(self.resolver.settings.tenant, screen_ref, screen_revision)
        if record is None:
            raise LegitimacyScreenBoundaryError("resource_unavailable")
        return record

    async def latest_for_legal_entity(
        self, session_secret: str, legal_entity_ref: str
    ) -> LegitimacyScreenRecord:
        await self._authorize(session_secret)
        if not legal_entity_ref:
            raise LegitimacyScreenBoundaryError("invalid_request")
        return await self._read_latest_of(None, legal_entity_ref)

    async def _read_latest_of(self, screen_ref: str | None, legal_entity_ref: str) -> LegitimacyScreenRecord:
        """Última revisão da PJ; com `screen_ref`, EXIGE que a instância seja a mesma.

        Um ref ausente e um ref de outro tenant são indistinguíveis (molde D-36) — e a
        exigência do ref no eixo da PJ é o que impede revalidar instância que deixou de ser
        a tela atual da contratante.
        """
        record = await self.store.latest_for_legal_entity(self.resolver.settings.tenant, legal_entity_ref)
        if record is None or (screen_ref is not None and record.screen_ref != screen_ref):
            raise LegitimacyScreenBoundaryError("resource_unavailable")
        return record

    async def _save(self, record: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
        try:
            return await self.store.save(record)
        except LegitimacyScreenStoreError as exc:
            if exc.code == "contract_mismatch":
                raise LegitimacyScreenBoundaryError("screen_conflict") from None
            raise LegitimacyScreenBoundaryError("dependency_unavailable") from None


def _service(request: Request) -> LegitimacyScreenService:
    factory: LegitimacyScreenServiceFactory | None = request.app.state.legitimacy_screen_service_factory
    resolver = request.app.state.human_session_resolver
    if factory is None:
        raise IntakeError()
    service = factory(resolver)
    if not isinstance(service, LegitimacyScreenService) or service.resolver is not resolver:
        raise IntakeError()
    return service


def _secret(request: Request) -> str:
    from maezo.portal.api.app import _SESSION, _cookie

    try:
        return _cookie(request, _SESSION)
    except AuthenticationError:
        raise IntakeError("authentication_unavailable") from None


class LegitimacyScreenRoute(APIRoute):
    """Bounded framing first, then the reserved-fields guard, then the closed DTO.

    Mirrors the D-36 route with the OP21-specific on-sight order: a POST naming a
    server-owned fact (state, art. 39 signal, criteria/anchor materialization, audit) is a
    403 `field_not_client_owned` before the DTO; anything OUTSIDE the closed checklist
    shape dies in the DTO (400) — the five §5 criteria are the whole input surface. All of
    it holds with ZERO store calls and zero session dependence.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def checked(request: Request) -> Response:
            try:
                data = await _body(request)
                if request.method == "POST":
                    if request.scope.get("query_string", b"") or request.headers.getlist("content-type") != [
                        "application/json"
                    ]:
                        raise ValueError
                    if any(len(request.headers.getlist(name)) != 1 for name in ("origin", "x-csrf-token")):
                        return legitimacy_screen_error("authentication_unavailable")
                    parsed = json.loads(
                        data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite
                    )
                    if type(parsed) is not dict:
                        raise ValueError
                    if _RESERVED_FIELDS & set(parsed):
                        return legitimacy_screen_error("field_not_client_owned")
                elif data:
                    raise ValueError
            except Exception:
                return legitimacy_screen_error("invalid_request")

            async def replay() -> dict[str, Any]:
                return {"type": "http.request", "body": data, "more_body": False}

            try:
                return await original(Request(request.scope, receive=replay))
            except LegitimacyScreenBoundaryError as exc:
                return legitimacy_screen_error(exc.code)
            except IntakeError as exc:
                return legitimacy_screen_error(exc.code)
            except RequestValidationError:
                return legitimacy_screen_error("invalid_request")
            except AuthenticationError:
                return legitimacy_screen_error("authentication_unavailable")
            except Exception:
                return legitimacy_screen_error()

        return checked


legitimacy_screen_router = APIRouter(route_class=LegitimacyScreenRoute)


def is_legitimacy_screen_request(request: Request) -> bool:
    return any(route.matches(request.scope)[0] != Match.NONE for route in legitimacy_screen_router.routes)


@legitimacy_screen_router.get(
    ROUTE_PATH + "/checklist", response_model=LegitimacyChecklistManifest, responses=ERRORS
)
async def read_legitimacy_checklist(request: Request) -> Response:
    if request.scope.get("query_string", b""):
        raise LegitimacyScreenBoundaryError("invalid_request")
    checklist = await _service(request).checklist(_secret(request))
    return Response(content=checklist.model_dump_json(), status_code=200, media_type="application/json")


@legitimacy_screen_router.get(
    ROUTE_PATH + "/contratante/{legal_entity_ref}", response_model=LegitimacyScreenRecord, responses=ERRORS
)
async def read_latest_legitimacy_screen(request: Request, legal_entity_ref: str) -> Response:
    if request.scope.get("query_string", b""):
        raise LegitimacyScreenBoundaryError("invalid_request")
    record = await _service(request).latest_for_legal_entity(_secret(request), legal_entity_ref)
    return Response(content=record.model_dump_json(), status_code=200, media_type="application/json")


@legitimacy_screen_router.post(
    ROUTE_PATH, response_model=LegitimacyScreenRecord, status_code=201, responses=ERRORS
)
async def create_legitimacy_screen(request: Request, body: LegitimacyScreenSubmission) -> Response:
    record = await _service(request).register(
        _secret(request),
        csrf=request.headers.get("x-csrf-token"),
        origin=request.headers.get("origin"),
        submission=body,
    )
    return Response(content=record.model_dump_json(), status_code=201, media_type="application/json")


@legitimacy_screen_router.post(
    ROUTE_PATH + "/{screen_ref}", response_model=LegitimacyScreenRecord, status_code=201, responses=ERRORS
)
async def revalidate_legitimacy_screen(
    request: Request, screen_ref: str, body: LegitimacyScreenRevalidation
) -> Response:
    record = await _service(request).revalidate(
        _secret(request),
        csrf=request.headers.get("x-csrf-token"),
        origin=request.headers.get("origin"),
        screen_ref=screen_ref,
        submission=body,
    )
    return Response(content=record.model_dump_json(), status_code=201, media_type="application/json")


@legitimacy_screen_router.get(
    ROUTE_PATH + "/{screen_ref}/{screen_revision}", response_model=LegitimacyScreenRecord, responses=ERRORS
)
async def read_legitimacy_screen(request: Request, screen_ref: str, screen_revision: int) -> Response:
    if request.scope.get("query_string", b""):
        raise LegitimacyScreenBoundaryError("invalid_request")
    record = await _service(request).read(_secret(request), screen_ref, screen_revision)
    return Response(content=record.model_dump_json(), status_code=200, media_type="application/json")
