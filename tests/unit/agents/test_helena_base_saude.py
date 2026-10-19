"""BASE DE CONHECIMENTO DE SAUDE na Helena (ADR-0064, DL-0118), atras da chave `MAEZO_HELENA_BASE_SAUDE`.

O que este arquivo prova, contra o GRAFO REAL (com as tabelas `.dmn` REAIS, primeiro acerto) e a base de
FIXTURE (`tests/fixtures/base_saude`: dipirona, PSA, dor de cabeca):

  1. PERGUNTA INFORMATIVA de saude ("pra que serve dipirona?", "o que e' PSA?") responde DA BASE — texto,
     credito, link, aviso — SEM caso e SEM dose; assunto que nao existe recebe "nao tenho informacao
     aprovada"; resultado PESSOAL ("meu PSA deu fora") recebe o texto geral + "leve ao seu medico".
  2. Os portoes de seguranca vem ANTES e intactos: dor no peito escala com 192; risco psicossocial; pedido de
     pessoa e cobranca nunca sao respondidos pela base.
  3. DOR sem sinal de alarme ("muita dor de cabeca desde ontem"): o fail-safe r10 da tabela de adulto deixa de
     abrir caso na hora; vem a pergunta de ALERTA do arquivo `sin-*`; "nao" -> orientacao + opcoes; "sim" ->
     alerta com 192. Termo de alarme do arquivo escala na hora. So' a r10, so' adulto, so' com a v4.
  4. RECUSA com caso aberto -> confirmacao -> SIM -> o caso e' ENCERRADO pela mensagem
     `msg.escalation.withdrawn`, com auditoria ANTES do efeito; caso grave NAO e' encerrado (192).
  5. CHAVE DESLIGADA: o grafo de antes, byte a byte; base invalida desliga a chave (falha fechada).
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena.graph import (
    _HELENA_MEMORIA_DE_CONVERSA,
    MOTIVO_FAILSAFE_ADULTO,
    NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO,
    RESPOSTA_CASO_ENCERRADO,
    RESPOSTA_CASO_GRAVE_NAO_ENCERRA,
    RESPOSTA_CONFIRMA_ENCERRAMENTO,
    RESPOSTA_ENCERRAMENTO_MANTIDO,
    RESPOSTA_RECUSA_DE_EQUIPE,
    RESULTADO_ENCERRADO_A_PEDIDO,
    HelenaGraph,
    caso_grave_para_encerrar,
    new_helena_state,
    pede_para_encerrar_o_atendimento,
)
from maezo.agents.helena.prompts import (
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)
from maezo.conhecimento import base_saude as b
from maezo.qa.simulador.checagens import frases_longas
from maezo.runtime.caso_clinico import chave_do_escalonamento
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    FakeCibSevenTransport,
    ProcessInstance,
    correlate_dedup_key,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_REPO = Path(__file__).resolve().parents[3]
_FIXTURE = _REPO / "tests" / "fixtures" / "base_saude"
_CONVERSA = "wa:amh:hk1_base_saude"
_TABELAS = (
    "triage_redflag_adult",
    "triage_redflag_pediatric",
    "triage_redflag_gestante",
    "triage_redflag_mental_health",
    "triage_sufficiency",
)
_BK = chave_do_escalonamento("amh", _CONVERSA)
_BK_CLIN = chave_do_escalonamento("amh", _CONVERSA, clinico=True)


class _DmnReal:
    def __init__(self) -> None:
        self._tabelas = {chave: read_live_table(DMN_DIR / f"{chave}.dmn") for chave in _TABELAS}
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        self.chamadas.append((decision_key, dict(variables)))
        if decision_key not in self._tabelas:
            raise DmnEvaluationError(f"tabela indisponivel: {decision_key}")
        tabela = self._tabelas[decision_key]
        veredito = evaluate(tabela, {nome: variables.get(nome) for nome in tabela.input_names})
        return [veredito.saidas], DmnVersion(
            key=decision_key, id=f"{decision_key}:1:offline", version=1, deployment_id="offline"
        )

    def chaves(self) -> list[str]:
        return [chave for chave, _ in self.chamadas]


class _Motor(FakeCibSevenTransport):
    """O motor: guarda os starts e, como o CIB Seven com o BPMN v1.1.0, ENCERRA a instancia viva quando recebe
    `msg.escalation.withdrawn` pela business key (sem instancia viva, recusa como o motor: 400)."""

    def __init__(self, *, recusa_correlacao: bool = False) -> None:
        super().__init__()
        self.inicios: list[dict[str, Any]] = []
        self._recusa = recusa_correlacao

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.inicios.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        await super().correlate_message(message_name, business_key, variables)
        if self._recusa:
            raise CibSevenError("CIB Seven correlate_message failed [500]")
        linhas = self._history.get(business_key, [])
        if not any(i.state == "ACTIVE" for i in linhas):
            raise CibSevenError("CIB Seven correlate_message failed [400]")
        self._history[business_key] = [
            replace(i, state="COMPLETED") if i.state == "ACTIVE" else i for i in linhas
        ]

    def abrir(self, business_key: str, **variaveis: Any) -> None:
        self.seed_instance(
            ProcessInstance(
                instance_id=f"inst-{business_key}",
                process_key="SP-OP-ESCALATION-001",
                business_key=business_key,
                state="ACTIVE",
            ),
            variables=variaveis,
        )


class _Modelo:
    def __init__(self) -> None:
        self.respostas: list[str] = []
        self.prompts: list[str] = []
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return self.respostas.pop(0) if self.respostas else ""


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


def _classify(**campos: Any) -> str:
    dados: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    dados.update(campos)
    return json.dumps(dados)


_INFO = _classify()
_CLINICA = _classify(intent="clinical_question")
_MUITA_DOR = _classify(intent="symptom", population="adult", intensidade="grave")
_DOR_NO_PEITO = _classify(
    intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
)
_PESSOA = _classify(intent="human_request")


class _Conversa:
    def __init__(
        self,
        *,
        chave: bool = True,
        v4: bool = True,
        base: b.BaseSaude | None = None,
        motor: _Motor | None = None,
        auditoria: FakeStartAuditSink | None = None,
    ) -> None:
        self.chave = chave
        self.v4 = v4
        self.base = base if base is not None else b.carregar_base(_FIXTURE)
        self.cib = motor or _Motor()
        self.auditoria = auditoria or FakeStartAuditSink()
        self.dmn = _DmnReal()
        self.whatsapp = _WhatsApp()
        self.modelo = _Modelo()
        self.memoria: dict[str, Any] = {}

    def _grafo(self) -> Any:
        grafo = HelenaGraph(
            inference=self.modelo,  # type: ignore[arg-type]
            dmn=self.dmn,  # type: ignore[arg-type]
            cibseven=self.cib,
            audit_sink=self.auditoria,
            whatsapp=self.whatsapp,
            historico_enabled=True,
            coleta_enabled=True,
            helena_v4_enabled=self.v4,
            helena_base_saude_enabled=self.chave,
            base_saude=self.base,
            relogio=lambda: datetime(2026, 10, 17, 15, 0, tzinfo=UTC),
        )
        return grafo.compile_graph().compile()

    async def diz(self, mensagem: str, extracao: str = _INFO, *extras: str) -> dict[str, Any]:
        self.modelo.respostas.extend([extracao, *extras])
        estado: dict[str, Any] = dict(
            new_helena_state(
                tenant_id="amh",
                conversation_id=_CONVERSA,
                canal="whatsapp",
                beneficiario_pseudo_id="PSEUDO-BASE-SAUDE",
                message_body=mensagem,
                message_ref="hk1_msg_base_saude",
            )
        )
        estado.update(self.memoria)
        resultado = await self._grafo().ainvoke(estado)
        self.memoria = {k: resultado[k] for k in _HELENA_MEMORIA_DE_CONVERSA if k in resultado}
        return cast(dict[str, Any], resultado)

    @property
    def motivos(self) -> list[str]:
        return [v["motivo_categoria"] for v in self.cib.inicios]


def _texto(resultado: dict[str, Any]) -> str:
    return str(resultado.get("response_text") or "")


# =================================================================================================
# 1. Pergunta informativa, resultado pessoal, assunto inexistente
# =================================================================================================


async def test_pra_que_serve_dipirona_responde_da_base_sem_caso_com_fonte_e_link_sem_dose() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Pra que serve dipirona?", _CLINICA)

    texto = _texto(resultado)
    dipirona = conversa.base.get("med-dipirona")
    assert dipirona is not None
    assert dipirona.texto in texto
    assert "Texto da operadora com base em Bula do paciente (ANVISA)." in texto
    assert "Para ler mais: https://consultas.anvisa.gov.br/" in texto
    assert texto.endswith(b.AVISO_PADRAO)
    assert not re.search(r"\b\d+ ?mg\b|\bdose\b|\btome\b", texto.lower())
    assert conversa.cib.inicios == [], "pergunta informativa nao abre caso"
    assert resultado["response_kind"] == "inform"
    assert conversa.whatsapp.enviados == [texto]


async def test_o_que_e_psa_cita_a_fonte_literal() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("O que é PSA?", _CLINICA)

    assert "Fonte: INCA — Instituto Nacional de Câncer." in _texto(resultado)
    assert conversa.cib.inicios == []


async def test_resultado_pessoal_recebe_o_geral_e_leve_ao_medico_sem_caso() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Meu PSA deu fora dos parâmetros, o que me sugere?", _CLINICA)

    texto = _texto(resultado)
    assert "Posso te passar a informação geral sobre PSA." in texto
    assert b.FRASE_LEVE_AO_MEDICO in texto
    assert b.AVISO_PADRAO in texto
    assert conversa.cib.inicios == []


async def test_assunto_inexistente_recebe_nao_tenho_informacao_aprovada() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("O que é xyzabc?", _CLINICA)

    assert _texto(resultado) == b.RESPOSTA_NAO_ENCONTRADO
    assert conversa.cib.inicios == []


async def test_a_pergunta_clinica_sem_queixa_deixa_de_escalar_so_com_a_chave() -> None:
    desligada = _Conversa(chave=False)
    await desligada.diz("Pra que serve dipirona?", _CLINICA)
    assert desligada.motivos == ["intencao_clinica"], "chave desligada: o Gatilho 2 de sempre"


async def test_ambiguo_o_modelo_escolhe_so_da_lista_fechada(tmp_path: Path) -> None:
    import yaml

    for ident, termo in (("con-cistite", "cistite"), ("con-cistete", "cistete")):
        pasta = tmp_path / "condicao"
        pasta.mkdir(exist_ok=True)
        dados = yaml.safe_load((_FIXTURE / "exame" / "exa-psa.yaml").read_text(encoding="utf-8"))
        dados.update(
            {
                "id": ident,
                "categoria": "condicao",
                "titulo": termo.title(),
                "sinonimos": [termo],
                "texto": f"Texto sintetico sobre {termo}, curto e sem nada proibido.",
            }
        )
        (pasta / f"{ident}.yaml").write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    base = b.carregar_base(tmp_path)

    escolheu = _Conversa(base=base)
    resultado = await escolheu.diz("O que é cistote?", _CLINICA, '{"id": "con-cistete"}')
    assert "Texto sintetico sobre cistete" in _texto(resultado)
    assert "Lista fechada de ids" in escolheu.modelo.prompts[-1]

    inventou = _Conversa(base=base)
    resultado = await inventou.diz("O que é cistote?", _CLINICA, '{"id": "med-dipirona"}')
    assert _texto(resultado) == b.RESPOSTA_NAO_ENCONTRADO

    escreveu = _Conversa(base=base)
    resultado = await escreveu.diz("O que é cistote?", _CLINICA, "Cistite e uma infeccao, tome agua.")
    assert _texto(resultado) == b.RESPOSTA_NAO_ENCONTRADO


# =================================================================================================
# 2. Os portoes de seguranca vem antes
# =================================================================================================


async def test_dor_no_peito_e_falta_de_ar_escala_com_192() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Estou com dor no peito e falta de ar", _DOR_NO_PEITO)

    assert conversa.motivos == ["red_flag_clinico"]
    assert "192" in _texto(resultado)


async def test_risco_psicossocial_vence_a_base() -> None:
    conversa = _Conversa()
    await conversa.diz("O que é depressão? Não aguento mais viver", _classify(psychosocial_risk=True))
    assert conversa.motivos == ["risco_psicossocial"]


@pytest.mark.parametrize(
    ("mensagem", "extracao"),
    [
        ("O que é PSA? Quero falar com uma pessoa", _PESSOA),
        ("Pra que serve dipirona? me passa pra um atendente", _CLINICA),
    ],
)
async def test_pedido_de_pessoa_vence_a_base(mensagem: str, extracao: str) -> None:
    conversa = _Conversa()
    await conversa.diz(mensagem, extracao)
    assert conversa.motivos, "o pedido de pessoa abre o caso (RN 623/2024)"


async def test_cobranca_nao_e_respondida_pela_base() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("O que é coparticipação no meu boleto?", _CLINICA)
    assert not b.e_texto_da_base(_texto(resultado), conversa.base)


# =================================================================================================
# 3. Dor sem alarme: perguntas de alerta antes do caso
# =================================================================================================


def test_o_literal_da_r10_esta_na_tabela_de_adulto() -> None:
    """O grafo intercepta SO' a saida exata da r10; mudar a tabela sem mudar o grafo deixa isto vermelho."""
    tabela = (DMN_DIR / "triage_redflag_adult.dmn").read_text(encoding="utf-8")
    bloco = tabela.split('<rule id="r10">', 1)[1].split("</rule>", 1)[0]
    assert f'"{MOTIVO_FAILSAFE_ADULTO}"' in bloco
    assert '"P2"' in bloco and '"ESCALATE_NURSE"' in bloco


