"""O agente de ENTRADA visto de longe: `AgenteDeEntradaRemoto` implementa `AgenteDeEntrada` por HTTP interno.

ADR-0067 fase 4b. O despachante do WhatsApp (`HelenaDispatcher`) continua falando so' com a porta; com
`MAEZO_AGENTE_DE_ENTRADA_URL` definida ele recebe ESTE objeto em vez da Helena em processo, e cada operacao da
porta vira uma chamada assinada ao servico do agente (`python -m maezo.platform.agente_remoto`), que executa a
mesma operacao, em processo, com as dependencias compostas como no receptor.

O que viaja e o que nao viaja
-----------------------------
* Do `config` do grafo so' viaja o que o servico do agente nao recompoe sozinho: `roteador_lucas_enabled`
  (o Lucas e o roteador so' existem no receptor) e o REMETENTE do turno. O remetente NUNCA viaja: ele
  fecha sobre o numero cru do turno. O cliente o guarda num `RegistroDeRemetentes` em memoria, sob um token
  aleatorio de 128 bits com prazo, e manda so' o token; o envio volta ao receptor por
  `POST /interno/agentes/envio` com o token (`rota_de_envio.py`).
* O resto do `config` (inferencia, DMN, motor, auditoria, chaves `helena_*`) o servico do agente compoe das
  proprias settings, do mesmo jeito que o receptor (`webhooks/service.py::build_agent_dependencies`).
* `estado_inicial`/`estado_de_retomada`/`memoria_neutra` sao sincronos na porta: aqui viram um marcador
  ADIADO que o servico materializa (ver `maezo.contratos.conversa_remota`), para nao bloquear o laco do
  receptor numa chamada HTTP sincrona.
* `eh_pedido_explicito_de_pessoa` tambem e' sincrono, e o despachante precisa do booleano na hora. O
  `ainvoke` do turno ja' devolve a resposta para o texto daquele turno (e' o caso de uso real: o pedido de
  pessoa junto da cobranca, avaliado sobre a MESMA mensagem); o cliente a guarda num cache curto pelo
  digest do texto. Fora do cache (o atalho do recuo, sem `ainvoke`), uma chamada sincrona curta.
* `resposta_de_cadastro`: `sinal_de_emergencia` e' do receptor e puro — vai avaliado (booleano). O
  perfil da AMH (`resolver_identidade`) so' e' resolvido se o servico PEDIR: a primeira chamada vai sem
  ele; se o tipo da pergunta precisar, o servico responde `precisa_identidade` e o cliente resolve e
  chama de novo com o perfil (um round-trip a mais, so' nesse caso).

Falhas
------
Rede, prazo, resposta sem assinatura valida ou status inesperado viram `AgenteRemotoIndisponivelError`
(subclasse de `RuntimeError`, uma falha EXTERNA para `maezo.runtime.dependency_failures`). Uma excecao
levantada pela operacao LA' volta so' com o NOME do tipo (nunca a mensagem, que pode carregar dado): os
tipos embutidos de erro de programacao/validacao voltam como o proprio tipo (para `PROGRAMMING_ERRORS`
continuar valendo no despachante), o resto como `FalhaNoAgenteRemotoError` (`RuntimeError`).
"""

from __future__ import annotations

import builtins
import hashlib
import os
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx
import structlog

from maezo.contratos import conversa_remota as fio
from maezo.contratos.conversa import RemetenteDoTurno, RespostaDeCadastro

logger = structlog.get_logger(__name__)

#: A chave de onde a chave de assinatura e' derivada (os dois servicos ja' a tem).
ENV_PHI_HMAC_KEY: Final[str] = "PHI_HMAC_KEY"
#: Prazo (s) das operacoes do turno (o `ainvoke` inclui as chamadas de modelo do turno inteiro).
ENV_PRAZO_S: Final[str] = "MAEZO_AGENTE_DE_ENTRADA_PRAZO_S"
PRAZO_PADRAO_S: Final[float] = 120.0
#: Prazo (s) da unica chamada sincrona (pedido explicito de pessoa fora do cache).
PRAZO_SINCRONO_S: Final[float] = 5.0
#: Validade de um token de remetente. Um turno (com as escalas) cabe com folga; vencido, o envio e' recusado.
TTL_DO_REMETENTE_S: Final[float] = 900.0
_TAMANHO_DO_CACHE_DE_PEDIDO: Final[int] = 256

