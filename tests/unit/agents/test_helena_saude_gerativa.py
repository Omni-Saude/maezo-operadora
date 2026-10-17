"""SAUDE GERATIVA da Helena (ADR-0065, DL-0119): a chave `MAEZO_HELENA_SAUDE_GERATIVA` + a allowlist.

O que este arquivo prova, contra o GRAFO REAL (tabelas `.dmn` reais, base de FIXTURE) com um transporte FALSO
no lugar da rota global (`experimento_global_autorizado_dpo_2026-10-17`):

  1. ALLOWLIST: so' as conversas da lista vao ao modelo; FORA da lista o grafo e' o de antes, texto a texto
     (mesmas respostas, mesmos casos) e o transporte nunca e' chamado.
  2. MINIMIZACAO: o que sai para o modelo nao tem CPF, nome, carteirinha, telefone, e-mail nem data; as falas
     da Helena nunca vao.
  3. CHECAGEM: texto que reprova -> UMA reescrita; reprovou de novo -> resposta segura padrao.
  4. FALHA do modelo -> resposta segura padrao, enviada, sem quebrar o turno.
  5. ALARME, risco psicossocial e pedido de pessoa NUNCA vao ao modelo (192/caso como hoje).
  6. Queixa: pergunta de alerta CURTA (sem eco, sem gravidez para adulto), CONTEXTO sem alarme recebe a
     orientacao redigida + as opcoes 1/2 (sem repetir a pergunta), "nao" idem, "sim" escala como sempre.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import _HELENA_MEMORIA_DE_CONVERSA, HelenaGraph, new_helena_state
from maezo.conhecimento import base_saude as b
from maezo.conhecimento import saude_gerativa as sg
from maezo.runtime.inference.experimento_global import (
    MODELO_HAIKU_GLOBAL,
    ROTA_DE_RESIDENCIA,
    FalhaDaRotaGlobalError,
    RespostaDaRotaGlobal,
    TransporteGlobalExperimental,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnVersion
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

_REPO = Path(__file__).resolve().parents[3]
_FIXTURE = _REPO / "tests" / "fixtures" / "base_saude"
_DONO = "wa:amh:hk1_" + "a" * 64
_OUTRA = "wa:amh:hk1_" + "b" * 64
_TABELAS = (
    "triage_redflag_adult",
    "triage_redflag_pediatric",
    "triage_redflag_gestante",
    "triage_redflag_mental_health",
    "triage_sufficiency",
)

_BOA_ENDOMETRIOSE = (
    "A endometriose é uma condição em que um tecido parecido com o que reveste o útero cresce fora dele. "
    "Pode causar cólica forte e dor na relação. O tratamento é definido pelo médico e pode envolver remédios "
    "para dor, controle hormonal ou cirurgia. Quem costuma cuidar é o ginecologista. Se quiser, posso "
    "mostrar "
    "como marcar uma consulta pelo plano ou passar para a nossa equipe de saúde."
)
_BOA_SINTOMA = (
    "Sinto muito que a dor não tenha melhorado mesmo depois do que você já usou. Pelo que contou, vale ser "
    "avaliado por um clínico geral ainda hoje. Se a dor ficar súbita e muito forte, ou vier com boca "
    "torta ou "
    "fraqueza de um lado do corpo, ligue 192 ou vá ao pronto-socorro agora."
)


class _DmnReal:
    def __init__(self) -> None:
        self._tabelas = {chave: read_live_table(DMN_DIR / f"{chave}.dmn") for chave in _TABELAS}

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], DmnVersion]:
        if decision_key not in self._tabelas:
            raise DmnEvaluationError(f"tabela indisponivel: {decision_key}")
        tabela = self._tabelas[decision_key]
        veredito = evaluate(tabela, {nome: variables.get(nome) for nome in tabela.input_names})
        return [veredito.saidas], DmnVersion(
            key=decision_key, id=f"{decision_key}:1:offline", version=1, deployment_id="offline"
        )


class _Motor(FakeCibSevenTransport):
    def __init__(self) -> None:
        super().__init__()
        self.inicios: list[dict[str, Any]] = []

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.inicios.append(dict(variables))
        return await super().start_process_instance(process_key, business_key, variables)


class _Modelo:
    """O modelo da zona PHI (GLM-5 no dev): so' classifica, aqui."""

    def __init__(self) -> None:
        self.respostas: list[str] = []
        self.model_id = "fake-glm"

    async def generate(self, prompt: str, **_: Any) -> str:
        return self.respostas.pop(0) if self.respostas else ""


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


