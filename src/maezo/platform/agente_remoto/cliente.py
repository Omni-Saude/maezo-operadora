"""O agente de ENTRADA visto de longe: `AgenteDeEntradaRemoto` implementa `AgenteDeEntrada` por HTTP interno.

ADR-0067 fase 4b. O despachante do WhatsApp (`HelenaDispatcher`) continua falando so' com a porta; com
`MAEZO_AGENTE_DE_ENTRADA_URL` definida ele recebe ESTE objeto em vez da Helena em processo, e cada operacao da
porta vira uma chamada assinada ao servico do agente (`python -m maezo.platform.agente_remoto`), que executa a
mesma operacao, em processo, com as dependencias compostas como no receptor.

O que viaja e o que nao viaja
-----------------------------
* Do `config` do grafo so' viaja o que o servico do agente nao recompoe sozinho: `roteador_lucas_enabled`
  (o Lucas e o roteador so' existem no receptor) e o REMETENTE do turno. O remetente NUNCA viaja: ele
  fecha sobre o numero cru do turno. Cada operacao o registra num `RegistroDeRemetentes` em memoria, sob
  um token aleatorio de 128 bits com prazo, manda so' o token e o REVOGA quando a operacao termina; o envio
  volta ao receptor por `POST /interno/agentes/envio` (`rota_de_envio.py`).
* O endereco de RETORNO viaja em todo pedido: o token so' existe na replica que abriu o turno, e o nome do
  receptor no Cloud Map e' MULTIVALUE (com deploy 100/200 ou mais de uma replica, o callback pelo nome
  cairia na replica errada = 404 e beneficiario sem resposta). No ECS e' o IP da propria task, lido do
  metadata (`ECS_CONTAINER_METADATA_URI_V4`), na porta do receptor; fora dele, `MAEZO_RECEPTOR_URL`.
* O resto do `config` (inferencia, DMN, motor, auditoria, chaves `helena_*`) o servico do agente compoe das
  proprias settings, do mesmo jeito que o receptor (`webhooks/service.py::build_agent_dependencies`).
* `estado_inicial`/`estado_de_retomada`/`memoria_neutra` sao sincronos na porta: aqui viram um marcador
  ADIADO que o servico materializa (ver `maezo.contratos.conversa_remota`), para nao bloquear o laco do
  receptor numa chamada HTTP sincrona.
* `eh_pedido_explicito_de_pessoa` tambem e' sincrono, e NENHUM I/O sincrono roda aqui: o booleano vem
  na resposta do `ainvoke` do turno (cache curto pelo digest do texto) ou e' antecipado de forma
  assincrona pelo despachante (`antecipar_pedido_de_pessoa`) antes do uso — o caso do atalho do recuo.
* `resposta_de_cadastro`: `sinal_de_emergencia` e' do receptor e puro: vai avaliado (booleano). O
  perfil da AMH (`resolver_identidade`) so' e' resolvido se o servico PEDIR: a primeira chamada vai sem
  ele; se o tipo da pergunta precisar, o servico responde `precisa_identidade` e o cliente resolve e
  chama de novo com o perfil (um round-trip a mais, so' nesse caso).

Falhas
------
Rede, prazo, resposta sem assinatura valida ou status inesperado viram `AgenteRemotoIndisponivelError`
(`maezo.contratos.conversa_remota`, subclasse de `RuntimeError`): no `dispatch` ela vira o texto fixo de
indisponibilidade ao beneficiario. Uma excecao levantada pela operacao LA' volta so' com o NOME do tipo
(nunca a mensagem, que pode carregar dado): os tipos embutidos de erro de programacao/validacao voltam como
o proprio tipo (para `PROGRAMMING_ERRORS` continuar valendo no despachante), o resto como
`FalhaNoAgenteRemotoError` (`RuntimeError`).
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
from maezo.contratos.conversa_remota import AgenteRemotoIndisponivelError

logger = structlog.get_logger(__name__)

#: A chave de onde a chave de assinatura e' derivada (os dois servicos ja' a tem).
ENV_PHI_HMAC_KEY: Final[str] = "PHI_HMAC_KEY"
#: Prazo (s) das operacoes do turno (o `ainvoke` inclui as chamadas de modelo do turno inteiro).
ENV_PRAZO_S: Final[str] = "MAEZO_AGENTE_DE_ENTRADA_PRAZO_S"
#: O metadata da task no ECS (de onde sai o IP proprio do receptor para o retorno).
ENV_METADATA_ECS: Final[str] = "ECS_CONTAINER_METADATA_URI_V4"
#: O endereco de retorno fora do ECS (dev local, testes de integracao).
ENV_RECEPTOR_URL: Final[str] = "MAEZO_RECEPTOR_URL"
#: A porta do receptor (a mesma do `/webhook` e da rota interna de envio).
ENV_PORTA_DO_RECEPTOR: Final[str] = "HEALTH_PORT"
PORTA_DO_RECEPTOR_PADRAO: Final[int] = 8080
PRAZO_PADRAO_S: Final[float] = 120.0
PRAZO_DO_METADATA_S: Final[float] = 2.0
#: Validade de um token de remetente (rede de seguranca: o token e' revogado no fim de cada operacao).
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


class FalhaNoAgenteRemotoError(RuntimeError):
    """A operacao levantou do outro lado. So' o NOME do tipo atravessa (`tipo`), nunca a mensagem."""

    def __init__(self, tipo: str) -> None:
        super().__init__(f"falha no agente remoto ({tipo})")
        self.tipo = tipo


