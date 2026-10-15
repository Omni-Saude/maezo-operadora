"""NOVA TRIAGEM DA HELENA (DL-0104) — experimento em DEV atras da chave `MAEZO_HELENA_TRIAGEM_V2`.

Este modulo guarda SO' o que e' puro: o vocabulario fechado da tabela nova
(`spec/processes/dmn/helena_triage_admissibility.dmn`), os lexicos deterministicos que apuram a
NECESSIDADE do catalogo a partir do texto, e os textos fixos. Quem consulta a tabela, decide a rota e
monta o estado e' `graph.py` (`HelenaGraph._triagem_v2`). Nada aqui chama modelo, motor ou rede.

CHAVE DESLIGADA (o default) = a Helena de antes, byte a byte: nenhum simbolo deste modulo e' alcancado
por um turno com a chave desligada (`graph.py` so' entra aqui com `self._triagem_v2`).

FONTE DE TUDO: o catalogo `Catalogo_Necessidades_Helena_Lucas.xlsx` (14/10/2026) — aba "1 Necessidades"
(codigos S01..A05), aba "2 Tabela de decisao" (R01..R24), aba "4 Ficha minima" (perguntas por tipo de
queixa) e aba "5 Textos" (T01..T22). CONTEUDO CLINICO DRAFT — pendente de ratificacao do medico-auditor.

O LIMITE QUE NAO MUDA (L0, ADR-0005/0008/0012): nenhum texto daqui diagnostica, diz se e' grave, orienta
conduta ou indica remedio. Os textos mostram o CAMINHO (rede do plano, UBS, pronto-atendimento, 192) e
oferecem uma pessoa (RN 623/2024, art. 9, III).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, NamedTuple

#: A chave da decisao no motor — o `id` da `decision` em `helena_triage_admissibility.dmn`.
TRIAGEM_V2_DMN_KEY: Final[str] = "helena_triage_admissibility"

#: FICHA: ate' 3 rodadas de pergunta, SO' com a chave ligada (aba 4: "no maximo 3-4 perguntas"; a
#: decisao D02 do catalogo segue pendente). Com a chave desligada continua `COLETA_MAX_RODADAS = 2`.
#: A tabela nova repete o limite (`rodadas < 3` / `>= 3`); `test_helena_triagem_v2_dmn.py` confere os dois.
COLETA_MAX_RODADAS_TRIAGEM_V2: Final[int] = 3

#: O `coleta_veredito` de um turno de PERGUNTA da nova triagem: e' a DECISAO da tabela (`PERGUNTAR_MAIS`),
#: nao um veredito da `triage_sufficiency`. O backstop de `_route` aceita a rota `collect` com ele (e so'
#: dentro do limite de 3 rodadas); com a chave desligada nenhum turno o emite.
COLETA_VEREDITO_PERGUNTAR_MAIS: Final[str] = "PERGUNTAR_MAIS"
#: Prefixo da PERGUNTA DA FICHA POR TIPO DE QUEIXA (aba 4) em `coleta_pergunta`. As perguntas da
#: `triage_sufficiency` (PERGUNTAR_INTENSIDADE etc.) continuam com o nome de sempre.
PREFIXO_PERGUNTA_DA_FICHA: Final[str] = "PERGUNTAR_FICHA_"

# --- Vocabulario FECHADO da tabela -----------------------------------------------------------------
DECISAO_ABRIR_CASO_EMERGENCIA: Final[str] = "ABRIR_CASO_EMERGENCIA"
DECISAO_ABRIR_CASO: Final[str] = "ABRIR_CASO"
DECISAO_PERGUNTAR_MAIS: Final[str] = "PERGUNTAR_MAIS"
DECISAO_ESCLARECER: Final[str] = "ESCLARECER_NECESSIDADE"
DECISAO_ORIENTAR: Final[str] = "ORIENTAR_CAMINHO_SEM_CASO"
DECISAO_RESPONDER: Final[str] = "RESPONDER_SEM_CASO"
DECISAO_PASSAR_AO_LUCAS: Final[str] = "PASSAR_AO_LUCAS"
DECISOES: Final[frozenset[str]] = frozenset(
    {
        DECISAO_ABRIR_CASO_EMERGENCIA,
        DECISAO_ABRIR_CASO,
        DECISAO_PERGUNTAR_MAIS,
        DECISAO_ESCLARECER,
        DECISAO_ORIENTAR,
        DECISAO_RESPONDER,
        DECISAO_PASSAR_AO_LUCAS,
    }
)
DECISOES_QUE_ABREM_CASO: Final[frozenset[str]] = frozenset(
    {DECISAO_ABRIR_CASO_EMERGENCIA, DECISAO_ABRIR_CASO}
)
DECISOES_COM_TEXTO: Final[frozenset[str]] = frozenset(
    {DECISAO_ESCLARECER, DECISAO_ORIENTAR, DECISAO_RESPONDER}
)
#: Os `motivo_categoria` que a Helena pode abrir (o `MotivoCategoria` de `graph.py`, subconjunto do enum
#: do contrato SP-OP-ESCALATION-001: `cobranca` e `alerta_sla` sao de outros produtores).
MOTIVOS_DA_HELENA: Final[frozenset[str]] = frozenset(
    {
        "red_flag_clinico",
        "risco_psicossocial",
        "intencao_clinica",
        "solicitacao_humano",
        "falha_tecnica",
        "outro",
    }
)
SEVERIDADES: Final[frozenset[str]] = frozenset({"grave", "moderada", "leve"})
#: "-" = a coluna nao se aplica (a linha nao abre caso / o texto vem de outro lugar).
NAO_SE_APLICA: Final[str] = "-"

# --- Necessidades (aba 1 do catalogo) ----------------------------------------------------------------
NECESSIDADE_SINTOMA: Final[str] = "S01"
NECESSIDADE_SINTOMA_GESTANTE: Final[str] = "S04"
NECESSIDADE_SINTOMA_CRIANCA: Final[str] = "S05"
NECESSIDADE_CONDUTA: Final[str] = "S06"
NECESSIDADE_EXAME: Final[str] = "S08"
NECESSIDADE_RECEITA: Final[str] = "S09"
NECESSIDADE_NAVEGACAO: Final[str] = "S10"
NECESSIDADE_NAO_CLARA: Final[str] = "A05"
NECESSIDADE_COBRANCA: Final[str] = "COBRANCA"
NECESSIDADES_DE_SINTOMA: Final[frozenset[str]] = frozenset(
    {NECESSIDADE_SINTOMA, NECESSIDADE_SINTOMA_GESTANTE, NECESSIDADE_SINTOMA_CRIANCA}
)
NECESSIDADES: Final[frozenset[str]] = NECESSIDADES_DE_SINTOMA | frozenset(
    {
        NECESSIDADE_CONDUTA,
        NECESSIDADE_EXAME,
        NECESSIDADE_RECEITA,
        NECESSIDADE_NAVEGACAO,
        NECESSIDADE_NAO_CLARA,
        NECESSIDADE_COBRANCA,
    }
)

# --- Textos (aba "5 Textos") -----------------------------------------------------------------------
#: T05 — orientar o caminho depois da ficha, sem alarme (R13/R14).
#: RASCUNHO — pendente de aprovacao do dono (D14). Adaptado do T05 do catalogo so' para caber na regua de
#: CLAREZA (frase <= 20 palavras, DL-0087) e na cerca de canal (nomes confirmados por inteiro). Prazo da
#: ANS: SO' o valor conferido no texto-base da RN 566/2022 (art. 3, I: consulta basica — pediatria, clinica
#: medica, cirurgia geral, ginecologia e obstetricia — em 7 dias uteis).
#: 15/10/2026 (aceite em dev): ENXUTO (~10 frases era longo demais; 6 frases sem perder nada — nao avalia;
#: consulta na rede pelo aplicativo/portal/central; prazo da ANS; UBS; piorou -> pronto-atendimento;
#: emergencia -> 192; oferta de pessoa) e POR POPULACAO: adulto -> clinico geral; crianca -> pediatra;
#: gestante -> obstetra ou ginecologista (`texto_de_orientacao`).
RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO: Final[str] = (
    "Eu não avalio sintomas, mas posso te mostrar o caminho. "
    "Para marcar consulta na rede, use o aplicativo Austa Clínicas, o portal "
    "ou a central de atendimento do plano. "
    "Pelas regras da ANS, a consulta com clínico geral deve ser oferecida em até 7 dias úteis. "
    "A UBS, pelo SUS, também atende. "
    "Se piorar, procure um pronto-atendimento, e se for emergência ligue agora para o 192 (SAMU). "
    "Se quiser falar com uma pessoa da equipe, é só me pedir."
)
RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA: Final[str] = (
    "Eu não avalio sintomas, mas posso te mostrar o caminho. "
    "Para marcar consulta na rede, use o aplicativo Austa Clínicas, o portal "
    "ou a central de atendimento do plano. "
    "Pelas regras da ANS, a consulta com pediatra deve ser oferecida em até 7 dias úteis. "
    "A UBS, pelo SUS, também atende. "
    "Se piorar, procure um pronto-atendimento, e se for emergência ligue agora para o 192 (SAMU). "
    "Se quiser falar com uma pessoa da equipe, é só me pedir."
)
RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_GESTANTE: Final[str] = (
    "Eu não avalio sintomas, mas posso te mostrar o caminho. "
    "Para marcar consulta na rede, use o aplicativo Austa Clínicas, o portal "
    "ou a central de atendimento do plano. "
    "Pelas regras da ANS, a consulta com obstetra ou ginecologista deve ser oferecida em até 7 dias úteis. "
    "A UBS, pelo SUS, também atende. "
    "Se piorar, procure um pronto-atendimento, e se for emergência ligue agora para o 192 (SAMU). "
    "Se quiser falar com uma pessoa da equipe, é só me pedir."
)
#: BEBE COM MENOS DE 3 MESES (inclusive recem-nascido) SEM alarme ao fim da ficha: NAO manda marcar
#: consulta em 7 dias uteis — oferece EXPLICITAMENTE falar agora com a equipe de saude (sem abrir caso
#: automaticamente) e mantem pronto-atendimento/192 se piorar.
#: RASCUNHO — DECISAO CLINICA PENDENTE (medico-auditor): o corte de 3 meses e a conduta "oferecer a equipe
#: agora em vez do prazo da ANS" sao do pedido do dono (15/10/2026), sem outro criterio clinico. Ate' a
#: ratificacao, nada alem disto muda para essa faixa.
RESPOSTA_TRIAGEM_BEBE_PEQUENO: Final[str] = (
    "Obrigada pelas respostas. Eu não avalio sintomas. "
    "O bebê ainda é bem pequeno. "
    "Se quiser falar agora com uma pessoa da nossa equipe de saúde, é só me pedir. "
    "Se piorar, procure um pronto-atendimento. Se for emergência, ligue agora para o 192 (SAMU)."
)
#: T08 — recusar remedio / "o que eu tenho?" (R15). RASCUNHO — pendente de aprovacao do dono (D01/D14).
#: Variacao do T07 (`RESPOSTA_DUVIDA_CLINICA`, JA EXISTE): diz que nao diagnostica nem indica remedio,
#: mostra o caminho e oferece uma pessoa.
RESPOSTA_TRIAGEM_SEM_CONDUTA: Final[str] = (
    "Não consigo dizer o que você tem nem qual remédio tomar: isso é feito por um médico, numa consulta. "
    "Você pode marcar consulta na rede do plano. "
    "Use o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento do plano. "
    "A UBS, pelo SUS, também atende. Se for uma emergência, ligue agora para o 192 (SAMU). "
    "Se quiser falar com uma pessoa da nossa equipe de saúde, é só me pedir."
)
#: T09 — resultado de exame / receita / atestado / laudo (R16). RASCUNHO — pendente de aprovacao do dono.
RESPOSTA_TRIAGEM_EXAME_RECEITA: Final[str] = (
    "Quem interpreta resultado de exame e faz receita ou atestado é o médico, numa consulta. "
    "Leve o resultado ao médico que pediu o exame, ou marque consulta na rede do plano. "
    "Use o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento do plano. "
    "Se quiser falar com uma pessoa da equipe, é só me pedir."
)
#: T11 — pergunta de esclarecimento com opcoes (R23). RASCUNHO — pendente de aprovacao do dono (D09).
#: Em lista (uma opcao por linha) para caber na regua de CLAREZA.
RESPOSTA_TRIAGEM_ESCLARECER: Final[str] = (
    "Para te ajudar melhor, sua mensagem é sobre:\n"
    "1) um sintoma ou dúvida de saúde.\n"
    "2) o plano (cobertura, carteirinha, autorização).\n"
    "3) mensalidade ou boleto.\n"
    "4) falar com uma pessoa.\n"
    "Pode responder com o número."
)
#: Catch-all (R24 com a divergencia do experimento): oferecer uma pessoa, sem abrir caso.
#: RASCUNHO — pendente de aprovacao do dono (D08). Sem ID no catalogo (`OFERTA_PESSOA` na tabela).
RESPOSTA_TRIAGEM_OFERTA_PESSOA: Final[str] = (
    "Ainda não entendi o que você precisa. Se quiser falar com uma pessoa da equipe, é só me pedir. "
    "Se for uma emergência, ligue agora para o 192 (SAMU) ou vá ao pronto-socorro mais próximo."
)
#: Resposta a opcao 2 do T11. RASCUNHO — pendente de aprovacao do dono (D09). A opcao 1 reaproveita
#: `RESPOSTA_DUVIDA_DE_SAUDE` (JA EXISTE), a 3 passa ao Lucas (handoff de cobranca de sempre; sem o
#: roteador, `RESPOSTA_FORA_DO_CANAL`) e a 4 e' pedido de pessoa (abre `solicitacao_humano`).
RESPOSTA_TRIAGEM_OPCAO_PLANO: Final[str] = (
    "Certo, é sobre o plano. Me conte o que você quer saber: cobertura, carência, carteirinha ou autorização."
)

# --- A ficha minima (aba "4 Ficha minima") ---------------------------------------------------------
#: O que cada pergunta da ficha quer saber. A ficha NAO pergunta o que a pessoa ja' disse (aceite em dev,
#: 15/10/2026: "Estou com dor nas costas" -> "Em que parte do corpo e' a dor?"): um item cujo assunto ja'
#: foi informado (pela extracao do classificador, pela memoria clinica ou pelo lexico abaixo) sai da
#: pergunta. `ITEM_SINAL` (sinal de alarme especifico da queixa) so' sai quando a pessoa FALOU daquele
#: assunto (`_RESPOSTA_DO_SINAL`); senao e' perguntado — e, perguntado sem resposta, volta na rodada
#: seguinte (aceite em dev, 15/10/2026: a ficha concluiu "suficiente" sem saber se o bebe mamava).
ITEM_LOCAL: Final[str] = "LOCAL"
ITEM_INTENSIDADE: Final[str] = "INTENSIDADE"
ITEM_INICIO: Final[str] = "INICIO"
ITEM_IDADE: Final[str] = "IDADE"
ITEM_SEMANAS: Final[str] = "SEMANAS"
ITEM_FEBRE: Final[str] = "FEBRE"
ITEM_SINAL: Final[str] = "SINAL"

#: A ficha minima POR TIPO DE QUEIXA (aba "4 Ficha minima", coluna "Exemplo de pergunta (RASCUNHO)"), um
#: item por frase. RASCUNHO — pendente de aprovacao do dono e do medico-auditor (D02). UMA mensagem com as
#: perguntas AINDA NAO RESPONDIDAS da queixa, enviada como TEXTO FIXO (sem modelo). Redigidas SEM sujeito
#: ("Está com falta de ar?") porque o sintoma pode ser de outra pessoa ("meu filho"); a da crianca troca
#: "o bebe" por "a crianca" pelo mesmo motivo. Cada frase se entende SOZINHA (pode sair sem as vizinhas).
#: Nenhuma orienta conduta: so' perguntam o que alimenta as tabelas de alarme.
ITENS_DA_FICHA: Final[Mapping[str, tuple[tuple[str, str], ...]]] = {
    "DOR": (
        (ITEM_LOCAL, "Em que parte do corpo é a dor?"),
        (ITEM_INTENSIDADE, "A dor está fraca, média ou forte?"),
        (ITEM_INICIO, "Começou de repente?"),
    ),
    "FEBRE": (
        (ITEM_IDADE, "Quem está com febre e qual a idade? Se for bebê, diga em meses."),
        (ITEM_SINAL, "Apareceram manchinhas vermelhas na pele?"),
    ),
    "RESPIRATORIO": (
        (ITEM_SINAL, "Está com falta de ar?"),
        (ITEM_SINAL, "Consegue falar uma frase inteira sem parar para respirar?"),
        (ITEM_FEBRE, "Tem febre?"),
    ),
    "PELE": (
        (ITEM_SINAL, "Inchou o rosto, a boca ou a língua?"),
        (ITEM_SINAL, "Está difícil respirar?"),
        (ITEM_SINAL, "As manchas vieram junto com febre?"),
    ),
    "GASTRO": (
        (ITEM_SINAL, "Tem sangue no vômito ou nas fezes, ou as fezes estão pretas?"),
        (ITEM_SINAL, "Está conseguindo beber água e fazer xixi?"),
    ),
    "URINARIO": (
        (ITEM_FEBRE, "Tem febre?"),
        (ITEM_INTENSIDADE, "A dor é forte?"),
        (ITEM_SINAL, "Apareceu sangue no xixi?"),
    ),
    "MENTAL": (
        (ITEM_SINAL, "Quer me contar um pouco do que está sentindo?"),
        (ITEM_INICIO, "Desde quando está assim?"),
    ),
    "GESTANTE": (
        (ITEM_SEMANAS, "De quantas semanas é a gestação?"),
        (ITEM_SINAL, "Está tendo sangramento, perda de líquido ou o bebê está mexendo menos?"),
    ),
    "CRIANCA": (
        (ITEM_IDADE, "Qual é a idade da criança? Se for bebê, diga em meses ou dias."),
        (ITEM_SINAL, "Está mamando ou comendo e fazendo xixi como de costume?"),
        (ITEM_SINAL, "Está muito mole ou sonolenta?"),
    ),
}
#: A ficha INTEIRA de cada queixa (todas as frases) — o texto da primeira rodada quando nada foi dito.
PERGUNTAS_DA_FICHA: Final[Mapping[str, str]] = {
    grupo: " ".join(frase for _, frase in itens) for grupo, itens in ITENS_DA_FICHA.items()
}
#: O PACIENTE como a pessoa disse ("minha filha" -> "sua filha"), para a ficha da crianca nao falar de "a
#: crianca" generica (aceite do dono_v1, 15/10/2026). RASCUNHO, como as frases de `ITENS_DA_FICHA`; a
#: mesma ordem de itens (idade, alimentacao/xixi, mole/sonolenta).
PACIENTE_FILHA: Final[str] = "filha"
PACIENTE_FILHO: Final[str] = "filho"
PACIENTE_BEBE: Final[str] = "bebe"
FICHA_DA_CRIANCA_POR_PACIENTE: Final[Mapping[str, tuple[str, str, str]]] = {
    PACIENTE_FILHA: (
        "Qual é a idade da sua filha? Se for bebê, diga em meses ou dias.",
        "Sua filha está mamando ou comendo e fazendo xixi como de costume?",
        "Ela está muito mole ou sonolenta?",
    ),
    PACIENTE_FILHO: (
        "Qual é a idade do seu filho? Se for bebê, diga em meses ou dias.",
        "Seu filho está mamando ou comendo e fazendo xixi como de costume?",
        "Ele está muito mole ou sonolento?",
    ),
    PACIENTE_BEBE: (
        "Quantos dias ou meses tem o bebê?",
        "O bebê está mamando e fazendo xixi como de costume?",
        "Ele está muito mole ou sonolento?",
    ),
}
#: A volta de um item perguntado e NAO respondido (rodada seguinte): curta, so' o que faltou.
ABERTURA_DE_NOVO: Final[str] = "Só para eu entender:"

# --- Lexicos deterministicos (sobre texto JA NORMALIZADO: sem acento, minusculo, sem pontuacao) ---------
#: Relato de SINTOMA numa pergunta clinica ("o que pode ser essa dor ao urinar?"): com ele, a pergunta e'
#: tratada como sintoma (ficha), nunca como pedido de conduta sem sintoma. "urina" sozinha fica de fora de
#: proposito: "o resultado do exame de urina" e' S08, nao sintoma.
_RELATO_DE_SINTOMA = re.compile(
    r"\b(?:dor|dores|doi|doendo|doer|febre|tosse|tossindo|vomit\w*|diarreia|enjoo|enjoad\w*|nausea\w*"
    r"|coceira|cocando|manchas?|bolinh\w+|sangr\w*|ardencia|arde|ardendo|queimacao|inchad\w*|inchaco"
    r"|tontura|tont[oa]|falta de ar|chiado|catarro|coriza|mal estar|passando mal|machuc\w*|ferid[oa]s?"
    r"|alergi\w*|irritacao|colica|desmai\w*|formigamento|dormencia)\b"
)
_RESULTADO_DE_EXAME = re.compile(
    r"\b(?:resultados?|meu exame|meus exames|o exame (?:deu|saiu|veio)"
    r"|exames? (?:deu|deram|saiu|sairam|veio))\b"
)
_RECEITA_ATESTADO = re.compile(r"\b(?:receitas?|atestados?|laudos?|receituario)\b")
#: NECESSIDADE NAO CLARA (A05): a mensagem inteira e' feita destas palavras E tem pelo menos uma palavra de
#: necessidade ("preciso de ajuda", "tenho um problema"). Vocabulario FECHADO como o de
#: `_conversa_sem_relato_clinico`: nenhuma palavra de corpo, idade, remedio, numero ou cobranca cabe nele,
#: entao um sintoma nunca vira "nao clara" por aqui.
_PALAVRAS_DE_NECESSIDADE: Final[frozenset[str]] = frozenset(
    {"ajuda", "ajudar", "ajude", "problema", "problemas", "coisa", "coisas", "aquilo", "resolver", "situacao"}
)
_VOCABULARIO_VAGO: Final[frozenset[str]] = _PALAVRAS_DE_NECESSIDADE | frozenset(
    {
        *("eu", "me", "mim", "minha", "meu", "voce", "vc", "a", "o", "um", "uma", "uns", "umas"),
        *("de", "da", "do", "com", "para", "pra", "por", "e", "ou", "que", "so", "isso", "ai", "la"),
        *("oi", "ola", "opa", "bom", "boa", "dia", "tarde", "noite", "helena", "agora", "favor"),
        *("quero", "queria", "gostaria", "preciso", "precisava", "tenho", "tem", "pode", "poderia"),
        *("consegue", "sei", "nao", "outra", "outro", "algum", "alguma", "uma", "esse", "essa"),
    }
)
_MAX_PALAVRAS_VAGAS: Final[int] = 12
#: Resposta a pergunta de esclarecimento (T11): o NUMERO da opcao ("4", "4)", "opcao 4", "a 4", "quatro",
#: "4 falar com uma pessoa") ou o ROTULO dela inteiro ("falar com uma pessoa", "boleto"). So' a mensagem
#: INTEIRA conta: "4 dias de febre" nao e' opcao.
_PREFIXO_DA_OPCAO = re.compile(r"^(?:(?:e|eh)\s+)?(?:(?:a|o)\s+)?(?:(?:opcao|numero|n|item)\s+)?")
_CORTESIA_NO_FIM = re.compile(r"\s+(?:por favor|pf|pfv|pfvr|obrigad[oa])$")
_OPCOES_POR_EXTENSO: Final[Mapping[str, str]] = {
    **dict.fromkeys(("um", "uma", "primeira", "primeiro"), "1"),
    **dict.fromkeys(("dois", "duas", "segunda", "segundo"), "2"),
    **dict.fromkeys(("tres", "terceira", "terceiro"), "3"),
    **dict.fromkeys(("quatro", "quarta", "quarto"), "4"),
}
_ROTULOS_DAS_OPCOES: Final[Mapping[str, frozenset[str]]] = {
    "1": frozenset(
        {
            "sintoma",
            "um sintoma",
            "duvida de saude",
            "uma duvida de saude",
            "sintoma ou duvida de saude",
            "um sintoma ou duvida de saude",
        }
    ),
    "2": frozenset({"plano", "o plano", "sobre o plano", "o plano cobertura carteirinha autorizacao"}),
    "3": frozenset({"mensalidade", "boleto", "mensalidade ou boleto", "a mensalidade", "o boleto"}),
    "4": frozenset(
        {"pessoa", "uma pessoa", "falar com uma pessoa", "falar com pessoa", "quero falar com uma pessoa"}
    ),
}

# --- O que a pessoa JA' disse (para a ficha nao repetir a pergunta) ---------------------------------
#: LOCAL: parte do corpo nomeada ("dor nas costas", "dor de cabeca").
_LOCAL_DO_CORPO = re.compile(
    r"\b(?:cabeca|costas|lombar|coluna|nuca|pescoco|ombros?|bracos?|cotovelos?|punhos?|maos?|dedos?"
    r"|peito|torax|costelas?|barriga|abdomen|abdominal|estomago|quadril|virilha|pernas?|joelhos?"
    r"|tornozelos?|calcanhar|garganta|ouvidos?|dentes?|olhos?|rosto|boca|mandibula|seios?|mamas?"
    r"|no pe|nos pes|pe (?:direito|esquerdo)|lado (?:direito|esquerdo))\b"
)
#: Codigos da allowlist que ja' dizem ONDE e' a dor.
_CODIGOS_COM_LOCAL: Final[frozenset[str]] = frozenset(
    {"dor_toracica", "dor_abdominal", "cefaleia_subita_intensa"}
)
_INTENSIDADE_DITA = re.compile(
    r"\b(?:leve|levinha|media|medio|moderad[ao]|forte|fortes|fortissim[ao]|intens[ao]|insuportavel"
    r"|horrivel|muita dor|pouca dor|(?:dor|e|esta|ta) fraca)\b"
)
_INICIO_DITO = re.compile(
    r"\b(?:de repente|repentin\w*|subit\w*|do nada|comecou|comecaram|desde|ontem|anteontem|hoje|hj"
    r"|(?:ha|faz) (?:\d+|um|uma|dois|duas|tres|alguns|algumas|pouco|muito|mais de)"
    r"|(?:essa|esta|na) semana|semana passada|mes passado|agora pouco|agorinha)\b"
)
_NUMERO = r"(?:\d{1,3}|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze)"
#: IDADE: "de 6 anos", "tem 8 meses", "6 aninhos", "5 dias de vida", "recem nascido". NUNCA a DURACAO
#: ("ha 2 meses", "faz 3 anos", "mais de 2 meses"): sem idade a ficha pergunta (lado seguro).
_IDADE_DITA = re.compile(
    rf"(?<!\bha )(?<!\bfaz )(?<!\bmais de )(?<!\bmenos de )(?<!\bcerca de )(?<!\buns )(?<!\bumas )"
    rf"\b{_NUMERO} (?:anos?|aninhos?|mes(?:es)?)\b"
    rf"|\b{_NUMERO} dias? de (?:vida|nascid\w*)\b|\brecem nascid\w*\b|\bneonat\w*\b|\bnasceu\b"
    rf"|\bbebe (?:de|com) {_NUMERO} (?:dias?|semanas?)\b"
)
#: BEBE COM MENOS DE 3 MESES pelo texto (o numero de meses, quando ha', vem da extracao: `idade_meses`).
_BEBE_PEQUENO = re.compile(
    rf"\brecem nascid\w*\b|\bneonat\w*\b|\bnasceu\b|\b{_NUMERO} (?:dias?|semanas?) de vida\b"
    rf"|\bbebe (?:de|com) {_NUMERO} (?:dias?|semanas?)\b"
    r"|\b(?:de|tem|com) (?:1|2|um|dois) mes(?:es)?\b|\bmenos de (?:um|1|dois|2|tres|3) mes"
)
#: Quem e' o paciente ("minha filha" / "meu filho"; senao "bebe"/"recem nascido").
_PACIENTE_FILHO_OU_FILHA = re.compile(r"\b(?:minha|meu) (filha|filho)\b")
_PACIENTE_BEBE = re.compile(r"\b(?:bebe|recem nascid\w*|nenem)\b")
#: Resposta CURTA que responde tudo o que foi perguntado ("nao", "sim, normal", "tudo bem").
_RESPOSTA_CURTA = re.compile(
    r"^(?:(?:sim|s|nao|n|normal|normais|normalmente|tudo|bem|ok|isso|certo|nada|nenhum|nenhuma|ta|esta"
    r"|tambem|nunca|direitinho)(?:\s+|$))+$"
)
#: O assunto de cada SINAL da ficha, `(grupo, indice)`: se a pessoa falou dele, o item conta como respondido.
_RESPOSTA_DO_SINAL: Final[Mapping[tuple[str, int], re.Pattern[str]]] = {
    ("FEBRE", 2): re.compile(r"\b(?:manch\w*|pintinh\w*|pele)\b"),
    ("RESPIRATORIO", 1): re.compile(r"\b(?:falta de ar|folego|respir\w*|cansad\w*|chiado)\b"),
    ("RESPIRATORIO", 2): re.compile(r"\b(?:fala\w*|frase|respir\w*|folego)\b"),
    ("PELE", 1): re.compile(r"\b(?:inch\w*|rosto|boca|lingua|labios?|olhos?)\b"),
    ("PELE", 2): re.compile(r"\b(?:respir\w*|falta de ar|folego)\b"),
    ("PELE", 3): re.compile(r"\b(?:febre|febril|temperatura)\b"),
    ("GASTRO", 1): re.compile(r"\b(?:sangue|sangr\w*|fezes|pret[ao]s?|coco)\b"),
    ("GASTRO", 2): re.compile(r"\b(?:agua|beb\w*|xixi|urin\w*|hidrat\w*|liquidos?)\b"),
    ("URINARIO", 3): re.compile(r"\b(?:sangue|sangr\w*|vermelh\w*|escur\w*)\b"),
    ("MENTAL", 1): re.compile(
        r"\b(?:sinto|sentindo|trist\w*|ansios\w*|ansiedade|angusti\w*|medo|nervos\w*|deprimid\w*|chor\w*)\b"
    ),
    ("GESTANTE", 2): re.compile(r"\b(?:sangr\w*|sangue|liquido|perd\w*|mex\w*|bolsa)\b"),
    ("CRIANCA", 2): re.compile(
        r"\b(?:mam(?:a|ando|ou|ar|adas?|adeira)|peito|comendo|comeu|come|xixi|fraldas?|alimenta\w*)\b"
    ),
    ("CRIANCA", 3): re.compile(
        r"\b(?:mol[ei]\w*|sonolent\w*|prostrad\w*|dormindo|dorme|ativ[oa]|espert[oa]|brincando|acordad[oa])\b"
    ),
}
_SEMANAS_DITAS = re.compile(rf"(?<!\bha )(?<!\bfaz )\b{_NUMERO} semanas\b")
_FEBRE_DITA = re.compile(r"\b(?:febre|febril|temperatura)\b")

#: Tipo de queixa pelo CODIGO da allowlist (quando ha' um) — so' os que a ficha usa.
_GRUPO_DO_CODIGO: Final[Mapping[str, str]] = {
    "febre": "FEBRE",
    "dispneia": "RESPIRATORIO",
    "dificuldade_respiratoria": "RESPIRATORIO",
    "reacao_alergica": "PELE",
    "dor_abdominal": "GASTRO",
    "sinais_desidratacao": "GASTRO",
    "vomitos_persistentes": "GASTRO",
}
#: Tipo de queixa pelo TEXTO, na ORDEM: o especifico antes de "dor" ("dor ao urinar" e' urinario).
_GRUPOS_PELO_TEXTO: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("URINARIO", re.compile(r"\b(?:urin\w*|xixi|bexiga)\b")),
    (
        "RESPIRATORIO",
        re.compile(
            r"\b(?:tosse|tossindo|falta de ar|respir\w*|chiado|catarro|coriza|gripe|gripad[oa]|resfriad\w*"
            r"|garganta)\b"
        ),
    ),
    ("PELE", re.compile(r"\b(?:coceira|cocando|manchas?|bolinh\w+|pele|alergi\w*|urticaria|inchad\w*)\b")),
    (
        "GASTRO",
        re.compile(
            r"\b(?:vomit\w*|diarreia|enjoo|enjoad\w*|nausea\w*|barriga|estomago|fezes|intestin\w*|colica)\b"
        ),
    ),
    ("FEBRE", re.compile(r"\b(?:febre|febril)\b")),
    ("DOR", re.compile(r"\b(?:dor|dores|doi|doendo)\b")),
)
_GRUPO_DA_POPULACAO: Final[Mapping[str, str]] = {
    "gestante": "GESTANTE",
    "pediatric": "CRIANCA",
    "mental_health": "MENTAL",
}


def relata_sintoma(plano: str, intensidade: object = None) -> bool:
    """A mensagem (normalizada) RELATA um sintoma? Uma intensidade dita pelo modelo tambem conta."""
    if isinstance(intensidade, str) and intensidade in SEVERIDADES:
        return True
    return bool(_RELATO_DE_SINTOMA.search(plano))


def necessidade_do_sintoma(population: object) -> str:
    """S04 (gestante), S05 (crianca) ou S01 — o quadro ja' passou pela tabela de alarme da populacao."""
    if population == "gestante":
        return NECESSIDADE_SINTOMA_GESTANTE
    if population == "pediatric":
        return NECESSIDADE_SINTOMA_CRIANCA
    return NECESSIDADE_SINTOMA


