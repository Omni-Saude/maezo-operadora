"""Compositores UNICOS de business key por familia de processo (WP-J1-11, decisao do dono #16).

POR QUE ESTE MODULO EXISTE. Ate WP-J1-11 a business key de `SP-OP-AUTH-001` era montada por
f-string em TRES lugares independentes, e um quarto lugar montava uma chave de OUTRA FORMA para
a MESMA guia:

  * `agents/rafael/graph.py::_business_key`            -> `f"AUTH-{tenant}-{guia}"`
  * `runtime/agent_runtime/ingress.py::build_ingress_router` -> `f"AUTH-{tenant}-{guia}"`
  * `gateway/intake/native_composition.py`             -> `"AUTHI-" + guide_identity_ref`
  * `gateway/intake/native_dispatch.py::start_human`   -> a mesma comparacao `"AUTHI-" + ...`

Os dois primeiros concordam com o contrato (`docs/processes/contracts/SP-OP-AUTH-001.md`
"Business key (idempotencia)" e o cabecalho do BPMN,
`spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`); os dois ultimos NAO. O efeito e'
DOIS DOMINIOS DE IDEMPOTENCIA para a MESMA guia TISS: o canal de agente e o canal do portal nao
se enxergam, `find_active_instance` de um nunca encontra a instancia do outro, e
`correlate_message` por business key e' inutilizavel para instancias abertas pelo portal. A
decisao do dono #16 (2026-09-12), opcao (a), fecha isso: UM dominio por guia, na forma
contratual.

Este modulo e' o unico compositor. Ele e' FOLHA de proposito — nao importa nada de `maezo` —
para poder ser importado por `agents/`, `runtime/`, `gateway/` e `tools/` sem criar ciclo, e
espelha o precedente de `tools/workers/base.py::mint_contract_business_key`, que ja' e' o minter
compartilhado da familia `CANCEL-`.

POR QUE ELE RECUSA COMPONENTE VAZIO (e nao apenas concatena). As duas f-strings do canal de
agente usavam `state.get("tenant_id", "")` / `state.get("numero_guia_tiss", "")`: com qualquer um
dos dois ausente a chave COLAPSAVA para `"AUTH--"` — uma unica chave compartilhada por TODAS as
solicitacoes malformadas. Enquanto `SP-OP-AUTH-001` era `NON_STRICT` isso era latente (o pior
caso era `find_active_instance` devolver a instancia malformada anterior). Com a promocao a
`EXCLUSIVE` que este mesmo pacote faz (`tools/mcp_cibseven/transport.py::_START_DEDUP_POLICY`) a
falha ganha dentes: a segunda solicitacao malformada acha a reivindicacao duravel da primeira,
resolve contra a instancia viva dela e devolve O CASO CLINICO DE OUTRA PESSOA como se fosse o
seu. Recusar na montagem e' a unica saida que nao inventa identidade: falha alto, antes de
qualquer efeito, e nunca funde duas guias.
"""

from __future__ import annotations

from typing import Final

# R-101 / F-139: os primitivos FOLHA (o erro tipado e a validacao de componente) e o compositor
# CONTAS vivem em `maezo.domain.chave_de_negocio`, que e' SDK de agente (ADR-0067,
# `maezo.contratos.sdk.MODULOS_DO_SDK`) — Marina compoe a chave CONTAS sem cruzar a fronteira
# agente -> plataforma. Re-exportados aqui: `BusinessKeyComponentError` e' UM so' tipo para AUTH e
# CONTAS, e os chamadores existentes continuam importando deste modulo.
from maezo.domain.chave_de_negocio import (
    CONTAS_BUSINESS_KEY_PREFIX,
    BusinessKeyComponentError,
    contas_business_key,
)
from maezo.domain.chave_de_negocio import componente as _component

__all__ = [
    "AUTH_BUSINESS_KEY_PREFIX",
    "CONTAS_BUSINESS_KEY_PREFIX",
    "LEGACY_AUTH_INTAKE_BUSINESS_KEY_PREFIX",
    "BusinessKeyComponentError",
    "LegacyAuthIntakeBusinessKeyError",
    "auth_business_key",
    "contas_business_key",
    "is_legacy_auth_intake_business_key",
    "refuse_legacy_auth_intake_business_key",
]

#: Prefixo contratual da familia AUTH (`AUTH-{tenant_id}-{numero_guia_tiss}`).
AUTH_BUSINESS_KEY_PREFIX: Final[str] = "AUTH-"

