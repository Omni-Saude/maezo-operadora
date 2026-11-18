"""ROTA DE RESIDENCIA PROPRIA: `experimento_global_autorizado_dpo_2026-10-17` (ADR-0065, DL-0119, DL-0155).

O QUE E'. O transporte que manda texto de conversa de beneficiario para um modelo pelo perfil GLOBAL do
Bedrock (`MODELO_HAIKU_GLOBAL`), que PODE PROCESSAR FORA DO BRASIL. O dono (CTO e DPO) autorizou o desvio de
residencia em 17/10/2026 para a redacao de saude da Helena, so' em dev e como teste reversivel; depois
(26/10/2026, DL-0155; 27/10 e 02/11/2026, limpeza da saude) o uso cresceu para o ROTEADOR v7, os passos do
protocolo, o CLASSIFY e a LEITURA da porta unica de saude (`agents/helena/modelo_allowlist.py`,
`agents/helena/habilidades/saude_original/fluxo.py`), nas conversas da lista ou, com
`MAEZO_HELENA_ROTA_GLOBAL_TODAS`, em TODAS as conversas keyed do processo.

QUEM CONSTROI. So' a raiz de composicao do receptor (`platform/webhooks/service.py::_rota_global`), quando
ha' lista de conversas (`MAEZO_HELENA_SAUDE_GERATIVA_CONVERSAS`) ou a chave de todas. Em modo de producao
(`runtime_mode` diferente de `local`) ela so' constroi com uma autorizacao registrada
(`_autorizacao_da_rota_global`, DL-0192); sem ela, nao constroi. Nao e' uma zona do roteador de inferencia
(`MAEZO_INFERENCE_PROVIDER`/`phi=True`): o transporte regional da zona PHI (`br_regional.py`) nao sabe desta
rota, e o `phi=True` de quem chama nao a impede (o adaptador `ProvedorHaikuGlobal` o ignora).

O QUE VAI NA CHAMADA. O prompt de sistema fixo de quem chama e o texto que ele montou: instrucoes, a
mensagem da pessoa e, conforme o chamador, o historico curto da conversa. Este modulo NAO minimiza nada; os
dois chamadores de hoje passam o texto por `modelo_allowlist.minimizar_prompt`, uma mascara por padrao
(e-mail, nomes conhecidos e de apresentacao, numeros longos, identificadores curtos) que NAO tira todo dado
pessoal nem o conteudo de saude, que e' justamente o que vai. Nada de conteudo vai para log: so' rota, modelo,
latencia, tokens e o codigo da falha.

COMO DESLIGAR. Lista vazia e `helena_rota_global_todas = false` (tfvars do dev) + apply direcionado: sem
lista e sem a chave, a composicao nem constroi este transporte. Runbook: `docs/runbooks/helena-base-saude.md`.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import structlog

from maezo.runtime.dependency_failures import PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

#: O rotulo da rota — vai em todo log desta chamada e no ADR/DL. NUNCA reaproveitar para outra coisa.
ROTA_DE_RESIDENCIA: Final[str] = "experimento_global_autorizado_dpo_2026-10-17"
#: O UNICO modelo admitido nesta rota (perfil GLOBAL de inferencia do Bedrock).
MODELO_HAIKU_GLOBAL: Final[str] = "global.anthropic.claude-haiku-5-5"
#: A regiao em que o pedido e' ASSINADO (o perfil global roteia para onde quiser a partir dela).
REGIAO_DO_CLIENTE: Final[str] = "sa-east-1"
#: Os Claude 5.x RECUSAM `temperature` (medido no experimento de 17/10/2026); esforco BAIXO, como no
#: experimento (sem ele o Haiku 5.5 gastava o teto pensando).
CAMPOS_ADICIONAIS: Final[Mapping[str, Any]] = {"output_config": {"effort": "low"}}
#: Classificacao curta (roteador, protocolo, classify da allowlist, 26/10/2026): o Haiku 5.5 aceita o thinking
#: DESLIGADO com esforco ate' `high`; medido na rota: mediana 2,2 s -> 1,5 s e sem os picos de pensamento.
CAMPOS_SEM_PENSAMENTO: Final[Mapping[str, Any]] = {**CAMPOS_ADICIONAIS, "thinking": {"type": "disabled"}}
PRAZO_PADRAO_S: Final[float] = 15.0

#: Codigos de falha (vocabulario fechado, vao para log e para quem chama).
FALHA_PRAZO: Final[str] = "prazo"
FALHA_SERVICO: Final[str] = "servico"
FALHA_RESPOSTA_VAZIA: Final[str] = "resposta_vazia"


class FalhaDaRotaGlobalError(RuntimeError):
    """A chamada nao rendeu texto (subclasse de `RuntimeError`: cai em `EXTERNAL_DEPENDENCY_FAILURES`).
    `codigo` e' do vocabulario fechado acima (+ o nome da classe do erro do
    SDK, sem mensagem: a mensagem do SDK pode ecoar o prompt)."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(f"rota global experimental: {codigo}")


