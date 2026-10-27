"""UMA linha com os sinais de alerta da queixa (decisao do dono de 27/10/2026, opcao A). Modulo PURO.

RASCUNHO DEV — REVISAO MEDICA PENDENTE. Cada linha CONDENSA as `perguntas_de_alerta` com destino
LIGAR_192_AGORA / PRONTO_SOCORRO_AGORA / CVV do protocolo (`spec/agents/helena/pacote/protocolos/**`), na
linguagem leiga das perguntas. Ficam no codigo (e nao no pacote) porque o `Protocolo` do pacote recusa chave
nova e uma imagem anterior derrubaria o pacote inteiro (issue #1005).

Quando a pessoa pergunta QUAL profissional procurar, a Helena responde direto (especialista do mapa, clinico
como porta de entrada, canais) e fecha com esta linha, sem as perguntas de alerta. Sem linha propria:
a linha do alerta universal do publico.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from maezo.conhecimento import protocolos as _p

_LIGUE: Final[str] = "procure um pronto-socorro ou ligue 192"

#: protocolo -> a linha (comeca com "Se ..."). Rascunho dev, revisao medica pendente.
LINHAS: Final[Mapping[str, str]] = {
    # --- adulto -------------------------------------------------------------------------------------------
    "tri-adulto-dor-no-peito": (
        "Se a dor vier com suor frio, enjoo, falta de ar ou sensação de desmaio, ou aparecer mesmo parado, "
        "ligue 192."
    ),
    "tri-adulto-palpitacoes": (
        f"Se o coração disparado vier com dor no peito, falta de ar, desmaio ou suor frio, {_LIGUE}."
    ),
    "tri-adulto-falta-de-ar": (
        "Se a falta de ar ficar tão forte que você não consegue falar uma frase, vier com dor no peito ou "
        "com "
        "inchaço no rosto ou na garganta, ou você ficar sonolento ou confuso, ligue 192."
    ),
    "tri-adulto-tontura": (
        "Se a tontura vier com fala enrolada, boca torta, fraqueza de um lado do corpo, desmaio, dor no "
        "peito "
        f"ou uma dor de cabeça nova e forte, {_LIGUE}."
    ),
    "tri-adulto-dor-de-cabeca": (
        "Se a dor de cabeça começar de repente e muito forte, vier com confusão, fala enrolada, fraqueza de "
        f"um lado do corpo, convulsão, ou febre com pescoço duro, {_LIGUE}."
    ),
    "tri-adulto-dor-nas-costas": (
        "Se a perna ficar fraca, aparecer dormência entre as pernas ou nas nádegas, você perder o controle "
        "do "
        "xixi ou do cocô, ou a dor vier com febre alta ou depois de uma queda, procure um pronto-socorro."
    ),
    "tri-adulto-dor-no-braco-ou-perna": (
        "Se o braço ou a perna ficar de repente frio, pálido ou sem força, a perna inchar e doer, ou vier "
        f"falta de ar ou dor no peito junto, {_LIGUE}."
    ),
    "tri-adulto-queda-ou-trauma": (
        "Se depois da queda houver desmaio, confusão, fraqueza ou dormência, algo torto ou muito inchado, ou "
        f"você não conseguir apoiar ou mexer, {_LIGUE}."
    ),
    "tri-adulto-dor-ao-urinar": (
        "Se vier febre, calafrios, dor forte nas costas ou no lado da barriga, vômitos ou sangue na urina, "
        "procure atendimento ainda hoje; se houver confusão ou desmaio, ligue 192."
    ),
    "tri-adulto-sangue-na-urina": (
        "Se você não conseguir urinar, a dor ficar muito forte, ou vier febre alta com calafrios, procure um "
        "pronto-socorro; se houver desmaio ou fraqueza muito forte, ligue 192."
    ),
    "tri-adulto-dor-de-barriga": (
        "Se a dor começar de repente e muito forte, a barriga ficar dura, vier desmaio, suor frio, fezes "
        f"pretas ou com sangue, {_LIGUE}."
    ),
    "tri-adulto-diarreia-e-vomito": (
        "Se aparecer sangue no vômito ou nas fezes, fezes pretas, dor de barriga muito forte, desmaio ou "
        f"confusão, {_LIGUE}."
    ),
    "tri-adulto-mal-estar": (
        "Se aparecer falta de ar forte, dor no peito, desmaio, confusão, boca torta, fala enrolada ou "
        "fraqueza "
        f"de um lado do corpo, {_LIGUE}."
    ),
    "tri-adulto-manchas-na-pele": (
        f"Se a boca, a língua ou o rosto incharem, faltar o ar, ou vier febre com manchas roxas, {_LIGUE}."
    ),
    "tri-adulto-dor-de-garganta": (
        f"Se ficar difícil respirar, você não conseguir abrir a boca ou a voz ficar abafada, {_LIGUE}."
    ),
    "tri-adulto-dor-de-ouvido": (
        "Se vier confusão, falta de ar, febre alta, sangramento que não para, rosto torto ou inchaço atrás "
        "da "
        f"orelha, {_LIGUE}."
    ),
    "tri-adulto-sinusite-gripe": (
        "Se faltar o ar, aparecer inchaço em volta do olho, a visão mudar, o pescoço ficar duro ou vier "
        f"confusão, {_LIGUE}."
    ),
    "tri-adulto-olho-vermelho": (
        "Se a visão piorar de repente, aparecer uma sombra ou flashes de luz, dor forte no olho com enjoo, "
        "ou "
        "cair produto químico no olho, procure um pronto-socorro."
    ),
    "tri-adulto-sangramento-genital": (
        "Se o sangramento for muito maior que o normal, vier desmaio, fraqueza forte, dor muito forte ou "
        f"febre, ou houver chance de gravidez, {_LIGUE}."
    ),
    "tri-adulto-alergia-grave": (
        "Se faltar o ar, a garganta parecer fechar, a boca ou o rosto incharem, ou vier tontura forte ou "
        "desmaio, ligue 192."
    ),
    "tri-adulto-convulsao": (
        "Se a crise durar mais de 5 minutos, vier uma atrás da outra, ou a pessoa continuar confusa ou com "
        "dificuldade para respirar, ligue 192."
    ),
    "tri-adulto-intoxicacao": (
        "Se a pessoa desmaiar, ficar sonolenta ou confusa, tiver convulsão ou dificuldade para respirar, "
        "ligue 192."
    ),
    "tri-adulto-queimadura": (
        "Se a queimadura for grande, pegar rosto, mãos, pés ou partes íntimas, for por choque ou produto "
        f"químico, ou houver fumaça respirada, {_LIGUE}."
    ),
    "tri-adulto-mordida-de-animal": (
        "Se foi morcego ou animal silvestre, o machucado for fundo, no rosto ou nas mãos, ou o animal "
        "parecia "
        "doente, procure atendimento ainda hoje."
    ),
    "uni-alerta-universal": (
        "Se aparecer falta de ar forte, dor no peito, desmaio, confusão, boca torta, fala enrolada, fraqueza "
        f"de um lado do corpo ou sangramento forte, {_LIGUE}."
    ),
    # --- doencas cronicas ---------------------------------------------------------------------------------
    "cron-crise-de-pressao": (
        "Se vier dor no peito, falta de ar forte, fraqueza de um lado do corpo, boca torta, fala enrolada ou "
        "perda da visão, ligue 192."
    ),
    "cron-glicose-alta-ou-baixa": (
        "Se vier confusão, sonolência forte, convulsão, suor frio com tremedeira, ou vômitos com respiração "
        f"rápida, {_LIGUE}."
    ),
    "cron-esqueci-o-remedio": (
        "Se vier dor no peito, falta de ar forte, fraqueza de um lado do corpo, fala enrolada ou sonolência "
        f"forte, {_LIGUE}."
    ),
    # --- saude mental -------------------------------------------------------------------------------------
    "mental-depressao": (
        "Se aparecerem pensamentos de se machucar ou de morrer, ligue 188 (CVV) ou 192; se a pessoa ouvir "
        "vozes ou ficar muito desconfiada, procure um pronto-socorro."
    ),
    "mental-crise-de-ansiedade": (
        "Se vier dor forte no peito, falta de ar muito forte ou desmaio, ligue 192; se aparecerem "
        "pensamentos de se machucar, ligue 188 (CVV) ou 192."
    ),
    "mental-alcool-e-drogas": (
        "Se a pessoa não acordar, respirar fraco, tiver convulsão, dor no peito, ou tremedeira e confusão "
        "depois de parar a bebida, ligue 192."
    ),
    "mental-risco-de-suicidio": (
        "Se o pensamento de se machucar aparecer agora ou hoje, ligue 188 (CVV) ou 192."
    ),
    # --- criancas (a linha do alerta universal da crianca vale para qualquer queixa delas) ------------------
    "uni-alerta-universal-crianca": (
        "Se a criança tiver convulsão, ficar muito molinha ou difícil de acordar, respirar com muito "
        "esforço, "
        f"ficar com os lábios roxos, não conseguir beber ou apresentar manchas roxas, {_LIGUE}."
    ),
}

_GESTACAO: Final[str] = (
    "Se vier sangramento, perda de líquido, dor forte na barriga, o bebê mexer menos, dor de cabeça forte "
    "com "
    "visão turva, falta de ar ou febre, procure a maternidade ou ligue 192."
)


def linha(protocolo: _p.Protocolo | None, publico: str) -> str:
    """A linha de sinais da queixa; sem linha propria, a do alerta universal do publico."""
    if protocolo is not None and protocolo.id in LINHAS:
        return LINHAS[protocolo.id]
    if publico in ("gestante", "puerpera"):
        return _GESTACAO
    if publico in _p.PUBLICOS_DE_CRIANCA:
        return LINHAS[_p.UNIVERSAL_CRIANCA]
    return LINHAS[_p.UNIVERSAL_ADULTO]


def mental(protocolo: _p.Protocolo | None) -> bool:
    return protocolo is not None and protocolo.tema == "saude_mental"


__all__ = ["LINHAS", "linha", "mental"]
