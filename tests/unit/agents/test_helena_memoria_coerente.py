"""A memoria clinica COERENTE: a tabela e o texto leem o mesmo paciente (F4/F5, 21/09/2026).

O QUE FOI MEDIDO, bateria do diretor de 21/09/2026 em dev, imagem `8b014a60`.

F4 · caso `D2`, tres turnos:

    | turno | mensagem                       | tabela          | sintoma | idade |
    |-------|--------------------------------|-----------------|---------|-------|
    | 1     | "meu filho esta com febre"     | pediatrica  OK  | febre   | —     |
    | 2     | "3 anos"                       | pediatrica  OK  | —       | 36    |
    | 3     | "desde ontem"                  | ADULTA      ERR | febre   | —     |

E a resposta do turno 3: *"entendi que voce esta falando sobre seu bebe de 36 meses, certo?"*

A frase PROVA que a memoria tinha os 36 meses. A tabela consultada foi a de adulto com idade
vazia. O relatorio leu isso como "dois caminhos leem a mesma memoria e um ignora" — e o codigo
diz outra coisa: `_fundir_memoria_clinica` roda em `classify` ANTES da DMN, e a extracao E' o que
vai para `_evaluate_dmn`. Os dois caminhos leem a MESMA fusao.

A CAUSA REAL, reproduzida no primeiro teste deste arquivo: no turno 3 o modelo devolveu
`population="adult"` — nao porque "desde ontem" diga algo sobre um adulto, mas porque uma mensagem
que nao fala de ninguem precisa de ALGUM valor no campo. E a regra 3 do desenho ("a informacao
NOVA vence, exceto quando a nova e' AUSENCIA") tratou esse `"adult"` como AFIRMACAO de que o
paciente mudou. Ele nao e': nao vem acompanhado de idade nenhuma. Entao a populacao virou adulta
e a idade LEMBRADA (36 meses, do campo pediatrico) veio junto — o par incoerente
`population="adult" + idade_meses=36`, que e' exatamente o que produziu "seu bebe de 36 meses"
numa consulta a tabela de adulto com `idade_anos=None`.

A REGRA QUE FECHA, e ela e' uma so': uma populacao sem LASTRO nao troca a populacao lembrada, e
uma idade so' atravessa se pertencer a populacao final. "Lastro" e' o campo de idade que aquela
populacao usa — `idade_anos` para adulto, `idade_meses` para pediatrico,
`idade_gestacional_semanas` para gestante. Com lastro, a informacao nova continua vencendo (o
teste `test_informacao_nova_vence_a_lembrada`, que ja' existia, e' justamente esse caso).

F5 · caso `D3`, dois turnos:

    > tenho 30 anos e estou com febre   -> adulta, sintoma=febre, idade=30
    > me enganei, tenho 70 anos          -> SEM TABELA, sintoma perdido

Ela respondeu "Entendi que voce tem 70 anos" e voltou ao cartao administrativo — para alguem que
acabou de dizer que tem febre e 70 anos, que e' o limiar da regra `r8` da tabela de adulto.

A REGRA QUE FECHA: correcao de um dado que uma avaliacao JA USOU re-dispara a avaliacao, com o
dado novo e o sintoma lembrado. E ela NAO derruba a decisao de nao lembrar sintoma — ver
`test_a_memoria_continua_nao_lembrando_o_sintoma_da_mensagem_anterior`: o que a conversa passa a
lembrar e' que uma AVALIACAO aconteceu e com que dado, o que e' outra coisa.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final, cast

import pytest

from maezo.agents.helena.graph import (
    HelenaGraph,
    HelenaState,
    _correcao_de_dado_avaliado,
    _frase_de_confirmacao,
    _fundir_memoria_clinica,
    _marcar_avaliacao,
    _memoria_a_gravar,
    _memoria_clinica_valida,
)
from maezo.agents.helena.prompts import ALLOWED_SINTOMA_CODIGOS, SINTOMA_EM_PALAVRAS
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

AGORA: Final[datetime] = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

#: A linha que o `FakeDmnTransport` devolve quando nada precisa ser decidido pelo teste.
_SEM_BANDEIRA = [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE", "motivo": "Sem criterio"}]
_COM_BANDEIRA = [
    {"red_flag": True, "prioridade": "P2", "conduta": "ESCALATE_PRIORITARIO", "motivo": "febre em idoso"}
]


def memoria(**campos: Any) -> dict[str, Any]:
    return {"gravado_em": AGORA.isoformat(), **campos}


# =================================================================================================
# F4 · a fusao: uma populacao sem lastro nao troca a lembrada
# =================================================================================================


def test_o_turno_3_do_d2_reproduzido_a_populacao_sem_lastro_nao_troca_o_paciente() -> None:
    """O DEFEITO MEDIDO, reduzido ao seu nucleo. RED antes da correcao, GREEN depois.

    `"desde ontem"` nao afirma nada sobre um adulto. O `population="adult"` que o modelo devolveu
    nao traz `idade_anos` nenhum — e' o valor de preenchimento de um campo obrigatorio, nao uma
    correcao. Tratado como afirmacao, ele troca a tabela clinica de uma crianca de 3 anos.
    """
    extracao_do_turno_3 = {
        "intent": "symptom",
        "population": "adult",
        "sintoma_codigo": "febre",
        "idade_anos": None,
        "idade_meses": None,
    }

    fundida, da_memoria = _fundir_memoria_clinica(
        extracao_do_turno_3, memoria(population="pediatric", idade_meses=36)
    )

    assert fundida["population"] == "pediatric", (
        "uma populacao SEM a idade que ela usa nao e' afirmacao de que o paciente mudou — e' o "
        "campo obrigatorio preenchido por falta de opcao, e trocar por ele manda a crianca para a "
        "tabela de adulto"
    )
    assert fundida["idade_meses"] == 36
    assert fundida["idade_anos"] is None
    assert da_memoria["population"] == "pediatric"


def test_a_idade_lembrada_nunca_atravessa_para_uma_populacao_de_outro_campo() -> None:
    """O par INCOERENTE que gerou "seu bebe de 36 meses" numa tabela de adulto.

    Quando a populacao final E' adulta com lastro proprio, uma `idade_meses` lembrada nao tem o
    que fazer ali: a tabela de adulto nem a le', e `_frase_de_confirmacao` leria — e falaria de um
    bebe para quem acabou de dizer a propria idade.
    """
    fundida, da_memoria = _fundir_memoria_clinica(
        {"population": "adult", "idade_anos": 34, "idade_meses": None},
        memoria(population="pediatric", idade_meses=36),
    )

    assert fundida["population"] == "adult", "com lastro (idade_anos=34), a informacao nova vence"
    assert fundida["idade_meses"] is None, "a idade pediatrica nao acompanha uma populacao adulta"
    assert "idade_meses" not in da_memoria


def test_a_populacao_lembrada_com_lastro_novo_continua_sendo_trocada() -> None:
    """A metade que uma regra preguicosa quebraria: "na verdade e' pra mim, tenho 34 anos" TEM de
    trocar o paciente. Sem este teste, uma fusao que nunca troca passaria o de cima."""
    fundida, _ = _fundir_memoria_clinica(
        {"population": "adult", "idade_anos": 34},
        memoria(population="pediatric", idade_meses=11),
    )

    assert fundida["population"] == "adult"
    assert fundida["idade_anos"] == 34


def test_uma_gestante_lembrada_nao_e_trocada_por_uma_populacao_sem_semanas() -> None:
    """A mesma regra na terceira populacao — e' regra, nao remendo para o par adulto/pediatrico."""
    fundida, _ = _fundir_memoria_clinica(
        {"population": "adult", "idade_anos": None},
        memoria(population="gestante", idade_gestacional_semanas=32),
    )

    assert fundida["population"] == "gestante"
    assert fundida["idade_gestacional_semanas"] == 32