#: Prefixo LEGADO que o despachante de intake do portal montava a partir do `guide_identity_ref`
#: opaco (`"AUTHI-" + guide_identity_ref`). Retido aqui — e SOMENTE aqui — como constante de
#: RECUSA: nenhum caminho o monta mais, e `refuse_legacy_auth_intake_business_key` existe para
#: que uma chave legada seja rejeitada por nome em vez de aceita por descuido. Ver
#: `docs/adr/0050-...` para por que a migracao e' uma recusa e nao uma reescrita.
LEGACY_AUTH_INTAKE_BUSINESS_KEY_PREFIX: Final[str] = "AUTHI-"


class LegacyAuthIntakeBusinessKeyError(ValueError):
    """Uma business key na forma legada `AUTHI-{guide_identity_ref}` chegou a um caminho vivo.

    Nunca migrada silenciosamente para a forma contratual: `guide_identity_ref` e' uma
    referencia OPACA do portal e `numero_guia_tiss` e' o numero da guia TISS — nao ha' funcao
    total de um para o outro, e inventar uma seria fabricar identidade clinica. A recusa e' a
    unica resposta honesta.
    """


def auth_business_key(*, tenant_id: object, numero_guia_tiss: object) -> str:
    """A business key contratual de `SP-OP-AUTH-001`: `AUTH-{tenant_id}-{numero_guia_tiss}`.

    UMA instancia por guia TISS, identica nos dois canais (agente e portal) — e' isso que faz
    dos dois UM dominio de idempotencia (decisao do dono #16, opcao (a)).

    Recusa componente ausente/vazio/com espaco (`BusinessKeyComponentError`) em vez de produzir
    uma chave colapsada; ver o docstring do modulo para por que a promocao a `EXCLUSIVE` torna
    esse colapso um risco de troca de caso clinico.

    C1 (ressalva de seguranca): a forma `{PREFIXO}-{tenant}-{guia}` so' e' injetiva se o
    `tenant_id` NAO contiver `-` (o separador aparece dentro de `numero_guia_tiss`: ha' guias com
    hifen em uso), senao `("a", "b-c")` e `("a-b", "c")` colapsam em `AUTH-a-b-c`. Nenhum tenant
    implantado tem hifen, entao recusar aqui nao renomeia chave implantada: fecha o residual.
    Paridade com `AuthBusinessKeys.java` pelos vetores `auth-business-key-vectors.json`.
    """
    tenant = _component("tenant_id", tenant_id)
    if "-" in tenant:
        raise BusinessKeyComponentError(
            "business key AUTH: componente 'tenant_id' contem '-' — o separador da chave; "
            "`AUTH-{tenant}-{guia}` deixaria de ser injetiva (a guia pode conter '-')"
        )
    guia = _component("numero_guia_tiss", numero_guia_tiss)
    return f"{AUTH_BUSINESS_KEY_PREFIX}{tenant}-{guia}"


def is_legacy_auth_intake_business_key(candidate: object) -> bool:
    """True sse `candidate` esta' na forma legada `AUTHI-{guide_identity_ref}`.

    Note que `AUTH-` NAO e' prefixo de `AUTHI-` nem o contrario: `"AUTHI-x".startswith("AUTH-")`
    e' False (o quarto byte de `AUTHI-` e' `I`, nao `-`), entao as duas familias sao
    distinguiveis por prefixo sem ambiguidade.
    """
    return type(candidate) is str and candidate.startswith(LEGACY_AUTH_INTAKE_BUSINESS_KEY_PREFIX)


def refuse_legacy_auth_intake_business_key(candidate: object) -> None:
    """Levanta `LegacyAuthIntakeBusinessKeyError` se `candidate` for uma chave legada `AUTHI-`.

    Chamado nos pontos vivos que aceitam uma business key de AUTH vinda de fora do compositor.
    Nao converte: ver o docstring da excecao.
    """
    if is_legacy_auth_intake_business_key(candidate):
        raise LegacyAuthIntakeBusinessKeyError(
            "business key AUTH na forma legada `AUTHI-{guide_identity_ref}`: o dominio de "
            "idempotencia unico por guia (decisao do dono #16) admite SOMENTE a forma contratual "
            "`AUTH-{tenant_id}-{numero_guia_tiss}`. A chave legada NAO e' convertida — "
            "`guide_identity_ref` e' opaco e nao determina o numero da guia TISS"
        )
