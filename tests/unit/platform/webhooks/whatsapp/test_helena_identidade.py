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
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from maezo.agents.helena.graph import (
    IDENTIDADE_CHAVES,
    IDENTIDADE_INDETERMINADA,
    IDENTIDADE_NAO_ENCONTRADA,
    IDENTIDADE_RECONHECIDA,
    NOME_OPERADORA_PADRAO,
    gate_inbound_state,
    new_helena_state,
    normalizar_identidade,
)
from maezo.agents.lucas.identidade_amh import BaseLegalExecucaoDeContrato, ResolvedorDeSujeitoAmh
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp import helena_identidade as hi
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.subject_resolution import (
    PlanSummary,
    SubjectCandidate,
    SubjectProfile,
    SubjectResolution,
)
from maezo.runtime.checkpoint import Checkpointer
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
        self.prazos: list[tuple[str, Any]] = []

    async def resolve_by_phone(self, contact_pseudonym: str, **kw: Any) -> PortResult[SubjectResolution]:
        self.chamadas_resolucao += 1
        self.prazos.append(("resolucao", kw.get("timeout_seconds")))
        if self.levanta:
            raise RuntimeError("amh fora")
        if self.espera_s:
            await asyncio.sleep(self.espera_s)
        return self.resolucao

    async def get_profile(
        self, portable_subject_ref: str, *, consent_decision_ref: str, **kw: Any
    ) -> PortResult[SubjectProfile]:
        self.chamadas_perfil += 1
        self.prazos.append(("perfil", kw.get("timeout_seconds")))
        self.bases_legais.append(consent_decision_ref)
        return self.perfil


class _Relogio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _identidade(
    port: _Port, *, hash_fn: Any = None, relogio: Any = None, prazo: float | None = None
) -> hi.IdentidadeHelena:
    kwargs: dict[str, Any] = {}
    if relogio is not None:
        kwargs["relogio"] = relogio
    if prazo is not None:
        kwargs["prazo_total_s"] = prazo
    return hi.IdentidadeHelena(
        port=port,  # type: ignore[arg-type]
        resolvedor=ResolvedorDeSujeitoAmh(
            port=port,  # type: ignore[arg-type]
            amh_tenant="austa_operadora",
            hash_scheme="amh-phone-lookup-v1",
            purpose_of_use="sharing_amh_internal",
            timeout_seconds=prazo if prazo is not None else hi.PRAZO_TOTAL_S,
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


async def test_estouro_de_prazo_vira_none() -> None:
    assert await _resolver(_identidade(_Port(espera_s=1.0), prazo=0.05)) is None


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


async def test_falha_transitoria_so_segura_o_recuo_curto() -> None:
    """DL-0079: indisponibilidade da AMH nao vira resposta da conversa; so' segura uma rajada."""
    port, relogio = _Port(resolucao=PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)), _Relogio()
    ident = _identidade(port, relogio=relogio)
    assert await _resolver(ident) is None
    assert await _resolver(ident) is None  # dentro do recuo: nao martela a AMH
    assert port.chamadas_resolucao == 1
    relogio.t += hi.TTL_TRANSITORIO_S + 0.1  # muito antes do TTL negativo
    port.resolucao = PortResult.ok(_resolucao(_REF))
    assert await _resolver(ident) is not None
    assert port.chamadas_resolucao == 2


def test_politica_de_ttl() -> None:
    assert hi.TTL_TRANSITORIO_S < hi.TTL_NEGATIVO_S < hi.TTL_POSITIVO_S
    assert hi.PRAZO_TOTAL_S == 15.0
    assert {
        "sujeito_nao_encontrado",
        "sujeito_ambiguo",
        "perfil_fora_do_vocabulario",
    } == hi.MOTIVOS_DEFINITIVOS


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


# --- DL-0078: o desfecho fechado e o aviso de identidade ------------------------------------------

_AVISO_RECONHECIDO = (
    "Reconheci este número no cadastro de beneficiários da Austa Clínicas. "
    "Por segurança, não mostro dados pessoais por aqui."
)
_NAO_ENCONTRADO = "Não encontrei este número no cadastro de beneficiários."


async def _desfecho(ident: hi.IdentidadeHelena, conversa: str = "wa:amh:hk1_x") -> hi.ResultadoIdentidade:
    return await ident.resolver_com_desfecho(_NUMERO, conversation_id=conversa, pseudo_id="pseudo")


class _SemBaseLegal:
    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        return None


class _ResolvedorSemDesfecho:
    """Um `ResolvedorDeSujeito` que so' tem `portable_ref` (nao distingue nenhum de varios)."""

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        return None


async def test_desfecho_reconhecido_so_com_candidato_unico_e_perfil_valido() -> None:
    r = await _desfecho(_identidade(_Port()))
    assert r.desfecho == IDENTIDADE_RECONHECIDA
    assert r.identidade is not None and r.identidade["portable_subject_ref"] == _REF


async def test_desfecho_nao_encontrado_so_com_o_nenhum_definitivo_da_amh() -> None:
    port = _Port(resolucao=PortResult.ok(_resolucao()))
    r = await _desfecho(_identidade(port))
    assert (r.identidade, r.desfecho) == (None, IDENTIDADE_NAO_ENCONTRADA)
    assert port.chamadas_perfil == 0


@pytest.mark.parametrize(
    "port",
    [
        _Port(resolucao=PortResult.ok(_resolucao("a", "b"))),  # telefone compartilhado
        _Port(resolucao=PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)),
        _Port(perfil=PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)),
        _Port(perfil=PortResult.ok(_perfil(ref="subj-ref-outro"))),
        _Port(levanta=True),
        # incoerentes: `nenhum` com candidato, `unico` com dois
        _Port(
            resolucao=PortResult.ok(
                SubjectResolution("nenhum", (SubjectCandidate("x", "titular", True),), frozenset())
            )
        ),
        _Port(
            resolucao=PortResult.ok(
                SubjectResolution(
                    "unico",
                    (SubjectCandidate("x", "titular", True), SubjectCandidate("y", "titular", True)),
                    frozenset(),
                )
            )
        ),
    ],
)
async def test_tudo_que_nao_e_unico_nem_nenhum_e_indeterminado(port: _Port) -> None:
    r = await _desfecho(_identidade(port))
    assert (r.identidade, r.desfecho) == (None, IDENTIDADE_INDETERMINADA)