def necessidade_da_pergunta_clinica(plano: str, *, navegacao: bool) -> str:
    """S09 (receita/atestado/laudo), S08 (resultado de exame), S10 (onde ir) ou S06 (conduta/diagnostico).

    So' para pergunta clinica SEM sintoma relatado. S07 (duvida geral) cai em S06 ate' a decisao D10.
    """
    if _RECEITA_ATESTADO.search(plano):
        return NECESSIDADE_RECEITA
    if _RESULTADO_DE_EXAME.search(plano):
        return NECESSIDADE_EXAME
    if navegacao:
        return NECESSIDADE_NAVEGACAO
    return NECESSIDADE_CONDUTA


def necessidade_vaga(plano: str) -> bool:
    """A05: a mensagem inteira e' vaga ("preciso de ajuda", "oi, tenho um problema")."""
    palavras = plano.split()
    if not palavras or len(palavras) > _MAX_PALAVRAS_VAGAS:
        return False
    return all(p in _VOCABULARIO_VAGO for p in palavras) and any(
        p in _PALAVRAS_DE_NECESSIDADE for p in palavras
    )


def resposta_numerada(plano: str) -> str | None:
    """ "1".."4" quando a mensagem (normalizada) e' SO' a escolha de uma opcao do T11 — o numero ("4",
    "opcao 4", "a 4", "quatro"), o numero com o rotulo dele ("4 falar com uma pessoa") ou o rotulo inteiro
    ("falar com uma pessoa"); senao `None`. Quem decide se HOUVE oferta recente e' `graph.py`."""
    texto = _CORTESIA_NO_FIM.sub("", " ".join(plano.split()))
    resto = _PREFIXO_DA_OPCAO.sub("", texto, count=1)
    primeira, _, cauda = resto.partition(" ")
    numero = primeira if primeira in _ROTULOS_DAS_OPCOES else _OPCOES_POR_EXTENSO.get(primeira)
    if numero is not None and (not cauda or cauda in _ROTULOS_DAS_OPCOES[numero]):
        return numero
    for opcao, rotulos in _ROTULOS_DAS_OPCOES.items():
        if texto in rotulos:
            return opcao
    return None