class _Transporte:
    """A rota global, FALSA: devolve os textos da fila (ou levanta a falha da fila)."""

    rota = ROTA_DE_RESIDENCIA
    modelo = MODELO_HAIKU_GLOBAL

    def __init__(self, *respostas: str | Exception) -> None:
        self.fila: list[str | Exception] = list(respostas)
        self.chamadas: list[tuple[str, list[dict[str, Any]]]] = []

    async def conversar(self, sistema: str, mensagens: Any, *, max_tokens: int) -> RespostaDaRotaGlobal:
        self.chamadas.append((sistema, [dict(m) for m in mensagens]))
        item = self.fila.pop(0) if self.fila else _BOA_ENDOMETRIOSE
        if isinstance(item, Exception):
            raise item
        return RespostaDaRotaGlobal(texto=item, parada="end_turn", entrada=1, saida=1, latencia_ms=1)

    def enviado(self) -> str:
        """Tudo o que saiu para a rota global (texto das mensagens), para conferir a minimizacao."""
        return "\n".join(
            str(c.get("text", "")) for _, msgs in self.chamadas for m in msgs for c in m["content"]
        )


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
_SINTOMA = _classify(intent="symptom", population="adult", intensidade="leve")
_DOR_NO_PEITO = _classify(
    intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
)
_RISCO = _classify(intent="symptom", population="adult", psychosocial_risk=True)
_PESSOA = _classify(intent="human_request")


class _Conversa:
    def __init__(
        self,
        transporte: _Transporte | None = None,
        *,
        conversa: str = _DONO,
        allowlist: tuple[str, ...] = (_DONO,),
        gerativa: bool = True,
    ) -> None:
        self.transporte = transporte or _Transporte()
        self.conversa = conversa
        self.redator = (
            sg.RedatorDeSaude(transporte=self.transporte, conversas=frozenset(allowlist))
            if gerativa
            else None
        )
        self.base = b.carregar_base(_FIXTURE)
        self.cib = _Motor()
        self.whatsapp = _WhatsApp()
        self.modelo = _Modelo()
        self.memoria: dict[str, Any] = {}
        self._n = 0

    def _grafo(self) -> Any:
        grafo = HelenaGraph(
            inference=self.modelo,  # type: ignore[arg-type]
            dmn=_DmnReal(),  # type: ignore[arg-type]
            cibseven=self.cib,
            audit_sink=FakeStartAuditSink(),
            whatsapp=self.whatsapp,
            historico_enabled=True,
            coleta_enabled=True,
            helena_v4_enabled=True,
            helena_base_saude_enabled=True,
            base_saude=self.base,
            saude_gerativa=self.redator,
            relogio=lambda: datetime(2026, 10, 17, 15, 0, tzinfo=UTC),
        )
        return grafo.compile_graph().compile()

    async def diz(self, mensagem: str, extracao: str = _INFO, *extras: str) -> dict[str, Any]:
        self._n += 1
        self.modelo.respostas.extend([extracao, *extras])
        estado: dict[str, Any] = dict(
            new_helena_state(
                tenant_id="amh",
                conversation_id=self.conversa,
                canal="whatsapp",
                beneficiario_pseudo_id="PSEUDO-GERATIVA",
                message_body=mensagem,
                message_ref=f"hk1_msg_gerativa_{self._n}",
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
# 1. Allowlist
# =================================================================================================


def test_a_allowlist_so_aceita_id_keyed() -> None:
    bruto = f"{_DONO}, {_OUTRA};5511999999999 wa:amh:hk1_curto *  wa:amh:{'c' * 64}"
    assert sg.conversas_autorizadas(bruto) == frozenset({_DONO, _OUTRA})
    assert sg.conversas_autorizadas("") == frozenset()
    assert sg.conversas_autorizadas(None) == frozenset()
    redator = sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_DONO}))
    assert redator.autorizada(_DONO) and not redator.autorizada(_OUTRA) and not redator.autorizada(None)
    assert redator.rota == "experimento_global_autorizado_dpo_2026-10-17"


