"""Resultados do Canal de Teste — o que aconteceu com um caso REAL, so' para numeros DECLARADOS.

Parte 2 de `temp/TESTES_CAMINHO_REAL.md`. A pagina `paginas/resultados.html` responde, por caso:
o que a Helena entendeu, para onde foi, com que prazo, quem tratou, o que o humano fez e como
terminou. E' SO' LEITURA: este modulo so' emite `GET` e so' para sete caminhos do motor
(`LEITURAS_PERMITIDAS`). Nao envia mensagem, nao conclui tarefa, nao cria rota no motor.

A CERCA, E POR QUE ELA E' DE SERVIDOR

O telefone vira `conversation_id = wa:{tenant}:hk1_{hmac}` no receptor, com a chave `PHI_HMAC_KEY`,
e a chave do caso e' `ESC-{tenant}-{conversation_id}`. O navegador nao tem a chave e nao deve ter.
Entao o SERVIDOR le' a lista declarada (`MAEZO_TESTCHANNEL_RESULT_ALLOWLIST`, E.164), converte cada
numero com a MESMA funcao do receptor e so' pergunta ao motor por essas chaves. Um caso de numero
fora da lista nao aparece por construcao: ninguem pede por ele. E a resposta do motor passa por
uma segunda peneira (`businessKey` tem de estar no conjunto), porque um parametro de consulta que o
motor ignorasse devolveria tudo.

O numero nunca sai daqui: nao vai ao navegador, nao e' logado, nao e' guardado. O campo "meu
numero" da pagina chega por POST, e' convertido e descartado; so' serve para ESTREITAR a lista
declarada, nunca para ampliar — um numero fora da lista devolve lista vazia, igual a um numero
sem caso.

A CONVERSAO E' A DO RECEPTOR, E O TESTE PROVA

`hash_phone` (`maezo.platform.webhooks.whatsapp.security`) faz
`pseudonymizer.pseudonymize({"telefone": f"{tenant}:{phone}"})["telefone"]` com o prefixo `hk1_`.
Este modulo chama o MESMO `Pseudonymizer`, pelo MESMO `from_settings`, com as MESMAS variaveis
(`PHI_HMAC_KEY`, `TENANT_ID`, `RUNTIME_MODE`). Nao importa `hash_phone` direto por um motivo
medido: o pacote `maezo.platform.webhooks.whatsapp` arrasta o receptor inteiro (2.833 modulos,
langgraph incluido) para um container de 512 MB que roda stdlib. O teste
`test_canal_resultados.py` compara as duas funcoes numero a numero — se `hash_phone` mudar,
ele reprova.

O NONO DIGITO. A Meta devolve alguns numeros brasileiros antigos SEM o 9 (`wa_id` de 12 digitos).
Um numero declarado com 13 digitos gera tambem a forma de 12 — mas so' quando o digito seguinte e'
6-9 (faixa de celular antiga). Tirar o 9 de `55 11 9 3xxx-xxxx` daria um FIXO de outra pessoa, e
acrescentar um 9 a um numero de 12 digitos daria um celular de outra pessoa; nenhuma das duas coisas
e' feita.

O QUE O MOTOR NAO GUARDA, E A PAGINA DIZ

- As mensagens trocadas vivem no checkpoint da conversa (Postgres do agente), nao no motor. O que o
  processo recebe e' `resumo_contexto` — o resumo que a Helena escreveu, ja' pseudonimizado.
- O momento do "assumir" (claim) nao aparece em nenhuma das sete leituras. O que o processo marca
  e' a CONCLUSAO da tarefa; e' isso que "tempo ate o humano concluir" mede.
- O texto que a Helena envia na retomada (`devolvido_agente`) nao volta ao motor.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

#: Variavel da lista declarada. E.164 (com ou sem `+`), separada por virgula, ponto e virgula ou
#: espaco. Vazia = a pagina nao lista nada — o lado que falha seguro.
ALLOWLIST_ENV = "MAEZO_TESTCHANNEL_RESULT_ALLOWLIST"

PROCESSO = "SP-OP-ESCALATION-001"

#: AS SETE LEITURAS. Comparacao EXATA de caminho: um prefixo `/job` deixaria passar
#: `/job-definition/...`, e `startswith("/history/task")` deixaria passar o que viesse depois.
LEITURAS_PERMITIDAS: frozenset[str] = frozenset(
    {
        "/history/process-instance",
        "/history/decision-instance",
        "/job",
        "/history/job-log",
        "/history/task",
        "/history/variable-instance",
        "/history/activity-instance",
    }
)

#: Janela maxima de uma consulta. A pagina e' para achar um caso, nao para varrer o motor.
JANELA_MAXIMA = timedelta(days=31)

#: Quanto antes do inicio do processo a triagem pode ter acontecido e ainda ser DESTE caso.
JANELA_TRIAGEM = timedelta(seconds=180)

_FORMATO_MOTOR = "%Y-%m-%dT%H:%M:%S.000+0000"
#: America/Sao_Paulo sem horario de verao desde 2019: UTC-3 fixo, sem depender de `tzdata` na imagem.
_BRASILIA = timezone(timedelta(hours=-3), "BRT")
_SEPARADORES = re.compile(r"[\s\-().]")
_E164 = re.compile(r"[1-9]\d{9,14}")

PRIORIDADES = {"P1": "P1 — emergência", "P2": "P2 — urgente", "P3": "P3 — pode esperar"}
MOTIVOS = {
    "red_flag_clinico": "bandeira vermelha clínica",
    "risco_psicossocial": "risco psicossocial",
    "intencao_clinica": "pedido de orientação clínica",
    "solicitacao_humano": "pediu falar com uma pessoa",
    "falha_tecnica": "falha técnica",
    "outro": "outro",
}
RESULTADOS = {
    "resolvido_humano": "resolvido por uma pessoa",
    "devolvido_agente": "devolvido ao agente (a conversa retoma)",
    "emergencia_acionada": "emergência acionada",
}
FINS = {
    "End_ResolvidoPorHumano": "Resolvido por humano",
    "End_DevolvidoAoAgente": "Devolvido ao agente",
    "End_SupervisorAlertado": "Supervisor alertado (caso segue aberto)",
}
RELOGIOS = {"BT_SlaAck": "Prazo de ciência", "BT_SlaResolucao": "Prazo de resolução"}
TAREFAS = {"UT_TratarEscalonamento", "UT_SupervisorAssume"}


class LeituraRecusadaError(RuntimeError):
    """Caminho fora das sete leituras. Levantada ANTES de qualquer rede."""


# ------------------------------------------------------------------------------------ numeros


def normalizar_numero(bruto: str) -> str | None:
    """E.164 em digitos puros, como a Meta entrega em `from` (sem `+`). `None` se nao for numero."""
    texto = _SEPARADORES.sub("", bruto or "")
    if texto.startswith("+"):
        texto = texto[1:]
    return texto if _E164.fullmatch(texto) else None


def formas_do_numero(digitos: str) -> tuple[str, ...]:
    """O numero e, para celular BR de 13 digitos na faixa antiga (6-9), a forma sem o nono digito."""
    formas = [digitos]
    if len(digitos) == 13 and digitos.startswith("55") and digitos[4] == "9" and digitos[5] in "6789":
        formas.append(digitos[:4] + digitos[5:])
    return tuple(formas)


def ler_lista(bruta: str) -> tuple[list[str], int]:
    """(numeros validos sem repeticao, quantidade de entradas invalidas). Entrada invalida nunca e'
    ecoada — nem no log: pode ser um numero real digitado errado."""
    validos: list[str] = []
    invalidos = 0
    for pedaco in re.split(r"[,;\s]+", bruta or ""):
        if not pedaco:
            continue
        numero = normalizar_numero(pedaco)
        if numero is None:
            invalidos += 1
        elif numero not in validos:
            validos.append(numero)
    return validos, invalidos


def conversation_id(numero: str, tenant: str, pseudonymizer: Any) -> str:
    """`wa:{tenant}:hk1_{hmac}` — a composicao de `hash_phone` + a do despachante, byte a byte."""
    from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX

    digest = pseudonymizer.pseudonymize({"telefone": f"{tenant}:{numero}"})["telefone"]
    return f"wa:{tenant}:{KEYED_PSEUDONYM_PREFIX}{digest}"


@dataclass(frozen=True)
class Cerca:
    """O conjunto de conversas que a pagina pode mostrar. Nada fora dele e' consultado."""

    tenant: str
    conversas: frozenset[str]
    numeros_declarados: int
    invalidos: int
    pseudonymizer: Any = field(repr=False)

    def chave(self, cid: str) -> str:
        return f"ESC-{self.tenant}-{cid}"

    @property
    def chaves(self) -> frozenset[str]:
        return frozenset(self.chave(c) for c in self.conversas)

    def conversas_do_numero(self, bruto: str) -> frozenset[str]:
        """So' ESTREITA: a intersecao com a lista declarada. Numero fora da lista -> vazio."""
        numero = normalizar_numero(bruto)
        if numero is None:
            return frozenset()
        return (
            frozenset(conversation_id(f, self.tenant, self.pseudonymizer) for f in formas_do_numero(numero))
            & self.conversas
        )


