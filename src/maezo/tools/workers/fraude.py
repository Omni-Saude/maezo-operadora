"""Worker: fraude (SP-OP-FRAUDE-001).

Investigacao de Fraude — Cadeia de Custodia.
MOST COMPLEX: custody sealing (Merkle), Beatriz A2A, L0-hard accusation guard.
Guards: ERR_FRAUD_ACCUSATION_NOT_HUMAN, ERR_CUSTODY_NOT_SEALED, ERR_PHI_IN_CUSTODY.
Inverts the reference detect_fraud v2: score is routing FACT, NEVER verdict.
"""

from __future__ import annotations

from typing import Any

import structlog

from maezo.gateway.custody import CustodyBundle

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_FRAUD_ACCUSATION_NOT_HUMAN = "ERR_FRAUD_ACCUSATION_NOT_HUMAN"
ERR_CUSTODY_NOT_SEALED = "ERR_CUSTODY_NOT_SEALED"
ERR_PHI_IN_CUSTODY = "ERR_PHI_IN_CUSTODY"
ERR_FRAUDE_CASO_INVALIDO = "ERR_FRAUDE_CASO_INVALIDO"

# Decision values
DECISAO_ACUSAR_FRAUDE = "ACUSAR_FRAUDE"
DECISAO_ARQUIVAR = "ARQUIVAR"
DECISAO_MONITORAR = "MONITORAR"

# PHI markers that must NEVER appear in evidence references
_PHI_MARKERS = frozenset({"cpf", "nome", "nome_social", "endereco", "telefone", "email"})


# ---------------------------------------------------------------
# intake — neutral start (register case, no adverse effect)
# ---------------------------------------------------------------


def intake(variables: dict[str, Any]) -> dict[str, Any]:
    """Register the fraud investigation case (NEUTRAL start).

    Records provenance: who referred the case (encaminhado_por_id from Phase 2).
    """
    numero_caso = variables.get("numero_caso", "")
    origem = variables.get("origem_encaminhamento", "")
    encaminhado_por = variables.get("encaminhado_por_id", "")

    logger.info(
        "fraude_intake",
        numero_caso=numero_caso,
        origem=origem,
        encaminhado_por=encaminhado_por,
    )

    return {
        "caso_registrado": True,
        "intake_ts": "now",  # placeholder
    }


# ---------------------------------------------------------------
# gather_evidence — collect evidence (convocates Beatriz, A2A)
# ---------------------------------------------------------------


def gather_evidence(variables: dict[str, Any]) -> dict[str, Any]:
    """Collect/normalize evidence via Beatriz (fraude.investigate).

    Beatriz is human-gated delegate; she instructs, NEVER decides.
    All evidence as pseudonymized pointers — NEVER raw PHI (ADR-0006).
    TASY write DROP (ADR-0013).
    """
    numero_caso = variables.get("numero_caso", "")
    entidade_tipo = variables.get("entidade_tipo", "")

    # Placeholder: real implementation calls Beatriz via A2A
    evidencia_refs = variables.get("evidencia_refs", [])
    if not isinstance(evidencia_refs, list):
        evidencia_refs = []

    logger.info(
        "fraude_gather_evidence",
        numero_caso=numero_caso,
        entidade_tipo=entidade_tipo,
        evidencia_count=len(evidencia_refs),
    )

    return {
        "evidencia_refs": evidencia_refs,
        "evidencia_coletada_em": "now",
    }


# ---------------------------------------------------------------
# score_indicators — INVERTED detect_fraud v2 port
# ---------------------------------------------------------------


def score_indicators(variables: dict[str, Any]) -> dict[str, Any]:
    """Calculate fraud indicator scores — INVERTED from reference.

    The reference detect_fraud v2 summed 7 DMN scores and issued FRAUD_DETECTED.
    Here: score_indicators is a ROUTING FACT (integer), NEVER a verdict.
    No FRAUD_DETECTED error. No auto-accusation.
    """
    evidencia_refs = variables.get("evidencia_refs", [])
    if not isinstance(evidencia_refs, list):
        evidencia_refs = []

    # Placeholder scoring: count of evidence items × factor
    # Real implementation ports the 7 DMNs (upcoding, unbundling, phantom, etc.)
    # but ONLY to produce indicadores_presentes, never a verdict.
    base_score = len(evidencia_refs) * 10

    indicadores_presentes: list[str] = []
    if base_score > 0:
        indicadores_presentes.append("evidencia_presente")
    if base_score > 50:
        indicadores_presentes.append("score_elevado")
    if base_score > 100:
        indicadores_presentes.append("multiplos_indicadores")

    # Determine investigation intensity (NEVER accusation)
    if base_score > 100:
        intensidade = "PRIORITARIA"
    elif base_score > 50:
        intensidade = "APROFUNDADA"
    else:
        intensidade = "LEVE"

    logger.info(
        "fraude_score_indicators",
        score=base_score,
        intensidade=intensidade,
        indicadores_count=len(indicadores_presentes),
    )

    return {
        "score_indicadores": base_score,
        "indicadores_presentes": indicadores_presentes,
        "intensidade_investigacao": intensidade,
    }