def itens_ja_respondidos(plano: str, extracao: Mapping[str, object]) -> frozenset[str]:
    """Os itens da ficha que a pessoa JA' informou nesta conversa: pela EXTRACAO do classificador (que ja'
    traz a memoria clinica fundida — `sintoma_codigo`, `intensidade`, idades) ou pelo LEXICO sobre o texto
    normalizado acumulado da ficha (`plano`). `ITEM_SINAL` nunca entra (so' a pergunta direta o apura)."""
    respondidos: set[str] = set()
    codigo = extracao.get("sintoma_codigo")
    if _LOCAL_DO_CORPO.search(plano) or (isinstance(codigo, str) and codigo in _CODIGOS_COM_LOCAL):
        respondidos.add(ITEM_LOCAL)
    intensidade = extracao.get("intensidade")
    if (isinstance(intensidade, str) and intensidade in SEVERIDADES) or _INTENSIDADE_DITA.search(plano):
        respondidos.add(ITEM_INTENSIDADE)
    if _INICIO_DITO.search(plano):
        respondidos.add(ITEM_INICIO)

    def _tem(chave: str) -> bool:
        valor = extracao.get(chave)
        return isinstance(valor, int) and not isinstance(valor, bool)

    if _tem("idade_anos") or _tem("idade_meses") or _IDADE_DITA.search(plano):
        respondidos.add(ITEM_IDADE)
    if _tem("idade_gestacional_semanas") or _SEMANAS_DITAS.search(plano):
        respondidos.add(ITEM_SEMANAS)
    if codigo == "febre" or _FEBRE_DITA.search(plano):
        respondidos.add(ITEM_FEBRE)
    return frozenset(respondidos)