_ROTEIRO_DE_FORA: tuple[tuple[str, str], ...] = (
    ("Pra que serve dipirona?", _CLINICA),
    ("O que é Endometriose? e como tratar?", _CLINICA),
    ("Meu PSA deu fora dos parâmetros, o que me sugere?", _CLINICA),
    ("Estou com dor de cabeça", _SINTOMA),
    ("Começou hoje de manhã, já tomei dorflex e buscopan e não melhorou", _SINTOMA),
    ("Não", _INFO),
    ("Estou com dor no peito e falta de ar", _DOR_NO_PEITO),
)


async def test_fora_da_allowlist_o_grafo_e_o_de_antes_texto_a_texto() -> None:
    sem_chave = _Conversa(gerativa=False, conversa=_OUTRA)
    com_chave_fora = _Conversa(conversa=_OUTRA, allowlist=(_DONO,))
    for mensagem, extracao in _ROTEIRO_DE_FORA:
        a = await sem_chave.diz(mensagem, extracao)
        c = await com_chave_fora.diz(mensagem, extracao)
        assert _texto(a) == _texto(c), mensagem
        assert a.get("next_kind") == c.get("next_kind"), mensagem
    assert sem_chave.whatsapp.enviados == com_chave_fora.whatsapp.enviados
    assert sem_chave.motivos == com_chave_fora.motivos
    assert com_chave_fora.transporte.chamadas == [], "fora da allowlist nada vai a rota global"


# =================================================================================================
# 2. Pergunta informativa na allowlist + minimizacao
# =================================================================================================


async def test_pergunta_informativa_e_redigida_pelo_modelo_sem_caso() -> None:
    conversa = _Conversa(_Transporte(_BOA_ENDOMETRIOSE))
    resultado = await conversa.diz("O que é Endometriose? e como tratar?", _CLINICA)

    assert _texto(resultado) == _BOA_ENDOMETRIOSE
    assert conversa.whatsapp.enviados == [_BOA_ENDOMETRIOSE]
    assert conversa.cib.inicios == [], "pergunta informativa nao abre caso"
    assert len(conversa.transporte.chamadas) == 1
    sistema, _ = conversa.transporte.chamadas[0]
    assert sistema == sg.PROMPT_SISTEMA
    assert "Pergunta geral de saúde" in conversa.transporte.enviado()


async def test_seguimento_curto_usa_a_pergunta_anterior_como_contexto() -> None:
    conversa = _Conversa(_Transporte(_BOA_ENDOMETRIOSE, "A osteoporose deixa os ossos mais frágeis. " * 2))
    await conversa.diz("O que é Endometriose? e como tratar?", _CLINICA)
    resultado = await conversa.diz("E Osteoporose?", _INFO)

    assert "osteoporose" in _texto(resultado).lower()
    assert len(conversa.transporte.chamadas) == 2
    _, mensagens = conversa.transporte.chamadas[1]
    enviado = mensagens[0]["content"][0]["text"]
    assert "[Mensagens anteriores da pessoa nesta conversa]" in enviado
    assert "Endometriose" in enviado
    assert _BOA_ENDOMETRIOSE not in enviado, "a fala da Helena nunca vai a rota global"
    assert conversa.cib.inicios == []