def construir_cerca(ambiente: Mapping[str, str]) -> tuple[Cerca | None, str]:
    """`(cerca, motivo)`. Sem lista ou sem chave -> `None`, e a pagina diz por que em vez de listar."""
    numeros, invalidos = ler_lista(ambiente.get(ALLOWLIST_ENV, ""))
    if not numeros:
        extra = f" ({invalidos} entrada(s) invalida(s) ignorada(s))" if invalidos else ""
        return None, f"sem lista declarada em {ALLOWLIST_ENV}{extra}"
    from maezo.gateway.pseudonymizer import Pseudonymizer, PseudonymizerKeyMissingError

    tenant = ambiente.get("TENANT_ID", "amh")
    try:
        # MESMA chamada da raiz de composicao do receptor (`webhooks/service.py`): em modo que nao
        # e' `local`, chave ausente RECUSA — nunca cai numa chave de dev que produziria outro hash.
        pseudo = Pseudonymizer.from_settings(
            phi_hmac_key=ambiente.get("PHI_HMAC_KEY"),
            production=ambiente.get("RUNTIME_MODE", "") != "local",
            tenant_id=tenant,
        )
    except PseudonymizerKeyMissingError:
        return None, "PHI_HMAC_KEY ausente — sem a chave do receptor nao ha como achar caso nenhum"
    conversas = frozenset(conversation_id(f, tenant, pseudo) for n in numeros for f in formas_do_numero(n))
    extra = f", {invalidos} entrada(s) invalida(s) ignorada(s)" if invalidos else ""
    return (
        Cerca(
            tenant=tenant,
            conversas=conversas,
            numeros_declarados=len(numeros),
            invalidos=invalidos,
            pseudonymizer=pseudo,
        ),
        f"ligada ({len(numeros)} numero(s) declarado(s){extra})",
    )


