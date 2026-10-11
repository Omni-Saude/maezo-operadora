"""CONSULTA DO PLANO na Helena: fatos do cadastro do PROPRIO beneficiario pelo contrato TINA da AMH.

Decisao do dono de 07/10/2026 (`docs/decisions-log.md`, DL de fatos do plano). Com
`MAEZO_HELENA_CONSULTAS_AMH` ligada E a identidade resolvida (DL-0077: candidato UNICO, referencia
opaca no estado), a Helena responde quatro perguntas — elegibilidade, carteirinha, carencia e
autorizacao — com os FATOS que a AMH devolve (`ports/tina.py`). Desligada, nada deste modulo e'
alcancado: o classify, o validador e o grafo sao byte a byte os de antes.

O QUE ESTE MODULO E'. Vocabulario (intencao, subtipos), o adendo do classify, o prompt de redacao, a
selecao FECHADA dos campos que podem chegar ao texto, a resposta deterministica (sem modelo) e a cerca
propria da resposta. Nao tem E/S: quem consulta a AMH e' a fonte injetada (`FonteDeFatosDoPlano`,
implementada na plataforma por `platform/webhooks/whatsapp/helena_consultas.py`).

AS REGRAS DO TEXTO, e onde cada uma e' garantida:
  * so' fatos do bloco: o prompt manda, e a cerca exige que todo NUMERO do texto exista nos fatos;
  * nunca promete cobertura nem autorizacao: o prompt manda, e a cerca recusa o vocabulario de promessa;
  * carencia e cobertura sao "o que consta no cadastro": texto fixo e prompt;
  * senha e carteirinha SO' mascaradas: so' entram nos fatos se o valor JA vier mascarado;
  * sem nome nem CPF: nenhum campo de nome entra nos fatos (o medico solicitante fica fora) e a cerca
    recusa sequencia de 11 digitos.
Qualquer recusa da cerca (ou falha do modelo) troca o rascunho pela resposta DETERMINISTICA, montada
so' com os fatos — nunca por um segundo rascunho.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Final, Literal, Protocol, get_args, runtime_checkable

from maezo.ports.tina import CarenciasView, ElegibilidadeView, RequisicoesView
from maezo.runtime.prompt_format import UNTRUSTED_INSTRUCAO_DE_PROMPT, render_untrusted_block

from .prompts import SYSTEM_PROMPT, motivo_de_canal_nao_confirmado, motivo_de_recusa

INTENT_CONSULTA_PLANO: Final[str] = "consulta_plano"
ConsultaSubtipo = Literal["elegibilidade", "carteirinha", "carencia", "autorizacao"]
CONSULTA_SUBTIPOS: Final[frozenset[str]] = frozenset(get_args(ConsultaSubtipo))
#: Os UNICOS subtipos que o modelo redige (revisao do #690). Autorizacao e carencia sao sempre a
#: resposta deterministica: status e prazo sao exatamente o que um rascunho poderia transformar em promessa.
SUBTIPOS_REDIGIDOS: Final[frozenset[str]] = frozenset({"elegibilidade", "carteirinha"})

#: Versao do ADENDO do classify (anexado ao classify v5/v6 SO' com as consultas ligadas) e do prompt
#: de redacao da resposta com fatos. As duas entram em `graph.PROMPT_VERSIONS` e no `agent.yaml`.
CLASSIFY_CONSULTAS_ADENDO_VERSION: Final[str] = "classify-consultas-v1"
#: v2 — 10/10/2026 (DL-0087): regra 7, o tom humanizado (curto, cordial, so' o perguntado, sem emoji).
CONSULTA_PLANO_PROMPT_VERSION: Final[str] = "consulta-plano-v2"

#: Quantas requisicoes/carencias vao ao texto. O agente conversa, nao exporta (contrato: 1..50).
LIMITE_REQUISICOES: Final[int] = 5
LIMITE_CARENCIAS: Final[int] = 20
#: Teto do texto redigido; acima disso a resposta deterministica e' mais honesta que um extrato.
MAX_CHARS_RESPOSTA: Final[int] = 1200

#: SEM IDENTIDADE OU FALHA: texto FIXO, sem modelo. Nao promete humano (a oferta e' a mesma frase de
#: `RESPOSTA_FORA_DO_CANAL`, ja' aprovada pelas cercas); quem pede uma pessoa cai no gatilho 3 do
#: classify (SP-OP-ESCALATION-001, `solicitacao_humano`) no turno seguinte.
RESPOSTA_CONSULTA_SEM_IDENTIDADE: Final[str] = (
    "Não consegui confirmar seus dados, então não consigo ver as informações do seu plano por "
    "aqui. Se quiser falar com uma pessoa da equipe, é só me pedir."
)
#: Fecho da resposta com fatos: o que o fato E' (cadastro) e a mesma oferta de pessoa. DL-0087: mais curto,
#: mesma substancia (cadastro, nao confirma cobertura nem autorizacao, oferta de pessoa).
_FECHO: Final[str] = (
    "Essas informações são do cadastro do plano e não confirmam cobertura nem autorização de "
    "procedimento. Se quiser falar com uma pessoa da equipe, é só me pedir."
)


@runtime_checkable
class FonteDeFatosDoPlano(Protocol):
    """Consulta a AMH para UM sujeito e UM subtipo. Nunca levanta; falha = `None`."""

    async def consultar(
        self, portable_subject_ref: str, subtipo: str
    ) -> ElegibilidadeView | CarenciasView | RequisicoesView | None: ...


# --------------------------------------------------------------------------------------------
# Classify (adendo) e redacao
# --------------------------------------------------------------------------------------------

#: Anexado ao FIM do classify (v5 ou v6) com as consultas ligadas. Acrescenta a intencao e o campo
#: `consulta_subtipo`, e VENCE o paragrafo de "fora do canal" para as quatro perguntas do plano.
ADENDO_CLASSIFY_CONSULTAS: Final[str] = """

