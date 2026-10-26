"""O NUCLEO PURO da habilidade de protocolo (sem grafo, sem rede): prompts, leitura ESTRITA do JSON do modelo,
a REGRA deterministica e os textos fixos.

"O MODELO conversa, o PROTOCOLO diz o que fazer, o CODIGO garante a seguranca" (dono, 25/10/2026):
- o modelo ESCOLHE o protocolo pelo sentido (id da lista fechada ou "nenhum") e LE a resposta a cada pergunta
  de alerta (sim / nao / sem_clareza) — vocabulario fechado, UMA re-pergunta quando o JSON vem invalido;
- as perguntas que a pessoa le' sao o texto do PROTOCOLO, numeradas, numa mensagem so' (sem modelo);
- a REGRA (`decidir`) e' deterministica (ADR-0012): o sinal mais grave presente leva ao destino dele; sem
  clareza, pergunta de novo UMA vez e depois oferece a equipe de saude (sem 192); nenhum sinal, o destino
  sem sinal + a orientacao final + o especialista + os canais. NENHUM prazo da ANS em texto nenhum.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from maezo.conhecimento import protocolos as _p

PROMPT_ESCOLHA: Final[str] = "protocolo-escolha-v1"
PROMPT_LEITURA: Final[str] = "protocolo-leitura-v1"
PROMPT_MAPA: Final[str] = "protocolo-mapa-v1"

#: Pendencias (vocabulario fechado, sem texto da pessoa). Dona no contrato do roteador: `sintoma`.
PREFIXO: Final[str] = "PROT_"
PENDENTE_ALERTA: Final[str] = "PROT_ALERTA:"
PENDENTE_IDADE: Final[str] = "PROT_IDADE"

RESPOSTAS: Final[tuple[str, ...]] = ("sim", "nao", "sem_clareza")
PUBLICOS_DA_ESCOLHA: Final[tuple[str, ...]] = ("adulto", "crianca", "gestante", "puerpera")
FAIXAS: Final[tuple[str, ...]] = ("0_2m", "2m_5a", "5_12a", "adolescente", "desconhecida", "nao_se_aplica")
_PUBLICO_DA_FAIXA: Final[Mapping[str, str]] = {
    "0_2m": "crianca_0_2m",
    "2m_5a": "crianca_2m_5a",
    "5_12a": "crianca_5_12a",
    "adolescente": "adolescente",
}
_EXEMPLOS_POR_PROTOCOLO: Final[int] = 4


# --- Textos fixos ------------------------------------------------------------------------------------------
RODAPE_DAS_PERGUNTAS: Final[str] = 'Pode responder do seu jeito, por exemplo: "nenhuma" ou "a 2 sim".'
TEXTO_DA_REPERGUNTA: Final[str] = "Desculpe, não entendi bem. Só para confirmar, isto acontece agora?"
RODAPE_DA_REPERGUNTA: Final[str] = "Responda sim ou não para cada uma."
TEXTO_IDADE: Final[str] = "Para eu te orientar direito: qual é a idade da criança?"
PONTE_SEM_SINAL: Final[str] = "Obrigada. Pelo que você contou, dá para marcar uma consulta."
TEXTO_AVALIAR_A_CAUSA: Final[str] = "Quem pode avaliar a causa é o médico, numa consulta."
TEXTO_JA_RESPONDIDO: Final[str] = "Desculpe, você já tinha me respondido."
TEXTO_NAO_MARCO: Final[str] = "Por aqui eu não consigo marcar a consulta."
_DESTINO_POR_SINAL: Final[Mapping[str, str]] = {
    "PRONTO_SOCORRO_AGORA": (
        "Pelo que você respondeu, o mais seguro é ir agora a um pronto-socorro. Se piorar no caminho ou a "
        "pessoa não responder, ligue 192."
    ),
    "ATENDIMENTO_HOJE": (
        "Pelo que você respondeu, o ideal é ser atendido ainda hoje, num pronto-atendimento ou numa consulta "
        "no mesmo dia. Se piorar ou aparecer algum sinal de alerta, procure um pronto-socorro ou ligue 192."
    ),
}
_DESTINO_POR_SINAL_GESTACAO: Final[Mapping[str, str]] = {
    "PRONTO_SOCORRO_AGORA": (
        "Pelo que você respondeu, o mais seguro é ir agora à maternidade ou a um pronto-socorro. "
        "Se piorar no caminho, ligue 192."
    ),
    "ATENDIMENTO_HOJE": (
        "Pelo que você respondeu, o ideal é ser avaliada ainda hoje, na maternidade ou num "
        "pronto-atendimento. Se piorar ou aparecer algum sinal de alerta, procure a maternidade ou ligue 192."
    ),
}
#: A frase de seguranca no fim da orientacao final do protocolo ("Se piorar ...", "Se a crianca piorar ...").
_SEGURANCA_NO_FIM: Final[re.Pattern[str]] = re.compile(
    r"\s*(Se (?:a criança |o bebê |a pessoa |você )?piorar\b[^\n]*)$"
)


def textos_fixos() -> frozenset[str]:
    """Os textos INTEIROS desta habilidade que nao dependem do protocolo (o `respond` os envia sem modelo)."""
    return frozenset({TEXTO_IDADE, *_DESTINO_POR_SINAL.values(), *_DESTINO_POR_SINAL_GESTACAO.values()})


def rotulo(protocolo: _p.Protocolo) -> str:
    """Como a Helena chama a queixa do protocolo ("dor ou ardência para urinar")."""
    if protocolo.id == _p.UNIVERSAL_ADULTO:
        return "o que você está sentindo"
    if protocolo.id == _p.UNIVERSAL_CRIANCA:
        return "o que a criança está sentindo"
    t = protocolo.titulo.strip()
    return t[:1].lower() + t[1:] if len(t) > 1 and not t[1:2].isupper() else t


def cabecalho(protocolo: _p.Protocolo) -> str:
    """A primeira linha das perguntas: identifica o protocolo no historico curto (cabe nos 300 caracteres)."""
    return f"Sobre {rotulo(protocolo)}, me diga se acontece alguma destas coisas agora:"


def texto_das_perguntas(
    protocolo: _p.Protocolo, indices: Sequence[int] | None = None, *, primeira: bool = False
) -> str:
    """As perguntas de alerta do protocolo, numeradas 1..k, numa mensagem so' (texto do protocolo, sem
    modelo). `indices` (1..N): so' estas perguntas — na re-pergunta (as sem clareza) ou, com `primeira`, na
    primeira vez quando a pessoa ja' respondeu as outras no que contou (26/10/2026)."""
    perguntas = protocolo.perguntas_de_alerta
    if indices is None:
        linhas = [f"{i}) {q.pergunta}" for i, q in enumerate(perguntas, start=1)]
        return "\n".join([cabecalho(protocolo), *linhas, RODAPE_DAS_PERGUNTAS])
    linhas = [f"{n}) {perguntas[i - 1].pergunta}" for n, i in enumerate(indices, start=1)]
    if primeira:
        return "\n".join([cabecalho(protocolo), *linhas, RODAPE_DAS_PERGUNTAS])
    return "\n".join([TEXTO_DA_REPERGUNTA, *linhas, RODAPE_DA_REPERGUNTA])