async def test_o_que_sai_para_a_rota_global_e_minimizado() -> None:
    conversa = _Conversa(_Transporte(_BOA_ENDOMETRIOSE))
    await conversa.diz(
        "Meu nome é Maria Souza, CPF 123.456.789-09, nasci em 12/03/1980, carteirinha 0012345678901, "
        "tel (11) 98765-4321, maria@exemplo.com. O que é endometriose?",
        _CLINICA,
    )
    enviado = conversa.transporte.enviado()
    for proibido in ("Maria", "Souza", "123.456.789-09", "12/03/1980", "0012345678901", "98765", "maria@"):
        assert proibido not in enviado, proibido
    assert "endometriose" in enviado


def test_minimizar_mantem_idade_e_valores_de_exame() -> None:
    texto = sg.minimizar("Tenho 45 anos, sou mulher e minha hemoglobina deu 10,5; glicose 130 em jejum")
    assert "45 anos" in texto and "10,5" in texto and "130" in texto and "mulher" in texto


def test_contexto_da_pessoa_descarta_o_fluxo_de_acesso() -> None:
    assert sg.contexto_da_pessoa(
        ["Aceito", "123.456.789-09", "12/03/1980", "Oi", "Estou com dor de cabeça"]
    ) == ("Estou com dor de cabeça",)


# =================================================================================================
# 3. Checagem: reescrita e resposta segura padrao
# =================================================================================================


async def test_texto_com_dose_e_reescrito_uma_vez() -> None:
    ruim = "A dipirona alivia dor e febre. Tome 500 mg a cada 6 horas."
    boa = (
        "A dipirona é usada para aliviar dor e baixar a febre. Quanto e como usar quem orienta é o "
        "médico ou o "
        "farmacêutico, e a bula traz as orientações. Se quiser, posso passar para a nossa equipe de saúde."
    )
    conversa = _Conversa(_Transporte(ruim, boa))
    resultado = await conversa.diz("Pra que serve a dipirona?", _CLINICA)

    assert _texto(resultado) == boa
    assert len(conversa.transporte.chamadas) == 2
    _, segunda = conversa.transporte.chamadas[1]
    assert "REPROVOU" in segunda[-1]["content"][0]["text"]


async def test_reprovou_duas_vezes_vira_a_resposta_segura_padrao() -> None:
    ruim = "Não dê remédio para febre ao bebê. Você tem uma virose."
    conversa = _Conversa(_Transporte(ruim, ruim))
    resultado = await conversa.diz("O que é endometriose?", _CLINICA)

    assert _texto(resultado) == sg.RESPOSTA_SEGURA_INFORMAR
    assert conversa.whatsapp.enviados == [sg.RESPOSTA_SEGURA_INFORMAR]
    assert conversa.cib.inicios == []


async def test_falha_do_modelo_vira_resposta_segura_sem_quebrar_o_turno() -> None:
    conversa = _Conversa(_Transporte(FalhaDaRotaGlobalError("prazo")))
    resultado = await conversa.diz("Meu PSA deu fora dos parâmetros, o que me sugere?", _CLINICA)

    assert _texto(resultado) == sg.RESPOSTA_SEGURA_RESULTADO
    assert conversa.whatsapp.enviados == [sg.RESPOSTA_SEGURA_RESULTADO], "nunca silencio"
    assert not resultado.get("error")


async def test_falha_na_reescrita_tambem_vira_resposta_segura() -> None:
    conversa = _Conversa(_Transporte("Tome 2 comprimidos.", FalhaDaRotaGlobalError("servico:X")))
    resultado = await conversa.diz("O que é endometriose?", _CLINICA)
    assert _texto(resultado) == sg.RESPOSTA_SEGURA_INFORMAR


