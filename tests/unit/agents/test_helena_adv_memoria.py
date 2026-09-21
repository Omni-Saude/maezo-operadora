"""ADVERSARIAL (21/09/2026) — a memoria clinica nos limites: 0, 24, 25, a janela exata e a
incoerencia que a fusao NAO filtra.

`test_helena_memoria_coerente.py` prova as regras novas (lastro, coerencia, correcao) com os
turnos medidos. Este arquivo ataca o que fica nas BORDAS delas:

  * `idade_meses=0` — um recem-nascido. Zero e' falso em Python, e toda a cadeia (validacao ->
    fusao -> gravacao -> frase) usa `is not None`; um unico `if not valor` em qualquer um dos
    quatro pontos apagaria o paciente mais fragil da carteira. Aqui isso e' cobrado nos QUATRO.
  * 24 e 25 meses — o limiar da frase, nos dois lados exatos, e nao "acima de dois anos".
  * a janela de 6 horas no SEGUNDO exato, e nao +-0,1 hora.
  * a COERENCIA e' de mao unica: ela filtra a idade que vem da MEMORIA e nao a que vem da
    MENSAGEM. O ultimo teste do arquivo mostra o que isso custa.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import (
    MEMORIA_CLINICA_JANELA_HORAS,
    HelenaGraph,
    HelenaState,
    _coerce_age,
    _correcao_de_dado_avaliado,
    _frase_de_confirmacao,
    _fundir_memoria_clinica,
    _marcar_avaliacao,
    _memoria_a_gravar,
    _memoria_clinica_valida,
    _populacao_tem_lastro,
)
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

AGORA = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


class _InferenciaFixa:
    def __init__(self, texto: str = "irrelevante") -> None:
        self.texto = texto
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        return self.texto


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append((to_hash, text))
        return {"ok": True}


def _graph() -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, _InferenciaFixa()),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=cast(Any, _WhatsApp()),
    )


def _memoria(**campos: Any) -> dict[str, Any]:
    bruta = {"gravado_em": AGORA.isoformat()}
    bruta.update(campos)
    return bruta


def _extracao(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "symptom",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "idade_anos": None,
        "idade_meses": None,
        "idade_gestacional_semanas": None,
    }
    base.update(campos)
    return base


# =================================================================================================
# 1. ZERO nao e' ausencia — o recem-nascido nos quatro pontos da cadeia
# =================================================================================================


def test_zero_meses_atravessa_validacao_fusao_gravacao_e_frase() -> None:
    """`idade_meses=0` e' um bebe de dias, e e' o paciente com mais red flag na tabela pediatrica.

    Os quatro pontos em uma unica cadeia, porque e' assim que o turno roda: se qualquer um deles
    trocasse `is not None` por verdade/falsidade, o zero viraria "nao sei a idade" — e a tabela
    pediatrica perderia exatamente as regras de lactente.
    """
    valida = _memoria_clinica_valida(_memoria(population="pediatric", idade_meses=0), agora=AGORA)
    assert valida is not None and valida["idade_meses"] == 0

    fundida, veio = _fundir_memoria_clinica(_extracao(), valida)
    assert fundida["idade_meses"] == 0
    assert veio["idade_meses"] == 0

    proxima = _memoria_a_gravar(fundida, valida, agora=AGORA, confirmada=False)
    assert proxima is not None and proxima["idade_meses"] == 0

    assert "0 meses" in _frase_de_confirmacao(veio)


def test_zero_semanas_e_zero_anos_tambem_atravessam() -> None:
    """O mesmo zero nos outros dois campos de idade — um `or` no lugar do `is not None` em
    qualquer um deles some com o dado sem erro nenhum."""
    for campo, populacao in (("idade_anos", "adult"), ("idade_gestacional_semanas", "gestante")):
        valida = _memoria_clinica_valida(_memoria(population=populacao, **{campo: 0}), agora=AGORA)
        assert valida is not None and valida[campo] == 0
        fundida, veio = _fundir_memoria_clinica(_extracao(), valida)
        assert fundida[campo] == 0, f"{campo}=0 nao atravessou a fusao"
        assert veio[campo] == 0


# =================================================================================================
# 2. O limiar da frase, nos dois lados exatos
# =================================================================================================


@pytest.mark.parametrize(
    ("meses", "esperado"),
    [
        (0, "seu bebe de 0 meses"),
        (1, "seu bebe de 1 meses"),
        (11, "seu bebe de 11 meses"),
        (24, "seu bebe de 24 meses"),
        (25, "sua crianca de 2 anos"),
        (30, "sua crianca de 2 anos"),
        (36, "sua crianca de 3 anos"),
        (47, "sua crianca de 3 anos"),
        (48, "sua crianca de 4 anos"),
    ],
)
def test_a_frase_de_confirmacao_no_limiar_exato(meses: int, esperado: str) -> None:
    """24 e' INCLUSIVE em "bebe" e 25 vira anos — o limiar declarado, medido nos dois lados.

    30 meses tem de sair "2 anos" (divisao inteira, como se fala), e nao "2 anos e meio": a frase
    existe PARA a pessoa corrigir, e uma frase que soa errada e' uma frase que ela para de ler.
    """
    frase = _frase_de_confirmacao({"idade_meses": meses})

    assert esperado in frase
    if meses > 24:
        assert "bebe" not in frase
    else:
        assert "anos" not in frase


@pytest.mark.parametrize(
    ("lembrados", "esperado"),
    [
        ({"idade_anos": 0}, "a pessoa de 0 anos"),
        ({"idade_anos": 70}, "a pessoa de 70 anos"),
        ({"idade_gestacional_semanas": 33}, "a gestacao de 33 semanas"),
        ({"population": "pediatric"}, "sua crianca"),
        ({"population": "gestante"}, "a gestacao"),
        ({"population": "adult"}, "a mesma pessoa de antes"),
        ({}, "a mesma pessoa de antes"),
    ],
)
def test_a_frase_cobre_todos_os_ramos_e_nunca_fica_sem_sujeito(
    lembrados: dict[str, Any], esperado: str
) -> None:
    """Todos os ramos do `elif`, incluindo o ultimo: uma frase sem sujeito ("entendi que voce esta
    falando sobre , certo?") chegaria ao beneficiario como sistema quebrado."""
    frase = _frase_de_confirmacao(lembrados)

    assert esperado in frase
    assert frase.startswith("entendi que voce esta falando sobre ")
    assert frase.endswith(", certo?")


def test_a_precedencia_da_frase_e_meses_antes_de_anos() -> None:
    """Com os dois campos preenchidos a frase fala em MESES — e' a leitura que o `D2` expos
    ("seu bebe de 36 meses" enquanto a tabela consultada era a de adulto). A precedencia
    permanece; o que a entrega consertou foi a COERENCIA a montante, nao esta ordem."""
    frase = _frase_de_confirmacao({"idade_meses": 36, "idade_anos": 30})

    assert "3 anos" in frase and "30 anos" not in frase


# =================================================================================================
# 3. A janela de 6 horas no segundo exato
# =================================================================================================


@pytest.mark.parametrize(
    ("delta", "valida"),
    [
        (timedelta(0), True),
        (timedelta(hours=MEMORIA_CLINICA_JANELA_HORAS), True),
        (timedelta(hours=MEMORIA_CLINICA_JANELA_HORAS, seconds=1), False),
        (timedelta(seconds=-1), False),
        (timedelta(days=-1), False),
    ],
)
def test_a_janela_e_inclusiva_no_limite_e_recusa_o_futuro(delta: timedelta, valida: bool) -> None:
    """`> janela * 3600` — entao 6h EXATAS valem e 6h+1s nao. O passado negativo (carimbo no
    futuro, relogio torto ou valor plantado) e' recusado no mesmo `if`."""
    bruta = _memoria(population="pediatric", idade_meses=11)
    bruta["gravado_em"] = (AGORA - delta).isoformat()

    assert (_memoria_clinica_valida(bruta, agora=AGORA) is not None) is valida


def test_carimbo_sem_fuso_e_lido_como_utc_e_nao_derruba_o_turno() -> None:
    """Um carimbo naive (sem `+00:00`) e' tratado como UTC. Se em vez disso o `-` levantasse
    `TypeError`, a memoria derrubaria o turno inteiro — `TypeError` esta' em `PROGRAMMING_ERRORS`
    e propaga."""
    bruta = _memoria(population="pediatric", idade_meses=11)
    bruta["gravado_em"] = AGORA.replace(tzinfo=None).isoformat()

    assert _memoria_clinica_valida(bruta, agora=AGORA) is not None


@pytest.mark.parametrize(
    "carimbo",
    ["", "ontem", "2026-13-45T99:99:99", None, 1758456000, {"iso": "2026-09-21"}, []],
)
def test_carimbo_invalido_falha_para_none_e_nunca_para_um_valor_parcial(carimbo: Any) -> None:
    """Fail-closed da fronteira: `None` degrada para o comportamento conhecido (cada turno comeca
    do zero); um valor PARCIAL seria um paciente parcialmente lembrado."""
    bruta = _memoria(population="pediatric", idade_meses=11)
    bruta["gravado_em"] = carimbo

    assert _memoria_clinica_valida(bruta, agora=AGORA) is None


async def test_memoria_expirada_desaparece_no_receive_e_o_turno_segue() -> None:
    """A validacao mora na FRONTEIRA (`receive`), nao no ponto de uso: uma memoria de 7 horas
    atras nao chega a `classify` como meia-verdade."""
    graph = _graph()
    velha = _memoria(population="pediatric", idade_meses=36)
    velha["gravado_em"] = (datetime.now(UTC) - timedelta(hours=7)).isoformat()

    reset = await graph.receive(
        cast(
            HelenaState,
            {
                "tenant_id": "amh",
                "conversation_id": "wa:amh:adv_mem",
                "canal": "whatsapp",
                "beneficiario_pseudo_id": "PSEUDO-ADV",
                "message_body": "desde ontem",
                "memoria_clinica": velha,
            },
        )
    )

    assert reset["memoria_clinica"] is None
    assert reset.get("error") is None


# =================================================================================================
# 4. A fronteira da idade: o que e' idade para a memoria tambem e' idade para a extracao
# =================================================================================================


@pytest.mark.parametrize(
    "valor",
    [-1, -36, True, False, 1.5, 36.0, "36", " 36 ", "trinta e seis", "3²", [36], {"meses": 36}],
)
def test_a_memoria_recusa_toda_idade_que_nao_seja_inteiro_nao_negativo(valor: Any) -> None:
    """`bool` primeiro, porque `True` passaria por `isinstance(v, int)` e viraria idade 1; float,
    string (mesmo "36") e estrutura sao recusados sem excecao.

    A recusa e' da MEMORIA INTEIRA, e nao do campo: um paciente parcialmente lembrado e' o modo de
    falha que esta frente existe para acabar.
    """
    assert _memoria_clinica_valida(_memoria(population="pediatric", idade_meses=valor), agora=AGORA) is None


def test_idade_ausente_nao_invalida_a_memoria_mas_tambem_nao_e_gravada() -> None:
    """`None` numa idade e' AUSENCIA, nao valor invalido: a memoria segue valida pelo que ela tem
    (a populacao), e o campo simplesmente nao existe no resultado — o que e' o que faz a fusao
    depois nao ter nada a atravessar. Uma memoria que so' tem ausencia nao vale lembrar."""
    limpa = _memoria_clinica_valida(
        _memoria(population="pediatric", idade_meses=None, idade_anos=None), agora=AGORA
    )

    assert limpa is not None
    assert "idade_meses" not in limpa and "idade_anos" not in limpa
    assert _memoria_clinica_valida(_memoria(), agora=AGORA) is None


@pytest.mark.parametrize("valor", [0, 1, 24, 36, 1200])
def test_toda_idade_que_a_memoria_aceita_a_extracao_tambem_aceitaria(valor: int) -> None:
    """As duas fronteiras (memoria e extracao) julgam o MESMO conceito em modulos diferentes.

    A direcao cobrada e' a que importa: nada pode ser gravado na memoria que o validador da
    extracao recusaria, senao um dado lembrado chegaria a DMN por um caminho que o dado novo nao
    conseguiria. A recíproca nao vale de proposito — `_coerce_age` aceita `"36"` (JSON de modelo
    manda numero entre aspas) e a memoria nao, o que e' o lado seguro.

    `1200` esta' na lista como o que ele e': 100 anos em meses passa nas DUAS fronteiras. Nenhuma
    das duas tem limite superior — registrado aqui, nao corrigido aqui.
    """
    valida = _memoria_clinica_valida(_memoria(population="pediatric", idade_meses=valor), agora=AGORA)
    assert valida is not None and valida["idade_meses"] == valor

    ok, coagido = _coerce_age(valor)
    assert ok is True and coagido == valor


# =================================================================================================
# 5. Lastro e correcao: as bordas das tres condicoes
# =================================================================================================


@pytest.mark.parametrize(
    ("nova", "lembrada", "campos", "tem_lastro"),
    [
        ("adult", "adult", {}, True),
        ("adult", "pediatric", {}, False),
        ("adult", "pediatric", {"idade_anos": 34}, True),
        ("adult", "pediatric", {"idade_anos": 0}, True),
        ("pediatric", "adult", {}, False),
        ("pediatric", "adult", {"idade_meses": 11}, True),
        ("gestante", "adult", {}, False),
        ("gestante", "adult", {"idade_gestacional_semanas": 26}, True),
        ("mental_health", "pediatric", {}, True),
        ("none", "pediatric", {}, True),
    ],
)
def test_o_lastro_exige_o_campo_de_idade_daquela_populacao(
    nova: str, lembrada: str, campos: dict[str, Any], tem_lastro: bool
) -> None:
    """A matriz inteira, com `idade_anos=0` incluido: um lastro que dependesse de verdade/falsidade
    diria que "tenho 0 anos" nao e' afirmacao. `mental_health` nao tem campo de idade e e' a
    excecao declarada; `none` nunca chega aqui como afirmacao (o chamador trata antes)."""
    assert _populacao_tem_lastro(_extracao(**campos), nova, lembrada) is tem_lastro


def test_correcao_exige_as_tres_condicoes_e_cada_uma_derruba_sozinha() -> None:
    """As tres condicoes de `_correcao_de_dado_avaliado`, uma a uma, com a MESMA correcao de
    idade (30 -> 70) que o `D3` mediu. Um gatilho largo aqui reabriria a triagem em toda pergunta
    administrativa; um estreito perde o `D3`."""
    avaliada = _memoria(
        population="adult", idade_anos=30, sintoma_avaliado="febre", intensidade_avaliada="moderada"
    )
    corrige = _extracao(population="adult", idade_anos=70)

    # 1. sem avaliacao nesta conversa -> nada a refazer.
    sem_marca = {k: v for k, v in avaliada.items() if k != "sintoma_avaliado"}
    assert _correcao_de_dado_avaliado(corrige, sem_marca) is None
    assert _correcao_de_dado_avaliado(corrige, None) is None
    assert _correcao_de_dado_avaliado(corrige, {}) is None

    # 2. a mensagem traz sintoma proprio -> o caminho normal resolve.
    assert _correcao_de_dado_avaliado(_extracao(**{**corrige, "sintoma_codigo": "febre"}), avaliada) is None

    # 3. o valor NAO e' diferente -> repetir o dado nao e' corrigir.
    assert _correcao_de_dado_avaliado(_extracao(population="adult", idade_anos=30), avaliada) is None

    # As tres satisfeitas -> reavalia com o sintoma e a intensidade que a avaliacao usou.
    achado = _correcao_de_dado_avaliado(corrige, avaliada)
    assert achado == {
        "sintoma_codigo": "febre",
        "intensidade": "moderada",
        "corrigidos": {"idade_anos": 70},
    }


def test_correcao_ignora_ausencia_e_o_none_de_populacao() -> None:
    """Ausencia nao e' correcao (a regra 3 do documento, do outro lado) — nem `None` numa idade,
    nem o literal `"none"` numa populacao, que e' o que o modelo devolve para "a mensagem nao e'
    sobre ninguem em particular"."""
    avaliada = _memoria(
        population="pediatric", idade_meses=36, sintoma_avaliado="febre", intensidade_avaliada="leve"
    )

    assert _correcao_de_dado_avaliado(_extracao(population="none"), avaliada) is None
    assert _correcao_de_dado_avaliado(_extracao(population="pediatric"), avaliada) is None


def test_correcao_de_populacao_com_lastro_e_uma_correcao() -> None:
    """O outro eixo do `D3`: "na verdade e' pra mim mesma, tenho 34 anos" corrige QUEM e' o
    paciente, e a reavaliacao tem de sair com o campo trocado — nao so' com a idade."""
    avaliada = _memoria(
        population="pediatric", idade_meses=36, sintoma_avaliado="febre", intensidade_avaliada="moderada"
    )
    fundida, _ = _fundir_memoria_clinica(_extracao(population="adult", idade_anos=34), avaliada)

    achado = _correcao_de_dado_avaliado(fundida, avaliada)

    assert achado is not None
    assert achado["corrigidos"]["population"] == "adult"
    assert achado["sintoma_codigo"] == "febre"


@pytest.mark.parametrize("intensidade", [None, "", "forte", "GRAVE", 3, True])
def test_intensidade_avaliada_invalida_degrada_para_desconhecida_na_reavaliacao(intensidade: Any) -> None:
    """A reavaliacao nao pode herdar uma intensidade fora do vocabulario: a DMN casa `grave` no
    proprio fail-safe, e um valor cru passaria por la' sem casar regra nenhuma.

    Duas portas, o mesmo default: `_marcar_avaliacao` na entrada e `_correcao_de_dado_avaliado`
    na saida.
    """
    marcada = _marcar_avaliacao(
        _memoria(population="adult", idade_anos=30), sintoma_codigo="febre", intensidade=intensidade
    )
    assert marcada is not None and marcada["intensidade_avaliada"] == "desconhecida"

    crua = _memoria(
        population="adult", idade_anos=30, sintoma_avaliado="febre", intensidade_avaliada=intensidade
    )
    achado = _correcao_de_dado_avaliado(_extracao(population="adult", idade_anos=70), crua)
    assert achado is not None and achado["intensidade"] == "desconhecida"


def test_a_marca_de_avaliacao_nao_muta_a_memoria_recebida() -> None:
    """`_marcar_avaliacao` devolve copia. Mutar o dict recebido faria o turno seguinte ler uma
    marca que este turno nunca gravou (e a memoria e' o unico estado que atravessa turnos)."""
    original = _memoria(population="adult", idade_anos=30)
    copia = dict(original)

    marcada = _marcar_avaliacao(original, sintoma_codigo="febre", intensidade="leve")

    assert original == copia
    assert marcada is not None and marcada is not original


def test_nada_a_lembrar_nao_apaga_o_que_ja_se_lembrava() -> None:
    """Um turno administrativo no meio de uma conversa clinica ("qual o telefone da central?")
    nao pode fazer a Helena esquecer o bebe — e a marca da avaliacao atravessa junto."""
    anterior = _memoria(
        population="pediatric", idade_meses=11, sintoma_avaliado="febre", intensidade_avaliada="moderada"
    )

    proxima = _memoria_a_gravar(_extracao(), anterior, agora=AGORA, confirmada=True)

    assert proxima == anterior


# =================================================================================================
# 6. O BURACO: a coerencia filtra a idade da MEMORIA e nao a da MENSAGEM
# =================================================================================================


def test_bug_valor_json_nao_hashavel_derruba_o_turno_em_vez_de_falhar_fechado() -> None:
    """REPROVA — bug real: as duas fronteiras usam `x in frozenset` sobre valor NAO validado, e
    um valor nao-hashavel levanta `TypeError` em vez de recusar.

    `TypeError` esta' em `PROGRAMMING_ERRORS` (`runtime/dependency_failures.py:83`), ou seja
    PROPAGA de proposito: o turno morre alto, sem resposta ao beneficiario, sem escalonamento e
    sem o desfecho de falha. Os dois sitios:

      1. `_memoria_clinica_valida` (graph.py:866, codigo NOVO desta entrega). O docstring declara
         "FALHA PARA `None`, NUNCA PARA UM VALOR PARCIAL" e a funcao e' chamada em `receive` sobre
         `state["memoria_clinica"]` — a fronteira que existe para nao confiar no valor recebido
         (T1.11). O campo `sintoma_avaliado` logo acima FAZ a guarda certa
         (`not isinstance(avaliado, str) or ...`); `intensidade_avaliada`, duas linhas abaixo, nao.
      2. `_validate_extraction` (graph.py:1373-1384, pre-existente). O docstring declara a postura
         "schema-invalid JSON -> escalate `falha_tecnica`, NEVER a silent inform default", e ela
         vale para todo valor HASHAVEL. Um modelo que responde
         `"sintoma_codigo": ["febre", "dispneia"]` para quem relatou dois sintomas — ou
         `"intensidade": {"valor": "grave"}` — derruba o turno nos quatro campos de dominio
         fechado (`intent`, `population`, `sintoma_codigo`, `intensidade`), enquanto os tres
         campos de IDADE tratam o mesmo caso corretamente (`_coerce_age` devolve
         `(False, None)` para lista/dict).

    Conserto: a mesma guarda que `sintoma_avaliado` e `_coerce_age` ja' usam —
    `isinstance(valor, str)` antes do `in`.
    """
    problemas: list[str] = []

    for valor in (["grave"], {"valor": "grave"}):
        try:
            resultado = _memoria_clinica_valida(
                _memoria(
                    population="adult", idade_anos=30, sintoma_avaliado="febre", intensidade_avaliada=valor
                ),
                agora=AGORA,
            )
        except Exception as exc:  # noqa: BLE001 — o achado E' o tipo da excecao
            problemas.append(f"_memoria_clinica_valida({valor!r}) levantou {type(exc).__name__}: {exc}")
        else:
            if resultado is not None:
                problemas.append(f"_memoria_clinica_valida({valor!r}) aceitou um valor nao-hashavel")

    from maezo.agents.helena.graph import _validate_extraction

    for campo, valor in (
        ("intent", ["symptom"]),
        ("population", ["adult"]),
        ("sintoma_codigo", ["febre", "dispneia"]),
        ("intensidade", {"valor": "grave"}),
    ):
        bruta = {
            "intent": "symptom",
            "population": "adult",
            "psychosocial_risk": False,
            "sintoma_codigo": None,
            "intensidade": "leve",
            campo: valor,
        }
        try:
            motivo = _validate_extraction(bruta)
        except Exception as exc:  # noqa: BLE001 — o achado E' o tipo da excecao
            problemas.append(f"_validate_extraction({campo}={valor!r}) levantou {type(exc).__name__}: {exc}")
        else:
            if motivo is None:
                problemas.append(f"_validate_extraction({campo}={valor!r}) aceitou um valor nao-hashavel")

    assert not problemas, "\n  ".join(problemas)


def test_bug_idade_da_mensagem_no_campo_errado_deixa_a_idade_velha_decidir() -> None:
    """REPROVA — bug real: a regra de COERENCIA da fusao e' de mao unica.

    CENARIO, o mesmo `D2` um turno adiante. A conversa lembra `population=pediatric` +
    `idade_meses=36`. A mae escreve "na verdade ele tem 5 anos" e o modelo devolve
    `population=pediatric` com a idade em `idade_anos=5` (o `classify_prompt` pede `idade_meses`
    para pediatric, mas "5 anos" e' como a frase chega, e `_validate_extraction` NAO cruza campo
    de idade com populacao — so' valida cada um isoladamente).

    O QUE A FUSAO FAZ: `idade_meses` da mensagem e' `None`, entao a idade LEMBRADA (36) atravessa
    — a regra de coerencia so' pergunta se o campo pertence a populacao final, nunca se a mensagem
    ja' trouxe aquela idade em OUTRO campo. O resultado e' a extracao fundida com 5 anos E 36
    meses ao mesmo tempo, e `_evaluate_dmn` na tabela pediatrica le' `idade_meses` — ou seja, a
    idade de DOIS turnos atras decide a regra, e o dado que a pessoa acabou de dar e' descartado em
    silencio. A frase de confirmacao ainda diz "sua crianca de 3 anos", contradizendo o que ela
    escreveu no mesmo turno.

    E' a mesma familia do F4 ("a memoria guarda a idade e a chamada da tabela nao usa"), pelo
    avesso: agora a mensagem traz a idade e a tabela usa a da memoria.

    Conserto de menor superficie: no passo 2 da fusao, uma idade lembrada nao atravessa quando a
    mensagem trouxe QUALQUER campo de idade (a pessoa esta' falando de idade agora) — ou a
    incoerencia populacao x campo vira recusa de extracao, fail-closed como todo o resto.
    """
    lembrada = _memoria_clinica_valida(
        _memoria(
            population="pediatric",
            idade_meses=36,
            sintoma_avaliado="febre",
            intensidade_avaliada="moderada",
        ),
        agora=AGORA,
    )
    assert lembrada is not None

    fundida, veio = _fundir_memoria_clinica(_extracao(population="pediatric", idade_anos=5), lembrada)
    idades = {k: v for k, v in fundida.items() if k.startswith("idade")}

    assert fundida.get("idade_meses") != 36, (
        "a idade lembrada (36 meses) sobreviveu ao lado da idade que a mensagem acabou de trazer "
        f"(5 anos) e e' ela que a tabela pediatrica le' (graph.py:921 passo 2 de "
        f"`_fundir_memoria_clinica`); fundida={idades}, "
        f"frase={_frase_de_confirmacao(veio)!r}"
    )