def test_o_risco_psicossocial_declarado_troca_a_populacao_sem_precisar_de_idade() -> None:
    """A excecao, e ela e' deliberada: `mental_health` nao TEM campo de idade — a tabela dela le'
    `risco_imediato`. Exigir lastro de idade ali seria exigir o impossivel, e a populacao vem
    sempre junto do `psychosocial_risk` que ja' forca a tabela em `classify`."""
    fundida, _ = _fundir_memoria_clinica(
        {"population": "mental_health", "idade_anos": None},
        memoria(population="adult", idade_anos=40),
    )

    assert fundida["population"] == "mental_health"


def test_uma_memoria_sem_populacao_nao_impede_a_idade_de_atravessar() -> None:
    """Regressao possivel da regra de coerencia: com a populacao final em `none`, nenhuma idade
    conflita — `none` nao e' uma populacao com campo proprio, e uma idade lembrada e' o unico
    dado que a conversa tem."""
    fundida, da_memoria = _fundir_memoria_clinica(
        {"population": "none", "idade_anos": None}, memoria(idade_anos=30)
    )

    assert fundida["idade_anos"] == 30
    assert da_memoria == {"idade_anos": 30}


# =================================================================================================
# F4 · a frase: anos acima de 24 meses
# =================================================================================================


def test_a_confirmacao_fala_em_anos_acima_de_dois_anos() -> None:
    """O defeito de redacao do D2: *"seu bebe de 36 meses"* para uma crianca de 3 anos.

    Nenhuma mae fala assim, e chamar de bebe quem ja anda soa a erro — o que importa porque esta
    frase e' uma PERGUNTA, existe para a pessoa poder corrigir, e uma frase que soa errada e' uma
    frase que ela para de ler.
    """
    frase = _frase_de_confirmacao({"population": "pediatric", "idade_meses": 36})

    assert "3 anos" in frase
    assert "36 meses" not in frase
    assert "bebe" not in frase
    assert frase.rstrip().endswith("?")