async def test_prazo_total_estourado_vira_resposta_segura() -> None:
    import asyncio

    class _Lento(_Transporte):
        async def conversar(self, sistema: str, mensagens: Any, *, max_tokens: int) -> RespostaDaRotaGlobal:
            await asyncio.sleep(1)
            return await super().conversar(sistema, mensagens, max_tokens=max_tokens)

    redator = sg.RedatorDeSaude(transporte=_Lento(), conversas=frozenset({_DONO}), prazo_total_s=0.01)
    redacao = await redator.redigir(
        sg.Pedido(modo=sg.MODO_INFORMAR, mensagem="o que e x"), cercas=lambda t: []
    )
    assert (
        redacao is not None and redacao.desfecho == "falha" and redacao.texto == sg.RESPOSTA_SEGURA_INFORMAR
    )


@pytest.mark.parametrize(
    "texto",
    [
        sg.RESPOSTA_SEGURA_INFORMAR,
        sg.RESPOSTA_SEGURA_RESULTADO,
        sg.resposta_segura_de_sintoma("boca torta, fraqueza de um lado do corpo"),
        sg.opcoes_do_sintoma(False),
        sg.opcoes_do_sintoma(True),
    ],
)
def test_as_respostas_seguras_passam_em_todas_as_cercas(texto: str) -> None:
    assert HelenaGraph._cercas_da_helena(texto) == []
    assert b.termos_proibidos_no_texto(texto) == []
    modo = sg.MODO_SINTOMA_SEM_ALARME if "192 (SAMU)" in texto else sg.MODO_INFORMAR
    if "Você prefere" not in texto:
        assert (
            sg.verificar(texto, sg.Pedido(modo=modo, mensagem="x"), cercas=HelenaGraph._cercas_da_helena)
            == []
        )


# =================================================================================================
# 4. As regras corrigidas do experimento
# =================================================================================================


def _regras(texto: str, **campos: Any) -> list[str]:
    pedido = sg.Pedido(**{"modo": sg.MODO_INFORMAR, "mensagem": "x", **campos})
    return [v.regra for v in sg.verificar(texto, pedido, cercas=HelenaGraph._cercas_da_helena)]


def test_valor_de_exame_com_unidade_nao_e_dose() -> None:
    texto = (
        "A hemoglobina é a proteína que leva oxigênio no sangue. Em adultos, a faixa de referência "
        "costuma ficar "
        "entre 12 e 16 g/dL. A glicemia de jejum acima de 130 mg/dL pede avaliação. Leve o resultado ao "
        "médico."
    )
    assert _regras(texto, modo=sg.MODO_RESULTADO) == []
    assert "dose_numerica" in _regras("Use 500 mg de dipirona.")


def test_vacina_todo_ano_nao_e_conduta_mas_exame_a_cada_ano_e() -> None:
    assert _regras("A vacina da gripe é feita anualmente, porque o vírus muda.") == []
    assert "cerca:conduta_clinica" in _regras("Faça o exame de sangue anualmente.")


def test_fonte_por_prefixo() -> None:
    nome = "ANS — Rol de Procedimentos e Eventos em Saúde (atualização do Rol)"
    assert sg.fonte_confere("ANS — Rol de Procedimentos e Eventos em Saúde", nome)
    assert sg.fonte_confere(nome, nome)
    assert not sg.fonte_confere("ANS", nome)
    assert not sg.fonte_confere("Ministério da Saúde", nome)
    apoio = sg.Apoio(
        "x", "X", "exame", "bloco", "Saúde de A a Z — Gripe (influenza) (Ministério da Saúde)", "u"
    )
    assert "fonte_com_nome_errado" not in _regras("Texto curto.\nFonte: Saúde de A a Z — Gripe", apoio=apoio)
    assert "fonte_sem_apoio" in _regras("Texto curto.\nFonte: Ministério da Saúde")


