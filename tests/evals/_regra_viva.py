"""A REGRA de quando um eval vivo (Tier B, `llm_live`) roda — so' biblioteca padrao, fonte unica.

Por que um modulo separado (21/11/2026)
---------------------------------------
`tests/evals/_vivo.py` importa `maezo.runtime.inference` (o provedor real). A regra de "roda / pula" e' usada
tambem por quem precisa responder mesmo com o pacote quebrado — o resumo do noturno
(`scripts/ci/tier_b_resumo.py`) SEMPRE reporta — e por isso fica aqui, sem dependencia: ninguem copia a
regra (uma copia literal divergiria).

A regra
-------
1. **Opt-in explicito de execucao** (`TIER_B_EXECUCAO` em {noturno, manual}): o job noturno e o script manual
   (`scripts/dev/tier_b_manual.py`) a definem. Sem ela, todo caso vivo e' PULADO com motivo estavel, qualquer
   que seja o provedor ou a chave: um `make test`/`pytest` local com `MAEZO_INFERENCE_PROVIDER=bedrock_br` e
   sessao AWS exportados nunca faz chamada real (paga, phi=True) sem pedir.
2. **Credencial pretendida**: chave Anthropic, ou qualquer provedor nomeado que nao seja `anthropic` nem de
   teste. Quem nomeou `bedrock_br` quis rodar: sem credencial utilizavel, o caso falha (LIVE CALL FAILED),
   nunca "NAO RODOU". "NAO RODOU" fica so' para sem chave E sem provedor nomeado.
"""

from __future__ import annotations

import os

#: A MESMA variavel que o runtime usa para escolher o provedor (`InferenceSettings`, MAEZO_INFERENCE_*).
VARIAVEL_PROVEDOR = "MAEZO_INFERENCE_PROVIDER"
#: Sem a variavel: `anthropic` (nao phi_capable => LIVE CALL FAILED visivel com phi=True).
PROVEDOR_PADRAO = "anthropic"
#: Provedores REAIS aceitos num eval vivo; os da zona PHI nao usam chave de API (SigV4/sessao do dev).
PROVEDORES_VIVOS = frozenset({"anthropic", "bedrock", "bedrock_br", "br_resident"})
PROVEDORES_ZONA_PHI = frozenset({"bedrock_br", "br_resident"})
#: Provedores de TESTE do runtime (`_PROVIDER_FACTORIES`): nomeados (ex.: `noop` num `.env` local) seguem
#: pulados; num caso que roda (com chave) sao recusados (`ProvedorNaoVivo`), nunca pontuados.
PROVEDORES_DE_TESTE = frozenset({"noop", "phi_zone_mock"})

#: Opt-in de execucao: so' o job noturno e o script manual definem.
VARIAVEL_EXECUCAO = "TIER_B_EXECUCAO"
EXECUCAO_NOTURNA, EXECUCAO_MANUAL = "noturno", "manual"
EXECUCOES_VIVAS = frozenset({EXECUCAO_NOTURNA, EXECUCAO_MANUAL})

MOTIVO_SEM_OPT_IN = (
    "Tier B so' roda no noturno ou na execucao manual (TIER_B_EXECUCAO=noturno|manual ausente): "
    "caso vivo pulado, nenhuma chamada a provedor"
)
MOTIVO_SEM_CREDENCIAL = (
    "Tier B sem credencial viva (nenhum provedor nomeado em MAEZO_INFERENCE_PROVIDER nem chave): "
    "caso vivo pulado; o Tier B exige provedor na zona PHI — ver a execucao manual em tests/evals/README.md"
)


def execucao_viva() -> str | None:
    """`noturno` ou `manual` quando o opt-in esta' presente; senao `None`."""
    valor = os.environ.get(VARIAVEL_EXECUCAO, "").strip().lower()
    return valor if valor in EXECUCOES_VIVAS else None


def provedor_configurado() -> str:
    return os.environ.get(VARIAVEL_PROVEDOR, "").strip() or PROVEDOR_PADRAO


def credencial_viva_presente() -> bool:
    """Ha' credencial PRETENDIDA para um eval vivo: chave Anthropic, ou qualquer provedor nomeado alem do
    padrao que nao seja de teste (um nome desconhecido tambem roda e falha: `ProvedorNaoVivo`)."""
    chave = os.environ.get("MAEZO_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    return bool(chave) or provedor_configurado() not in PROVEDORES_DE_TESTE | {PROVEDOR_PADRAO}


def motivo_do_pulo() -> str | None:
    """Por que um caso vivo e' pulado agora (`None` = roda). O opt-in vem ANTES da credencial."""
    if execucao_viva() is None:
        return MOTIVO_SEM_OPT_IN
    if not credencial_viva_presente():
        return MOTIVO_SEM_CREDENCIAL
    return None