@pytest.mark.parametrize(
    ("meses", "esperado"),
    [
        (2, "2 meses"),
        (11, "11 meses"),
        (24, "24 meses"),  # o limiar INCLUSIVE: dois anos ainda e' idade de bebe no uso comum
        (25, "2 anos"),
        (36, "3 anos"),
        (96, "8 anos"),
    ],
)
def test_o_limiar_dos_24_meses_e_exato(meses: int, esperado: str) -> None:
    """Os dois lados do limiar, para a conversao nao ser nem cedo nem tarde."""
    assert esperado in _frase_de_confirmacao({"idade_meses": meses})


# =================================================================================================
# F5 · correcao de um dado ja avaliado re-dispara a avaliacao
# =================================================================================================


def test_a_memoria_guarda_que_uma_avaliacao_aconteceu_e_com_que_dado() -> None:
    """A peca nova de F5, e o cuidado com o que ela NAO e'.

    Nao e' "lembrar o sintoma da mensagem anterior" — essa decisao continua valendo e tem teste
    proprio abaixo. E' lembrar que uma AVALIACAO CLINICA aconteceu nesta conversa e qual dado ela
    usou, que e' a unica informacao que permite REFAZE-LA quando a pessoa corrige esse dado.
    """
    gravada = _memoria_a_gravar(
        {"population": "adult", "idade_anos": 30}, None, agora=AGORA, confirmada=False
    )
    marcada = _marcar_avaliacao(gravada, sintoma_codigo="febre", intensidade="moderada")

    assert marcada is not None
    assert marcada["sintoma_avaliado"] == "febre"
    assert marcada["intensidade_avaliada"] == "moderada"


def test_a_memoria_continua_nao_lembrando_o_sintoma_da_mensagem_anterior() -> None:
    """A decisao que F5 NAO derruba, e a diferenca entre as duas chaves.

    `sintoma_codigo` continua fora da memoria: o sintoma e' o que a pessoa esta dizendo AGORA, e
    carregar o anterior faria a Helena responder a mensagem errada — com a agravante de disparar a
    DMN num turno em que ninguem o mencionou. `sintoma_avaliado` nao dispara nada por conta
    propria: ele so' e' lido quando a mensagem CORRIGE um dado que aquela avaliacao usou.
    """
    gravada = _memoria_a_gravar(
        {"population": "adult", "idade_anos": 30, "sintoma_codigo": "febre", "intensidade": "grave"},
        None,
        agora=AGORA,
        confirmada=False,
    )

    assert gravada is not None
    assert "sintoma_codigo" not in gravada
    assert "intensidade" not in gravada
    assert "sintoma_avaliado" not in gravada, "quem marca a avaliacao e' `_marcar_avaliacao`, nao esta"


