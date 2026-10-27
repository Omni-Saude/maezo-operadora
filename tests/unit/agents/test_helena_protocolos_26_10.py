"""Teste do dono de 26/10/2026 (logs `/ecs/maezo-operadora-dev/agent-helena`, 16:39-17:12 UTC): o modelo deu
`ReadTimeout` em quase metade das chamadas e

1. as respostas obvias as perguntas do protocolo ("nehuma", "eu ja' respondi nao para todas") foram lidas
   errado: agora o CODIGO le' o obvio (todas nao, sim em algumas, todas sim) e o modelo so' o resto;
2. o "nao" a oferta da equipe recomecou as perguntas: agora a recusa curta fecha a queixa;
3. a escolha do protocolo caiu sempre no alerta universal: agora a queixa e' lida pelo jeito de falar antes;
4. a falha do classificador abriu caso `falha_tecnica`: agora pede para repetir e guarda a pendencia.

Grafo COMPILADO com o modelo falso (as falhas sao `TimeoutError`, como o `ReadTimeout`); 3 frases ou mais por
comportamento, com o controle negativo. Nenhum dado pessoal (as falas do dono vao parafraseadas)."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.graph import RESPOSTA_PECA_REENVIO
from maezo.agents.helena.habilidades.atendimento_humano.textos import (
    PENDENTE_OFERTA_EQUIPE,
    RESPOSTA_RECUSA_DE_EQUIPE_SAUDE,
)
from maezo.agents.helena.habilidades.protocolo import leitura_sem_modelo as lsm
from maezo.agents.helena.habilidades.protocolo import nucleo as n
from maezo.agents.helena.habilidades.protocolo import sinonimos
from maezo.agents.helena.habilidades.sintoma.textos import TEXTO_OFERTA_EQUIPE
from maezo.conhecimento import base_saude as bs
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_protocolos import (
    _BASE_REAL,
    _CATALOGO,
    _TEXTOS,
    _UNIVERSAL,
    _URINAR,
    _classify,
    _escalou,
    _estado,
    _grafo,
    _leitura,
    _Modelo,
    _starts,
)

_BASE = bs.carregar_base(_BASE_REAL)
_MEMBRO = _CATALOGO.protocolos["tri-adulto-dor-no-braco-ou-perna"]
_PALPITACOES = _CATALOGO.protocolos["tri-adulto-palpitacoes"]
_K = len(_URINAR.perguntas_de_alerta)


class _Fora(_Modelo):
    """O modelo falso com as chamadas de `fora` em prazo estourado (o `ReadTimeout` do teste do dono)."""

    def __init__(self, *args: Any, fora: tuple[str, ...] = (), **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.fora = fora
        self.chamadas: dict[str, int] = {}

    async def generate(self, prompt: str, **kw: Any) -> str:
        tipo = (
            "roteador"
            if "roteador de um assistente" in prompt
            else "escolha"
            if "Voce escolhe o PROTOCOLO" in prompt
            else "leitura"
            if "PERGUNTAS DE ALERTA que um assistente" in prompt
            else "pre"
            if "JA RESPONDIDAS" in prompt
            else "outras"
        )
        self.chamadas[tipo] = self.chamadas.get(tipo, 0) + 1
        if tipo in self.fora:
            raise TimeoutError("prazo")
        return await super().generate(prompt, **kw)


# --- 1. A leitura sem modelo (nucleo puro) ---------------------------------------------------------------
_TODAS_NAO = [
    "nenhuma",
    "Nehuma",
    "nenhma",
    "não",
    "nao",
    "nenhum",
    "nenhum desses",
    "não para todas",
    "Não, eu respondi NÃO para todas as suas perguntas",
    "Pqp, eu já respondi não para todas as perguntas acima",
    "já respondi não",
]


@pytest.mark.parametrize("fala", _TODAS_NAO)
def test_todas_nao_e_lido_sem_modelo(fala: str) -> None:
    assert lsm.ler_sem_modelo(fala, _K) == ("nao",) * _K
    assert lsm.nega_tudo(fala)


@pytest.mark.parametrize(
    ("fala", "sim"),
    [
        ("a 2 sim", {2}),
        ("só a 3", {3}),
        ("sim na 1", {1}),
        ("1 e 3 sim", {1, 3}),
        ("a primeira sim", {1}),
        ("só a 3, as outras não", {3}),
    ],
)
def test_sim_em_algumas_e_lido_sem_modelo(fala: str, sim: set[int]) -> None:
    esperado = tuple("sim" if i in sim else "nao" for i in range(1, _K + 1))
    assert lsm.ler_sem_modelo(fala, _K) == esperado
    assert not lsm.nega_tudo(fala)


@pytest.mark.parametrize("fala", ["todas", "sim para todas", "todas sim"])
def test_todas_sim_so_quando_nao_ha_duvida(fala: str) -> None:
    assert lsm.ler_sem_modelo(fala, _K) == ("sim",) * _K


def test_sim_sozinho_so_vale_com_uma_pergunta() -> None:
    assert lsm.ler_sem_modelo("sim", _K) is None
    assert lsm.ler_sem_modelo("sim", 1) == ("sim",)


@pytest.mark.parametrize(
    "fala",
    [
        "não, mas tô com falta de ar agora",
        "não sei",
        "acho que não",
        "não estou bem",
        "o nenem",
        "a 1 não",
        "a 7 sim",
        "isso",
        "talvez a 2",
    ],
)
def test_controle_o_que_nao_e_obvio_vai_ao_modelo(fala: str) -> None:
    assert lsm.ler_sem_modelo(fala, _K) is None
    assert not lsm.nega_tudo(fala)


# --- 1. No grafo: a leitura sem modelo decide, mesmo com o modelo fora -----------------------------------
@pytest.mark.parametrize("fala", ["Nehuma", "Não, eu respondi NÃO para todas", "já respondi não para todas"])
async def test_todas_nao_com_o_modelo_fora_vai_ao_destino_sem_sinal(fala: str) -> None:
    modelo = _Fora([decisao_v7("sintoma", responde=True)], fora=("leitura",))
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(fala, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    assert _TEXTOS is not None
    assert whatsapp.enviados[-1] == n.texto_sem_sinal(_URINAR, _TEXTOS)
    assert modelo.chamadas.get("leitura", 0) == 0
    assert not final.get("coleta_pendente") and not _escalou(final) and not _starts(cib)


@pytest.mark.parametrize("fala", ["a 2 sim", "só a 2", "sim na 2"])
async def test_sim_em_uma_com_o_roteador_fora_decide_pela_regra(fala: str) -> None:
    modelo = _Fora([], fora=("roteador", "leitura"))
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(fala, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    assert whatsapp.enviados[-1].startswith(n.texto_do_destino_por_sinal("ATENDIMENTO_HOJE", "adulto"))
    assert "urologista" in whatsapp.enviados[-1]  # dono, 27/10: todo final diz o especialista
    assert not _escalou(final) and not _starts(cib)


@pytest.mark.parametrize(
    "fala",
    [
        "não, mas tô com falta de ar forte agora",
        "não, mas ele desmaiou agora",
        "nenhuma, só que estou com dor no peito forte",
    ],
)
async def test_controle_sinal_de_alarme_afirmado_vai_a_seguranca_antes(fala: str) -> None:
    modelo = _Fora(
        [decisao_v7("sintoma", responde=True)], leitura=[_leitura("nao", "nao", "nao", "nao", outro=True)]
    )
    grafo, whatsapp, _ = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(fala, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    assert _TEXTOS is not None
    assert whatsapp.enviados[-1] != n.texto_sem_sinal(_URINAR, _TEXTOS)
    assert final.get("escalation_motivo") == "red_flag_clinico"


# --- 2. O "nao" a oferta da equipe fecha a queixa --------------------------------------------------------
_OFERTA_DEPOIS_DO_PROTOCOLO = [
    n.texto_das_perguntas(_UNIVERSAL),
    n.texto_das_perguntas(_UNIVERSAL, tuple(range(1, len(_UNIVERSAL.perguntas_de_alerta) + 1))),
    TEXTO_OFERTA_EQUIPE,
]


@pytest.mark.parametrize(
    "fala",
    [
        "não",
        "Não, eu respondi NÃO para todas as suas perguntas",
        "Pqp, eu já respondi não para todas as perguntas acima",
        "nao obrigado",
    ],
)
@pytest.mark.parametrize("roteador_fora", [True, False])
async def test_nao_a_oferta_da_equipe_recebe_a_recusa_curta_e_nunca_as_perguntas(
    fala: str, roteador_fora: bool
) -> None:
    modelo = _Fora(
        [decisao_v7("sintoma", responde=True)],
        fora=("roteador",) if roteador_fora else (),
        outras=[_classify(intent="symptom", intensidade="leve")] * 4,
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala, _OFERTA_DEPOIS_DO_PROTOCOLO, PENDENTE_OFERTA_EQUIPE))
    assert whatsapp.enviados[-1] == RESPOSTA_RECUSA_DE_EQUIPE_SAUDE
    assert modelo.chamadas.get("escolha", 0) == 0 and modelo.chamadas.get("pre", 0) == 0
    assert not final.get("coleta_pendente") and not _escalou(final) and not _starts(cib)


@pytest.mark.parametrize(
    "fala", ["ainda estou com aquela dor", "continua a mesma coisa", "e agora, o que eu faço?"]
)
async def test_depois_da_recusa_a_mesma_queixa_nunca_recebe_as_perguntas_de_novo(fala: str) -> None:
    falas = [*_OFERTA_DEPOIS_DO_PROTOCOLO, RESPOSTA_RECUSA_DE_EQUIPE_SAUDE]
    modelo = _Fora(
        [decisao_v7("sintoma")],
        escolha=['{"publico":"adulto","faixa_crianca":"nao_se_aplica","protocolo":"nenhum"}'],
        outras=[_classify(intent="symptom", intensidade="leve")] * 4,
    )
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala, falas))
    texto = whatsapp.enviados[-1]
    assert not texto.startswith(n.cabecalho(_UNIVERSAL)) and "1)" not in texto
    assert not str(final.get("coleta_pendente") or "").startswith(n.PENDENTE_ALERTA)
    assert not _starts(cib)


async def test_controle_sim_a_oferta_abre_o_caso() -> None:
    modelo = _Fora([], fora=("roteador",))
    grafo, _, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado("sim, pode passar", _OFERTA_DEPOIS_DO_PROTOCOLO, PENDENTE_OFERTA_EQUIPE)
    )
    assert final.get("escalation_motivo") == "solicitacao_humano" and _starts(cib) == 1


# --- 3. A escolha do protocolo sem o modelo, pelo jeito de falar -----------------------------------------
@pytest.mark.parametrize(
    ("fala", "protocolo"),
    [
        ("entorse no tornozelo", _MEMBRO.id),
        ("palpitações e cansaço", _PALPITACOES.id),
        ("dores nas articulações e fraqueza muscular", _MEMBRO.id),
        ("estou com dificuldades ao urinar", _URINAR.id),
        ("bati a cabeça e agora estou com dor de cabeça", "tri-adulto-queda-ou-trauma"),
    ],
)
def test_sinonimos_escolhem_o_protocolo_da_queixa(fala: str, protocolo: str) -> None:
    assert sinonimos.escolher(_CATALOGO, _BASE, fala, "adulto") == protocolo


@pytest.mark.parametrize(
    "fala", ["estou passando mal", "estou com uma dor estranha", "tenho um problema de saúde"]
)
def test_controle_sem_queixa_reconhecida_fica_o_alerta_universal(fala: str) -> None:
    assert sinonimos.escolher(_CATALOGO, _BASE, fala, "adulto") is None


@pytest.mark.parametrize(
    ("fala", "protocolo"),
    [
        ("estou com uma entorse no tornozelo", _MEMBRO),
        ("tenho palpitações e cansaço", _PALPITACOES),
        ("estou com dores nas articulações e fraqueza muscular", _MEMBRO),
    ],
)
async def test_escolha_fora_do_prazo_usa_os_sinonimos_no_grafo(fala: str, protocolo: Any) -> None:
    modelo = _Fora([decisao_v7("sintoma")], fora=("escolha", "pre"))
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(protocolo)
    assert final["coleta_pendente"] == n.codigo_do_alerta(protocolo.id) and not _starts(cib)


async def test_controle_escolha_fora_sem_queixa_reconhecida_vai_ao_alerta_universal() -> None:
    modelo = _Fora([decisao_v7("sintoma")], fora=("escolha", "pre"))
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado("estou com uma dor estranha"))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_UNIVERSAL)


# --- 4. Falha tecnica nunca abre caso sozinha --------------------------------------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "A minha filha está com febre desde ontem",
        "estou com dor de cabeça desde ontem",
        "meu marido está com tosse faz dois dias",
    ],
)
@pytest.mark.parametrize("segunda_vez", [False, True])
async def test_classify_fora_pede_para_repetir_sem_abrir_caso(fala: str, segunda_vez: bool) -> None:
    modelo = _Fora([], fora=("roteador", "outras"))
    grafo, whatsapp, cib = _grafo(modelo)
    falas = [RESPOSTA_PECA_REENVIO] if segunda_vez else None
    final = await grafo.ainvoke(_estado(fala, falas))
    assert whatsapp.enviados[-1] == RESPOSTA_PECA_REENVIO
    assert not _escalou(final) and not _starts(cib)


@pytest.mark.parametrize(
    "fala",
    [
        "meu pai desmaiou e não acorda",
        "estou com dor no peito muito forte e falta de ar",
        "minha filha está convulsionando agora",
    ],
)
async def test_controle_alarme_afirmado_com_o_modelo_fora_escala(fala: str) -> None:
    modelo = _Fora([], fora=("roteador", "outras", "escolha", "leitura", "pre"))
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    # O alarme afirmado escala como sempre (pela tabela, ou o escalonamento de antes com o modelo fora).
    assert final.get("escalation_motivo") in ("red_flag_clinico", "falha_tecnica")
    assert whatsapp.enviados[-1] != RESPOSTA_PECA_REENVIO and _starts(cib) == 1