# --- Perguntas JA' respondidas no que a pessoa contou (teste do simulador de 26/10/2026) ---------------
PRE_RESPOSTAS: Final[tuple[str, ...]] = ("sim", "nao", "nao_dito")
#: Destinos urgentes: um "sim" ja' dito leva direto a eles, sem perguntar o resto.
DESTINOS_URGENTES: Final[frozenset[str]] = frozenset(
    {"LIGAR_192_AGORA", "CVV_188_E_192", "PRONTO_SOCORRO_AGORA", "ATENDIMENTO_HOJE"}
)


def prompt_da_pre_leitura(protocolo: _p.Protocolo, *, bloco_conversa: str, bloco_mensagem: str) -> str:
    perguntas = "\n".join(f"{n}) {q.pergunta}" for n, q in enumerate(protocolo.perguntas_de_alerta, 1))
    k = len(protocolo.perguntas_de_alerta)
    return "\n".join(
        [
            "Voce confere quais destas perguntas JA RESPONDIDAS pela pessoa no que ela contou (mensagem "
            "nova e conversa), ANTES de um assistente de saude pergunta-las. Para CADA pergunta, na ordem:",
            '- "sim": a pessoa AFIRMOU claramente, sobre agora, TUDO o que a pergunta pede, inclusive a '
            "intensidade ('cai da escada' responde 'veio depois de uma queda?' = sim; 'dor nas costas' NAO "
            "responde 'dor forte nas costas?');",
            '- "nao": a pessoa NEGOU claramente ("nao bati a cabeca" = nao para a pergunta da cabeca);',
            '- "nao_dito": nao foi dito, foi so\' perguntado, hipotese ("sera\' que", "e se"), duvida.',
            "Nunca invente. Na duvida, nao_dito.",
            f'Responda SO\' um JSON numa linha: {{"respostas":[{k} valores sim|nao|nao_dito]}}',
            "PERGUNTAS:",
            perguntas,
            "CONVERSA:",
            bloco_conversa or "(vazia)",
            "MENSAGEM NOVA:",
            bloco_mensagem,
        ]
    )