#: Tipos de erro que voltam como eles mesmos (os de programacao/validacao que o despachante distingue).
_ERROS_EMBUTIDOS: Final[dict[str, type[Exception]]] = {
    nome: getattr(builtins, nome)
    for nome in (
        "TypeError",
        "ValueError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "AssertionError",
        "NotImplementedError",
        "LookupError",
        "PermissionError",
    )
}


class AgenteRemotoIndisponivelError(RuntimeError):
    """O servico do agente (ou o receptor, no envio) nao respondeu, respondeu fora do protocolo ou sem
    assinatura valida. Falha externa: o despachante a trata como trata um provedor fora do ar."""


class FalhaNoAgenteRemotoError(RuntimeError):
    """A operacao levantou do outro lado. So' o NOME do tipo atravessa (`tipo`), nunca a mensagem."""

    def __init__(self, tipo: str) -> None:
        super().__init__(f"falha no agente remoto ({tipo})")
        self.tipo = tipo


def erro_remoto(tipo: str) -> Exception:
    """A excecao local equivalente ao tipo levantado do outro lado (ver docstring do modulo)."""
    embutido = _ERROS_EMBUTIDOS.get(tipo)
    if embutido is not None:
        return embutido(f"falha no agente remoto ({tipo})")
    return FalhaNoAgenteRemotoError(tipo)


def corpo_de_erro(exc: BaseException) -> dict[str, Any]:
    """O corpo de erro do protocolo: so' o nome do tipo."""
    return {"erro": {"tipo": type(exc).__name__}}


class RegistroDeRemetentes:
    """Remetentes de turnos em curso, por token aleatorio de 128 bits, com prazo. So' em memoria.

    O remetente fecha sobre o numero cru do turno e nunca sai do processo do receptor; o token e' a unica
    coisa que o servico do agente ve'. Um token vencido (ou de outra replica) e' simplesmente desconhecido.
    """

    def __init__(self, *, ttl_s: float = TTL_DO_REMETENTE_S, relogio: Callable[[], float] = time.monotonic):
        if ttl_s <= 0:
            raise ValueError("registro de remetentes: ttl_s deve ser positivo")
        self._ttl_s = ttl_s
        self._relogio = relogio
        self._itens: dict[str, tuple[float, RemetenteDoTurno]] = {}

    def registrar(self, remetente: RemetenteDoTurno) -> str:
        agora = self._relogio()
        self._expurgar(agora)
        token = secrets.token_urlsafe(16)
        self._itens[token] = (agora + self._ttl_s, remetente)
        return token

    def resolver(self, token: str) -> RemetenteDoTurno | None:
        agora = self._relogio()
        item = self._itens.get(token)
        if item is None:
            return None
        vence_em, remetente = item
        if agora >= vence_em:
            self._itens.pop(token, None)
            return None
        return remetente

    def __len__(self) -> int:
        return len(self._itens)

    def _expurgar(self, agora: float) -> None:
        vencidos = [t for t, (vence_em, _) in self._itens.items() if agora >= vence_em]
        for token in vencidos:
            del self._itens[token]


def _cabecalhos(chave: bytes, corpo: bytes) -> dict[str, str]:
    return {fio.CABECALHO_ASSINATURA: fio.assinar(chave, corpo), "content-type": fio.TIPO_DO_CORPO}


def ler_resposta(resposta: httpx.Response, chave: bytes, rota: str) -> Any:
    """O `resultado` de uma resposta do protocolo, assinatura conferida. Erro remoto vira a excecao local."""
    if resposta.status_code not in (200, 500, 502):
        raise AgenteRemotoIndisponivelError(f"{rota}: status {resposta.status_code}")
    corpo = resposta.content
    if not fio.assinatura_valida(chave, corpo, resposta.headers.get(fio.CABECALHO_ASSINATURA)):
        raise AgenteRemotoIndisponivelError(f"{rota}: resposta sem assinatura valida")
    try:
        conteudo = fio.decodificar(corpo)
    except ValueError as exc:
        raise AgenteRemotoIndisponivelError(f"{rota}: corpo fora do protocolo") from exc
    if not isinstance(conteudo, dict):
        raise AgenteRemotoIndisponivelError(f"{rota}: corpo fora do protocolo")
    if resposta.status_code != 200:
        erro = conteudo.get("erro")
        tipo = erro.get("tipo") if isinstance(erro, dict) else None
        if not isinstance(tipo, str):
            raise AgenteRemotoIndisponivelError(f"{rota}: erro fora do protocolo")
        raise erro_remoto(tipo)
    return conteudo.get("resultado")