class FichaPerguntada(NamedTuple):
    """Uma pergunta da ficha, lida do codigo `PERGUNTAR_FICHA_<GRUPO>[:<i>,<j>][@<paciente>][#de_novo]`."""

    grupo: str
    indices: tuple[int, ...]
    paciente: str | None
    de_novo: bool


_SUFIXO_DE_NOVO: Final[str] = "#de_novo"


def paciente_da_conversa(plano: str) -> str | None:
    """`PACIENTE_FILHA`/`PACIENTE_FILHO` ("minha filha"), `PACIENTE_BEBE` ("bebe", "recem nascido") ou
    `None` (a ficha fala de "a crianca")."""
    achado = _PACIENTE_FILHO_OU_FILHA.search(plano)
    if achado:
        return achado.group(1)
    return PACIENTE_BEBE if _PACIENTE_BEBE.search(plano) else None


def bebe_pequeno(plano: str, idade_meses: object) -> bool:
    """Bebe com MENOS DE 3 MESES (inclusive recem-nascido): pela extracao (`idade_meses`) ou pelo texto."""
    if isinstance(idade_meses, int) and not isinstance(idade_meses, bool):
        return idade_meses < 3
    return bool(_BEBE_PEQUENO.search(plano))


def resposta_curta(plano: str) -> bool:
    """A mensagem (normalizada) e' resposta curta que cobre o perguntado ("nao", "sim, normal")."""
    palavras = plano.split()
    return 0 < len(palavras) <= 5 and bool(_RESPOSTA_CURTA.match(" ".join(palavras)))


