"""O Canal de Teste NAO conclui tarefa humana pelo motor (pendencia 48, DL-0153).

O proxy `/engine/*` repassava QUALQUER caminho ao `engine-rest`, sem autenticacao propria: a pagina
de escalonamento concluia a tarefa humana direto no motor, por fora do portal (que autentica, audita
e confere o grupo). Agora, sem `CANAL_PERMITE_CONCLUIR_PELO_MOTOR=1`:

  * `POST /engine/task/{id}/(complete|claim|resolve|submit-form|unclaim|assignee)` -> 403, nada chega
    ao motor — inclusive nas grafias que o motor normalizaria para o mesmo recurso;
  * `DELETE` em qualquer caminho -> 403;
  * EXCECAO da pagina de autorizacao: `complete` de tarefa de SP-OP-AUTH-001, com o processo LIDO NO
    MOTOR, passa; outra acao, outro processo ou leitura que falha -> 403;
  * `GET` e os demais `POST` (iniciar processo, executar job, external task) passam como antes.

Com a chave ligada, tudo passa como antes. Servidor REAL do canal e um motor FALSO, ambos em socket
local (porta efemera), para medir o que de fato chega ao motor.
"""

from __future__ import annotations

import importlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

TAREFA_ESCALONAMENTO = "tarefa-esc-1"
TAREFA_AUTORIZACAO = "tarefa-auth-1"
TAREFA_SUMIDA = "tarefa-que-nao-existe"
DEFINICOES = {
    TAREFA_ESCALONAMENTO: "SP-OP-ESCALATION-001:7:def-esc",
    TAREFA_AUTORIZACAO: "SP-OP-AUTH-001:3:def-auth",
}
CHAVES = {
    "SP-OP-ESCALATION-001:7:def-esc": "SP-OP-ESCALATION-001",
    "SP-OP-AUTH-001:3:def-auth": "SP-OP-AUTH-001",
}


class _MotorFalso(BaseHTTPRequestHandler):
    pedidos: list[tuple[str, str]] = []

    def _json(self, status: int, corpo: Any) -> None:
        dados = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self) -> None:  # noqa: N802
        self.pedidos.append(("GET", self.path))
        caminho = self.path.split("?")[0]
        if caminho.startswith("/engine-rest/task/"):
            tarefa = caminho.rsplit("/", 1)[-1]
            if tarefa in DEFINICOES:
                self._json(200, {"id": tarefa, "processDefinitionId": DEFINICOES[tarefa]})
            else:
                self._json(404, {"type": "InvalidRequestException"})
            return
        if caminho.startswith("/engine-rest/process-definition/"):
            definicao = urllib.request.unquote(caminho.rsplit("/", 1)[-1])
            self._json(200, {"id": definicao, "key": CHAVES.get(definicao, "")})
            return
        self._json(200, [])

    def _sem_corpo(self) -> None:
        tamanho = int(self.headers.get("Content-Length", 0) or 0)
        if tamanho:
            self.rfile.read(tamanho)
        self.pedidos.append((self.command, self.path))
        self.send_response(204)
        self.end_headers()

    do_POST = _sem_corpo  # noqa: N815
    do_DELETE = _sem_corpo  # noqa: N815

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def _subir(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


@pytest.fixture
def canal(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, str]]:
    modulo = importlib.import_module("maezo.platform.testchannel.server")
    _MotorFalso.pedidos = []
    motor = _subir(_MotorFalso)
    monkeypatch.setattr(modulo, "ENGINE", f"http://127.0.0.1:{motor.server_address[1]}/engine-rest")
    monkeypatch.setattr(modulo, "PERMITE_CONCLUIR_PELO_MOTOR", False)
    servidor = _subir(modulo.Handler)
    try:
        yield modulo, f"http://127.0.0.1:{servidor.server_address[1]}"
    finally:
        servidor.shutdown()
        motor.shutdown()