CONSULTA DO PLANO (classify-consultas-v1, 07/10/2026). Alem das intencoes acima, existe
intent="consulta_plano": a pessoa quer saber um FATO do proprio cadastro no plano. Esta regra VENCE
o paragrafo de assunto fora do canal para estes quatro assuntos, e so' para eles. Devolva tambem o
campo "consulta_subtipo": SOMENTE quando intent="consulta_plano", um de ["elegibilidade",
"carteirinha", "carencia", "autorizacao"]; em qualquer outro intent, null.
  - "elegibilidade": se o plano esta' ativo, qual e' o plano, vigencia, acomodacao, se pode ser
    atendido ("meu plano esta ativo?", "qual o meu plano?");
  - "carteirinha": numero ou dados da carteirinha ("qual o numero da minha carteirinha?");
  - "carencia": carencias do plano ("ja cumpri a carencia?", "tenho carencia para parto?");
  - "autorizacao": andamento de um pedido de autorizacao ou guia ja' feito ("minha autorizacao saiu?",
    "qual o status da minha guia?").
Autorizacao NEGADA com pedido de explicacao ("por que negaram?") continua "outside_channel": voce
nunca explica uma negativa. Cobranca, boleto e mensalidade NUNCA sao "consulta_plano". Uma mensagem
com QUALQUER sinal de saude segue as regras de saude, e pedir uma pessoa continua "human_request"."""

#: Instrucao de redacao da resposta com fatos (`consulta-plano-v2`).
_PROMPT_CONSULTA: Final[
    str
] = """Tarefa: responda a pergunta do beneficiario sobre o PROPRIO plano usando SOMENTE os fatos
do bloco de fatos do cadastro abaixo. Escreva em portugues do Brasil, em ate' 6 frases curtas, sem
markdown.

REGRAS (todas obrigatorias):
1. Use so' o que esta' no bloco de fatos. Campo nulo ou ausente e' "nao consta no cadastro": nunca
   complete, estime ou suponha um valor. Nao invente data, numero, prazo, plano nem status.
2. Nunca prometa cobertura nem autorizacao, e nunca diga que um procedimento pode ou nao pode ser
   feito. Carencia e cobertura sao "o que consta no cadastro".
