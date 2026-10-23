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

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from maezo.agents.helena.historico import historico_em_texto
from maezo.agents.helena.roteador_contrato import (
    CONFIANCAS,
    DUVIDA,
    HABILIDADES,
    ContratoDoRoteador,
)
from maezo.conhecimento import saude_gerativa as _sg
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.prompt_format import render_untrusted_block

ROTEADOR_PROMPT_VERSION: Final[str] = "roteador-v6"
NL: Final[str] = chr(10)

#: Rótulos de trava (os ids do contrato) e de falha do roteador, para o log.
TRAVA_ALARME: Final[str] = "alarme_pela_tabela"
TRAVA_RISCO: Final[str] = "risco_psicossocial"
TRAVA_DADOS: Final[str] = "dados_pessoais"
FALHA_PRAZO: Final[str] = "prazo"
FALHA_MODELO: Final[str] = "modelo"
FALHA_JSON: Final[str] = "json_ilegivel"
FALHA_OCUPADO: Final[str] = "ocupado"
#: F2: a pergunta "X ou Y?" que o roteador deixou pendente (o código leva as duas habilidades).
PREFIXO_DUVIDA: Final[str] = "ROT_DUVIDA:"

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
    #: Só em "duvida": a habilidade mais provável (a primeira opção da pergunta "X ou Y?").
    provavel: str | None = None

    @property
    def pergunta_na_duvida(self) -> bool:
        """F2: a pergunta "X ou Y?" no lugar de uma habilidade — "duvida", ou confiança baixa com uma
        alternativa diferente."""
        if self.habilidade == DUVIDA:
            return True
        return self.confianca == "baixa" and self.alternativa not in (None, self.habilidade)

    def opcoes_da_duvida(self) -> tuple[str, str] | None:
        """As duas habilidades da pergunta "X ou Y?", ou `None` quando não há duas diferentes."""
        if not self.pergunta_na_duvida:
            return None
        x = self.provavel if self.habilidade == DUVIDA else self.habilidade
        y = self.alternativa
        if x is None or y is None or x == y:
            return None
        return x, y