def itens_que_faltam(
    grupo: str,
    plano: str,
    respondidos: frozenset[str],
    perguntados: tuple[int, ...] | None = None,
) -> tuple[int, ...]:
    """Os indices (1-based, em ordem) dos itens da ficha de `grupo` ainda SEM resposta, entre os
    `perguntados` (`None` = todos). Item de dado (local, intensidade, inicio, idade, semanas, febre): pelo
    `respondidos`; item de SINAL: pelo assunto dele no texto acumulado (`_RESPOSTA_DO_SINAL`)."""
    faltam: list[int] = []
    for indice, (item, _) in enumerate(ITENS_DA_FICHA.get(grupo, ()), start=1):
        if perguntados is not None and indice not in perguntados:
            continue
        if item == ITEM_SINAL:
            padrao = _RESPOSTA_DO_SINAL.get((grupo, indice))
            if padrao is not None and padrao.search(plano):
                continue
        elif item in respondidos:
            continue
        faltam.append(indice)
    return tuple(faltam)


def codigo_da_ficha(
    grupo: str, faltam: tuple[int, ...], *, paciente: str | None = None, de_novo: bool = False
) -> str:
    """O codigo da pergunta: `PERGUNTAR_FICHA_<GRUPO>` (a ficha inteira) ou `...:<i>,<j>` (so' o que falta),
    com `@<paciente>` (so' na ficha da crianca) e `#de_novo` (item perguntado e nao respondido)."""
    codigo = f"{PREFIXO_PERGUNTA_DA_FICHA}{grupo}"
    if tuple(faltam) != tuple(range(1, len(ITENS_DA_FICHA[grupo]) + 1)):
        codigo = f"{codigo}:{','.join(str(i) for i in faltam)}"
    if paciente is not None and grupo == "CRIANCA" and paciente in FICHA_DA_CRIANCA_POR_PACIENTE:
        codigo = f"{codigo}@{paciente}"
    return f"{codigo}{_SUFIXO_DE_NOVO}" if de_novo else codigo