async def test_muita_dor_de_cabeca_recebe_as_perguntas_do_arquivo_e_nao_abre_caso() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Estou com muita dor de cabeça desde ontem", _MUITA_DOR)

    texto = _texto(resultado)
    assert conversa.cib.inicios == [], "o fail-safe de dor sem alarme nao abre caso na hora"
    assert "A dor começou de repente e é a pior que você já sentiu?" in texto
    assert "Responda sim ou não." in texto
    assert conversa.memoria["coleta_pendente"] == "V4_ALERTA:sin-dor-de-cabeca:adult"

    resposta = await conversa.diz("Não", _INFO)
    final = _texto(resposta)
    sin = conversa.base.get("sin-dor-de-cabeca")
    assert sin is not None and sin.texto in final
    assert "1) saber onde marcar consulta pelo plano" in final
    assert "2) falar com a nossa equipe de saúde" in final
    assert "ligue 192" in final
    assert conversa.cib.inicios == []


async def test_muita_dor_com_sim_no_alerta_escala_com_192() -> None:
    conversa = _Conversa()
    await conversa.diz("Estou com muita dor de cabeça desde ontem", _MUITA_DOR)
    resultado = await conversa.diz("Sim", _MUITA_DOR)

    assert conversa.motivos == ["red_flag_clinico"]
    assert "192" in _texto(resultado)
    resumo = conversa.cib.inicios[0]["resumo_contexto"]
    assert "pior dor da vida" in resumo, "o resumo cita os sinais do arquivo da base"


