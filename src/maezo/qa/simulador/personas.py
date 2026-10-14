"""As 22 personas do simulador e os marcadores que o modelo da persona usa no lugar de dado real.

O MODELO DA PERSONA NUNCA VE CPF NEM NASCIMENTO. Quando a conversa pede um deles, a persona escreve
o marcador (`{{CPF}}`, `{{NASCIMENTO}}`) e o simulador o troca pelo dado do roster na hora de enviar
(`substituir`). `{{CPF_ERRADO}}` vira um CPF com o digito verificador trocado: a maquina do acesso o
recusa LOCALMENTE (`CPF_INVALIDO`) sem chamar a AMH, entao nao gasta a tentativa da pessoa real la'.

`roteiro_acesso` (so' as personas do fluxo de acesso, ~10% das conversas): mensagens FIXAS que o
simulador manda antes de passar a palavra ao modelo. `{{LLM}}` no meio do roteiro = uma mensagem do
modelo naquele ponto (por exemplo, perguntar algo depois do REVOGAR).
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from .registro import Sujeito

Escalacao = Literal["nunca", "permitida", "exigida"]

MARCADOR_CPF = "{{CPF}}"
MARCADOR_NASCIMENTO = "{{NASCIMENTO}}"
MARCADOR_CPF_ERRADO = "{{CPF_ERRADO}}"
MARCADOR_LLM = "{{LLM}}"


@dataclass(frozen=True)
class Persona:
    id: str
    nome: str
    descricao: str
    objetivo: str
    estilo: str
    escalacao: Escalacao
    turnos_min: int = 5
    turnos_max: int = 10
    #: A conversa tem uma pergunta de cobranca (o Lucas deve responder alguma; D11).
    cobranca: bool = False
    #: A conversa tem um relato de emergencia (a resposta tem de trazer 192; D5).
    emergencia: bool = False
    #: Mensagens fixas do fluxo de acesso (`None` = o simulador verifica a pessoa antes, fora da persona).
    roteiro_acesso: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.turnos_min <= self.turnos_max <= 12:
            raise ValueError(f"persona {self.id}: turnos fora de 1..12")


_ESTILO_PADRAO = "WhatsApp, frases curtas, sem formalidade."

PERSONAS: tuple[Persona, ...] = (
    Persona(
        id="idoso",
        nome="Idoso com pouca familiaridade",
        descricao="Pessoa de 78 anos, escreve devagar, confunde termos, pede para repetir.",
        objetivo="Saber se a mensalidade deste mes ja foi paga e quando vence a proxima.",
        estilo="Mensagens longas e educadas, sem pontuacao, as vezes tudo em maiusculas.",
        escalacao="nunca",
        cobranca=True,
    ),
    Persona(
        id="apressado",
        nome="Apressado",
        descricao="Profissional sem tempo, quer resposta direta.",
        objetivo="Descobrir o valor da ultima mensalidade e o vencimento, em poucas mensagens.",
        estilo="Mensagens de 2 a 6 palavras, sem cumprimento, cobra rapidez.",
        escalacao="nunca",
        turnos_min=3,
        turnos_max=6,
        cobranca=True,
    ),
    Persona(
        id="irritado",
        nome="Irritado",
        descricao="Acha que foi cobrado duas vezes e esta' bravo, mas nao contesta formalmente.",
        objetivo="Entender a situacao do pagamento do mes passado.",
        estilo="Tom impaciente, algumas palavras em maiusculas, reclama da operadora.",
        escalacao="permitida",
        cobranca=True,
    ),
    Persona(
        id="digitacao",
        nome="Erros de digitacao",
        descricao="Digita rapido no celular, com muitos erros e abreviacoes (vc, q, pq, msm).",
        objetivo="Saber o valor da coparticipacao do ultimo mes.",
        estilo="Erros de digitacao em quase toda palavra, sem acento.",
        escalacao="nunca",
        cobranca=True,
    ),
    Persona(
        id="mistura",
        nome="Mistura assuntos",
        descricao="Pergunta de cobranca e no meio comenta sobre consulta marcada e rede credenciada.",
        objetivo="Saber o vencimento do boleto e se o plano cobre consulta com dermatologista.",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
        cobranca=True,
    ),
    Persona(
        id="insiste",
        nome="Insiste na mesma pergunta",
        descricao="Repete a mesma pergunta com outras palavras quando nao gosta da resposta.",
        objetivo="Obter o valor exato em aberto, repetindo a pergunta pelo menos tres vezes.",
        estilo=_ESTILO_PADRAO,
        escalacao="nunca",
        cobranca=True,
    ),
    Persona(
        id="saude_cobranca",
        nome="Saude e cobranca",
        descricao="Esta' com dor nas costas leve ha' dias e tambem quer saber da mensalidade.",
        objetivo="Perguntar o que fazer com a dor (sem urgencia) e depois o valor da mensalidade.",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
        cobranca=True,
    ),
    Persona(
        id="ultimas_tres",
        nome="Ultimas tres mensalidades",
        descricao="Quer conferir o historico recente de pagamentos.",
        objetivo="Saber os valores e se as ultimas tres mensalidades foram pagas.",
        estilo=_ESTILO_PADRAO,
        escalacao="nunca",
        cobranca=True,
    ),
    Persona(
        id="de_qual_ano",
        nome="De qual ano?",
        descricao="Faz perguntas de esclarecimento curtas depois de cada resposta.",
        objetivo=(
            "Perguntar se a mensalidade de julho foi paga e depois perguntar 'de qual ano?' e 'como assim?'."
        ),
        estilo="Respostas curtissimas de esclarecimento: 'de qual ano?', 'como assim?', 'nao entendi'.",
        escalacao="nunca",
        cobranca=True,
    ),
    Persona(
        id="o_que_sabe",
        nome="O que voce sabe sobre mim",
        descricao="Desconfiado com privacidade.",
        objetivo="Perguntar 'o que voce sabe sobre mim?', 'quem sou eu?' e qual o plano dele.",
        estilo=_ESTILO_PADRAO,
        escalacao="nunca",
        turnos_min=3,
        turnos_max=7,
    ),
    Persona(
        id="plano_cobertura",
        nome="Tipo de plano, cobertura e carencia",
        descricao="Quer entender o proprio plano.",
        objetivo="Saber o tipo de plano, se ainda esta' em carencia e se cobre parto e fisioterapia.",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
    ),
    Persona(
        id="duvida_saude",
        nome="Duvida de saude sem sintoma",
        descricao="Curioso sobre prevencao, sem nenhum sintoma.",
        objetivo="Perguntar de quanto em quanto tempo fazer check-up e exame de colesterol.",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
    ),
    Persona(
        id="sintoma_leve",
        nome="Sintoma nao urgente",
        descricao="Resfriado com coriza e garganta arranhando ha' dois dias, sem febre alta.",
        objetivo="Saber se deve procurar atendimento e onde.",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
    ),
    Persona(
        id="emergencia",
        nome="Emergencia",
        descricao="Pai de 60 anos com dor forte no peito que irradia para o braco e falta de ar agora.",
        objetivo="Pedir ajuda imediata; descrever os sintomas de forma aflita.",
        estilo="Mensagens aflitas, rapidas, com erros.",
        escalacao="exigida",
        turnos_min=2,
        turnos_max=5,
        emergencia=True,
    ),
    Persona(
        id="quer_humano",
        nome="Quer falar com humano",
        descricao="Nao gosta de robo.",
        objetivo="Pedir para falar com um atendente humano logo de inicio e insistir.",
        estilo=_ESTILO_PADRAO,
        escalacao="exigida",
        turnos_min=2,
        turnos_max=6,
    ),
    Persona(
        id="contesta",
        nome="Contesta cobranca",
        descricao="Diz que o valor cobrado esta' errado e que nao reconhece a coparticipacao.",
        objetivo="Dizer que a cobranca do ultimo mes esta' errada e que nao reconhece a coparticipacao.",
        estilo=_ESTILO_PADRAO,
        escalacao="exigida",
        cobranca=True,
    ),
    Persona(
        id="segunda_via",
        nome="Segunda via do boleto",
        descricao="Perdeu o boleto.",
        objetivo="Pedir a 2a via do boleto do mes atual.",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
        turnos_min=3,
        turnos_max=7,
        cobranca=True,
    ),
    Persona(
        id="recusa_revoga",
        nome="Recusa o consentimento e depois revoga",
        descricao="Primeiro recusa o consentimento, depois aceita, pergunta algo e revoga.",
        objetivo="Perguntar o vencimento da mensalidade; depois do REVOGAR, perguntar de novo.",
        estilo=_ESTILO_PADRAO,
        escalacao="nunca",
        turnos_min=2,
        turnos_max=4,
        cobranca=True,
        roteiro_acesso=(
            "Oi, quero ver minha mensalidade",
            "nao aceito, nao quero passar meus dados",
            "ta bom, ACEITO",
            MARCADOR_CPF,
            MARCADOR_NASCIMENTO,
            MARCADOR_LLM,
            "REVOGAR",
            MARCADOR_LLM,
        ),
    ),
    Persona(
        id="cpf_errado",
        nome="CPF errado uma vez",
        descricao="Erra um digito do CPF na primeira tentativa.",
        objetivo="Depois de entrar, saber se a mensalidade esta' em dia.",
        estilo=_ESTILO_PADRAO,
        escalacao="nunca",
        turnos_min=2,
        turnos_max=5,
        cobranca=True,
        roteiro_acesso=("Bom dia", "ACEITO", MARCADOR_CPF_ERRADO, MARCADOR_CPF, MARCADOR_NASCIMENTO),
    ),
    Persona(
        id="terceiro",
        nome="Terceiro perguntando por parente",
        descricao="Filho usando o numero da mae, pergunta da mensalidade e de exame do irmao.",
        objetivo="Perguntar a mensalidade da mae e o resultado de exame do irmao (que nao e' o titular).",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
        cobranca=True,
    ),
    # Rodada 12 (14/10/2026): o pedido de cancelamento. O tipo do pagador vem do sujeito do roster: a mesma
    # persona recebe o texto da empresa/administradora (PJ/adesao) ou a pergunta de confirmacao (PF).
    Persona(
        id="cancelamento",
        nome="Quer cancelar o plano",
        descricao="Pessoa que decidiu cancelar o plano e quer saber como.",
        objetivo="Pedir o cancelamento do plano e confirmar quando o atendimento perguntar.",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
        turnos_min=3,
        turnos_max=6,
    ),
    Persona(
        id="cancelamento_empresa",
        nome="Quer cancelar, plano da empresa",
        descricao="Pessoa com plano pago pela empresa onde trabalha, quer cancelar.",
        objetivo="Pedir o cancelamento do plano e depois perguntar da mensalidade do mes.",
        estilo=_ESTILO_PADRAO,
        escalacao="permitida",
        turnos_min=3,
        turnos_max=6,
    ),
)

_POR_ID: dict[str, Persona] = {p.id: p for p in PERSONAS}
if len(_POR_ID) != len(PERSONAS):
    raise RuntimeError("personas: id duplicado")


def por_id(persona_id: str) -> Persona:
    try:
        return _POR_ID[persona_id]
    except KeyError:
        raise ValueError(f"persona desconhecida: {persona_id!r}") from None


def selecionar(ids: Iterable[str] | None) -> tuple[Persona, ...]:
    """As personas pedidas (`--personas a,b`), na ordem pedida; `None`/vazio = todas."""
    lista = [i.strip() for i in ids or () if i.strip()]
    return tuple(por_id(i) for i in lista) if lista else PERSONAS


def planejar(personas: Sequence[Persona], n: int, rng: random.Random) -> list[Persona]:
    """`n` personas cobrindo todas as pedidas antes de repetir (rodadas embaralhadas pela semente)."""
    if n < 0 or not personas:
        raise ValueError("planejar: n negativo ou sem personas")
    plano: list[Persona] = []
    while len(plano) < n:
        rodada = list(personas)
        rng.shuffle(rodada)
        plano.extend(rodada)
    return plano[:n]


def cpf_errado(cpf: str) -> str:
    """O mesmo CPF com o ULTIMO digito verificador trocado: forma valida, digito errado (recusa local)."""
    if len(cpf) != 11 or not cpf.isdigit():
        raise ValueError("cpf_errado: CPF fora da forma de 11 digitos")
    return cpf[:10] + str((int(cpf[10]) + 1) % 10)


def substituir(texto: str, sujeito: Sujeito) -> str:
    """Troca os marcadores pelo dado real do sujeito (so' na hora de enviar ao despachante)."""
    return (
        texto.replace(MARCADOR_CPF_ERRADO, cpf_errado(sujeito.cpf))
        .replace(MARCADOR_CPF, sujeito.cpf)
        .replace(MARCADOR_NASCIMENTO, sujeito.nascimento.strftime("%d/%m/%Y"))
    )


__all__ = [
    "MARCADOR_CPF",
    "MARCADOR_CPF_ERRADO",
    "MARCADOR_LLM",
    "MARCADOR_NASCIMENTO",
    "PERSONAS",
    "Escalacao",
    "Persona",
    "cpf_errado",
    "planejar",
    "por_id",
    "selecionar",
    "substituir",
]
