"""Textos fixos da oferta da equipe e da recusa dela (avaliacao independente r5 de 25/10/2026).

Modulo PURO (sem import do grafo): o `graph.py` o importa no topo, como `habilidades.sintoma.textos`.
Nenhum destes textos tem equivalente no pacote (`spec/agents/helena/pacote/textos.yaml`), entao ficam aqui.
"""

from __future__ import annotations

from typing import Final

#: O codigo de "onde a conversa parou" (`coleta_pendente`) quando a Helena OFERECEU a equipe e espera sim/nao.
#: Vocabulario fechado, sem texto da pessoa. Dono no contrato do roteador: a habilidade `equipe`.
PENDENTE_OFERTA_EQUIPE: Final[str] = "EQ_OFERTA"

#: A mesma resposta ia sair pela terceira vez seguida (gatilho de loop, DL-0147): em vez de abrir caso
#: sozinho, a Helena reconhece e OFERECE a equipe. O caso so' abre com o "sim" da pessoa.
TEXTO_OFERTA_POR_REPETICAO: Final[str] = (
    "Acho que a minha resposta não está te ajudando. Se você quiser, posso passar o seu atendimento para "
    "uma pessoa da nossa equipe. Quer? Responda sim ou não."
)

#: O roteador leu pedido de pessoa num "sim" solto, sem oferta na conversa e sem verbo de passar: a Helena
#: confirma antes de abrir o caso.
TEXTO_CONFIRMA_PASSAR: Final[str] = (
    "Você quer que eu passe o seu atendimento para uma pessoa da nossa equipe? Responda sim ou não."
)

#: A recusa CURTA, so' quando havia oferta da equipe na conversa (antes, um paragrafo longo com 192 saia
#: fora de contexto). Sem oferta e sem caso, a recusa e' so' reconhecida
#: (`leitura_curta.resposta_de_cortesia`).
RESPOSTA_RECUSA_DE_EQUIPE: Final[str] = (
    "Tudo bem, não vou passar para a equipe. Se mudar de ideia, é só me pedir."
)

#: A recusa com um atendimento ja' aberto (condicional: este turno nao consulta o motor). Nada e' encerrado.
RESPOSTA_RECUSA_COM_CASO: Final[str] = (
    "Tudo bem. Se você já tem um atendimento aberto com a nossa equipe, ele continua lá, sem pressa."
)

#: DL-0088 (complemento, 25/10/2026): quando a oferta recusada era da equipe de SAUDE (oferta de saude
#: pendente ou assunto de sintoma/saude na conversa), a recusa curta guarda a linha de seguranca do 192.
LINHA_192_DA_RECUSA: Final[str] = "Se piorar ou for grave, ligue 192."
RESPOSTA_RECUSA_DE_EQUIPE_SAUDE: Final[str] = f"{RESPOSTA_RECUSA_DE_EQUIPE} {LINHA_192_DA_RECUSA}"
RESPOSTA_RECUSA_COM_CASO_SAUDE: Final[str] = f"{RESPOSTA_RECUSA_COM_CASO} {LINHA_192_DA_RECUSA}"

#: Os textos acima que `classify` deixa prontos: saem como estao, sem modelo
#: (`roteador._e_texto_do_roteador`).
TEXTOS_FIXOS: Final[frozenset[str]] = frozenset(
    {
        TEXTO_OFERTA_POR_REPETICAO,
        TEXTO_CONFIRMA_PASSAR,
        RESPOSTA_RECUSA_DE_EQUIPE,
        RESPOSTA_RECUSA_COM_CASO,
        RESPOSTA_RECUSA_DE_EQUIPE_SAUDE,
        RESPOSTA_RECUSA_COM_CASO_SAUDE,
    }
)

__all__ = [
    "TEXTOS_FIXOS",
    "PENDENTE_OFERTA_EQUIPE",
    "LINHA_192_DA_RECUSA",
    "RESPOSTA_RECUSA_COM_CASO",
    "RESPOSTA_RECUSA_COM_CASO_SAUDE",
    "RESPOSTA_RECUSA_DE_EQUIPE",
    "RESPOSTA_RECUSA_DE_EQUIPE_SAUDE",
    "TEXTO_CONFIRMA_PASSAR",
    "TEXTO_OFERTA_POR_REPETICAO",
]
