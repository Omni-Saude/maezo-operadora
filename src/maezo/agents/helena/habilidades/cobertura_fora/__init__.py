"""Habilidade `cobertura` FORA da Helena (dono, 23/10/2026, DL-0154; Plano de ajustes v2, etapa 5).

A Helena nao trata mais de cobertura: nenhuma consulta ao Rol, a carencia ou a segmentacao para dizer se um
procedimento esta' coberto, nenhuma explicacao de Rol/carencia e nenhuma oferta da equipe por causa da
cobertura. Toda pergunta de cobertura recebe UM aviso fixo com os canais da empresa
(`TEXTO_COBERTURA_FORA`, chave `cobertura_fora` do pacote de textos). O codigo da cobertura assistida
(`cobertura_assistida.py`, `cobertura_fluxo.py`, `cobertura_rol.py`) e o modo `MAEZO_HELENA_COBERTURA_MODO`
sairam; o historico do repositorio guarda a versao anterior (ate' 3c619d91).

Os DADOS do plano (carteirinha, dependentes, carencias do proprio titular, tipo de plano) continuam com a
habilidade `plano`. A trava de saida `cobertura_sem_promessa` continua: nenhum rascunho afirma cobertura.
"""

from __future__ import annotations

import re
from typing import Final

#: O aviso do dono (23/10/2026), com os nomes CONFIRMADOS dos canais (cerca de canal; coordenador, 23/10).
TEXTO_COBERTURA_FORA: Final[str] = (
    "Por aqui eu não trato de cobertura. Você consegue essa informação pelo aplicativo Austa Clínicas, "
    "pelo portal do plano ou pela central de atendimento do plano."
)
#: A chave do texto no pacote de comportamento (`spec/agents/helena/pacote/textos.yaml`).
CHAVE_TEXTO: Final[str] = "cobertura_fora"


# --- Os detectores da conversa que o aviso respeita (limpeza da saude, etapa 3) --------------------------
#: MOVIDOS SEM MUDANCA de `helena_v4.py` (`especialidade_pedida`) e de `conhecimento/saude_gerativa.py`
#: (`queixa_atual`), que sairam do codigo (dono, 02/11/2026): a pergunta com especialista pedido ou com queixa
#: atual na mesma mensagem nao recebe so' o aviso (`graph._cobertura_fora`). So' leem o texto; nada de saude
#: e' decidido aqui.
_ESPECIALIDADES: Final[tuple[tuple[str, re.Pattern[str], bool], ...]] = (
    ("cardiologista", re.compile(r"\bcardiolog(?:ista|istas|ia)\b"), False),
    ("dermatologista", re.compile(r"\bdermato(?:logistas?|logia)?\b"), False),
    ("ortopedista", re.compile(r"\bortoped(?:ista|istas|ia)\b"), False),
    ("endocrinologista", re.compile(r"\bendocrino(?:logistas?|logia)?\b"), False),
    ("urologista", re.compile(r"\burolog(?:ista|istas|ia)\b"), False),
    ("oftalmologista", re.compile(r"\b(?:oftalmolog(?:ista|istas|ia)|oculista)\b"), False),
    ("neurologista", re.compile(r"\bneurolog(?:ista|istas|ia)\b"), False),
    ("otorrinolaringologista", re.compile(r"\botorrino(?:laringolog(?:ista|istas|ia))?\b"), False),
    ("gastroenterologista", re.compile(r"\bgastroenterolog(?:ista|istas|ia)\b"), False),
    ("pneumologista", re.compile(r"\bpneumolog(?:ista|istas|ia)\b"), False),
    ("reumatologista", re.compile(r"\breumatolog(?:ista|istas|ia)\b"), False),
    # Consulta BASICA:
    ("ginecologista", re.compile(r"\bginecolog(?:ista|istas|ia)\b"), True),
    ("obstetra", re.compile(r"\bobstetras?\b"), True),
    ("pediatra", re.compile(r"\bpediatras?\b"), True),
    ("clínico geral", re.compile(r"\bclinicos? gerais\b|\bclinico geral\b"), True),
)
#: O pedido NAO e' de consulta quando fala de outra coisa: procedimento, exame, resultado, papel, cobranca.
#: "cobre ressonancia?" segue o caminho de hoje (cobertura deterministica da consulta do plano).
_NAO_E_PEDIDO_DE_CONSULTA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:exames?|ressonancia|tomografia|ultrassom|ultrassonografia|raio x|biopsia|cirurgias?|procedimentos?"
    r"|internacao|tratamentos?|terapias?|laudos?|resultados?|receitas?|atestados?|encaminhamentos?"
    r"|reembolso|autorizacao|guias?|pedido medico|boletos?|mensalidades?|coparticipacao|faturas?"
    r"|cirurgi\w+|cobranca)\b"
)


def especialidade_pedida(plano: str) -> tuple[str, bool] | None:
    """`(nome, consulta_basica)` da PRIMEIRA especialidade do lexico no texto normalizado, ou `None`.

    `None` tambem quando a mensagem fala de procedimento, exame, resultado, papel ou cobranca (o caminho
    de hoje). So' le' o texto: quem exclui sintoma, pedido de pessoa e portoes de seguranca e' `graph.py`."""
    if _NAO_E_PEDIDO_DE_CONSULTA.search(plano):
        return None
    achado: tuple[int, str, bool] | None = None
    for nome, padrao, basica in _ESPECIALIDADES:
        m = padrao.search(plano)
        if m is not None and (achado is None or m.start() < achado[0]):
            achado = (m.start(), nome, basica)
    return None if achado is None else (achado[1], achado[2])


_QUADRO_ATUAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:estou|to|tou|ando|eu estou) (?:com|sentindo|tendo|perdendo|passando mal)\b"
    r"|\b(?:esta|ta|tá|estao|tao) (?:com|sentindo|tendo)\b|\bsinto agora\b|\bestou sentindo\b"
    r"|\bdesde (?:ontem|hoje|anteontem|cedo|a noite|de manha|\d+|o dia|a semana|segunda|terca|quarta|quinta"
    r"|sexta|sabado|domingo)\b|\bcomec\w* (?:hoje|agora|ontem|ha|de repente|faz)\b"
    r"|\b(?:ha|faz) (?:\d+|um|uma|dois|duas|tres|alguns|algumas) (?:minutos?|horas?|dias?|semanas?)\b"
    r"|\b(?:agora|neste momento|nesse momento|de repente)\b|\b(?:me|minha|meu) \w+ (?:doi|doendo)\b"
    r"|\bestou gravida\b[^.?!]{0,40}\b(?:sangr|dor|perd)"
)


def queixa_atual(plano: str) -> bool:
    """A mensagem (normalizada) descreve um QUADRO ATUAL (presente ou tempo de inicio)?"""
    return bool(_QUADRO_ATUAL.search(plano))


__all__ = ["CHAVE_TEXTO", "TEXTO_COBERTURA_FORA", "especialidade_pedida", "queixa_atual"]
