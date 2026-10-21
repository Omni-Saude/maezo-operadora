"""ROTEADOR de habilidades da Helena (ADR-0066, DL-0134) — F1: MODO SOMBRA.

Em sombra (`MAEZO_HELENA_ROTEADOR=sombra`) o roteador e as travas de entrada rodam EM PARALELO à Helena
atual e só REGISTRAM a decisão (log `helena_roteador_sombra`), sem mudar resposta, estado, caso ou envio. O
log leva só vocabulário fechado (habilidade, confiança, trava, pendência), nunca texto da pessoa nem do
modelo.

Peças (todas puras, testáveis sem grafo):
- `montar_prompt`: instruções a partir do CONTRATO (`spec/agents/helena/roteador.yaml`) + histórico, pendência
e
  mensagem atual em blocos NÃO CONFIÁVEIS (`render_untrusted_block`).
- `ler_decisao`: o JSON do modelo -> `DecisaoDoRoteador` (habilidade do contrato ou "duvida"); qualquer desvio
  -> `None` (falha registrada, nunca adivinhada).
- `trava_de_entrada`: qual trava fixa decidiria o turno pelos sinais DETERMINÍSTICOS do texto (alarme, risco,
  dados pessoais), na ordem do contrato.
- `habilidades_da_helena_atual`: a decisão que a Helena atual tomou, no vocabulário das habilidades, a partir
do
  estado final do turno (para a concordância).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from maezo.agents.helena.roteador_contrato import (
    CONFIANCAS,
    DUVIDA,
    HABILIDADES,
    ContratoDoRoteador,
)

ROTEADOR_PROMPT_VERSION: Final[str] = "roteador-v3"
NL: Final[str] = chr(10)

#: Rótulos de trava (os ids do contrato) e de falha do roteador, para o log.
TRAVA_ALARME: Final[str] = "alarme_pela_tabela"
TRAVA_RISCO: Final[str] = "risco_psicossocial"
TRAVA_DADOS: Final[str] = "dados_pessoais"
FALHA_PRAZO: Final[str] = "prazo"
FALHA_MODELO: Final[str] = "modelo"
FALHA_JSON: Final[str] = "json_ilegivel"
FALHA_OCUPADO: Final[str] = "ocupado"

#: CPF/CNPJ (com ou sem pontuação) e sequências longas de dígitos (carteirinha, cartão) ditas na mensagem.
_DADO_PESSOAL: Final[re.Pattern[str]] = re.compile(
    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b|\b\d{12,}\b"
)


@dataclass(frozen=True)
class PendenteDoRoteador:
    """A pergunta que a Helena deixou no turno anterior: a habilidade dona e o código (vocabulário
    fechado)."""

    habilidade: str
    codigo: str


@dataclass(frozen=True)
class DecisaoDoRoteador:
    habilidade: str
    confianca: str
    alternativa: str | None
    responde_a_pendencia: bool

    @property
    def pergunta_na_duvida(self) -> bool:
        """Na F2, esta decisão vira a pergunta "X ou Y?" em vez de uma habilidade."""
        return self.habilidade == DUVIDA or self.confianca == "baixa"


def _instrucoes(contrato: ContratoDoRoteador) -> str:
    """roteador-v3 (21/10/2026): prompt ENXUTO — uma linha por habilidade (o `resumo` do contrato), a regra da
    pendencia e a saida JSON curta. Medido com mensagens sinteticas antes de ligar (ADR-0066)."""
    linhas = [
        "Voce e' o roteador de um assistente de plano de saude no WhatsApp. Escolha UMA habilidade para a "
        "MENSAGEM ATUAL:",
        *(f"{h.id}: {h.resumo}" for h in contrato.habilidades),
        "A PENDENTE e' a pergunta que o assistente deixou: se a mensagem responde a ela, escolha a "
        "habilidade dela e p=true; se mudou de assunto, escolha o assunto novo.",
        'Na duvida real entre duas, h="duvida", a=<a mais provavel>, b=<a outra>.',
        'Responda SO\' o JSON {"h":"<habilidade>","c":"alta|media|baixa","p":true|false}',
    ]
    return NL.join(linhas)


def montar_prompt(
    contrato: ContratoDoRoteador,
    *,
    bloco_historico: str,
    bloco_mensagem: str,
    pendente: PendenteDoRoteador | None,
) -> str:
    """O prompt do roteador. `bloco_historico` e `bloco_mensagem` chegam JA' demarcados como não confiáveis
    pelo chamador (`render_untrusted_block`, onde a cerca de entrada os vê); o histórico pode vir vazio."""
    pend = (
        f"habilidade dona: {pendente.habilidade}; código: {pendente.codigo}"
        if pendente is not None
        else "nenhuma"
    )
    partes = [_instrucoes(contrato), f"PENDENTE: {pend}"]
    if bloco_historico.strip():
        partes += ["ULTIMAS:", bloco_historico]
    partes += ["MENSAGEM ATUAL:", bloco_mensagem]
    return NL.join(partes)


def _json_do_texto(bruto: str) -> Mapping[str, Any] | None:
    texto = (bruto or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        texto = texto[texto.find("{") :] if "{" in texto else texto
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return None
    try:
        dado = json.loads(texto[inicio : fim + 1])
    except (ValueError, TypeError):
        return None
    return dado if isinstance(dado, Mapping) else None


def ler_decisao(bruto: str) -> DecisaoDoRoteador | None:
    """O JSON do modelo -> decisão. Habilidade fora do contrato, confiança fora do vocabulário ou JSON
    ilegível -> `None` (o chamador registra a falha; nada é adivinhado)."""
    dado = _json_do_texto(bruto)
    if dado is None:
        return None
    # roteador-v3 usa chaves curtas (h, c, p, a, b); as longas continuam aceitas.
    habilidade = str(dado.get("h") or dado.get("habilidade") or "").strip().lower()
    confianca = str(dado.get("c") or dado.get("confianca") or "alta").strip().lower()
    if "p" in dado and "responde_a_pendencia" not in dado:
        dado = {**dado, "responde_a_pendencia": dado.get("p")}
    if habilidade == DUVIDA:
        dado = {
            **dado,
            "habilidade_provavel": dado.get("a") or dado.get("habilidade_provavel"),
            "alternativa": dado.get("b") or dado.get("alternativa"),
        }
    alternativa_bruta = dado.get("alternativa")
    alternativa = str(alternativa_bruta).strip().lower() if alternativa_bruta else None
    if alternativa not in HABILIDADES:
        alternativa = None
    if habilidade == DUVIDA:
        provavel = str(dado.get("habilidade_provavel") or "").strip().lower()
        if provavel not in HABILIDADES:
            return None
        # "duvida" leva a provável como primeira opção e a alternativa como segunda.
        return DecisaoDoRoteador(
            DUVIDA, "baixa", alternativa or provavel, bool(dado.get("responde_a_pendencia"))
        )
    if habilidade not in HABILIDADES or confianca not in CONFIANCAS:
        return None
    return DecisaoDoRoteador(habilidade, confianca, alternativa, dado.get("responde_a_pendencia") is True)


def trava_de_entrada(
    texto: str,
    *,
    alarme: Callable[[str], bool],
    risco: Callable[[str], bool],
) -> str | None:
    """A trava fixa que decidiria o turno pelos sinais DETERMINÍSTICOS do texto, na ordem do contrato.

    `alarme` e `risco` são as regras que a Helena já usa (injetadas pelo grafo: alarme determinístico, sinal
    forte de emergência, alarme do assunto de sintoma em curso; risco psicossocial pelo texto). Consentimento
    e
    identificação são decididos antes do grafo e não aparecem aqui. A tabela DMN não é consultada em sombra
    (a decisão dela aparece na Helena atual como `red_flag_clinico`)."""
    if alarme(texto):
        return TRAVA_ALARME
    if risco(texto):
        return TRAVA_RISCO
    if _DADO_PESSOAL.search(texto or ""):
        return TRAVA_DADOS
    return None


_MOTIVO_PARA_HABILIDADE: Final[Mapping[str, str]] = {
    "red_flag_clinico": TRAVA_ALARME,
    "risco_psicossocial": TRAVA_RISCO,
    "solicitacao_humano": "equipe",
    "intencao_clinica": "sintoma",
}


def habilidades_da_helena_atual(
    final: Mapping[str, Any],
    *,
    pendente_antes: str | None,
    dona_da_pendencia: Callable[[str | None], str | None],
) -> frozenset[str]:
    """A decisão da Helena atual no vocabulário das habilidades (ou de uma trava), pelo estado FINAL do turno.

    Devolve um CONJUNTO porque algumas saídas de hoje servem a duas habilidades (ex.: `information` sem
    pendência responde dúvida de saúde e conversa); o roteador concorda se escolheu qualquer uma delas."""
    motivo = str(final.get("escalation_motivo") or "")
    if motivo in _MOTIVO_PARA_HABILIDADE:
        return frozenset({_MOTIVO_PARA_HABILIDADE[motivo]})
    if final.get("psychosocial_risk") is True:
        return frozenset({TRAVA_RISCO})
    handoff = final.get("handoff")
    intent = str(final.get("intent") or "")
    subtipo = str(final.get("cobranca_subtipo") or "")
    if (isinstance(handoff, Mapping) and handoff.get("cobranca_subtipo") == "cancelamento") or (
        intent == "cobranca" and subtipo == "cancelamento"
    ):
        return frozenset({"pedidos"})
    if handoff or intent == "cobranca":
        return frozenset({"cobranca"})
    nova = dona_da_pendencia(str(final.get("coleta_pendente") or "") or None)
    if nova is not None:
        return frozenset({nova})
    antiga = dona_da_pendencia(pendente_antes)
    if intent == "consulta_plano":
        if antiga == "cobertura" or str(final.get("consulta_subtipo") or "") == "cobertura":
            return frozenset({"cobertura"})
        return frozenset({"plano", "cobertura"})
    if antiga is not None and intent in ("", "information", "greeting"):
        # A pendência foi respondida e fechada sem nova pergunta (ex.: "ok" depois da resposta).
        return frozenset({antiga, "conversa"})
    por_intencao: Mapping[str, frozenset[str]] = {
        "symptom": frozenset({"sintoma"}),
        "clinical_question": frozenset({"saude", "sintoma"}),
        "human_request": frozenset({"equipe"}),
        "greeting": frozenset({"conversa"}),
        "outside_channel": frozenset({"conversa"}),
        "scheduling": frozenset({"conversa", "equipe"}),
        "information": frozenset({"saude", "conversa", "plano"}),
    }
    return por_intencao.get(intent, frozenset({"conversa"}))


def rotulo_da_conversa(conversation_id: str) -> str:
    """Um rótulo curto e estável para agrupar os turnos de uma conversa no log (nunca o id em si)."""
    return hashlib.sha256((conversation_id or "").encode("utf-8")).hexdigest()[:10]


def campos_do_log(
    decisao: DecisaoDoRoteador | None,
    *,
    falha: str | None,
    trava: str | None,
    atual: frozenset[str],
    pendente: PendenteDoRoteador | None,
    ms: int,
    conversa: str,
) -> dict[str, Any]:
    """Os campos do `helena_roteador_sombra` — só vocabulário fechado e números."""
    escolha = trava or (decisao.habilidade if decisao is not None else None)
    concorda = escolha is not None and (
        escolha in atual
        or (decisao is not None and decisao.habilidade == DUVIDA and decisao.alternativa in atual)
    )
    return {
        "habilidade": decisao.habilidade if decisao is not None else None,
        "confianca": decisao.confianca if decisao is not None else None,
        "alternativa": decisao.alternativa if decisao is not None else None,
        "responde_a_pendencia": decisao.responde_a_pendencia if decisao is not None else None,
        "trava": trava,
        "atual": ",".join(sorted(atual)),
        "concorda": concorda,
        "pendencia": pendente.habilidade if pendente is not None else None,
        "falha": falha,
        "ms": ms,
        "conversa": conversa,
    }


def pendente_do_estado(
    coleta_pendente: str | None, dona_da_pendencia: Callable[[str | None], str | None]
) -> PendenteDoRoteador | None:
    codigo = str(coleta_pendente or "")
    dona = dona_da_pendencia(codigo or None)
    if dona is None:
        return None
    # O código da pendência é vocabulário fechado (ex.: "COB2:QUEM", "V4_ALERTA:sin-...:adult"); o CONTEXTO da
    # pendência (`coleta_contexto`) nunca vai ao modelo nem ao log.
    return PendenteDoRoteador(dona, codigo[:60])


def ultimas(historico: Sequence[Mapping[str, Any]] | None, maximo: int) -> list[dict[str, str]]:
    return [dict(m) for m in (historico or [])][-maximo:]


__all__ = [
    "FALHA_JSON",
    "FALHA_MODELO",
    "FALHA_OCUPADO",
    "FALHA_PRAZO",
    "ROTEADOR_PROMPT_VERSION",
    "TRAVA_ALARME",
    "TRAVA_DADOS",
    "TRAVA_RISCO",
    "DecisaoDoRoteador",
    "PendenteDoRoteador",
    "campos_do_log",
    "habilidades_da_helena_atual",
    "ler_decisao",
    "montar_prompt",
    "pendente_do_estado",
    "rotulo_da_conversa",
    "trava_de_entrada",
    "ultimas",
]