async def test_sem_base_legal_e_indeterminado() -> None:
    ident = _identidade(_Port())
    ident.consentimento = _SemBaseLegal()
    r = await _desfecho(ident)
    assert (r.identidade, r.desfecho) == (None, IDENTIDADE_INDETERMINADA)


async def test_hash_indisponivel_e_prazo_estourado_sao_indeterminados() -> None:
    assert (await _desfecho(_identidade(_Port(), hash_fn=lambda numero: None))).desfecho == (
        IDENTIDADE_INDETERMINADA
    )
    port = _Port(resolucao=PortResult.ok(_resolucao()), espera_s=1.0)
    assert (await _desfecho(_identidade(port, prazo=0.05))).desfecho == IDENTIDADE_INDETERMINADA


# --- prazo configuravel e cache so' do definitivo (DL-0079) ----------------------------------------


async def test_prazo_estourado_nao_fica_em_cache_e_a_proxima_mensagem_tenta_de_novo() -> None:
    """O caso medido em dev: a AMH achou a pessoa, o prazo estourou, e a mensagem seguinte herdava o
    `indeterminado` por 60 s. Agora a seguinte (depois do recuo curto) resolve."""
    port, relogio = _Port(espera_s=1.0), _Relogio()
    ident = _identidade(port, relogio=relogio, prazo=0.05)
    with structlog.testing.capture_logs() as logs:
        assert (await _desfecho(ident)).desfecho == IDENTIDADE_INDETERMINADA
    evento = next(e for e in logs if e["event"] == "helena_identidade_resolvida")
    assert (evento["motivo"], evento["definitivo"]) == ("prazo_estourado", False)
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_INDETERMINADA  # recuo: sem nova chamada
    assert port.chamadas_resolucao == 1
    relogio.t += hi.TTL_TRANSITORIO_S + 0.1
    port.espera_s = 0.0
    r = await _desfecho(ident)
    assert r.desfecho == IDENTIDADE_RECONHECIDA and r.identidade is not None
    assert port.chamadas_resolucao == 2


