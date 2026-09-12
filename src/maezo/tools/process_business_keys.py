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

#: Prefixo contratual da familia AUTH (`AUTH-{tenant_id}-{numero_guia_tiss}`).
AUTH_BUSINESS_KEY_PREFIX: Final[str] = "AUTH-"

#: Prefixo LEGADO que o despachante de intake do portal montava a partir do `guide_identity_ref`
#: opaco (`"AUTHI-" + guide_identity_ref`). Retido aqui — e SOMENTE aqui — como constante de
#: RECUSA: nenhum caminho o monta mais, e `refuse_legacy_auth_intake_business_key` existe para
#: que uma chave legada seja rejeitada por nome em vez de aceita por descuido. Ver
#: `docs/adr/0050-...` para por que a migracao e' uma recusa e nao uma reescrita.
LEGACY_AUTH_INTAKE_BUSINESS_KEY_PREFIX: Final[str] = "AUTHI-"


class BusinessKeyComponentError(ValueError):
    """Um componente da business key esta' ausente, vazio ou contem espaco/controle.

    `ValueError` de proposito: e' a mesma classe que o ladder do harness de worker e o
    construtor de estado do ingresso ja' tratam como ENTRADA ma' (recusa fail-closed sem
    `bpmnError` nao catalogado), entao nenhum chamador precisa aprender um tipo novo para
    falhar direito.
    """


class LegacyAuthIntakeBusinessKeyError(ValueError):
    """Uma business key na forma legada `AUTHI-{guide_identity_ref}` chegou a um caminho vivo.

    Nunca migrada silenciosamente para a forma contratual: `guide_identity_ref` e' uma
    referencia OPACA do portal e `numero_guia_tiss` e' o numero da guia TISS — nao ha' funcao
    total de um para o outro, e inventar uma seria fabricar identidade clinica. A recusa e' a
    unica resposta honesta.
    """


def _component(name: str, value: object) -> str:
    if type(value) is not str:
        raise BusinessKeyComponentError(
            f"business key AUTH: componente {name!r} precisa ser `str`, veio {type(value).__name__}"
        )
    if not value:
        raise BusinessKeyComponentError(
            f"business key AUTH: componente {name!r} vazio — a chave colapsaria para uma chave "
            "compartilhada entre solicitacoes distintas; recusado antes de qualquer efeito"
        )
    if value.strip() != value or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise BusinessKeyComponentError(
            f"business key AUTH: componente {name!r} contem espaco ou caractere de controle — "
            "a chave e' comparada byte a byte contra `ACT_HI_PROCINST.BUSINESS_KEY_` e contra a "
            "reivindicacao duravel de dedup; uma variante com espaco e' um segundo dominio"
        )
    return value


def auth_business_key(*, tenant_id: object, numero_guia_tiss: object) -> str:
    """A business key contratual de `SP-OP-AUTH-001`: `AUTH-{tenant_id}-{numero_guia_tiss}`.

    UMA instancia por guia TISS, identica nos dois canais (agente e portal) — e' isso que faz
    dos dois UM dominio de idempotencia (decisao do dono #16, opcao (a)).

    Recusa componente ausente/vazio/com espaco (`BusinessKeyComponentError`) em vez de produzir
    uma chave colapsada; ver o docstring do modulo para por que a promocao a `EXCLUSIVE` torna
    esse colapso um risco de troca de caso clinico.

    RESIDUAL DECLARADO (nao fechado aqui): a forma `{PREFIXO}-{tenant}-{guia}` e' ambigua se um
    `tenant_id` contiver `-`, porque o separador tambem aparece dentro de `numero_guia_tiss`
    (ha' guias com hifen em uso). Nenhum tenant implantado tem hifen hoje, e ESTREITAR o
    componente aqui reescreveria chaves ja' implantadas — o que o contrato proibe
    explicitamente ("ADR-0038 continua Proposed; nao autoriza renomear chaves implantadas").
    A desambiguacao pertence a ratificacao do ADR-0038, nao a este compositor.
    """
    tenant = _component("tenant_id", tenant_id)
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
