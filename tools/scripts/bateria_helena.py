"""Bateria da Helena no dev: manda mensagens pelo canal de teste, le o que abriu e cancela o que abriu.

Roda DENTRO da VPC (o canal de teste esta' atras do Cloudflare e o motor nao e' alcancavel de fora),
tipicamente na tarefa avulsa `maezo-operadora-dev-bateria`, que nao guarda segredo nenhum — este
script usa so' a biblioteca padrao e nenhuma credencial:

    python -I tools/scripts/bateria_helena.py --saida bateria.jsonl
    python -I tools/scripts/bateria_helena.py --casos A01 B07 D01 --saida parcial.jsonl

Uma linha JSONL por mensagem enviada: caso, texto, HTTP, resposta literal da Helena, latencia e, se a
mensagem abriu atendimento, o roteamento que o MOTOR aplicou (grupo, prioridade, prazos, motivo,
severidade, tabela consultada). A linha final de cada caso diz se o desfecho bate com `espera` — so' a
ROTA; o texto da resposta quem julga e' uma pessoa.

POR QUE ESTE SCRIPT EXISTE. Os DL-0051 e DL-0052 prometem "bateria dev apos o deploy" e a bateria que
mediu o ambiente em 30/09 e 01/10 morava na sessao de quem a rodou. As licoes que ela custou estao
aqui como codigo, nao como memoria:

  * CANCELAR PELA CHAVE EXATA. O filtro de instancia VIVA do CIB Seven e' `businessKey=`;
    `processInstanceBusinessKey=` so' existe no historico e, na instancia viva, e' IGNORADO — devolve
    TODAS as instancias (77 em dev, em 01/10/2026). `reset_conversa_maezo.py` usa o segundo e, com
    `CONFIRM=1`, cancelaria todas. Aqui o cancelamento so' acontece se a consulta devolver EXATAMENTE
    uma instancia e a chave dela for a esperada.
  * SO' A FAIXA SINTETICA. O canal de teste ja' recusa fora de 5511900000xxx; o script recusa antes.
  * RITMO. 6 mensagens por conversa por minuto e 120 por tenant (`WHATSAPP_LIMITE_*`); a 7a mensagem
    volta vazia e parece defeito da Helena. O script espera entre mensagens da mesma conversa.
  * CANCELAR NAO E' CONCLUIR. A instancia some do motor sem o desfecho do atendente; o que depende
    de retomada (cenarios G) nao e' coberto aqui.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

PROCESSO: Final[str] = "SP-OP-ESCALATION-001"
TENANT: Final[str] = "amh"
FAIXA_TESTE: Final[re.Pattern[str]] = re.compile(r"^5511900000\d{3}$")
#: `WHATSAPP_LIMITE_POR_CONVERSA_POR_MINUTO` e' 6: 10 s entre mensagens da mesma conversa deixa folga.
INTERVALO_NA_CONVERSA_S: Final[float] = 10.0
#: `WHATSAPP_LIMITE_POR_TENANT_POR_MINUTO` e' 120: 0,6 s entre quaisquer duas mensagens fica em 100/min.
INTERVALO_ENTRE_ENVIOS_S: Final[float] = 0.6
PRIMEIRO_NUMERO: Final[int] = 200

CANAL_PADRAO: Final[str] = "http://canal-teste.maezo-operadora-dev.internal:8500"
MOTOR_PADRAO: Final[str] = "http://cibseven.maezo-operadora-dev.internal:8080/engine-rest"
CASOS_PADRAO: Final[Path] = Path(__file__).with_name("bateria_helena_casos.json")

Http = Callable[[str, str, "Mapping[str, Any] | None", float], "tuple[int, Any]"]


class RecusaDeSegurancaError(RuntimeError):
    """O script se recusou a fazer algo que poderia atingir instancia que nao e' dele."""


def http_json(
    metodo: str, url: str, corpo: Mapping[str, Any] | None = None, timeout: float = 30.0
) -> tuple[int, Any]:
    """HTTP minimo com a biblioteca padrao. Devolve `(status, json | None)`; nunca levanta por 4xx/5xx."""
    dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
    pedido = urllib.request.Request(url, data=dados, method=metodo)
    if dados is not None:
        pedido.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            bruto = resposta.read()
            status = resposta.status
    except urllib.error.HTTPError as erro:
        bruto, status = erro.read(), erro.code
    try:
        return status, (json.loads(bruto) if bruto else None)
    except ValueError:
        return status, None