def _instrucoes(contrato: ContratoDoRoteador) -> str:
    """roteador-v3 (21/10/2026): prompt ENXUTO — uma linha por habilidade (o `resumo` do contrato), a regra da
    pendencia e a saida JSON curta. Medido com mensagens sinteticas antes de ligar (ADR-0066)."""
    linhas = [
        "Voce e' o roteador de um assistente de plano de saude no WhatsApp. Escolha UMA habilidade para a "
        "MENSAGEM ATUAL:",
        *(f"{h.id}: {h.resumo}" for h in contrato.habilidades),
        "A PENDENTE e' a pergunta que o assistente deixou: se a mensagem responde a ela, escolha a "
        "habilidade dela e p=true; se mudou de assunto, escolha o assunto novo.",
        "Se a PENDENTE e' 'duvida' (o assistente perguntou 'X ou Y?'), escolha uma das duas do codigo e "
        "p=true.",
        "Depois de uma resposta de cobertura: 'faltou algum?', outra pessoa ou outro exame continuam em "
        "cobertura; agradecimento ou 'ok' e' conversa.",
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
            DUVIDA,
            "baixa",
            alternativa if alternativa != provavel else None,
            dado.get("responde_a_pendencia") is True,
            provavel=provavel,
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
        or (
            trava is None
            and decisao is not None
            and decisao.habilidade == DUVIDA
            and (decisao.provavel in atual or decisao.alternativa in atual)
        )
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
    if codigo.startswith(PREFIXO_DUVIDA):
        return PendenteDoRoteador(DUVIDA, codigo[:60])
    dona = dona_da_pendencia(codigo or None)
    if dona is None:
        return None
    # O código da pendência é vocabulário fechado (ex.: "COB2:QUEM", "V4_ALERTA:sin-...:adult"); o CONTEXTO da
    # pendência (`coleta_contexto`) nunca vai ao modelo nem ao log.
    return PendenteDoRoteador(dona, codigo[:60])


def ultimas(historico: Sequence[Mapping[str, Any]] | None, maximo: int) -> list[dict[str, str]]:
    return [dict(m) for m in (historico or [])][-maximo:]


# --- O roteador DENTRO do turno (metodos do HelenaGraph, movidos SEM MUDANCA do graph.py; ADR-0067 §5) ---
if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph


def _roteador_chave(self: HelenaGraph, state: Mapping[str, Any]) -> tuple[str, str]:
    return (str(state.get("conversation_id") or ""), str(state.get("message_ref") or ""))


async def _roteador_decidir(
    self: HelenaGraph, state: Mapping[str, Any], pendente: Any
) -> tuple[Any, str | None, int]:
    from maezo.agents.helena import roteador as _rt

    contrato = self._roteador_contrato
    historico = state.get("historico_conversa") if self._historico_enabled else None
    prompt = _rt.montar_prompt(
        contrato,
        bloco_historico=(
            render_untrusted_block(
                "historico_conversa",
                historico_em_texto(_rt.ultimas(historico, contrato.maximo_de_mensagens)),
            )
            if historico
            else ""
        ),
        bloco_mensagem=render_untrusted_block("message_body", str(state.get("message_body") or "")),
        pendente=pendente,
    )
    if self._roteador_em_curso >= self._roteador_limite:
        # Limite de concorrencia cheio: o turno segue sem o roteador (nunca fila, nunca espera).
        return None, _rt.FALHA_OCUPADO, 0
    self._roteador_em_curso += 1
    inicio = time.monotonic()
    try:
        # O prazo REAL e' o tempo de leitura do provedor proprio (a thread volta nele); este `wait_for` e'
        # so'
        # a rede de seguranca, um pouco acima.
        bruto = await asyncio.wait_for(
            self._roteador_llm.generate(
                prompt,
                phi=True,
                agent_id="helena",
                tenant_id=str(state.get("tenant_id") or ""),
                task_kind="task_default",
            ),
            timeout=contrato.prazo_s + 1.0,
        )
    except PROGRAMMING_ERRORS:
        raise
    except TimeoutError:
        return None, _rt.FALHA_PRAZO, int((time.monotonic() - inicio) * 1000)
    except EXTERNAL_DEPENDENCY_FAILURES:
        ms_falha = int((time.monotonic() - inicio) * 1000)
        return (
            None,
            (_rt.FALHA_PRAZO if ms_falha >= contrato.prazo_s * 1000 else _rt.FALHA_MODELO),
            ms_falha,
        )
    finally:
        self._roteador_em_curso -= 1
    ms = int((time.monotonic() - inicio) * 1000)
    decisao = _rt.ler_decisao(bruto)
    return decisao, (None if decisao is not None else _rt.FALHA_JSON), ms


def _roteador_entrada(self: HelenaGraph, state: Mapping[str, Any]) -> tuple[str | None, Any, str | None]:
    """A pendencia de antes, a pendencia para o roteador e a trava de entrada deterministica."""
    from maezo.agents.helena import roteador as _rt

    antes = str(state.get("coleta_pendente") or "") or None
    pendente = _rt.pendente_do_estado(antes, self._roteador_contrato.dona_da_pendencia)
    trava = _rt.trava_de_entrada(
        str(state.get("message_body") or ""),
        alarme=lambda t: self._roteador_alarme(t, antes, self._falas_da_pessoa(state)),
        risco=lambda t: _sg.tem_risco(_plano(t)),
    )
    return antes, pendente, trava


def _roteador_guardar(
    self: HelenaGraph, state: Mapping[str, Any], registro: tuple[Any, Any, str | None, str | None]
) -> None:
    if len(self._roteador_em_voo) >= 256:
        velha = next(iter(self._roteador_em_voo))
        self._roteador_em_voo.pop(velha)[0].cancel()
    self._roteador_em_voo[self._roteador_chave(state)] = registro


async def _classify_com_sombra(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    # So' codigo deterministico aqui (sem dependencia injetada): a chamada ao modelo roda na tarefa
    # paralela, cuja falha vira o campo `falha` do log.
    antes, pendente, trava = self._roteador_entrada(state)
    tarefa = asyncio.ensure_future(self._roteador_decidir(dict(state), pendente))
    self._roteador_guardar(state, (tarefa, pendente, trava, antes))
    return await self.classify(state)


async def _classify_com_roteador(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """F2: `ativo` decide pelo roteador SO' nas conversas da allowlist dele (e da saude gerativa);
    fora dela, sombra."""
    conversa = state.get("conversation_id")
    if (
        self._roteador_modo == "ativo"
        and isinstance(conversa, str)
        and conversa in self._roteador_conversas
        and self._gerativa_para(state)
    ):
        return await self._classify_ativo(state)
    return await self._classify_com_sombra(state)


async def _classify_ativo(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """F2 (ADR-0066): travas de entrada -> roteador -> UMA habilidade.

    - Trava (alarme, risco, dados pessoais) ou roteador sem decisao: o caminho de sempre, com
      o estado INTEIRO (as portas de seguranca de hoje rodam como sempre).
    - A pergunta pendente so' vale quando o roteador diz que a mensagem RESPONDE a ela; senao ela e'
      descartada antes da habilidade (a pendencia nunca sequestra a mensagem).
    - "X ou Y?" na duvida; cortesia sem pendencia = resposta curta; cobertura = o aviso unico; as
      demais habilidades usam o codigo de hoje com a decisao do roteador valendo sobre as regras de
      cobertura (`_HABILIDADE_DO_TURNO`).
    """
    from maezo.agents.helena import roteador as _rt

    if state.get("error") or self._seguimento_com_o_lucas(state) is not None:
        # Falha do proprio turno (`receive`) ou conversa em seguimento com o Lucas: o caminho de sempre.
        return await self.classify(state)
    antes, pendente, trava = self._roteador_entrada(state)
    decisao, falha, ms = await self._roteador_decidir(dict(state), pendente)
    feita: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    feita.set_result((decisao, falha, ms))
    self._roteador_guardar(state, (feita, pendente, trava, antes))
    duvida_antes = bool(antes and antes.startswith(_rt.PREFIXO_DUVIDA))
    if trava is not None or decisao is None:
        logger.info(
            "helena_roteador_caminho_de_sempre", motivo="trava" if trava else (falha or "sem_decisao")
        )
        st_sempre: HelenaState = state
        if duvida_antes:
            st_sempre = cast(HelenaState, {**dict(state), "coleta_pendente": None, "coleta_contexto": ""})
        resultado_sempre = self._sem_pendencia_velha(await self.classify(st_sempre), duvida_antes)
        return {**resultado_sempre, "habilidade_roteador": None}
    texto = str(state.get("message_body") or "")
    opcoes = decisao.opcoes_da_duvida()
    if opcoes is not None:
        x, y = opcoes
        pergunta = self._roteador_contrato.texto_da_duvida.format(
            x=self._roteador_contrato.habilidade(x).titulo, y=self._roteador_contrato.habilidade(y).titulo
        )
        logger.info("helena_roteador_duvida", x=x, y=y)
        return self._base_texto_fixo(
            self._update_sem_modelo(state), pergunta, pendente=f"{_rt.PREFIXO_DUVIDA}{x}:{y}"
        )
    habilidade = decisao.habilidade if decisao.habilidade != "duvida" else (decisao.provavel or "conversa")
    mantem = bool(antes) and decisao.responde_a_pendencia and not duvida_antes
    if (
        antes
        and not duvida_antes
        and self._roteador_contrato.dona_da_pendencia(antes) == "sintoma"
        and habilidade in ("saude", "sintoma")
    ):
        # DL-0154: com o sintoma EM CURSO (pergunta de alerta, opcoes ou oferta pendentes), a fala de
        # saude continua o MESMO assunto ("eu dormi de mau jeito"): a pendencia fica e o sintoma decide
        # (antes, a pendencia caia e a pergunta de alerta recomecava).
        mantem = True
        habilidade = "sintoma"
    if (
        antes
        and not mantem
        and not duvida_antes
        and habilidade in ("conversa", "equipe")
        and _lc.e_reclamacao(texto)
        and not (_pedido_explicito_com_verbo(texto) or _pedido_explicito_de_pessoa(texto))
    ):
        # Reclamacao sobre a resposta anterior ("ue, como assim?", "voce falou outra coisa") pertence a
        # quem fez a pergunta pendente: a pendencia fica e a habilidade dona trata (desculpa curta e de
        # novo).
        mantem = True
        habilidade = self._roteador_contrato.dona_da_pendencia(antes) or habilidade
    dona = self._roteador_contrato.dona_da_pendencia(antes) if antes and not duvida_antes else None
    if (
        dona is not None
        and (mantem or _lc.ler_resposta(texto) is not None)
        and not (_pedido_explicito_com_verbo(texto) or _pedido_explicito_de_pessoa(texto))
    ):
        # Contrato (ADR-0066): a resposta a pergunta pendente e' da habilidade DONA dela. Roteiro de
        # 22/10/2026: "nao" a oferta da equipe da cobertura foi para `equipe` (p=true) e "deixa pra la'"
        # para `conversa`; a resposta curta (o leitor que ja' existe, `ler_resposta`) fica com a dona.
        mantem = True
        habilidade = dona
    st: HelenaState = (
        state
        if mantem or not antes
        else cast(HelenaState, {**dict(state), "coleta_pendente": None, "coleta_contexto": ""})
    )
    logger.info(
        "helena_roteador_decidiu",
        habilidade=habilidade,
        confianca=decisao.confianca,
        pendencia_mantida=mantem,
        ms=ms,
    )
    if habilidade == "cobertura":
        # COBERTURA FORA (dono, 23/10/2026, DL-0154): o aviso unico, sem consulta e sem oferta da equipe.
        return {**self._texto_da_cobertura_fora(state), "habilidade_roteador": habilidade}
    if habilidade == "conversa" and not mantem and (_lc.e_cortesia(texto) or _lc.e_cumprimento(texto)):
        cortesia = self._base_texto_fixo(self._update_sem_modelo(state), _lc.resposta_de_cortesia(texto))
        return {**cortesia, "habilidade_roteador": habilidade}
    marca = _HABILIDADE_DO_TURNO.set(habilidade)
    try:
        resultado = await self.classify(st)
    finally:
        _HABILIDADE_DO_TURNO.reset(marca)
    return {
        **self._sem_pendencia_velha(resultado, bool(antes) and not mantem),
        "habilidade_roteador": habilidade,
    }


def _e_texto_do_roteador(self: HelenaGraph, texto: object) -> bool:
    if self._roteador_modo != "ativo" or not isinstance(texto, str) or self._roteador_contrato is None:
        return False
    inicio_da_duvida = self._roteador_contrato.texto_da_duvida.split("{x}")[0]
    return texto in (_lc.TEXTO_DE_NADA, _lc.TEXTO_COMBINADO, _lc.TEXTO_SAUDACAO) or texto.startswith(
        inicio_da_duvida
    )


def _sem_pendencia_velha(update: dict[str, Any], descartou: bool) -> dict[str, Any]:
    """A pendencia descartada pelo roteador nao pode sobreviver no estado (o grafo mescla o update)."""
    if descartou and "coleta_pendente" not in update:
        update = {**update, "coleta_pendente": None, "coleta_contexto": ""}
    return update


async def _respond_com_sombra(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    saida = await self.respond(state)
    em_voo = self._roteador_em_voo.pop(self._roteador_chave(state), None)
    if em_voo is None:
        return saida
    from maezo.agents.helena import roteador as _rt

    tarefa, pendente, trava, antes = em_voo
    final = {**dict(state), **saida}
    atual = _rt.habilidades_da_helena_atual(
        final, pendente_antes=antes, dona_da_pendencia=self._roteador_contrato.dona_da_pendencia
    )
    conversa = _rt.rotulo_da_conversa(str(state.get("conversation_id") or ""))

    def registrar(feita: Any) -> None:
        if feita.cancelled():
            decisao, falha, ms = None, "cancelado", 0
        elif feita.exception() is not None:
            decisao, falha, ms = None, type(feita.exception()).__name__, 0
        else:
            decisao, falha, ms = feita.result()
        c = _rt.campos_do_log(
            decisao, falha=falha, trava=trava, atual=atual, pendente=pendente, ms=ms, conversa=conversa
        )
        # Campos EXPLICITOS (a varredura de chaves do log ve cada nome; nenhum `**mapping`).
        logger.info(
            "helena_roteador_sombra",
            habilidade=c["habilidade"],
            confianca=c["confianca"],
            alternativa=c["alternativa"],
            responde_a_pendencia=c["responde_a_pendencia"],
            trava=c["trava"],
            atual=c["atual"],
            concorda=c["concorda"],
            pendencia=c["pendencia"],
            falha=c["falha"],
            ms=c["ms"],
            conversa=c["conversa"],
        )

    tarefa.add_done_callback(registrar)
    return saida


__all__ = [
    "FALHA_JSON",
    "FALHA_MODELO",
    "FALHA_OCUPADO",
    "FALHA_PRAZO",
    "PREFIXO_DUVIDA",
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


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro; o graph importa este
# modulo logo antes da classe).
from maezo.agents.helena.graph import (  # noqa: E402
    _HABILIDADE_DO_TURNO,
    HelenaState,
    _pedido_explicito_com_verbo,
    _pedido_explicito_de_pessoa,
    _plano,
    logger,
)