def ler_codigo_da_ficha(pergunta: object) -> FichaPerguntada | None:
    """O codigo de `codigo_da_ficha` lido de volta, ou `None` (nao e' da ficha, ou esta' malformado)."""
    if not isinstance(pergunta, str) or not pergunta.startswith(PREFIXO_PERGUNTA_DA_FICHA):
        return None
    resto = pergunta[len(PREFIXO_PERGUNTA_DA_FICHA) :]
    de_novo = resto.endswith(_SUFIXO_DE_NOVO)
    if de_novo:
        resto = resto[: -len(_SUFIXO_DE_NOVO)]
    resto, arroba, paciente = resto.partition("@")
    if arroba and paciente not in FICHA_DA_CRIANCA_POR_PACIENTE:
        return None
    grupo, separador, indices = resto.partition(":")
    itens = ITENS_DA_FICHA.get(grupo)
    if itens is None:
        return None
    if not separador:
        escolhidos = tuple(range(1, len(itens) + 1))
    else:
        partes = indices.split(",")
        if not all(p.isdigit() for p in partes):
            return None
        escolhidos = tuple(int(p) for p in partes)
        if list(escolhidos) != sorted(set(escolhidos)) or not all(1 <= i <= len(itens) for i in escolhidos):
            return None
    return FichaPerguntada(grupo, escolhidos, paciente if arroba else None, de_novo)