def decodifica_roteamento(valor_base64: str) -> dict[str, str]:
    """`roteamento` e' um `HashMap<String,String>` serializado em Java e guardado em base64.

    Le as strings (`TC_STRING` = 0x74 + tamanho de 2 bytes + UTF-8) na ordem e as pareia como
    chave/valor. Nao e' um desserializador: so' o que o motor grava nessa variavel (grupo, prioridade
    e prazos), e devolve `{}` se o formato nao for o esperado."""
    try:
        bruto = base64.b64decode(valor_base64)
    except ValueError:
        return {}
    textos: list[str] = []
    i = 0
    while i + 3 <= len(bruto):
        if bruto[i] == 0x74:
            tamanho = int.from_bytes(bruto[i + 1 : i + 3], "big")
            trecho = bruto[i + 3 : i + 3 + tamanho]
            if len(trecho) == tamanho and tamanho > 0:
                try:
                    textos.append(trecho.decode("utf-8"))
                    i += 3 + tamanho
                    continue
                except UnicodeDecodeError:
                    pass
        i += 1
    return dict(zip(textos[0::2], textos[1::2], strict=False))


def valida_numero(numero: str) -> str:
    if not FAIXA_TESTE.match(numero):
        raise RecusaDeSegurancaError(f"numero fora da faixa sintetica 5511900000xxx: {numero!r}")
    return numero


class Bateria:
    """Envia, le o motor e limpa. Os tres lados (canal, motor, relogio) sao injetaveis para teste."""

    def __init__(
        self,
        *,
        canal: str = CANAL_PADRAO,
        motor: str = MOTOR_PADRAO,
        http: Http = http_json,
        dormir: Callable[[float], None] = time.sleep,
        agora: Callable[[], float] = time.monotonic,
    ) -> None:
        self._canal = canal.rstrip("/")
        self._motor = motor.rstrip("/")
        self._http = http
        self._dormir = dormir
        self._agora = agora
        self._ultimo_envio = -1e9
        self._ultimo_por_numero: dict[str, float] = {}
        self.canceladas: list[str] = []

    # ------------------------------------------------------------------ motor
    def _instancias(self, conversation_id: str) -> list[dict[str, Any]]:
        """As instancias VIVAS desta conversa — por `businessKey=`, o filtro que o motor respeita."""
        chave = urllib.parse.quote(f"ESC-{TENANT}-{conversation_id}", safe="")
        status, corpo = self._http("GET", f"{self._motor}/process-instance?businessKey={chave}", None, 20.0)
        return list(corpo) if status == 200 and isinstance(corpo, list) else []

    def _variaveis(self, instance_id: str) -> dict[str, Any]:
        status, corpo = self._http(
            "GET",
            f"{self._motor}/variable-instance?processInstanceIdIn={instance_id}&deserializeValues=false",
            None,
            20.0,
        )
        if status != 200 or not isinstance(corpo, list):
            return {}
        achadas = {v["name"]: v.get("value") for v in corpo if isinstance(v, dict) and "name" in v}
        valor_roteamento = achadas.get("roteamento")
        roteamento = decodifica_roteamento(valor_roteamento) if isinstance(valor_roteamento, str) else {}
        referencia = str(achadas.get("dmn_decision_ref") or "").split("#")[0] or None
        return {
            "grupo": roteamento.get("grupo_atendimento"),
            "prioridade": roteamento.get("prioridade"),
            "sla_ack": roteamento.get("sla_ack"),
            "sla_resolucao": roteamento.get("sla_resolucao"),
            "motivo": achadas.get("motivo_categoria"),
            "severidade": achadas.get("severidade"),
            "tabela": referencia,
        }

    def cancelar(self, conversation_id: str) -> str:
        """Cancela a instancia desta conversa, SE e somente se houver exatamente uma e a chave bater."""
        esperada = f"ESC-{TENANT}-{conversation_id}"
        achadas = self._instancias(conversation_id)
        if not achadas:
            return "nada_a_cancelar"
        if len(achadas) != 1 or achadas[0].get("businessKey") != esperada:
            raise RecusaDeSegurancaError(
                f"a consulta por {esperada!r} devolveu {len(achadas)} instancia(s); "
                "o script so' cancela quando devolve exatamente uma com a chave esperada"
            )
        iid = achadas[0]["id"]
        status, _ = self._http(
            "DELETE",
            f"{self._motor}/process-instance/{iid}?skipCustomListeners=true&skipIoMappings=true",
            None,
            30.0,
        )
        self.canceladas.append(iid)
        return f"cancelado_{status}"

    # ------------------------------------------------------------------ canal
    def _esperar_ritmo(self, numero: str) -> None:
        agora = self._agora()
        ate = max(
            self._ultimo_envio + INTERVALO_ENTRE_ENVIOS_S,
            self._ultimo_por_numero.get(numero, -1e9) + INTERVALO_NA_CONVERSA_S,
        )
        if ate > agora:
            self._dormir(ate - agora)

    def enviar(self, numero: str, texto: str) -> dict[str, Any]:
        valida_numero(numero)
        self._esperar_ritmo(numero)
        inicio = self._agora()
        status, corpo = self._http(
            "POST", f"{self._canal}/receptor/simular", {"telefone": numero, "texto": texto}, 130.0
        )
        self._ultimo_envio = self._ultimo_por_numero[numero] = self._agora()
        corpo = corpo if isinstance(corpo, dict) else {}
        return {
            "status": status,
            "resposta": corpo.get("resposta"),
            "conversation_id": corpo.get("conversation_id"),
            "segundos": round(self._agora() - inicio, 1),
        }

    # ------------------------------------------------------------------ caso
    def rodar_caso(self, caso: Mapping[str, Any], numero_inicial: int) -> tuple[list[dict[str, Any]], int]:
        """Roda um caso e devolve `(linhas, proximo_numero)`. Cancela o que abriu, sempre."""
        linhas: list[dict[str, Any]] = []
        numero = numero_inicial
        conversas: list[str] = []
        usados: list[str] = []
        try:
            for posicao, mensagem in enumerate(caso["msgs"], 1):
                if posicao == 1 or caso.get("fresh"):
                    telefone = f"5511900000{numero:03d}"
                    numero += 1
                usados.append(telefone)
                if mensagem["texto"] == "CANCELAR":
                    for conv in conversas:
                        linhas.append(
                            {"caso": caso["id"], "acao": "cancelar_entre", "resultado": self.cancelar(conv)}
                        )
                    self._dormir(float(mensagem.get("espera_s", 2)))
                    continue
                envio = self.enviar(telefone, mensagem["texto"])
                conv = envio["conversation_id"]
                if conv and conv not in conversas:
                    conversas.append(conv)
                self._dormir(float(mensagem.get("espera_s", 1)))
                instancias = self._instancias(conv) if conv else []
                linha = {
                    "caso": caso["id"],
                    "msg": posicao,
                    "telefone_final": telefone[-3:],
                    "texto": mensagem["texto"][:80],
                    **envio,
                    "abriu": bool(instancias),
                    "motor": self._variaveis(instancias[0]["id"]) if instancias else None,
                }
                linha.pop("conversation_id")
                linhas.append(linha)
        finally:
            for conv in conversas:
                try:
                    linhas.append({"caso": caso["id"], "acao": "limpeza", "resultado": self.cancelar(conv)})
                except RecusaDeSegurancaError as erro:
                    linhas.append({"caso": caso["id"], "acao": "limpeza", "resultado": f"RECUSADO: {erro}"})
        linhas.append({"caso": caso["id"], "acao": "veredito_de_rota", **veredito_de_rota(caso, linhas)})
        return linhas, numero


