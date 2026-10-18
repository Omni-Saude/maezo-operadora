"""Fases 2 e 3 de 18/10/2026 (decisoes do dono): transbordo falso e qualidade da resposta de saude.

Contra o GRAFO REAL (tabelas `.dmn` reais, base de FIXTURE) com transportes FALSOS, como
`test_helena_saude_gerativa.py` (o arnes e' o mesmo):

  FASE 2
  1. Pergunta de saude em 1a pessoa SEM alarme ("tenho SOP, e' a mesma coisa que cisto?") NAO cai no gatilho 2
     (`intencao_clinica`): na allowlist vai a redacao (sem caso), inclusive logo depois das opcoes 1/2 (o caso
     do dono de 17/10, 22:28). Queixa atual segue as perguntas de alerta; alarme escala como sempre; fora da
     allowlist nada muda.
  2. "Nao quero falar com ninguem" sozinho e' decidido ANTES do modelo, para todos: nunca risco psicossocial
     (mesmo que o modelo marcasse). Com termo de risco na mensagem, os portoes de sempre.
  3. Escolha pura ("nao", "2") a uma oferta pendente, na allowlist: sem chamar o classificador.
  FASE 3
  4. Busca por significado: limiar, lexico como desempate, filtro de populacao, indice uma vez, falha = None.
  5. Dicionario de remedios na checagem; oferta so' quando faz sentido; resumo em variaveis fechadas.
  6. Cobertura pelo Rol x segmentacao (D1): texto deterministico com o aviso.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from maezo.agents.helena import cobertura_rol as cr
from maezo.agents.helena.graph import RESPOSTA_CONFIRMA_ENCERRAMENTO, RESPOSTA_RECUSA_DE_EQUIPE
from maezo.conhecimento import base_saude as b
from maezo.conhecimento import busca_semantica as bsem
from maezo.conhecimento import saude_gerativa as sg
from maezo.runtime.cobranca_intencao import e_confirmacao_pura_do_cancelamento
from tests.unit.agents.test_helena_saude_gerativa import (
    _CLINICA,
    _DONO,
    _DOR_NO_PEITO,
    _INFO,
    _OUTRA,
    _RISCO,
    _SINTOMA,
    _classify,
    _Conversa,
    _texto,
    _Transporte,
)

_RESPOSTA_SOP = (
    "A síndrome dos ovários policísticos é uma alteração hormonal; o cisto é uma bolsa de líquido no ovário. "
    "Não são a mesma coisa. Quem costuma acompanhar é o ginecologista."
)
_BOA_SINTOMA = (
    "Sinto muito pela dor. Quem costuma avaliar isso é o clínico geral. Se aparecer dor súbita e muito forte "
    "ou fraqueza de um lado do corpo, ligue 192 ou vá ao pronto-socorro."
)


# =================================================================================================
# FASE 2.1 — pergunta pessoal sem alarme
# =================================================================================================


async def test_pergunta_pessoal_depois_das_opcoes_vai_a_redacao_sem_caso() -> None:
    conversa = _Conversa(_Transporte(_BOA_SINTOMA, _RESPOSTA_SOP))
    await conversa.diz("Estou com dor de cabeça", _SINTOMA)
    nao = await conversa.diz("Não")
    assert str(nao.get("coleta_pendente") or "").startswith("V4_ESCOLHA:"), "a oferta 1/2 ficou pendente"
    assert conversa.modelo.respostas == [_INFO], "o 'Nao' foi decidido sem o classificador"
    conversa.modelo.respostas.clear()
    sop = await conversa.diz("tenho SOP, é a mesma coisa que cisto?", _CLINICA)
    assert conversa.motivos == [], "nenhum caso (antes: intencao_clinica P2 na enfermagem)"
    assert _texto(sop).startswith(_RESPOSTA_SOP)
    ultimo = conversa.transporte.chamadas[-1][1][0]["content"][0]["text"]
    assert "Pergunta geral de saúde." in ultimo
    assert "oferecendo mostrar como marcar" in ultimo, "duvida sobre a propria condicao: a oferta faz sentido"


async def test_diagnostico_da_pessoa_sem_intencao_clinica_tambem_vai_a_redacao() -> None:
    conversa = _Conversa(_Transporte(_RESPOSTA_SOP))
    resultado = await conversa.diz(
        "fui diagnosticada com mioma, é perigoso?", _classify(intent="symptom", population="adult")
    )
    assert conversa.motivos == []
    assert _texto(resultado).startswith(_RESPOSTA_SOP)
    assert "responda sim ou não" not in _texto(resultado).lower(), "nao e' queixa: sem perguntas de alerta"


async def test_queixa_atual_segue_as_perguntas_de_alerta() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Estou com dor de cabeça, é grave?", _SINTOMA)
    assert "responda sim ou não" in _texto(resultado).lower()
    assert conversa.transporte.chamadas == []
    assert conversa.motivos == []


async def test_alarme_na_pergunta_pessoal_escala_como_sempre() -> None:
    conversa = _Conversa()
    await conversa.diz("tenho SOP e estou com dor no peito e falta de ar, é grave?", _DOR_NO_PEITO)
    assert conversa.motivos == ["red_flag_clinico"]
    assert conversa.transporte.chamadas == []


async def test_fora_da_allowlist_a_pergunta_pessoal_e_a_de_antes() -> None:
    conversa = _Conversa(conversa=_OUTRA, allowlist=(_DONO,))
    await conversa.diz("tenho SOP, é a mesma coisa que cisto?", _CLINICA)
    assert conversa.transporte.chamadas == []


# =================================================================================================
# FASE 2.2 — recusa pura, antes do modelo, para todos
# =================================================================================================


@pytest.mark.parametrize("conversa_id", [_DONO, _OUTRA])
@pytest.mark.parametrize(
    "mensagem",
    ["Não quero falar com ninguém", "não preciso de atendente", "Não quero falar com ninguém, obrigado"],
)
async def test_recusa_pura_nunca_vira_risco_psicossocial(conversa_id: str, mensagem: str) -> None:
    conversa = _Conversa(conversa=conversa_id)
    resultado = await conversa.diz(mensagem, _RISCO)
    assert conversa.motivos == [], "nenhum caso, nunca risco psicossocial"
    assert _texto(resultado) == RESPOSTA_RECUSA_DE_EQUIPE
    assert "188" not in _texto(resultado)
    assert conversa.modelo.respostas == [_RISCO], "o classificador nao foi chamado"


async def test_recusa_com_termo_de_risco_segue_os_portoes_de_sempre() -> None:
    conversa = _Conversa()
    await conversa.diz("não quero mais viver, não quero falar com ninguém", _RISCO)
    assert conversa.motivos == ["risco_psicossocial"]


async def test_recusa_com_caso_aberto_pede_a_confirmacao_de_encerramento() -> None:
    conversa = _Conversa()
    await conversa.diz("Quero falar com uma pessoa", _classify(intent="human_request"))
    assert conversa.motivos == ["solicitacao_humano"]
    resultado = await conversa.diz("Não quero falar com ninguém", _RISCO)
    assert _texto(resultado) == RESPOSTA_CONFIRMA_ENCERRAMENTO
    assert conversa.modelo.respostas == [_RISCO]


# =================================================================================================
# FASE 2.3 — escolha pura sem o classificador (allowlist)
# =================================================================================================


async def test_nao_e_dois_a_oferta_pendente_sem_o_classificador() -> None:
    conversa = _Conversa(_Transporte(_BOA_SINTOMA))
    await conversa.diz("Estou com dor de cabeça", _SINTOMA)
    nao = await conversa.diz("não", _RISCO)
    assert conversa.modelo.respostas == [_RISCO], "o 'nao' as perguntas de alerta nao chamou o classificador"
    assert _texto(nao).endswith(sg.opcoes_do_sintoma(False))
    dois = await conversa.diz("2", _RISCO)
    assert conversa.motivos == ["intencao_clinica"]
    assert dois.get("escalation_motivo") == "intencao_clinica"


async def test_sim_as_perguntas_de_alerta_escala_sem_o_classificador() -> None:
    conversa = _Conversa()
    await conversa.diz("Estou com dor de cabeça", _SINTOMA)
    await conversa.diz("sim", _INFO)
    assert conversa.motivos == ["red_flag_clinico"]
    assert conversa.modelo.respostas == [_INFO]


async def test_fora_da_allowlist_a_escolha_ainda_passa_pelo_classificador() -> None:
    conversa = _Conversa(conversa=_OUTRA, allowlist=(_DONO,))
    await conversa.diz("Estou com dor de cabeça", _SINTOMA)
    await conversa.diz("não", _INFO)
    assert conversa.modelo.respostas == [], "fora da allowlist o modelo classifica, como antes"


def test_resposta_pura() -> None:
    assert sg.resposta_pura("nao") == "nao" and sg.resposta_pura("nao tenho") == "nao"
    assert sg.resposta_pura("sim") == "sim" and sg.resposta_pura("tenho sim") == "sim"
    assert sg.resposta_pura("nao sei") is None and sg.resposta_pura("nao mas doi muito") is None


def test_confirmacao_pura_do_cancelamento() -> None:
    for t in ("Sim", "sim!", "Sim, quero cancelar", "confirmo", "SIM, QUERO CANCELAR"):
        assert e_confirmacao_pura_do_cancelamento(t), t
    for t in ("sim, mas antes quero saber da multa", "não", "quero cancelar o boleto de outubro?"):
        assert not e_confirmacao_pura_do_cancelamento(t), t


# =================================================================================================
# FASE 3.1 — busca por significado
# =================================================================================================


def _assunto(
    id_: str, categoria: str, titulo: str, sinonimos: tuple[str, ...], populacao: str | None = None
) -> b.Assunto:
    extras: dict[str, Any] = {}
    if categoria == "sintoma":
        extras = {
            "perguntas_alerta": ("Tem febre muito alta?",),
            "sinais_alarme": ("febre muito alta",),
            "populacao": populacao or "todos",
        }
    return b.Assunto.model_validate(
        {
            "id": id_,
            "categoria": categoria,
            "titulo": titulo,
            "sinonimos": sinonimos,
            "texto": f"Texto aprovado sobre {titulo.lower()} para o teste da busca.",
            "tipo_texto": "proprio",
            "fontes": [
                {
                    "nome": "Saúde de A a Z (Ministério da Saúde)",
                    "url": "https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z",
                    "licenca": "CC BY-ND 3.0",
                    "coletado_em": "2026-10-17",
                }
            ],
            "revisao": {
                "status": "rascunho_dono_aprovado_dev",
                "autor": "fixture de teste",
                "aprovado_por": "dono (17/10/2026, dev)",
                "revisao_medica": "pendente",
                "revisar_ate": "2027-04-17",
            },
            **extras,
        }
    )


def _base(*assuntos: b.Assunto) -> b.BaseSaude:
    por_sinonimo = {b.normalizar(t): a.id for a in assuntos for t in (*a.sinonimos, a.titulo)}
    por_categoria: dict[str, tuple[str, ...]] = {}
    for a in assuntos:
        por_categoria[a.categoria] = (*por_categoria.get(a.categoria, ()), a.id)
    return b.BaseSaude({a.id: a for a in assuntos}, por_sinonimo, por_categoria)


_EIXOS = ("urin", "pele", "febre", "bebe")


@dataclass
class _Embeddings:
    modelo: str = "fake-embeddings"
    chamadas: list[tuple[int, bool]] = field(default_factory=list)
    falha: bool = False

    async def vetores(self, textos: Any, *, consulta: bool) -> list[list[float]]:
        self.chamadas.append((len(textos), consulta))
        if self.falha:
            raise bsem.FalhaDeEmbeddingsError("servico:teste")
        saida = []
        for t in textos:
            n = b.normalizar(t)
            v = [
                float(("urin" in n) or ("cistite" in n)),
                float(("pele" in n) or ("mancha" in n)),
                float("febre" in n),
                float(("bebe" in n) or ("crianca" in n)),
            ]
            saida.append([*v, 0.05])
        return saida


_BASE_BUSCA = _base(
    _assunto("sin-dor-ao-urinar", "sintoma", "Dor ao urinar", ("ardencia para urinar", "infeccao urinaria")),
    _assunto("sin-manchas-na-pele", "sintoma", "Manchas na pele", ("manchas", "pintas")),
    _assunto("sin-febre-adulto", "sintoma", "Febre em adulto", ("febre",), populacao="adulto"),
    _assunto("sin-febre-crianca", "sintoma", "Febre em criança", ("febre no bebe",), populacao="crianca"),
)


async def test_cistite_casa_infeccao_urinaria_nao_manchas_na_pele() -> None:
    busca = bsem.BuscaSemantica(transporte=_Embeddings(), bloquear_no_indice=True, limiar=0.6)
    achado = await busca.buscar(_BASE_BUSCA, "infecção urinária (cistite) tem cura?")
    assert achado is not None and achado.assunto_id == "sin-dor-ao-urinar"


async def test_bebe_com_febre_casa_febre_infantil_com_o_filtro_de_populacao() -> None:
    busca = bsem.BuscaSemantica(transporte=_Embeddings(), bloquear_no_indice=True, limiar=0.6)
    achado = await busca.buscar(
        _BASE_BUSCA, "meu bebê está com febre", filtro=lambda a: a.populacao in (None, "todos", "crianca")
    )
    assert achado is not None and achado.assunto_id == "sin-febre-crianca"


async def test_abaixo_do_limiar_nao_ha_apoio_e_o_indice_e_montado_uma_vez() -> None:
    emb = _Embeddings()
    busca = bsem.BuscaSemantica(transporte=emb, bloquear_no_indice=True, limiar=0.9)
    assert await busca.buscar(_BASE_BUSCA, "qual o horário da farmácia?") is None
    assert await busca.buscar(_BASE_BUSCA, "outra pergunta qualquer") is None
    assert [c for c in emb.chamadas if not c[1]] == [(4, False)], "o indice dos assuntos so' uma vez"


async def test_falha_do_modelo_de_embeddings_volta_none() -> None:
    busca = bsem.BuscaSemantica(transporte=_Embeddings(falha=True), bloquear_no_indice=True)
    assert await busca.buscar(_BASE_BUSCA, "febre") is None


async def test_o_lexico_desempata_quando_o_significado_fica_proximo() -> None:
    busca = bsem.BuscaSemantica(
        transporte=_Embeddings(), bloquear_no_indice=True, limiar=0.1, margem_do_lexico=0.5
    )
    achado = await busca.buscar(_BASE_BUSCA, "manchas e febre")
    assert achado is not None and achado.como in ("significado", "lexico_desempate")


def test_so_modelos_regionais_sao_admitidos() -> None:
    with pytest.raises(ValueError):
        bsem.TransporteBedrockRegional(modelo="global.cohere.embed-v4:0", client=object())
    assert (
        bsem.TransporteBedrockRegional(modelo=bsem.MODELO_TITAN_V2, client=object()).modelo
        == bsem.MODELO_TITAN_V2
    )


# =================================================================================================
# FASE 3.2-3.4 — checagem com nomes de remedios, oferta, resumo
# =================================================================================================


def _regras(texto: str, pessoa: str = "") -> list[str]:
    pedido = sg.Pedido(modo=sg.MODO_INFORMAR, mensagem="x")
    return [v.regra for v in sg.verificar(texto, pedido, cercas=lambda t: [], texto_da_pessoa=pessoa)]


def test_o_dicionario_de_remedios_pega_mandar_usar() -> None:
    assert "manda_usar_remedio" in _regras("Use o paracetamol com cuidado.")
    assert "manda_usar_remedio" in _regras("Você pode usar dipirona para a dor.")
    assert _regras("A dipirona é usada para dor e febre. Quem indica é o médico.") == []
    assert _regras("Não use dipirona sem orientação médica.") == []


def test_remedio_esquecido_e_combinacao_pedem_o_medico_ou_o_farmaceutico() -> None:
    assert "remedio_esquecido_sem_orientacao" in _regras("Depende do caso.", "esqueci de tomar a losartana")
    assert (
        _regras("Fale com o seu médico ou farmacêutico e veja a bula.", "esqueci de tomar a losartana") == []
    )
    assert "combinacao_sem_orientacao" in _regras("Depende.", "posso tomar dipirona com cerveja?")
    assert _regras("Converse com o farmacêutico.", "posso tomar dipirona com ibuprofeno?") == []
    assert _regras("Sinto muito pela dor.", "ja usei dorflex e buscopan e nao melhorou") == []


def test_a_oferta_so_quando_faz_sentido_e_varia() -> None:
    conceitual = sg.mensagem_do_usuario(sg.Pedido(modo=sg.MODO_INFORMAR, mensagem="o que e psa"))
    assert "NÃO termine oferecendo consulta" in conceitual
    resultado = sg.mensagem_do_usuario(sg.Pedido(modo=sg.MODO_RESULTADO, mensagem="meu psa deu 5"))
    assert "oferecendo mostrar como marcar" in resultado
    formas = {
        sg.mensagem_do_usuario(
            sg.Pedido(modo=sg.MODO_INFORMAR, mensagem="x", oferecer=True, forma_da_oferta=i)
        )
        for i in range(3)
    }
    assert len(formas) == 3
    assert sg.pede_tratamento(b.normalizar("Como tratar a enxaqueca?"))


def test_o_resumo_em_variaveis_fechadas() -> None:
    texto = sg.mensagem_do_usuario(
        sg.Pedido(
            modo=sg.MODO_SINTOMA_SEM_ALARME,
            mensagem="nao",
            populacao="idoso",
            alarme_negado=True,
            assunto_anterior="Dor de cabeça",
        )
    )
    assert "pessoa idosa" in texto
    assert "NÃO tem os sinais de alarme" in texto
    assert "assunto anterior da conversa: Dor de cabeça" in texto


def test_pergunta_pessoal_de_saude() -> None:
    n = b.normalizar
    assert sg.pergunta_pessoal_de_saude(
        n("fui diagnosticada com mioma, é perigoso?"), "?", assunto_de_saude=False
    )
    assert sg.pergunta_pessoal_de_saude(
        n("tenho SOP, é a mesma coisa que cisto?"), "?", assunto_de_saude=True
    )
    assert not sg.pergunta_pessoal_de_saude(
        n("tenho SOP, é a mesma coisa que cisto?"), "?", assunto_de_saude=False
    )
    assert not sg.pergunta_pessoal_de_saude(n("tenho direito a reembolso?"), "?", assunto_de_saude=True)


# =================================================================================================
# FASE 3.5 — cobertura pelo Rol x segmentacao (decisao D1)
# =================================================================================================


@dataclass(frozen=True)
class _Car:
    cumprida: bool | None
    dias: int | None = 0
    termina_em: str | None = None


@dataclass(frozen=True)
class _Item:
    descricao: str
    situacao: str
    tem_diretriz: bool = False
    rol_edicao: str | None = "ROL DE PROCEDIMENTOS 2021 - RN Nº 465/2021"
    carencias: tuple[_Car, ...] = ()


@dataclass(frozen=True)
class _Cob:
    itens: tuple[_Item, ...]
    segmentacao: str | None = "Ambulatorial + Hospitalar com Obstetrícia"
    truncada: bool = False


def test_vasectomia_consta_no_rol_com_o_aviso() -> None:
    texto = cr.texto_da_cobertura(
        _Cob((_Item("Vasectomia unilateral", "coberto_rol", tem_diretriz=True, carencias=(_Car(True),)),))
    )
    assert "Vasectomia unilateral: consta no Rol da ANS (RN nº 465/2021)" in texto
    assert "Ambulatorial + Hospitalar com Obstetrícia" in texto
    assert "diretriz de utilização" in texto
    assert texto.endswith(cr.AVISO_DA_COBERTURA)
    assert "é só me pedir" not in texto


def test_cobertura_sem_item_generica_e_carencia() -> None:
    assert cr.texto_da_cobertura(_Cob(())) == cr.RESPOSTA_SEM_ITEM
    assert cr.texto_da_cobertura(_Cob((_Item("A", "coberto_rol"),), truncada=True)) == cr.RESPOSTA_GENERICA
    com_carencia = cr.texto_da_cobertura(
        _Cob((_Item("Parto", "coberto_rol", carencias=(_Car(False, 300, "2027-01-10"),)),))
    )
    assert "Há carência em andamento até 10/01/2027." in com_carencia
    nunca = cr.texto_da_cobertura(_Cob((_Item("X", "fora_do_rol"),)))
    assert "não cobre" not in nunca and "não consegui confirmar" in nunca


def test_termo_da_mensagem() -> None:
    assert cr.termo_da_mensagem("O plano cobre vasectomia?") == "vasectomia"
    assert cr.termo_da_mensagem("A laqueadura consta no Rol da ANS?") == "laqueadura"
    assert cr.termo_da_mensagem("Qual o meu plano?") is None


class _ConsultasComCobertura:
    def __init__(self, view: Any) -> None:
        self.view = view
        self.termos: list[str] = []

    async def consultar(self, ref: str, subtipo: str) -> Any:
        return None

    async def consultar_cobertura(self, ref: str, procedimento: str) -> Any:
        self.termos.append(procedimento)
        return self.view


async def test_o_grafo_responde_a_cobertura_so_na_allowlist() -> None:
    from maezo.agents.helena.graph import HelenaGraph

    conversa = _Conversa()
    fonte = _ConsultasComCobertura(_Cob((_Item("Vasectomia unilateral", "coberto_rol", tem_diretriz=True),)))
    grafo = HelenaGraph(
        inference=conversa.modelo,  # type: ignore[arg-type]
        dmn=None,  # type: ignore[arg-type]
        cibseven=conversa.cib,
        audit_sink=None,  # type: ignore[arg-type]
        whatsapp=conversa.whatsapp,
        consultas_plano=fonte,  # type: ignore[arg-type]
        helena_v4_enabled=True,
        helena_base_saude_enabled=True,
        base_saude=conversa.base,
        saude_gerativa=conversa.redator,
    )
    estado: Any = {"conversation_id": _DONO, "message_body": "Meu plano cobre vasectomia?"}
    resposta = await grafo._cobertura_do_procedimento(estado, "amh:psr:v1:x")
    assert resposta is not None and "consta no Rol da ANS" in resposta["response_text"]
    assert fonte.termos == ["vasectomia"]
    assert "ROL DE PROCEDIMENTOS 2021 - RN Nº 465/2021" in resposta["consulta_literais"]
    fora: Any = {"conversation_id": _OUTRA, "message_body": "Meu plano cobre vasectomia?"}
    assert await grafo._cobertura_do_procedimento(fora, "amh:psr:v1:x") is None


async def test_sem_indice_pronto_a_busca_nao_espera_e_monta_em_segundo_plano() -> None:
    import asyncio

    emb = _Embeddings()
    busca = bsem.BuscaSemantica(transporte=emb, limiar=0.6)
    assert await busca.buscar(_BASE_BUSCA, "infecção urinária (cistite)") is None, "o lexico vale neste turno"
    await asyncio.sleep(0)
    assert busca._montando is not None
    await busca._montando
    achado = await busca.buscar(_BASE_BUSCA, "infecção urinária (cistite)")
    assert achado is not None and achado.assunto_id == "sin-dor-ao-urinar"


async def test_queixa_com_pergunta_clinica_segue_as_perguntas_de_alerta_na_allowlist() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Estou com dor nas costas, é grave?", _CLINICA)
    assert conversa.motivos == [], "nunca intencao_clinica (o transbordo falso)"
    assert "responda sim ou não" in _texto(resultado).lower()


async def test_fora_da_allowlist_a_queixa_com_pergunta_clinica_e_a_de_antes() -> None:
    conversa = _Conversa(conversa=_OUTRA, allowlist=(_DONO,))
    await conversa.diz("Estou com dor nas costas, é grave?", _CLINICA)
    assert conversa.motivos == ["intencao_clinica"]