def _pedir(url: str, metodo: str, corpo: bytes | None = None) -> tuple[int, bytes]:
    pedido = urllib.request.Request(
        url, data=corpo, method=metodo, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(pedido, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _escritas_no_motor() -> list[tuple[str, str]]:
    return [p for p in _MotorFalso.pedidos if p[0] != "GET"]


_COMPLETE = json.dumps({"variables": {"resultado": {"value": "resolvido_humano", "type": "String"}}}).encode()


#: Todo `POST /task/{id}/<sub-recurso>` do engine-rest muda a tarefa: a recusa e' por padrao, nao lista
#: fechada (revisao do #869: `delegate` passava pela lista antiga).
_SUB_RECURSOS_DE_TAREFA = [
    "complete",
    "claim",
    "resolve",
    "submit-form",
    "unclaim",
    "assignee",
    "delegate",
    "identity-links",
    "identity-links/delete",
    "variables",
    "localVariables",
    "bpmnError",
    "bpmnEscalation",
    "comment/create",
    "attachment/create",
]


@pytest.mark.parametrize("acao", _SUB_RECURSOS_DE_TAREFA)
def test_sem_a_chave_acao_de_tarefa_humana_e_recusada_e_nada_chega_ao_motor(
    canal: tuple[Any, str], acao: str
) -> None:
    _, base = canal
    status, corpo = _pedir(f"{base}/engine/task/{TAREFA_ESCALONAMENTO}/{acao}", "POST", _COMPLETE)

    assert status == 403
    resposta = json.loads(corpo)
    assert resposta["orientacao"] == "Concluir pelo portal"
    assert resposta["chave"] == "CANAL_PERMITE_CONCLUIR_PELO_MOTOR"
    assert _escritas_no_motor() == []


@pytest.mark.parametrize(
    "caminho",
    [
        f"/engine/task/{TAREFA_ESCALONAMENTO}/%63omplete",
        f"/engine/task/{TAREFA_ESCALONAMENTO}/%2563omplete",
        f"/engine/task/{TAREFA_ESCALONAMENTO}/x/../complete",
        f"/engine/default/task/{TAREFA_ESCALONAMENTO}/complete",
        f"/engine//task/{TAREFA_ESCALONAMENTO}/complete",
        f"/engine/task/{TAREFA_ESCALONAMENTO}/complete;x=1",
        f"/engine/task/{TAREFA_ESCALONAMENTO}/complete/",
        f"/engine/task/{TAREFA_ESCALONAMENTO}/complete?x=1",
        f"/engine/task/{TAREFA_ESCALONAMENTO}/COMPLETE",
    ],
)
def test_grafias_que_o_motor_normalizaria_tambem_sao_recusadas(canal: tuple[Any, str], caminho: str) -> None:
    _, base = canal
    status, _ = _pedir(base + caminho, "POST", _COMPLETE)

    assert status == 403
    assert _escritas_no_motor() == []


@pytest.mark.parametrize(
    "caminho", ["/engine/process-instance/pid-1", "/engine/task/x", "/engine/deployment/d-1?cascade=true"]
)
def test_sem_a_chave_delete_e_recusado_em_qualquer_caminho(canal: tuple[Any, str], caminho: str) -> None:
    _, base = canal
    status, _ = _pedir(base + caminho, "DELETE")

    assert status == 403
    assert _escritas_no_motor() == []


def test_com_a_chave_tudo_passa_como_antes(canal: tuple[Any, str], monkeypatch: pytest.MonkeyPatch) -> None:
    modulo, base = canal
    monkeypatch.setattr(modulo, "PERMITE_CONCLUIR_PELO_MOTOR", True)

    assert _pedir(f"{base}/engine/task/{TAREFA_ESCALONAMENTO}/complete", "POST", _COMPLETE)[0] == 204
    assert _pedir(f"{base}/engine/task/{TAREFA_ESCALONAMENTO}/claim", "POST", b"{}")[0] == 204
    assert _pedir(f"{base}/engine/process-instance/pid-1", "DELETE")[0] == 204
    assert _escritas_no_motor() == [
        ("POST", f"/engine-rest/task/{TAREFA_ESCALONAMENTO}/complete"),
        ("POST", f"/engine-rest/task/{TAREFA_ESCALONAMENTO}/claim"),
        ("DELETE", "/engine-rest/process-instance/pid-1"),
    ]


def test_excecao_da_autorizacao_conclui_tarefa_de_sp_op_auth_001(canal: tuple[Any, str]) -> None:
    _, base = canal
    status, _ = _pedir(f"{base}/engine/task/{TAREFA_AUTORIZACAO}/complete", "POST", _COMPLETE)

    assert status == 204
    assert _escritas_no_motor() == [("POST", f"/engine-rest/task/{TAREFA_AUTORIZACAO}/complete")]
    # o processo foi LIDO no motor, nao tirado do pedido
    assert ("GET", f"/engine-rest/task/{TAREFA_AUTORIZACAO}") in _MotorFalso.pedidos


@pytest.mark.parametrize("acao", [a for a in _SUB_RECURSOS_DE_TAREFA if a != "complete"])
def test_excecao_da_autorizacao_vale_so_para_complete(canal: tuple[Any, str], acao: str) -> None:
    _, base = canal
    status, _ = _pedir(f"{base}/engine/task/{TAREFA_AUTORIZACAO}/{acao}", "POST", b"{}")

    assert status == 403
    assert _escritas_no_motor() == []


def test_tarefa_que_o_motor_nao_acha_e_recusada(canal: tuple[Any, str]) -> None:
    _, base = canal
    status, _ = _pedir(f"{base}/engine/task/{TAREFA_SUMIDA}/complete", "POST", _COMPLETE)

    assert status == 403
    assert _escritas_no_motor() == []


def test_motor_fora_do_ar_na_leitura_da_excecao_recusa(
    canal: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    modulo, base = canal
    monkeypatch.setattr(modulo, "ENGINE", "http://127.0.0.1:9/engine-rest")
    status, _ = _pedir(f"{base}/engine/task/{TAREFA_AUTORIZACAO}/complete", "POST", _COMPLETE)

    assert status == 403


@pytest.mark.parametrize(
    "caminho",
    [
        "/engine/task?processInstanceId=pid-1",
        f"/engine/task/{TAREFA_ESCALONAMENTO}",
        "/engine/history/process-instance?processDefinitionKey=SP-OP-ESCALATION-001",
    ],
)
def test_get_continua_passando(canal: tuple[Any, str], caminho: str) -> None:
    _, base = canal
    status, _ = _pedir(base + caminho, "GET")

    assert status == 200
    assert ("GET", caminho.replace("/engine", "/engine-rest", 1)) in _MotorFalso.pedidos


@pytest.mark.parametrize(
    "caminho",
    [
        "/engine/process-definition/key/SP-OP-ESCALATION-001/start",
        "/engine/job/job-1/execute",
        "/engine/external-task/et-1/complete",
        "/engine/task",
        "/engine/task/count",
    ],
)
def test_demais_posts_continuam_passando(canal: tuple[Any, str], caminho: str) -> None:
    _, base = canal
    status, _ = _pedir(base + caminho, "POST", b"{}")

    assert status == 204
    assert _escritas_no_motor() == [("POST", caminho.replace("/engine", "/engine-rest", 1))]


@pytest.mark.parametrize(("ligada", "esperado"), [(False, b"= false;"), (True, b"= true;")])
def test_a_pagina_de_escalonamento_recebe_o_estado_da_chave(
    canal: tuple[Any, str], monkeypatch: pytest.MonkeyPatch, ligada: bool, esperado: bytes
) -> None:
    modulo, base = canal
    monkeypatch.setattr(modulo, "PERMITE_CONCLUIR_PELO_MOTOR", ligada)
    status, pagina = _pedir(f"{base}/p/escalonamento.html", "GET")

    assert status == 200
    assert b"__CANAL_MOTOR_CONCLUI__" not in pagina
    assert b"window.CANAL_MOTOR_CONCLUI " + esperado in pagina


def test_o_padrao_do_ambiente_e_desligado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANAL_PERMITE_CONCLUIR_PELO_MOTOR", raising=False)
    modulo = importlib.reload(importlib.import_module("maezo.platform.testchannel.server"))
    assert modulo.PERMITE_CONCLUIR_PELO_MOTOR is False
    monkeypatch.setenv("CANAL_PERMITE_CONCLUIR_PELO_MOTOR", "true")
    assert importlib.reload(modulo).PERMITE_CONCLUIR_PELO_MOTOR is False, "so' '1' liga"
    monkeypatch.setenv("CANAL_PERMITE_CONCLUIR_PELO_MOTOR", "1")
    assert importlib.reload(modulo).PERMITE_CONCLUIR_PELO_MOTOR is True
    monkeypatch.delenv("CANAL_PERMITE_CONCLUIR_PELO_MOTOR")
    importlib.reload(modulo)


def test_o_terraform_do_dev_nao_declara_a_chave() -> None:
    from pathlib import Path

    tf = Path(__file__).resolve().parents[3] / "deploy/aws-ecs/envs/dev-sa-east-1/service-canal-teste.tf"
    assert "CANAL_PERMITE_CONCLUIR_PELO_MOTOR" not in tf.read_text(encoding="utf-8")


def test_criar_tarefa_avulsa_pelo_motor_e_recusada(canal: tuple[Any, str]) -> None:
    _, base = canal
    status, _ = _pedir(f"{base}/engine/task/create", "POST", b"{}")

    assert status == 403
    assert _escritas_no_motor() == []


def test_delegar_pelo_motor_e_recusado_como_assumir() -> None:
    """O cenario da revisao do #869: com a chave desligada, `delegate` e' recusado como `claim`."""
    from maezo.platform.testchannel import server

    assert server._recusa_do_motor("POST", "/task/tarefa-esc-1/delegate") is not None
    assert server._recusa_do_motor("POST", "/task/tarefa-esc-1/claim") is not None
