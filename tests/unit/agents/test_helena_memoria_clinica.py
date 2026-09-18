"""A memoria clinica entre turnos: quem e' o paciente sobrevive ao turno (Frente 2.1).

O DEFEITO, REPRODUZIDO TRES VEZES EM 13/09/2026. Um bebe de 11 meses foi triado pela tabela de
ADULTO. A mae disse a idade num turno e descreveu o sintoma no seguinte; `receive` zera toda saida
entre turnos (defesa T1.11 contra valor plantado), entao `population` voltou a `none` e o sintoma
caiu em `triage_redflag_adult`. E a resposta listou sinais de alerta de adulto e perguntou
"descreva melhor como VOCE esta se sentindo" a quem nao era o paciente.

Nao era um bug de extracao: o modelo extraiu a idade corretamente no turno em que ela foi dita. Era
a memoria desenhada estreita de proposito, que resolveu SEGURANCA e nao resolveu CONTINUIDADE.

O QUE ESTE ARQUIVO PROVA, e em que ordem de importancia:
  1. a tabela consultada no turno 2 e' a da crianca (o defeito medido, em RED/GREEN);
  2. a defesa T1.11 continua de pe' — memoria malformada, sem carimbo ou fora da janela NAO
     atravessa, e o que nao atravessa degrada para o comportamento de hoje, nunca para um paciente
     parcialmente lembrado;
  3. as cinco decisoes do documento, uma a uma, incluindo a que e' contraintuitiva: a informacao
     nova vence, EXCETO quando a nova e' ausencia.

O QUE ELE NAO PROVA: que o modelo obedece a instrucao de confirmar em voz alta. Isso e' uma
afirmacao sobre o LLM, e a licao de 13/09 e' explicita — proibir (ou mandar) no prompt nao segura.
O que se prova aqui e' que a frase CHEGA ao contexto do prompt; quem garante que ela apareca no
texto seria uma cerca de saida, e a Helena ja tem uma para o caminho inverso.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from maezo.agents.helena.graph import (
    MEMORIA_CLINICA_JANELA_HORAS,
    HelenaGraph,
    HelenaState,
    _frase_de_confirmacao,
    _fundir_memoria_clinica,
    _memoria_a_gravar,
    _memoria_clinica_valida,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

AGORA: Final[datetime] = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def memoria(**campos: Any) -> dict[str, Any]:
    """Uma memoria gravada AGORA, com os campos pedidos."""
    return {"gravado_em": AGORA.isoformat(), **campos}


# =================================================================================================
# 1. O defeito medido: a tabela do turno 2
# =================================================================================================


def test_a_populacao_do_turno_anterior_escolhe_a_tabela_do_turno_seguinte() -> None:
    """O caso de 13/09, reduzido ao seu nucleo.

    Turno 1: "meu bebe tem 11 meses" -> `population=pediatric`, `idade_meses=11`.
    Turno 2: "ele esta com febre" -> a extracao devolve `population="none"` e `idade_meses=None`,
    porque a mae nao repetiu a idade. Sem memoria, `_evaluate_dmn` escolhe `triage_redflag_adult`.
    """
    extracao_do_turno_2 = {
        "intent": "symptom",
        "population": "none",
        "sintoma_codigo": "febre",
        "idade_meses": None,
    }
    fundida, da_memoria = _fundir_memoria_clinica(
        extracao_do_turno_2, memoria(population="pediatric", idade_meses=11)
    )
    assert fundida["population"] == "pediatric", (
        "a populacao lembrada nao entrou na extracao — e' ela que escolhe a tabela em "
        "`_evaluate_dmn`, entao sem isto o bebe volta para triage_redflag_adult"
    )
    assert fundida["idade_meses"] == 11
    assert da_memoria == {"population": "pediatric", "idade_meses": 11}


def test_sem_memoria_o_comportamento_e_exatamente_o_de_hoje() -> None:
    """O RED do teste acima, e o contrato do desligamento.

    Com `memoria_clinica_enabled=False`, ou numa conversa nova, `classify` passa `None` — e o
    resultado tem de ser a extracao INTACTA, o mesmo objeto de comportamento que roda hoje em
    producao. Degradar para o conhecido e' o que torna a feature reversivel sem susto.
    """
    extracao = {"intent": "symptom", "population": "none", "idade_meses": None}
    fundida, da_memoria = _fundir_memoria_clinica(extracao, None)
    assert fundida == extracao
    assert da_memoria == {}
    assert fundida["population"] == "none", "sem memoria, o turno 2 volta a cair na tabela de adulto"


# =================================================================================================
# 2. A defesa T1.11 continua de pe'
# =================================================================================================


@pytest.mark.parametrize(
    ("bruta", "porque"),
    [
        ("pediatric", "string no lugar do mapa"),
        (["pediatric"], "lista no lugar do mapa"),
        ({"population": "pediatric"}, "sem carimbo de tempo — memoria sem relogio e' eterna"),
        ({"gravado_em": "ontem", "population": "pediatric"}, "carimbo que nao parseia"),
        ({"gravado_em": AGORA.isoformat(), "population": "crianca"}, "populacao fora do vocabulario"),
        ({"gravado_em": AGORA.isoformat(), "idade_meses": -1}, "idade negativa"),
        ({"gravado_em": AGORA.isoformat(), "idade_meses": "11"}, "idade como texto"),
        ({"gravado_em": AGORA.isoformat(), "idade_meses": True}, "bool, que e' int em Python"),
        ({"gravado_em": AGORA.isoformat()}, "carimbo sem nenhum campo clinico"),
    ],
)
def test_memoria_que_nao_se_pode_confiar_vira_none(bruta: Any, porque: str) -> None:
    """FALHA PARA `None`, NUNCA PARA UM VALOR PARCIAL.

    `None` degrada para o comportamento de hoje — cada turno comeca do zero —, que e' conhecido e
    ja roda em producao. Um valor parcialmente aceito seria um paciente parcialmente lembrado, que
    e' o modo de falha que esta frente existe para acabar.

    O caso do `bool` merece a linha: `True` e' instancia de `int` em Python e viraria idade 1 sem a
    recusa explicita — um bebe de um mes que ninguem mencionou.
    """
    assert _memoria_clinica_valida(bruta, agora=AGORA) is None, porque


def test_memoria_fora_da_janela_e_esquecida() -> None:
    """Janela de horas, decisao 2 do documento.

    Depois dela o quadro clinico pode ter mudado o bastante para que lembrar seja pior que
    perguntar de novo — e uma conversa de ontem nao e' a mesma conversa.
    """
    velha = {
        "gravado_em": (AGORA - timedelta(hours=MEMORIA_CLINICA_JANELA_HORAS + 0.1)).isoformat(),
        "population": "pediatric",
    }
    assert _memoria_clinica_valida(velha, agora=AGORA) is None

    recente = {
        "gravado_em": (AGORA - timedelta(hours=MEMORIA_CLINICA_JANELA_HORAS - 0.1)).isoformat(),
        "population": "pediatric",
    }
    assert (_memoria_clinica_valida(recente, agora=AGORA) or {}).get("population") == "pediatric"


def test_memoria_do_futuro_e_recusada() -> None:
    """Carimbo a frente do relogio nao e' memoria — e' valor plantado ou relogio quebrado.

    Aceita-la faria uma memoria "eterna" pela porta dos fundos: basta gravar uma data distante para
    que a janela nunca expire.
    """
    futura = {"gravado_em": (AGORA + timedelta(minutes=5)).isoformat(), "population": "pediatric"}
    assert _memoria_clinica_valida(futura, agora=AGORA) is None


def test_a_memoria_valida_so_deixa_passar_os_campos_previstos() -> None:
    """Um campo a mais na memoria nao atravessa.

    Sem isto, a memoria viraria um canal de passagem para qualquer chave que alguem gravasse — o
    oposto de uma memoria estreita por desenho, que e' a propriedade que a T1.11 comprou.
    """
    limpa = _memoria_clinica_valida(
        memoria(population="pediatric", idade_meses=11, sintoma_codigo="febre", error="x"),
        agora=AGORA,
    )
    assert limpa is not None
    assert set(limpa) == {"population", "idade_meses", "gravado_em", "confirmada"}, (
        "campo nao previsto atravessou a validacao"
    )


# =================================================================================================
# 3. As decisoes do documento
# =================================================================================================


def test_informacao_nova_vence_a_lembrada() -> None:
    """Decisao 3, primeira metade: afirmacao explicita em contrario troca a populacao.

    "na verdade e' pra mim mesma, tenho 34 anos" depois de ter falado do bebe.
    """
    fundida, da_memoria = _fundir_memoria_clinica(
        {"population": "adult", "idade_anos": 34},
        memoria(population="pediatric", idade_meses=11),
    )
    assert fundida["population"] == "adult"
    assert fundida["idade_anos"] == 34
    assert "population" not in da_memoria, "a populacao veio da mensagem, nao ha o que confirmar"


def test_ausencia_nao_apaga_o_que_a_conversa_sabia() -> None:
    """Decisao 3, segunda metade, e e' a contraintuitiva.

    NAO REPETIR "meu bebe" NAO APAGA O BEBE. `population="none"` e `idade=None` sao o que o modelo
    devolve quando a mensagem nao fala do assunto — nao sao afirmacoes de que o paciente mudou.
    Trata-las como troca faria a Helena esquecer a crianca no turno em que a mae so' diz "e agora?".
    """
    fundida, _ = _fundir_memoria_clinica(
        {"population": "none", "idade_meses": None, "idade_anos": None},
        memoria(population="pediatric", idade_meses=11),
    )
    assert fundida["population"] == "pediatric"
    assert fundida["idade_meses"] == 11


def test_um_turno_administrativo_no_meio_nao_apaga_a_memoria() -> None:
    """O corolario da decisao 3 na GRAVACAO, nao so' na leitura.

    "qual o telefone da central?" no meio de uma conversa sobre o bebe nao tem nada que valha
    lembrar — e sobrescrever a memoria com esse nada faria a Helena esquecer a crianca por causa de
    uma pergunta de cadastro.
    """
    anterior = memoria(population="pediatric", idade_meses=11)
    gravada = _memoria_a_gravar(
        {"intent": "information", "population": "none"}, anterior, agora=AGORA, confirmada=False
    )
    assert gravada == anterior


def test_o_que_foi_lembrado_num_turno_continua_lembrado_no_seguinte() -> None:
    """A memoria grava a extracao JA' FUNDIDA — a diferenca entre lembrar e ter memoria de um turno.

    Turno 3 sem a mae repetir nada: se a gravacao usasse so' o que a MENSAGEM trouxe, a memoria
    morreria no turno 2 e o defeito voltaria uma mensagem depois.
    """
    fundida, _ = _fundir_memoria_clinica(
        {"population": "none", "idade_meses": None}, memoria(population="pediatric", idade_meses=11)
    )
    gravada = _memoria_a_gravar(fundida, None, agora=AGORA, confirmada=True)
    assert gravada is not None
    assert gravada["population"] == "pediatric"
    assert gravada["idade_meses"] == 11


def test_o_carimbo_e_renovado_a_cada_turno() -> None:
    """A janela mede desde o ULTIMO uso, nao desde a primeira vez que o dado foi dito.

    Uma conversa ativa nao deveria esquecer quem e' o paciente no meio so' porque comecou ha' seis
    horas — o que a janela protege e' a conversa ABANDONADA, nao a longa.
    """
    depois = AGORA + timedelta(hours=5)
    gravada = _memoria_a_gravar(
        {"population": "pediatric"},
        memoria(population="pediatric"),
        agora=depois,
        confirmada=True,
    )
    assert gravada is not None
    assert gravada["gravado_em"] == depois.isoformat()


def test_a_confirmacao_nomeia_o_dado_e_e_uma_pergunta() -> None:
    """Decisao 4: uma frase, nao um formulario — e uma PERGUNTA, nao um aviso.

    A pessoa precisa poder corrigir: o custo de uma populacao errada lembrada e' a tabela errada
    consultada em silencio, e o unico ponto em que isso e' recuperavel e' ela ler e discordar.
    """
    frase = _frase_de_confirmacao({"population": "pediatric", "idade_meses": 11})
    assert "11 meses" in frase
    assert frase.rstrip().endswith("?"), "a confirmacao tem de ser uma pergunta"


def test_a_confirmacao_nunca_fica_vazia() -> None:
    """Mesmo sem idade, ha' o que confirmar: a POPULACAO ja' muda a tabela sozinha.

    Uma frase vazia aqui produziria uma resposta comecando com nada e um dado lembrado usado em
    silencio — o pior dos dois mundos.
    """
    for lembrados in ({"population": "pediatric"}, {"population": "gestante"}, {"population": "adult"}):
        assert _frase_de_confirmacao(lembrados).strip()


def test_a_memoria_guarda_quem_e_o_paciente_e_nao_o_que_ele_disse() -> None:
    """`sintoma_codigo` NAO e' memoria, e a ausencia dele aqui e' deliberada.

    Lembrar o sintoma seria pior que nao lembrar: o sintoma e' o que a pessoa esta dizendo AGORA, e
    carregar o do turno anterior faria a Helena responder a mensagem errada — com a agravante de
    que um sintoma lembrado poderia disparar a DMN num turno em que ninguem o mencionou.
    """
    gravada = _memoria_a_gravar(
        {"population": "pediatric", "idade_meses": 11, "sintoma_codigo": "febre", "intensidade": "grave"},
        None,
        agora=AGORA,
        confirmada=False,
    )
    assert gravada is not None
    assert "sintoma_codigo" not in gravada
    assert "intensidade" not in gravada


# =================================================================================================
# 4. A prova que importa: dois turnos de verdade, pelo grafo
# =================================================================================================
#
# Os testes acima exercitam os helpers. Este exercita o CAMINHO — `receive` -> `classify` ->
# `_evaluate_dmn` —, que e' onde o defeito de 13/09 aconteceu. A diferenca nao e' cosmetica: um
# helper correto ligado no lugar errado produz exatamente o mesmo bug com testes verdes.


class _Inferencia:
    def __init__(self, respostas: list[str]) -> None:
        self._respostas = list(respostas)
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if not self._respostas:
            raise AssertionError("inferencia esgotada — o teste pediu mais turnos do que registrou")
        return self._respostas.pop(0)


class _WhatsApp:
    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        return {"ok": True}


_SEM_BANDEIRA = [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE", "motivo": "Sem criterio"}]


def _grafo(inferencia: _Inferencia, dmn: FakeDmnTransport, *, memoria_ligada: bool = True) -> HelenaGraph:
    return HelenaGraph(
        inference=inferencia,
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        memoria_clinica_enabled=memoria_ligada,
    )


def _estado(texto: str, **extra: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-1",
        "message_body": texto,
    }
    s.update(extra)  # type: ignore[typeddict-item]
    return s


def _extracao(**over: Any) -> str:
    base = {
        "intent": "symptom",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": "febre",
        "intensidade": "moderada",
    }
    base.update(over)
    return json.dumps(base)


async def _turno(g: HelenaGraph, estado: HelenaState) -> dict[str, Any]:
    acumulado: dict[str, Any] = dict(estado)
    acumulado.update(await g.receive(acumulado))  # type: ignore[arg-type]
    acumulado.update(await g.classify(acumulado))  # type: ignore[arg-type]
    return acumulado


@pytest.mark.asyncio
async def test_o_bebe_do_turno_1_e_triado_pela_tabela_da_crianca_no_turno_2() -> None:
    """O defeito de 13/09, ponta a ponta, e o motivo de esta frente existir.

    O `FakeDmnTransport` levanta para uma tabela nao registrada — entao registrar APENAS a
    pediatrica faz o teste falhar alto se o grafo escolher a de adulto, em vez de passar calado com
    uma tabela errada mas disponivel.
    """
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_pediatric", _SEM_BANDEIRA)

    inferencia = _Inferencia(
        [
            _extracao(intent="information", population="pediatric", idade_meses=11, sintoma_codigo=None),
            _extracao(),  # turno 2: a mae NAO repete a idade
        ]
    )
    g = _grafo(inferencia, dmn)

    turno1 = await _turno(g, _estado("meu bebe tem 11 meses"))
    turno2 = await _turno(g, _estado("ele esta com febre", memoria_clinica=turno1["memoria_clinica"]))

    assert turno2["dmn_table"] == "triage_redflag_pediatric"
    assert turno2["population"] == "pediatric"
    assert turno2["idade_meses"] == 11


@pytest.mark.asyncio
async def test_sem_a_memoria_o_mesmo_turno_2_cai_na_tabela_de_adulto() -> None:
    """O RED do teste acima — a prova de que ele nao passa por acidente.

    Mesmas duas mensagens, mesma extracao, `memoria_clinica_enabled=False`: a tabela consultada
    volta a ser a de adulto. E' o comportamento medido em producao em 13/09.
    """
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", _SEM_BANDEIRA)
    dmn.register("triage_redflag_pediatric", _SEM_BANDEIRA)

    inferencia = _Inferencia(
        [
            _extracao(intent="information", population="pediatric", idade_meses=11, sintoma_codigo=None),
            _extracao(),
        ]
    )
    g = _grafo(inferencia, dmn, memoria_ligada=False)

    turno1 = await _turno(g, _estado("meu bebe tem 11 meses"))
    turno2 = await _turno(g, _estado("ele esta com febre", memoria_clinica=turno1.get("memoria_clinica")))

    assert turno2["dmn_table"] == "triage_redflag_adult", (
        "com a memoria desligada o comportamento tem de ser o de hoje — se este teste virar verde "
        "com a tabela pediatrica, o desligamento nao desliga"
    )


@pytest.mark.asyncio
async def test_a_confirmacao_chega_ao_contexto_do_prompt_uma_vez_so() -> None:
    """Decisao 4, no caminho real: a frase entra no prompt da resposta, e nao se repete.

    O QUE ISTO NAO PROVA: que o modelo obedece. A licao de 13/09 e' que mandar no prompt nao
    segura — o que se prova aqui e' que a frase CHEGA, que e' a parte deterministica.
    """
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_pediatric", _SEM_BANDEIRA)
    inferencia = _Inferencia(
        [
            _extracao(intent="information", population="pediatric", idade_meses=11, sintoma_codigo=None),
            _extracao(),
            _extracao(),
        ]
    )
    g = _grafo(inferencia, dmn)

    t1 = await _turno(g, _estado("meu bebe tem 11 meses"))
    t2 = await _turno(g, _estado("ele esta com febre", memoria_clinica=t1["memoria_clinica"]))
    assert t2["memoria_a_confirmar"], "o dado lembrado entrou numa decisao clinica sem ser mostrado"
    assert "11 meses" in t2["memoria_a_confirmar"]

    t3 = await _turno(g, _estado("e agora?", memoria_clinica=t2["memoria_clinica"]))
    assert t3["memoria_a_confirmar"] is None, (
        "a confirmacao e' UMA vez por conversa: repeti-la a cada mensagem vira ruido e a pessoa "
        "para de ler justamente a frase que existe para ela corrigir"
    )


@pytest.mark.asyncio
async def test_a_memoria_nao_atravessa_por_fora_do_validador() -> None:
    """T1.11 no caminho real: uma memoria plantada com forma errada nao vira paciente.

    `receive` valida na FRONTEIRA, entao o que chega malformado de um chamador vira `None` e o
    turno degrada para o comportamento de hoje — nunca para um paciente parcialmente lembrado.
    """
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", _SEM_BANDEIRA)
    g = _grafo(_Inferencia([_extracao()]), dmn)

    turno = await _turno(
        g,
        _estado("estou com febre", memoria_clinica={"population": "pediatric"}),  # sem carimbo
    )
    assert turno["dmn_table"] == "triage_redflag_adult"
    assert turno["memoria_a_confirmar"] is None
