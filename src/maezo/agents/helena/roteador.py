"""ROTEADOR de habilidades da Helena (ADR-0066, DL-0134) — F1: MODO SOMBRA.

Em sombra (`MAEZO_HELENA_ROTEADOR=sombra`) o roteador e as travas de entrada rodam EM PARALELO à Helena
atual e só REGISTRAM a decisão (log `helena_roteador_sombra`), sem mudar resposta, estado, caso ou envio. O
log leva só vocabulário fechado (habilidade, confiança, trava, pendência), nunca texto da pessoa nem do
modelo.

Peças (todas puras, testáveis sem grafo):
- o prompt e a leitura da decisao sao os do CONTRATO v7 (`roteador_v7.py`; etapa 7 do Plano de ajustes v2):
  vocabulario fechado em todo campo, UMA re-pergunta, nada adivinhado. `decisao_do_v7` a traz para o
  vocabulario interno das habilidades (`roteador_contrato`).
- `trava_de_entrada`: qual trava fixa decidiria o turno pelos sinais DETERMINÍSTICOS do texto (alarme, risco,
  dados pessoais), na ordem do contrato.
- `habilidades_da_helena_atual`: a decisão que a Helena atual tomou, no vocabulário das habilidades, a partir
do
  estado final do turno (para a concordância).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from maezo.agents.helena import roteador_v7 as _rv7
from maezo.agents.helena.alarme_deterministico import POPULACAO_DO_ALARME, alarme_no_relato
from maezo.agents.helena.habilidades.atendimento_humano.textos import TEXTOS_FIXOS as TEXTOS_FIXOS_DA_OFERTA
from maezo.agents.helena.habilidades.conversa import app as _app
from maezo.agents.helena.habilidades.conversa import assuntos as _assuntos
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from maezo.agents.helena.habilidades.conversa import reexplicar as _rx
from maezo.agents.helena.historico import historico_em_texto
from maezo.agents.helena.roteador_contrato import (
    DUVIDA,
)
from maezo.conhecimento import saude_gerativa as _sg
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.prompt_format import render_untrusted_block

#: Plano de ajustes v2, etapa 7: o contrato v7 (saida de vocabulario fechado, `roteador_v7.py`).
ROTEADOR_PROMPT_VERSION: Final[str] = _rv7.PROMPT_VERSION
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
    #: Contrato v7 (etapa 7): a decisao inteira do roteador (sim/nao no contexto, alarme, recusa...).
    v7: _rv7.DecisaoV7 | None = None

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


def decisao_do_v7(
    v7: _rv7.DecisaoV7, tipo_pendente: str | None, dona_da_pendencia: str | None
) -> DecisaoDoRoteador:
    """A decisao v7 no vocabulario do contrato interno (as habilidades de `roteador_contrato`), com a v7
    inteira junto. "Responde a pendencia" sai do proprio modelo pelo TIPO da pergunta: sim/nao respondido,
    opcao escolhida, ou a continuacao do assunto da dona da pendencia."""
    habilidade = v7.habilidade_interna
    responde = (
        (tipo_pendente == "sim_nao" and v7.sim_nao != "nao_se_aplica")
        or (tipo_pendente == "opcoes" and v7.opcao != "nenhuma")
        or (tipo_pendente == "aberta" and v7.continuacao)
        or (dona_da_pendencia is not None and dona_da_pendencia == habilidade and v7.continuacao)
    )
    if v7.precisa_esclarecer and len(v7.pedidos) >= 2:
        internas = list(
            dict.fromkeys("saude" if p == "saude" else _rv7.DecisaoV7.interna_de(p) for p in v7.pedidos)
        )
        if len(internas) >= 2:
            # Dois pedidos e o modelo diz que precisa perguntar: "voce quer saber sobre X ou sobre Y?".
            return DecisaoDoRoteador(DUVIDA, "baixa", internas[1], responde, provavel=internas[0], v7=v7)
    return DecisaoDoRoteador(habilidade, "alta", None, responde, v7=v7)


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


def _fatos_do_roteador(state: Mapping[str, Any]) -> dict[str, Any]:
    """Os FATOS do prompt v7: so' vocabulario fechado (booleano e o tipo do caso aberto), nunca texto. A
    identificacao da pessoa NAO vai (o roteador escolhe a habilidade; quem precisa dela e' a habilidade)."""
    return {
        "alarme_ja_tratado": _alarme_ja_tratado(state),
        "caso_aberto": _caso_aberto_no_historico(state) or "nenhum",
    }


async def _roteador_decidir(
    self: HelenaGraph, state: Mapping[str, Any], pendente: Any
) -> tuple[Any, str | None, int]:
    from maezo.agents.helena import roteador as _rt

    contrato = self._roteador_contrato
    historico = state.get("historico_conversa") if self._historico_enabled else None
    codigo = str(state.get("coleta_pendente") or "") or None
    tipo_pendente = _rv7.tipo_da_pendente(codigo)
    falas_helena = _falas_da_helena(state)

    prompt = _rv7.montar_prompt(
        instrucoes=self._roteador_instrucoes,
        bloco_conversa=(
            render_untrusted_block(
                "historico_conversa",
                historico_em_texto(_rt.ultimas(historico, contrato.maximo_de_mensagens)),
            )
            if historico
            else ""
        ),
        bloco_mensagens=render_untrusted_block("message_body", str(state.get("message_body") or "")),
        tipo_pendente=tipo_pendente,
        # O texto da pergunta e' a ULTIMA fala da propria Helena (o historico curto ja' a guarda).
        texto_pendente=(falas_helena[-1] if falas_helena and tipo_pendente else None),
        fatos=_fatos_do_roteador(state),
    )

    if self._roteador_em_curso >= self._roteador_limite:
        # Limite de concorrencia cheio: o turno segue sem o roteador (nunca fila, nunca espera).
        return None, _rt.FALHA_OCUPADO, 0
    self._roteador_em_curso += 1
    inicio = time.monotonic()
    prazo_s = self._roteador_prazo_s
    v7: _rv7.DecisaoV7 | None = None
    ruins: list[str] = []
    try:
        for tentativa in range(2):
            restante = prazo_s - (time.monotonic() - inicio)
            if tentativa:
                if v7 is not None or restante <= 1.0:
                    break
                # UMA re-pergunta (contrato v7): o aviso diz SO' os campos invalidos, nunca o texto do modelo.
                logger.info("helena_roteador_reenvio", campos=sorted(set(ruins))[:6])
                prompt = NL.join([prompt, _rv7.aviso_de_reenvio(ruins)])
            # O prazo REAL e' o tempo de leitura do provedor proprio (a thread volta nele); este `wait_for`
            # e' so' a rede de seguranca, um pouco acima.
            bruto = await asyncio.wait_for(
                self._roteador_llm.generate(
                    prompt,
                    phi=True,
                    agent_id="helena",
                    tenant_id=str(state.get("tenant_id") or ""),
                    task_kind="task_default",
                ),
                timeout=restante + 1.0,
            )
            v7, ruins = _rv7.ler_decisao(bruto)
    except PROGRAMMING_ERRORS:
        raise
    except TimeoutError:
        logger.info("helena_roteador_falhou", falha=_rt.FALHA_PRAZO, causa="TimeoutError")
        return None, _rt.FALHA_PRAZO, int((time.monotonic() - inicio) * 1000)
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        ms_falha = int((time.monotonic() - inicio) * 1000)
        # Rodada de 25/10/2026 (item 8): as 4 falhas "modelo" do dia eram PRAZO de leitura do provedor
        # (ReadTimeoutError a ~5-6 s, antes do prazo cheio): o tipo do erro decide, nao so' o tempo.
        causa = type(exc).__name__
        prazo = ms_falha >= prazo_s * 1000 or _e_erro_de_prazo(causa)
        falha_externa = _rt.FALHA_PRAZO if prazo else _rt.FALHA_MODELO
        logger.info("helena_roteador_falhou", falha=falha_externa, causa=causa, ms=ms_falha)
        return None, falha_externa, ms_falha
    finally:
        self._roteador_em_curso -= 1
    ms = int((time.monotonic() - inicio) * 1000)
    if v7 is None:
        # So' os NOMES dos campos invalidos (nunca o texto do modelo): JSON ilegivel x campo fora do enum.
        logger.info("helena_roteador_falhou", falha=_rt.FALHA_JSON, campos=sorted(set(ruins))[:6], ms=ms)
        return None, _rt.FALHA_JSON, ms
    return _rt.decisao_do_v7(v7, tipo_pendente, contrato.dona_da_pendencia(codigo)), None, ms


def _e_erro_de_prazo(nome_da_classe: str) -> bool:
    """O erro do provedor e' de PRAZO (ReadTimeoutError, ConnectTimeoutError, ReadTimeout...), pelo NOME da
    classe (so' o token de classe sai do `except`, nunca o texto do erro)."""
    return "timeout" in nome_da_classe.lower()


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
    falha: str | None
    if trava is not None:
        # Rodada de 24/10/2026: com trava (alarme, risco, dados pessoais) a TABELA responde primeiro: o turno
        # nao espera o modelo do roteador (era ate' 4 s a mais antes do 192).
        decisao, falha, ms = None, "trava", 0
        alarme = alarme_no_relato(_plano(str(state.get("message_body") or "")))
        if trava == _rt.TRAVA_ALARME and alarme is not None:
            # Alarme da tabela deterministica: o escalonamento SEM esperar nem o classificador.
            logger.warning("helena_alarme_deterministico", node="classify", categoria=alarme, sem_modelo=True)
            update = self._update_sem_modelo(state, POPULACAO_DO_ALARME.get(alarme, "adult"))
            self._expirar_pendencias_do_turno(state, update)
            update["intent"] = "symptom"
            update["next_kind"] = "escalate"
            update["escalation_motivo"] = "red_flag_clinico"
            update["escalation_severidade"] = "grave"
            return {**update, "habilidade_roteador": None}
    else:
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
    # Contrato v7: a leitura do modelo vale no turno inteiro (as habilidades leem sim/nao, opcao e recusa
    # por ela, no lugar dos leitores de palavra).
    marca_v7 = _rv7.LEITURA_DO_TURNO.set(decisao.v7)
    try:
        resultado = await _classify_ativo_decidido(self, state, decisao, antes, ms)
    finally:
        _rv7.LEITURA_DO_TURNO.reset(marca_v7)
    return _com_assuntos_abertos(self, state, decisao.v7, resultado)


def _com_assuntos_abertos(
    self: HelenaGraph, state: HelenaState, v7: _rv7.DecisaoV7, resultado: dict[str, Any]
) -> dict[str, Any]:
    """Etapa 9 (assuntos abertos): a lista do turno a partir dos `topic_events`, e a retomada UMA vez por
    assunto — depois da passagem ao Lucas (o despachante manda depois da resposta dele) ou no lugar da
    cortesia curta ("ok, obrigado" depois de um aviso)."""
    lista = _assuntos.depois_do_turno(_assuntos.validos(state.get("assuntos_abertos")), v7)
    saida = dict(resultado)
    modelo = self._textos[_assuntos.CHAVE_RETOMADA]
    if v7.skill.startswith("lucas_") and (
        isinstance(resultado.get("handoff"), Mapping) or resultado.get("intent") == "cobranca"
    ):
        topico = _assuntos.a_retomar(lista, exceto=v7.skill)
        if topico is not None:
            saida["texto_de_retomada"] = _assuntos.texto_de_retomada(modelo, topico)
            lista = _assuntos.marcar_retomado(lista, topico)
    elif (
        resultado.get("habilidade_roteador") == "conversa"
        and v7.tipo == "nenhum"
        and not v7.pedidos
        and not resultado.get("escalation_motivo")
        and not resultado.get("coleta_pendente")
        and _lc.e_cortesia(str(state.get("message_body") or ""))
    ):
        topico = _assuntos.a_retomar(lista, exceto=None)
        if topico is not None:
            logger.info("helena_assunto_retomado", topico=topico)
            saida = {
                **self._base_texto_fixo(
                    self._update_sem_modelo(state), _assuntos.texto_de_retomada(modelo, topico)
                ),
                "habilidade_roteador": "conversa",
            }
            lista = _assuntos.marcar_retomado(lista, topico)
    saida["assuntos_abertos"] = lista or None
    return saida


async def _classify_ativo_decidido(
    self: HelenaGraph, state: HelenaState, decisao: Any, antes: str | None, ms: int
) -> dict[str, Any]:
    """O turno com a decisao do roteador (contrato v7, DL-0154 etapa 7). A ordem:

    1. Alarme dito pelo MODELO: "claro" so' com o sinal no vocabulario da tabela (validado em codigo) -> 192
       na hora, como o alarme da tabela; o mesmo alarme ja' tratado nao leva um segundo 192 (trava). Sinal
       sem clareza ou fora da tabela -> a habilidade de sintoma faz a pergunta de alerta. O modelo so'
       ACRESCENTA urgencia: a tabela e os lexicos ja' rodaram antes (trava de entrada).
    2. "X ou Y?" quando o modelo diz que precisa perguntar entre dois pedidos.
    3. Recusa da equipe (fora do sintoma em curso): encerra P2/P3; P1 nunca se encerra por recusa.
    4. A pendencia so' fica quando o modelo diz que a mensagem responde a ela (ou corrige o que foi dito).
    """
    from maezo.agents.helena import roteador as _rt

    v7 = cast("_rv7.DecisaoV7", decisao.v7)
    duvida_antes = bool(antes and antes.startswith(_rt.PREFIXO_DUVIDA))
    dona = self._roteador_contrato.dona_da_pendencia(antes) if antes and not duvida_antes else None
    alarme = _alarme_do_modelo(self, state, v7)
    if alarme == "claro":
        return {**await _escalar_alarme_do_modelo(self, state, v7), "habilidade_roteador": "sintoma"}
    texto = str(state.get("message_body") or "")
    if (oferta := _oferta.responder(self, state)) is not None:
        # Avaliacao r5 (25/10/2026): o "sim" a oferta da equipe abre o caso (antes, a habilidade `equipe`
        # fechava a intencao do classificador em `information` e nada abria); o "nao" recebe a recusa curta.
        return {**oferta, "habilidade_roteador": "equipe"}
    if alarme is None and not antes:
        fixo = await _texto_fixo_de_conversa(self, state, texto)
        if fixo is not None:
            return fixo
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
        v7.recusa_equipe
        and dona not in ("sintoma", "saude")
        and not str(antes or "").startswith(PENDENTE_ENCERRAR_CASO)
        and not (_pedido_explicito_com_verbo(texto) or _pedido_explicito_de_pessoa(texto))
    ):
        # Recusa da equipe ("nao, deixa, desisto", "ja' resolvi"), lida pelo modelo: encerra os casos P2/P3
        # da conversa; caso P1/grave nunca se encerra por recusa (o texto do caso grave, com 192).
        return {**await _recusa_da_equipe(self, state), "habilidade_roteador": "equipe"}
    if alarme == "incerto" and habilidade in ("saude", "sintoma", "conversa"):
        # Sinal de alarme sem clareza (ou fora da tabela): a habilidade de sintoma faz a pergunta de alerta
        # (o pedido de pessoa, a cobranca e o cancelamento seguem sempre ouvidos).
        habilidade = "sintoma"
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
        and (v7.correcao or v7.continuacao)
        and not (_pedido_explicito_com_verbo(texto) or _pedido_explicito_de_pessoa(texto))
    ):
        # Reclamacao sobre a resposta anterior ("ue, como assim?", "voce falou outra coisa") pertence a
        # quem fez a pergunta pendente: a pendencia fica e a habilidade dona trata (desculpa curta e de
        # novo).
        mantem = True
        habilidade = self._roteador_contrato.dona_da_pendencia(antes) or habilidade
    if (
        dona is not None
        and (mantem or v7.sim_nao != "nao_se_aplica" or v7.opcao != "nenhuma" or v7.correcao)
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
    if habilidade == "cobertura" and _lc.ler_resposta(texto) in ("nao", "encerrar"):
        # Avaliacao r5 (25/10/2026): "esquece, nao precisa mais" depois do aviso da cobertura ia para a
        # cobertura e repetia o aviso (e o loop abria caso). A recusa curta do assunto so' e' reconhecida.
        cortesia = self._base_texto_fixo(self._update_sem_modelo(state), _lc.resposta_de_cortesia(texto))
        return {**cortesia, "habilidade_roteador": "conversa"}
    if habilidade == "cobertura":
        # COBERTURA FORA (dono, 23/10/2026, DL-0154): o aviso unico, sem consulta e sem oferta da equipe.
        return {**self._texto_da_cobertura_fora(state), "habilidade_roteador": habilidade}
    if (
        habilidade == "conversa"
        and not mantem
        and v7.tipo == "nenhum"
        and len(v7.pedidos) <= 1
        and (
            _lc.e_cortesia(texto)
            or _lc.e_cumprimento(texto)
            or (len(_plano(texto).split()) <= 2 and bool(_falas_da_helena(state)))
        )
    ):
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


async def _texto_fixo_de_conversa(self: HelenaGraph, state: HelenaState, texto: str) -> dict[str, Any] | None:
    """Teste do dono (25/10/2026), sem pergunta pendente e sem alarme, qualquer que seja a habilidade do
    roteador: "nao entendi" reexplica a ULTIMA resposta (`reexplicar.py`) e "onde baixo o aplicativo?" recebe
    o texto do pacote (sem link). `None` = o caminho de sempre."""
    if _pedido_explicito_com_verbo(texto) or _pedido_explicito_de_pessoa(texto):
        return None
    if _app.pergunta_onde_baixar_o_app(texto):
        logger.info("helena_texto_do_app", node="classify")
        texto_app = self._textos[_app.CHAVE_APP_ONDE_BAIXAR]
        return {
            **self._base_texto_fixo(self._update_sem_modelo(state), texto_app),
            "habilidade_roteador": "conversa",
        }
    if not _rx.pede_reexplicacao(texto):
        return None
    reexplicado = await _rx.reexplicar(self, state)
    if reexplicado is None:
        return None
    tipo = reexplicado.pop("tipo", None)
    logger.info("helena_reexplicacao", node="classify", tipo=tipo)
    saida = self._base_texto_fixo(self._update_sem_modelo(state), str(reexplicado.pop("response_text")))
    return {**saida, **reexplicado, "habilidade_roteador": "conversa"}


def _alarme_do_modelo(self: HelenaGraph, state: HelenaState, v7: _rv7.DecisaoV7) -> str | None:
    """ "claro" (192 na hora), "incerto" (pergunta de alerta) ou `None` (sem sinal que conte).

    So' o sinal no VOCABULARIO DA TABELA vale como claro ("vomito" sozinho nao e'); o alarme ja' tratado
    nesta conversa, sem sinal NOVO, nao conta de novo (trava "sem segundo 192")."""
    sinais = [s.sinal for s in v7.sinais_atuais()]
    if not sinais:
        return None
    if _alarme_ja_tratado(state) and not self._sinal_do_modelo_e_novo(state, sinais):
        return None
    validos = [s for s in sinais if self._sinal_do_modelo_na_tabela(s)]
    claro = v7.confianca_alarme == "clear" and bool(validos)
    logger.info("helena_alarme_do_modelo", claro=claro, sinais=len(sinais), validos=len(validos))
    return "claro" if claro else "incerto"


async def _escalar_alarme_do_modelo(
    self: HelenaGraph, state: HelenaState, v7: _rv7.DecisaoV7
) -> dict[str, Any]:
    """O alarme claro dito pelo modelo e validado na tabela: o mesmo escalonamento do alarme da tabela
    (`red_flag_clinico` grave, 192), ou `risco_psicossocial` grave (CVV 188, 192) para o sinal de risco."""
    risco = any(_sg.tem_risco(_plano(s.sinal)) for s in v7.sinais_atuais())
    populacao = str(state.get("population") or "")
    update: dict[str, Any] = {
        "intent": "symptom",
        "population": "mental_health" if risco else (populacao if populacao not in ("", "none") else "adult"),
        "psychosocial_risk": risco,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "idade_anos": None,
        "idade_meses": None,
        "idade_gestacional_semanas": None,
    }
    self._expirar_pendencias_do_turno(state, update)
    update["next_kind"] = "escalate"
    update["escalation_motivo"] = "risco_psicossocial" if risco else "red_flag_clinico"
    update["escalation_severidade"] = "grave"
    logger.warning("helena_alarme_do_modelo_escala", node="classify", risco=risco)
    return update


async def _recusa_da_equipe(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """A recusa da equipe lida pelo roteador: casos P2/P3 abertos se encerram e a pessoa le' a confirmacao;
    caso grave aberto -> o texto do caso grave (nunca se encerra); sem caso -> o "combinado" curto."""
    update = self._update_sem_modelo(state)
    abertos = await self._casos_abertos_da_conversa(state) if self._base_saude else []
    graves = [c for c in abertos if caso_grave_para_encerrar(*await self._motivo_e_severidade(c))]
    logger.info("helena_recusa_de_equipe_pelo_roteador", caso_aberto=bool(abertos), grave=bool(graves))
    if graves:
        return self._base_texto_fixo(update, RESPOSTA_CASO_GRAVE_NAO_ENCERRA)
    if abertos:
        encerrado = await self._base_confirmacao_de_encerramento(state, update, "sim", leitura="sim")
        if encerrado is not None:
            return encerrado
    # Avaliacao r5 (25/10/2026): a recusa curta so' com oferta pendente; sem oferta nem caso, so' o
    # reconhecimento (antes, o paragrafo longo da recusa saia fora de contexto).
    texto = _oferta.texto_da_recusa(state, caso_aberto=bool(abertos))
    update.update({"coleta_pendente": None, "coleta_contexto": ""})
    return self._base_texto_fixo(update, texto)


def _e_texto_do_roteador(self: HelenaGraph, texto: object) -> bool:
    if isinstance(texto, str) and texto in TEXTOS_FIXOS_DA_OFERTA:
        # A oferta da equipe e a recusa dela (avaliacao r5): texto fixo, com ou sem o roteador ativo.
        return True
    if self._roteador_modo != "ativo" or not isinstance(texto, str) or self._roteador_contrato is None:
        return False
    inicio_da_duvida = self._roteador_contrato.texto_da_duvida.split("{x}")[0]
    retomadas = {
        _assuntos.texto_de_retomada(self._textos[_assuntos.CHAVE_RETOMADA], t)
        for t in _assuntos.NOMES_DOS_ASSUNTOS
    }
    return (
        texto in (_lc.TEXTO_DE_NADA, _lc.TEXTO_COMBINADO, _lc.TEXTO_SAUDACAO, RESPOSTA_RECUSA_DE_EQUIPE)
        or texto in retomadas
        or texto.startswith(inicio_da_duvida)
        or _rx.e_reexplicacao(texto)
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
    "decisao_do_v7",
    "pendente_do_estado",
    "rotulo_da_conversa",
    "trava_de_entrada",
    "ultimas",
]


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro; o graph importa este
# modulo logo antes da classe).
from maezo.agents.helena.graph import (  # noqa: E402
    _HABILIDADE_DO_TURNO,
    PENDENTE_ENCERRAR_CASO,
    RESPOSTA_CASO_GRAVE_NAO_ENCERRA,
    RESPOSTA_RECUSA_DE_EQUIPE,
    HelenaState,
    _alarme_ja_tratado,
    _caso_aberto_no_historico,
    _falas_da_helena,
    _pedido_explicito_com_verbo,
    _pedido_explicito_de_pessoa,
    _plano,
    caso_grave_para_encerrar,
    logger,
)
from maezo.agents.helena.habilidades.atendimento_humano import oferta as _oferta  # noqa: E402