@pytest.mark.parametrize(
    "port",
    [
        _Port(perfil=PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)),  # perfil_indisponivel
        _Port(perfil=PortResult.refused(Reason.TIMEOUT)),
        _Port(perfil=PortResult.ok(_perfil(ref="subj-ref-outro"))),  # perfil_de_outro_sujeito
        _Port(resolucao=PortResult.refused(Reason.NOT_AUTHENTICATED)),  # recusa do port
        _Port(levanta=True),  # excecao: nem o recuo
    ],
)
async def test_transitorios_nao_viram_resposta_da_conversa(port: _Port) -> None:
    relogio = _Relogio()
    ident = _identidade(port, relogio=relogio)
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_INDETERMINADA
    antes = port.chamadas_resolucao
    relogio.t += hi.TTL_TRANSITORIO_S + 0.1
    await _desfecho(ident)
    assert port.chamadas_resolucao == antes + 1


async def test_sem_base_legal_e_transitorio() -> None:
    port, relogio = _Port(), _Relogio()
    ident = _identidade(port, relogio=relogio)
    ident.consentimento = _SemBaseLegal()
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_INDETERMINADA
    relogio.t += hi.TTL_TRANSITORIO_S + 0.1
    ident.consentimento = BaseLegalExecucaoDeContrato()
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_RECONHECIDA


@pytest.mark.parametrize(
    ("port", "desfecho"),
    [
        (_Port(resolucao=PortResult.ok(_resolucao())), IDENTIDADE_NAO_ENCONTRADA),
        (_Port(resolucao=PortResult.ok(_resolucao("a", "b"))), IDENTIDADE_INDETERMINADA),  # compartilhado
        (
            _Port(perfil=PortResult.ok(_perfil(titular_ref="x" * 300))),
            IDENTIDADE_INDETERMINADA,
        ),  # fora do vocab.
    ],
)
async def test_definitivos_da_amh_ficam_em_cache_pelo_ttl_negativo(port: _Port, desfecho: str) -> None:
    relogio = _Relogio()
    ident = _identidade(port, relogio=relogio)
    assert (await _desfecho(ident)).desfecho == desfecho
    relogio.t += hi.TTL_TRANSITORIO_S + 1  # passou o recuo, nao o TTL negativo
    assert (await _desfecho(ident)).desfecho == desfecho
    assert port.chamadas_resolucao == 1
    relogio.t += hi.TTL_NEGATIVO_S
    await _desfecho(ident)
    assert port.chamadas_resolucao == 2


async def test_reconhecido_fica_em_cache_pelo_ttl_positivo() -> None:
    port, relogio = _Port(), _Relogio()
    ident = _identidade(port, relogio=relogio)
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_RECONHECIDA
    relogio.t += hi.TTL_NEGATIVO_S + 1
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_RECONHECIDA
    assert port.chamadas_resolucao == 1


async def test_prazo_configurado_e_o_teto_de_cada_chamada_e_da_soma() -> None:
    port = _Port(espera_s=0.05)
    ident = _identidade(port, prazo=2.5)
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_RECONHECIDA
    assert port.prazos == [("resolucao", 2.5), ("perfil", 2.5)]


async def test_prazo_configurado_maior_deixa_passar_o_que_o_menor_derrubaria() -> None:
    # Mesma AMH lenta (0.2 s), dois prazos: o curto estoura, o longo reconhece.
    assert (await _desfecho(_identidade(_Port(espera_s=0.2), prazo=0.1))).desfecho == (
        IDENTIDADE_INDETERMINADA
    )
    assert (await _desfecho(_identidade(_Port(espera_s=0.2), prazo=1.0))).desfecho == (IDENTIDADE_RECONHECIDA)


def test_prazo_padrao_e_15s() -> None:
    assert _identidade(_Port()).prazo_total_s == 15.0
    ident = hi.IdentidadeHelena(
        port=_Port(),  # type: ignore[arg-type]
        resolvedor=_ResolvedorSemDesfecho(),
        consentimento=BaseLegalExecucaoDeContrato(),
        purpose_of_use="sharing_amh_internal",
        hash_telefone=lambda numero: _HASH,
    )
    assert ident.prazo_total_s == hi.PRAZO_TOTAL_S == 15.0


@pytest.mark.parametrize("prazo", [0, -1.0, 30.5, float("nan"), float("inf"), True, "15"])
def test_prazo_invalido_recusa_na_construcao(prazo: Any) -> None:
    with pytest.raises(ValueError):
        hi.IdentidadeHelena(
            port=_Port(),  # type: ignore[arg-type]
            resolvedor=_ResolvedorSemDesfecho(),
            consentimento=BaseLegalExecucaoDeContrato(),
            purpose_of_use="sharing_amh_internal",
            hash_telefone=lambda numero: _HASH,
            prazo_total_s=prazo,
        )