async def test_termo_de_alarme_do_arquivo_escala_na_hora() -> None:
    conversa = _Conversa()
    await conversa.diz(
        "Estou com dor de cabeça e rigidez no pescoço", _classify(intent="symptom", population="adult")
    )
    assert conversa.motivos == ["red_flag_clinico"]


@pytest.mark.parametrize(
    ("kwargs", "mensagem", "extracao"),
    [
        ({"chave": False}, "Estou com muita dor de cabeça desde ontem", _MUITA_DOR),
        ({"v4": False}, "Estou com muita dor de cabeça desde ontem", _MUITA_DOR),
        (
            {},
            "Meu filho está com muita dor de cabeça",
            _classify(intent="symptom", population="pediatric", intensidade="grave"),
        ),
        ({}, "Estou muito tonto e cansado", _MUITA_DOR),  # nao e' DOR: o fail-safe continua
        ({}, "Estou com muita dor de cabeça e desmaio", _MUITA_DOR),  # alarme do arquivo
    ],
)
async def test_o_fail_safe_continua_fora_do_caso_aprovado(
    kwargs: dict[str, Any], mensagem: str, extracao: str
) -> None:
    conversa = _Conversa(**kwargs)
    await conversa.diz(mensagem, extracao)
    assert conversa.motivos, (
        "fora de 'dor sem alarme, adulto, base + v4 ligadas' o fail-safe escala como sempre"
    )


