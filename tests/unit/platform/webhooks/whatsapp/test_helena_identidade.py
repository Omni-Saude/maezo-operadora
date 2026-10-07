"""Identidade do beneficiario na Helena (DL-0077): resolucao fail-closed, cache e entrega ao estado.

Contra fakes do port `SubjectResolutionPort` (nenhuma rede) e o grafo REAL da Helena (inferencia
roteirizada). O que se prova: so' candidato UNICO vira identidade; qualquer falha vira `None` sem
levantar; o hash do telefone, a referencia e o numero nunca vao a log; a identidade chega ao estado
em vocabulario fechado e NAO muda a decisao da Helena.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import structlog

from maezo.agents.helena.graph import (
    IDENTIDADE_CHAVES,
    gate_inbound_state,
    new_helena_state,
    normalizar_identidade,
)
from maezo.agents.lucas.identidade_amh import BaseLegalExecucaoDeContrato, ResolvedorDeSujeitoAmh
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp import helena_identidade as hi
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.subject_resolution import (
    PlanSummary,
    SubjectCandidate,
    SubjectProfile,
    SubjectResolution,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_NUMERO = "5511900000177"  # faixa sintetica de teste
_HASH = "a" * 64
_REF = "subj-ref-0001"


def _perfil(ref: str = _REF, **sobre: Any) -> SubjectProfile:
    valores: dict[str, Any] = {
        "portable_subject_ref": ref,
        "idade_anos": 34,
        "idade_meses": None,
        "plano": PlanSummary(
            ativo=True, vigencia_inicio="2024-01-01", vigencia_fim=None, carencia_vigente=False
        ),
        "titular_ref": "subj-ref-titular",
        "fonte_atualizada_em": "2026-10-01T00:00:00Z",
        "campos_ausentes": frozenset(),
    }
    valores.update(sobre)
    return SubjectProfile(**valores)


def _resolucao(*refs: str) -> SubjectResolution:
    resultado = "nenhum" if not refs else ("unico" if len(refs) == 1 else "multiplos")
    return SubjectResolution(
        resultado=resultado,
        candidatos=tuple(SubjectCandidate(r, "titular", True) for r in refs),
        campos_ausentes=frozenset(),
    )


class _Port:
    def __init__(
        self,
        *,
        resolucao: PortResult[SubjectResolution] | None = None,
        perfil: PortResult[SubjectProfile] | None = None,
        espera_s: float = 0.0,
        levanta: bool = False,
    ) -> None:
        self.resolucao = resolucao if resolucao is not None else PortResult.ok(_resolucao(_REF))
        self.perfil = perfil if perfil is not None else PortResult.ok(_perfil())
        self.espera_s = espera_s
        self.levanta = levanta
        self.chamadas_resolucao = 0
        self.chamadas_perfil = 0
        self.bases_legais: list[str] = []

    async def resolve_by_phone(self, contact_pseudonym: str, **_: Any) -> PortResult[SubjectResolution]:
        self.chamadas_resolucao += 1
        if self.levanta:
            raise RuntimeError("amh fora")
        if self.espera_s:
            await asyncio.sleep(self.espera_s)
        return self.resolucao

    async def get_profile(
        self, portable_subject_ref: str, *, consent_decision_ref: str, **_: Any
    ) -> PortResult[SubjectProfile]:
        self.chamadas_perfil += 1
        self.bases_legais.append(consent_decision_ref)
        return self.perfil


class _Relogio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _identidade(port: _Port, *, hash_fn: Any = None, relogio: Any = None) -> hi.IdentidadeHelena:
    kwargs: dict[str, Any] = {}
    if relogio is not None:
        kwargs["relogio"] = relogio
    return hi.IdentidadeHelena(
        port=port,  # type: ignore[arg-type]
        resolvedor=ResolvedorDeSujeitoAmh(
            port=port,  # type: ignore[arg-type]
            amh_tenant="austa_operadora",
            hash_scheme="amh-phone-lookup-v1",
            purpose_of_use="sharing_amh_internal",
        ),
        consentimento=BaseLegalExecucaoDeContrato(),
        purpose_of_use="sharing_amh_internal",
        hash_telefone=hash_fn if hash_fn is not None else (lambda numero: _HASH),
        **kwargs,
    )


async def _resolver(ident: hi.IdentidadeHelena, conversa: str = "wa:amh:hk1_x") -> dict[str, Any] | None:
    return await ident.resolver(_NUMERO, conversation_id=conversa, pseudo_id="pseudo")


# --- faixa etaria ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anos", "meses", "esperada"),
    [
        (None, 6, "lactente"),
        (1, 23, "lactente"),
        (2, 24, "crianca"),
        (11, None, "crianca"),
        (12, None, "adolescente"),
        (17, None, "adolescente"),
        (18, None, "adulto"),
        (59, None, "adulto"),
        (60, None, "idoso"),
        (None, None, None),
        (-1, None, None),
        (200, None, None),
    ],
)
def test_faixa_etaria_grossa(anos: int | None, meses: int | None, esperada: str | None) -> None:
    assert hi.faixa_etaria(anos, meses) == esperada


def test_identidade_do_perfil_so_leva_o_vocabulario_fechado_e_nunca_a_idade_exata() -> None:
    identidade = hi.identidade_do_perfil(_perfil())
    assert identidade is not None
    assert frozenset(identidade) == IDENTIDADE_CHAVES
    assert identidade["faixa_etaria"] == "adulto"
    assert identidade["plano_ativo"] is True
    assert identidade["carencia_vigente"] is False
    assert 34 not in identidade.values()


@pytest.mark.parametrize(
    "sobre",
    [
        {"portable_subject_ref": "tem espaco e +55 11 9"},
        {"titular_ref": "x" * 300},
        {
            "plano": PlanSummary(
                ativo=True, vigencia_inicio="01/01/2024", vigencia_fim=None, carencia_vigente=None
            )
        },
    ],
)
def test_perfil_fora_do_vocabulario_vira_none(sobre: dict[str, Any]) -> None:
    assert hi.identidade_do_perfil(_perfil(**sobre)) is None


# --- resolucao ------------------------------------------------------------------------------------


async def test_candidato_unico_resolve_identidade_e_usa_a_base_legal_fixa() -> None:
    port = _Port()
    identidade = await _resolver(_identidade(port))
    assert identidade is not None
    assert identidade["portable_subject_ref"] == _REF
    assert identidade["titular_ref"] == "subj-ref-titular"
    assert port.bases_legais == ["base-legal:execucao-de-contrato"]


async def test_telefone_compartilhado_nao_resolve() -> None:
    port = _Port(resolucao=PortResult.ok(_resolucao("a", "b")))
    assert await _resolver(_identidade(port)) is None
    assert port.chamadas_perfil == 0


async def test_nenhum_candidato_nao_resolve() -> None:
    port = _Port(resolucao=PortResult.ok(_resolucao()))
    assert await _resolver(_identidade(port)) is None


async def test_recusa_do_port_na_resolucao_ou_no_perfil_nao_resolve() -> None:
    assert (
        await _resolver(_identidade(_Port(resolucao=PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)))) is None
    )
    assert await _resolver(_identidade(_Port(perfil=PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)))) is None


async def test_perfil_de_outro_sujeito_nao_resolve() -> None:
    port = _Port(perfil=PortResult.ok(_perfil(ref="subj-ref-outro")))
    assert await _resolver(_identidade(port)) is None


async def test_numero_sem_hash_nao_chama_a_amh() -> None:
    port = _Port()
    assert await _resolver(_identidade(port, hash_fn=lambda numero: None)) is None
    assert port.chamadas_resolucao == 0


async def test_excecao_nunca_sobe_e_vira_none() -> None:
    assert await _resolver(_identidade(_Port(levanta=True))) is None

    def _quebra(numero: str) -> str:
        raise RuntimeError("hasher")

    assert await _resolver(_identidade(_Port(), hash_fn=_quebra)) is None


async def test_estouro_de_prazo_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hi, "PRAZO_TOTAL_S", 0.05)
    assert await _resolver(_identidade(_Port(espera_s=1.0))) is None


# --- cache por conversa ---------------------------------------------------------------------------


async def test_cache_positivo_evita_nova_chamada_e_expira() -> None:
    port, relogio = _Port(), _Relogio()
    ident = _identidade(port, relogio=relogio)
    assert await _resolver(ident) is not None
    assert await _resolver(ident) is not None
    assert port.chamadas_resolucao == 1
    assert await _resolver(ident, "wa:amh:hk1_outra") is not None  # outra conversa nao reaproveita
    assert port.chamadas_resolucao == 2
    relogio.t += hi.TTL_POSITIVO_S + 1
    assert await _resolver(ident) is not None
    assert port.chamadas_resolucao == 3


async def test_cache_negativo_e_curto() -> None:
    port, relogio = _Port(resolucao=PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)), _Relogio()
    ident = _identidade(port, relogio=relogio)
    assert await _resolver(ident) is None
    assert await _resolver(ident) is None
    assert port.chamadas_resolucao == 1
    relogio.t += hi.TTL_NEGATIVO_S + 1
    port.resolucao = PortResult.ok(_resolucao(_REF))
    assert await _resolver(ident) is not None


async def test_cache_e_limitado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hi, "MAX_CONVERSAS_EM_CACHE", 2)
    ident = _identidade(_Port())
    for n in range(5):
        await _resolver(ident, f"wa:amh:hk1_{n}")
    assert len(ident._cache) == 2


async def test_log_nunca_carrega_hash_referencia_nem_numero() -> None:
    with structlog.testing.capture_logs() as logs:
        await _resolver(_identidade(_Port()))
        await _resolver(_identidade(_Port(levanta=True)), "wa:amh:hk1_y")
    texto = repr(logs)
    assert logs
    for proibido in (_HASH, _REF, _NUMERO, "subj-ref-titular"):
        assert proibido not in texto


# --- o estado da Helena ---------------------------------------------------------------------------


def _identidade_valida() -> dict[str, Any]:
    identidade = hi.identidade_do_perfil(_perfil())
    assert identidade is not None
    return identidade


def test_new_helena_state_grava_sempre_o_campo_e_recusa_fora_do_vocabulario() -> None:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_x",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "p",
        "message_body": "oi",
    }
    assert new_helena_state(**base)["identidade_beneficiario"] is None
    com = new_helena_state(**base, identidade_beneficiario=_identidade_valida())
    assert com["identidade_beneficiario"] == _identidade_valida()
    for ruim in ({"portable_subject_ref": "x"}, {**_identidade_valida(), "cpf": "123"}, "texto"):
        with pytest.raises(ValueError, match="identidade_beneficiario"):
            new_helena_state(**base, identidade_beneficiario=ruim)  # type: ignore[arg-type]


def test_gate_inbound_state_zera_identidade_plantada() -> None:
    gated = gate_inbound_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_x",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "p",
            "message_body": "oi",
            "identidade_beneficiario": _identidade_valida(),
        }
    )
    assert gated["identidade_beneficiario"] is None


def test_normalizar_identidade_e_estrita() -> None:
    assert normalizar_identidade(_identidade_valida()) == _identidade_valida()
    assert normalizar_identidade({**_identidade_valida(), "faixa_etaria": "bebe"}) is None
    assert normalizar_identidade({**_identidade_valida(), "plano_ativo": "sim"}) is None
    assert normalizar_identidade(None) is None


# --- o despachante --------------------------------------------------------------------------------

_CLASSIFICACAO = '{"intent": "information", "population": "none", "psychosocial_risk": false}'


class _FakeInference:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self._respostas: list[str] = []

    def rearmar(self) -> None:
        self._respostas = [_CLASSIFICACAO, "resposta"]

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self._respostas.pop(0) if self._respostas else ""


class _Cliente:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "wamid.reply.1"}]}


def _despachante(identidade: hi.IdentidadeHelena | None) -> tuple[HelenaDispatcher, _FakeInference]:
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", [{"red_flag": False, "conduta": "CONTINUE"}])
    inferencia = _FakeInference()
    return (
        HelenaDispatcher(
            tenant_id="amh",
            inference=inferencia,  # type: ignore[arg-type]
            dmn=dmn,
            cibseven=FakeCibSevenTransport(),
            whatsapp_client=_Cliente(),  # type: ignore[arg-type]
            pseudonymizer=Pseudonymizer(),
            audit_sink=FakeStartAuditSink(),
            identidade=identidade,
        ),
        inferencia,
    )


async def _turno(d: HelenaDispatcher, inf: _FakeInference, n: int = 1) -> dict[str, Any]:
    inf.rearmar()
    return await d.dispatch(InboundMessage(from_number=_NUMERO, text="oi", message_id=f"wamid.{n}"))


async def test_despachante_entrega_a_identidade_ao_estado_sem_mudar_a_resposta() -> None:
    sem, inf_sem = _despachante(None)
    com, inf_com = _despachante(_identidade(_Port()))
    r_sem, r_com = await _turno(sem, inf_sem), await _turno(com, inf_com)
    assert r_sem["identidade_beneficiario"] is None
    assert r_com["identidade_beneficiario"] == _identidade_valida()
    # contexto, nunca decisao: mesma decisao e MESMOS prompts (a identidade nao entra no prompt)
    for chave in ("next_kind", "intent", "population", "response_kind", "escalation_started"):
        assert r_sem[chave] == r_com[chave]
    assert inf_sem.prompts == inf_com.prompts
    assert _REF not in "".join(inf_com.prompts)


async def test_despachante_com_amh_fora_responde_como_sem_identidade() -> None:
    com, inf = _despachante(_identidade(_Port(levanta=True)))
    r = await _turno(com, inf)
    assert r["identidade_beneficiario"] is None
    assert r["next_kind"] == "inform"
    assert not r.get("error")


async def test_despachante_resolve_uma_vez_por_conversa() -> None:
    port = _Port()
    com, inf = _despachante(_identidade(port))
    for n in range(3):
        await _turno(com, inf, n)
    assert port.chamadas_resolucao == 1