def erro_remoto(tipo: str) -> Exception:
    """A excecao local equivalente ao tipo levantado do outro lado (ver docstring do modulo)."""
    if tipo == AgenteRemotoIndisponivelError.__name__:
        return AgenteRemotoIndisponivelError("falha no agente remoto (dependencia do outro lado)")
    embutido = _ERROS_EMBUTIDOS.get(tipo)
    if embutido is not None:
        return embutido(f"falha no agente remoto ({tipo})")
    return FalhaNoAgenteRemotoError(tipo)


def corpo_de_erro(exc: BaseException) -> dict[str, Any]:
    """O corpo de erro do protocolo: so' o nome do tipo."""
    return {"erro": {"tipo": type(exc).__name__}}


class RegistroDeRemetentes:
    """Remetentes de operacoes em curso, por token aleatorio de 128 bits, com prazo. So' em memoria.

    O remetente fecha sobre o numero cru do turno e nunca sai do processo do receptor; o token e' a unica
    coisa que o servico do agente ve'. Revogado (fim da operacao), vencido ou de outra replica = desconhecido.
    """

    def __init__(
        self, *, ttl_s: float = TTL_DO_REMETENTE_S, relogio: Callable[[], float] = time.monotonic
    ) -> None:
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

    def revogar(self, token: str | None) -> None:
        if token is not None:
            self._itens.pop(token, None)

    def __len__(self) -> int:
        return len(self._itens)

    def _expurgar(self, agora: float) -> None:
        vencidos = [t for t, (vence_em, _) in self._itens.items() if agora >= vence_em]
        for token in vencidos:
            del self._itens[token]


def ler_resposta(resposta: httpx.Response, chave: bytes, rota: str, assinatura_do_pedido: str) -> Any:
    """O `resultado` de uma resposta do protocolo, assinatura conferida CONTRA O PEDIDO que a originou (uma
    resposta de outro pedido e' recusada). Erro remoto vira a excecao local."""
    if resposta.status_code not in (200, 500, 502):
        raise AgenteRemotoIndisponivelError(f"{rota}: status {resposta.status_code}")
    corpo = resposta.content
    if not fio.resposta_valida(
        chave, assinatura_do_pedido, corpo, resposta.headers.get(fio.CABECALHO_ASSINATURA)
    ):
        raise AgenteRemotoIndisponivelError(f"{rota}: resposta sem assinatura valida para este pedido")
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


