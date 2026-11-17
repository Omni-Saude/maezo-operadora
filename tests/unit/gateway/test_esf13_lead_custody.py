"""E-SF-13 / GTM-E-43 — custodia cifrada do telefone de lead, com finalidade (gemeo unit, sem banco).

Guardas: `test_lead_custody_refuses_cross_prospect_decrypt`, `test_lead_custody_refuses_after_expiry`,
"sem admissao A6, boot recusa". O gemeo com Postgres real (marcado `integration`) depende da
migracao 0036, que nao entrou neste lote (ver docstring de `lead_custody.py`).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from maezo.gateway.commercial_suppression_admission import SuppressionAdmission
from maezo.gateway.communications.lead_custody import (
    LeadCustodyError,
    PostgresLeadContactVault,
    open_lead_phone,
    seal_lead_phone,
)
from maezo.gateway.communications.outbound_gate import Motivo, PedidoDeEnvio
from maezo.gateway.recipient_custody import open_phone, seal_phone
from tests.support.recipient_custody_fakes import FakeKms
from tests.unit.esf13_apoio import (
    FINALIDADE_LEAD,
    TEMPLATE_LEAD,
    ReservaEmMemoria,
    TransporteGravador,
    aceite_os05_sintetico,
    gate_ratificado,
)

AGORA = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
TELEFONE = "5511977775555"
P1 = "prospect-ref-aaaa0001"
P2 = "prospect-ref-bbbb0002"


async def _selar(
    kms: FakeKms, *, prospect: str = P1, finalidade: str = FINALIDADE_LEAD, ttl_h: int = 24
) -> object:
    return await seal_lead_phone(
        TELEFONE,
        kms,
        tenant="amh",
        prospect_ref=prospect,
        finalidade=finalidade,
        agora=AGORA,
        ttl=timedelta(hours=ttl_h),
    )


async def test_abre_para_o_mesmo_prospect_e_finalidade() -> None:
    kms = FakeKms()
    row = await _selar(kms)
    aberto = await open_lead_phone(
        row,  # type: ignore[arg-type]
        kms,
        tenant="amh",
        prospect_ref=P1,
        finalidade=FINALIDADE_LEAD,
        agora=AGORA,
    )
    assert aberto == TELEFONE


async def test_lead_custody_refuses_cross_prospect_decrypt() -> None:
    kms = FakeKms()
    row = await _selar(kms)
    with pytest.raises(LeadCustodyError) as exc:
        await open_lead_phone(
            row,  # type: ignore[arg-type]
            kms,
            tenant="amh",
            prospect_ref=P2,
            finalidade=FINALIDADE_LEAD,
            agora=AGORA,
        )
    assert TELEFONE not in str(exc.value) and P1 not in str(exc.value)


@pytest.mark.parametrize(("tenant", "finalidade"), [("outro", FINALIDADE_LEAD), ("amh", "outra_finalidade")])
async def test_outra_finalidade_ou_tenant_nao_abre(tenant: str, finalidade: str) -> None:
    kms = FakeKms()
    row = await _selar(kms)
    with pytest.raises(LeadCustodyError):
        await open_lead_phone(
            row,  # type: ignore[arg-type]
            kms,
            tenant=tenant,
            prospect_ref=P1,
            finalidade=finalidade,
            agora=AGORA,
        )


async def test_mesmo_ciphertext_com_wrapped_key_trocado_nao_abre() -> None:
    """Copiar so' o blob de um prospect para a linha de outro (mesma chave KMS) nao decifra."""
    kms = FakeKms()
    r1 = await _selar(kms, prospect=P1)
    r2 = await _selar(kms, prospect=P2)
    misturada = replace(r2, sealed=r1.sealed)  # type: ignore[type-var]
    with pytest.raises(LeadCustodyError):
        await open_lead_phone(
            misturada,  # type: ignore[arg-type]
            kms,
            tenant="amh",
            prospect_ref=P2,
            finalidade=FINALIDADE_LEAD,
            agora=AGORA,
        )