# =================================================================================================
# 4. Recusa com caso aberto: confirmacao e encerramento
# =================================================================================================


@pytest.mark.parametrize(
    "mensagem",
    ["Não quero falar com ninguém", "Pode cancelar o atendimento", "Não preciso mais", "Cancela o caso"],
)
def test_as_formas_do_pedido_de_encerrar(mensagem: str) -> None:
    assert pede_para_encerrar_o_atendimento(mensagem)


@pytest.mark.parametrize(
    "mensagem", ["Quero cancelar meu plano", "Não quero pagar o boleto", "Cancela a consulta"]
)
def test_o_que_nao_e_pede_para_encerrar_o_atendimento(mensagem: str) -> None:
    assert not pede_para_encerrar_o_atendimento(mensagem)


def test_o_que_e_grave_nao_se_encerra() -> None:
    assert caso_grave_para_encerrar("risco_psicossocial", "moderada")
    assert caso_grave_para_encerrar("red_flag_clinico", "grave")
    assert caso_grave_para_encerrar("", None), "na duvida, grave"
    assert not caso_grave_para_encerrar("solicitacao_humano", "leve")
    assert not caso_grave_para_encerrar("intencao_clinica", "moderada")
    assert not caso_grave_para_encerrar("red_flag_clinico", "moderada")


