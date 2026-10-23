"""O protocolo de fio do agente de ENTRADA remoto (ADR-0067 fase 4b): receptor do WhatsApp <-> `agent-helena`.

A porta `maezo.contratos.conversa.AgenteDeEntrada` continua sendo o contrato; este modulo so' fixa como ela
atravessa a rede quando o despachante fala com a Helena por HTTP interno em vez de em processo. So' stdlib:
os dois lados (plataforma e o servico do agente) o importam sem puxar dependencia nenhuma.

Tres pecas:

* o CODEC: JSON que preserva em ida-e-volta exata o que o estado do turno carrega e o JSON puro perde
  (`tuple`, `set`/`frozenset`, `datetime`/`date`, `bytes`, dict com chave nao-texto). Cada um vai com uma
  etiqueta explicita (`{"__maezo__": "<tipo>", "v": ...}`); tipo desconhecido LEVANTA `TypeError` (nunca vira
  `str` em silencio, que devolveria do outro lado um valor de outro tipo);
* os NOMES das rotas e do cabecalho;
* a ASSINATURA: HMAC-SHA256 do corpo, com chave DERIVADA `hmac(PHI_HMAC_KEY, b"maezo-agente-remoto-v1")`
  (os dois servicos ja' tem a `PHI_HMAC_KEY`; a derivacao separa o uso, entao a assinatura nunca e' um
  pseudonimo valido nem o contrario), em hexadecimal no cabecalho `X-Maezo-Assinatura`, comparada em tempo
  constante.

O estado ADIADO: `estado_inicial`, `estado_de_retomada` e `memoria_neutra` sao SINCRONOS na porta e o
despachante os chama dentro do laco assincrono. Remotos, uma chamada HTTP sincrona ali congelaria o
receptor inteiro enquanto o servico do agente demorasse. Por isso o cliente devolve um marcador
(`ESTADO_ADIADO`) com os campos, e o servico do agente MONTA o estado (pela logica da Helena, so' la') na
operacao do grafo que o consome. Um campo forjado continua levantando `TypeError` — no `ainvoke`, do
outro lado.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import date, datetime
from typing import Any, Final

#: Versao do protocolo; muda junto nos dois lados.
VERSAO: Final[str] = "maezo-agente-remoto-v1"
#: Contexto da derivacao da chave (separacao de dominio da `PHI_HMAC_KEY`).
CONTEXTO_DA_CHAVE: Final[bytes] = b"maezo-agente-remoto-v1"
#: O cabecalho com a assinatura hexadecimal do corpo.
CABECALHO_ASSINATURA: Final[str] = "X-Maezo-Assinatura"
TIPO_DO_CORPO: Final[str] = "application/json"

# --- rotas do SERVICO DO AGENTE (`python -m maezo.platform.agente_remoto`) ---------------------------------
_PREFIXO: Final[str] = "/interno/agente"
ROTA_INVOCAR: Final[str] = f"{_PREFIXO}/grafo/invocar"
ROTA_LER_ESTADO: Final[str] = f"{_PREFIXO}/grafo/ler-estado"
ROTA_ATUALIZAR_ESTADO: Final[str] = f"{_PREFIXO}/grafo/atualizar-estado"
ROTA_ESCALAR_IDENTIDADE: Final[str] = f"{_PREFIXO}/escalar-identidade"
ROTA_ESCALAR_E_RESPONDER: Final[str] = f"{_PREFIXO}/escalar-e-responder"
ROTA_PEDIDO_DE_PESSOA: Final[str] = f"{_PREFIXO}/pedido-de-pessoa"
ROTA_RESPOSTA_DE_CADASTRO: Final[str] = f"{_PREFIXO}/resposta-de-cadastro"

# --- rota do RECEPTOR (o envio do turno remoto volta por ela) -----------------------------------------------
ROTA_ENVIO: Final[str] = "/interno/agentes/envio"

# --- estado adiado -----------------------------------------------------------------------------------------
#: Chave do marcador de estado adiado (ver docstring do modulo).
ESTADO_ADIADO: Final[str] = "__maezo_estado_adiado__"
ADIADO_INICIAL: Final[str] = "inicial"
ADIADO_RETOMADA: Final[str] = "retomada"
ADIADO_MEMORIA_NEUTRA: Final[str] = "memoria_neutra"
TIPOS_ADIADOS: Final[frozenset[str]] = frozenset({ADIADO_INICIAL, ADIADO_RETOMADA, ADIADO_MEMORIA_NEUTRA})


def estado_adiado(tipo: str, campos: dict[str, Any] | None = None) -> dict[str, Any]:
    """O marcador de um estado que o servico do agente monta (ver docstring do modulo)."""
    if tipo not in TIPOS_ADIADOS:
        raise ValueError(f"estado adiado desconhecido: {tipo!r}")
    return {ESTADO_ADIADO: tipo, "campos": dict(campos or {})}


# --- codec -------------------------------------------------------------------------------------------------
_ETIQUETA: Final[str] = "__maezo__"


def _para_fio(valor: Any) -> Any:
    # `bool` antes de `int` nao importa aqui (os dois passam direto), mas `datetime` antes de `date` importa:
    # `datetime` e' subclasse de `date`.
    if valor is None or isinstance(valor, (bool, int, float, str)):
        return valor
    if isinstance(valor, list):
        return [_para_fio(v) for v in valor]
    if isinstance(valor, tuple):
        return {_ETIQUETA: "tuple", "v": [_para_fio(v) for v in valor]}
    if isinstance(valor, frozenset):
        return {_ETIQUETA: "frozenset", "v": [_para_fio(v) for v in valor]}
    if isinstance(valor, set):
        return {_ETIQUETA: "set", "v": [_para_fio(v) for v in valor]}
    if isinstance(valor, datetime):
        return {_ETIQUETA: "datetime", "v": valor.isoformat()}
    if isinstance(valor, date):
        return {_ETIQUETA: "date", "v": valor.isoformat()}
    if isinstance(valor, (bytes, bytearray)):
        return {_ETIQUETA: "bytes", "v": base64.b64encode(bytes(valor)).decode("ascii")}
    if isinstance(valor, dict):
        if all(isinstance(k, str) for k in valor) and _ETIQUETA not in valor:
            return {k: _para_fio(v) for k, v in valor.items()}
        # Chave nao-texto (ou que colide com a etiqueta): pares, para voltar o dict identico.
        return {_ETIQUETA: "dict", "v": [[_para_fio(k), _para_fio(v)] for k, v in valor.items()]}
    raise TypeError(f"tipo nao suportado no fio do agente remoto: {type(valor).__name__}")


def _do_fio(valor: Any) -> Any:
    if isinstance(valor, list):
        return [_do_fio(v) for v in valor]
    if not isinstance(valor, dict):
        return valor
    etiqueta = valor.get(_ETIQUETA)
    if etiqueta is None:
        return {k: _do_fio(v) for k, v in valor.items()}
    bruto: Any = valor.get("v")
    if etiqueta == "tuple":
        return tuple(_do_fio(v) for v in bruto)
    if etiqueta == "set":
        return {_do_fio(v) for v in bruto}
    if etiqueta == "frozenset":
        return frozenset(_do_fio(v) for v in bruto)
    if etiqueta == "datetime":
        return datetime.fromisoformat(bruto)
    if etiqueta == "date":
        return date.fromisoformat(bruto)
    if etiqueta == "bytes":
        return base64.b64decode(bruto.encode("ascii"), validate=True)
    if etiqueta == "dict":
        return {_do_fio(k): _do_fio(v) for k, v in bruto}
    raise ValueError(f"etiqueta desconhecida no fio do agente remoto: {etiqueta!r}")


def codificar(valor: Any) -> bytes:
    """`valor` -> corpo JSON (UTF-8). Tipo sem representacao no fio levanta `TypeError`."""
    return json.dumps(_para_fio(valor), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def decodificar(corpo: bytes) -> Any:
    """Corpo JSON -> valor, com as etiquetas desfeitas. Corpo invalido levanta `ValueError`."""
    return _do_fio(json.loads(corpo.decode("utf-8")))


# --- assinatura --------------------------------------------------------------------------------------------
def derivar_chave(phi_hmac_key: str | bytes) -> bytes:
    """A chave de assinatura: `hmac(PHI_HMAC_KEY, b"maezo-agente-remoto-v1")`. Chave vazia levanta."""
    bruta = phi_hmac_key.encode("utf-8") if isinstance(phi_hmac_key, str) else bytes(phi_hmac_key)
    if not bruta:
        raise ValueError("agente remoto: PHI_HMAC_KEY vazia — sem chave nao ha' assinatura (falha fechada)")
    return hmac.new(bruta, CONTEXTO_DA_CHAVE, hashlib.sha256).digest()


def assinar(chave: bytes, corpo: bytes) -> str:
    """HMAC-SHA256 hexadecimal de `corpo`."""
    return hmac.new(chave, corpo, hashlib.sha256).hexdigest()


def assinatura_valida(chave: bytes, corpo: bytes, assinatura: str | None) -> bool:
    """`True` so' com a assinatura exata de `corpo`; ausente ou malformada = `False`. Tempo constante."""
    if not assinatura:
        return False
    try:
        recebida = assinatura.strip().encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(recebida, assinar(chave, corpo).encode("ascii"))


__all__ = [
    "ADIADO_INICIAL",
    "ADIADO_MEMORIA_NEUTRA",
    "ADIADO_RETOMADA",
    "CABECALHO_ASSINATURA",
    "CONTEXTO_DA_CHAVE",
    "ESTADO_ADIADO",
    "ROTA_ATUALIZAR_ESTADO",
    "ROTA_ENVIO",
    "ROTA_ESCALAR_E_RESPONDER",
    "ROTA_ESCALAR_IDENTIDADE",
    "ROTA_INVOCAR",
    "ROTA_LER_ESTADO",
    "ROTA_PEDIDO_DE_PESSOA",
    "ROTA_RESPOSTA_DE_CADASTRO",
    "TIPOS_ADIADOS",
    "TIPO_DO_CORPO",
    "VERSAO",
    "assinar",
    "assinatura_valida",
    "codificar",
    "decodificar",
    "derivar_chave",
    "estado_adiado",
]
