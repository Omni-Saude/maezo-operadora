"""O SDK do runtime: o que um agente pode importar da plataforma (ADR-0067, saida da fase 2).

Um agente importa o proprio pacote, `maezo.contratos`, o dominio e as portas — e, da plataforma, SO' os
modulos abaixo. Tudo o mais e' interior da plataforma, que muda sem avisar o agente; e' por esta lista que
um agente pode ganhar imagem e ciclo de entrega proprios sem arrastar o Maezo inteiro (fase 4).

A catraca `scripts/ci/check_fronteira_agentes.py` le' esta lista (pelo AST, sem importar nada) e reprova
import de agente fora dela que ainda nao esteja inventariado na baseline. Acrescentar um modulo aqui e'
DECISAO de contrato: o modulo passa a ter de ser estavel para os agentes (revisao dos dois lados).

Cada entrada vale para o modulo e seus submodulos.
"""

from __future__ import annotations

from typing import Final

MODULOS_DO_SDK: Final[frozenset[str]] = frozenset(
    {
        # Contratos, dominio, portas e conhecimento curado: compartilhados por desenho.
        "maezo.contratos",
        "maezo.domain",
        "maezo.ports",
        "maezo.conhecimento",
        # Runtime: inferencia, formatacao de prompt, taxonomia de falhas e telemetria do turno.
        "maezo.runtime.inference",
        "maezo.runtime.prompt_format",
        "maezo.runtime.dependency_failures",
        "maezo.runtime.error_text",
        "maezo.runtime.start_outcome",
        "maezo.runtime.turn_telemetry",
        # O pacote de comportamento do agente, fora da imagem (ADR-0067 fase 3).
        "maezo.runtime.pacote_do_agente",
        "maezo.platform.observability",
        # Transportes: motor (processos e DMN), WhatsApp, redacao de PHI nas variaveis, delegacao A2A.
        "maezo.tools.mcp_cibseven.transport",
        "maezo.tools.workers.dmn_transport",
        "maezo.tools.workers.phi_vars",
        "maezo.tools.mcp_whatsapp.sender",
        "maezo.a2a",
    }
)


def no_sdk(modulo: str) -> bool:
    """`True` quando `modulo` (ou o pacote dele) esta' no SDK."""
    return any(modulo == m or modulo.startswith(m + ".") for m in MODULOS_DO_SDK)