async def test_pedido_de_pessoa_depois_recusa_e_sim_encerra_o_caso_no_motor() -> None:
    conversa = _Conversa()
    await conversa.diz("Quero falar com uma pessoa", _PESSOA)
    assert conversa.motivos == ["solicitacao_humano"]
    assert await conversa.cib.find_active_instance(_BK) is not None

    pergunta = await conversa.diz("Não quero falar com ninguém", _INFO)
    assert _texto(pergunta) == RESPOSTA_CONFIRMA_ENCERRAMENTO
    assert conversa.cib.correlate_calls == []

    fim = await conversa.diz("SIM", _INFO)
    assert _texto(fim) == RESPOSTA_CASO_ENCERRADO
    assert conversa.whatsapp.enviados[-2:] == [RESPOSTA_CONFIRMA_ENCERRAMENTO, RESPOSTA_CASO_ENCERRADO]
    assert await conversa.cib.find_active_instance(_BK) is None, "a instancia nao esta mais ativa no motor"
    [chamada] = conversa.cib.correlate_calls
    assert chamada["message_name"] == NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO
    assert chamada["business_key"] == _BK
    assert chamada["variables"]["resultado"] == RESULTADO_ENCERRADO_A_PEDIDO
    # AUDITORIA ANTES DO EFEITO: o registro do encerramento existe, com a chave de dedup da instancia.
    registro, dedup = conversa.auditoria.calls[-1]
    acao = f"correlate_message:SP-OP-ESCALATION-001:{NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO}"
    assert registro.action == acao
    assert dedup == correlate_dedup_key(
        "amh", "SP-OP-ESCALATION-001", NOME_DA_MENSAGEM_DE_ENCERRAMENTO_A_PEDIDO, _BK, f"fake-{_BK}"
    )
    assert _BK not in json.dumps(registro.details), "a business key nunca vai ao registro em claro"
    assert len(conversa.cib.inicios) == 1, "encerrar nao abre caso novo"


