"""Um fato apurado-e-desfavoravel nao pode chegar ao modelo com a mesma cara de "nao apurado".

DE ONDE VEM ESTE ARQUIVO (CC-11 / RAF-12 — auditoria da frota, 04/09/2026)

O incidente de 24/08/2026 esta contado por inteiro em
`tests/unit/agents/test_rafael_dossier_fatos.py` e em `_FATOS_BOOLEANOS` (rafael/graph.py):
num caso com prestador FORA da rede, o dossie escreveu "nao ha registro de verificacao de
teto L2 ou rede credenciada". A rede TINHA sido verificada e dado FALSO. A causa nao era
descarte na montagem — era a PASSAGEM PARA O MODELO: os fatos iam para o prompt como repr de
dicionario Python (`'rede_credenciada': False, 'dentro_teto_l2': None`), onde `False` e `None`
parecem igualmente "vazios".

O conserto de 24/08 foi feito NUM AGENTE SO'. A auditoria da frota encontrou o mesmo veiculo
do defeito vivo em 8 dos 9 agentes que passam fatos a um LLM — dez sitios de montagem de
prompt interpolando `fatos={facts}` / `fatos_agregados={facts}` crus:

    grep -n 'fatos={facts}\\|fatos_agregados={facts}' src/maezo/agents/*/graph.py

Este arquivo trava as duas metades da correcao:

1. A FENCE (`test_nenhum_grafo_interpola_fatos_crus`): nenhum `agents/*/graph.py` pode voltar
   a interpolar o dicionario de fatos cru numa f-string de prompt. E' uma checagem de AST, e
   nao de texto, porque o alvo e' exatamente a forma sintatica `...fatos={<expr>}` — um grep
   de texto casaria com o comentario que documenta a proibicao.

2. O COMPORTAMENTO, por sitio: cada montador de prompt renderiza SEU mapa de booleanos como
   SIM / NAO / SEM DADO, e a chave booleana NAO sobra no repr do contexto. Sao dois runs por
   sitio — todos os booleanos `False`, depois todos AUSENTES — porque a distincao que o
   incidente apagou e' precisamente entre esses dois estados.

3. A NAO-REGRESSAO DE RAFAEL (`test_rafael_render_e_byte_identico_ao_de_antes`): as tres
   saidas congeladas abaixo foram capturadas do rafael ANTES da extracao do helper. Elas sao a
   prova de que promover a funcao ao runtime nao mexeu num unico byte do prompt que o
   medico-auditor ja' recebe hoje — bytes de prompt alimentam proveniencia de auditoria
   (ADR-0007) e baselines de eval.

O QUE ESTES TESTES NAO SAO. Nao sao teste de modelo, e nao sao teste de decisao: o
renderizador nao decide nada, so' apresenta. Nenhum dicionario de fatos muda de conteudo aqui
— apenas a sua RENDERIZACAO no prompt.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_GRAFOS = sorted((_RAIZ / "src" / "maezo" / "agents").glob("*/graph.py"))

#: Os dois nomes de campo sob os quais um dicionario de fatos ja' viajou cru para um prompt.
#: `fatos_agregados` e' o do andre (agregados k-anon) — mesma forma, mesmo defeito.
_CHAVES_DE_FATOS: tuple[str, ...] = ("fatos=", "fatos_agregados=")


def _interpolacoes_cruas(fonte: str) -> list[str]:
    """Sitios em `fonte` onde uma f-string faz `...fatos={<expr>}` / `...fatos_agregados={<expr>}`.

    AST, e nao regex: o alvo e' a ADJACENCIA entre um literal terminado no nome do campo e uma
    interpolacao. Um comentario ou uma docstring que MENCIONE `fatos={facts}` (rafael tem um)
    nao e' um sitio de montagem e nao pode acusar. Literais adjacentes concatenados
    (`f"...\\n" f"fatos={x}"`) viram um unico `JoinedStr`, entao a checagem os cobre de graca.
    """
    achados: list[str] = []
    for no in ast.walk(ast.parse(fonte)):
        if not isinstance(no, ast.JoinedStr):
            continue
        partes = no.values
        for indice, parte in enumerate(partes[:-1]):
            if not isinstance(parte, ast.Constant) or not isinstance(parte.value, str):
                continue
            if not isinstance(partes[indice + 1], ast.FormattedValue):
                continue
            for chave in _CHAVES_DE_FATOS:
                if parte.value.endswith(chave):
                    achados.append(f"linha {no.lineno}: ...{chave}{{...}}")
    return achados


@pytest.mark.parametrize("grafo", _GRAFOS, ids=lambda p: p.parent.name)
def test_nenhum_grafo_interpola_fatos_crus(grafo: Path) -> None:
    """CC-11: o repr de dicionario colapsa `False` e `None` na mesma aparencia de vazio.

    Quem escrever um sitio novo por copia de outro — que e' exatamente como os 8 nasceram —
    esbarra aqui. Use `maezo.runtime.prompt_format.render_fatos_para_prompt` com o mapa de
    booleanos DAQUELE fluxo.
    """
    achados = _interpolacoes_cruas(grafo.read_text(encoding="utf-8"))
    assert not achados, (
        f"{grafo.relative_to(_RAIZ)} interpola o dicionario de fatos cru no prompt: {achados}. "
        "Um fato apurado-e-falso fica indistinguivel de um fato nao apurado (incidente de "
        "24/08/2026). Renderize com `render_fatos_para_prompt(facts, booleanos=...)`."
    )


# --- Byte-identidade de rafael -------------------------------------------------------------

#: Saidas de `rafael.graph.render_fatos_para_prompt` capturadas ANTES da extracao do helper
#: (base cc01 `1350bae`). Congeladas aqui de proposito: sao o unico teste de que a promocao ao
#: runtime foi uma refatoracao, e nao uma mudanca de prompt disfarcada.
_RAFAEL_CASOS: tuple[tuple[dict[str, Any], str], ...] = (
    (
        {},
        "fatos apurados:\n"
        "  SEM DADO  beneficiario com plano ativo\n"
        "  SEM DADO  carencia cumprida\n"
        "  SEM DADO  diretriz de utilizacao (DUT) atendida\n"
        "  SEM DADO  prestador na rede credenciada\n"
        "  SEM DADO  documentacao completa\n"
        "  APURADO PELO MOTOR  valor dentro do teto de aprovacao automatica\n"
        "\ndemais dados do caso: {}",
    ),
    (
        {
            "rede_credenciada": False,
            "carencia_cumprida": None,
            "beneficiario_ativo": True,
            "codigo_procedimento_tuss": "30306027",
            "valor_estimado_brl": 1234.5,
            "dentro_teto_l2": True,
        },
        "fatos apurados:\n"
        "  SIM       beneficiario com plano ativo\n"
        "  SEM DADO  carencia cumprida\n"
        "  SEM DADO  diretriz de utilizacao (DUT) atendida\n"
        "  NAO       prestador na rede credenciada\n"
        "  SEM DADO  documentacao completa\n"
        "  APURADO PELO MOTOR  valor dentro do teto de aprovacao automatica\n"
        "\ndemais dados do caso: {'codigo_procedimento_tuss': '30306027', "
        "'valor_estimado_brl': 1234.5, 'dentro_teto_l2': True}",
    ),
    (
        {
            "beneficiario_ativo": 1,
            "carencia_cumprida": "sim",
            "dut_atendida": False,
            "rede_credenciada": True,
            "documentacao_completa": None,
            "cid10": None,
            "dmn_refs": {},
        },
        "fatos apurados:\n"
        "  SEM DADO  beneficiario com plano ativo\n"
        "  SEM DADO  carencia cumprida\n"
        "  NAO       diretriz de utilizacao (DUT) atendida\n"
        "  SIM       prestador na rede credenciada\n"
        "  SEM DADO  documentacao completa\n"
        "  APURADO PELO MOTOR  valor dentro do teto de aprovacao automatica\n"
        "\ndemais dados do caso: {'cid10': None, 'dmn_refs': {}}",
    ),
)


@pytest.mark.parametrize("facts,esperado", _RAFAEL_CASOS, ids=["vazio", "tres-estados", "nao-booleanos"])
def test_rafael_render_e_byte_identico_ao_de_antes(facts: dict[str, Any], esperado: str) -> None:
    """A extracao para o runtime nao pode mover um byte do prompt do rafael.

    Inclui o caso `beneficiario_ativo=1` / `carencia_cumprida="sim"`: o `is True`/`is False`
    deliberado do renderizador tem de sobreviver a mudanca de casa — um `bool()` transformaria
    `1` e `"sim"` em afirmacao sobre o beneficiario.
    """
    from maezo.agents.rafael.graph import render_fatos_para_prompt

    assert render_fatos_para_prompt(facts) == esperado


# --- Comportamento por sitio de montagem ---------------------------------------------------


class _CapturaPrompt:
    """Duplo de `InferenceProvider` que so' guarda o prompt recebido.

    Aceita `**kwargs` porque os sitios passam `phi`/`agent_id`/`tenant_id`/`task_kind` em
    combinacoes diferentes, e o que este arquivo testa e' o primeiro argumento posicional.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return "narrativa sintetica"


