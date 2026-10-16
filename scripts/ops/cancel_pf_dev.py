"""SP-OP-CANCEL-001 em DEV (DL-0111): listar, concluir execucao/decisao de TESTE e apagar instancia de teste.

Roda DENTRO da VPC de dev, na task avulsa `bateria` (sem senhas, sem identidade AWS; o SG dela alcanca
`cibseven:8080/engine-rest`, aberto so' em dev — decisao N5, `scripts/ci/check_engine_rest_auth.py`). Use pelo
`scripts/ops/bateria_vpc_run.sh`. Runbook: `docs/runbooks/cancel-001-pf.md`.

SO' DEV, SO' TESTE. Atalho interino ate' o portal concluir as tarefas do cancelamento (mesma natureza do
atalho de conclusao direta do DL-0049): sem assinatura, sem recibo. NAO substitui o portal e NAO serve para
caso real. Nunca imprime nome, CPF, telefone nem texto livre: so' ids tecnicos, chave de negocio (pseudonima),
nome da tarefa e o protocolo derivado do id da instancia.

MODO (variavel de ambiente):
  listar    instancias VIVAS de SP-OP-CANCEL-001, a tarefa aberta de cada uma e o protocolo.
  executar  conclui UT_ExecutarCancelamentoCadastro / UT_CoordenacaoExecucaoCadastro (TASK_ID) com
            RESULTADO=EXECUTADO|IMPEDIDO ("cancelamento executado no sistema" ou impedido).
  decidir   conclui UT_AnaliseRescisao / UT_CoordenacaoCancelamento (TASK_ID) com DECISAO=SOLICITAR_INFO|
            MANTER|EFETIVAR_PEDIDO. RESCINDIR e SUSPENDER NAO existem aqui (L0: so' pela pessoa, no portal).
  apagar    apaga a instancia INSTANCE_ID (so' de SP-OP-CANCEL-001), sem disparar listeners: limpeza de teste,
            nenhuma mensagem sai ao beneficiario.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any

PROCESSO = "SP-OP-CANCEL-001"
BASE = os.environ.get("ENGINE_REST", "http://cibseven.maezo-operadora-dev.internal:8080/engine-rest")
TAREFAS_EXECUCAO = frozenset({"UT_ExecutarCancelamentoCadastro", "UT_CoordenacaoExecucaoCadastro"})
TAREFAS_DECISAO = frozenset({"UT_AnaliseRescisao", "UT_CoordenacaoCancelamento"})
DECISOES_DE_TESTE = frozenset({"SOLICITAR_INFO", "MANTER", "EFETIVAR_PEDIDO"})


def _http(metodo: str, caminho: str, corpo: dict[str, Any] | None = None) -> Any:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(
        BASE + caminho, data=dados, method=metodo, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # URL interna fixa da VPC de dev
        bruto = resp.read()
    return json.loads(bruto) if bruto else None


def protocolo(instance_id: str) -> str:
    """O MESMO de `cancelamento_pf.protocolo_do_cancelamento` (repetido: a task nao tem o pacote maezo)."""
    d = hashlib.sha256(f"maezo-cancel-protocolo/v1:{instance_id.strip()}".encode()).hexdigest().upper()
    return f"CAN-{d[:4]}-{d[4:8]}"


def _var(valor: Any, tipo: str = "String") -> dict[str, Any]:
    return {"value": valor, "type": tipo}


def listar() -> None:
    q = urllib.parse.urlencode({"processDefinitionKey": PROCESSO, "active": "true"})
    for inst in _http("GET", f"/process-instance?{q}") or []:
        tarefas = _http("GET", "/task?" + urllib.parse.urlencode({"processInstanceId": inst["id"]})) or []
        print(
            json.dumps(
                {
                    "instance_id": inst["id"],
                    "business_key": inst.get("businessKey"),
                    "definicao": inst.get("definitionId"),
                    "protocolo": protocolo(inst["id"]),
                    "tarefas": [{"task_id": t["id"], "tarefa": t["taskDefinitionKey"]} for t in tarefas],
                }
            )
        )


def _tarefa_do_cancelamento(task_id: str, permitidas: frozenset[str]) -> dict[str, Any]:
    tarefa = _http("GET", f"/task/{urllib.parse.quote(task_id)}")
    if tarefa.get("taskDefinitionKey") not in permitidas:
        raise SystemExit(f"recusado: a tarefa nao e' uma de {sorted(permitidas)}")
    if not str(tarefa.get("processDefinitionId", "")).startswith(PROCESSO + ":"):
        raise SystemExit("recusado: a tarefa nao e' de SP-OP-CANCEL-001")
    return dict(tarefa)


def executar() -> None:
    task_id, resultado = os.environ["TASK_ID"], os.environ["RESULTADO"]
    if resultado not in ("EXECUTADO", "IMPEDIDO"):
        raise SystemExit("RESULTADO deve ser EXECUTADO ou IMPEDIDO")
    _tarefa_do_cancelamento(task_id, TAREFAS_EXECUCAO)
    variaveis = {
        "execucao_cadastro": _var(resultado),
        "responsavel_id": _var(os.environ.get("RESPONSAVEL_ID", "teste-dev-cadastro")),
    }
    _http("POST", f"/task/{urllib.parse.quote(task_id)}/complete", {"variables": variaveis})
    print(json.dumps({"concluida": task_id, "execucao_cadastro": resultado}))


def decidir() -> None:
    task_id, decisao = os.environ["TASK_ID"], os.environ["DECISAO"]
    if decisao not in DECISOES_DE_TESTE:
        raise SystemExit(f"DECISAO deve ser uma de {sorted(DECISOES_DE_TESTE)} (nunca RESCINDIR/SUSPENDER)")
    _tarefa_do_cancelamento(task_id, TAREFAS_DECISAO)
    variaveis = {
        "decisao_cancelamento": _var(decisao),
        "responsavel_id": _var(os.environ.get("RESPONSAVEL_ID", "teste-dev-juridico")),
    }
    if decisao == "MANTER":
        variaveis["fundamentacao_contratual"] = _var("Teste em dev (DL-0111) - sem caso real")
    _http("POST", f"/task/{urllib.parse.quote(task_id)}/complete", {"variables": variaveis})
    print(json.dumps({"concluida": task_id, "decisao_cancelamento": decisao}))


def apagar() -> None:
    instance_id = os.environ["INSTANCE_ID"]
    inst = _http("GET", f"/process-instance/{urllib.parse.quote(instance_id)}")
    if not str(inst.get("definitionId", "")).startswith(PROCESSO + ":"):
        raise SystemExit("recusado: a instancia nao e' de SP-OP-CANCEL-001")
    q = urllib.parse.urlencode(
        {"skipCustomListeners": "true", "skipIoMappings": "true", "deleteReason": "teste-cancel-pf-dev"}
    )
    _http("DELETE", f"/process-instance/{urllib.parse.quote(instance_id)}?{q}")
    print(json.dumps({"apagada": instance_id}))


def main() -> int:
    modos = {"listar": listar, "executar": executar, "decidir": decidir, "apagar": apagar}
    modo = os.environ.get("MODO", "")
    if modo not in modos:
        print(f"MODO deve ser um de {sorted(modos)}", file=sys.stderr)
        return 2
    modos[modo]()
    return 0


if __name__ in ("__main__", "vpc"):
    raise SystemExit(main())