# ------------------------------------------------------------------------------------ motor


class LeitorDoMotor:
    """Cliente do `engine-rest` que SO' sabe fazer GET, e so' nos sete caminhos."""

    def __init__(self, base: str, abrir: Callable[..., Any] = urllib.request.urlopen, timeout: int = 15):
        self._base = base.rstrip("/")
        self._abrir = abrir
        self._timeout = timeout

    def ler(self, caminho: str, **params: Any) -> Any:
        if caminho not in LEITURAS_PERMITIDAS:
            raise LeituraRecusadaError(f"leitura fora da lista: {caminho}")
        consulta = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(
            f"{self._base}{caminho}?{consulta}", method="GET", headers={"Accept": "application/json"}
        )
        with self._abrir(req, timeout=self._timeout) as resposta:
            return json.loads(resposta.read() or b"null")


# ------------------------------------------------------------------------------------ tempo


def _data(valor: str | None) -> datetime | None:
    if not valor:
        return None
    for formato in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(valor, formato)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def _para_motor(momento: datetime) -> str:
    return momento.astimezone(UTC).strftime(_FORMATO_MOTOR)


def janela(desde: str | None, ate: str | None, agora: datetime | None = None) -> tuple[datetime, datetime]:
    """Janela pedida, com default de 24 h e teto de `JANELA_MAXIMA`."""
    agora = agora or datetime.now(UTC)
    fim = _data(ate) or agora
    inicio = _data(desde) or (fim - timedelta(hours=24))
    if fim.tzinfo is None:
        fim = fim.replace(tzinfo=UTC)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=UTC)
    if inicio > fim:
        inicio, fim = fim, inicio
    if fim - inicio > JANELA_MAXIMA:
        inicio = fim - JANELA_MAXIMA
    return inicio, fim