async def test_resolvedor_sem_desfecho_nunca_diz_nao_encontrado() -> None:
    ident = _identidade(_Port(resolucao=PortResult.ok(_resolucao())))
    ident.resolvedor = _ResolvedorSemDesfecho()
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_INDETERMINADA


async def test_o_cache_guarda_o_desfecho_e_o_negativo_expira() -> None:
    port, relogio = _Port(resolucao=PortResult.ok(_resolucao())), _Relogio()
    ident = _identidade(port, relogio=relogio)
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_NAO_ENCONTRADA
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_NAO_ENCONTRADA
    assert port.chamadas_resolucao == 1
    relogio.t += hi.TTL_NEGATIVO_S + 1
    port.resolucao = PortResult.ok(_resolucao(_REF))
    assert (await _desfecho(ident)).desfecho == IDENTIDADE_RECONHECIDA


async def test_quem_recebe_do_cache_nao_altera_o_cache() -> None:
    ident = _identidade(_Port())
    r = await _desfecho(ident)
    assert r.identidade is not None
    r.identidade["plano_ativo"] = "adulterado"
    de_novo = await _desfecho(ident)
    assert de_novo.identidade is not None and de_novo.identidade["plano_ativo"] is True


@pytest.mark.parametrize(
    ("refs", "esperado"),
    [((), (None, "nenhum")), ((_REF,), (_REF, "unico")), (("a", "b"), (None, "multiplos"))],
)
async def test_resolvedor_amh_expoe_o_desfecho(
    refs: tuple[str, ...], esperado: tuple[str | None, str]
) -> None:
    resolvedor = ResolvedorDeSujeitoAmh(
        port=_Port(resolucao=PortResult.ok(_resolucao(*refs))),  # type: ignore[arg-type]
        amh_tenant="austa_operadora",
        hash_scheme="amh-phone-lookup-v1",
        purpose_of_use="sharing_amh_internal",
    )
    assert await resolvedor.portable_ref_com_desfecho("p", phone_hash=_HASH) == esperado
    # `portable_ref` continua o de sempre: so' o candidato unico
    assert await resolvedor.portable_ref("p", phone_hash=_HASH) == esperado[0]
    assert await resolvedor.portable_ref_com_desfecho("p", phone_hash=None) == (None, "indisponivel")


async def test_log_do_desfecho_so_tem_tokens_fechados() -> None:
    with structlog.testing.capture_logs() as logs:
        await _desfecho(_identidade(_Port(resolucao=PortResult.ok(_resolucao()))))
    evento = next(e for e in logs if e["event"] == "helena_identidade_resolvida")
    assert evento["desfecho"] == IDENTIDADE_NAO_ENCONTRADA
    assert evento["motivo"] == "sujeito_nao_encontrado"
    for proibido in (_HASH, _REF, _NUMERO):
        assert proibido not in repr(logs)


# --- o despachante entrega o aviso pelo caminho governado -----------------------------------------

_SAUDACAO = '{"intent": "greeting", "population": "none", "psychosocial_risk": false}'


def _despachante_com_checkpoint(
    identidade: hi.IdentidadeHelena | None,
) -> tuple[HelenaDispatcher, _FakeInference, _Cliente]:
    dispatcher, inferencia = _despachante(identidade)
    dispatcher.checkpointer = Checkpointer(saver=InMemorySaver())
    cliente = dispatcher.whatsapp_client
    assert isinstance(cliente, _Cliente)
    return dispatcher, inferencia, cliente


async def _turno_saudacao(d: HelenaDispatcher, inf: _FakeInference, n: int) -> dict[str, Any]:
    inf._respostas = [_SAUDACAO]
    return await d.dispatch(InboundMessage(from_number=_NUMERO, text="oi", message_id=f"wamid.s{n}"))