async def postar(
    http: httpx.AsyncClient, chave: bytes, rota: str, pedido: Mapping[str, Any], *, base: str | None = None
) -> Any:
    """POST assinado de `pedido` em `rota` (em `base`, se dada); devolve o `resultado` conferido."""
    corpo = fio.codificar(dict(pedido))
    cabecalhos = fio.cabecalhos_do_pedido(chave, rota, corpo)
    try:
        resposta = await http.post(f"{base}{rota}" if base else rota, content=corpo, headers=cabecalhos)
    except httpx.HTTPError as exc:
        raise AgenteRemotoIndisponivelError(f"{rota}: {type(exc).__name__}") from exc
    return ler_resposta(resposta, chave, rota, cabecalhos[fio.CABECALHO_ASSINATURA])


def cliente_http(url: str | None, *, prazo_s: float) -> httpx.AsyncClient:
    """O cliente HTTP INTERNO do protocolo (servico a servico, sem proxy do ambiente nem redirecionamento)."""
    if url:
        return httpx.AsyncClient(base_url=url, timeout=prazo_s, trust_env=False, follow_redirects=False)
    return httpx.AsyncClient(timeout=prazo_s, trust_env=False, follow_redirects=False)


def _digest(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class RemetenteRemoto:
    """O remetente do turno do lado do SERVICO DO AGENTE: cada `send` volta ao receptor que abriu o turno
    (`retorno`, o endereco da propria replica) com o token da operacao.

    Implementa `RemetenteDoTurno`. O receptor resolve o token no registro dele e chama o remetente real
    (cercado, com o numero cru) — o numero nunca sai do receptor; volta so' um recibo booleano.
    """

    def __init__(self, *, http: httpx.AsyncClient, chave: bytes, token: str, retorno: str) -> None:
        if not fio.retorno_valido(retorno):
            raise ValueError("endereco de retorno fora do formato")
        self._http = http
        self._chave = chave
        self._token = token
        self._retorno = retorno

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        resultado = await postar(
            self._http,
            self._chave,
            fio.ROTA_ENVIO,
            {"token": self._token, "to_hash": to_hash, "text": text},
            base=self._retorno,
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
        remetente: RemetenteDoTurno | None,
        roteador_lucas_enabled: bool,
        persistente: bool,
    ) -> None:
        self._agente = agente
        self._remetente = remetente
        self._base = {"roteador_lucas_enabled": roteador_lucas_enabled, "persistente": persistente}

    async def ainvoke(self, state: Mapping[str, Any], config: Any = None) -> dict[str, Any]:
        estado = dict(state)
        resposta = await self._agente.postar_com_remetente(
            self._remetente, fio.ROTA_INVOCAR, {**self._base, "estado": estado, "config": config}
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
        resposta = await self._agente.postar_com_remetente(
            self._remetente, fio.ROTA_LER_ESTADO, {**self._base, "config": config}
        )
        if not isinstance(resposta, dict):
            raise AgenteRemotoIndisponivelError(f"{fio.ROTA_LER_ESTADO}: resultado fora do protocolo")
        valores = resposta.get("values")
        return EstadoDoGrafoRemoto(
            values=dict(valores) if isinstance(valores, dict) else {},
            next=tuple(resposta.get("next") or ()),
        )

    async def aupdate_state(self, config: Any, values: Any, as_node: str | None = None) -> Any:
        return await self._agente.postar_com_remetente(
            self._remetente,
            fio.ROTA_ATUALIZAR_ESTADO,
            {**self._base, "config": config, "values": dict(values), "as_node": as_node},
        )


class _GrafoRemoto:
    """O grafo-procuracao: `compile()` devolve o compilado remoto (o remetente e' registrado por operacao)."""

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
        return _GrafoCompiladoRemoto(
            self._agente,
            remetente=self._remetente,
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
        registro: RegistroDeRemetentes | None = None,
        retorno: str | None = None,
        metadata_ecs: str | None = None,
        porta_do_receptor: int = PORTA_DO_RECEPTOR_PADRAO,
    ) -> None:
        if not url:
            raise ValueError("agente remoto: URL vazia")
        if not chave:
            raise ValueError("agente remoto: chave de assinatura vazia (falha fechada)")
        if retorno is None and not metadata_ecs:
            raise ValueError(
                f"agente remoto: sem endereco de retorno ({ENV_METADATA_ECS} ou {ENV_RECEPTOR_URL})"
            )
        if retorno is not None and not fio.retorno_valido(retorno):
            raise ValueError(f"agente remoto: {ENV_RECEPTOR_URL} fora do formato http(s)://host[:porta]")
        self.agent_id = agent_id
        #: So' rotulo local: a versao que o grafo carimba e' a do agente, do lado do servico (ela nao viaja).
        self.agent_version = f"{agent_id}@remoto"
        self.registro = registro if registro is not None else RegistroDeRemetentes()
        #: Nonces dos pedidos que a rota de envio do RECEPTOR ja' aceitou (anti-replay, 60 s).
        self.nonces = fio.GuardaDeNonces()
        self._chave = chave
        self._http = http if http is not None else cliente_http(url, prazo_s=prazo_s)
        self._retorno = retorno
        self._metadata_ecs = metadata_ecs
        self._porta_do_receptor = porta_do_receptor
        self._pedidos_de_pessoa: OrderedDict[str, bool] = OrderedDict()

    @classmethod
    def do_ambiente(
        cls, url: str, agent_id: str, *, environ: Mapping[str, str] | None = None
    ) -> AgenteDeEntradaRemoto:
        """Construido das variaveis do receptor. Sem `PHI_HMAC_KEY` ou sem endereco de retorno LEVANTA."""
        env = os.environ if environ is None else environ
        chave = fio.derivar_chave(env.get(ENV_PHI_HMAC_KEY, ""))
        prazo_bruto = env.get(ENV_PRAZO_S, "").strip()
        prazo_s = float(prazo_bruto) if prazo_bruto else PRAZO_PADRAO_S
        if prazo_s <= 0:
            raise ValueError(f"agente remoto: {ENV_PRAZO_S} deve ser positivo")
        metadata = env.get(ENV_METADATA_ECS, "").strip() or None
        # No ECS manda o IP da propria task; fora dele, a URL configurada.
        retorno = None if metadata else (env.get(ENV_RECEPTOR_URL, "").strip() or None)
        porta = int(env.get(ENV_PORTA_DO_RECEPTOR, "").strip() or PORTA_DO_RECEPTOR_PADRAO)
        return cls(
            url,
            agent_id,
            chave=chave,
            prazo_s=prazo_s,
            retorno=retorno,
            metadata_ecs=metadata,
            porta_do_receptor=porta,
        )

    # --- assinatura e transporte --------------------------------------------------------------------------
    @property
    def chave(self) -> bytes:
        """A chave de assinatura do protocolo (a rota de envio do receptor usa a MESMA)."""
        return self._chave

    async def endereco_de_retorno(self) -> str:
        """`http://<ip da task>:<porta>` no ECS (lido uma vez do metadata), ou `MAEZO_RECEPTOR_URL`."""
        if self._retorno is not None:
            return self._retorno
        try:
            resposta = await self._http.get(str(self._metadata_ecs), timeout=PRAZO_DO_METADATA_S)
            resposta.raise_for_status()
            redes = resposta.json().get("Networks") or []
            ip = (redes[0].get("IPv4Addresses") or [None])[0] if redes else None
        except (httpx.HTTPError, ValueError, AttributeError, IndexError) as exc:
            raise AgenteRemotoIndisponivelError(f"metadata do ECS ilegivel: {type(exc).__name__}") from exc
        retorno = f"http://{ip}:{self._porta_do_receptor}"
        if not isinstance(ip, str) or not fio.retorno_valido(retorno):
            raise AgenteRemotoIndisponivelError("metadata do ECS sem IPv4 da task")
        self._retorno = retorno
        return retorno

    async def postar(self, rota: str, pedido: Mapping[str, Any]) -> Any:
        return await postar(self._http, self._chave, rota, pedido)

    async def postar_com_remetente(
        self, remetente: RemetenteDoTurno | None, rota: str, pedido: Mapping[str, Any]
    ) -> Any:
        """POST de uma operacao que pode enviar: registra o remetente sob token, manda token + retorno, e
        REVOGA o token quando a operacao termina (sucesso ou falha)."""
        retorno = await self.endereco_de_retorno()
        token = self.registro.registrar(remetente) if remetente is not None else None
        try:
            return await self.postar(rota, {**pedido, "token": token, "retorno": retorno})
        finally:
            self.registro.revogar(token)

    async def aclose(self) -> None:
        await self._http.aclose()

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
        resultado = await self.postar_com_remetente(
            dependencias.get("whatsapp"),
            fio.ROTA_ESCALAR_IDENTIDADE,
            {**self._chaves_dos_nos(dependencias), "estado": estado, "motivo": motivo},
        )
        if not isinstance(resultado, dict):
            raise AgenteRemotoIndisponivelError(f"{fio.ROTA_ESCALAR_IDENTIDADE}: resultado fora do protocolo")
        return resultado

    async def escalar_e_responder(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any]
    ) -> dict[str, Any]:
        resultado = await self.postar_com_remetente(
            dependencias.get("whatsapp"),
            fio.ROTA_ESCALAR_E_RESPONDER,
            {**self._chaves_dos_nos(dependencias), "estado": estado},
        )
        if not isinstance(resultado, dict):
            raise AgenteRemotoIndisponivelError(
                f"{fio.ROTA_ESCALAR_E_RESPONDER}: resultado fora do protocolo"
            )
        # Mesma forma do agente em processo: o estado do chamador e' atualizado e devolvido.
        estado.update(resultado)
        return estado

    def eh_pedido_explicito_de_pessoa(self, texto: str) -> bool:
        """Do cache, SEM I/O (a porta e' sincrona e roda no laco do receptor). O booleano chega pelo `ainvoke`
        do turno ou por `antecipar_pedido_de_pessoa`; ausente = erro de fiacao, levantado alto."""
        lembrado = self._pedidos_de_pessoa.get(_digest(texto))
        if lembrado is None:
            raise AgenteRemotoIndisponivelError(
                "pedido explicito de pessoa sem resposta antecipada (o despachante antecipa antes do uso)"
            )
        return lembrado

    async def antecipar_pedido_de_pessoa(self, texto: str) -> None:
        """Garante, de forma ASSINCRONA, o booleano de `eh_pedido_explicito_de_pessoa(texto)` no cache."""
        if _digest(texto) in self._pedidos_de_pessoa:
            return
        resultado = await self.postar(fio.ROTA_PEDIDO_DE_PESSOA, {"texto": texto})
        if not isinstance(resultado, bool):
            raise AgenteRemotoIndisponivelError(f"{fio.ROTA_PEDIDO_DE_PESSOA}: resultado fora do protocolo")
        self.lembrar_pedido_de_pessoa(texto, resultado)

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

    @staticmethod
    def _chaves_dos_nos(dependencias: Mapping[str, Any]) -> dict[str, Any]:
        """Das dependencias dos nos so' viaja a chave do Lucas (o remetente vai como token)."""
        return {"roteador_lucas_enabled": dependencias.get("roteador_lucas_enabled") is True}


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