@pytest.mark.parametrize(
    ("texto", "regra"),
    [
        ("Não dê remédio para febre ao bebê.", "nao_de_nao_use"),
        ("Não use antibiótico.", "nao_de_nao_use"),
        ("Pare de usar o remédio.", "manda_usar_ou_parar"),
        ("Continue usando o remédio.", "manda_usar_ou_parar"),
        ("Use o dobro amanhã.", "use_o_dobro"),
        ("Use assim que lembrar.", "dose_esquecida"),
        ("Você não está sozinho nessa.", "genero"),
        ("Procure um pediatra.", "pediatra_para_adulto"),
        ("Vou agendar sua consulta.", "promessa"),
    ],
)
def test_regras_novas(texto: str, regra: str) -> None:
    assert regra in _regras(texto)


def test_cuidado_geral_sem_orientacao_e_pediatra_de_crianca_passam() -> None:
    assert _regras("Por favor, não use nenhum remédio sem orientação do médico.") == []
    assert _regras("Quem costuma cuidar é o pediatra.", crianca=True) == []


def test_limite_de_palavras() -> None:
    assert "longa_demais" in _regras("palavra " * (sg.PALAVRAS_MAX + 1))


async def test_so_o_tamanho_passou_a_reescrita_sai_e_nao_a_resposta_segura() -> None:
    longa = "A endometriose é uma condição ginecológica comum. " * 20
    conversa = _Conversa(_Transporte(longa, longa))
    resultado = await conversa.diz("O que é endometriose?", _CLINICA)
    assert _texto(resultado) == longa.strip(), "tamanho e' estilo: nao vira resposta segura"
    gigante = "palavra " * (sg.PALAVRAS_TETO + 5)
    conversa2 = _Conversa(_Transporte(gigante, gigante))
    assert _texto(await conversa2.diz("O que é endometriose?", _CLINICA)) == sg.RESPOSTA_SEGURA_INFORMAR


def test_limpar_tira_markdown_e_acerta_a_fonte() -> None:
    assert sg.limpar("**Endometriose** é...\n# Título\nTexto.\nFonte: Inventada", None) == (
        "Endometriose é...\nTítulo\nTexto."
    )
    apoio = sg.Apoio("x", "X", "exame", "b", "Saúde de A a Z — Gripe (influenza) (Ministério da Saúde)", "u")
    assert sg.limpar("Texto.\nFonte: Ministério", apoio).endswith(
        "\nFonte: Saúde de A a Z — Gripe (influenza) (Ministério da Saúde)"
    )


def test_febre_comum_em_bebe_e_minimizar() -> None:
    assert "minimiza" in _regras("Febre de 38 em bebê é comum.", crianca=True)
    assert _regras("A osteoporose é mais comum depois da menopausa.") == []


def test_apoio_so_quando_o_assunto_casa_de_verdade() -> None:
    base = b.base_aplicavel(b.carregar_base(b.raiz_padrao()), hoje=datetime(2026, 10, 17).date())
    assert sg.apoio_para(base, "O que é Infecção urinária (cistite)?") is None, (
        "cistite nao vira manchas na pele"
    )
    colono = sg.apoio_para(base, "o que é colonoscopia? precisa de preparo?")
    assert colono is None, "o Rol da ANS nao e' explicacao"
    gripe = sg.apoio_para(base, "vacina da gripe tem que tomar todo ano?")
    assert gripe is not None and gripe.fonte_nome is not None and "Saúde de A a Z" in gripe.fonte_nome


def test_abertura_repetida_sai_uma_vez() -> None:
    assert sg.sem_abertura_repetida("Entendi. Entendi: dor de cabeça.") == "Entendi: dor de cabeça."
    assert sg.sem_abertura_repetida("Entendi. Certo.") == "Entendi. Certo."


# =================================================================================================
# 5. O que NUNCA vai ao modelo
# =================================================================================================