def test_a_marca_da_avaliacao_atravessa_os_turnos_seguintes() -> None:
    """Sem isto a memoria da avaliacao morreria no turno seguinte, e o D3 voltaria uma mensagem
    depois — o mesmo modo de falha que `test_o_que_foi_lembrado_num_turno_continua_lembrado_no
    _seguinte` fechou para as idades."""
    anterior = memoria(
        population="adult", idade_anos=30, sintoma_avaliado="febre", intensidade_avaliada="moderada"
    )

    gravada = _memoria_a_gravar(
        {"population": "adult", "idade_anos": 70}, anterior, agora=AGORA, confirmada=True
    )

    assert gravada is not None
    assert gravada["sintoma_avaliado"] == "febre"
    assert gravada["intensidade_avaliada"] == "moderada"


def test_o_turno_2_do_d3_e_reconhecido_como_correcao() -> None:
    """A correcao do D3: "me enganei, tenho 70 anos" depois de "tenho 30 anos e estou com febre".

    O sinal e' DETERMINISTICO e nao depende de o modelo rotular a intencao como "correcao": a
    mensagem traz um valor para um campo que a avaliacao anterior USOU, DIFERENTE do que estava
    lembrado, e nao traz sintoma proprio.
    """
    correcao = _correcao_de_dado_avaliado(
        {"population": "adult", "idade_anos": 70, "sintoma_codigo": None},
        memoria(
            population="adult",
            idade_anos=30,
            sintoma_avaliado="febre",
            intensidade_avaliada="moderada",
        ),
    )

    assert correcao is not None
    assert correcao["sintoma_codigo"] == "febre"
    assert correcao["intensidade"] == "moderada"
    assert correcao["corrigidos"] == {"idade_anos": 70}


@pytest.mark.parametrize(
    ("extracao", "mem", "porque"),
    [
        (
            {"idade_anos": 70, "sintoma_codigo": None},
            memoria(population="adult", idade_anos=30),
            "nenhuma avaliacao aconteceu nesta conversa — nao ha o que refazer",
        ),
        (
            {"idade_anos": 30, "sintoma_codigo": None},
            memoria(idade_anos=30, sintoma_avaliado="febre"),
            "repetir o mesmo dado nao e' correcao",
        ),
        (
            {"idade_anos": 70, "sintoma_codigo": "dor_toracica"},
            memoria(idade_anos=30, sintoma_avaliado="febre"),
            "a mensagem trouxe sintoma PROPRIO — o caminho normal ja' resolve, e usar o lembrado "
            "aqui seria responder a mensagem errada",
        ),
        (
            {"idade_anos": None, "sintoma_codigo": None},
            memoria(idade_anos=30, sintoma_avaliado="febre"),
            "ausencia nao e' correcao (a mesma regra 3 do documento, do outro lado)",
        ),
        (
            {"population": "none", "idade_anos": None, "sintoma_codigo": None},
            memoria(population="adult", idade_anos=30, sintoma_avaliado="febre"),
            "`population=none` e' o valor de ausencia do vocabulario fechado, nao uma troca",
        ),
        ({"idade_anos": 70, "sintoma_codigo": None}, None, "conversa sem memoria nenhuma"),
    ],
)
def test_o_que_nao_e_correcao_nao_re_dispara_avaliacao_nenhuma(
    extracao: dict[str, Any], mem: dict[str, Any] | None, porque: str
) -> None:
    """A metade que impede a regra de virar "todo turno vira sintoma".

    Um gatilho largo aqui faria cada mensagem administrativa no meio de uma conversa clinica
    reabrir a triagem — e o custo de um falso positivo e' uma DMN consultada com um sintoma que a
    pessoa nao mencionou neste turno.
    """
    assert _correcao_de_dado_avaliado(extracao, mem) is None, porque


