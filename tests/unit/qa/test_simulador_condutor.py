"""O condutor: plano de telefones, verificacao uma vez por execucao, roteiro de acesso, ritmo."""

from __future__ import annotations

import json
from typing import Any

from maezo.platform.webhooks.whatsapp import acesso as acesso_mod
from maezo.platform.webhooks.whatsapp.dispatch import InboundMessage
from maezo.qa.simulador import condutor as cd
from maezo.qa.simulador.beneficiario_llm import BeneficiarioLLM
from maezo.qa.simulador.composicao import CapturaDeEnvios
from maezo.qa.simulador.personas import cpf_errado, por_id

from .apoio_simulador import CPF_SINTETICO, sujeito


class _Relogio:
    def __init__(self) -> None:
        self.agora = 0.0
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.agora

    async def dormir(self, segundos: float) -> None:
        self.esperas.append(round(segundos, 3))
        self.agora += segundos


class _Despachante:
    """Responde como o acesso + a Helena, pela captura (o mesmo caminho de saida do real)."""

    tenant_id = "amh"

    def __init__(self, *, indisponivel: bool = False, falhar_em: str | None = None) -> None:
        self.captura = CapturaDeEnvios(chave_de_saida=None)
        self.recebidas: list[str] = []
        self._indisponivel = indisponivel
        self._falhar_em = falhar_em

    def _respostas(self, texto: str) -> list[str]:
        if self._indisponivel:
            return [acesso_mod.INDISPONIVEL]
        if texto in (
            "Oi",
            "Bom dia",
            "Oi, quero ver minha mensalidade",
            "nao aceito, nao quero passar meus dados",
        ):
            return [acesso_mod.TEXTO_CONSENTIMENTO]
        if "ACEITO" in texto:
            return [acesso_mod.PEDIDO_CPF]
        if texto == CPF_SINTETICO:
            return [acesso_mod.PEDIDO_NASCIMENTO]
        if texto == cpf_errado(CPF_SINTETICO):
            return [acesso_mod.CPF_INVALIDO]
        if texto == "14/03/1980":
            return [acesso_mod.RESPOSTA_VERIFICADO]
        return ["Resposta da Helena."]

    async def dispatch(self, message: InboundMessage) -> dict[str, Any]:
        self.recebidas.append(message.text)
        if message.text == self._falhar_em:
            raise RuntimeError("turno quebrou")
        for texto in self._respostas(message.text):
            await self.captura.send_message(message.from_number, texto)
        return {"next_kind": "inform", "error": None, "texto_livre": "nunca registrado"}


class _Limpeza:
    def __init__(self) -> None:
        self.reiniciadas: list[str] = []
        self.acessos_apagados: list[str] = []

    async def reiniciar_conversa(self, conversation_id: str) -> None:
        self.reiniciadas.append(conversation_id)

    async def apagar_acesso(self, conversation_id: str) -> int:
        self.acessos_apagados.append(conversation_id)
        return 1


class _Condutor(cd.Condutor):
    def conversation_id(self, numero: str) -> str:
        return f"wa:amh:hk1_{numero}"


def _persona_que_encerra_no(n: int, *, mensagem: str = "quanto pago?") -> BeneficiarioLLM:
    chamadas = {"n": 0}

    async def gerar(prompt: str) -> str:
        chamadas["n"] += 1
        return json.dumps({"mensagem": mensagem, "tema": "cobranca", "encerrar": chamadas["n"] >= n})

    return BeneficiarioLLM(gerar)


def _condutor(
    despachante: _Despachante, beneficiario: BeneficiarioLLM, relogio: _Relogio
) -> tuple[_Condutor, _Limpeza]:
    limpeza = _Limpeza()
    condutor = _Condutor(
        dispatcher=despachante,
        beneficiario=beneficiario,
        limpeza=limpeza,
        run_id="r1",
        config=cd.ConfigDoCondutor(concorrencia=2, intervalo_min_s=10.0),
        relogio=relogio,
        dormir=relogio.dormir,
    )
    return condutor, limpeza


def _plano(persona_id: str, telefone_: str = "5511900000500", cid: str = "c0001") -> cd.PlanoDeConversa:
    return cd.PlanoDeConversa(
        id=cid, persona=por_id(persona_id), sujeito=sujeito(), telefone=telefone_, semente=1
    )


# --- plano ----------------------------------------------------------------------------------------
def test_planejar_poe_o_dono_na_primeira_e_o_acesso_na_faixa_propria() -> None:
    dono = sujeito(dono=True, rotulo="s000")
    outros = [sujeito(rotulo=f"s{i:03d}") for i in range(1, 4)]
    personas = [por_id("idoso"), por_id("cpf_errado"), por_id("contesta"), por_id("recusa_revoga")]
    planos = cd.planejar_conversas(personas, [dono, *outros], semente=3)
    assert [p.id for p in planos] == ["c0001", "c0002", "c0003", "c0004"]
    assert planos[0].sujeito.dono and planos[0].telefone == "5511900000500"
    assert planos[1].telefone == "5511900000760" and planos[3].telefone == "5511900000761"
    principal = int(planos[2].telefone[-3:])
    assert principal in cd.FAIXA_PRINCIPAL
    assert planos == cd.planejar_conversas(personas, [dono, *outros], semente=3)