@dataclass(frozen=True)
class RespostaDaRotaGlobal:
    texto: str
    parada: str
    entrada: int
    saida: int
    latencia_ms: int


class TransporteGlobalExperimental:
    """Bedrock Converse para `MODELO_HAIKU_GLOBAL`, assinado em sa-east-1, sem repeticao automatica (quem
    chama tem a resposta segura padrao). `client` injetavel para teste."""

    rota: Final[str] = ROTA_DE_RESIDENCIA

    def __init__(
        self,
        *,
        modelo: str = MODELO_HAIKU_GLOBAL,
        prazo_s: float = PRAZO_PADRAO_S,
        client: Any = None,
    ) -> None:
        if modelo != MODELO_HAIKU_GLOBAL:
            # A autorizacao do DPO e' para ESTE modelo; outro id exige nova decisao (ADR-0065).
            raise ValueError(f"rota {ROTA_DE_RESIDENCIA}: so' o modelo {MODELO_HAIKU_GLOBAL} e' admitido")
        self.modelo = modelo
        self._prazo_s = prazo_s
        self._campos: Mapping[str, Any] = CAMPOS_ADICIONAIS
        if client is not None:
            self._client = client
        else:
            import boto3  # type: ignore[import-untyped]  # extra [bedrock] opcional
            from botocore.config import Config  # type: ignore[import-untyped]

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=REGIAO_DO_CLIENTE,
                config=Config(
                    read_timeout=prazo_s,
                    connect_timeout=min(prazo_s, 5.0),
                    retries={"max_attempts": 1, "mode": "standard"},
                ),
            )

    def com_campos(self, campos: Mapping[str, Any]) -> TransporteGlobalExperimental:
        """O MESMO transporte (cliente, modelo, prazo, rota) com outros campos do pedido (ex.: o thinking
        desligado da classificacao curta)."""
        irmao = copy.copy(self)
        irmao._campos = dict(campos)
        return irmao

    async def conversar(
        self, sistema: str, mensagens: Sequence[Mapping[str, Any]], *, max_tokens: int
    ) -> RespostaDaRotaGlobal:
        inicio = time.monotonic()
        try:
            bruto = await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.converse,
                    modelId=self.modelo,
                    system=[{"text": sistema}],
                    messages=[dict(m) for m in mensagens],
                    inferenceConfig={"maxTokens": max_tokens},
                    additionalModelRequestFields=dict(self._campos),
                ),
                timeout=self._prazo_s + 2.0,
            )
        except PROGRAMMING_ERRORS:
            raise
        except TimeoutError as exc:
            self._registrar_falha(FALHA_PRAZO, inicio)
            raise FalhaDaRotaGlobalError(FALHA_PRAZO) from exc
        except Exception as exc:  # nenhum erro do SDK escapa desta rota (so' a classe, nunca a mensagem)
            codigo = f"{FALHA_SERVICO}:{type(exc).__name__}"
            self._registrar_falha(codigo, inicio)
            raise FalhaDaRotaGlobalError(codigo) from exc
        latencia = int((time.monotonic() - inicio) * 1000)
        blocos = ((bruto or {}).get("output") or {}).get("message", {}).get("content") or []
        texto = "\n".join(
            str(b["text"]) for b in blocos if isinstance(b, Mapping) and isinstance(b.get("text"), str)
        ).strip()
        uso = (bruto or {}).get("usage") or {}
        resposta = RespostaDaRotaGlobal(
            texto=texto,
            parada=str((bruto or {}).get("stopReason") or ""),
            entrada=int(uso.get("inputTokens") or 0),
            saida=int(uso.get("outputTokens") or 0),
            latencia_ms=latencia,
        )
        logger.info(
            "rota_global_experimental_chamada",
            rota=ROTA_DE_RESIDENCIA,
            modelo=self.modelo,
            latencia_ms=latencia,
            entrada=resposta.entrada,
            saida=resposta.saida,
            parada=resposta.parada,
        )
        if not texto:
            raise FalhaDaRotaGlobalError(FALHA_RESPOSTA_VAZIA)
        return resposta

    def _registrar_falha(self, codigo: str, inicio: float) -> None:
        logger.warning(
            "rota_global_experimental_falhou",
            rota=ROTA_DE_RESIDENCIA,
            modelo=self.modelo,
            falha=codigo,
            latencia_ms=int((time.monotonic() - inicio) * 1000),
        )


__all__ = [
    "CAMPOS_ADICIONAIS",
    "FALHA_PRAZO",
    "FALHA_RESPOSTA_VAZIA",
    "FALHA_SERVICO",
    "MODELO_HAIKU_GLOBAL",
    "REGIAO_DO_CLIENTE",
    "ROTA_DE_RESIDENCIA",
    "FalhaDaRotaGlobalError",
    "RespostaDaRotaGlobal",
    "TransporteGlobalExperimental",
]
