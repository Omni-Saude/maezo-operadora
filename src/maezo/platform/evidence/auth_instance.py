"""Evidência de uma instância REAL de SP-OP-AUTH-001, lida do próprio engine.

Cada bloco impresso corresponde a uma linha do quadro de validação que a
diretoria pediu.

Uso:

    ENGINE_REST_URL=http://cibseven.<cluster>.internal:8080/engine-rest \\
        python -m maezo.platform.evidence

O que este módulo deliberadamente NÃO faz:

- **Não conclui que algo "passou".** Imprime o que o engine registrou e deixa a
  leitura para quem confere. Um validador que conclui sozinho é um validador que
  ninguém pode auditar.
- **Não mede inferência de agente.** A única chamada de LLM do fluxo de
  autorização está em `agents/rafael/graph.py:583` e passa ``phi=True``; com o
  provedor Bedrock (``phi_allowed=False``) a fachada recusa ANTES do egresso
  (`PhiZoneRoutingError`). Isso é o controle funcionando, não uma falha desta
  validação — e provar a conexão com o Bedrock é trabalho do probe de inferência.
- **Não inventa dado clínico.** Usa o mesmo `beneficiario_pseudo_id`
  pseudonimizado do teste de integração (ADR-0006 — nunca CPF ou nome).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import httpx

#: Os quatro critérios de aprovação automática que o worker calcula
#: (`tools/workers/auth.py`), como variáveis booleanas de processo.
CRITERIOS: tuple[str, ...] = (
    "criterio_regulatorio_ok",
    "criterio_contratual_ok",
    "criterio_clinico_ok",
    "criterio_financeiro_ok",
)

#: Payload canônico — idêntico ao do teste de integração
#: (`tests/integration/processes/test_sp_op_auth_001.py`), para que a evidência
#: colhida na AWS seja comparável com a do teste local, em vez de ser um caso
#: novo inventado para a ocasião.
PAYLOAD_BASE: dict[str, Any] = {
    "tenant_id": "amh",
    "beneficiario_pseudo_id": "PSEUDO-PACIENTE-TESTE-001",
    "prestador_id": "PRESTADOR-TESTE-001",
    "codigo_procedimento_tuss": "40301010",
    "categoria_procedimento": "consulta",
    "carater_atendimento": "eletivo",
    "valor_estimado_brl": "180.00",
    "documentos_refs": "{}",
    "requer_autorizacao": True,
    "documentacao_completa": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dut_atendida": True,
    "dentro_teto_l2": True,
    "rede_credenciada": True,
}


def _base_url() -> str:
    return os.environ.get("ENGINE_REST_URL", "http://localhost:8080/engine-rest").rstrip("/")


def _tipar(valor: Any) -> dict[str, Any]:
    """Envelopa um valor no formato `variables` do engine."""
    if isinstance(valor, bool):
        return {"value": valor, "type": "Boolean"}
    return {"value": valor, "type": "String"}


def _bloco(titulo: str) -> None:
    print("")
    print("=" * 78)
    print(titulo)
    print("=" * 78)


def coletar(*, client: httpx.Client | None = None, espera_s: float = 25.0) -> dict[str, Any]:
    """Abre uma instância, espera assentar, imprime a evidência bruta."""
    base = _base_url()
    http = client or httpx.Client(timeout=30.0)
    guia = "VALID-" + uuid.uuid4().hex[:10].upper()
    business_key = "AUTH-amh-" + guia

    _bloco("1. ABERTURA DA SOLICITACAO  (guia " + guia + ")")
    print("Canal: REST do engine. NAO houve portal, WhatsApp nem TISS — nenhum canal")
    print("de entrada esta implantado, e o quadro da diretoria registra isso.")
    variaveis = {chave: _tipar(valor) for chave, valor in PAYLOAD_BASE.items()}
    variaveis["numero_guia_tiss"] = _tipar(guia)
    resposta = http.post(
        base + "/process-definition/key/SP-OP-AUTH-001/start",
        json={"businessKey": business_key, "variables": variaveis},
    )
    resposta.raise_for_status()
    pid = resposta.json()["id"]
    print("processInstanceId = " + pid)
    print("businessKey       = " + business_key)

    # O engine e' assincrono (external tasks + job executor). Sem esta espera a
    # evidencia sai pela metade e parece que o processo nao rodou.
    print("")
    print("aguardando " + str(int(espera_s)) + "s o worker e o job executor trabalharem...")
    time.sleep(espera_s)

    _bloco("2. PASSOS QUE O ENGINE REGISTROU")
    atividades = http.get(
        base + "/history/activity-instance",
        params={"processInstanceId": pid, "sortBy": "startTime", "sortOrder": "asc"},
    ).json()
    for atividade in atividades:
        fim = atividade.get("endTime") or "EM ABERTO"
        print(
            "  "
            + str(atividade.get("activityType")).ljust(26)
            + str(atividade.get("activityId")).ljust(44)
            + str(fim)
        )

    _bloco("3. DECISOES DMN — o que entrou, o que saiu")
    decisoes = http.get(
        base + "/history/decision-instance",
        params={
            "processInstanceId": pid,
            "includeInputs": "true",
            "includeOutputs": "true",
            "sortBy": "evaluationTime",
            "sortOrder": "asc",
        },
    ).json()
    if not decisoes:
        print("  NENHUMA decisao registrada — se o quadro afirma 3, esta e' a divergencia.")
    for decisao in decisoes:
        print("")
        print(
            "  tabela: "
            + str(decisao.get("decisionDefinitionKey"))
            + "   ("
            + str(decisao.get("evaluationTime"))
            + ")"
        )
        for entrada in decisao.get("inputs") or []:
            nome = entrada.get("clauseName") or entrada.get("clauseId")
            print("    entrada  " + str(nome) + " = " + repr(entrada.get("value")))
        for saida in decisao.get("outputs") or []:
            print("    saida    " + str(saida.get("variableName")) + " = " + repr(saida.get("value")))

    _bloco("4. OS QUATRO CRITERIOS DE APROVACAO AUTOMATICA")
    variaveis_hist = http.get(base + "/history/variable-instance", params={"processInstanceId": pid}).json()
    por_nome = {v["name"]: v for v in variaveis_hist}
    for criterio in CRITERIOS:
        registro = por_nome.get(criterio)
        print("  " + criterio.ljust(28) + " = " + (str(registro["value"]) if registro else "AUSENTE"))
    for extra in ("motivo_criterios", "criterios_falhas", "auto_aprovavel", "desfecho"):
        if extra in por_nome:
            print("  " + extra.ljust(28) + " = " + repr(por_nome[extra]["value"]))

    _bloco("5. PRAZOS DA RN 259 (relogios armados)")
    jobs = http.get(base + "/job", params={"processInstanceId": pid}).json()
    if not jobs:
        print("  nenhum job/timer pendente")
    for job in jobs:
        print("  vence em " + str(job.get("dueDate")) + "   atividade=" + str(job.get("activityId")))

    _bloco("6. ONDE PAROU — a decisao humana")
    tarefas = http.get(base + "/task", params={"processInstanceId": pid}).json()
    if not tarefas:
        print("  nenhuma tarefa humana aberta")
    for tarefa in tarefas:
        print("  " + str(tarefa.get("taskDefinitionKey")) + "   nome=" + repr(tarefa.get("name")))
        print("    criada=" + str(tarefa.get("created")) + "  assignee=" + str(tarefa.get("assignee")))

    _bloco("7. INCIDENTES (falha real, se houver)")
    incidentes = http.get(base + "/incident", params={"processInstanceId": pid}).json()
    if not incidentes:
        print("  nenhum incidente")
    for incidente in incidentes:
        print("  " + str(incidente.get("incidentType")) + "  atividade=" + str(incidente.get("activityId")))
        print("    mensagem=" + str(incidente.get("incidentMessage")))

    _bloco("8. ESTADO FINAL DA INSTANCIA")
    historico = http.get(base + "/history/process-instance/" + pid).json()
    print(
        "  state="
        + str(historico.get("state"))
        + "  inicio="
        + str(historico.get("startTime"))
        + "  fim="
        + str(historico.get("endTime"))
    )

    if client is None:
        http.close()

    return {
        "process_instance_id": pid,
        "business_key": business_key,
        "state": historico.get("state"),
        "decisoes": [d.get("decisionDefinitionKey") for d in decisoes],
        "tarefas_abertas": [t.get("taskDefinitionKey") for t in tarefas],
        "timers": [j.get("dueDate") for j in jobs],
        "incidentes": [i.get("incidentType") for i in incidentes],
        "criterios": {c: (por_nome.get(c) or {}).get("value") for c in CRITERIOS},
    }


def main() -> int:
    resumo = coletar()
    _bloco("RESUMO (json, para o dossie)")
    print(json.dumps(resumo, indent=2, ensure_ascii=False, default=str))
    return 0