def test_a_marca_da_avaliacao_e_validada_na_fronteira_como_todo_o_resto() -> None:
    """T1.11 nos campos novos: um `sintoma_avaliado` fora da allowlist, ou uma intensidade fora do
    dominio, derruba a memoria INTEIRA para `None` — nunca um paciente parcialmente lembrado."""
    assert (
        _memoria_clinica_valida(
            memoria(population="adult", idade_anos=30, sintoma_avaliado="febre"), agora=AGORA
        )
        or {}
    ).get("sintoma_avaliado") == "febre"

    for bruta, porque in (
        (
            memoria(population="adult", sintoma_avaliado="sintoma_que_nao_existe"),
            "codigo fora da allowlist da DMN",
        ),
        (memoria(population="adult", sintoma_avaliado=42), "codigo que nao e' texto"),
        (
            memoria(population="adult", sintoma_avaliado="febre", intensidade_avaliada="urgentissima"),
            "intensidade fora do dominio fechado",
        ),
    ):
        assert _memoria_clinica_valida(bruta, agora=AGORA) is None, porque


# =================================================================================================
# A prova que importa: as conversas de verdade, pelo grafo
# =================================================================================================


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


def _grafo(inferencia: _Inferencia, dmn: FakeDmnTransport) -> HelenaGraph:
    return HelenaGraph(
        # `cast` e nao `type: ignore`, mesmo idioma de `HelenaGraph.build()` em producao: o duplo
        # satisfaz o Protocol ESTRUTURALMENTE, e uma supressao esconderia uma deriva real de
        # assinatura (a classe de defeito que CC-12 ja custou uma vez).
        inference=cast(InferenceProvider, inferencia),
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        memoria_clinica_enabled=True,
    )


def _estado(texto: str, **extra: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:d2_deadbeef",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-d2",
        "message_body": texto,
    }
    s.update(extra)  # type: ignore[typeddict-item]
    return s