def grupo_da_queixa(population: object, sintoma_codigo: object, plano: str) -> str | None:
    """O tipo de queixa da aba 4 (chave de `PERGUNTAS_DA_FICHA`), ou `None` quando nao se sabe.

    A POPULACAO vence (gestante, crianca, saude mental): a ficha dela pergunta o que as tabelas de alarme
    daquela populacao mais precisam (semanas de gestacao, idade em meses). Depois o codigo da allowlist,
    depois o texto.
    """
    if isinstance(population, str) and population in _GRUPO_DA_POPULACAO:
        return _GRUPO_DA_POPULACAO[population]
    if isinstance(sintoma_codigo, str) and sintoma_codigo in _GRUPO_DO_CODIGO:
        return _GRUPO_DO_CODIGO[sintoma_codigo]
    for grupo, padrao in _GRUPOS_PELO_TEXTO:
        if padrao.search(plano):
            return grupo
    return None


def pergunta_da_ficha(pergunta: object) -> str | None:
    """O texto fixo de um codigo de `codigo_da_ficha`, ou `None` se `pergunta` nao e' uma pergunta da ficha
    (ou esta' malformada — o `collect` cai no generico)."""
    lida = ler_codigo_da_ficha(pergunta)
    if lida is None:
        return None
    if lida.grupo == "CRIANCA" and lida.paciente is not None:
        frases = [FICHA_DA_CRIANCA_POR_PACIENTE[lida.paciente][i - 1] for i in lida.indices]
    else:
        frases = [ITENS_DA_FICHA[lida.grupo][i - 1][1] for i in lida.indices]
    if lida.de_novo:
        primeira = frases[0]
        frases = [ABERTURA_DE_NOVO, primeira[:1].lower() + primeira[1:], *frases[1:]]
    return " ".join(frases)