# ---------------------------------------------------------------
# assemble_dossier — Beatriz assembles dossier (instructs, never decides)
# ---------------------------------------------------------------


def assemble_dossier(variables: dict[str, Any]) -> dict[str, Any]:
    """Assemble the investigation dossier (Beatriz A2A).

    Beatriz instructs, NEVER decides. Dossier includes: narrative,
    evidence refs, indicators, feature snapshot ref.
    """
    evidencia_refs = variables.get("evidencia_refs", [])
    if not isinstance(evidencia_refs, list):
        evidencia_refs = []
    indicadores = variables.get("indicadores_presentes", [])

    logger.info(
        "fraude_assemble_dossier",
        numero_caso=variables.get("numero_caso"),
        evidencia_count=len(evidencia_refs),
        indicadores_count=len(indicadores),
    )

    return {
        "dossie_montado": True,
        "dossie_items": len(evidencia_refs),
    }


# ---------------------------------------------------------------
# seal_custody_bundle — Merkle seal BEFORE human decision
# ---------------------------------------------------------------


def seal_custody_bundle(variables: dict[str, Any]) -> dict[str, Any]:
    """Seal the evidence bundle with a Merkle root in the audit chain.

    This MUST happen BEFORE UT_DecisaoInvestigador is created.
    The bundle_root is written to the audit chain (ADR-0007 projection).
    Any PHI in evidence is rejected (ERR_PHI_IN_CUSTODY, ADR-0006).

    Sequence: assemble_dossier → seal_custody_bundle → UT_DecisaoInvestigador
    """
    evidencia_refs = variables.get("evidencia_refs", [])
    if not isinstance(evidencia_refs, list):
        evidencia_refs = []

    # Check for PHI in evidence (ADR-0006)
    for ref in evidencia_refs:
        if isinstance(ref, str):
            for marker in _PHI_MARKERS:
                if marker in ref.lower():
                    logger.error(
                        "fraude_phi_in_custody_detected",
                        ref=ref,
                        marker=marker,
                    )
                    raise FraudeError(
                        ERR_PHI_IN_CUSTODY,
                        f"PHI marker '{marker}' detected in evidence reference",
                    )

    # Compute Merkle root over ordered evidence references
    bundle_root = CustodyBundle.seal_bundle([str(r) for r in evidencia_refs])

    logger.info(
        "fraude_custody_sealed",
        numero_caso=variables.get("numero_caso"),
        bundle_root=bundle_root,
        record_count=len(evidencia_refs),
    )

    return {
        "bundle_root": bundle_root,
        "custody_sealed": True,
        "record_count": len(evidencia_refs),
    }


# ---------------------------------------------------------------
# register_fraud_accusation — GATED L0-hard adverse effect
# ---------------------------------------------------------------