async def test_despachante_avisa_reconhecido_uma_vez_por_conversa() -> None:
    d, inf, cliente = _despachante_com_checkpoint(_identidade(_Port()))
    r1 = await _turno_saudacao(d, inf, 1)
    r2 = await _turno_saudacao(d, inf, 2)

    textos = [texto for _, texto in cliente.sent]
    assert textos[0] == _AVISO_RECONHECIDO
    assert textos.count(_AVISO_RECONHECIDO) == 1
    assert len(textos) == 3  # aviso + resposta, depois so' a resposta
    assert r1["identidade_desfecho"] == IDENTIDADE_RECONHECIDA
    assert r1["aviso_identidade_dado"] is True and r2["aviso_identidade_dado"] is True
    # o envio vai ao numero cru deste turno, pelo mesmo remetente da resposta
    assert {para for para, _ in cliente.sent} == {_NUMERO}


async def test_despachante_avisa_nao_encontrado() -> None:
    d, inf, cliente = _despachante_com_checkpoint(_identidade(_Port(resolucao=PortResult.ok(_resolucao()))))
    r = await _turno_saudacao(d, inf, 1)
    assert [texto for _, texto in cliente.sent][0] == _NAO_ENCONTRADO
    assert r["identidade_desfecho"] == IDENTIDADE_NAO_ENCONTRADA
    assert r["identidade_beneficiario"] is None


@pytest.mark.parametrize(
    "port",
    [_Port(resolucao=PortResult.ok(_resolucao("a", "b"))), _Port(levanta=True)],
)
async def test_despachante_indeterminado_nao_fala_de_identidade(port: _Port) -> None:
    d, inf, cliente = _despachante_com_checkpoint(_identidade(port))
    r = await _turno_saudacao(d, inf, 1)
    assert len(cliente.sent) == 1
    assert cliente.sent[0][1] not in (_AVISO_RECONHECIDO, _NAO_ENCONTRADO)
    assert r["identidade_desfecho"] == IDENTIDADE_INDETERMINADA


async def test_despachante_desligado_e_o_de_sempre() -> None:
    d, inf, cliente = _despachante_com_checkpoint(None)
    r = await _turno_saudacao(d, inf, 1)
    assert len(cliente.sent) == 1
    assert r["identidade_desfecho"] is None
    assert r["aviso_identidade_dado"] is False


async def test_despachante_nome_da_operadora_configuravel() -> None:
    d, inf, cliente = _despachante_com_checkpoint(_identidade(_Port()))
    d.identidade_nome_operadora = "Operadora Exemplo"
    await _turno_saudacao(d, inf, 1)
    assert cliente.sent[0][1] == _AVISO_RECONHECIDO.replace("Austa Clínicas", "Operadora Exemplo")


async def test_log_do_aviso_nao_carrega_referencia_hash_nem_numero() -> None:
    d, inf, _ = _despachante_com_checkpoint(_identidade(_Port()))
    with structlog.testing.capture_logs() as logs:
        await _turno_saudacao(d, inf, 1)
    avisos = [e for e in logs if e["event"] == "helena_aviso_identidade"]
    assert [e["motivo"] for e in avisos] == ["enviado"]
    assert avisos[0]["aviso_identidade"] == IDENTIDADE_RECONHECIDA
    inicio = next(e for e in logs if e["event"] == "helena_dispatch_turn_started")
    assert inicio["identidade_desfecho"] == IDENTIDADE_RECONHECIDA
    for proibido in (_HASH, _REF, _NUMERO, "subj-ref-titular"):
        assert proibido not in repr(logs)


# --- a settings do nome --------------------------------------------------------------------------


def _settings(**extra: object) -> WhatsAppWebhookSettings:
    return WhatsAppWebhookSettings(app_secret="s", verify_token="v", **extra)  # type: ignore[arg-type]


def test_nome_da_operadora_padrao_bate_com_o_do_grafo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_HELENA_IDENTIDADE_NOME_OPERADORA", raising=False)
    assert _settings().helena_identidade_nome_operadora == NOME_OPERADORA_PADRAO


def test_nome_da_operadora_le_o_nome_canonico_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_HELENA_IDENTIDADE_NOME_OPERADORA", "  Operadora   Exemplo ")
    assert _settings().helena_identidade_nome_operadora == "Operadora Exemplo"


@pytest.mark.parametrize("nome", ["", "   ", "{operadora}", "https://x.y", "a" * 61, "Nome\x07sino", "123"])
def test_nome_da_operadora_invalido_recusa_no_boot(nome: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_HELENA_IDENTIDADE_NOME_OPERADORA", raising=False)
    with pytest.raises(ValidationError):
        _settings(helena_identidade_nome_operadora=nome)