async def postar(http: httpx.AsyncClient, chave: bytes, rota: str, pedido: Mapping[str, Any]) -> Any:
    """POST assinado de `pedido` em `rota`; devolve o `resultado` da resposta (assinatura conferida)."""
    corpo = fio.codificar(dict(pedido))
    try:
        resposta = await http.post(rota, content=corpo, headers=_cabecalhos(chave, corpo))
    except httpx.HTTPError as exc:
        raise AgenteRemotoIndisponivelError(f"{rota}: {type(exc).__name__}") from exc
    return ler_resposta(resposta, chave, rota)


def cliente_http(url: str, *, prazo_s: float) -> httpx.AsyncClient:
    """O cliente HTTP INTERNO do protocolo (servico a servico, sem proxy do ambiente nem redirecionamento)."""
    return httpx.AsyncClient(base_url=url, timeout=prazo_s, trust_env=False, follow_redirects=False)


def _digest(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class RemetenteRemoto:
    """O remetente do turno do lado do SERVICO DO AGENTE: cada `send` volta ao receptor com o token do turno.

    Implementa `RemetenteDoTurno`. O receptor resolve o token no registro dele e chama o remetente real
    (cercado, com o numero cru) — o numero nunca sai do receptor.
    """

    def __init__(self, *, http: httpx.AsyncClient, chave: bytes, token: str) -> None:
        self._http = http
        self._chave = chave
        self._token = token

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        resultado = await postar(
            self._http, self._chave, fio.ROTA_ENVIO, {"token": self._token, "to_hash": to_hash, "text": text}
        )
        return dict(resultado) if isinstance(resultado, Mapping) else {}


@dataclass(frozen=True, slots=True)
class EstadoDoGrafoRemoto:
    """O que `aget_state` devolve de longe: so' o que o despachante le' (`values`) e o `next`."""

    values: dict[str, Any]
    next: tuple[str, ...] = ()


class _GrafoCompiladoRemoto:
    def __init__(
        self,
        agente: AgenteDeEntradaRemoto,
        *,
        token: str | None,
        roteador_lucas_enabled: bool,
        persistente: bool,
    ) -> None:
        self._agente = agente
        self._base = {
            "token": token,
            "roteador_lucas_enabled": roteador_lucas_enabled,
            "persistente": persistente,
        }

    async def ainvoke(self, state: Mapping[str, Any], config: Any = None) -> dict[str, Any]:
        estado = dict(state)
        resposta = await self._agente.postar(
            fio.ROTA_INVOCAR, {**self._base, "estado": estado, "config": config}
        )
        if not isinstance(resposta, dict) or not isinstance(resposta.get("resultado"), dict):
            raise AgenteRemotoIndisponivelError(f"{fio.ROTA_INVOCAR}: resultado fora do protocolo")
        pedido = resposta.get("pedido_de_pessoa")
        campos = estado.get("campos")
        if (
            isinstance(pedido, bool)
            and isinstance(campos, dict)
            and isinstance(campos.get("message_body"), str)
        ):
            self._agente.lembrar_pedido_de_pessoa(campos["message_body"], pedido)
        resultado: dict[str, Any] = resposta["resultado"]
        return resultado

    async def aget_state(self, config: Any) -> EstadoDoGrafoRemoto:
        resposta = await self._agente.postar(fio.ROTA_LER_ESTADO, {**self._base, "config": config})
        if not isinstance(resposta, dict):
            raise AgenteRemotoIndisponivelError(f"{fio.ROTA_LER_ESTADO}: resultado fora do protocolo")
        valores = resposta.get("values")
        return EstadoDoGrafoRemoto(
            values=dict(valores) if isinstance(valores, dict) else {},
            next=tuple(resposta.get("next") or ()),
        )

    async def aupdate_state(self, config: Any, values: Any, as_node: str | None = None) -> Any:
        return await self._agente.postar(
            fio.ROTA_ATUALIZAR_ESTADO,
            {**self._base, "config": config, "values": dict(values), "as_node": as_node},
        )


class _GrafoRemoto:
    """O grafo-procuracao: `compile()` registra o remetente do turno e devolve o compilado remoto."""

    def __init__(
        self,
        agente: AgenteDeEntradaRemoto,
        *,
        remetente: RemetenteDoTurno | None,
        roteador_lucas_enabled: bool,
    ) -> None:
        self._agente = agente
        self._remetente = remetente
        self._roteador_lucas_enabled = roteador_lucas_enabled

    def compile(self, checkpointer: Any = None) -> _GrafoCompiladoRemoto:
        token = self._agente.registro.registrar(self._remetente) if self._remetente is not None else None
        return _GrafoCompiladoRemoto(
            self._agente,
            token=token,
            roteador_lucas_enabled=self._roteador_lucas_enabled,
            persistente=checkpointer is not None,
        )


class AgenteDeEntradaRemoto:
    """`maezo.contratos.conversa.AgenteDeEntrada` por HTTP interno (ver docstring do modulo)."""

    def __init__(
        self,
        url: str,
        agent_id: str,
        *,
        chave: bytes,
        prazo_s: float = PRAZO_PADRAO_S,
        http: httpx.AsyncClient | None = None,
        http_sincrono: httpx.Client | None = None,
        registro: RegistroDeRemetentes | None = None,
    ) -> None:
        if not url:
            raise ValueError("agente remoto: URL vazia")
        if not chave:
            raise ValueError("agente remoto: chave de assinatura vazia (falha fechada)")
        self.agent_id = agent_id
        #: So' rotulo local: a versao que o grafo carimba e' a do agente, do lado do servico (ela nao viaja).
        self.agent_version = f"{agent_id}@remoto"
        self.registro = registro if registro is not None else RegistroDeRemetentes()
        self._url = url
        self._chave = chave
        self._http = http if http is not None else cliente_http(url, prazo_s=prazo_s)
        self._http_sincrono = http_sincrono
        self._pedidos_de_pessoa: OrderedDict[str, bool] = OrderedDict()

    @classmethod
    def do_ambiente(
        cls, url: str, agent_id: str, *, environ: Mapping[str, str] | None = None
    ) -> AgenteDeEntradaRemoto:
        """Construido das variaveis de ambiente do receptor. Sem `PHI_HMAC_KEY` LEVANTA (falha fechada)."""
        env = os.environ if environ is None else environ
        chave = fio.derivar_chave(env.get(ENV_PHI_HMAC_KEY, ""))
        prazo_bruto = env.get(ENV_PRAZO_S, "").strip()
        prazo_s = float(prazo_bruto) if prazo_bruto else PRAZO_PADRAO_S
        if prazo_s <= 0:
            raise ValueError(f"agente remoto: {ENV_PRAZO_S} deve ser positivo")
        return cls(url, agent_id, chave=chave, prazo_s=prazo_s)

    # --- assinatura (a rota de envio do receptor usa a MESMA chave) ----------------------------------------
    def assinatura_valida(self, corpo: bytes, assinatura: str | None) -> bool:
        return fio.assinatura_valida(self._chave, corpo, assinatura)

    def assinar(self, corpo: bytes) -> str:
        return fio.assinar(self._chave, corpo)

    async def postar(self, rota: str, pedido: Mapping[str, Any]) -> Any:
        return await postar(self._http, self._chave, rota, pedido)

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._http_sincrono is not None:
            self._http_sincrono.close()

    # --- a porta ------------------------------------------------------------------------------------------
    def construir_grafo(self, config: Mapping[str, Any]) -> _GrafoRemoto:
        return _GrafoRemoto(
            self,
            remetente=config.get("whatsapp"),
            roteador_lucas_enabled=config.get("roteador_lucas_enabled") is True,
        )

    def estado_inicial(self, **campos: Any) -> dict[str, Any]:
        return fio.estado_adiado(fio.ADIADO_INICIAL, campos)

    def estado_de_retomada(self, **campos: Any) -> dict[str, Any]:
        return fio.estado_adiado(fio.ADIADO_RETOMADA, campos)

    def memoria_neutra(self) -> dict[str, Any]:
        return fio.estado_adiado(fio.ADIADO_MEMORIA_NEUTRA)

    async def escalar_identidade(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any], *, motivo: str
    ) -> Mapping[str, Any]:
        resultado = await self.postar(
            fio.ROTA_ESCALAR_IDENTIDADE,
            {**self._dependencias(dependencias), "estado": estado, "motivo": motivo},
        )
        if not isinstance(resultado, dict):
            raise AgenteRemotoIndisponivelError(f"{fio.ROTA_ESCALAR_IDENTIDADE}: resultado fora do protocolo")
        return resultado

    async def escalar_e_responder(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any]
    ) -> dict[str, Any]:
        resultado = await self.postar(
            fio.ROTA_ESCALAR_E_RESPONDER, {**self._dependencias(dependencias), "estado": estado}
        )
        if not isinstance(resultado, dict):
            raise AgenteRemotoIndisponivelError(
                f"{fio.ROTA_ESCALAR_E_RESPONDER}: resultado fora do protocolo"
            )
        # Mesma forma do agente em processo: o estado do chamador e' atualizado e devolvido.
        estado.update(resultado)
        return estado

    def eh_pedido_explicito_de_pessoa(self, texto: str) -> bool:
        lembrado = self._pedidos_de_pessoa.get(_digest(texto))
        if lembrado is not None:
            return lembrado
        # Fora do cache: chamada SINCRONA curta (a porta e' sincrona). So' o atalho do recuo chega aqui.
        if self._http_sincrono is None:
            self._http_sincrono = httpx.Client(
                base_url=self._url, timeout=PRAZO_SINCRONO_S, trust_env=False, follow_redirects=False
            )
        corpo = fio.codificar({"texto": texto})
        try:
            resposta = self._http_sincrono.post(
                fio.ROTA_PEDIDO_DE_PESSOA, content=corpo, headers=_cabecalhos(self._chave, corpo)
            )
        except httpx.HTTPError as exc:
            raise AgenteRemotoIndisponivelError(f"{fio.ROTA_PEDIDO_DE_PESSOA}: {type(exc).__name__}") from exc
        resultado = ler_resposta(resposta, self._chave, fio.ROTA_PEDIDO_DE_PESSOA)
        if not isinstance(resultado, bool):
            raise AgenteRemotoIndisponivelError(f"{fio.ROTA_PEDIDO_DE_PESSOA}: resultado fora do protocolo")
        self.lembrar_pedido_de_pessoa(texto, resultado)
        return resultado

    def lembrar_pedido_de_pessoa(self, texto: str, pedido: bool) -> None:
        chave = _digest(texto)
        self._pedidos_de_pessoa[chave] = pedido
        self._pedidos_de_pessoa.move_to_end(chave)
        while len(self._pedidos_de_pessoa) > _TAMANHO_DO_CACHE_DE_PEDIDO:
            self._pedidos_de_pessoa.popitem(last=False)

    async def resposta_de_cadastro(
        self,
        texto: str,
        *,
        consultas_ligadas: bool,
        fonte_ligada: bool,
        sinal_de_emergencia: Callable[[str], bool],
        resolver_identidade: Callable[[], Awaitable[dict[str, Any] | None]] | None,
    ) -> RespostaDeCadastro | None:
        pedido: dict[str, Any] = {
            "texto": texto,
            "consultas_ligadas": consultas_ligadas,
            "fonte_ligada": fonte_ligada,
            # Puro e do receptor: vai avaliado. O agente so' o le' depois de decidir o tipo, mas avaliar antes
            # nao muda nada (sem efeito, sem I/O).
            "emergencia": bool(sinal_de_emergencia(texto)),
            "identidade_disponivel": resolver_identidade is not None,
        }
        resultado = await self.postar(fio.ROTA_RESPOSTA_DE_CADASTRO, pedido)
        if isinstance(resultado, dict) and resultado.get("precisa_identidade") is True:
            if resolver_identidade is None:
                raise AgenteRemotoIndisponivelError(
                    f"{fio.ROTA_RESPOSTA_DE_CADASTRO}: pediu perfil sem fonte"
                )
            identidade = await resolver_identidade()
            resultado = await self.postar(
                fio.ROTA_RESPOSTA_DE_CADASTRO,
                {**pedido, "identidade_resolvida": True, "identidade": identidade},
            )
        if not isinstance(resultado, dict) or "resposta" not in resultado:
            raise AgenteRemotoIndisponivelError(
                f"{fio.ROTA_RESPOSTA_DE_CADASTRO}: resultado fora do protocolo"
            )
        resposta = resultado["resposta"]
        if resposta is None:
            return None
        return RespostaDeCadastro(
            tipo=str(resposta["tipo"]), texto=str(resposta["texto"]), com_dados=resposta["com_dados"] is True
        )

    def _dependencias(self, dependencias: Mapping[str, Any]) -> dict[str, Any]:
        """Das dependencias dos nos so' viajam o token do remetente e a chave do Lucas."""
        remetente = dependencias.get("whatsapp")
        return {
            "token": self.registro.registrar(remetente) if remetente is not None else None,
            "roteador_lucas_enabled": dependencias.get("roteador_lucas_enabled") is True,
        }


__all__ = [
    "AgenteDeEntradaRemoto",
    "AgenteRemotoIndisponivelError",
    "EstadoDoGrafoRemoto",
    "FalhaNoAgenteRemotoError",
    "RegistroDeRemetentes",
    "RemetenteRemoto",
    "cliente_http",
    "corpo_de_erro",
    "erro_remoto",
    "ler_resposta",
    "postar",
]