async def test_auditoria_que_falha_impede_o_encerramento() -> None:
    from maezo.gateway.audit_postgres import AuditPersistenceError

    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    conversa = _Conversa(motor=motor, auditoria=FakeStartAuditSink(fail=True))
    conversa.memoria = {"coleta_pendente": graph_module.PENDENTE_ENCERRAR_CASO}
    with pytest.raises(AuditPersistenceError):
        await conversa.diz("sim", _INFO)
    assert motor.correlate_calls == [], "sem registro duravel, nenhum efeito no motor"


async def test_caso_grave_nao_e_encerrado_e_recebe_o_192() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="red_flag_clinico", severidade="grave")
    conversa = _Conversa(motor=motor)
    pergunta = await conversa.diz("Pode cancelar o atendimento", _INFO)
    assert _texto(pergunta) == RESPOSTA_CONFIRMA_ENCERRAMENTO

    resultado = await conversa.diz("sim", _INFO)
    assert _texto(resultado) == RESPOSTA_CASO_GRAVE_NAO_ENCERRA
    assert conversa.whatsapp.enviados[-1] == RESPOSTA_CASO_GRAVE_NAO_ENCERRA
    assert "192" in _texto(resultado)
    assert motor.correlate_calls == []
    assert await motor.find_active_instance(_BK) is not None


async def test_caso_clin_paralelo_segue_a_mesma_regra() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    motor.abrir(_BK_CLIN, motivo_categoria="risco_psicossocial", severidade="moderada")
    conversa = _Conversa(motor=motor)
    await conversa.diz("Não preciso mais", _INFO)
    resultado = await conversa.diz("sim", _INFO)

    assert await motor.find_active_instance(_BK) is None, "o caso administrativo foi encerrado"
    assert await motor.find_active_instance(_BK_CLIN) is not None, "o psicossocial (P1) fica"
    assert _texto(resultado) == RESPOSTA_CASO_GRAVE_NAO_ENCERRA


async def test_nao_mantem_o_caso_e_falha_do_motor_diz_a_verdade() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    conversa = _Conversa(motor=motor)
    await conversa.diz("Cancela o caso", _INFO)
    resultado = await conversa.diz("não", _INFO)
    assert _texto(resultado) == RESPOSTA_ENCERRAMENTO_MANTIDO
    assert await motor.find_active_instance(_BK) is not None

    recusa = _Motor(recusa_correlacao=True)
    recusa.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    falha = _Conversa(motor=recusa)
    await falha.diz("Cancela o caso", _INFO)
    resultado = await falha.diz("sim", _INFO)
    assert _texto(resultado) == graph_module.RESPOSTA_ENCERRAMENTO_FALHOU


async def test_recusa_sem_caso_aberto_e_a_resposta_de_antes() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Não quero falar com ninguém", _INFO)
    assert _texto(resultado) == RESPOSTA_RECUSA_DE_EQUIPE
    assert conversa.memoria.get("coleta_pendente") is None


async def test_sim_sem_a_pergunta_de_confirmacao_nao_encerra_nada() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    conversa = _Conversa(motor=motor)
    await conversa.diz("sim", _INFO)
    assert motor.correlate_calls == []


