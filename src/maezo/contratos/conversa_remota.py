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
  constante. ANTI-REPLAY (revisao de seguranca da 4b): o PEDIDO assina
  `VERSAO\\nPOST\\n<rota>\\n<carimbo>\\n<nonce>\\n` + corpo (`X-Maezo-Carimbo` em segundos Unix,
  `X-Maezo-Nonce`
  aleatorio); quem recebe recusa desvio de relogio > 60 s e nonce ja' visto (`GuardaDeNonces`, 60 s), entao
  um pedido capturado nao vale de novo nem em outra rota. A RESPOSTA assina
  `VERSAO\\nRESPOSTA\\n<assinatura do pedido>\\n` + corpo: uma resposta so' vale para o pedido que a originou.

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
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any, Final

#: Versao do protocolo; muda junto nos dois lados.
VERSAO: Final[str] = "maezo-agente-remoto-v1"
#: Contexto da derivacao da chave (separacao de dominio da `PHI_HMAC_KEY`).
CONTEXTO_DA_CHAVE: Final[bytes] = b"maezo-agente-remoto-v1"
#: O cabecalho com a assinatura hexadecimal do corpo.
CABECALHO_ASSINATURA: Final[str] = "X-Maezo-Assinatura"
#: Segundos Unix (inteiro) da emissao do pedido.
CABECALHO_CARIMBO: Final[str] = "X-Maezo-Carimbo"
#: Valor aleatorio unico por pedido.
CABECALHO_NONCE: Final[str] = "X-Maezo-Nonce"
#: A versao do protocolo (`VERSAO`), conferida explicitamente antes da assinatura (que tambem a cobre).
CABECALHO_VERSAO: Final[str] = "X-Maezo-Versao"
#: Desvio maximo de relogio aceito, e o prazo em que um nonce visto continua recusado.
DESVIO_MAXIMO_S: Final[int] = 60
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


class AgenteRemotoIndisponivelError(RuntimeError):
    """O servico do agente (ou o receptor, no envio) nao respondeu, respondeu fora do protocolo ou sem
    assinatura valida. Falha EXTERNA: o despachante responde ao beneficiario com o texto fixo de
    indisponibilidade em vez de deixa-lo sem resposta."""


#: Endereco de retorno aceito (o receptor que abriu o turno): `http(s)://host[:porta]`, sem caminho.
_FORMATO_DO_RETORNO: Final[str] = r"^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?$"


def retorno_valido(retorno: Any) -> bool:
    """`True` para um endereco de retorno no formato aceito (sem caminho, consulta nem credencial)."""
    return isinstance(retorno, str) and re.fullmatch(_FORMATO_DO_RETORNO, retorno) is not None


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


# --- anti-replay: pedido amarrado a rota/carimbo/nonce, resposta amarrada ao pedido -----------------------
def material_do_pedido(rota: str, carimbo: str, nonce: str, corpo: bytes) -> bytes:
    """O que o PEDIDO assina: versao, metodo, rota, carimbo e nonce, e o corpo."""
    return f"{VERSAO}\nPOST\n{rota}\n{carimbo}\n{nonce}\n".encode() + corpo


def material_da_resposta(assinatura_do_pedido: str, corpo: bytes) -> bytes:
    """O que a RESPOSTA assina: a assinatura do pedido que a originou, e o corpo."""
    return f"{VERSAO}\nRESPOSTA\n{assinatura_do_pedido}\n".encode() + corpo


def cabecalhos_do_pedido(
    chave: bytes, rota: str, corpo: bytes, *, relogio: Callable[[], float] = time.time
) -> dict[str, str]:
    """Carimbo, nonce novo e assinatura de um pedido a `rota`."""
    carimbo = str(int(relogio()))
    nonce = secrets.token_hex(16)
    return {
        CABECALHO_VERSAO: VERSAO,
        CABECALHO_CARIMBO: carimbo,
        CABECALHO_NONCE: nonce,
        CABECALHO_ASSINATURA: assinar(chave, material_do_pedido(rota, carimbo, nonce, corpo)),
        "content-type": TIPO_DO_CORPO,
    }


