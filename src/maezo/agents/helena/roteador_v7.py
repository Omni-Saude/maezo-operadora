"""CONTRATO v7 do ROTEADOR da Helena (Plano de ajustes v2, etapa 7; desenho validado na sonda da parte B).

O modelo CONDUZ a conversa; as regras fixas so' protegem. O roteador le' a conversa, a pergunta pendente
(com o TIPO: sim/nao, opcoes ou aberta) e as mensagens novas (varias seguidas = uma fala so'), e devolve UM
JSON de vocabulario fechado: a habilidade, se a fala continua o assunto, a resposta a pendente (sim/nao/sem
clareza, ou a opcao), os sinais de alarme ATUAIS ditos (de quem), a confianca do alarme, se e' historia ou
passado, o tipo da fala de saude, os eventos de assunto (abre/fecha/retoma), os varios pedidos, a recusa da
equipe, a correcao e se precisa perguntar antes de agir.

Este modulo e' PURO: as instrucoes (do pacote de comportamento, `roteador.yaml` do pacote, quando ha'; senao
o padrao daqui), a montagem do prompt com os blocos NAO CONFIAVEIS, e a LEITURA ESTRITA do JSON (enum fora do
vocabulario = invalido; o chamador re-pergunta UMA vez dizendo o campo). Quem chama o modelo e decide e' o
grafo (`roteador.py`); a seguranca (tabela de alarme, trava sem segundo 192, CVV) continua no codigo.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Final

PROMPT_VERSION: Final[str] = "roteador-v7"

#: As habilidades do contrato v7 (o que a pessoa quer agora).
SKILLS: Final[tuple[str, ...]] = (
    "saude",
    "plano",
    "atendimento_humano",
    "conversa",
    "lucas_cobranca",
    "lucas_pedidos",
    "cobertura_fora",
    "fora_do_escopo",
)
#: Os assuntos que entram na lista de assuntos abertos (todas as habilidades menos a conversa).
TOPICOS: Final[tuple[str, ...]] = tuple(s for s in SKILLS if s != "conversa")
SIM_NAO: Final[tuple[str, ...]] = ("sim", "nao", "sem_clareza", "nao_se_aplica")
OPCOES: Final[tuple[str, ...]] = ("1", "2", "nenhuma", "sem_clareza")
DE_QUEM: Final[tuple[str, ...]] = ("pessoa", "terceiro")
CONFIANCA_ALARME: Final[tuple[str, ...]] = ("clear", "uncertain")
TIPOS: Final[tuple[str, ...]] = ("sintoma_proprio", "sintoma_de_terceiro", "duvida_geral", "nenhum")
EVENTOS: Final[tuple[str, ...]] = ("abre", "fecha", "retoma")
TIPOS_DE_PENDENTE: Final[tuple[str, ...]] = ("sim_nao", "opcoes", "aberta")

#: As instrucoes PADRAO (o texto validado na sonda v3, modo longo). O pacote pode trazer outra redacao
#: (`spec/agents/helena/pacote/roteador.yaml`, chave `instrucoes`); o formato de saida e' sempre o daqui.
INSTRUCOES_PADRAO: Final[str] = "\n".join(
    [
        "Voce e' o roteador de um assistente de plano de saude no WhatsApp (a Helena). Leia a CONVERSA "
        "inteira, a "
        "PENDENTE (a ultima pergunta que o assistente deixou, com o tipo) e a(s) MENSAGEM(NS) NOVA(S) "
        "(podem ser "
        "varias seguidas: leia juntas como uma fala so').",
        "Escolha UMA habilidade para as mensagens novas:",
        "- saude: qualquer assunto de saude: duvida geral (o que e', para que serve, exames, qual "
        "especialista, "
        "remedio, dose), OU alguem sente algo agora (dor, febre, queda, batida, piora; da pessoa ou de "
        "outra pessoa), "
        "OU resposta as perguntas de alerta de um sintoma",
        "- plano: dados e uso do plano do proprio titular: carteirinha, dependentes, carencia, plano ativo, "
        "acomodacao, como usar o plano (urgencia, internacao, autorizacao, prazos), incluir dependente",
        "- atendimento_humano: pede explicitamente uma pessoa/atendente/equipe; responde a oferta de "
        "passar para a "
        "equipe; nao quer mais o atendimento aberto; irritacao com ameaca (ANS, Procon, processo)",
        "- conversa: cumprimento, agradecimento, ok, risada, despedida, quem e' voce/o que voce faz, "
        "irritacao ou "
        "reclamacao sem pedido",
        "- lucas_cobranca: mensalidade, boleto, segunda via, valor, vencimento, pagamento, coparticipacao "
        "ja' "
        "cobrada (passado ao Lucas)",
        "- lucas_pedidos: cancelar o plano, confirmar o cancelamento ou voltar atras nele, protocolo do "
        "cancelamento (passado ao Lucas)",
        "- cobertura_fora: se o plano cobre (paga, autoriza) exame, procedimento, cirurgia, terapia, "
        "remedio — "
        "este canal NAO trata cobertura",
        "- fora_do_escopo: o que a Helena nao faz: marcar consulta por ela, buscar medico, reembolso, "
        "audio/foto/"
        "arquivo (ela so' le' texto), outros assuntos",
        "Regras:",
        "1 A pessoa escreve do jeito dela: erros de digitacao, girias, abreviacoes (vc, tbm, pq, hj, okk, "
        "kkk, "
        "valeuu), ironia e exagero ('morrendo de rir', 'morta de cansaco') NAO sao sintomas nem alarme.",
        "2 Se a mensagem responde a PENDENTE, a habilidade e' a dona da pendente; se mudou de assunto, a "
        "do assunto "
        "novo. PENDENTE tipo opcoes: um numero ('1', 'a segunda') ou o nome da opcao = chosen_option. "
        "Resposta a "
        "oferta da equipe ('pode passar', 'pode ser, mas rapido', 'nao precisa') e' atendimento_humano.",
        "3 resposta_sim_nao so' existe com PENDENTE tipo sim_nao: 'sim' afirma ou aceita ('pode ser', "
        "'pode passar "
        "pra equipe sim'); 'nao' nega ou recusa ('nao, ele nao tem', 'acho que nao', 'nao, foi rapidinho', "
        "'eu ja' "
        "disse que nao' = nao); 'sem_clareza' = responde mas nao da' para saber ('sera' que e' isso?', "
        "'talvez'). "
        "Sem PENDENTE sim_nao, outro assunto ou pergunta nova: 'nao_se_aplica'. 'Nao, mas <sinal de "
        "alarme>' = nao "
        "e o sinal vai em alarm_signs_reported.",
        "4 alarm_signs_reported: so' sinais ATUAIS ditos nas MENSAGENS NOVAS: dor/aperto no peito, falta "
        "de ar, "
        "desmaio, convulsao, vomito/tosse com sangue, sangramento forte, fraqueza/formigamento de um lado, "
        "face "
        "caida, fala enrolada, confusao, crianca molinha/sem reagir, febre em bebe <3 meses ou idoso >=65, "
        "batida "
        "na cabeca de bebe <1 ano, bolsa rompida ou sangramento na gravidez, alergia com inchaco/falta de "
        "ar, dor "
        "de cabeca subita 'pior da vida', vontade de morrer ou de se machucar. Febre em adulto sozinha nao "
        "e' "
        "alarme. Negacao ('nao tenho falta de ar'), duvida e o alarme ja' tratado (FATOS.alarme_ja_tratado) "
        "repetido ou comentado nao contam: so' um sinal NOVO conta. Nunca invente sinal.",
        "5 alarm_confidence: 'clear' quando o sinal e' dito de forma clara e atual; 'uncertain' quando e' "
        "vago, "
        "indireto ou duvidoso; com lista vazia use 'clear'.",
        "6 Historia, lembranca ou caso que ja' passou ('meu avo teve infarto', 'minha vizinha ficou "
        "internada') e' "
        "CONTEXTO: is_story_or_past=true, nao alarme.",
        "7 is_continuation_of_topic=true se segue o assunto da ultima troca; encerramento ('ok', 'valeu', "
        "'obrigado') = true; voltar a assunto antigo = false.",
        "8 topic_events (sem 'conversa'): abre X (pedido novo), fecha X (encerra, agradece, recusa ou "
        "desiste de X), "
        "retoma X (volta a assunto antigo da conversa). 'voce nao entendeu' retoma o ultimo assunto da "
        "pessoa.",
        "9 multiple_requests so' com 2+ pedidos diferentes (a habilidade do primeiro vai em skill).",
        "10 refusal_of_team: true quando a pessoa recusa ou desiste da equipe/atendimento/caso ('nao, deixa, "
        "desisto', 'ja' resolvi'). correction: true quando corrige o que disse ou o que o assistente "
        "entendeu ('e' "
        "pra mim, me confundi').",
        "11 needs_clarifying_question so' quando nao da' para agir sem perguntar (para quem e', qual das "
        "perguntas, "
        "'nao, mas <algo vago>'); nunca em alarme claro.",
    ]
)

_FORMATO_DE_SAIDA: Final[str] = (
    "Responda SO' um JSON numa linha, sem texto fora dele, com EXATAMENTE estas chaves: "
    '{"skill":"<habilidade>","is_continuation_of_topic":true|false,'
    '"resposta_sim_nao":"sim|nao|sem_clareza|nao_se_aplica","chosen_option":"1|2|nenhuma|sem_clareza",'
    '"alarm_signs_reported":[{"sinal":"<curto>","de_quem":"pessoa|terceiro"}],"alarm_confidence":"clear|uncertain",'
    '"is_story_or_past":true|false,'
    '"is_personal_symptom_vs_general_question":"sintoma_proprio|sintoma_de_terceiro|duvida_geral|nenhum",'
    '"topic_events":[{"evento":"abre|fecha|retoma","topico":"<habilidade sem conversa>"}],'
    '"multiple_requests":["<habilidade>"],"refusal_of_team":true|false,"correction":true|false,'
    '"needs_clarifying_question":true|false}'
)

_CAMPOS: Final[tuple[str, ...]] = (
    "skill",
    "is_continuation_of_topic",
    "resposta_sim_nao",
    "chosen_option",
    "alarm_signs_reported",
    "alarm_confidence",
    "is_story_or_past",
    "is_personal_symptom_vs_general_question",
    "topic_events",
    "multiple_requests",
    "refusal_of_team",
    "correction",
    "needs_clarifying_question",
)
_BOOLEANOS: Final[frozenset[str]] = frozenset(
    {
        "is_continuation_of_topic",
        "is_story_or_past",
        "refusal_of_team",
        "correction",
        "needs_clarifying_question",
    }
)
_ENUMS: Final[Mapping[str, tuple[str, ...]]] = {
    "skill": SKILLS,
    "resposta_sim_nao": SIM_NAO,
    "chosen_option": OPCOES,
    "alarm_confidence": CONFIANCA_ALARME,
    "is_personal_symptom_vs_general_question": TIPOS,
}
_MAX_SINAIS: Final[int] = 6
_MAX_SINAL: Final[int] = 80


@dataclass(frozen=True)
class SinalDeAlarme:
    sinal: str
    de_quem: str


@dataclass(frozen=True)
class EventoDeAssunto:
    evento: str
    topico: str


@dataclass(frozen=True)
class DecisaoV7:
    """A decisao do roteador v7, VALIDADA (vocabulario fechado em todo campo)."""

    skill: str
    continuacao: bool
    sim_nao: str
    opcao: str
    sinais: tuple[SinalDeAlarme, ...]
    confianca_alarme: str
    historia: bool
    tipo: str
    eventos: tuple[EventoDeAssunto, ...]
    pedidos: tuple[str, ...]
    recusa_equipe: bool
    correcao: bool
    precisa_esclarecer: bool

    @property
    def habilidade_interna(self) -> str:
        """A habilidade do contrato interno (`roteador_contrato.HABILIDADES`) que executa o turno."""
        if self.skill == "saude":
            return "sintoma" if self.tipo in ("sintoma_proprio", "sintoma_de_terceiro") else "saude"
        return _PARA_INTERNA[self.skill]

    @staticmethod
    def interna_de(skill: str) -> str:
        """A habilidade interna de uma habilidade v7 (a `saude` sem o tipo da fala fica `saude`)."""
        return "saude" if skill == "saude" else _PARA_INTERNA[skill]

    def sinais_atuais(self) -> tuple[SinalDeAlarme, ...]:
        """Os sinais de alarme que contam (nunca os de uma historia ou de algo que ja' passou)."""
        return () if self.historia else self.sinais


_PARA_INTERNA: Final[Mapping[str, str]] = {
    "plano": "plano",
    "atendimento_humano": "equipe",
    "conversa": "conversa",
    "lucas_cobranca": "cobranca",
    "lucas_pedidos": "pedidos",
    "cobertura_fora": "cobertura",
    "fora_do_escopo": "conversa",
}


#: A decisao v7 do turno CORRENTE (o roteador ativo a guarda; as habilidades leem a resposta a pendente NO
#: CONTEXTO por ela, no lugar dos leitores de palavra). `None` fora do roteador ativo: valem os de antes.
LEITURA_DO_TURNO: ContextVar[DecisaoV7 | None] = ContextVar("helena_leitura_v7_do_turno", default=None)


def tipo_da_pendente(codigo: str | None) -> str | None:
    """O TIPO da pergunta pendente pelo codigo dela (vocabulario fechado das habilidades), ou `None`."""
    c = str(codigo or "")
    if not c:
        return None
    if c.startswith(("V4_ESCOLHA:", "SAUDE_OFERTA", "ROT_DUVIDA:")):
        return "opcoes"
    if c.startswith(("V4_ALERTA:", "V4_OFERTA:", "BS_ENCERRAR_CASO")):
        return "sim_nao"
    if c.startswith("PERGUNTAR_FICHA_"):
        return "aberta"
    return None


def montar_prompt(
    *,
    instrucoes: str,
    bloco_conversa: str,
    bloco_mensagens: str,
    tipo_pendente: str | None,
    texto_pendente: str | None,
    fatos: Mapping[str, Any],
    aviso: str = "",
) -> str:
    """O prompt v7. `bloco_conversa` e `bloco_mensagens` chegam JA' demarcados como nao confiaveis
    (`render_untrusted_block`); a pendente vai como TIPO + o texto da pergunta da propria Helena."""
    pendente = (
        f'(tipo {tipo_pendente}) "{texto_pendente or ""}"'
        if tipo_pendente in TIPOS_DE_PENDENTE
        else "nenhuma"
    )
    partes = [
        instrucoes.strip(),
        _FORMATO_DE_SAIDA,
        "FATOS: " + json.dumps(dict(fatos), ensure_ascii=False, sort_keys=True),
        "PENDENTE: " + pendente,
        "CONVERSA:",
        bloco_conversa or "(vazia)",
        "MENSAGENS NOVAS:",
        bloco_mensagens,
    ]
    if aviso:
        partes.append(aviso)
    return "\n".join(partes)


def _json_do_texto(bruto: str) -> Mapping[str, Any] | None:
    texto = (bruto or "").strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return None
    try:
        dado = json.loads(texto[inicio : fim + 1])
    except (ValueError, TypeError):
        return None
    return dado if isinstance(dado, Mapping) else None


def campos_invalidos(dado: Mapping[str, Any] | None) -> list[str]:
    """Os campos fora do vocabulario (para a UMA re-pergunta); JSON ilegivel = `["(json)"]`."""
    if dado is None:
        return ["(json)"]
    ruins: list[str] = []
    for campo in _CAMPOS:
        if campo not in dado:
            ruins.append(campo)
            continue
        valor = dado[campo]
        if campo in _BOOLEANOS:
            if not isinstance(valor, bool):
                ruins.append(campo)
        elif campo in _ENUMS:
            if valor not in _ENUMS[campo]:
                ruins.append(campo)
        elif campo == "alarm_signs_reported":
            if not isinstance(valor, list) or any(
                not isinstance(x, Mapping)
                or not isinstance(x.get("sinal"), str)
                or x.get("de_quem") not in DE_QUEM
                for x in valor
            ):
                ruins.append(campo)
        elif campo == "topic_events":
            if not isinstance(valor, list) or any(
                not isinstance(x, Mapping) or x.get("evento") not in EVENTOS or x.get("topico") not in TOPICOS
                for x in valor
            ):
                ruins.append(campo)
        elif campo == "multiple_requests" and (
            not isinstance(valor, list) or any(x not in SKILLS for x in valor)
        ):
            ruins.append(campo)
    return ruins


def ler_decisao(bruto: str) -> tuple[DecisaoV7 | None, list[str]]:
    """O JSON do modelo -> `DecisaoV7`, ou `(None, campos invalidos)`. Nada e' adivinhado."""
    dado = _json_do_texto(bruto)
    ruins = campos_invalidos(dado)
    if ruins or dado is None:
        return None, ruins
    sinais = tuple(
        SinalDeAlarme(" ".join(str(x["sinal"]).split())[:_MAX_SINAL], str(x["de_quem"]))
        for x in dado["alarm_signs_reported"][:_MAX_SINAIS]
        if str(x["sinal"]).strip()
    )
    eventos = tuple(EventoDeAssunto(str(x["evento"]), str(x["topico"])) for x in dado["topic_events"])
    return (
        DecisaoV7(
            skill=str(dado["skill"]),
            continuacao=bool(dado["is_continuation_of_topic"]),
            sim_nao=str(dado["resposta_sim_nao"]),
            opcao=str(dado["chosen_option"]),
            sinais=sinais,
            confianca_alarme=str(dado["alarm_confidence"]),
            historia=bool(dado["is_story_or_past"]),
            tipo=str(dado["is_personal_symptom_vs_general_question"]),
            eventos=eventos,
            pedidos=tuple(dict.fromkeys(str(x) for x in dado["multiple_requests"])),
            recusa_equipe=bool(dado["refusal_of_team"]),
            correcao=bool(dado["correction"]),
            precisa_esclarecer=bool(dado["needs_clarifying_question"]),
        ),
        [],
    )


def aviso_de_reenvio(ruins: Sequence[str]) -> str:
    """O aviso da UMA re-pergunta: so' os nomes dos campos (nunca o texto do modelo)."""
    return (
        f"ATENCAO: a resposta anterior veio com valor invalido ou ausente em {sorted(set(ruins))}; responda "
        "de "
        "novo SO' o JSON, so' com os valores permitidos."
    )


__all__ = [
    "INSTRUCOES_PADRAO",
    "LEITURA_DO_TURNO",
    "PROMPT_VERSION",
    "SKILLS",
    "TOPICOS",
    "DecisaoV7",
    "EventoDeAssunto",
    "SinalDeAlarme",
    "aviso_de_reenvio",
    "campos_invalidos",
    "ler_decisao",
    "montar_prompt",
    "tipo_da_pendente",
]