def ler_pre_leitura(bruto: str, quantas: int) -> tuple[tuple[str, ...] | None, list[str]]:
    dado = _json(bruto)
    respostas = dado.get("respostas") if dado is not None else None
    if (
        not isinstance(respostas, list)
        or len(respostas) != quantas
        or any(r not in PRE_RESPOSTAS for r in respostas)
    ):
        return None, ["respostas"]
    return tuple(str(r) for r in respostas), []


@dataclass(frozen=True)
class PlanoDasPerguntas:
    """O que fazer com as respostas ja' ditas: `decisao` (destino) ou `faltam` (so' estas)."""

    decisao: Decisao | None
    faltam: tuple[int, ...]


def plano_das_perguntas(protocolo: _p.Protocolo, pre: Sequence[str]) -> PlanoDasPerguntas:
    """A REGRA sobre as respostas ja' ditas: "sim" urgente -> o destino dele na hora; perguntas nao ditas ->
    so' elas; tudo dito sem "sim" (ou "sim" so' de consulta) -> a decisao com o que foi dito."""
    n = len(protocolo.perguntas_de_alerta)
    todos = tuple(range(1, n + 1))
    respostas = tuple("nao" if r == "nao_dito" else r for r in pre)
    urgentes = [
        i
        for i, r in zip(todos, pre, strict=True)
        if r == "sim" and protocolo.perguntas_de_alerta[i - 1].destino_se_sim in DESTINOS_URGENTES
    ]
    if urgentes:
        return PlanoDasPerguntas(decidir(protocolo, todos, respostas, 0), ())
    faltam = tuple(i for i, r in zip(todos, pre, strict=True) if r == "nao_dito")
    if faltam:
        return PlanoDasPerguntas(None, faltam)
    return PlanoDasPerguntas(decidir(protocolo, todos, respostas, 0), ())


# --- Queda/trauma tem prioridade sobre o protocolo da parte do corpo (26/10/2026) -----------------------
PROTOCOLO_TRAUMA_ADULTO: Final[str] = "tri-adulto-queda-ou-trauma"
#: Os protocolos de DOR/parte do corpo (e o alerta universal) que cedem ao de queda/trauma no adulto.
_CEDEM_AO_TRAUMA: Final[frozenset[str]] = frozenset(
    {
        "tri-adulto-dor-nas-costas",
        "tri-adulto-dor-no-braco-ou-perna",
        "tri-adulto-dor-de-cabeca",
        "tri-adulto-mal-estar",
        _p.UNIVERSAL_ADULTO,
    }
)
#: Palavra CLARA de queda ou trauma (texto normalizado, sem acento), nao negada ("nao cai").
_TRAUMA: Final[re.Pattern[str]] = re.compile(
    r"(?<!nao )\b(?:cai|caiu|caimos|queda|tombo|escorreg\w*|bati|bateu|batida|pancada|acidente|torci|"
    r"torceu|torcao|atropel\w*|trombad\w*)\b"
)


def trauma_no_texto(texto_plano: str) -> bool:
    return _TRAUMA.search(texto_plano or "") is not None


def com_prioridade_do_trauma(escolha: Escolha, texto_plano: str, catalogo: _p.Catalogo) -> Escolha:
    """Adulto com queda/trauma dito e o modelo escolheu um protocolo de dor/parte do corpo (ou nenhum): o de
    queda/trauma vale (ele pergunta o que importa depois da queda)."""
    if (
        escolha.publico == "adulto"
        and (escolha.protocolo is None or escolha.protocolo in _CEDEM_AO_TRAUMA)
        and PROTOCOLO_TRAUMA_ADULTO in catalogo.protocolos
        and trauma_no_texto(texto_plano)
    ):
        return Escolha(escolha.publico, escolha.faixa, PROTOCOLO_TRAUMA_ADULTO)
    return escolha