async def test_alarme_escala_com_192_e_nao_vai_ao_modelo() -> None:
    conversa = _Conversa()
    resultado = await conversa.diz("Estou com dor no peito e falta de ar", _DOR_NO_PEITO)

    assert conversa.motivos == ["red_flag_clinico"]
    assert "192" in _texto(resultado)
    assert conversa.transporte.chamadas == []


async def test_risco_psicossocial_nao_vai_ao_modelo() -> None:
    conversa = _Conversa()
    await conversa.diz("Não aguento mais viver, o que é depressão?", _RISCO)
    assert conversa.motivos == ["risco_psicossocial"]
    assert conversa.transporte.chamadas == []


async def test_redator_recusa_risco_mesmo_se_chamado() -> None:
    redator = sg.RedatorDeSaude(transporte=_Transporte(), conversas=frozenset({_DONO}))
    pedido = sg.Pedido(modo=sg.MODO_INFORMAR, mensagem="quero morrer, o que e depressao")
    assert await redator.redigir(pedido, cercas=lambda t: []) is None
    assert redator.transporte.chamadas == []  # type: ignore[attr-defined]


async def test_pedido_de_pessoa_nao_vai_ao_modelo() -> None:
    conversa = _Conversa()
    await conversa.diz("Quero falar com uma pessoa", _PESSOA)
    assert conversa.motivos == ["solicitacao_humano"]
    assert conversa.transporte.chamadas == []


# =================================================================================================
# 6. Queixa: alerta curto, contexto, "nao" e "sim"
# =================================================================================================


async def test_queixa_recebe_alerta_curto_e_contexto_recebe_orientacao_com_opcoes() -> None:
    conversa = _Conversa(_Transporte(_BOA_SINTOMA))
    primeira = await conversa.diz("Estou com dor de cabeça", _SINTOMA)
    pergunta = _texto(primeira)
    assert pergunta.startswith("Antes de te orientar, uma pergunta rápida (responda sim ou não):")
    assert "Entendi:" not in pergunta and "grávida" not in pergunta
    assert conversa.transporte.chamadas == [], "a pergunta de alerta e' texto fixo"

    segunda = await conversa.diz(
        "Começou hoje de manhã, já tomei dorflex e buscopan e não melhorou", _SINTOMA
    )
    texto = _texto(segunda)
    assert texto.startswith(_BOA_SINTOMA)
    assert texto.endswith(sg.opcoes_do_sintoma(False))
    assert "pediatra" not in texto
    assert "Só preciso de um sim ou não" not in texto, "contexto nao repete a pergunta"
    assert conversa.cib.inicios == []
    assert str(segunda.get("coleta_pendente") or "").startswith("V4_ESCOLHA:")
    enviado = conversa.transporte.enviado()
    assert "dorflex" in enviado and "ainda NÃO respondeu" in enviado

    terceira = await conversa.diz("2", _INFO)
    assert conversa.motivos == ["intencao_clinica"], "a opcao 2 da v4 continua abrindo o caso"
    assert terceira.get("next_kind") == "escalate"


async def test_nao_as_perguntas_de_alerta_recebe_orientacao_redigida() -> None:
    conversa = _Conversa(_Transporte(_BOA_SINTOMA))
    await conversa.diz("Estou com dor de cabeça", _SINTOMA)
    resultado = await conversa.diz("Não", _INFO)
    assert _texto(resultado) == f"{_BOA_SINTOMA}\n{sg.opcoes_do_sintoma(False)}"
    assert "respondeu NÃO às perguntas de sinal de alarme" in conversa.transporte.enviado()


async def test_sim_as_perguntas_de_alerta_escala_sem_modelo() -> None:
    conversa = _Conversa()
    await conversa.diz("Estou com dor de cabeça", _SINTOMA)
    await conversa.diz("Sim", _INFO)
    assert conversa.motivos == ["red_flag_clinico"]
    assert conversa.transporte.chamadas == []


