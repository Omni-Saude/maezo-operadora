"""Protocolos da Helena — travas que continuam valendo (simulador de 26/10/2026, imagem 1ca6ed73):

1. bebe com menos de 3 meses: sinal de sofrimento no recem-nascido escala (rodada 10, regra A) e o "sem sinal"
   NUNCA manda marcar consulta (rodada 12: a oferta explicita da equipe de saude);
2. roteador fora (prazo/provedor): a resposta as perguntas do protocolo continua do protocolo, e a pergunta
   GENERICA da v4 (peito/AVC) nunca volta;
3. crianca que bateu a cabeca sem a idade dita: a idade vem JUNTO das perguntas de alerta de qualquer idade.

Grafo COMPILADO com respostas falsas do modelo; 3 frases ou mais por comportamento, com controle negativo."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.protocolo import nucleo as n
from maezo.agents.helena.helena_v4 import GRUPO_GERAL, texto_da_pergunta_de_alerta
from maezo.agents.helena.triagem_v2 import RESPOSTA_TRIAGEM_BEBE_PEQUENO
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_protocolos import (
    _CATALOGO,
    _TEXTOS,
    _URINAR,
    _escalou,
    _escolha,
    _estado,
    _grafo,
    _leitura,
    _Modelo,
    _starts,
)

_MANCHAS_RN = _CATALOGO.protocolos["tri-crianca-0-2m-manchas"]
_FEBRE_5_12 = _CATALOGO.protocolos["tri-crianca-5-12a-febre"]
_UNI_CRIANCA = _CATALOGO.protocolos["uni-alerta-universal-crianca"]
_CABECA_2M_5A = _CATALOGO.protocolos["tri-crianca-batida-cabeca"]
_GERAL = texto_da_pergunta_de_alerta(GRUPO_GERAL, "x")


class _RoteadorFora(_Modelo):
    """O roteador falha (prazo do provedor); as outras chamadas seguem as filas."""

    async def generate(self, prompt: str, **kw: Any) -> str:
        if "roteador de um assistente" in prompt:
            raise TimeoutError("prazo")
        return await super().generate(prompt, **kw)


# --- 1. Bebe com menos de 3 meses ----------------------------------------------------------------------
@pytest.mark.parametrize("resposta", ["não", "nenhuma dessas", "não, só as manchinhas mesmo"])
async def test_recem_nascido_sem_sinal_recebe_a_oferta_da_equipe_e_nunca_marcar_consulta(
    resposta: str,
) -> None:
    k = len(_MANCHAS_RN.perguntas_de_alerta)
    modelo = _Modelo([decisao_v7("sintoma", responde=True)], leitura=[_leitura(*("nao",) * k)])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_MANCHAS_RN)], pendente=n.codigo_do_alerta(_MANCHAS_RN.id))
    )
    texto = whatsapp.enviados[-1]
    assert texto == RESPOSTA_TRIAGEM_BEBE_PEQUENO
    assert "marcar" not in texto.lower() and final["coleta_pendente"] == PENDENTE_OFERTA_EQUIPE
    assert not _escalou(final) and not _starts(cib)


@pytest.mark.parametrize(
    "resposta", ["não, mas ela tem só 1 mês", "nada disso, é um recém-nascido", "não, ele nasceu faz 10 dias"]
)
async def test_bebe_pequeno_dito_em_protocolo_de_outra_faixa_tambem_recebe_a_oferta(resposta: str) -> None:
    k = len(_UNI_CRIANCA.perguntas_de_alerta)
    modelo = _Modelo([decisao_v7("sintoma", responde=True)], leitura=[_leitura(*("nao",) * k)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_UNI_CRIANCA)], pendente=n.codigo_do_alerta(_UNI_CRIANCA.id))
    )
    assert whatsapp.enviados[-1] == RESPOSTA_TRIAGEM_BEBE_PEQUENO


@pytest.mark.parametrize("resposta", ["não", "nenhuma", "não, ele tem 8 anos"])
async def test_controle_crianca_maior_continua_com_a_orientacao_do_protocolo(resposta: str) -> None:
    k = len(_FEBRE_5_12.perguntas_de_alerta)
    modelo = _Modelo([decisao_v7("sintoma", responde=True)], leitura=[_leitura(*("nao",) * k)])
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_FEBRE_5_12)], pendente=n.codigo_do_alerta(_FEBRE_5_12.id))
    )
    assert _TEXTOS is not None
    assert whatsapp.enviados[-1] == n.texto_sem_sinal(_FEBRE_5_12, _TEXTOS)


@pytest.mark.parametrize(
    "fala",
    [
        "meu recém-nascido está com febre",
        "minha bebê nasceu há 5 dias e está gemendo",
        "o recém nascido não quer mamar desde cedo",
    ],
)
async def test_recem_nascido_com_sinal_de_sofrimento_escala_antes_do_protocolo(fala: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(_MANCHAS_RN.id, "crianca", "0_2m")])
    grafo, _, _ = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    assert final["escalation_motivo"] == "red_flag_clinico" and final["escalation_severidade"] == "grave"
    assert modelo.prompts["escolha"] == []


@pytest.mark.parametrize(
    "fala",
    [
        "meu filho recém-nascido está com manchinhas faz uns dois dias",
        "recém-nascido com umas bolinhas vermelhas no rosto",
        "meu bebê nasceu semana passada e apareceu uma mancha",
    ],
)
async def test_controle_recem_nascido_sem_sofrimento_recebe_as_perguntas(fala: str) -> None:
    modelo = _Modelo([decisao_v7("sintoma")], escolha=[_escolha(_MANCHAS_RN.id, "crianca", "0_2m")])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_MANCHAS_RN)
    assert not _escalou(final) and not _starts(cib)


# --- 2. Roteador fora ----------------------------------------------------------------------------------
@pytest.mark.parametrize("resposta", ["Não", "não tenho nada disso", "nenhuma"])
@pytest.mark.parametrize("falha", ["vazio", "prazo"])
async def test_roteador_fora_a_resposta_continua_do_protocolo(resposta: str, falha: str) -> None:
    filas = {"leitura": [_leitura("nao", "nao", "nao", "nao")]}
    modelo = _RoteadorFora([], **filas) if falha == "prazo" else _Modelo(["", ""], **filas)
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    texto = whatsapp.enviados[-1]
    assert _TEXTOS is not None
    assert texto == n.texto_sem_sinal(_URINAR, _TEXTOS) and texto != _GERAL
    assert not str(final.get("coleta_pendente") or "").startswith("V4_")
    assert not _escalou(final) and not _starts(cib)


@pytest.mark.parametrize("resposta", ["Não", "acho que não", "hmm"])
async def test_roteador_e_leitura_fora_repergunta_o_protocolo_e_depois_a_equipe(resposta: str) -> None:
    modelo = _RoteadorFora([])
    grafo, whatsapp, cib = _grafo(modelo)
    final = await grafo.ainvoke(
        _estado(resposta, [n.texto_das_perguntas(_URINAR)], pendente=n.codigo_do_alerta(_URINAR.id))
    )
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_URINAR, (1, 2, 3, 4))
    assert final["coleta_pendente"] == n.codigo_do_alerta(_URINAR.id, 1, (1, 2, 3, 4))
    grafo2, whatsapp2, cib2 = _grafo(_RoteadorFora([]))
    final2 = await grafo2.ainvoke(
        _estado(resposta, [whatsapp.enviados[-1]], pendente=final["coleta_pendente"])
    )
    assert final2["coleta_pendente"] == PENDENTE_OFERTA_EQUIPE and "192" not in whatsapp2.enviados[-1]
    assert not _starts(cib) and not _starts(cib2)


@pytest.mark.parametrize(
    "fala",
    [
        "agora estou com uma tontura estranha",
        "e também uma dor de barriga",
        "estou me sentindo esquisito hoje",
    ],
)
async def test_roteador_fora_com_protocolo_na_conversa_nunca_faz_a_pergunta_generica(fala: str) -> None:
    assert _TEXTOS is not None
    historico = [n.texto_das_perguntas(_URINAR), n.texto_sem_sinal(_URINAR, _TEXTOS)]
    grafo, whatsapp, _ = _grafo(_RoteadorFora([]))
    final = await grafo.ainvoke(_estado(fala, historico))
    assert whatsapp.enviados[-1] != _GERAL
    assert not str(final.get("coleta_pendente") or "").startswith("V4_ALERTA:GERAL")


# --- 3. Crianca que bateu a cabeca sem a idade ------------------------------------------------------------
@pytest.mark.parametrize(
    "fala",
    [
        "Minha filha caiu da cama e bateu a cabeça",
        "meu filho bateu a cabeça na mesa",
        "a criança caiu do sofá e bateu a testa",
    ],
)
async def test_idade_vem_junto_das_perguntas_de_alerta_de_qualquer_idade(fala: str) -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma", tipo="sintoma_de_terceiro")],
        escolha=[_escolha(_CABECA_2M_5A.id, "crianca", "desconhecida")],
    )
    grafo, whatsapp, _ = _grafo(modelo)
    final = await grafo.ainvoke(_estado(fala))
    texto = whatsapp.enviados[-1]
    assert texto == f"{n.texto_das_perguntas(_UNI_CRIANCA)}\n{n.TEXTO_IDADE_JUNTO}"
    assert "convulsão" in texto and texto != n.TEXTO_IDADE
    assert final["coleta_pendente"] == n.codigo_do_alerta(_UNI_CRIANCA.id)


@pytest.mark.parametrize("faixa", ["2m_5a"])
@pytest.mark.parametrize(
    "fala",
    ["meu filho de 3 anos caiu e bateu a cabeça", "minha filha de 2 anos bateu a cabeça", "o de 4 anos caiu"],
)
async def test_controle_com_a_idade_dita_vai_o_protocolo_da_faixa(fala: str, faixa: str) -> None:
    modelo = _Modelo(
        [decisao_v7("sintoma", tipo="sintoma_de_terceiro")],
        escolha=[_escolha(_CABECA_2M_5A.id, "crianca", faixa)],
    )
    grafo, whatsapp, _ = _grafo(modelo)
    await grafo.ainvoke(_estado(fala))
    assert whatsapp.enviados[-1] == n.texto_das_perguntas(_CABECA_2M_5A)