def protocolos_na_conversa(falas_da_helena: Sequence[str], catalogo: _p.Catalogo) -> list[str]:
    """Os protocolos cujas perguntas a Helena JA' fez nesta conversa e que JA' tiveram resposta (alguma fala
    da Helena depois delas), do mais antigo ao mais recente."""
    por_cabecalho = {cabecalho(p): p.id for p in catalogo.protocolos.values()}
    achados: list[str] = []
    for i, fala in enumerate(falas_da_helena):
        for cab, pid in por_cabecalho.items():
            if fala.startswith(cab) and i < len(falas_da_helena) - 1 and pid not in achados:
                achados.append(pid)
    return achados


# --- Pendencia --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PendenteDoAlerta:
    protocolo: str
    rodada: int
    indices: tuple[int, ...]


def codigo_do_alerta(protocolo: str, rodada: int = 0, indices: Sequence[int] = ()) -> str:
    sufixo = f":{','.join(str(i) for i in indices)}" if indices else ""
    return f"{PENDENTE_ALERTA}{protocolo}:{rodada}{sufixo}"


def ler_pendente_do_alerta(codigo: str | None) -> PendenteDoAlerta | None:
    c = str(codigo or "")
    if not c.startswith(PENDENTE_ALERTA):
        return None
    partes = c[len(PENDENTE_ALERTA) :].split(":")
    if len(partes) not in (2, 3) or partes[1] not in ("0", "1"):
        return None
    try:
        indices = tuple(int(x) for x in partes[2].split(",")) if len(partes) == 3 else ()
    except ValueError:
        return None
    return PendenteDoAlerta(partes[0], int(partes[1]), indices)


# --- A REGRA deterministica --------------------------------------------------------------------------------
@dataclass(frozen=True)
class Decisao:
    """`destino` (com `sinal` quando veio de um SIM), `repergunta` (os indices) ou `oferta_equipe`."""

    tipo: str
    destino: str | None = None
    sinal: str | None = None
    indices: tuple[int, ...] = ()


def decidir(
    protocolo: _p.Protocolo, indices: Sequence[int], respostas: Sequence[str], rodada: int
) -> Decisao:
    """O sinal MAIS GRAVE presente leva ao destino dele; sem sinal e com duvida, re-pergunta UMA vez (so' as
    duvidosas) e depois oferece a equipe; nenhum sinal, o destino sem sinal. As perguntas fora de `indices`
    ja' foram respondidas "nao" na rodada anterior."""
    perguntas = protocolo.perguntas_de_alerta
    presentes = [perguntas[i - 1] for i, r in zip(indices, respostas, strict=True) if r == "sim"]
    if presentes:
        mais_grave = max(presentes, key=lambda q: _p.GRAVIDADE[q.destino_se_sim])
        return Decisao("destino", mais_grave.destino_se_sim, mais_grave.sinal)
    duvidosas = tuple(i for i, r in zip(indices, respostas, strict=True) if r == "sem_clareza")
    if duvidosas:
        return Decisao("repergunta", indices=duvidosas) if rodada == 0 else Decisao("oferta_equipe")
    return Decisao("destino", protocolo.destino_se_nenhum_sinal)


# --- Textos de orientacao ---------------------------------------------------------------------------------
def _gestacao(publico: str) -> bool:
    return publico in ("gestante", "puerpera")


def texto_do_destino_por_sinal(destino: str, publico: str) -> str:
    """PRONTO_SOCORRO_AGORA e ATENDIMENTO_HOJE: orientacao fixa, sem caso."""
    tabela = _DESTINO_POR_SINAL_GESTACAO if _gestacao(publico) else _DESTINO_POR_SINAL
    return tabela[destino]


def seguranca(textos: _p.TextosDoEspecialista, publico: str, *, mental: bool = False) -> str:
    if mental:
        return textos.seguranca.get("saude_mental", textos.seguranca["padrao"])
    if publico in _p.PUBLICOS_DE_CRIANCA:
        return textos.seguranca.get("crianca", textos.seguranca["padrao"])
    if _gestacao(publico):
        return textos.seguranca.get("gestante", textos.seguranca["padrao"])
    return textos.seguranca["padrao"]