async def test_sintoma_sem_192_e_reescrito() -> None:
    sem_192 = "Sinto muito pela dor. Vale procurar um clínico geral."
    conversa = _Conversa(_Transporte(sem_192, _BOA_SINTOMA))
    await conversa.diz("Estou com dor de cabeça", _SINTOMA)
    resultado = await conversa.diz("Não", _INFO)
    assert _texto(resultado).startswith(_BOA_SINTOMA)
    assert len(conversa.transporte.chamadas) == 2


# =================================================================================================
# 7. O transporte da rota global
# =================================================================================================


class _Cliente:
    def __init__(self, resposta: Any = None, erro: Exception | None = None) -> None:
        self.resposta = resposta
        self.erro = erro
        self.pedidos: list[dict[str, Any]] = []

    def converse(self, **pedido: Any) -> Any:
        self.pedidos.append(pedido)
        if self.erro is not None:
            raise self.erro
        return self.resposta


async def test_transporte_global_manda_sem_temperatura_e_com_esforco_baixo() -> None:
    cliente = _Cliente(
        {
            "output": {"message": {"content": [{"reasoningContent": {}}, {"text": " Olá "}]}},
            "usage": {"inputTokens": 10, "outputTokens": 3},
            "stopReason": "end_turn",
        }
    )
    transporte = TransporteGlobalExperimental(client=cliente)
    resposta = await transporte.conversar(
        "sis", [{"role": "user", "content": [{"text": "x"}]}], max_tokens=50
    )

    assert resposta.texto == "Olá" and resposta.entrada == 10
    pedido = cliente.pedidos[0]
    assert pedido["modelId"] == "global.anthropic.claude-haiku-5-5"
    assert pedido["inferenceConfig"] == {"maxTokens": 50}, "os Claude 5.x recusam temperature"
    assert pedido["additionalModelRequestFields"] == {"output_config": {"effort": "low"}}
    assert transporte.rota == "experimento_global_autorizado_dpo_2026-10-17"


async def test_transporte_global_recusa_outro_modelo_e_traduz_falhas() -> None:
    with pytest.raises(ValueError):
        TransporteGlobalExperimental(modelo="global.anthropic.claude-opus-5", client=_Cliente())
    falha = TransporteGlobalExperimental(client=_Cliente(erro=OSError("rede")))
    with pytest.raises(FalhaDaRotaGlobalError) as exc:
        await falha.conversar("s", [], max_tokens=10)
    assert exc.value.codigo == "servico:OSError"
    vazio = TransporteGlobalExperimental(client=_Cliente({"output": {"message": {"content": []}}}))
    with pytest.raises(FalhaDaRotaGlobalError):
        await vazio.conversar("s", [], max_tokens=10)


# =================================================================================================
# 8. A raiz de composicao
# =================================================================================================


def test_composicao_so_constroi_o_redator_com_a_chave_e_a_base() -> None:
    from maezo.platform.webhooks.service import _saude_gerativa

    class _S:
        def __init__(self, gerativa: bool, base: bool, conversas: str = "") -> None:
            self.helena_saude_gerativa = gerativa
            self.helena_base_saude = base
            self.helena_saude_gerativa_conversas = conversas

    assert _saude_gerativa(_S(False, True)) is None  # type: ignore[arg-type]
    assert _saude_gerativa(_S(True, False)) is None  # type: ignore[arg-type]
    redator = _saude_gerativa(_S(True, True, f"{_DONO},lixo"))  # type: ignore[arg-type]
    assert isinstance(redator, sg.RedatorDeSaude)
    assert redator.conversas == frozenset({_DONO})


def test_settings_leem_a_chave_e_a_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    campos = WhatsAppWebhookSettings.model_fields
    assert campos["helena_saude_gerativa"].default is False
    assert campos["helena_saude_gerativa_conversas"].default == ""