def test_faixas_nao_se_sobrepoem_e_ficam_na_faixa_sintetica() -> None:
    assert set(cd.FAIXA_PRINCIPAL).isdisjoint(cd.FAIXA_ACESSO)
    assert cd.telefone(799) == "5511900000799" and cd.telefone(500) == "5511900000500"


# --- conversa -------------------------------------------------------------------------------------
async def test_verifica_uma_vez_por_telefone_e_reinicia_entre_conversas() -> None:
    relogio = _Relogio()
    despachante = _Despachante()
    condutor, limpeza = _condutor(despachante, _persona_que_encerra_no(5), relogio)
    primeira = await condutor.conversar(_plano("apressado", cid="c0001"))
    segunda = await condutor.conversar(_plano("apressado", cid="c0002"))
    assert [t.fase for t in primeira.turnos][:4] == ["acesso"] * 4
    assert primeira.turnos[3].textos("acesso") == [acesso_mod.RESPOSTA_VERIFICADO]
    assert [t.fase for t in segunda.turnos] == ["persona"] * len(segunda.turnos)
    # a persona `apressado` tem turnos_min=3, entao o encerrar do 5o turno vale
    assert len(primeira.turnos_da_persona()) == 5 and primeira.encerrada_por == "persona"
    assert limpeza.reiniciadas == ["wa:amh:hk1_5511900000500"] * 2
    assert limpeza.acessos_apagados == ["wa:amh:hk1_5511900000500"]
    # o CPF real chegou ao despachante (marcador substituido), e o resultado guarda so' tokens
    assert CPF_SINTETICO in despachante.recebidas
    assert "texto_livre" not in primeira.turnos[-1].resultado


async def test_ritmo_entre_mensagens_da_mesma_conversa() -> None:
    relogio = _Relogio()
    condutor, _ = _condutor(_Despachante(), _persona_que_encerra_no(3), relogio)
    await condutor.conversar(_plano("apressado"))
    assert relogio.esperas and all(e == 10.0 for e in relogio.esperas)


async def test_persona_de_acesso_segue_o_roteiro_do_zero() -> None:
    relogio = _Relogio()
    despachante = _Despachante()
    condutor, limpeza = _condutor(despachante, _persona_que_encerra_no(2), relogio)
    c = await condutor.conversar(_plano("cpf_errado", telefone_="5511900000760"))
    assert despachante.recebidas[:5] == [
        "Bom dia",
        "ACEITO",
        cpf_errado(CPF_SINTETICO),
        CPF_SINTETICO,
        "14/03/1980",
    ]
    assert c.turnos[2].textos("acesso") == [acesso_mod.CPF_INVALIDO]
    assert limpeza.acessos_apagados == ["wa:amh:hk1_5511900000760"]
    assert len(c.turnos_da_persona()) == 2


async def test_roteiro_com_falas_do_modelo_no_meio() -> None:
    relogio = _Relogio()
    despachante = _Despachante()
    condutor, _ = _condutor(despachante, _persona_que_encerra_no(1, mensagem="e o vencimento?"), relogio)
    c = await condutor.conversar(_plano("recusa_revoga", telefone_="5511900000761"))
    assert "REVOGAR" in despachante.recebidas
    fases = [t.fase for t in c.turnos]
    assert (
        fases[:5] == ["acesso"] * 5
        and fases[5] == "persona"
        and fases[6] == "acesso"
        and fases[7] == "persona"
    )


async def test_verificacao_que_nao_conclui_para_a_conversa_com_retentativas() -> None:
    relogio = _Relogio()
    condutor, _ = _condutor(_Despachante(indisponivel=True), _persona_que_encerra_no(1), relogio)
    c = await condutor.conversar(_plano("apressado"))
    assert c.erro_preparo == "verificacao_nao_concluida"
    assert relogio.esperas.count(cd.ESPERA_INDISPONIVEL_S) == cd.TENTATIVAS_INDISPONIVEL * len(
        cd.ROTEIRO_DE_VERIFICACAO
    )


async def test_turno_que_quebra_vira_erro_e_a_conversa_segue() -> None:
    relogio = _Relogio()
    condutor, _ = _condutor(_Despachante(falhar_em="quanto pago?"), _persona_que_encerra_no(4), relogio)
    c = await condutor.conversar(_plano("apressado"))
    erros = [t.erro for t in c.turnos_da_persona()]
    assert erros and all(e == "RuntimeError" for e in erros)
    assert all(t.latencia_s >= 0 for t in c.turnos)


async def test_persona_invalida_encerra_a_conversa() -> None:
    async def gerar(prompt: str) -> str:
        return "sem json"

    condutor, _ = _condutor(_Despachante(), BeneficiarioLLM(gerar), _Relogio())
    c = await condutor.conversar(_plano("apressado"))
    assert c.encerrada_por == "persona_invalida" and not c.turnos_da_persona()


async def test_executar_uma_fila_por_telefone_e_ordem_estavel() -> None:
    condutor, _ = _condutor(_Despachante(), _persona_que_encerra_no(3), _Relogio())
    planos = [
        _plano("apressado", "5511900000501", "c0003"),
        _plano("apressado", "5511900000500", "c0001"),
        _plano("apressado", "5511900000500", "c0002"),
    ]
    conversas = await condutor.executar(planos)
    assert [c.id for c in conversas] == ["c0001", "c0002", "c0003"]
    assert planos[0].como_manifesto("cid")["fluxo_de_acesso"] is False