def _extracao(**over: Any) -> str:
    base = {
        "intent": "symptom",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(over)
    return json.dumps(base)


async def _turno(g: HelenaGraph, estado: HelenaState) -> dict[str, Any]:
    acumulado: dict[str, Any] = dict(estado)
    acumulado.update(await g.receive(acumulado))  # type: ignore[arg-type]
    acumulado.update(await g.classify(acumulado))  # type: ignore[arg-type]
    return acumulado


async def test_d2_os_tres_turnos_ficam_na_tabela_pediatrica() -> None:
    """O caso `D2` inteiro, na ORDEM NATURAL da conversa: sintoma -> idade -> detalhe.

    O `FakeDmnTransport` levanta para tabela nao registrada, entao registrar APENAS a pediatrica
    faz o teste falhar ALTO se o grafo escolher a de adulto — em vez de passar calado com uma
    tabela errada mas disponivel.
    """
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_pediatric", _SEM_BANDEIRA)
    inferencia = _Inferencia(
        [
            # `_turno` roda SO' receive+classify: UMA chamada por turno, a da extracao.
            # turno 1: "meu filho esta com febre" — populacao pediatrica, sem idade
            _extracao(population="pediatric", sintoma_codigo="febre", intensidade="moderada"),
            # turno 2: "3 anos" — a idade, sem sintoma
            # turno 2: "3 anos" - a idade, SEM codigo de sintoma. `intent=symptom` porque a
            # bateria mediu a tabela pediatrica consultada NESTE turno com `sintoma = -`:
            # houve avaliacao, sobre um codigo nulo.
            _extracao(population="pediatric", idade_meses=36),
            # turno 3: "desde ontem" — o detalhe. O modelo devolve `adult` porque a mensagem nao
            # fala de ninguem, e era este valor que trocava o paciente.
            _extracao(population="adult", sintoma_codigo="febre", intensidade="moderada"),
        ]
    )
    g = _grafo(inferencia, dmn)

    t1 = await _turno(g, _estado("meu filho esta com febre"))
    assert t1["dmn_table"] == "triage_redflag_pediatric"

    t2 = await _turno(g, _estado("3 anos", memoria_clinica=t1["memoria_clinica"]))
    assert t2["dmn_table"] == "triage_redflag_pediatric"
    assert t2["idade_meses"] == 36

    t3 = await _turno(g, _estado("desde ontem", memoria_clinica=t2["memoria_clinica"]))

    assert t3["dmn_table"] == "triage_redflag_pediatric", (
        "o turno 3 do D2: o detalhe nao pode mandar a crianca para a tabela de adulto"
    )
    assert t3["population"] == "pediatric"
    assert t3["idade_meses"] == 36


async def test_d2_a_frase_de_confirmacao_do_turno_3_fala_em_3_anos() -> None:
    """A outra metade do D2: a frase que provou a memoria tambem passa a estar certa."""
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_pediatric", _SEM_BANDEIRA)
    inferencia = _Inferencia(
        [
            _extracao(population="pediatric", sintoma_codigo="febre", intensidade="moderada"),
            _extracao(population="pediatric", idade_meses=36),
            _extracao(population="adult", sintoma_codigo="febre", intensidade="moderada"),
        ]
    )
    g = _grafo(inferencia, dmn)

    t1 = await _turno(g, _estado("meu filho esta com febre"))
    t2 = await _turno(g, _estado("3 anos", memoria_clinica=t1["memoria_clinica"]))
    t3 = await _turno(g, _estado("desde ontem", memoria_clinica=t2["memoria_clinica"]))

    assert t3["memoria_a_confirmar"], "um dado lembrado entrou numa decisao clinica sem ser mostrado"
    assert "3 anos" in t3["memoria_a_confirmar"]
    assert "36 meses" not in t3["memoria_a_confirmar"]


async def test_d3_a_correcao_da_idade_reavalia_com_o_sintoma_lembrado() -> None:
    """O caso `D3` inteiro. Turno 2 registra `SEM TABELA, sintoma perdido`; passa a reavaliar.

    A tabela de adulto e' a UNICA registrada, e o teste exige `idade_anos=70` NA ENTRADA da
    decisao — sem isso o teste passaria com a tabela certa e a idade velha, que e' precisamente o
    modo de falha da regra `r8` (`febre` + intensidade + `>= 65`).
    """
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", _COM_BANDEIRA)
    inferencia = _Inferencia(
        [
            _extracao(population="adult", sintoma_codigo="febre", intensidade="moderada", idade_anos=30),
            # turno 2: "me enganei, tenho 70 anos" — sem sintoma proprio, intent administrativo
            _extracao(intent="information", population="adult", idade_anos=70),
        ]
    )
    g = _grafo(inferencia, dmn)

    t1 = await _turno(g, _estado("tenho 30 anos e estou com febre"))
    assert t1["dmn_table"] == "triage_redflag_adult"

    t2 = await _turno(g, _estado("me enganei, tenho 70 anos", memoria_clinica=t1["memoria_clinica"]))

    assert t2["dmn_table"] == "triage_redflag_adult", "a correcao tem de reavaliar, nao voltar ao cartao"
    assert t2["sintoma_codigo"] == "febre", "o sintoma nao se perde quando a mensagem so' corrige"
    assert t2["intent"] == "symptom"
    assert t2["next_kind"] == "escalate"
    entradas = dmn.calls[-1][1]
    assert entradas["idade_anos"] == 70, (
        f"a reavaliacao usou o dado VELHO: {entradas!r} — e' a idade nova que cruza o limiar de 65"
    )


async def test_um_turno_administrativo_sem_correcao_nao_reabre_a_triagem() -> None:
    """O RED do teste acima: sem correcao de dado, um turno administrativo no meio de uma conversa
    clinica continua administrativo. Sem este teste, "toda mensagem vira sintoma" passaria."""
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", _SEM_BANDEIRA)
    inferencia = _Inferencia(
        [
            _extracao(population="adult", sintoma_codigo="febre", intensidade="moderada", idade_anos=30),
            _extracao(intent="information", population="none"),
        ]
    )
    g = _grafo(inferencia, dmn)

    t1 = await _turno(g, _estado("tenho 30 anos e estou com febre"))
    t2 = await _turno(g, _estado("qual o telefone da central?", memoria_clinica=t1["memoria_clinica"]))

    assert t2["intent"] == "information"
    assert t2["next_kind"] == "inform"
    assert t2["sintoma_codigo"] is None
    assert len(dmn.calls) == 1, "a DMN foi consultada de novo num turno de cadastro"


# =================================================================================================
# SEGUNDA RODADA DE 21/09/2026 — a coerencia nos DOIS sentidos e a confirmacao da reavaliacao
# =================================================================================================


def test_a_idade_que_a_mensagem_trouxe_vence_a_lembrada_ainda_que_em_outro_campo() -> None:
    """A regra de coerencia era de MAO UNICA, e o preco era a idade velha decidir a regra.

    O CENARIO, o `D2` um turno adiante: a conversa lembra `population=pediatric` +
    `idade_meses=36`; a mae escreve "na verdade ele tem 5 anos" e o modelo devolve
    `population=pediatric` com a idade em `idade_anos=5` (o `classify_prompt` pede `idade_meses`
    para pediatric, mas "5 anos" e' como a frase chega, e `_validate_extraction` valida cada campo
    ISOLADAMENTE — nao cruza idade com populacao).

    O QUE ACONTECIA: `idade_meses` da mensagem e' `None`, entao a coerencia — que so' pergunta se o
    campo pertence a populacao FINAL — deixava a idade lembrada (36) atravessar. A extracao fundida
    saia com 5 anos E 36 meses ao mesmo tempo, `_evaluate_dmn` na tabela pediatrica le'
    `idade_meses`, e a idade de dois turnos atras decidia a regra enquanto o dado que a pessoa
    acabou de dar era descartado em silencio — com a frase de confirmacao dizendo "sua crianca de 3
    anos", contradizendo o que ela escreveu no mesmo turno.
    """
    lembrada = memoria(population="pediatric", idade_meses=36)

    fundida, veio = _fundir_memoria_clinica(
        {"population": "pediatric", "idade_anos": 5, "idade_meses": None}, lembrada
    )

    assert fundida.get("idade_meses") is None, (
        "a idade lembrada sobreviveu ao lado da que a mensagem acabou de trazer, e e' ela que a "
        "tabela pediatrica le'"
    )
    assert fundida["idade_anos"] == 5
    assert "idade_meses" not in veio


def test_sem_idade_na_mensagem_a_lembrada_continua_atravessando() -> None:
    """O RED do teste acima: a regra nova e' condicionada a mensagem FALAR de idade.

    Sem esta metade, "nenhuma idade lembrada atravessa nunca" passaria o teste de cima e
    desmontaria a frente inteira da memoria clinica — o bebe de 11 meses voltaria a ser triado
    pela tabela de adulto, que e' o defeito de 13/09 reproduzido tres vezes.
    """
    fundida, veio = _fundir_memoria_clinica(
        {"population": "pediatric", "idade_anos": None, "idade_meses": None},
        memoria(population="pediatric", idade_meses=11),
    )

    assert fundida["idade_meses"] == 11
    assert veio["idade_meses"] == 11


def test_a_frase_confirma_o_sintoma_quando_a_reavaliacao_o_usa() -> None:
    """A frase do ramo novo, e o vocabulario que ela usa.

    `febre` e' a unica entrada com texto declarado em `SINTOMA_EM_PALAVRAS` — para todo o resto a
    frase e' generica de proposito, porque traduzir um `sintoma_codigo` para portugues leigo e'
    afirmacao clinica (e nos codigos de saude mental seria devolver o diagnostico a pessoa).
    """
    assert _frase_de_confirmacao({"sintoma_avaliado": "febre"}) == (
        "entendi que ainda e' sobre a febre que voce contou, certo?"
    )
    assert _frase_de_confirmacao({"sintoma_avaliado": "ideacao_suicida"}) == (
        "entendi que ainda e' sobre o mesmo sintoma que voce contou, certo?"
    )
    # O ramo do sintoma vem ANTES do de idade: num turno de correcao e' ele a confirmacao que
    # importa, e o prompt usa UMA frase.
    frase = _frase_de_confirmacao({"sintoma_avaliado": "febre", "idade_anos": 70})
    assert "a febre" in frase and "70 anos" not in frase


@pytest.mark.parametrize("codigo", sorted(ALLOWED_SINTOMA_CODIGOS))
def test_todo_codigo_da_allowlist_tem_vocabulario_declarado(codigo: str) -> None:
    """A cerca que pega o codigo NOVO — a direcao que apodrece em silencio.

    `None` e' uma declaracao valida (e e' quase toda a tabela): significa "use a frase generica".
    O que nao pode e' um codigo ficar FORA do mapa, porque ai' a escolha entre nomear e nao nomear
    o sintoma para o beneficiario passaria a ser um default silencioso em vez de uma decisao.
    """
    assert codigo in SINTOMA_EM_PALAVRAS, (
        f"{codigo!r} nao tem vocabulario declarado em `SINTOMA_EM_PALAVRAS`: decida entre um texto "
        "leigo (decisao do dono clinico, docs/review-queue.md) e `None` (frase generica)"
    )


def test_o_vocabulario_do_sintoma_nao_declara_codigo_que_nao_existe() -> None:
    """O outro lado: um codigo removido da allowlist nao pode sobrar aqui como vocabulario morto."""
    orfaos = sorted(set(SINTOMA_EM_PALAVRAS) - ALLOWED_SINTOMA_CODIGOS)

    assert not orfaos, f"vocabulario declarado para codigo fora da allowlist: {orfaos}"


async def test_d3_a_reavaliacao_por_correcao_confirma_o_sintoma_lembrado() -> None:
    """F5, a metade que faltava: a pessoa tem de poder corrigir SOBRE O QUE ela esta falando.

    O turno de correcao reabre a triagem com um sintoma que a mensagem NAO mencionou (o lembrado),
    e `confirmar_agora` — calculado antes da conversao, e olhando so' os campos de QUEM E' O
    PACIENTE — nao cobria esse caso. Resultado: o dado lembrado mais decisivo do turno entrava sem
    ninguem dizer a pessoa, e a unica coisa que ela leria seria o resultado (um escalonamento P2).
    """
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", _COM_BANDEIRA)
    inferencia = _Inferencia(
        [
            _extracao(population="adult", sintoma_codigo="febre", intensidade="moderada", idade_anos=30),
            _extracao(intent="information", population="adult", idade_anos=70),
        ]
    )
    g = _grafo(inferencia, dmn)

    t1 = await _turno(g, _estado("tenho 30 anos e estou com febre"))
    t2 = await _turno(g, _estado("me enganei, tenho 70 anos", memoria_clinica=t1["memoria_clinica"]))

    assert t2["sintoma_codigo"] == "febre", "premissa: a reavaliacao usou o sintoma lembrado"
    assert t2["memoria_a_confirmar"], (
        "a reavaliacao usou um sintoma que a mensagem nao mencionou e nao disse isso a pessoa"
    )
    assert "a febre" in t2["memoria_a_confirmar"]
    assert t2["memoria_a_confirmar"].endswith(", certo?"), "e' uma PERGUNTA, nao um aviso"


async def test_um_turno_sem_correcao_nao_ganha_confirmacao_de_sintoma() -> None:
    """O RED do teste acima: a confirmacao do sintoma e' do ramo de CORRECAO, nao de todo turno.

    Um `memoria_a_confirmar` em toda mensagem viraria ruido e a pessoa pararia de ler — que e' a
    razao de a confirmacao de paciente acontecer uma vez por conversa.
    """
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_adult", _SEM_BANDEIRA)
    inferencia = _Inferencia(
        [
            _extracao(population="adult", sintoma_codigo="febre", intensidade="moderada", idade_anos=30),
            _extracao(intent="information", population="none"),
        ]
    )
    g = _grafo(inferencia, dmn)

    t1 = await _turno(g, _estado("tenho 30 anos e estou com febre"))
    t2 = await _turno(g, _estado("qual o telefone da central?", memoria_clinica=t1["memoria_clinica"]))

    assert t2.get("memoria_a_confirmar") is None