def _formatar(modelo: str, **valores: str) -> str:
    try:
        return modelo.format(**valores)
    except (KeyError, IndexError, ValueError):
        return modelo


def _leigo_e_mental(textos: _p.TextosDoEspecialista, nome: str) -> tuple[str | None, bool]:
    leigo = textos.nome_leigo(nome)
    return leigo, nome.strip().lower().startswith("psiquiatria")


def especialista_do_protocolo(protocolo: _p.Protocolo, textos: _p.TextosDoEspecialista) -> str:
    """A frase do especialista (modelos de `textos.yaml`) + como marcar. Sem prazo, sem link."""
    queixa = rotulo(protocolo)
    publico = protocolo.publico
    leigo, mental = _leigo_e_mental(textos, protocolo.especialista.depois)
    if publico in _p.PUBLICOS_DE_CRIANCA:
        frase = textos.modelos["crianca"]
    elif _gestacao(publico):
        frase = _formatar(textos.modelos["gestante"], queixa=queixa)
    elif mental and "saude_mental" in textos.modelos:
        frase = _formatar(textos.modelos["saude_mental"], queixa=queixa)
    elif leigo is None or leigo in ("o clínico geral", "o médico de família"):
        frase = textos.modelos["nao_sei"]
    elif protocolo.lacuna:
        frase = _formatar(textos.modelos["clinico_primeiro"], queixa=queixa, especialista=leigo)
    else:
        frase = _formatar(textos.modelos["padrao"], queixa=queixa, especialista=leigo)
    return f"{frase} {textos.como_marcar}"


def especialista_do_mapa(
    entrada: _p.EntradaDoMapa | None, publico: str, textos: _p.TextosDoEspecialista
) -> tuple[str, bool]:
    """A frase do especialista para uma entrada do mapa (ou `None` = nao sei) + como marcar; e se o assunto e'
    de saude mental (a frase de seguranca muda)."""
    if publico in _p.PUBLICOS_DE_CRIANCA:
        return f"{textos.modelos['crianca']} {textos.como_marcar}", False
    if entrada is None:
        return f"{textos.modelos['nao_sei']} {textos.como_marcar}", False
    queixa = entrada.queixa
    if _gestacao(publico):
        return f"{_formatar(textos.modelos['gestante'], queixa=queixa)} {textos.como_marcar}", False
    if (
        entrada.especialista_primeiro.nome_leigo.strip().lower() == "dentista"
        and "dentista" in textos.modelos
    ):
        return _formatar(textos.modelos["dentista"], queixa=queixa), False
    leigos: list[str] = []
    mental = False
    for esp in entrada.especialista_depois:
        if esp.cfm is None:
            continue
        leigo, eh_mental = _leigo_e_mental(textos, esp.cfm)
        mental = mental or eh_mental
        if leigo is not None and leigo not in leigos:
            leigos.append(leigo)
    if mental and "saude_mental" in textos.modelos:
        return f"{_formatar(textos.modelos['saude_mental'], queixa=queixa)} {textos.como_marcar}", True
    if not leigos:
        frase = textos.modelos["nao_sei"]
    elif entrada.lacuna is not False:
        frase = _formatar(textos.modelos["clinico_primeiro"], queixa=queixa, especialista=leigos[0])
    elif len(leigos) >= 2 and "dois_especialistas" in textos.modelos:
        frase = _formatar(
            textos.modelos["dois_especialistas"],
            queixa=queixa,
            especialista=leigos[0],
            especialista2=leigos[1],
        )
    else:
        frase = _formatar(textos.modelos["padrao"], queixa=queixa, especialista=leigos[0])
    return f"{frase} {textos.como_marcar}", False


def _corpo_e_seguranca(protocolo: _p.Protocolo, textos: _p.TextosDoEspecialista) -> tuple[str, str]:
    """A orientacao final do protocolo SEM a frase de seguranca do fim, e a frase de seguranca (a do proprio
    protocolo, quando ha'; senao a de `textos.yaml` para o publico)."""
    texto = protocolo.orientacao_final.strip()
    m = _SEGURANCA_NO_FIM.search(texto)
    if m is None:
        mental = protocolo.tema == "saude_mental"
        return texto, seguranca(textos, protocolo.publico, mental=mental)
    return texto[: m.start()].strip(), m.group(1).strip()