3. Senha e carteirinha: copie EXATAMENTE como vierem (ja' mascaradas). Nunca escreva um numero
   completo.
4. Nao escreva nome de pessoa, CPF, telefone nem endereco. Nao cite aplicativo, portal, site nem
   telefone.
5. Nao diga que vai encaminhar, registrar, atualizar ou acompanhar nada. Termine dizendo que, se a
   pessoa quiser falar com uma pessoa da equipe, e' so' pedir.
6. Status de autorizacao: repita o status exatamente como esta' no bloco, dizendo que e' o que consta
   no cadastro.
7. Tom (decisao do dono, 10/10/2026): cordial, calmo e direto, em segunda pessoa ("voce"). Responda
   SO' o assunto perguntado, sem despejar os outros fatos do bloco. Sem emoji. O tom nunca afrouxa as
   regras 1 a 6."""


def consulta_prompt(*, subtipo: str, fatos: Mapping[str, Any], bloco_mensagem: str) -> str:
    """O prompt inteiro da redacao: sistema + instrucao + FATOS delimitados + a mensagem JA demarcada.

    `bloco_mensagem` chega PRONTO (`render_untrusted_block("message_body", ...)` no proprio no' do
    grafo, onde a cerca AST da fronteira nao confiavel o enxerga). Os fatos tambem vao demarcados: sao
    texto do cadastro (terceiro), material a usar e nunca instrucao."""
    bloco_fatos = render_untrusted_block(
        "fatos_do_plano", json.dumps(dict(fatos), ensure_ascii=False, sort_keys=True)
    )
    return (
        f"{SYSTEM_PROMPT}\n\n{_PROMPT_CONSULTA}\n\n{UNTRUSTED_INSTRUCAO_DE_PROMPT} O bloco de fatos e a "
        f"mensagem chegam em blocos desses.\n\nassunto={subtipo}\n{bloco_fatos}\n{bloco_mensagem}"
    )


# --------------------------------------------------------------------------------------------
# Fatos: selecao FECHADA dos campos que podem chegar ao texto
# --------------------------------------------------------------------------------------------

#: Carteirinha e senha so' entram se JA vierem mascaradas: ao menos 3 `*` e no maximo 4 caracteres
#: visiveis no fim. Um valor fora disso e' tratado como ausente (nunca exposto).
_MASCARADO: Final[re.Pattern[str]] = re.compile(r"\*{3,}[0-9A-Za-z]{0,4}\Z")
_TEXTO_MAX: Final[int] = 120


def _texto(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    limpo = " ".join(valor.split())
    return limpo[:_TEXTO_MAX] if limpo else None


def _mascarado(valor: object) -> str | None:
    return valor if isinstance(valor, str) and _MASCARADO.fullmatch(valor) else None


def _bool(valor: object) -> bool | None:
    return valor if isinstance(valor, bool) else None


def _inteiro(valor: object) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else None


def _data(valor: object) -> str | None:
    """`AAAA-MM-DD` (ou o dia de um date-time) -> o mesmo `AAAA-MM-DD`; qualquer outra coisa -> None."""
    if not isinstance(valor, str) or len(valor) < 10:
        return None
    try:
        return date.fromisoformat(valor[:10]).isoformat()
    except ValueError:
        return None


def fatos_da_consulta(
    subtipo: str, view: ElegibilidadeView | CarenciasView | RequisicoesView | None
) -> dict[str, Any] | None:
    """Os fatos que PODEM ir ao texto, por subtipo, ou `None` (view ausente ou de outro tipo).

    Lista FECHADA por subtipo: o que nao esta' aqui nunca chega ao modelo nem ao beneficiario —
    em particular `medico_solicitante` (nome de pessoa), `titular_ref` e `relacao_dependencia`.
    """
    if subtipo in ("elegibilidade", "carteirinha") and isinstance(view, ElegibilidadeView):
        vinculos: list[dict[str, Any]] = []
        for v in view.vinculos:
            if subtipo == "carteirinha":
                item: dict[str, Any] = {
                    "carteirinha_mascarada": _mascarado(v.carteirinha_mascarada),
                    "plano": _texto(v.plano),
                    "vigencia_inicio": _data(v.vigencia_inicio),
                    "cancelamento": _data(v.cancelamento),
                    "titular": _bool(v.titular),
                }
            else:
                item = {
                    "plano": _texto(v.plano),
                    "segmentacao": _texto(v.segmentacao),
                    "acomodacao": _texto(v.acomodacao),
                    "tipo_contratacao": _texto(v.tipo_contratacao),
                    "vigencia_inicio": _data(v.vigencia_inicio),
                    "cancelamento": _data(v.cancelamento),
                    "atendimento_liberado": _bool(v.atendimento_liberado),
                    "situacao_beneficiario": _texto(v.situacao_vinculo),
                    "situacao_contrato": _texto(v.situacao_contrato),
                    "titular": _bool(v.titular),
                }
            vinculos.append(item)
        return {
            "ativo": view.ativo is True,
            "vinculos": vinculos,
            "fonte_atualizada_em": _data(view.fonte_atualizada_em),
        }
    if subtipo == "carencia" and isinstance(view, CarenciasView):
        return {
            "pendentes": _inteiro(view.pendentes),
            "carencias": [
                {
                    "carencia": _texto(c.carencia),
                    "inicio": _data(c.inicio),
                    "dias": _inteiro(c.dias),
                    "validade": _data(c.validade),
                    "cumprida": _bool(c.cumprida),
                }
                for c in view.carencias[:LIMITE_CARENCIAS]
            ],
            "fonte_atualizada_em": _data(view.fonte_atualizada_em),
        }
    if subtipo == "autorizacao" and isinstance(view, RequisicoesView):
        return {
            "requisicoes": [
                {
                    "solicitacao": _inteiro(r.solicitacao),
                    "solicitada_em": _data(r.solicitada_em),
                    "status": _texto(r.status),
                    "senha_mascarada": _mascarado(r.senha_mascarada),
                    "senha_validade": _data(r.senha_validade),
                    "senha_vigente": _bool(r.senha_vigente),
                    "sla_dias": _inteiro(r.sla_dias),
                    "liberacao_prevista": _data(r.liberacao_prevista),
                }
                for r in view.requisicoes[:LIMITE_REQUISICOES]
            ],
            "fonte_atualizada_em": _data(view.fonte_atualizada_em),
        }
    return None


def _folhas(valor: object) -> list[object]:
    if isinstance(valor, Mapping):
        return [folha for item in valor.values() for folha in _folhas(item)]
    if isinstance(valor, list | tuple):
        return [folha for item in valor for folha in _folhas(item)]
    return [valor]


def literais_dos_fatos(fatos: Mapping[str, Any]) -> tuple[str, ...]:
    """Os TEXTOS livres que vieram do cadastro (plano, status, descricao de carencia...).

    As cercas lexicas da Helena foram escritas para texto do MODELO: "em analise" e' status
    inventado quando o modelo o escreve, e e' fato quando e' o status que consta no cadastro. As duas
    cercas leem o texto com estes literais APAGADOS (`texto_para_cerca`), e o resto do texto continua
    passando por elas inteiro. Literal com menos de 3 caracteres nao entra (apagaria demais).

    NUNCA E' APAGADO (revisao do #690): literal que case com a cerca de PROMESSA ou com a cerca de CANAL.
    Um status "Autorizada" de requisicao antiga apagado deixaria passar "sua solicitacao esta' autorizada"
    — o literal do cadastro nao pode virar salvo-conduto para a frase que a cerca existe para barrar.
    """
    vistos: dict[str, None] = {}
    for folha in _folhas(fatos):
        if (
            isinstance(folha, str)
            and len(folha) >= 3
            and not _MASCARADO.fullmatch(folha)
            and not _PROMESSA.search(_sem_acento(folha))
            and motivo_de_canal_nao_confirmado(folha) is None
        ):
            vistos.setdefault(folha, None)
    return tuple(sorted(vistos, key=len, reverse=True))


def texto_para_cerca(texto: str, literais: Sequence[str] | None) -> str:
    """`texto` com cada literal do cadastro trocado por `[fato]` (caixa ignorada).

    SO' A PALAVRA INTEIRA: um literal "Ativo" apagado DENTRO de "aplicativo" desligaria a cerca de
    canal (medido no teste deste modulo). As bordas `(?<!\\w)`/`(?!\\w)` impedem o apagamento parcial.
    """
    if not literais:
        return texto
    for literal in literais:
        if isinstance(literal, str) and literal:
            texto = re.sub(rf"(?<!\w){re.escape(literal)}(?!\w)", "[fato]", texto, flags=re.IGNORECASE)
    return texto


# --------------------------------------------------------------------------------------------
# Resposta deterministica (sem modelo)
# --------------------------------------------------------------------------------------------


def _br(iso: object) -> str | None:
    if not isinstance(iso, str):
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return None


def _sim_nao(valor: object) -> str | None:
    if valor is True:
        return "sim"
    if valor is False:
        return "não"
    return None


def _linha(partes: Sequence[tuple[str, str | None]]) -> str:
    return "; ".join(f"{rotulo}: {valor}" for rotulo, valor in partes if valor)


def resposta_deterministica(subtipo: str, fatos: Mapping[str, Any]) -> str:
    """A resposta montada SO' com os fatos. E' o caminho quando o modelo falha ou a cerca recusa."""
    atualizado = _br(fatos.get("fonte_atualizada_em"))
    abertura = "Pelo que consta no cadastro do plano" + (
        f" (atualizado em {atualizado})" if atualizado else ""
    )
    linhas: list[str] = []
    if subtipo in ("elegibilidade", "carteirinha"):
        for v in fatos.get("vinculos") or []:
            if subtipo == "carteirinha":
                linha = _linha(
                    (
                        ("carteirinha", v.get("carteirinha_mascarada")),
                        ("plano", v.get("plano")),
                        ("vigência desde", _br(v.get("vigencia_inicio"))),
                        ("cancelamento", _br(v.get("cancelamento"))),
                    )
                )
            else:
                linha = _linha(
                    (
                        ("plano", v.get("plano")),
                        ("segmentação", v.get("segmentacao")),
                        ("acomodação", v.get("acomodacao")),
                        ("vigência desde", _br(v.get("vigencia_inicio"))),
                        ("cancelamento", _br(v.get("cancelamento"))),
                        ("situação", v.get("situacao_beneficiario")),
                        ("atendimento liberado", _sim_nao(v.get("atendimento_liberado"))),
                    )
                )
            if linha:
                linhas.append(f"- {linha}")
        if not linhas:
            corpo = "não consta nenhum vínculo com o plano para este cadastro."
        else:
            estado = "há vínculo ativo" if fatos.get("ativo") is True else "não há vínculo ativo"
            corpo = f"{estado}:\n" + "\n".join(linhas)
    elif subtipo == "carencia":
        for c in fatos.get("carencias") or []:
            cumprida = c.get("cumprida")
            situacao = (
                "cumprida"
                if cumprida is True
                else (f"em curso até {_br(c.get('validade'))}" if _br(c.get("validade")) else "em curso")
                if cumprida is False
                else None
            )
            linha = _linha(((c.get("carencia") or "carência sem descrição", situacao),))
            if linha:
                linhas.append(f"- {linha}")
        corpo = "constam estas carências:\n" + "\n".join(linhas) if linhas else "não consta nenhuma carência."
    elif subtipo == "autorizacao":
        for r in fatos.get("requisicoes") or []:
            linha = _linha(
                (
                    ("solicitação", str(r["solicitacao"]) if r.get("solicitacao") is not None else None),
                    ("pedida em", _br(r.get("solicitada_em"))),
                    ("status", r.get("status")),
                    ("senha", r.get("senha_mascarada")),
                    ("senha válida até", _br(r.get("senha_validade"))),
                    ("liberação prevista", _br(r.get("liberacao_prevista"))),
                )
            )
            if linha:
                linhas.append(f"- {linha}")
        corpo = (
            "estas são as solicitações de autorização mais recentes:\n" + "\n".join(linhas)
            if linhas
            else "não consta nenhuma solicitação de autorização."
        )
    else:
        return RESPOSTA_CONSULTA_SEM_IDENTIDADE
    return f"{abertura}, {corpo}\n{_FECHO}"


# --------------------------------------------------------------------------------------------
# Cerca propria da resposta com fatos
# --------------------------------------------------------------------------------------------

#: Promessa de cobertura/autorizacao e o vocabulario de "pode fazer". Lidos sobre o texto ja' sem os
#: literais do cadastro (um status "Autorizada" e' fato; "sua cirurgia esta' autorizada" escrito
#: pelo modelo sem estar no bloco, nao).
_PROMESSA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:cobert[oa]s?|cobre|cobrem|garant\w*|"
    r"(?:esta|estao|foi|foram|sera|serao|vai ser|vao ser)\s+(?:autorizad[oa]s?|liberad[oa]s?|aprovad[oa]s?)|"
    r"pode\s+(?:fazer|realizar|marcar|usar)|podera\s+(?:fazer|realizar|usar)|"
    # Prazo inventado (revisao do #690): "deve ser liberado", "sai em", "prazo de".
    r"devem?\s+ser\s+liberad\w*|(?:vai|vao|sera|serao)\s+ser\s+liberad\w*|sera\s+liberad\w*|"
    r"sai\s+em|prazo\s+de)\b",
    re.IGNORECASE,
)
_ONZE_DIGITOS: Final[re.Pattern[str]] = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_DIGITOS: Final[re.Pattern[str]] = re.compile(r"\d+")


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def motivo_de_recusa_da_consulta(texto: str, fatos: Mapping[str, Any]) -> str | None:
    """Token da regra violada pelo rascunho, ou `None`. PURA.

    Ordem: as duas cercas gerais da Helena (canal e recusa de saida, com `start_aconteceu=False` —
    ninguem foi acionado neste turno), depois as proprias: tamanho, CPF, promessa e NUMERO fora dos
    fatos. As gerais leem o texto com os literais do cadastro apagados (ver `literais_dos_fatos`).
    """
    if not isinstance(texto, str) or not texto.strip():
        return "vazio"
    if len(texto) > MAX_CHARS_RESPOSTA:
        return "longo_demais"
    cercado = texto_para_cerca(texto, literais_dos_fatos(fatos))
    if motivo_de_canal_nao_confirmado(cercado) is not None:
        return "canal_nao_confirmado"
    recusa = motivo_de_recusa(cercado, "inform", start_aconteceu=False)
    if recusa is not None:
        return recusa[0]
    if _ONZE_DIGITOS.search(cercado):
        return "documento"
    # A promessa e' lida no texto ORIGINAL: nenhum literal do cadastro a esconde.
    if _PROMESSA.search(_sem_acento(texto)):
        return "promessa_de_cobertura"
    tokens = _tokens_numericos(fatos)
    for numero in _DIGITOS.findall(cercado):
        if _sem_zero(numero) not in tokens:
            return "numero_fora_dos_fatos"
    return None


def _sem_zero(numero: str) -> str:
    return numero.lstrip("0") or "0"


def _tokens_numericos(fatos: Mapping[str, Any]) -> frozenset[str]:
    """O CONJUNTO de numeros que os fatos contem (revisao do #690), sem zeros a esquerda.

    Um token por inteiro e por sequencia de digitos de cada texto; uma data ISO entra como dia, mes e ano
    separados (o texto pode escrever 01/03/2024). Comparar por CONJUNTO, e nao por substring da
    concatenacao: "3 dias" ou "ate' 20/10" nao passam so' porque a solicitacao e' 12345.
    """
    tokens: set[str] = set()
    for folha in _folhas(fatos):
        if isinstance(folha, bool):
            continue
        if isinstance(folha, int):
            tokens.add(_sem_zero(str(folha)))
        elif isinstance(folha, str):
            tokens.update(_sem_zero(d) for d in _DIGITOS.findall(folha))
    return frozenset(tokens)


__all__ = [
    "ADENDO_CLASSIFY_CONSULTAS",
    "CLASSIFY_CONSULTAS_ADENDO_VERSION",
    "CONSULTA_PLANO_PROMPT_VERSION",
    "CONSULTA_SUBTIPOS",
    "INTENT_CONSULTA_PLANO",
    "LIMITE_CARENCIAS",
    "LIMITE_REQUISICOES",
    "MAX_CHARS_RESPOSTA",
    "RESPOSTA_CONSULTA_SEM_IDENTIDADE",
    "SUBTIPOS_REDIGIDOS",
    "ConsultaSubtipo",
    "FonteDeFatosDoPlano",
    "consulta_prompt",
    "fatos_da_consulta",
    "literais_dos_fatos",
    "motivo_de_recusa_da_consulta",
    "resposta_deterministica",
    "texto_para_cerca",
]