def register_fraud_accusation(variables: dict[str, Any]) -> dict[str, Any]:
    """Register a fraud accusation — L0-hard, NEVER automatic.

    GUARDED: ERR_FRAUD_ACCUSATION_NOT_HUMAN (decision) + ERR_CUSTODY_NOT_SEALED (integrity).
    Requires:
      - decisao_fraude == ACUSAR_FRAUDE from human investigator
      - investigator_id + tier present (ADR-0007)
      - fundamentacao, indicadores_fundamentantes, referencia_normativa
      - destino_referral present
      - bundle_root sealed and verifiable
    """
    decisao = variables.get("decisao_fraude", "")
    investigator_id = variables.get("investigator_id", "")
    tier = variables.get("tier", "")
    fundamentacao = variables.get("fundamentacao_investigacao", "")
    indicadores = variables.get("indicadores_fundamentantes", [])
    ref_normativa = variables.get("referencia_normativa", "")
    destino = variables.get("destino_referral", {})
    bundle_root = variables.get("bundle_root", "")
    evidencia_refs = variables.get("evidencia_refs", [])

    if not isinstance(evidencia_refs, list):
        evidencia_refs = []

    errors: list[str] = []

    # Guard 1: human decision
    if decisao != DECISAO_ACUSAR_FRAUDE:
        errors.append(f"decisao_fraude != {DECISAO_ACUSAR_FRAUDE} (got: {decisao!r})")
    if not investigator_id:
        errors.append("investigator_id ausente (ADR-0007)")
    if not tier:
        errors.append("tier ausente")
    if not fundamentacao:
        errors.append("fundamentacao_investigacao ausente")
    if not isinstance(indicadores, list) or len(indicadores) == 0:
        errors.append("indicadores_fundamentantes ausente/vazio")
    if not ref_normativa:
        errors.append("referencia_normativa ausente")
    if not isinstance(destino, dict) or len(destino) == 0:
        errors.append("destino_referral ausente")

    # Guard 2: custody integrity
    if not bundle_root:
        errors.append("bundle_root ausente — custodia nao selada")
    elif not CustodyBundle.verify_bundle(bundle_root, [str(r) for r in evidencia_refs]):
        errors.append("bundle_root nao verifica contra evidencia_refs — custodia violada")

    if errors:
        # Determine which error code to use
        custody_errors = [e for e in errors if "custodia" in e.lower() or "bundle_root" in e.lower()]
        if custody_errors and not any(e for e in errors if e not in custody_errors):
            # Pure custody failure
            logger.error(
                "fraude_custody_not_sealed",
                errors=errors,
                numero_caso=variables.get("numero_caso"),
            )
            raise FraudeError(ERR_CUSTODY_NOT_SEALED, "; ".join(errors))
        else:
            # Human decision failure (or mixed)
            logger.error(
                "fraude_accusation_guard_rejected",
                errors=errors,
                numero_caso=variables.get("numero_caso"),
            )
            raise FraudeError(ERR_FRAUD_ACCUSATION_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "fraude_accusation_registered",
        numero_caso=variables.get("numero_caso"),
        investigator_id=investigator_id,
        tier=tier,
    )

    return {
        "acusacao_registrada": True,
        "bundle_root_verificado": bundle_root,
    }


# ---------------------------------------------------------------
# refer_to_legal — downstream referral (only after accusation)
# ---------------------------------------------------------------


def refer_to_legal(variables: dict[str, Any]) -> dict[str, Any]:
    """Refer case to legal/ANS/civil/criminal (downstream of accusation)."""
    destino = variables.get("destino_referral", {})

    logger.info(
        "fraude_refer_to_legal",
        numero_caso=variables.get("numero_caso"),
        destino=destino,
    )

    return {
        "referral_executado": True,
        "destinos": destino,
    }


# ---------------------------------------------------------------
# start_credenciamento — handoff to CRED-001
# ---------------------------------------------------------------


def start_credenciamento(variables: dict[str, Any]) -> dict[str, Any]:
    """Initiate SP-OP-CRED-001 for provider de-credentialing.

    CRED-001 has its OWN human-gated adverse task.
    """
    logger.info(
        "fraude_start_credenciamento",
        prestador_id=variables.get("prestador_id"),
    )

    return {
        "handoff_credenciamento": True,
        "processo_destino": "SP-OP-CRED-001",
    }


# ---------------------------------------------------------------
# start_contratual — handoff to CANCEL/INADIMPLENCIA
# ---------------------------------------------------------------


def start_contratual(variables: dict[str, Any]) -> dict[str, Any]:
    """Initiate contract termination for beneficiary/contract fraud.

    CANCEL-001/INADIMPLENCIA-001 have their OWN human-gated tasks.
    """
    logger.info(
        "fraude_start_contratual",
        numero_contrato=variables.get("numero_contrato"),
    )

    return {
        "handoff_contratual": True,
        "processo_destino": "SP-OP-CANCEL-001 / SP-OP-INADIMPLENCIA-001",
    }


# ---------------------------------------------------------------
# publish_completed — publishing completion event
# ---------------------------------------------------------------


def publish_completed(variables: dict[str, Any]) -> dict[str, Any]:
    """Publish domain event for case completion."""
    desfecho = "arquivado_sem_indicio"
    if variables.get("decisao_fraude") == DECISAO_ACUSAR_FRAUDE:
        desfecho = "fraude_confirmada_humano"

    logger.info(
        "fraude_publish_completed",
        numero_caso=variables.get("numero_caso"),
        desfecho=desfecho,
    )

    return {
        "evento_publicado": True,
        "desfecho": desfecho,
    }


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class FraudeError(Exception):
    """Worker guard error for fraude adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