async def test_lead_custody_refuses_after_expiry() -> None:
    """Vencida: `None` SEM ir ao KMS; no gate, isso e' RECIPIENT_UNRESOLVED com zero reserva e zero POST."""
    kms = FakeKms()
    row = await _selar(kms, ttl_h=1)
    kms.calls.clear()
    depois = AGORA + timedelta(hours=1)
    aberto = await open_lead_phone(
        row,  # type: ignore[arg-type]
        kms,
        tenant="amh",
        prospect_ref=P1,
        finalidade=FINALIDADE_LEAD,
        agora=depois,
    )
    assert aberto is None and kms.calls == []

    async def destinatario() -> str | None:
        return await open_lead_phone(
            row,  # type: ignore[arg-type]
            kms,
            tenant="amh",
            prospect_ref=P1,
            finalidade=FINALIDADE_LEAD,
            agora=depois,
        )

    reserva, transporte = ReservaEmMemoria(), TransporteGravador()
    r = await gate_ratificado().enviar(
        PedidoDeEnvio(
            template_id=TEMPLATE_LEAD,
            finalidade=FINALIDADE_LEAD,
            tipo_titular="lead",
            titular_ref=P1,
            canal_ref="contact-ref-1",
            variaveis=("1",),
            permissao=aceite_os05_sintetico(),
            chave_idempotencia="k1",
        ),
        destinatario=destinatario,
        reserva=reserva,
        transporte=transporte,
    )
    assert r.motivo is Motivo.RECIPIENT_UNRESOLVED and reserva.reservas == 0 and transporte.posts == []


def _admissao() -> SuppressionAdmission:
    return SuppressionAdmission(
        admission_id="A6",
        contract_sha256="0" * 64,
        owners=("owner (origem)", "DPO (origem)"),
        decided_at=AGORA,
        valid_until=AGORA + timedelta(days=1),
        object_url="https://exemplo.invalid/objeto",
        object_sha256="0" * 64,
        origin_git_sha="0" * 40,
    )


@pytest.mark.parametrize("admissao", [None, "A6", {"admission_id": "A6"}])
def test_sem_admissao_a6_o_boot_recusa(admissao: object) -> None:
    with pytest.raises(LeadCustodyError, match="lead_custody_admission_missing"):
        PostgresLeadContactVault(
            dsn="postgresql://u:p@localhost/db",
            tenant="amh",
            wrapper=FakeKms(),
            admissao=admissao,  # type: ignore[arg-type]
        )


def test_com_admissao_a6_validada_o_cofre_constroi_sem_tocar_o_banco() -> None:
    cofre = PostgresLeadContactVault(
        dsn="postgresql://u:p@localhost/db", tenant="amh", wrapper=FakeKms(), admissao=_admissao()
    )
    assert "localhost" not in repr(cofre)


@pytest.mark.parametrize("ref", ["5511977775555", "+5511977775555", "curto", "com espaco aqui"])
async def test_prospect_ref_tem_de_ser_opaco(ref: str) -> None:
    with pytest.raises(LeadCustodyError, match="prospect_ref_invalid"):
        await _selar(FakeKms(), prospect=ref)


async def test_custodia_do_beneficiario_inalterada_pela_extracao() -> None:
    """A extracao (`seal_with_context`/`open_with_context`) nao mudou a custodia irma."""
    kms = FakeKms()
    sealed = await seal_phone(TELEFONE, kms, tenant="amh", conversation_id="wa:amh:hk1_aaaa0001")
    assert await open_phone(sealed, kms, tenant="amh", conversation_id="wa:amh:hk1_aaaa0001") == TELEFONE
    assert kms.calls[0][1] == {
        "tenant": "amh",
        "conversation_id": "wa:amh:hk1_aaaa0001",
        "finalidade": "maezo-retomada-pos-humano",
    }


def test_ninguem_constroi_suppression_admission_a_mao() -> None:
    """F6: em `src/`, `SuppressionAdmission(...)` so' e' construido pelo leitor validado
    (`commercial_suppression_admission.py`); a raiz obtem o valor por `read_suppression_admission`."""
    import ast
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[3] / "src" / "maezo"
    construtores: set[str] = set()
    for arquivo in raiz.rglob("*.py"):
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            if isinstance(no, ast.Call):
                nome = no.func.id if isinstance(no.func, ast.Name) else getattr(no.func, "attr", None)
                if nome == "SuppressionAdmission":
                    construtores.add(arquivo.relative_to(raiz).as_posix())
    assert construtores == {"gateway/commercial_suppression_admission.py"}


async def test_repr_nunca_mostra_telefone_nem_blob() -> None:
    row = await _selar(FakeKms())
    assert TELEFONE not in repr(row) and "cifrado" in repr(row)