# =================================================================================================
# 5. Cercas, regua de tom e a chave desligada
# =================================================================================================


def test_as_respostas_da_fixture_passam_nas_cercas_e_na_regua() -> None:
    base = b.carregar_base(_FIXTURE)
    textos = [b.RESPOSTA_NAO_ENCONTRADO, b.RESPOSTA_RESULTADO_NAO_ENCONTRADO]
    for assunto in base.assuntos.values():
        textos += [b.resposta_informativa(assunto), b.resposta_de_resultado_pessoal(assunto)]
        if assunto.perguntas_alerta:
            textos.append(b.texto_final_do_sintoma_com_orientacao("dor de cabeça", assunto))
    textos += sorted(graph_module.TEXTOS_FIXOS_DO_ENCERRAMENTO)
    for texto in textos:
        assert motivo_de_recusa(texto, "inform", start_aconteceu=None) is None, texto
        assert motivo_de_canal_nao_confirmado(texto) is None, texto
        assert menciona_encaminhamento(texto) is False, texto
        assert frases_longas(texto) == [], texto


_AMOSTRA = [
    ("Pra que serve dipirona?", _CLINICA),
    ("O que é PSA?", _CLINICA),
    ("Estou com muita dor de cabeça desde ontem", _MUITA_DOR),
    ("Não quero falar com ninguém", _INFO),
    ("Quero falar com uma pessoa", _PESSOA),
    ("Oi", _classify(intent="greeting")),
]


@pytest.mark.parametrize(("mensagem", "extracao"), _AMOSTRA)
async def test_chave_desligada_e_byte_a_byte_o_grafo_sem_a_chave(mensagem: str, extracao: str) -> None:
    """`helena_base_saude_enabled=False` (explicito, com base injetada) e o argumento ausente produzem
    EXATAMENTE a mesma saida, os mesmos prompts e as mesmas chamadas; nenhum metodo da base e' alcancado."""
    explicito = _Conversa(chave=False)
    sem_arg = _Conversa(chave=False)

    def _grafo_sem_argumento(self: _Conversa) -> Any:
        grafo = HelenaGraph(
            inference=self.modelo,  # type: ignore[arg-type]
            dmn=self.dmn,  # type: ignore[arg-type]
            cibseven=self.cib,
            audit_sink=self.auditoria,
            whatsapp=self.whatsapp,
            historico_enabled=True,
            coleta_enabled=True,
            helena_v4_enabled=self.v4,
            relogio=lambda: datetime(2026, 10, 17, 15, 0, tzinfo=UTC),
        )
        return grafo.compile_graph().compile()

    def _nao_deve_ser_chamado(*_: Any, **__: Any) -> None:
        raise AssertionError("a chave esta desligada: a base nao pode ser alcancada")

    alvos = ("_base_antes_dos_gatilhos", "_base_confirmacao_de_encerramento", "_base_hoje", "_retirar_caso")
    with (
        mock.patch.object(_Conversa, "_grafo", _grafo_sem_argumento),
        mock.patch.multiple(HelenaGraph, **{a: _nao_deve_ser_chamado for a in alvos}),
    ):
        a = await sem_arg.diz(mensagem, extracao)
    with mock.patch.multiple(HelenaGraph, **{a: _nao_deve_ser_chamado for a in alvos}):
        bb = await explicito.diz(mensagem, extracao)

    for campo in (
        "response_text",
        "response_kind",
        "next_kind",
        "escalation_motivo",
        "intent",
        "coleta_pendente",
    ):
        assert a.get(campo) == bb.get(campo), campo
    assert sem_arg.modelo.prompts == explicito.modelo.prompts
    assert sem_arg.dmn.chaves() == explicito.dmn.chaves()
    assert sem_arg.motivos == explicito.motivos