def _beatriz(llm: Any) -> Any:
    from maezo.agents.beatriz.graph import BeatrizGraph

    return BeatrizGraph(inference=llm)


def _com_deps(classe: Any, llm: Any, **extra: Any) -> Any:
    """Instancia um grafo com apenas o seam de inferencia real.

    Os montadores de dossie/mensagem nao tocam DMN, engine, sink de auditoria nem WhatsApp —
    passar `None` neles mantem o teste sobre a RENDERIZACAO e faz qualquer uso acidental
    desses seams explodir em vez de passar silenciosamente.
    """
    return classe(inference=llm, dmn=None, cibseven=None, audit_sink=None, **extra)


#: Um caso por SITIO de montagem de prompt (nao por agente: fernando e lucas tem dois cada, e
#: gustavo/marina/andre trocam o conjunto de fatos conforme o fluxo). Campos:
#:   `sitio`     — identificador legivel `<agente>.<metodo>[/<fluxo>]`
#:   `monta`     — corotina que recebe (grafo, state) e monta o prompt
#:   `constroi`  — fabrica do grafo
#:   `base`      — state minimo que seleciona o fluxo
#:   `booleanos` — chave do fato -> rotulo esperado na linha
#:   `ausente`   — marcador esperado quando a chave NAO esta no state
_Sitio = dict[str, Any]


