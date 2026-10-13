"""O modelo da persona e o juiz: prompt sem dado real, saida JSON validada, falha que nao aprova."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.qa.simulador import beneficiario_llm as bl
from maezo.qa.simulador import juiz as jz
from maezo.qa.simulador.mascaramento import Mascarador
from maezo.qa.simulador.personas import MARCADOR_CPF, por_id

from .apoio_simulador import CPF_SINTETICO, conversa, escalonamento, fatos_de_julho, sujeito, turno


def test_prompt_da_persona_nao_tem_dado_real_e_ensina_os_marcadores() -> None:
    historico = [("atendimento", Mascarador(sujeito()).texto(f"Seu CPF {CPF_SINTETICO}?"))]
    prompt = bl.montar_prompt(por_id("idoso"), historico, turno=1)
    assert CPF_SINTETICO not in prompt
    assert "{{CPF}}" in prompt and "{{NASCIMENTO}}" in prompt
    assert "Ainda NAO encerre." in prompt


def test_prompt_no_ultimo_turno_manda_encerrar() -> None:
    persona = por_id("apressado")
    assert "ultimo turno" in bl.montar_prompt(persona, [], turno=persona.turnos_max)


def test_interpretar_fala_valida_com_texto_em_volta() -> None:
    fala = bl.interpretar('ok:\n{"mensagem": "qual o valor?", "tema": "COBRANCA", "encerrar": false} fim')
    assert fala == bl.FalaDaPersona(mensagem="qual o valor?", tema="cobranca", encerrar=False)


def test_interpretar_troca_cpf_inventado_pelo_marcador_e_tema_desconhecido() -> None:
    fala = bl.interpretar('{"mensagem": "meu cpf e 123.456.789-09", "tema": "xyz", "encerrar": true}')
    assert fala.mensagem == f"meu cpf e {MARCADOR_CPF}"
    assert fala.tema == "outro" and fala.encerrar


@pytest.mark.parametrize("resposta", ["", "sem json", "{quebrado", '{"mensagem": ""}', "[1, 2]"])
def test_interpretar_recusa_resposta_fora_do_formato(resposta: str) -> None:
    with pytest.raises(bl.RespostaDaPersonaInvalidaError):
        bl.interpretar(resposta)


async def test_beneficiario_tenta_de_novo_quando_o_json_vem_quebrado() -> None:
    respostas = ["lixo", '{"mensagem": "oi", "tema": "outro", "encerrar": false}']

    async def gerar(prompt: str) -> str:
        return respostas.pop(0)

    fala = await bl.BeneficiarioLLM(gerar).proxima(por_id("idoso"), [], 1)
    assert fala.mensagem == "oi"


async def test_beneficiario_desiste_depois_das_tentativas() -> None:
    async def gerar(prompt: str) -> str:
        return "lixo"

    with pytest.raises(bl.RespostaDaPersonaInvalidaError):
        await bl.BeneficiarioLLM(gerar, tentativas=2).proxima(por_id("idoso"), [], 1)


async def test_gerador_da_inferencia_sempre_pede_a_zona_phi() -> None:
    chamadas: list[dict[str, Any]] = []

    class Inferencia:
        async def generate(self, prompt: str, **kwargs: Any) -> str:
            chamadas.append(kwargs)
            return "x"

    gerar = bl.gerador_da_inferencia(Inferencia(), tenant_id="amh", papel="simulador-persona")
    assert await gerar("p") == "x"
    assert chamadas == [{"phi": True, "agent_id": "simulador-persona", "tenant_id": "amh"}]


# --- juiz -----------------------------------------------------------------------------------------
def _transcricao() -> list[dict[str, Any]]:
    c = conversa(
        turno(
            1,
            texto=f"cpf {CPF_SINTETICO}",
            tema="cobranca",
            lucas=["A de julho de 2026 está paga (boleto final 0037)."],
            fatos=[fatos_de_julho()],
            escalonamentos=[escalonamento()],
        )
    )
    return jz.transcricao_mascarada(c, Mascarador(c.sujeito))


def test_transcricao_e_prompt_do_juiz_sao_mascarados() -> None:
    transcricao = _transcricao()
    prompt = jz.montar_prompt(por_id("apressado"), transcricao)
    assert CPF_SINTETICO not in prompt and "0037" not in prompt
    assert "[CPF]" in prompt and "teria" not in prompt
    assert "caso humano aberto por lucas" in prompt
    assert "ESCALACAO ESPERADA PARA HUMANO: nunca" in prompt


def test_interpretar_avaliacao_limita_notas_e_falha() -> None:
    a = jz.interpretar(
        '{"respondeu": 9, "esclarecimento": 0, "tom": 4, "seguranca_clinica": 5, "escalacao_adequada": 3,'
        ' "loop": true, "contradicao": false, "falha_principal": "inventada", "turno": 2, "motivo": "x"}'
    )
    assert (a.respondeu, a.esclarecimento) == (5, 1)
    assert a.falha_principal == "outra" and a.loop and a.turno == 2
    assert a.nota_minima() == 1


def test_interpretar_avaliacao_sem_nota_recusa() -> None:
    with pytest.raises(ValueError):
        jz.interpretar('{"respondeu": "cinco"}')


async def test_juiz_indisponivel_nunca_aprova() -> None:
    async def gerar(prompt: str) -> str:
        raise RuntimeError("bedrock fora")

    a = await jz.Juiz(gerar).avaliar(por_id("apressado"), _transcricao())
    assert a.erro == "RuntimeError" and a.nota_minima() == 0