def texto_de_orientacao(population: object, *, bebe_com_menos_de_3_meses: bool) -> str:
    """O T05 da populacao: adulto -> clinico geral; crianca -> pediatra (bebe com menos de 3 meses ->
    oferta da equipe de saude agora, sem o prazo); gestante -> obstetra ou ginecologista."""
    if population == "pediatric":
        if bebe_com_menos_de_3_meses:
            return RESPOSTA_TRIAGEM_BEBE_PEQUENO
        return RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA
    if population == "gestante":
        return RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_GESTANTE
    return RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO


class SaidaInvalidaError(ValueError):
    """A linha da tabela nova saiu do vocabulario fechado — o codigo trata como falha tecnica."""


def ler_linha(row: Mapping[str, object]) -> tuple[str, str | None, str | None, str | None]:
    """`(decisao, motivo, severidade, texto)` validados de UMA linha da tabela, ou `SaidaInvalidaError`.

    FAIL-CLOSED: decisao fora de `DECISOES`; caso aberto sem motivo/severidade do dominio; linha SEM caso
    que traga motivo/severidade; ou decisao que precisa de texto sem ID de texto conhecido. Os "-" viram
    `None`. A mensagem de erro so' carrega tokens fechados (nunca valor vindo de fora sem conferir).
    """

    def _valor(chave: str) -> str | None:
        bruto = row.get(chave)
        bruto = bruto.get("value") if isinstance(bruto, Mapping) else bruto
        if bruto is None:
            return None
        texto = str(bruto).strip()
        return None if texto in ("", NAO_SE_APLICA) else texto

    decisao = _valor("decisao")
    if decisao not in DECISOES:
        raise SaidaInvalidaError("decisao fora do vocabulario")
    motivo, severidade, texto = _valor("motivo_categoria"), _valor("severidade"), _valor("texto")
    if decisao in DECISOES_QUE_ABREM_CASO:
        if motivo not in MOTIVOS_DA_HELENA or severidade not in SEVERIDADES:
            raise SaidaInvalidaError("caso sem motivo/severidade do contrato")
    elif motivo is not None or severidade is not None:
        raise SaidaInvalidaError("linha sem caso com motivo/severidade")
    if decisao in DECISOES_COM_TEXTO and texto not in IDS_DE_TEXTO:
        raise SaidaInvalidaError("texto fora do vocabulario")
    return decisao, motivo, severidade, texto


#: Os IDs de texto que a tabela pode emitir (coluna `texto`). T12 = `RESPOSTA_NAVEGACAO` (JA EXISTE), que
#: `graph.py` escolhe com a variante de caso aberto; os outros sao as constantes acima.
IDS_DE_TEXTO: Final[frozenset[str]] = frozenset({"T05", "T08", "T09", "T11", "T12", "OFERTA_PESSOA"})
TEXTOS_POR_ID: Final[Mapping[str, str]] = {
    "T05": RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    "T08": RESPOSTA_TRIAGEM_SEM_CONDUTA,
    "T09": RESPOSTA_TRIAGEM_EXAME_RECEITA,
    "T11": RESPOSTA_TRIAGEM_ESCLARECER,
    "OFERTA_PESSOA": RESPOSTA_TRIAGEM_OFERTA_PESSOA,
}

#: Os textos NOVOS deste modulo que chegam ao beneficiario (as cercas de saida e a regua de CLAREZA sao
#: conferidas sobre todos eles em teste).
TEXTOS_NOVOS: Final[tuple[str, ...]] = (
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_GESTANTE,
    RESPOSTA_TRIAGEM_BEBE_PEQUENO,
    RESPOSTA_TRIAGEM_SEM_CONDUTA,
    RESPOSTA_TRIAGEM_EXAME_RECEITA,
    RESPOSTA_TRIAGEM_ESCLARECER,
    RESPOSTA_TRIAGEM_OFERTA_PESSOA,
    RESPOSTA_TRIAGEM_OPCAO_PLANO,
    *PERGUNTAS_DA_FICHA.values(),
    # Cada frase da ficha tambem sai SOZINHA (a ficha so' pergunta o que falta).
    *(frase for itens in ITENS_DA_FICHA.values() for _, frase in itens),
    *(frase for frases in FICHA_DA_CRIANCA_POR_PACIENTE.values() for frase in frases),
)


def linha_da_ficha(estado: Mapping[str, object]) -> str:
    """A FICHA COLETADA em uma linha para o atendente (vai no `resumo_contexto`, que ja' e' PHI listada e
    passa pelo `redact_free_text` como hoje). So' campos de VOCABULARIO FECHADO e numeros; o texto que a
    pessoa escreveu vai ao resumo pelo modelo (bloco nao confiavel), nunca colado aqui. "" sem nada."""
    partes: list[str] = []
    for rotulo, chave in (
        ("populacao", "population"),
        ("sintoma", "sintoma_codigo"),
        ("intensidade", "intensidade"),
        ("idade_anos", "idade_anos"),
        ("idade_meses", "idade_meses"),
        ("semanas_gestacao", "idade_gestacional_semanas"),
        ("perguntas_feitas", "coleta_rodadas"),
    ):
        valor = estado.get(chave)
        if valor is None or valor in ("", "none", "desconhecida") or isinstance(valor, bool):
            continue
        if not isinstance(valor, (str, int)):
            continue
        partes.append(f"{rotulo}={valor}")
    return f"[ficha da triagem: {', '.join(partes)}]" if partes else ""


__all__ = [
    "COLETA_MAX_RODADAS_TRIAGEM_V2",
    "COLETA_VEREDITO_PERGUNTAR_MAIS",
    "DECISOES",
    "IDS_DE_TEXTO",
    "ITENS_DA_FICHA",
    "NECESSIDADES",
    "PERGUNTAS_DA_FICHA",
    "PREFIXO_PERGUNTA_DA_FICHA",
    "TEXTOS_NOVOS",
    "TEXTOS_POR_ID",
    "TRIAGEM_V2_DMN_KEY",
    "FichaPerguntada",
    "SaidaInvalidaError",
    "bebe_pequeno",
    "codigo_da_ficha",
    "grupo_da_queixa",
    "itens_ja_respondidos",
    "itens_que_faltam",
    "ler_codigo_da_ficha",
    "ler_linha",
    "linha_da_ficha",
    "necessidade_da_pergunta_clinica",
    "necessidade_do_sintoma",
    "necessidade_vaga",
    "paciente_da_conversa",
    "pergunta_da_ficha",
    "relata_sintoma",
    "resposta_curta",
    "resposta_numerada",
    "texto_de_orientacao",
]