def assinar_resposta(chave: bytes, assinatura_do_pedido: str, corpo: bytes) -> str:
    return assinar(chave, material_da_resposta(assinatura_do_pedido, corpo))


def resposta_valida(chave: bytes, assinatura_do_pedido: str, corpo: bytes, assinatura: str | None) -> bool:
    """A resposta veio assinada para ESTE pedido (uma resposta de outro pedido e' recusada)."""
    return assinatura_valida(chave, material_da_resposta(assinatura_do_pedido, corpo), assinatura)


class GuardaDeNonces:
    """Nonces vistos nos ultimos `prazo_s` segundos (LRU por idade, com teto de itens). So' em memoria.

    Um nonce repetido dentro do prazo e' recusado; fora do prazo o carimbo do pedido ja' o recusa. O teto
    (`maximo`) limita a memoria sob abuso: cheio, o mais velho sai antes do prazo — o carimbo continua
    valendo.
    """

    def __init__(
        self,
        *,
        prazo_s: float = DESVIO_MAXIMO_S,
        maximo: int = 100_000,
        relogio: Callable[[], float] = time.time,
    ) -> None:
        self._prazo_s = prazo_s
        self._maximo = maximo
        self._relogio = relogio
        self._vistos: OrderedDict[str, float] = OrderedDict()

    def registrar(self, nonce: str) -> bool:
        """`True` se `nonce` e' novo (e passa a ser visto); `False` se ja' foi visto dentro do prazo."""
        agora = self._relogio()
        while self._vistos:
            mais_velho, quando = next(iter(self._vistos.items()))
            if agora - quando < self._prazo_s and len(self._vistos) < self._maximo:
                break
            del self._vistos[mais_velho]
        if nonce in self._vistos:
            return False
        self._vistos[nonce] = agora
        return True


def conferir_pedido(
    chave: bytes,
    guarda: GuardaDeNonces,
    rota: str,
    cabecalhos: Mapping[str, str],
    corpo: bytes,
    *,
    relogio: Callable[[], float] = time.time,
) -> str | None:
    """A assinatura do pedido se ele e' autentico, fresco e inedito; senao `None` (recusa sem detalhe).

    Ordem: assinatura (rota/carimbo/nonce/corpo) -> desvio de relogio -> nonce. O nonce so' e' registrado
    depois da assinatura valida, para um pedido forjado nao "queimar" nonce de ninguem.
    """
    if cabecalhos.get(CABECALHO_VERSAO) != VERSAO:
        return None
    assinatura = cabecalhos.get(CABECALHO_ASSINATURA)
    carimbo = cabecalhos.get(CABECALHO_CARIMBO) or ""
    nonce = cabecalhos.get(CABECALHO_NONCE) or ""
    if not assinatura or not carimbo.isdigit() or not nonce or len(nonce) > 128 or not nonce.isascii():
        return None
    if not assinatura_valida(chave, material_do_pedido(rota, carimbo, nonce, corpo), assinatura):
        return None
    if abs(relogio() - int(carimbo)) > DESVIO_MAXIMO_S:
        return None
    if not guarda.registrar(nonce):
        return None
    return assinatura.strip()


__all__ = [
    "ADIADO_INICIAL",
    "ADIADO_MEMORIA_NEUTRA",
    "ADIADO_RETOMADA",
    "AgenteRemotoIndisponivelError",
    "CABECALHO_ASSINATURA",
    "CABECALHO_CARIMBO",
    "CABECALHO_NONCE",
    "CABECALHO_VERSAO",
    "CONTEXTO_DA_CHAVE",
    "DESVIO_MAXIMO_S",
    "ESTADO_ADIADO",
    "GuardaDeNonces",
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
    "assinar_resposta",
    "assinatura_valida",
    "cabecalhos_do_pedido",
    "codificar",
    "conferir_pedido",
    "decodificar",
    "derivar_chave",
    "estado_adiado",
    "material_da_resposta",
    "material_do_pedido",
    "resposta_valida",
    "retorno_valido",
]