def texto_sem_sinal(protocolo: _p.Protocolo, textos: _p.TextosDoEspecialista) -> str:
    """Nenhum sinal: o destino sem sinal + a orientacao final + o especialista e os canais (consulta), ou a
    orientacao fixa do destino (atendimento hoje / pronto-socorro). Sem prazo da ANS."""
    corpo, fim = _corpo_e_seguranca(protocolo, textos)
    destino = protocolo.destino_se_nenhum_sinal
    if destino in _DESTINO_POR_SINAL:
        return f"{corpo}\n\n{texto_do_destino_por_sinal(destino, protocolo.publico)}"
    especialista = especialista_do_protocolo(protocolo, textos)
    return f"{PONTE_SEM_SINAL} {corpo}\n\n{especialista}\n\n{fim}"


def texto_de_seguimento(
    protocolo: _p.Protocolo, textos: _p.TextosDoEspecialista, *, reclamou: bool, agendamento: bool
) -> str:
    """A mesma queixa ja' respondida: nunca a pergunta de alerta de novo. Agendamento = o especialista e os
    canais; duvida sobre a causa = "quem pode avaliar e' o medico" + o especialista e os canais."""
    _, fim = _corpo_e_seguranca(protocolo, textos)
    partes = [TEXTO_JA_RESPONDIDO] if reclamou else []
    if not agendamento:
        partes.append(TEXTO_AVALIAR_A_CAUSA)
    partes.append(especialista_do_protocolo(protocolo, textos))
    return f"{' '.join(partes)}\n\n{fim}"


# --- Prompts e leitura estrita ------------------------------------------------------------------------------
def _json(bruto: str) -> Mapping[str, Any] | None:
    texto = (bruto or "").strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return None
    try:
        dado = json.loads(texto[inicio : fim + 1])
    except (ValueError, TypeError):
        return None
    return dado if isinstance(dado, Mapping) else None


def _grupo(protocolo: _p.Protocolo) -> str:
    if protocolo.publico in _p.PUBLICOS_DE_CRIANCA:
        return "crianca"
    return protocolo.publico


def _linha(ident: str, titulo: str, exemplos: Sequence[str]) -> str:
    ex = "; ".join(f'"{e}"' for e in list(exemplos)[:_EXEMPLOS_POR_PROTOCOLO])
    return f"- {ident}: {titulo} (ex.: {ex})"


def prompt_da_escolha(catalogo: _p.Catalogo, *, bloco_conversa: str, bloco_mensagem: str) -> str:
    """UMA chamada: o publico, a faixa da crianca e o protocolo (id da lista ou "nenhum"), pelo SENTIDO."""
    secoes: dict[str, list[str]] = {g: [] for g in PUBLICOS_DA_ESCOLHA}
    for p in catalogo.protocolos.values():
        faixa = (
            f" [{p.publico}]" if p.publico in _p.PUBLICOS_DE_CRIANCA and not p.id.startswith("uni-") else ""
        )
        secoes[_grupo(p)].append(_linha(p.id, f"{p.titulo}{faixa}", p.como_a_pessoa_fala))
    lista = "\n".join(f"[{g}]\n" + "\n".join(linhas) for g, linhas in secoes.items() if linhas)
    return "\n".join(
        [
            "Voce escolhe o PROTOCOLO de triagem de um assistente de saude no WhatsApp. Leia a CONVERSA e "
            "a MENSAGEM NOVA e escolha, PELO SENTIDO, o protocolo da queixa de saude ATUAL. Os exemplos de "
            "cada protocolo sao jeitos de falar, NAO uma lista de palavras exatas: 'dificuldades ao "
            "urinar' e 'arde quando faco xixi' sao a mesma queixa.",
            "Regras:",
            "1 publico: de quem e' a queixa: adulto (a propria pessoa ou outro adulto), crianca (filho, "
            "bebe, ate' 17 anos), gestante (gravida), puerpera (teve bebe ha' poucas semanas).",
            "2 faixa_crianca (so' crianca): 0_2m (ate' 2 meses), 2m_5a (2 meses a 5 anos), 5_12a (5 a 12 "
            "anos), adolescente (12 a 17), desconhecida (a idade nao foi dita); fora de crianca: "
            "nao_se_aplica.",
            "3 protocolo: o id da lista do publico (e da faixa) que corresponde a queixa; nenhum protocolo "
            'especifico corresponde: "nenhum". Nunca invente id.',
            "4 Gravidez tem prioridade: queixa de gestante usa os protocolos [gestante].",
            "5 Queda ou trauma junto com dor ('cai da escada e doem as costas', 'levei um tombo', 'bati o "
            "carro', 'torci o pe'') usa o protocolo de QUEDA/TRAUMA (adulto: tri-adulto-queda-ou-trauma; "
            "crianca que bateu a cabeca: o de queda ou batida na cabeca da faixa), nao o da parte do corpo.",
            'Responda SO\' um JSON numa linha: {"publico":"adulto|crianca|gestante|puerpera",'
            '"faixa_crianca":"0_2m|2m_5a|5_12a|adolescente|desconhecida|nao_se_aplica",'
            '"protocolo":"<id>|nenhum"}',
            "PROTOCOLOS:",
            lista,
            "CONVERSA:",
            bloco_conversa or "(vazia)",
            "MENSAGEM NOVA:",
            bloco_mensagem,
        ]
    )


