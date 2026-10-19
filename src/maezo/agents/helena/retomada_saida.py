"""A mensagem da retomada e a cerca que a julga — PURAS, sem `maezo.runtime` e sem modelo.

Movido de `graph.py` SEM MUDAR O TEXTO (P5 do portal, 16/10/2026): `graph.py` importa daqui e
re-exporta os mesmos nomes. O portal roda exatamente estas duas funcoes sobre a nota de
`devolvido_agente` antes de concluir a tarefa (`gateway/human/retomada_precheck.py`), sem carregar o
grafo (langgraph) nem a inferencia (SDK de LLM). So' depende de `cerca_de_saida.py`.
"""

from __future__ import annotations

import re

from .cerca_de_saida import motivo_de_canal_nao_confirmado, motivo_de_recusa

#: `response_kind` (e rota de telemetria) do turno de retomada.
RESPONSE_KIND_RETOMADA: str = "retomada"

#: REDACAO APROVADA PELO DONO (opcao B, 25/09/2026 — spec da diretoria, secao 6). A instrucao do
#: humano vai ENTRE ASPAS e atribuida a "um profissional da nossa equipe": quem le sabe que a
#: orientacao e' de uma pessoa, e que a Helena so' a repassa. Trocar a redacao e' editar ESTA
#: constante (e o teste que a fixa).
RETOMADA_TEMPLATE: str = (
    "Olá, aqui é a Helena, a assistente virtual do seu plano. Um profissional da nossa equipe "
    'revisou o seu caso e pediu que eu repassasse: "{instrucoes}". Posso ajudar com mais alguma coisa?'
)

#: Caracteres de controle (menos quebra de linha e tab) removidos da nota antes do template: nao
#: tem significado numa mensagem e sao o veiculo classico de texto que se disfarca na tela.
_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def compor_mensagem_de_retomada(instrucoes: str) -> str:
    """A mensagem ao beneficiario a partir das instrucoes humanas. PURA (template aprovado, opcao B).

    Deterministica de proposito: a instrucao do humano chega ao beneficiario COMO ESCRITA (menos
    caracteres de controle), sem um modelo no meio para parafrasear, omitir ou "melhorar" uma
    orientacao que outra pessoa assinou. Se produto preferir uma redacao pelo modelo, e' uma troca
    local aqui — as cercas continuam depois, em `HelenaGraph.resume`.
    """
    limpo = _CONTROLE.sub("", instrucoes).strip()
    return RETOMADA_TEMPLATE.format(instrucoes=limpo)


def motivo_de_recusa_da_retomada(texto: str) -> tuple[str, str] | None:
    """As cercas de saida aplicadas ao texto de retomada. `(grupo, padrao)` ou `None`. PURA.

    As MESMAS duas cercas de `HelenaGraph._cercar_saida`, na mesma ordem (canal primeiro), com o
    fato de start declarado: `start_aconteceu=False`, porque neste turno nenhum humano esta' com o
    caso — ele acabou de ser devolvido. Assim a promessa de humano e' barrada em qualquer forma,
    inclusive a mencao de encaminhamento que o humano tenha escrito na nota.
    """
    # `cerca_de_autorizacao=False`: o texto da retomada e' escrito por um ATENDENTE ("sua guia foi
    # liberada" e' fato dele, nao rascunho de modelo); a cerca de afirmacao existe para o modelo.
    return motivo_de_canal_nao_confirmado(texto) or motivo_de_recusa(
        texto, RESPONSE_KIND_RETOMADA, start_aconteceu=False, cerca_de_autorizacao=False
    )
