"""Chave de negocio: primitivos FOLHA + compositor UNICO de `SP-OP-CONTAS-001` (R-101 / F-139).

Folha de dominio (como todo `maezo.domain`): so' stdlib, nenhuma dependencia de outro pacote
`maezo`. Mora aqui — e nao em `maezo.tools` — porque `maezo.domain` e' SDK de agente (ADR-0067,
`maezo.contratos.sdk.MODULOS_DO_SDK`): Marina (`agents/marina`) e o worker
`tools/workers/contas.py` chamam o MESMO compositor sem que o agente importe a plataforma.
`maezo.tools.process_business_keys` (compositor AUTH, WP-J1-11) re-exporta daqui o erro tipado e
a validacao de componente, entao AUTH e CONTAS recusam com UM so' tipo.
"""

from __future__ import annotations

from typing import Final

#: Prefixo contratual da familia CONTAS (`docs/processes/contracts/SP-OP-CONTAS-001.md`
#: §"Business key (idempotencia)").
CONTAS_BUSINESS_KEY_PREFIX: Final[str] = "CONTAS-"


class BusinessKeyComponentError(ValueError):
    """Um componente da business key esta' ausente, vazio ou contem espaco/controle.

    `ValueError` de proposito: e' a mesma classe que o ladder do harness de worker e o
    construtor de estado do ingresso ja' tratam como ENTRADA ma' (recusa fail-closed sem
    `bpmnError` nao catalogado), entao nenhum chamador precisa aprender um tipo novo para
    falhar direito.
    """


def componente(name: str, value: object, *, family: str = "AUTH") -> str:
    """Valida UM componente de business key: `str`, nao vazio, sem espaco/controle (ou levanta)."""
    if type(value) is not str:
        raise BusinessKeyComponentError(
            f"business key {family}: componente {name!r} precisa ser `str`, veio {type(value).__name__}"
        )
    if not value:
        raise BusinessKeyComponentError(
            f"business key {family}: componente {name!r} vazio — a chave colapsaria para uma chave "
            "compartilhada entre solicitacoes distintas; recusado antes de qualquer efeito"
        )
    if value.strip() != value or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise BusinessKeyComponentError(
            f"business key {family}: componente {name!r} contem espaco ou caractere de controle — "
            "a chave e' comparada byte a byte contra `ACT_HI_PROCINST.BUSINESS_KEY_` e contra a "
            "reivindicacao duravel de dedup; uma variante com espaco e' um segundo dominio"
        )
    return value


def _tenant_componente(tenant_id: object, *, family: str) -> str:
    tenant = componente("tenant_id", tenant_id, family=family)
    if "-" in tenant:
        raise BusinessKeyComponentError(
            f"business key {family}: componente 'tenant_id' contem '-' — o separador da chave; "
            "a chave deixaria de ser injetiva entre tenants"
        )
    return tenant


def contas_business_key(
    *,
    tenant_id: object,
    numero_lote_tiss: object = "",
    numero_guia_tiss: object = "",
    numero_conta: object = "",
) -> str:
    """A business key contratual de `SP-OP-CONTAS-001` (R-101 / F-139) — o compositor UNICO.

    Contrato `SP-OP-CONTAS-001.md` §"Business key (idempotencia)", e SO ele (nenhum campo
    inventado):

      * `CONTAS-{tenant_id}-{numero_guia_tiss}-{numero_conta}` quando a operadora adjudica por
        guia/conta (os DOIS presentes);
      * senao `CONTAS-{tenant_id}-{numero_lote_tiss}` (o lote e' obrigatorio no contrato).

    POR QUE RECUSA. As f-strings que este compositor substitui (`agents/marina/graph.py`,
    `tools/workers/contas.py`) caiam, com guia SEM conta e sem lote, na forma lote com lote VAZIO:
    `CONTAS-{tenant}-`, uma chave compartilhada por TODOS esses casos do tenant — o
    `find_active_instance` devolvia a instancia de OUTRO caso e o protocolo do demonstrativo
    colidia. So `agents/marina/delegation.py` guardava contra isso, com um predicado paralelo
    que podia derivar. Agora o proprio compositor recusa (`BusinessKeyComponentError`, um
    `ValueError`) toda entrada que nao compoe uma das duas formas contratuais, e os chamadores
    perguntam a ELE se a identidade existe.

    RESIDUO DECLARADO (forma fixada pelo contrato, nao alterada aqui): as duas formas usam `-`
    como separador e `numero_lote_tiss`/`numero_guia_tiss`/`numero_conta` podem conter `-`, entao
    `lote="X-Y"` e `(guia="X", conta="Y")` compoem a mesma string. Fechar isso exige mudar o
    formato contratual — decisao do dono do contrato, nao deste compositor. O `tenant_id` com `-`
    E' recusado (como em `auth_business_key`; nenhum tenant de engine aceita `-`).
    """
    family = "CONTAS"
    tenant = _tenant_componente(tenant_id, family=family)
    if numero_guia_tiss and numero_conta:
        guia = componente("numero_guia_tiss", numero_guia_tiss, family=family)
        conta = componente("numero_conta", numero_conta, family=family)
        return f"{CONTAS_BUSINESS_KEY_PREFIX}{tenant}-{guia}-{conta}"
    lote = componente("numero_lote_tiss", numero_lote_tiss, family=family)
    return f"{CONTAS_BUSINESS_KEY_PREFIX}{tenant}-{lote}"