def _duracao(segundos: float | None) -> str:
    if segundos is None:
        return "—"
    s = int(round(segundos))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def _hora(valor: str | None) -> str:
    d = _data(valor)
    if d is None:
        return "—"
    return d.astimezone(_BRASILIA).strftime("%d/%m/%Y %H:%M:%S")


# ------------------------------------------------------------------------------------ casos


def _entradas(decisao: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i in decisao.get("inputs") or []:
        nome = i.get("clauseName") or i.get("clauseId") or "?"
        m = re.search(r"\(([a-z_]+)\)\s*$", str(nome))
        out[m.group(1) if m else str(nome)] = i.get("value")
    return out


def _saidas(decisao: Mapping[str, Any]) -> dict[str, Any]:
    return {
        (o.get("variableName") or o.get("clauseName") or "?"): o.get("value")
        for o in decisao.get("outputs") or []
    }


def _variaveis(motor: LeitorDoMotor, pid: str) -> dict[str, Any]:
    vs = motor.ler(
        "/history/variable-instance", processInstanceId=pid, deserializeValues="false", maxResults=200
    )
    return {v.get("name"): v.get("value") for v in (vs or []) if v.get("name")}


def _roteamento(motor: LeitorDoMotor, pid: str) -> dict[str, Any] | None:
    ds = motor.ler(
        "/history/decision-instance",
        processInstanceId=pid,
        includeInputs="true",
        includeOutputs="true",
        maxResults=20,
    )
    for d in ds or []:
        if d.get("decisionDefinitionKey") == "escalation_routing":
            decisao: dict[str, Any] = d
            return decisao
    return None


def _tarefas(motor: LeitorDoMotor, pid: str) -> list[dict[str, Any]]:
    ts = motor.ler("/history/task", processInstanceId=pid, sortBy="startTime", sortOrder="asc", maxResults=20)
    return list(ts or [])


def _tempo_tarefa(tarefa: Mapping[str, Any] | None, agora: datetime) -> tuple[str, bool]:
    """(texto, concluida). Mede criacao -> conclusao: e' o que o motor registra (ver docstring)."""
    if not tarefa:
        return "sem tarefa humana", False
    ini, fim = _data(tarefa.get("startTime")), _data(tarefa.get("endTime"))
    if ini is None:
        return "—", False
    if fim is None:
        return f"aberta há {_duracao((agora - ini).total_seconds())}", False
    return _duracao((fim - ini).total_seconds()), True


def _desfecho(estado: str | None, resultado: Any) -> str:
    if resultado in RESULTADOS:
        return RESULTADOS[resultado]
    if estado == "ACTIVE":
        return "em aberto"
    return (estado or "—").lower()


def resumo_do_caso(
    motor: LeitorDoMotor, cerca: Cerca, proc: Mapping[str, Any], agora: datetime
) -> dict[str, Any]:
    pid = str(proc.get("id"))
    rot = _roteamento(motor, pid)
    sai = _saidas(rot) if rot else {}
    ent = _entradas(rot) if rot else {}
    tarefas = [t for t in _tarefas(motor, pid) if t.get("taskDefinitionKey") in TAREFAS]
    principal = tarefas[0] if tarefas else None
    tempo, _ = _tempo_tarefa(principal, agora)
    variaveis = _variaveis(motor, pid)
    return {
        "id": pid,
        "conversa": _rotulo_conversa(cerca, proc.get("businessKey")),
        "inicio": proc.get("startTime"),
        "inicio_txt": _hora(proc.get("startTime")),
        "estado": proc.get("state"),
        "prioridade": sai.get("prioridade"),
        "grupo": sai.get("grupo_atendimento"),
        "motivo": MOTIVOS.get(str(ent.get("motivo_categoria")), ent.get("motivo_categoria")),
        "tratado_por": principal.get("assignee") if principal else None,
        "tempo_ate_concluir": tempo,
        "desfecho": _desfecho(proc.get("state"), variaveis.get("resultado")),
    }


def _rotulo_conversa(cerca: Cerca, chave: Any) -> str:
    """Os ultimos 8 caracteres do pseudonimo: distingue duas conversas sem expor nada novo (o
    pseudonimo inteiro ja' e' visivel no Cockpit)."""
    texto = str(chave or "")
    return "…" + texto[-8:] if texto else "—"


def listar_casos(
    motor: LeitorDoMotor,
    cerca: Cerca,
    desde: str | None,
    ate: str | None,
    numero: str | None = None,
    agora: datetime | None = None,
) -> dict[str, Any]:
    agora = agora or datetime.now(UTC)
    inicio, fim = janela(desde, ate, agora)
    conversas = cerca.conversas_do_numero(numero) if numero else cerca.conversas
    chaves = {cerca.chave(c) for c in conversas}
    processos: list[Mapping[str, Any]] = []
    for chave in sorted(chaves):
        achados = motor.ler(
            "/history/process-instance",
            processDefinitionKey=PROCESSO,
            processInstanceBusinessKey=chave,
            startedAfter=_para_motor(inicio),
            startedBefore=_para_motor(fim),
            sortBy="startTime",
            sortOrder="desc",
            maxResults=100,
        )
        # SEGUNDA PENEIRA: se o motor ignorasse o filtro, ele devolveria TODO mundo.
        processos.extend(p for p in achados or [] if p.get("businessKey") == chave)
    casos = [resumo_do_caso(motor, cerca, p, agora) for p in processos]
    casos.sort(key=lambda c: str(c.get("inicio") or ""), reverse=True)
    return {
        "casos": casos,
        "janela": {"desde": inicio.isoformat(), "ate": fim.isoformat()},
        "filtrado_por_numero": bool(numero),
    }


def _processo_na_cerca(motor: LeitorDoMotor, cerca: Cerca, pid: str) -> Mapping[str, Any] | None:
    achados = motor.ler("/history/process-instance", processInstanceId=pid, maxResults=1)
    for p in achados or []:
        if (
            p.get("id") == pid
            and p.get("processDefinitionKey") == PROCESSO
            and p.get("businessKey") in cerca.chaves
        ):
            proc: Mapping[str, Any] = p
            return proc
    return None


def _triagem(motor: LeitorDoMotor, proc: Mapping[str, Any], ref: Any) -> tuple[dict[str, Any] | None, str]:
    """A triagem acontece ANTES do processo, fora dele: o elo e' `dmn_decision_ref`
    (`{tabela}#{decisionDefinitionId}`) + a janela de tempo. So' mostra com UM candidato — dois
    candidatos na mesma janela podem ser de outra pessoa, e a pagina nao escolhe."""
    if not ref or "#" not in str(ref):
        return (
            None,
            "o processo não registrou `dmn_decision_ref` — a mensagem não passou por tabela de triagem",
        )
    tabela, definicao = str(ref).split("#", 1)
    inicio = _data(proc.get("startTime"))
    if inicio is None:
        return None, "processo sem hora de início"
    ds = motor.ler(
        "/history/decision-instance",
        decisionDefinitionId=definicao,
        evaluatedAfter=_para_motor(inicio - JANELA_TRIAGEM),
        evaluatedBefore=_para_motor(inicio + timedelta(seconds=5)),
        includeInputs="true",
        includeOutputs="true",
        maxResults=10,
    )
    candidatos = [
        d for d in ds or [] if d.get("decisionDefinitionKey") == tabela and not d.get("processInstanceId")
    ]
    if len(candidatos) == 1:
        return candidatos[0], "uma única avaliação desta tabela na janela do caso"
    if not candidatos:
        return (
            None,
            f"nenhuma avaliação de `{tabela}` nos {int(JANELA_TRIAGEM.total_seconds())} s antes do caso",
        )
    return None, (
        f"{len(candidatos)} avaliações de `{tabela}` na mesma janela — podem ser de outra pessoa; "
        "a página não escolhe uma"
    )


def _relogios(motor: LeitorDoMotor, pid: str, agora: datetime) -> list[dict[str, Any]]:
    log = motor.ler(
        "/history/job-log", processInstanceId=pid, sortBy="timestamp", sortOrder="asc", maxResults=200
    )
    ativos = motor.ler("/job", processInstanceId=pid, maxResults=50)
    por_job: dict[str, dict[str, Any]] = {}
    for e in log or []:
        if e.get("jobDefinitionType") not in (None, "timer") or e.get("activityId") not in RELOGIOS:
            continue
        r = por_job.setdefault(
            str(e.get("jobId")),
            {
                "relogio": RELOGIOS[e["activityId"]],
                "atividade": e["activityId"],
                "vence": None,
                "situacao": "armado",
            },
        )
        r["vence"] = r["vence"] or e.get("jobDueDate")
        if e.get("successLog"):
            r["situacao"] = "ESTOUROU (disparou em " + _hora(e.get("timestamp")) + ")"
        elif e.get("deletionLog") and not r["situacao"].startswith("ESTOUROU"):
            r["situacao"] = "desarmado sem disparar (a atividade terminou antes de vencer)"
        elif e.get("failureLog"):
            r["situacao"] = "falhou ao disparar"
    for j in ativos or []:
        ativo = por_job.get(str(j.get("id")))
        vence = _data(j.get("dueDate"))
        if ativo is None:
            continue
        r = ativo
        r["vence"] = j.get("dueDate") or r["vence"]
        if vence is not None:
            falta = (vence - agora).total_seconds()
            r["situacao"] = (
                f"armado — faltam {_duracao(falta)}"
                if falta > 0
                else f"armado e VENCIDO há {_duracao(-falta)}"
            )
    saida = list(por_job.values())
    for r in saida:
        r["vence_txt"] = _hora(r["vence"])
    return saida


def detalhe_do_caso(
    motor: LeitorDoMotor, cerca: Cerca, pid: str, agora: datetime | None = None
) -> dict[str, Any] | None:
    """Os oito blocos + o markdown para colar. `None` = fora da cerca OU inexistente (mesmo 404)."""
    agora = agora or datetime.now(UTC)
    proc = _processo_na_cerca(motor, cerca, pid)
    if proc is None:
        return None
    variaveis = _variaveis(motor, pid)
    triagem, triagem_nota = _triagem(motor, proc, variaveis.get("dmn_decision_ref"))
    rot = _roteamento(motor, pid)
    tarefas = [t for t in _tarefas(motor, pid) if t.get("taskDefinitionKey") in TAREFAS]
    atividades = (
        motor.ler(
            "/history/activity-instance",
            processInstanceId=pid,
            sortBy="startTime",
            sortOrder="asc",
            maxResults=300,
        )
        or []
    )
    relogios = _relogios(motor, pid, agora)

    resultado = variaveis.get("resultado")
    fins = [a for a in atividades if a.get("activityId") in FINS and a.get("endTime")]
    ids = {a.get("activityId") for a in atividades}
    t_ent, t_sai = (_entradas(triagem), _saidas(triagem)) if triagem else ({}, {})
    r_ent, r_sai = (_entradas(rot), _saidas(rot)) if rot else ({}, {})
    principal = tarefas[0] if tarefas else None
    tempo, _ = _tempo_tarefa(principal, agora)

    if resultado == "devolvido_agente":
        publicou = "ST_PublishProcessCompleted" in ids
        retorno = {
            "voltou": "sim" if "End_DevolvidoAoAgente" in ids else ("a caminho" if publicou else "não"),
            "nota": (
                "O motor registra a devolução e a publicação do evento de retomada. O texto que a Helena "
                "enviou na retomada NÃO volta ao motor (é enviado pelo agent-resume a partir de "
                "`notas_resolucao`) — esta página não o vê."
            ),
            "instrucao_do_humano": variaveis.get("notas_resolucao"),
        }
    elif resultado in RESULTADOS:
        retorno = {
            "voltou": "não se aplica",
            "nota": f"O desfecho `{resultado}` encerra o caso sem a Helena voltar a falar.",
        }
    else:
        retorno = {"voltou": "ainda não", "nota": "O caso não tem desfecho humano registrado."}

    blocos: dict[str, Any] = {
        "conversa": {
            "canal": variaveis.get("canal"),
            "resumo_contexto": variaveis.get("resumo_contexto"),
            "nota": (
                "As mensagens trocadas vivem no checkpoint da conversa, não no motor. O que o processo "
                "recebeu é o resumo que a Helena escreveu (`resumo_contexto`, já pseudonimizado)."
            ),
        },
        "leitura": {
            "tabela": triagem.get("decisionDefinitionKey")
            if triagem
            else (str(variaveis.get("dmn_decision_ref") or "").split("#")[0] or None),
            "sintoma": t_ent.get("sintoma_codigo"),
            "intensidade": t_ent.get("intensidade"),
            "idade": t_ent.get(
                "idade_anos", t_ent.get("idade_meses", t_ent.get("idade_gestacional_semanas"))
            ),
            "entradas": t_ent,
            "regra": ", ".join(
                sorted(
                    {str(o.get("ruleId")) for o in (triagem or {}).get("outputs") or [] if o.get("ruleId")}
                )
            )
            or None,
            "avaliada_em": _hora(triagem.get("evaluationTime")) if triagem else None,
            "nota": triagem_nota,
        },
        "veredito": {
            "red_flag": t_sai.get("red_flag"),
            "prioridade": t_sai.get("prioridade"),
            "conduta": t_sai.get("conduta"),
            "motivo": t_sai.get("motivo"),
        },
        "roteamento": {
            "motivo": r_ent.get("motivo_categoria") or variaveis.get("motivo_categoria"),
            "severidade": r_ent.get("severidade") or variaveis.get("severidade"),
            "grupo": r_sai.get("grupo_atendimento"),
            "prioridade": r_sai.get("prioridade"),
            "sla_ack": r_sai.get("sla_ack"),
            "sla_resolucao": r_sai.get("sla_resolucao"),
            "registrado": rot is not None,
        },
        "relogios": relogios,
        "acao_humana": {
            "tarefas": [
                {
                    "nome": t.get("name"),
                    "definicao": t.get("taskDefinitionKey"),
                    "tratado_por": t.get("assignee"),
                    "criada": _hora(t.get("startTime")),
                    "concluida": _hora(t.get("endTime")) if t.get("endTime") else None,
                    "motivo_fim": t.get("deleteReason"),
                }
                for t in tarefas
            ],
            "tempo_ate_concluir": tempo,
            "resultado": resultado,
            "resultado_txt": RESULTADOS.get(str(resultado)) if resultado else None,
            "notas_resolucao": variaveis.get("notas_resolucao"),
            "nota": (
                "O momento do 'assumir' (claim) não é registrado nas leituras do motor; o tempo é da "
                "criação da tarefa à conclusão."
            ),
        },
        "retorno": retorno,
        "rastro": [
            {
                "hora": _hora(a.get("startTime")),
                "atividade": a.get("activityName") or a.get("activityId"),
                "id": a.get("activityId"),
                "tipo": a.get("activityType"),
                "duracao": _duracao(a["durationInMillis"] / 1000)
                if a.get("durationInMillis") is not None
                else "(aberta)",
                "cancelada": bool(a.get("canceled")),
            }
            for a in atividades
        ],
    }
    caso = {
        "id": pid,
        "chave": proc.get("businessKey"),
        "conversa": _rotulo_conversa(cerca, proc.get("businessKey")),
        "estado": proc.get("state"),
        "inicio": _hora(proc.get("startTime")),
        "fim": _hora(proc.get("endTime")) if proc.get("endTime") else None,
        "fim_alcancado": [FINS[a["activityId"]] for a in fins],
        "desfecho": _desfecho(proc.get("state"), resultado),
        "blocos": blocos,
        "lido_em": _hora(agora.isoformat()),
    }
    caso["markdown"] = markdown_do_caso(caso)
    return caso


# ------------------------------------------------------------------------------------ markdown


def _v(valor: Any) -> str:
    if valor is None or valor == "":
        return "—"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    return str(valor)


def markdown_do_caso(caso: Mapping[str, Any]) -> str:
    """O texto que o diretor cola no Claude. Estruturado, com as lacunas DITAS — quem avalia precisa
    saber o que o motor nao mostra, senao le' ausencia como fato."""
    b = caso["blocos"]
    linhas = [
        f"# Caso de escalonamento — {caso.get('conversa')} ({caso.get('inicio')})",
        "",
        "Leitura do motor (CIB Seven) pelo Canal de Teste, página de resultados. Só leitura.",
        f"- Processo: {PROCESSO} · instância `{caso['id']}` · estado **{_v(caso.get('estado'))}**",
        f"- Início: {_v(caso.get('inicio'))} · fim: {_v(caso.get('fim'))}",
        f"- Desfecho: **{_v(caso.get('desfecho'))}**",
        f"- Evento de fim alcançado: {', '.join(caso.get('fim_alcancado') or []) or '—'}",
        f"- Lido em: {caso.get('lido_em')} (horário de Brasília)",
        "",
        "## 1. A conversa",
        f"- Canal: {_v(b['conversa'].get('canal'))}",
        "- Resumo que a Helena escreveu para o processo (`resumo_contexto`): "
        f"{_v(b['conversa'].get('resumo_contexto'))}",
        f"- Limite: {b['conversa']['nota']}",
        "",
        "## 2. A leitura (triagem)",
        f"- Tabela consultada: {_v(b['leitura'].get('tabela'))}",
        f"- Sintoma: {_v(b['leitura'].get('sintoma'))} · intensidade: {_v(b['leitura'].get('intensidade'))}",
        f"- Idade: {_v(b['leitura'].get('idade'))}",
        f"- Todas as entradas: {json.dumps(b['leitura'].get('entradas') or {}, ensure_ascii=False)}",
        f"- Regra: {_v(b['leitura'].get('regra'))} · avaliada em {_v(b['leitura'].get('avaliada_em'))}",
        f"- Como a triagem foi ligada ao caso: {b['leitura']['nota']}",
        "",
        "## 3. O veredito",
        f"- Bandeira vermelha: {_v(b['veredito'].get('red_flag'))}",
        f"- Prioridade: {_v(b['veredito'].get('prioridade'))} · conduta: {_v(b['veredito'].get('conduta'))}",
        f"- Motivo: {_v(b['veredito'].get('motivo'))}",
        "",
        "## 4. O roteamento (DMN escalation_routing)",
    ]
    r = b["roteamento"]
    if not r.get("registrado"):
        linhas.append("- Roteamento ainda não registrado no motor.")
    linhas += [
        f"- Motivo: {_v(MOTIVOS.get(str(r.get('motivo')), r.get('motivo')))}",
        f"- Severidade: {_v(r.get('severidade'))}",
        f"- Grupo: {_v(r.get('grupo'))}",
        f"- Prioridade: {_v(PRIORIDADES.get(str(r.get('prioridade')), r.get('prioridade')))}",
        f"- Prazo de ciência: {_v(r.get('sla_ack'))} · prazo de resolução: {_v(r.get('sla_resolucao'))}",
        "",
        "## 5. Os relógios",
    ]
    if not b["relogios"]:
        linhas.append("- Nenhum relógio registrado.")
    for rel in b["relogios"]:
        linhas.append(f"- {rel['relogio']}: vence {rel['vence_txt']} — {rel['situacao']}")
    h = b["acao_humana"]
    linhas += ["", "## 6. A ação humana"]
    if not h["tarefas"]:
        linhas.append("- Nenhuma tarefa humana criada.")
    for t in h["tarefas"]:
        linhas.append(
            f"- {_v(t['nome'])} (`{_v(t['definicao'])}`): tratado por {_v(t['tratado_por'])} · criada "
            f"{_v(t['criada'])} · concluída {_v(t['concluida'])}"
        )
    linhas += [
        f"- Tempo até o humano concluir: {h['tempo_ate_concluir']}",
        f"- Resultado: {_v(h.get('resultado_txt') or h.get('resultado'))}",
        f"- O que o humano escreveu (`notas_resolucao`): {_v(h.get('notas_resolucao'))}",
        f"- Limite: {h['nota']}",
        "",
        "## 7. O retorno",
        f"- A Helena voltou a falar? {b['retorno']['voltou']}",
        f"- {b['retorno']['nota']}",
        "",
        "## 8. O rastro (atividades do processo, em ordem)",
    ]
    for a in b["rastro"]:
        extra = " · cancelada" if a["cancelada"] else ""
        linhas.append(f"- {a['hora']} — {a['atividade']} (`{a['id']}`, {a['tipo']}) — {a['duracao']}{extra}")
    linhas += [
        "",
        "---",
        "Pergunta para quem avalia: o caminho acima (leitura → veredito → roteamento → prazos → ação "
        "humana → desfecho) está coerente com o que a pessoa relatou? Onde ele falhou ou demorou?",
    ]
    return "\n".join(linhas) + "\n"