def veredito_de_rota(caso: Mapping[str, Any], linhas: list[dict[str, Any]]) -> dict[str, Any]:
    """Compara so' a ROTA do ultimo atendimento aberto com `espera` — nunca o texto da resposta."""
    espera = caso.get("espera") or {}
    abertas = [linha for linha in linhas if linha.get("abriu") and linha.get("motor")]
    observado = abertas[-1]["motor"] if abertas else None
    esperado_prioridade = espera.get("prioridade")
    esperado_grupo = espera.get("grupo")
    if esperado_prioridade is None:
        bate = observado is None
    else:
        bate = (
            observado is not None
            and observado.get("prioridade") == esperado_prioridade
            and (esperado_grupo is None or observado.get("grupo") == esperado_grupo)
        )
    return {
        "esperado": {"prioridade": esperado_prioridade, "grupo": esperado_grupo},
        "observado": None
        if observado is None
        else {k: observado.get(k) for k in ("prioridade", "grupo", "motivo")},
        "bate": bate,
    }


def carregar_casos(caminho: Path, ids: list[str] | None) -> list[dict[str, Any]]:
    casos = json.loads(caminho.read_text(encoding="utf-8"))["casos"]
    if ids:
        faltando = sorted(set(ids) - {c["id"] for c in casos})
        if faltando:
            raise SystemExit(f"casos inexistentes: {faltando}")
        casos = [c for c in casos if c["id"] in ids]
    return list(casos)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--saida", type=Path, default=Path("bateria_helena.jsonl"))
    parser.add_argument("--casos", nargs="*", help="ids dos casos (padrao: todos)")
    parser.add_argument("--arquivo-de-casos", type=Path, default=CASOS_PADRAO)
    parser.add_argument("--canal", default=CANAL_PADRAO)
    parser.add_argument("--motor", default=MOTOR_PADRAO)
    args = parser.parse_args(argv)

    casos = carregar_casos(args.arquivo_de_casos, args.casos)
    bateria = Bateria(canal=args.canal, motor=args.motor)
    numero = PRIMEIRO_NUMERO
    divergentes: list[str] = []
    with args.saida.open("w", encoding="utf-8", newline="\n") as arquivo:
        for caso in casos:
            linhas, numero = bateria.rodar_caso(caso, numero)
            for linha in linhas:
                arquivo.write(json.dumps(linha, ensure_ascii=False, sort_keys=True) + "\n")
            arquivo.flush()
            if not linhas[-1]["bate"]:
                divergentes.append(caso["id"])
    print(f"bateria_helena: {len(casos)} casos, {len(divergentes)} com rota diferente da esperada")
    for cid in divergentes:
        print(f"  {cid}")
    print(f"bateria_helena: JSONL em {args.saida}; instancias canceladas: {len(bateria.canceladas)}")
    return 1 if divergentes else 0


if __name__ == "__main__":
    sys.exit(main())