@dataclass(frozen=True)
class Escolha:
    publico: str
    faixa: str
    protocolo: str | None


def ler_escolha(bruto: str, catalogo: _p.Catalogo) -> tuple[Escolha | None, list[str]]:
    """O JSON da escolha, ESTRITO: publico e faixa no vocabulario, protocolo da lista (e do publico)."""
    dado = _json(bruto)
    if dado is None:
        return None, ["(json)"]
    ruins: list[str] = []
    publico, faixa, pid = dado.get("publico"), dado.get("faixa_crianca"), dado.get("protocolo")
    if publico not in PUBLICOS_DA_ESCOLHA:
        ruins.append("publico")
    if faixa not in FAIXAS:
        ruins.append("faixa_crianca")
    if pid != "nenhum" and (
        not isinstance(pid, str)
        or pid not in catalogo.protocolos
        or (publico in PUBLICOS_DA_ESCOLHA and _grupo(catalogo.protocolos[pid]) != publico)
    ):
        ruins.append("protocolo")
    if ruins:
        return None, ruins
    return Escolha(str(publico), str(faixa), None if pid == "nenhum" else str(pid)), []


def publico_do_protocolo(escolha: Escolha) -> str:
    """O publico (do conjunto fechado dos protocolos) da escolha; crianca sem faixa -> `crianca_2m_5a` so'
    para escolher o alerta universal da crianca (que vale para qualquer idade)."""
    if escolha.publico == "crianca":
        return _PUBLICO_DA_FAIXA.get(escolha.faixa, "crianca_2m_5a")
    return escolha.publico


def precisa_da_idade(escolha: Escolha, catalogo: _p.Catalogo) -> bool:
    """Crianca com a idade nao dita e um protocolo de faixa especifica escolhido: a idade decide a faixa."""
    if escolha.publico != "crianca" or escolha.faixa != "desconhecida" or escolha.protocolo is None:
        return False
    return not escolha.protocolo.startswith("uni-") and catalogo.protocolos[escolha.protocolo].publico in (
        "crianca_0_2m",
        "crianca_2m_5a",
        "crianca_5_12a",
    )


def prompt_da_leitura(
    protocolo: _p.Protocolo, indices: Sequence[int], *, bloco_conversa: str, bloco_mensagem: str
) -> str:
    perguntas = "\n".join(
        f"{n}) {protocolo.perguntas_de_alerta[i - 1].pergunta}" for n, i in enumerate(indices, 1)
    )
    return "\n".join(
        [
            "Voce le' a resposta de uma pessoa as PERGUNTAS DE ALERTA que um assistente de saude acabou de "
            "fazer no WhatsApp. Para CADA pergunta, na ordem, diga o que a pessoa respondeu, no contexto:",
            '- "sim": a pessoa confirma que aquilo acontece AGORA;',
            '- "nao": nega ("nao", "nenhuma", "nada disso", "a 1 nao"), ou responde outra pergunta sem '
            "confirmar esta, ou so' repete/comenta a pergunta ('nao, mas o que isso tem a ver?' = nao);",
            '- "sem_clareza": responde mas nao da\' para saber ("talvez", "acho que sim", "mais ou '
            'menos", "nao sei").',
            "Uma resposta curta para todas ('nao', 'nenhuma', 'sim') vale para todas. 'A 2 sim' = sim so' "
            "na 2 e nao nas outras. Nunca invente sinal.",
            "outro_sinal_grave: true SO' se a pessoa diz um sinal de alarme ATUAL que NAO esta' nas "
            "perguntas (dor forte no peito, falta de ar forte, desmaio, convulsao, sangramento forte, fala "
            "enrolada).",
            f'Responda SO\' um JSON numa linha: {{"respostas":[{len(indices)} valores sim|nao|sem_clareza],'
            '"outro_sinal_grave":true|false}',
            "PERGUNTAS:",
            perguntas,
            "CONVERSA:",
            bloco_conversa or "(vazia)",
            "RESPOSTA DA PESSOA:",
            bloco_mensagem,
        ]
    )