def _sitios() -> list[_Sitio]:
    from maezo.agents.andre.graph import AndreGraph
    from maezo.agents.carolina.graph import CarolinaGraph
    from maezo.agents.fernando.graph import FernandoGraph
    from maezo.agents.gustavo.graph import GustavoGraph
    from maezo.agents.lucas.graph import LucasGraph
    from maezo.agents.marina.graph import MarinaGraph
    from maezo.agents.valentina.graph import ValentinaGraph

    async def dossie(grafo: Any, state: dict[str, Any]) -> None:
        await grafo._build_dossier(state)

    async def dossie_com_rota(grafo: Any, state: dict[str, Any]) -> None:
        await grafo._build_dossier(state, route="human_review")

    async def mensagem(grafo: Any, state: dict[str, Any]) -> None:
        await grafo._build_message(state)

    return [
        {
            "sitio": "beatriz._build_dossier",
            "constroi": _beatriz,
            "monta": dossie,
            "base": {},
            "booleanos": {"indicio_fraude_sinalizado": "indicio de fraude sinalizado"},
            # `_facts` coage com `bool(state.get(..., False))`: a ausencia JA' e' um `False`
            # apurado neste sitio, e SEM DADO e' inalcancavel. Registrado, nao contornado —
            # mudar a coercao seria mexer no CONTEUDO dos fatos, que e' outro WP.
            "ausente": "NAO",
        },
        {
            "sitio": "carolina._build_dossier",
            "constroi": lambda llm: _com_deps(CarolinaGraph, llm),
            "monta": dossie_com_rota,
            "base": {"direcao": "descredenciamento"},
            "booleanos": {
                "licenca_valida": "licenca do prestador valida",
                "documentacao_completa": "documentacao completa",
                "dentro_criterios_rede": "dentro dos criterios de rede",
                "notificacao_previa_feita": "notificacao previa ao prestador feita",
                "substituto_equivalente_identificado": "prestador substituto equivalente identificado",
                "tem_beneficiarios_vinculados": "ha beneficiarios vinculados ao prestador",
                "indicio_irregularidade_sinalizado": "indicio de irregularidade sinalizado",
                "exige_notificacao_previa": "exige notificacao previa (saida de DMN)",
                "exige_substituto_equivalente": "exige substituto equivalente (saida de DMN)",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "andre._build_dossier/pagto_dossier",
            "constroi": lambda llm: _com_deps(AndreGraph, llm),
            "monta": dossie_com_rota,
            "base": {"flow": "pagto_dossier"},
            "booleanos": {
                "dados_pagamento_validos": "dados de pagamento validos",
                "lastro_confirmado": "lastro do pagamento confirmado",
                "dentro_teto_l2": "valor dentro do teto de alcada L2",
                "duplicidade_suspeita": "suspeita de duplicidade",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "andre._build_dossier/adequacao_dossier",
            "constroi": lambda llm: _com_deps(AndreGraph, llm),
            "monta": dossie_com_rota,
            "base": {"flow": "adequacao_dossier"},
            "booleanos": {
                "adequacao.cobertura_geo_suficiente": "cobertura geografica suficiente",
                "adequacao.dados_geo_completos": "dados geograficos completos",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "fernando._build_message",
            "constroi": lambda llm: _com_deps(FernandoGraph, llm, whatsapp=None),
            "monta": mensagem,
            "base": {},
            "booleanos": {"dentro_janela_purga": "dentro da janela de purga"},
            "ausente": "SEM DADO",
        },
        {
            "sitio": "fernando._build_dossier",
            "constroi": lambda llm: _com_deps(FernandoGraph, llm, whatsapp=None),
            "monta": dossie,
            "base": {},
            "booleanos": {
                "dentro_periodo_minimo": "dentro do periodo minimo de inadimplencia",
                "notificacao_previa_feita": "notificacao previa ao beneficiario feita",
                "dentro_janela_purga": "dentro da janela de purga",
                "ja_em_rescisao_cancel": "contrato ja em rescisao/cancelamento",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "gustavo._build_dossier/ans_submit",
            "constroi": lambda llm: _com_deps(GustavoGraph, llm),
            "monta": dossie_com_rota,
            "base": {"fluxo": "ans_submit"},
            "booleanos": {
                "dataset_complete": "dataset completo",
                "schema_valid": "schema do envio valido",
                "lgpd_anonimizado": "dataset anonimizado (LGPD)",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "gustavo._build_dossier/nip",
            "constroi": lambda llm: _com_deps(GustavoGraph, llm),
            "monta": dossie_com_rota,
            "base": {"fluxo": "nip"},
            "booleanos": {
                "contesta_negativa": "a NIP contesta uma negativa anterior",
                "documentacao_suficiente": "documentacao suficiente para responder",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "lucas._build_message",
            "constroi": lambda llm: _com_deps(LucasGraph, llm, whatsapp=None),
            "monta": mensagem,
            "base": {},
            "booleanos": {"status_conciliado": "pagamento conciliado no CNAB"},
            "ausente": "SEM DADO",
        },
        {
            "sitio": "lucas._build_dossier",
            "constroi": lambda llm: _com_deps(LucasGraph, llm, whatsapp=None),
            "monta": dossie,
            "base": {},
            "booleanos": {
                "status_conciliado": "pagamento conciliado no CNAB",
                "contesta_cobranca": "beneficiario contesta a cobranca",
                "pedido_cancelamento": "ha pedido de cancelamento",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "marina._build_dossier/contas",
            "constroi": lambda llm: _com_deps(MarinaGraph, llm),
            "monta": dossie_com_rota,
            "base": {"flow": "contas"},
            "booleanos": {
                "item_conforme_tabela": "item conforme a tabela",
                "divergencia_valor": "divergencia de valor",
                "documentacao_anexa": "documentacao anexa",
                "indicio_fraude_sinalizado": "indicio de fraude sinalizado",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "marina._build_dossier/recurso",
            "constroi": lambda llm: _com_deps(MarinaGraph, llm),
            "monta": dossie_com_rota,
            "base": {"flow": "recurso"},
            "booleanos": {
                "glosa_existe": "a glosa recorrida existe",
                "dentro_prazo_recurso": "recurso dentro do prazo",
                "documentacao_recurso_completa": "documentacao do recurso completa",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "marina._build_dossier/reembolso",
            "constroi": lambda llm: _com_deps(MarinaGraph, llm),
            "monta": dossie_com_rota,
            "base": {"flow": "reembolso"},
            "booleanos": {
                "cobertura_prevista": "cobertura prevista no contrato",
                "dentro_prazo": "pedido dentro do prazo",
                "dentro_tabela": "valor dentro da tabela",
                "dentro_teto_l2": "valor dentro do teto de alcada L2",
            },
            "ausente": "SEM DADO",
        },
        {
            "sitio": "valentina._build_dossier",
            "constroi": lambda llm: _com_deps(ValentinaGraph, llm),
            "monta": dossie_com_rota,
            "base": {"task": "stratify"},
            "booleanos": {
                "elegibilidade_criterios_atendidos": "criterios de elegibilidade do programa atendidos",
                "criterio_alta_aparente": "criterio de alta aparente",
            },
            "ausente": "SEM DADO",
        },
    ]


def _com_valor(base: Mapping[str, Any], chaves: Mapping[str, str], valor: Any) -> dict[str, Any]:
    """`base` mais cada chave booleana com `valor`. Chave pontilhada = fato aninhado do andre."""
    state = dict(base)
    for chave in chaves:
        state[chave.split(".")[-1]] = valor
    return state


async def _prompt_do_sitio(sitio: _Sitio, state: dict[str, Any]) -> str:
    llm = _CapturaPrompt()
    grafo = sitio["constroi"](llm)
    await sitio["monta"](grafo, state)
    assert len(llm.prompts) == 1, f"{sitio['sitio']}: esperado 1 prompt, veio {len(llm.prompts)}"
    return llm.prompts[0]


def _linha(prompt: str, rotulo: str) -> str:
    linhas = [linha for linha in prompt.splitlines() if linha.strip().endswith(rotulo)]
    assert len(linhas) == 1, f"rotulo {rotulo!r} apareceu {len(linhas)}x no prompt"
    return linhas[0].strip()


@pytest.mark.parametrize("sitio", _sitios(), ids=lambda s: s["sitio"])
async def test_fato_apurado_falso_chega_ao_modelo_como_nao(sitio: _Sitio) -> None:
    """O estado que o incidente de 24/08 apagou: apurado E' desfavoravel.

    Alem do marcador, a chave booleana NAO pode sobrar no repr do contexto — se sobrasse, o
    modelo continuaria vendo `'rede_credenciada': False` logo abaixo da linha que diz NAO, e o
    repr e' justamente a forma que colapsa os tres estados.
    """
    prompt = await _prompt_do_sitio(sitio, _com_valor(sitio["base"], sitio["booleanos"], False))

    for chave, rotulo in sitio["booleanos"].items():
        assert _linha(prompt, rotulo).startswith("NAO"), (
            f"{sitio['sitio']}: {chave!r} apurado como False nao chegou marcado como NAO"
        )
        folha = chave.split(".")[-1]
        assert f"'{folha}':" not in prompt, (
            f"{sitio['sitio']}: {folha!r} sobrou no repr do contexto — o repr e' o veiculo do defeito"
        )


@pytest.mark.parametrize("sitio", _sitios(), ids=lambda s: s["sitio"])
async def test_fato_nao_apurado_chega_ao_modelo_como_sem_dado(sitio: _Sitio) -> None:
    """`None` nao e' `False`. A ausencia de apuracao tem forma propria, e a linha existe.

    Se o rotulo sumisse quando o fato esta ausente, o silencio seria lido como ausencia de
    problema — que e' a leitura errada e a que o dossie de 19/08 produziu.
    """
    prompt = await _prompt_do_sitio(sitio, dict(sitio["base"]))
    esperado = sitio["ausente"]

    for chave, rotulo in sitio["booleanos"].items():
        assert _linha(prompt, rotulo).startswith(esperado), (
            f"{sitio['sitio']}: {chave!r} ausente nao chegou marcado como {esperado}"
        )


@pytest.mark.parametrize("sitio", _sitios(), ids=lambda s: s["sitio"])
async def test_os_dois_estados_nao_se_parecem(sitio: _Sitio) -> None:
    """A asserção que resume o achado: as duas renderizacoes tem de DIFERIR.

    Um renderizador que devolvesse o mesmo texto para apurado-falso e para nao-apurado passaria
    nos dois testes acima se ambos os marcadores estivessem na linha. Este fecha essa porta.
    """
    if sitio["ausente"] == "NAO":
        pytest.skip("sitio coage ausencia para False na montagem dos fatos (registrado no caso)")

    falso = await _prompt_do_sitio(sitio, _com_valor(sitio["base"], sitio["booleanos"], False))
    ausente = await _prompt_do_sitio(sitio, dict(sitio["base"]))

    for rotulo in sitio["booleanos"].values():
        assert _linha(falso, rotulo) != _linha(ausente, rotulo)