async def test_base_invalida_desliga_a_chave(tmp_path: Path) -> None:
    (tmp_path / "exame").mkdir()
    (tmp_path / "exame" / "exa-quebrado.yaml").write_text("id: exa-quebrado\n", encoding="utf-8")
    with mock.patch.object(b, "raiz_padrao", lambda: tmp_path):
        grafo = HelenaGraph(
            inference=_Modelo(),  # type: ignore[arg-type]
            dmn=_DmnReal(),  # type: ignore[arg-type]
            cibseven=_Motor(),
            audit_sink=FakeStartAuditSink(),
            whatsapp=_WhatsApp(),
            helena_base_saude_enabled=True,
        )
    assert grafo._base_saude is False


def test_a_pendencia_de_encerramento_some_com_a_chave_desligada() -> None:
    import asyncio

    grafo = HelenaGraph(
        inference=_Modelo(),  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=_Motor(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        coleta_enabled=True,
    )
    estado = dict(
        new_helena_state(
            tenant_id="amh",
            conversation_id=_CONVERSA,
            canal="whatsapp",
            beneficiario_pseudo_id="P",
            message_body="sim",
            message_ref="m",
        )
    )
    estado["coleta_pendente"] = graph_module.PENDENTE_ENCERRAR_CASO
    reset = asyncio.run(grafo.receive(estado))  # type: ignore[arg-type]
    assert reset["coleta_pendente"] is None


# =================================================================================================
# A fiacao da chave: nasce desligada em todo lugar; so' o dev liga, pelo Terraform
# =================================================================================================


def test_a_chave_nasce_desligada_no_grafo_na_fabrica_e_nas_settings() -> None:
    from maezo.agents.helena.graph import build
    from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    assert inspect.signature(HelenaGraph.__init__).parameters["helena_base_saude_enabled"].default is False
    assert 'cfg.get("helena_base_saude_enabled", False) is True' in inspect.getsource(build)
    assert WhatsAppWebhookSettings.model_fields["helena_base_saude"].default is False
    alias = WhatsAppWebhookSettings.model_fields["helena_base_saude"].validation_alias
    assert "MAEZO_HELENA_BASE_SAUDE" in getattr(alias, "choices", ())
    assert HelenaDispatcher.__dataclass_fields__["helena_base_saude_enabled"].default is False
    assert '"helena_base_saude_enabled": self.helena_base_saude_enabled' in inspect.getsource(
        HelenaDispatcher._compile_turn_graph
    )
    servico = (_REPO / "src/maezo/platform/webhooks/service.py").read_text(encoding="utf-8")
    assert "helena_base_saude_enabled=settings.helena_base_saude" in servico


def test_a_chave_so_e_ligada_no_dev_pelo_terraform() -> None:
    envs = _REPO / "deploy" / "aws-ecs" / "envs"
    dev = envs / "dev-sa-east-1"
    tfvars = (dev / "amh-interop.auto.tfvars").read_text(encoding="utf-8")
    assert re.search(r"^helena_base_saude = true$", tfvars, re.MULTILINE)
    receptor = (dev / "service-webhook-receiver.tf").read_text(encoding="utf-8")
    assert '{ name = "MAEZO_HELENA_BASE_SAUDE", value = tostring(var.helena_base_saude) }' in receptor
    variaveis = (dev / "variables.tf").read_text(encoding="utf-8")
    bloco = variaveis.split('variable "helena_base_saude"', 1)[1].split("\n}\n", 1)[0]
    assert "default     = false" in bloco
    simulador = (dev / "task-simulador.tf").read_text(encoding="utf-8")
    assert "local.webhook_receiver_contrato.environment_app" in simulador, (
        "o simulador herda a env do receptor"
    )
    for outro in envs.iterdir():
        if outro.is_dir() and outro.name != "dev-sa-east-1":
            for arquivo in outro.rglob("*.tf*"):
                assert "helena_base_saude" not in arquivo.read_text(encoding="utf-8"), arquivo