@dataclass(frozen=True)
class Leitura:
    respostas: tuple[str, ...]
    outro_sinal_grave: bool


def ler_leitura(bruto: str, quantas: int) -> tuple[Leitura | None, list[str]]:
    dado = _json(bruto)
    if dado is None:
        return None, ["(json)"]
    respostas = dado.get("respostas")
    ruins: list[str] = []
    if (
        not isinstance(respostas, list)
        or len(respostas) != quantas
        or any(r not in RESPOSTAS for r in respostas)
    ):
        ruins.append("respostas")
    if not isinstance(dado.get("outro_sinal_grave"), bool):
        ruins.append("outro_sinal_grave")
    if ruins or not isinstance(respostas, list):
        return None, ruins
    return Leitura(tuple(str(r) for r in respostas), bool(dado["outro_sinal_grave"])), []


def prompt_do_mapa(catalogo: _p.Catalogo, *, bloco_conversa: str, bloco_mensagem: str) -> str:
    linhas = "\n".join(_linha(e.id, e.queixa, e.como_a_pessoa_fala) for e in catalogo.mapa.values())
    return "\n".join(
        [
            "Voce escolhe, PELO SENTIDO, a QUEIXA sobre a qual a pessoa pergunta qual medico/especialista "
            "procurar, num assistente de saude no WhatsApp. Os exemplos sao jeitos de falar, NAO palavras "
            "exatas. Use a CONVERSA quando a mensagem nova nao diz a queixa ('que medico procuro pra "
            "isso?').",
            "publico: de quem e' a queixa: adulto, crianca (ate' 17 anos), gestante, puerpera.",
            'Responda SO\' um JSON numa linha: {"queixa":"<id>|nenhum","publico":"adulto|crianca|gestante|'
            'puerpera"}',
            "QUEIXAS:",
            linhas,
            "CONVERSA:",
            bloco_conversa or "(vazia)",
            "MENSAGEM NOVA:",
            bloco_mensagem,
        ]
    )


def ler_mapa(bruto: str, catalogo: _p.Catalogo) -> tuple[tuple[str | None, str] | None, list[str]]:
    dado = _json(bruto)
    if dado is None:
        return None, ["(json)"]
    queixa, publico = dado.get("queixa"), dado.get("publico")
    ruins = [
        *(["queixa"] if queixa != "nenhum" and queixa not in catalogo.mapa else []),
        *(["publico"] if publico not in PUBLICOS_DA_ESCOLHA else []),
    ]
    if ruins:
        return None, ruins
    return (None if queixa == "nenhum" else str(queixa), str(publico)), []


def aviso_de_reenvio(ruins: Sequence[str]) -> str:
    """A UMA re-pergunta: so' os nomes dos campos (nunca o texto do modelo)."""
    return (
        f"ATENCAO: a resposta anterior veio invalida em {sorted(set(ruins))}; responda de novo SO' o JSON, "
        "so' com os valores permitidos."
    )


__all__ = [
    "PENDENTE_ALERTA",
    "PENDENTE_IDADE",
    "PREFIXO",
    "Decisao",
    "Escolha",
    "Leitura",
    "PendenteDoAlerta",
    "aviso_de_reenvio",
    "cabecalho",
    "codigo_do_alerta",
    "decidir",
    "especialista_do_mapa",
    "especialista_do_protocolo",
    "ler_escolha",
    "ler_leitura",
    "ler_mapa",
    "ler_pendente_do_alerta",
    "precisa_da_idade",
    "prompt_da_escolha",
    "prompt_da_leitura",
    "prompt_do_mapa",
    "protocolos_na_conversa",
    "publico_do_protocolo",
    "rotulo",
    "texto_das_perguntas",
    "texto_de_seguimento",
    "texto_do_destino_por_sinal",
    "texto_sem_sinal",
    "textos_fixos",
]
