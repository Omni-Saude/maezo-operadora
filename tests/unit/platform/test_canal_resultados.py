"""A pagina de resultados do Canal de Teste: cerca, conversao igual a do receptor, so' leitura.

Tres promessas, e cada uma tem o seu teste:

1. **A conversao e' a do receptor.** `resultados.conversation_id` e' comparada com a funcao REAL
   `hash_phone` do receptor, com o MESMO `Pseudonymizer.from_settings` — nao com uma copia do HMAC
   escrita aqui. Se o receptor mudar a derivacao, este arquivo reprova.
2. **Numero fora da lista nunca aparece.** O motor falso daqui IGNORA o filtro de chave e devolve
   todo mundo — e o caso do numero nao declarado continua sem aparecer, na lista e no detalhe.
3. **Nenhum metodo de escrita.** Todo pedido ao motor e' GET, so' nos sete caminhos; a pagina nao
   chama `/engine`, `/receptor` nem `/agente`.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.testchannel import resultados
from maezo.platform.webhooks.whatsapp.security import hash_phone

CHAVE = "chave-de-teste-nao-e-a-do-vault"
TENANT = "amh"
DECLARADO = "5511987654321"
NAO_DECLARADO = "5511976543210"
PASTA = Path(resultados.__file__).parent


def _ambiente(**extra: str) -> dict[str, str]:
    base = {
        resultados.ALLOWLIST_ENV: f"+55 (11) 98765-4321, {DECLARADO}",
        "PHI_HMAC_KEY": CHAVE,
        "TENANT_ID": TENANT,
        "RUNTIME_MODE": "aws",
    }
    base.update(extra)
    return base


def _cerca() -> resultados.Cerca:
    cerca, motivo = resultados.construir_cerca(_ambiente())
    assert cerca is not None, motivo
    return cerca


def _cid_do_receptor(numero: str, tenant: str = TENANT) -> str:
    """Exatamente o que `HelenaDispatcher` faz: `wa:{tenant}:{hash_phone(...)}`."""
    pseudo = Pseudonymizer.from_settings(phi_hmac_key=CHAVE, production=True, tenant_id=tenant)
    return f"wa:{tenant}:{hash_phone(numero, tenant, pseudo)}"


# --------------------------------------------------------------------- 1. conversao do receptor


@pytest.mark.parametrize("numero", [DECLARADO, "5511900000077", "551187654321", "12025550123"])
@pytest.mark.parametrize("tenant", ["amh", "outro"])
def test_conversao_e_a_mesma_funcao_do_receptor(numero: str, tenant: str) -> None:
    pseudo = Pseudonymizer.from_settings(phi_hmac_key=CHAVE, production=True, tenant_id=tenant)
    assert resultados.conversation_id(numero, tenant, pseudo) == _cid_do_receptor(numero, tenant)


def test_cerca_do_ambiente_contem_a_conversa_que_o_receptor_gravaria() -> None:
    cerca = _cerca()
    assert _cid_do_receptor(DECLARADO) in cerca.conversas
    # a forma sem o nono digito, como a Meta entrega numeros antigos
    assert _cid_do_receptor("551187654321") in cerca.conversas
    assert _cid_do_receptor(NAO_DECLARADO) not in cerca.conversas
    assert cerca.numeros_declarados == 1  # "+55 (11) 98765-4321" e o mesmo numero, sem repeticao
    assert f"ESC-{TENANT}-{_cid_do_receptor(DECLARADO)}" in cerca.chaves


def test_nono_digito_nunca_vira_numero_de_outra_pessoa() -> None:
    # 9 seguido de 3 -> tirar o 9 daria um FIXO de outra pessoa: nao gera a forma curta
    assert resultados.formas_do_numero("5511932345678") == ("5511932345678",)
    # 12 digitos nunca ganha um 9 inventado
    assert resultados.formas_do_numero("551187654321") == ("551187654321",)
    assert resultados.formas_do_numero("5511987654321") == ("5511987654321", "551187654321")


def test_sem_chave_fora_de_local_a_cerca_recusa_em_vez_de_usar_chave_de_dev() -> None:
    amb = _ambiente()
    del amb["PHI_HMAC_KEY"]
    del amb["RUNTIME_MODE"]  # ausente = nao e' local = producao
    cerca, motivo = resultados.construir_cerca(amb)
    assert cerca is None
    assert "PHI_HMAC_KEY" in motivo


def test_lista_vazia_nao_lista_nada_e_nao_ecoa_entrada_invalida() -> None:
    cerca, motivo = resultados.construir_cerca(_ambiente(**{resultados.ALLOWLIST_ENV: "abc 12"}))
    assert cerca is None
    assert "2 entrada(s) invalida(s)" in motivo
    assert "abc" not in motivo


# --------------------------------------------------------------------- 2. a cerca


class _MotorFalso:
    """Motor que IGNORA o filtro de chave: devolve os dois processos para qualquer consulta."""

    def __init__(self, cerca: resultados.Cerca) -> None:
        self.bk_ok = cerca.chave(_cid_do_receptor(DECLARADO))
        self.bk_fora = f"ESC-{TENANT}-{_cid_do_receptor(NAO_DECLARADO)}"
        self.procs = [
            self._proc("pid-ok", self.bk_ok),
            self._proc("pid-fora", self.bk_fora),
        ]
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def _proc(pid: str, bk: str) -> dict[str, Any]:
        return {
            "id": pid,
            "businessKey": bk,
            "processDefinitionKey": resultados.PROCESSO,
            "startTime": "2026-09-27T12:00:00.000+0000",
            "endTime": "2026-09-27T12:20:00.000+0000",
            "state": "COMPLETED",
        }

    def ler(self, caminho: str, **params: Any) -> Any:
        assert caminho in resultados.LEITURAS_PERMITIDAS
        self.chamadas.append((caminho, params))
        if caminho == "/history/process-instance":
            if "processInstanceId" in params:
                return [p for p in self.procs if p["id"] == params["processInstanceId"]]
            return list(self.procs)
        if caminho == "/history/variable-instance":
            return [
                {"name": "resultado", "value": "resolvido_humano"},
                {"name": "notas_resolucao", "value": "liguei e orientei"},
                {"name": "resumo_contexto", "value": "dor no peito forte"},
                {"name": "dmn_decision_ref", "value": "triage_redflag_adult#def-1"},
                {"name": "canal", "value": "whatsapp"},
            ]
        if caminho == "/history/decision-instance":
            if params.get("processInstanceId"):
                return [
                    {
                        "decisionDefinitionKey": "escalation_routing",
                        "inputs": [{"clauseName": "motivo_categoria", "value": "red_flag_clinico"}],
                        "outputs": [
                            {"variableName": "prioridade", "value": "P1"},
                            {"variableName": "grupo_atendimento", "value": "plantao-clinico"},
                            {"variableName": "sla_ack", "value": "PT5M"},
                            {"variableName": "sla_resolucao", "value": "PT30M"},
                        ],
                    }
                ]
            return [
                {
                    "decisionDefinitionKey": "triage_redflag_adult",
                    "evaluationTime": "2026-09-27T11:59:50.000+0000",
                    "inputs": [
                        {"clauseName": "Sintoma normalizado (sintoma_codigo)", "value": "dor_toracica"}
                    ],
                    "outputs": [{"variableName": "red_flag", "value": True, "ruleId": "r1"}],
                }
            ]
        if caminho == "/history/task":
            return [
                {
                    "taskDefinitionKey": "UT_TratarEscalonamento",
                    "name": "Assumir e tratar escalonamento",
                    "assignee": "enf.ana",
                    "startTime": "2026-09-27T12:00:05.000+0000",
                    "endTime": "2026-09-27T12:04:05.000+0000",
                }
            ]
        if caminho == "/history/job-log":
            return [
                {
                    "jobId": "j1",
                    "activityId": "BT_SlaAck",
                    "jobDefinitionType": "timer",
                    "jobDueDate": "2026-09-27T12:05:05.000+0000",
                    "creationLog": True,
                },
                {
                    "jobId": "j1",
                    "activityId": "BT_SlaAck",
                    "jobDefinitionType": "timer",
                    "deletionLog": True,
                    "timestamp": "2026-09-27T12:04:05.000+0000",
                },
            ]
        if caminho == "/job":
            return []
        if caminho == "/history/activity-instance":
            return [
                {
                    "activityId": "UT_TratarEscalonamento",
                    "activityName": "Assumir e tratar",
                    "activityType": "userTask",
                    "startTime": "2026-09-27T12:00:05.000+0000",
                    "durationInMillis": 240000,
                },
                {
                    "activityId": "End_ResolvidoPorHumano",
                    "activityName": "Resolvido por humano",
                    "activityType": "noneEndEvent",
                    "startTime": "2026-09-27T12:04:06.000+0000",
                    "endTime": "2026-09-27T12:04:06.000+0000",
                    "durationInMillis": 0,
                },
            ]
        raise AssertionError(caminho)


def test_lista_so_tem_numero_declarado_mesmo_com_motor_que_ignora_filtro() -> None:
    cerca = _cerca()
    motor = _MotorFalso(cerca)
    saida = resultados.listar_casos(motor, cerca, None, None)  # type: ignore[arg-type]
    assert [c["id"] for c in saida["casos"]] == ["pid-ok"]
    pedidas = {
        p.get("processInstanceBusinessKey") for c, p in motor.chamadas if c == "/history/process-instance"
    }
    assert pedidas <= cerca.chaves  # nunca perguntou pela chave de ninguem fora da lista
    assert motor.bk_fora not in pedidas
    assert saida["casos"][0]["prioridade"] == "P1"
    assert saida["casos"][0]["tempo_ate_concluir"] == "4 min 00 s"


def test_meu_numero_fora_da_lista_devolve_vazio_sem_consultar_o_motor() -> None:
    cerca = _cerca()
    motor = _MotorFalso(cerca)
    saida = resultados.listar_casos(motor, cerca, None, None, numero="+55 11 97654-3210")  # type: ignore[arg-type]
    assert saida["casos"] == []
    assert not [c for c, _ in motor.chamadas if c == "/history/process-instance"]


def test_meu_numero_declarado_estreita_para_o_proprio_caso() -> None:
    cerca = _cerca()
    motor = _MotorFalso(cerca)
    saida = resultados.listar_casos(motor, cerca, None, None, numero="+55 11 98765-4321")  # type: ignore[arg-type]
    assert [c["id"] for c in saida["casos"]] == ["pid-ok"]


def test_detalhe_de_caso_fora_da_lista_e_none() -> None:
    cerca = _cerca()
    motor = _MotorFalso(cerca)
    assert resultados.detalhe_do_caso(motor, cerca, "pid-fora") is None  # type: ignore[arg-type]
    assert resultados.detalhe_do_caso(motor, cerca, "nao-existe") is None  # type: ignore[arg-type]


def test_detalhe_traz_os_oito_blocos_e_o_markdown() -> None:
    cerca = _cerca()
    caso = resultados.detalhe_do_caso(_MotorFalso(cerca), cerca, "pid-ok")  # type: ignore[arg-type]
    assert caso is not None
    assert set(caso["blocos"]) == {
        "conversa",
        "leitura",
        "veredito",
        "roteamento",
        "relogios",
        "acao_humana",
        "retorno",
        "rastro",
    }
    md = caso["markdown"]
    for titulo in (
        "A conversa",
        "A leitura",
        "O veredito",
        "O roteamento",
        "Os relógios",
        "A ação humana",
        "O retorno",
        "O rastro",
    ):
        assert titulo in md
    assert "dor_toracica" in md and "plantao-clinico" in md and "liguei e orientei" in md
    assert "desarmado sem disparar" in md
    assert caso["fim_alcancado"] == ["Resolvido por humano"]


# --------------------------------------------------------------------- 3. so' leitura


class _Resposta:
    def __init__(self, corpo: bytes) -> None:
        self._corpo = corpo

    def read(self) -> bytes:
        return self._corpo

    def __enter__(self) -> _Resposta:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def test_leitor_so_faz_get_e_so_nos_sete_caminhos() -> None:
    pedidos: list[Any] = []

    def abrir(req: Any, timeout: int) -> _Resposta:
        pedidos.append(req)
        return _Resposta(b"[]")

    leitor = resultados.LeitorDoMotor("http://motor/engine-rest", abrir=abrir)
    for caminho in sorted(resultados.LEITURAS_PERMITIDAS):
        leitor.ler(caminho, processInstanceId="x")
    assert {r.get_method() for r in pedidos} == {"GET"}
    assert all(r.data is None for r in pedidos)
    for proibido in (
        "/task/x/complete",
        "/process-instance",
        "/job-definition",
        "/message",
        "/history/task/x",
    ):
        with pytest.raises(resultados.LeituraRecusadaError):
            leitor.ler(proibido)
    assert len(pedidos) == len(resultados.LEITURAS_PERMITIDAS)  # recusa antes de qualquer rede


def test_modulo_nao_constroi_request_que_nao_seja_get() -> None:
    arvore = ast.parse((PASTA / "resultados.py").read_text(encoding="utf-8"))
    requests = [
        n
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) == "Request"
    ]
    assert requests, "o leitor deveria construir um Request"
    for chamada in requests:
        metodos = [k.value for k in chamada.keywords if k.arg == "method"]
        assert metodos and all(isinstance(m, ast.Constant) and m.value == "GET" for m in metodos)
        assert not [k for k in chamada.keywords if k.arg == "data"]


def test_pagina_nao_chama_motor_receptor_nem_agente() -> None:
    html = (PASTA / "paginas" / "resultados.html").read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1]
    for proibido in ("/engine", "/receptor", "/agente", "/complete", "localStorage", "sessionStorage"):
        assert proibido not in script, proibido
    alvos = set(re.findall(r'fetch\("([^"?]+)', script))
    assert alvos == {"/resultados/api/casos", "/resultados/api/caso"}


# --------------------------------------------------------------------- servidor


class _FalsoRfile:
    def __init__(self, dados: bytes) -> None:
        self._dados = dados

    def read(self, n: int) -> bytes:
        return self._dados[:n]


class _Pedido:
    def __init__(self, path: str, corpo: dict[str, Any] | None = None) -> None:
        dados = json.dumps(corpo or {}).encode()
        self.rfile = _FalsoRfile(dados)
        self.headers = {"Content-Length": str(len(dados))}
        self.path = path
        self.status: int | None = None
        self.corpo: dict[str, Any] | None = None

    def _responder_json(self, status: int, corpo: dict[str, Any]) -> None:
        self.status = status
        self.corpo = corpo


@pytest.fixture
def servidor(monkeypatch: pytest.MonkeyPatch) -> Any:
    for k, v in _ambiente().items():
        monkeypatch.setenv(k, v)
    from maezo.platform.testchannel import server

    mod = importlib.reload(server)
    motor = _MotorFalso(mod.RESULTADOS_CERCA)
    monkeypatch.setattr(mod.resultados, "LeitorDoMotor", lambda base: motor)
    return mod


def test_servidor_lista_e_nunca_ecoa_o_numero(servidor: Any) -> None:
    pedido = _Pedido("/resultados/api/casos", {"numero": "+55 11 98765-4321"})
    servidor.Handler._resultados_casos(pedido)
    assert pedido.status == 200
    texto = json.dumps(pedido.corpo)
    assert "pid-ok" in texto and "pid-fora" not in texto
    for numero in (DECLARADO, "551187654321", "98765-4321", "+55 11", NAO_DECLARADO):
        assert numero not in texto


def test_servidor_detalhe_fora_da_lista_e_404_igual_a_inexistente(servidor: Any) -> None:
    fora, nada = _Pedido("/resultados/api/caso?id=pid-fora"), _Pedido("/resultados/api/caso?id=zzz")
    servidor.Handler._resultados_caso(fora)
    servidor.Handler._resultados_caso(nada)
    assert fora.status == nada.status == 404
    assert fora.corpo == nada.corpo


def test_servidor_detalhe_do_caso_declarado(servidor: Any) -> None:
    pedido = _Pedido("/resultados/api/caso?id=pid-ok")
    servidor.Handler._resultados_caso(pedido)
    assert pedido.status == 200
    assert pedido.corpo is not None and "markdown" in pedido.corpo
    assert DECLARADO not in json.dumps(pedido.corpo)


def test_servidor_sem_lista_responde_503_sem_consultar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(resultados.ALLOWLIST_ENV, raising=False)
    from maezo.platform.testchannel import server

    mod = importlib.reload(server)

    def explode(base: str) -> Any:
        raise AssertionError("nao deveria consultar o motor")

    monkeypatch.setattr(mod.resultados, "LeitorDoMotor", explode)
    pedido = _Pedido("/resultados/api/casos", {})
    mod.Handler._resultados_casos(pedido)
    assert pedido.status == 503
