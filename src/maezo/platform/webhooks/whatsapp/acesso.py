"""Acesso do beneficiario no WhatsApp: consentimento + verificacao de identidade, ANTES dos agentes (DL-0083).

DECISAO DO DONO (Filipe, CTO/DPO/dono da organizacao, 08/10/2026). Hoje os dois agentes leem dados da AMH
identificando a pessoa so' pelo telefone. Com `MAEZO_ACESSO_BENEFICIARIO` ligada, TODA conversa passa antes
por
uma maquina de estados DETERMINISTICA (nenhum LLM), no caminho do despachante, persistida no Postgres
(`conversa_acesso_beneficiario`, migration 0022). Desligada, este modulo nem e' construido e o receptor e' o
de sempre byte a byte.

A MAQUINA (cada estado, e o que ela responde; os agentes NUNCA rodam fora de `verificado`):

  (sem linha)             qualquer 1a mensagem            -> `sem_consentimento` + TEXTO_CONSENTIMENTO
  sem_consentimento       ACEITO                          -> `aguardando_cpf`      + PEDIDO_CPF
  sem_consentimento       outra coisa                     -> igual + TEXTO_CONSENTIMENTO de novo
  aguardando_cpf          telefone DESCONHECIDO (nenhum   -> `bloqueado_humano` + TELEFONE_NAO_IDENTIFICADO +
                          candidato na AMH)                  escalonamento humano. NUNCA entra pelo caminho do
                                                             documento: o CPF digitado nem e' lido
  aguardando_cpf          AMH fora no passo do telefone   -> INDISPONIVEL; estado e tentativas INTACTOS
  aguardando_cpf          CPF invalido                    -> igual (tentativa+1)   + CPF_INVALIDO
  aguardando_cpf          CPF valido, telefone = 1 pessoa -> CONFERE `cpf` contra a referencia do telefone
  aguardando_cpf          CPF valido, telefone de >1      -> `aguardando_nascimento` + PEDIDO_NASCIMENTO
  aguardando_nascimento   nascimento valido               -> CONFERE `cpfdob` contra CADA candidato do
                                                             telefone; vale so' se EXATAMENTE um conferir
  qualquer verificacao    confere                         -> `verificado` + VERIFICADO (a mensagem seguinte
                                                             segue para o fluxo de sempre)
  qualquer verificacao    nao confere / formato invalido  -> tentativa+1 + FALHA_CONFERENCIA
  3a tentativa falha      --                              -> `bloqueado_humano` + BLOQUEADO + escalonamento
                                                             humano (SP-OP-ESCALATION-001)
  bloqueado_humano        qualquer                        -> BLOQUEADO_CURTO, ate' a validade passar
  AMH fora na verificacao --                              -> INDISPONIVEL; estado e tentativas INTACTOS
  verificado              validade vencida                -> `aguardando_cpf` + PEDIDO_CPF (consent. fica)
  qualquer estado         REVOGAR                         -> `revogado` + REVOGADO + memoria da conversa
                                                             APAGADA; ACEITO recomeca em CPF
  verificacao concluida   pessoa DIFERENTE da ultima      -> `sem_consentimento` + TEXTO_CONSENTIMENTO: o
                          verificada (telefone de >1)        ACEITO anterior era de OUTRA pessoa; memoria da
                                                             conversa APAGADA
  fora de `verificado`    sinal lexical de emergencia     -> EMERGENCIA ANTES da resposta do estado (nunca no
                                                             lugar dela), sem dado de beneficiario nenhum

TENTATIVAS E BLOQUEIO SOBREVIVEM A REVOGAR/ACEITO (revisao do PR #700, P0): so' o vencimento do prazo do
bloqueio (ou uma verificacao bem-sucedida) zera o contador. Senao REVOGAR + ACEITO seria um "tente de novo"
ilimitado contra um CPF de 11 digitos.

CONFERE, NAO RESOLVE: a AMH so' responde se o fator confere com uma referencia que o MAEZO mandou (as do
telefone). Um documento digitado nunca descobre quem e' a pessoa.

CONSENTIMENTO NO LAGO. Logo apos a PRIMEIRA verificacao (a referencia passa a ser conhecida) o
consentimento e'
gravado na AMH (`consent-record`, `granted`); o `REVOGAR` grava `revoked` quando a referencia e' conhecida.
FALHA NA GRAVACAO NAO NEGA O ACESSO (escolha de engenharia, a ratificar): a decisao ja' vale aqui, a flag
`consentimento_pendente_gravacao`/`revogacao_pendente` fica no estado e a gravacao e' REPETIDA nos turnos
seguintes (chave de idempotencia deterministica) ate' dar certo. Uma revogacao pendente, ao contrario,
BLOQUEIA
um novo ACEITO: o lago tem de refletir o REVOGAR antes de um novo consentimento.

CPF E NASCIMENTO. Existem em memoria SO' pelo instante do calculo do hash; nunca em log, estado, checkpoint,
excecao, metrica ou prompt (a mensagem e' consumida AQUI e nunca chega ao grafo). Unica excecao, DIVULGADA ao
dono: no caminho "telefone nao identifica uma pessoa so'" o CPF digitado numa mensagem precisa esperar a
data de
nascimento da mensagem seguinte (o hash `cpfdob` cobre os dois). Ele fica numa memoria de PROCESSO, nunca
persistida, por no maximo `PRAZO_CPF_PENDENTE_S`, com `repr` oculto; se a replica mudar ou reiniciar, a
pessoa e'
convidada a mandar CPF e nascimento na mesma mensagem.

ORDEM DOS EFEITOS (idempotencia sob reentrega): `avaliar` calcula e chama a AMH, o despachante ENVIA a
resposta
e SO' ENTAO `persistir` grava o estado. Se o envio cai, o estado nao mudou e a reentrega recalcula (a AMH
repete a mesma pergunta, e a chave de saida e' a mesma).

LOG: so' `conversation_id` (keyed), tokens fechados de estado/motivo e contadores. Nunca texto, CPF,
nascimento,
hash, telefone ou referencia.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal, Protocol

import structlog

from maezo.ports.consent_record import ConsentRecordPort
from maezo.ports.subject_verification import SubjectVerificationPort
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

from .pre_roteamento import PreRoteamento, normalizar

logger = structlog.get_logger(__name__)

# --- Estados (CHECK da migration 0022 repete esta lista) -------------------------------------------
SEM_CONSENTIMENTO: Final[str] = "sem_consentimento"
AGUARDANDO_CPF: Final[str] = "aguardando_cpf"
AGUARDANDO_NASCIMENTO: Final[str] = "aguardando_nascimento"
VERIFICADO: Final[str] = "verificado"
BLOQUEADO_HUMANO: Final[str] = "bloqueado_humano"
REVOGADO: Final[str] = "revogado"
ESTADOS: Final[tuple[str, ...]] = (
    SEM_CONSENTIMENTO,
    AGUARDANDO_CPF,
    AGUARDANDO_NASCIMENTO,
    VERIFICADO,
    BLOQUEADO_HUMANO,
    REVOGADO,
)

# --- Textos FIXOS (decisao do dono, 08/10/2026) -----------------------------------------------------
VERSAO_TEXTO_CONSENTIMENTO: Final[str] = "wa-consent-v1"
TEXTO_CONSENTIMENTO: Final[str] = (
    "Olá! Sou a assistente da Austa Clínicas. Para atender você por aqui, preciso do seu consentimento "
    "para usar seus dados de beneficiário (cadastro, plano e cobrança) neste atendimento. Você pode "
    "revogar quando quiser, escrevendo REVOGAR. Para continuar, responda ACEITO. Se preferir não aceitar, "
    "procure a central de atendimento pelo aplicativo ou pelo portal do plano."
)
#: sha256 do texto EXATO mostrado (e enviado a AMH no registro do consentimento).
SHA256_TEXTO_CONSENTIMENTO: Final[str] = hashlib.sha256(TEXTO_CONSENTIMENTO.encode("utf-8")).hexdigest()
PEDIDO_CPF: Final[str] = "Obrigada! Agora, para confirmar que é você, digite o seu CPF (apenas números)."
CPF_INVALIDO: Final[str] = "Esse CPF não parece válido. Confira e digite de novo."
PEDIDO_NASCIMENTO: Final[str] = "Para sua segurança, digite também sua data de nascimento (dd/mm/aaaa)."
#: Caminho sem o CPF pendente em memoria (outra replica ou reinicio): a pessoa manda os dois juntos.
PEDIDO_CPF_E_NASCIMENTO: Final[str] = (
    "Para sua segurança, digite seu CPF e sua data de nascimento (dd/mm/aaaa) na mesma mensagem."
)
RESPOSTA_VERIFICADO: Final[str] = "Tudo certo, identificamos você. Como posso ajudar?"
FALHA_CONFERENCIA: Final[str] = "Não consegui confirmar seus dados. Confira e tente de novo."
BLOQUEADO: Final[str] = "Não consegui confirmar a sua identidade. Vou encaminhar você para um atendente."
BLOQUEADO_CURTO: Final[str] = "Seu atendimento foi encaminhado a um atendente."
INDISPONIVEL: Final[str] = "No momento não consegui confirmar seus dados. Tente novamente em alguns minutos."
#: Telefone SEM candidato na AMH: texto neutro (nao diz nada sobre CPF nenhum) + atendente humano.
TELEFONE_NAO_IDENTIFICADO: Final[str] = (
    "Não consegui confirmar seu cadastro por este número. Para sua segurança, vou encaminhar você para um "
    "atendente."
)
RESPOSTA_REVOGADO: Final[str] = (
    "Consentimento revogado. Não vou mais usar seus dados neste atendimento. "
    "Se quiser voltar a conversar, escreva ACEITO."
)
#: Orientacao FIXA de emergencia (nenhum texto puramente fixo de red flag existe na Helena: o dela e' o do
#: encaminhamento a equipe, que aqui seria falso porque nenhum caso e' aberto). Nunca usa dado de pessoa.
TEXTO_EMERGENCIA: Final[str] = "Se for uma emergência, ligue 192 (SAMU) ou vá ao pronto-socorro mais próximo."

# --- Vocabulario do registro de consentimento ------------------------------------------------------
ESCOPO_CONSENTIMENTO: Final[str] = "atendimento_whatsapp"
CANAL_CONSENTIMENTO: Final[str] = "whatsapp"
DECISAO_CONCEDIDA: Final[str] = "granted"
DECISAO_REVOGADA: Final[str] = "revoked"

#: Fatores do contrato `subject-verification` (AMH #212).
FATOR_CPF: Final[str] = "cpf"
FATOR_CPF_NASCIMENTO: Final[str] = "cpfdob"

MAX_TENTATIVAS: Final[int] = 3
PRAZO_CPF_PENDENTE_S: Final[float] = 300.0
MAX_CPF_PENDENTES: Final[int] = 1024
PRAZO_AMH_S: Final[float] = 8.0
#: Prazo PROPRIO da escrita do consentimento (`consent-record`): uma escrita no lago, mais lenta que a
#: conferencia, e cuja falha nao nega o acesso (fica pendente e e' repetida).
PRAZO_CONSENTIMENTO_S: Final[float] = 20.0
#: Teto de tentativas FALHAS de gravar (ou regravar) uma decisao de consentimento. Atingido, a regravacao
#: para (cada tentativa custa ate' `PRAZO_CONSENTIMENTO_S` no turno da pessoa) e um log de ALARME pede a
#: intervencao humana. A pendencia continua visivel no estado; uma revogacao pendente segue barrando ACEITO.
MAX_REGRAVACOES: Final[int] = 5
#: Mais candidatos que isto num telefone nao e' "titular e dependentes": nao entra pelo caminho do documento.
MAX_CANDIDATOS: Final[int] = 10
VALIDADE_HORAS_PADRAO: Final[int] = 24
ANO_MINIMO_NASCIMENTO: Final[int] = 1900

#: Motivo de escalonamento do bloqueio: a categoria EXISTENTE mais proxima de "uma pessoa precisa assumir" na
#: Helena (`solicitacao_humano`, severidade `leve`, P3), a mesma do agendamento. Nao ha' categoria propria.
MOTIVO_ESCALONAMENTO_BLOQUEIO: Final[str] = "solicitacao_humano"


# ==========================================================================================
# Reconhecimento dos comandos e dos fatores (puro, sem I/O)
# ==========================================================================================
def eh_aceito(texto: str) -> bool:
    """`True` so' para a palavra ACEITO (caixa, acento e pontuacao nao contam: "aceito.", "Aceito!")."""
    return normalizar(texto) == "aceito"


_REVOGAR: Final[frozenset[str]] = frozenset({"revogar", "revogar meu consentimento"})


def eh_revogar(texto: str) -> bool:
    """`True` para REVOGAR ou "revogar meu consentimento" (caixa, acento e pontuacao nao contam)."""
    return normalizar(texto) in _REVOGAR


_CPF_FORMA: Final[re.Pattern[str]] = re.compile(r"\s*(\d{3})[.\s]?(\d{3})[.\s]?(\d{3})[-.\s]?(\d{2})\s*")


def cpf_valido(digitos: str) -> bool:
    """11 digitos, nao todos iguais e os dois digitos verificadores corretos."""
    if len(digitos) != 11 or not digitos.isascii() or not digitos.isdigit() or len(set(digitos)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(digitos[i]) * (tamanho + 1 - i) for i in range(tamanho))
        if (soma * 10 % 11) % 10 != int(digitos[tamanho]):
            return False
    return True


def extrair_cpf(texto: str) -> str | None:
    """Os 11 digitos do CPF digitado com ou sem pontuacao, ou `None` se a forma ou os digitos nao valem."""
    casamento = _CPF_FORMA.fullmatch(texto)
    if casamento is None:
        return None
    digitos = "".join(casamento.groups())
    return digitos if cpf_valido(digitos) else None


_DATA_COM_SEPARADOR: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)"
)
_OITO_DIGITOS: Final[re.Pattern[str]] = re.compile(r"\d{8}")


@dataclass(frozen=True, slots=True, repr=False)
class LeituraNascimento:
    """O que a mensagem do passo do nascimento trouxe. `repr` oculto: carrega digitos pessoais."""

    #: A mensagem tem algo que parece data.
    encontrou: bool
    #: A data existe, e' >= 1900 e nao e' futura.
    nascimento: date | None
    #: CPF valido digitado JUNTO com a data (`None` se so' a data veio, ou se a sobra nao e' CPF valido).
    cpf: str | None
    #: A mensagem tinha outra coisa alem da data e dessa outra coisa nao e' um CPF valido.
    sobra_invalida: bool

    def __repr__(self) -> str:
        return "LeituraNascimento(<redacted>)"


def ler_nascimento(texto: str, *, hoje: date) -> LeituraNascimento:
    """Interpreta a mensagem do passo do nascimento: `dd/mm/aaaa` (separadores `/`, `.` ou `-`) ou `ddmmaaaa`,
    sozinha ou com o CPF antes/depois. Data fora do calendario, anterior a 1900 ou futura = invalida."""
    dia_mes_ano: tuple[int, int, int] | None = None
    sobra = texto
    casamento = _DATA_COM_SEPARADOR.search(texto)
    if casamento is not None:
        dia_mes_ano = (int(casamento.group(1)), int(casamento.group(2)), int(casamento.group(3)))
        sobra = texto[: casamento.start()] + " " + texto[casamento.end() :]
    else:
        pedacos = texto.split()
        oito = [p for p in pedacos if _OITO_DIGITOS.fullmatch(p)]
        if len(oito) == 1:
            token = oito[0]
            dia_mes_ano = (int(token[:2]), int(token[2:4]), int(token[4:]))
            pedacos.remove(token)
            sobra = " ".join(pedacos)
    if dia_mes_ano is None:
        return LeituraNascimento(encontrou=False, nascimento=None, cpf=None, sobra_invalida=False)
    nascimento: date | None
    try:
        dia, mes, ano = dia_mes_ano
        nascimento = date(ano, mes, dia)
    except ValueError:
        nascimento = None
    if nascimento is not None and not (nascimento.year >= ANO_MINIMO_NASCIMENTO and nascimento <= hoje):
        nascimento = None
    cpf: str | None = None
    sobra_invalida = False
    if sobra.strip():
        cpf = extrair_cpf(sobra)
        sobra_invalida = cpf is None
    return LeituraNascimento(encontrou=True, nascimento=nascimento, cpf=cpf, sobra_invalida=sobra_invalida)


# ==========================================================================================
# O estado persistido
# ==========================================================================================
@dataclass(frozen=True, slots=True)
class EstadoAcesso:
    """Uma linha de `conversa_acesso_beneficiario`. So' estado fechado, contadores, carimbos, versao/sha256 do
    texto e a referencia PSEUDONIMA da AMH. Nenhum telefone, CPF, nascimento, nome ou texto digitado."""

    conversation_id: str
    estado: str
    ultima_mensagem_em: datetime
    tentativas: int = 0
    texto_versao: str | None = None
    texto_sha256: str | None = None
    consentido_em: datetime | None = None
    revogado_em: datetime | None = None
    portable_subject_ref: str | None = field(default=None, repr=False)
    consent_ref: str | None = field(default=None, repr=False)
    consentimento_pendente_gravacao: bool = False
    revogacao_pendente: bool = False
    verificado_em: datetime | None = None
    expira_em: datetime | None = None
    bloqueado_ate: datetime | None = None
    #: Tentativas FALHAS de gravar a decisao pendente no lago (0..MAX_REGRAVACOES).
    regravacoes_falhas: int = 0

    def __post_init__(self) -> None:
        if self.estado not in ESTADOS:
            raise ValueError("acesso: estado fora do dominio")
        if not 0 <= self.tentativas <= MAX_TENTATIVAS:
            raise ValueError("acesso: tentativas fora de 0..3")
        if not 0 <= self.regravacoes_falhas <= MAX_REGRAVACOES:
            raise ValueError("acesso: regravacoes_falhas fora do dominio")


class AcessoStore(Protocol):
    async def ler(self, conversation_id: str) -> EstadoAcesso | None: ...

    async def gravar(self, estado: EstadoAcesso) -> None: ...


class AcessoStoreEmMemoria:
    """Store de processo, para testes e para a composicao sem Postgres. NAO sobrevive a reinicio."""

    def __init__(self) -> None:
        self._linhas: dict[str, EstadoAcesso] = {}

    async def ler(self, conversation_id: str) -> EstadoAcesso | None:
        return self._linhas.get(conversation_id)

    async def gravar(self, estado: EstadoAcesso) -> None:
        self._linhas[estado.conversation_id] = estado


# ==========================================================================================
# O CPF que espera a data de nascimento (memoria de PROCESSO, nunca persistida)
# ==========================================================================================
class _CpfsPendentes:
    """CPF digitado a espera do nascimento. Em memoria, com prazo e teto; `repr` e' constante."""

    __slots__ = ("_itens", "_relogio")

    def __init__(self, relogio: Callable[[], float] = time.monotonic) -> None:
        self._itens: dict[str, tuple[float, str]] = {}
        self._relogio = relogio

    def __repr__(self) -> str:
        return "_CpfsPendentes(<redacted>)"

    def guardar(self, conversation_id: str, cpf: str) -> None:
        agora = self._relogio()
        if len(self._itens) >= MAX_CPF_PENDENTES:
            self._itens = {k: v for k, v in self._itens.items() if v[0] > agora}
        if len(self._itens) >= MAX_CPF_PENDENTES:
            self._itens.pop(next(iter(self._itens)))
        self._itens[conversation_id] = (agora + PRAZO_CPF_PENDENTE_S, cpf)

    def ver(self, conversation_id: str) -> str | None:
        item = self._itens.get(conversation_id)
        if item is None:
            return None
        if self._relogio() >= item[0]:
            del self._itens[conversation_id]
            return None
        return item[1]

    def descartar(self, conversation_id: str) -> None:
        self._itens.pop(conversation_id, None)


# ==========================================================================================
# O servico
# ==========================================================================================
class HasherDeVerificacao(Protocol):
    """`gateway.amh_interop.AmhSubjectVerifyHasher`: devolve o hash ou `None` (entrada fora do formato)."""

    def hash_cpf(self, cpf_digitos: str) -> str | None: ...

    def hash_cpf_nascimento(self, cpf_digitos: str, nascimento_aaaammdd: str) -> str | None: ...


class CandidatosDoTelefone(Protocol):
    """`agents.lucas.identidade_amh.ResolvedorDeSujeitoAmh.candidatos_com_desfecho`: TODAS as referencias
    candidatas do telefone e o desfecho fechado (`unico`/`multiplos`/`nenhum`/`indisponivel`)."""

    async def candidatos_com_desfecho(
        self, pseudo_id: str, *, phone_hash: str | None
    ) -> tuple[tuple[str, ...], str]: ...


@dataclass(frozen=True, slots=True)
class DecisaoAcesso:
    """O que `avaliar` decidiu para ESTA mensagem. Nada foi gravado ainda (`persistir` grava)."""

    #: Textos a enviar, em ordem (a emergencia, quando houver, vem primeiro). Vazio com `prosseguir`.
    respostas: tuple[str, ...]
    #: `True` so' em `verificado`: a mensagem segue para o fluxo de sempre (Helena/Lucas).
    prosseguir: bool
    novo: EstadoAcesso
    #: Abrir o escalonamento humano (3a falha). Idempotente por chave de negocio.
    escalar: bool = False
    #: Uma verificacao acabou de ser concluida (o cache de identidade do despachante deve ser invalidado).
    nova_verificacao: bool = False
    #: A memoria da conversa (historico curto DL-0080, memoria clinica, coleta) e' de OUTRA pessoa ou de um
    #: consentimento revogado: o despachante a apaga ANTES de gravar o estado (DL-0083, revisao do #700).
    esquecer_memoria: bool = False

    @property
    def portable_subject_ref(self) -> str | None:
        return self.novo.portable_subject_ref if self.prosseguir else None

    @property
    def consent_ref(self) -> str | None:
        return self.novo.consent_ref if self.prosseguir else None


_ResultadoVerificacao = Literal["verificado", "nao_verificado", "indisponivel"]


def _agora_padrao() -> datetime:
    return datetime.now(UTC)


def _segundos(instante: datetime) -> datetime:
    return instante.astimezone(UTC).replace(microsecond=0)


def _iso_utc(instante: datetime) -> str:
    return _segundos(instante).strftime("%Y-%m-%dT%H:%M:%SZ")


def chave_de_idempotencia(
    conversation_id: str, texto_versao: str, decisao: str, decidido_em: datetime
) -> str:
    """Chave DETERMINISTICA do registro: a mesma decisao, da mesma conversa, no mesmo instante, repete a mesma
    chave (a AMH devolve o mesmo `consent_ref`)."""
    base = "|".join((conversation_id, texto_versao, decisao, _iso_utc(decidido_em)))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


class AcessoBeneficiario:
    """A maquina de estados do acesso. Construida UMA vez no boot do receptor, so' com a flag ligada."""

    def __init__(
        self,
        *,
        store: AcessoStore,
        verification: SubjectVerificationPort,
        consents: ConsentRecordPort,
        resolvedor: CandidatosDoTelefone,
        hash_telefone: Callable[[str], str | None],
        hasher: HasherDeVerificacao,
        lexicos: PreRoteamento,
        amh_tenant: str,
        hash_scheme: str,
        purpose_of_use: str,
        validade: timedelta = timedelta(hours=VALIDADE_HORAS_PADRAO),
        relogio: Callable[[], datetime] = _agora_padrao,
        prazo_amh_s: float = PRAZO_AMH_S,
        prazo_consentimento_s: float = PRAZO_CONSENTIMENTO_S,
        cpfs_pendentes: _CpfsPendentes | None = None,
        aclose_fn: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if validade <= timedelta(0):
            raise ValueError("acesso: validade deve ser positiva")
        if not 0 < prazo_amh_s <= 30:
            raise ValueError("acesso: prazo_amh_s fora de (0, 30]")
        if not 0 < prazo_consentimento_s <= 30:
            raise ValueError("acesso: prazo_consentimento_s fora de (0, 30]")
        self._prazo_consentimento = float(prazo_consentimento_s)
        self._store = store
        self._verification = verification
        self._consents = consents
        self._resolvedor = resolvedor
        self._hash_telefone = hash_telefone
        self._hasher = hasher
        self._lexicos = lexicos
        self._tenant_amh = amh_tenant
        self._hash_scheme = hash_scheme
        self._purpose = purpose_of_use
        self._validade = validade
        self._relogio = relogio
        self._prazo = float(prazo_amh_s)
        self._pendentes = cpfs_pendentes if cpfs_pendentes is not None else _CpfsPendentes()
        self._aclose_fn = aclose_fn
        self._travas: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def __repr__(self) -> str:
        return "AcessoBeneficiario(<redacted>)"

    async def aclose(self) -> None:
        if self._aclose_fn is not None:
            await self._aclose_fn()

    # -- serializacao por conversa ---------------------------------------------------------------
    @asynccontextmanager
    async def trava(self, conversation_id: str) -> AsyncIterator[None]:
        """Uma mensagem por vez por conversa neste processo (le' -> envia -> grava)."""
        trava = self._travas.get(conversation_id)
        if trava is None:
            trava = asyncio.Lock()
            self._travas[conversation_id] = trava
        async with trava:
            yield

    def sinal_de_emergencia(self, texto: str) -> bool:
        """O sinal lexical de saude/risco do pre-roteamento (so' booleano; nunca o termo nem o texto)."""
        return self._lexicos.avaliar(texto).sinal_saude

    async def persistir(self, decisao: DecisaoAcesso) -> None:
        await self._store.gravar(decisao.novo)

    # -- entrada ----------------------------------------------------------------------------------
    async def avaliar(self, *, conversation_id: str, texto: str, numero_cru: str) -> DecisaoAcesso:
        """Decide o que fazer com a mensagem. NUNCA loga nem guarda o texto; chama a AMH quando preciso."""
        agora = _segundos(self._relogio())
        emergencia = self.sinal_de_emergencia(texto)
        atual = await self._store.ler(conversation_id)
        if atual is None:
            # Primeira mensagem da conversa, QUALQUER conteudo: o texto de consentimento, e mais nada.
            novo = EstadoAcesso(
                conversation_id=conversation_id,
                estado=SEM_CONSENTIMENTO,
                ultima_mensagem_em=agora,
                texto_versao=VERSAO_TEXTO_CONSENTIMENTO,
                texto_sha256=SHA256_TEXTO_CONSENTIMENTO,
            )
            self._log_transicao(conversation_id, None, SEM_CONSENTIMENTO, "primeira_mensagem")
            return self._responder(novo, TEXTO_CONSENTIMENTO, emergencia=emergencia)

        atual = replace(atual, ultima_mensagem_em=agora)
        atual, reiniciou = self._vencimentos(atual, agora)
        atual = await self._regravar_pendencias(atual)

        comando = normalizar(texto)
        if comando in _REVOGAR:
            # REVOGAR vale em QUALQUER estado (inclusive bloqueado): e' direito do titular. Nao zera nada
            # do que conta tentativas (ver `_revogar`).
            return await self._revogar(atual, agora, emergencia=emergencia)

        if atual.estado == VERIFICADO:
            return DecisaoAcesso(respostas=(), prosseguir=True, novo=atual)
        if reiniciou:
            # A verificacao venceu (ou o bloqueio passou): recomeca no CPF, o consentimento fica.
            novo = replace(atual, estado=AGUARDANDO_CPF, tentativas=0)
            self._log_transicao(conversation_id, atual.estado, AGUARDANDO_CPF, "reinicio_por_validade")
            return self._responder(novo, PEDIDO_CPF, emergencia=emergencia)
        if atual.estado == BLOQUEADO_HUMANO:
            return self._responder(atual, BLOQUEADO_CURTO, emergencia=emergencia)
        if atual.estado in (SEM_CONSENTIMENTO, REVOGADO):
            if comando != "aceito":
                return self._responder(atual, TEXTO_CONSENTIMENTO, emergencia=emergencia)
            return await self._aceitar(atual, agora, emergencia=emergencia)
        if atual.estado == AGUARDANDO_CPF:
            return await self._passo_cpf(atual, agora, texto, numero_cru, emergencia=emergencia)
        return await self._passo_nascimento(atual, agora, texto, numero_cru, emergencia=emergencia)

    # -- helpers de decisao -----------------------------------------------------------------------
    def _responder(
        self,
        novo: EstadoAcesso,
        texto: str,
        *,
        emergencia: bool,
        escalar: bool = False,
        nova_verificacao: bool = False,
        esquecer_memoria: bool = False,
    ) -> DecisaoAcesso:
        respostas = (TEXTO_EMERGENCIA, texto) if emergencia and novo.estado != VERIFICADO else (texto,)
        return DecisaoAcesso(
            respostas=respostas,
            prosseguir=False,
            novo=novo,
            escalar=escalar,
            nova_verificacao=nova_verificacao,
            esquecer_memoria=esquecer_memoria,
        )

    def _vencimentos(self, atual: EstadoAcesso, agora: datetime) -> tuple[EstadoAcesso, bool]:
        """Verificacao vencida ou bloqueio cumprido -> `aguardando_cpf` (o consentimento persiste).

        O bloqueio e' um PRAZO, nao um estado: ele sobrevive a REVOGAR e a ACEITO (a conversa pode estar
        `revogado`/`sem_consentimento` com o prazo correndo). So' o vencimento dele zera as tentativas."""
        if atual.estado == VERIFICADO:
            vencida = atual.expira_em is None or agora >= atual.expira_em
            if vencida:
                return replace(
                    atual, estado=AGUARDANDO_CPF, tentativas=0, verificado_em=None, expira_em=None
                ), True
        if atual.estado == BLOQUEADO_HUMANO and (atual.bloqueado_ate is None or agora >= atual.bloqueado_ate):
            return replace(atual, estado=AGUARDANDO_CPF, tentativas=0, bloqueado_ate=None), True
        if atual.bloqueado_ate is not None and agora >= atual.bloqueado_ate:
            return replace(atual, tentativas=0, bloqueado_ate=None), False
        return atual, False

    def _bloqueio_vigente(self, atual: EstadoAcesso, agora: datetime) -> bool:
        return atual.bloqueado_ate is not None and agora < atual.bloqueado_ate

    def _log_transicao(self, conversation_id: str, de: str | None, para: str, motivo: str) -> None:
        logger.info("acesso_transicao", conversation_id=conversation_id, de=de, para=para, motivo=motivo)

    async def _gravar_consentimento(
        self, ref: str, estado: EstadoAcesso, decisao: str, decidido_em: datetime
    ) -> str | None:
        """Grava a decisao na AMH, com prazo PROPRIO. Devolve o `consent_ref` ou `None` (falha: repete
        depois, ate' `MAX_REGRAVACOES`)."""
        versao = estado.texto_versao or VERSAO_TEXTO_CONSENTIMENTO
        sha = estado.texto_sha256 or SHA256_TEXTO_CONSENTIMENTO
        try:
            async with asyncio.timeout(self._prazo_consentimento):
                resultado = await self._consents.record(
                    ref,
                    amh_tenant=self._tenant_amh,
                    scope=ESCOPO_CONSENTIMENTO,
                    decision=decisao,
                    consent_text_version=versao,
                    consent_text_sha256=sha,
                    decided_at=_iso_utc(decidido_em),
                    channel=CANAL_CONSENTIMENTO,
                    idempotency_key=chave_de_idempotencia(
                        estado.conversation_id, versao, decisao, decidido_em
                    ),
                    purpose_of_use=self._purpose,
                    timeout_seconds=self._prazo_consentimento,
                )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            resultado = None
        if resultado is None or not resultado.succeeded or resultado.value is None:
            logger.warning(
                "acesso_consentimento_nao_gravado",
                conversation_id=estado.conversation_id,
                decisao=decisao,
                motivo=resultado.failure.reason.value if resultado and resultado.failure else "excecao",
            )
            return None
        return resultado.value.consent_ref

    def _contar_falha_de_gravacao(self, estado: EstadoAcesso, decisao: str) -> EstadoAcesso:
        """Mais uma gravacao falha. No teto, ALARME (uma vez) e a regravacao automatica para."""
        falhas = min(estado.regravacoes_falhas + 1, MAX_REGRAVACOES)
        if falhas >= MAX_REGRAVACOES and estado.regravacoes_falhas < MAX_REGRAVACOES:
            logger.error(
                "acesso_consentimento_regravacao_esgotada",
                conversation_id=estado.conversation_id,
                decisao=decisao,
                tentativas=falhas,
                alarme=True,
            )
        return replace(estado, regravacoes_falhas=falhas)

    async def _regravar_pendencias(self, atual: EstadoAcesso) -> EstadoAcesso:
        """Repete, a cada turno, a gravacao que falhou antes (consentimento concedido ou revogado), ate' o
        teto `MAX_REGRAVACOES`; depois disso so' um humano destrava (alarme ja' emitido)."""
        ref = atual.portable_subject_ref
        if ref is None or atual.regravacoes_falhas >= MAX_REGRAVACOES:
            return atual
        if atual.revogacao_pendente and atual.revogado_em is not None:
            consent_ref = await self._gravar_consentimento(ref, atual, DECISAO_REVOGADA, atual.revogado_em)
            if consent_ref is None:
                return self._contar_falha_de_gravacao(atual, DECISAO_REVOGADA)
            return replace(
                atual,
                revogacao_pendente=False,
                portable_subject_ref=None,
                consent_ref=None,
                regravacoes_falhas=0,
            )
        if (
            atual.consentimento_pendente_gravacao
            and atual.estado == VERIFICADO
            and atual.consentido_em is not None
        ):
            consent_ref = await self._gravar_consentimento(ref, atual, DECISAO_CONCEDIDA, atual.consentido_em)
            if consent_ref is None:
                return self._contar_falha_de_gravacao(atual, DECISAO_CONCEDIDA)
            return replace(
                atual, consentimento_pendente_gravacao=False, consent_ref=consent_ref, regravacoes_falhas=0
            )
        return atual

    # -- REVOGAR ----------------------------------------------------------------------------------
    async def _revogar(self, atual: EstadoAcesso, agora: datetime, *, emergencia: bool) -> DecisaoAcesso:
        """REVOGAR: o consentimento acaba aqui e a memoria da conversa e' apagada. As TENTATIVAS e o PRAZO DO
        BLOQUEIO ficam como estao (revisao do #700, P0): REVOGAR + ACEITO nao pode zerar o contador."""
        self._pendentes.descartar(atual.conversation_id)
        ref = atual.portable_subject_ref
        novo = replace(
            atual,
            estado=REVOGADO,
            consentido_em=None,
            revogado_em=agora,
            consent_ref=None,
            consentimento_pendente_gravacao=False,
            verificado_em=None,
            expira_em=None,
            regravacoes_falhas=0,
        )
        if ref is not None:
            consent_ref = await self._gravar_consentimento(ref, novo, DECISAO_REVOGADA, agora)
            if consent_ref is None:
                novo = self._contar_falha_de_gravacao(
                    replace(novo, revogacao_pendente=True), DECISAO_REVOGADA
                )
            else:
                novo = replace(novo, revogacao_pendente=False, portable_subject_ref=None)
        else:
            novo = replace(novo, revogacao_pendente=False, portable_subject_ref=None)
        self._log_transicao(atual.conversation_id, atual.estado, REVOGADO, "revogar")
        return self._responder(novo, RESPOSTA_REVOGADO, emergencia=emergencia, esquecer_memoria=True)

    # -- ACEITO -----------------------------------------------------------------------------------
    async def _aceitar(self, atual: EstadoAcesso, agora: datetime, *, emergencia: bool) -> DecisaoAcesso:
        if atual.revogacao_pendente:
            # O lago ainda nao reflete o REVOGAR anterior (a regravacao acima falhou): nao se aceita de novo.
            return self._responder(atual, INDISPONIVEL, emergencia=emergencia)
        novo = replace(
            atual,
            estado=AGUARDANDO_CPF,
            texto_versao=VERSAO_TEXTO_CONSENTIMENTO,
            texto_sha256=SHA256_TEXTO_CONSENTIMENTO,
            consentido_em=agora,
            revogado_em=None,
            consent_ref=None,
            consentimento_pendente_gravacao=False,
            regravacoes_falhas=0,
        )
        if self._bloqueio_vigente(atual, agora):
            # O consentimento vale, mas o bloqueio tambem: so' o vencimento do prazo libera o CPF de novo.
            novo = replace(novo, estado=BLOQUEADO_HUMANO)
            self._log_transicao(atual.conversation_id, atual.estado, BLOQUEADO_HUMANO, "aceito_com_bloqueio")
            return self._responder(novo, BLOQUEADO_CURTO, emergencia=emergencia)
        self._log_transicao(atual.conversation_id, atual.estado, AGUARDANDO_CPF, "aceito")
        return self._responder(novo, PEDIDO_CPF, emergencia=emergencia)

    # -- CPF --------------------------------------------------------------------------------------
    async def _candidatos(self, numero_cru: str) -> tuple[tuple[str, ...], str]:
        """As referencias candidatas do telefone e o desfecho fechado. `indisponivel` = nao se sabe (AMH fora,
        numero fora do padrao, resposta incoerente); `nenhum` = a AMH disse que o telefone nao e' de ninguem
        (ou de gente demais para ser uma familia)."""
        try:
            phone_hash = self._hash_telefone(numero_cru)
            if not phone_hash:
                return (), "indisponivel"
            async with asyncio.timeout(self._prazo):
                refs, desfecho = await self._resolvedor.candidatos_com_desfecho("", phone_hash=phone_hash)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return (), "indisponivel"
        if desfecho == "nenhum" and not refs:
            return (), "nenhum"
        if desfecho == "unico" and len(refs) == 1:
            return tuple(refs), "unico"
        if desfecho == "multiplos" and len(refs) > 1:
            if len(refs) > MAX_CANDIDATOS:
                return (), "nenhum"
            return tuple(refs), "multiplos"
        return (), "indisponivel"

    async def _conferir(
        self, refs: tuple[str, ...], hash_fator: str, fator: str
    ) -> tuple[_ResultadoVerificacao, str | None]:
        """CONFERE o fator contra CADA referencia candidata (e so' contra elas). Verificado so' se EXATAMENTE
        uma conferir e nenhuma ficar sem resposta; qualquer AMH fora = `indisponivel` (nada e' concluido)."""
        conferem: list[str] = []
        for ref in refs:
            try:
                async with asyncio.timeout(self._prazo):
                    resultado = await self._verification.verify(
                        ref,
                        fator=fator,
                        verification_hash=hash_fator,
                        hash_scheme=self._hash_scheme,
                        purpose_of_use=self._purpose,
                        timeout_seconds=self._prazo,
                    )
            except PROGRAMMING_ERRORS:
                raise
            except EXTERNAL_DEPENDENCY_FAILURES:
                return "indisponivel", None
            if not resultado.succeeded or resultado.value is None:
                return "indisponivel", None
            if resultado.value.portable_subject_ref != ref:
                # Eco de outra referencia: resposta fora do contrato, nunca "e' outra pessoa".
                return "indisponivel", None
            if resultado.value.confere is True:
                conferem.append(ref)
        if len(conferem) == 1:
            return "verificado", conferem[0]
        return "nao_verificado", None

    def _telefone_nao_identificado(
        self, atual: EstadoAcesso, agora: datetime, *, emergencia: bool
    ) -> DecisaoAcesso:
        """Telefone sem candidato: NAO entra pelo caminho do documento (decisao do dono, lado seguro; o DPO
        pode relaxar). Texto neutro, que nao diz nada sobre CPF nenhum, e atendente humano. As tentativas nao
        mudam; o prazo do bloqueio e' o mesmo da 3a falha e, vencido, o telefone e' consultado de novo."""
        self._pendentes.descartar(atual.conversation_id)
        novo = replace(atual, estado=BLOQUEADO_HUMANO, bloqueado_ate=agora + self._validade)
        self._log_transicao(
            atual.conversation_id, atual.estado, BLOQUEADO_HUMANO, "telefone_nao_identificado"
        )
        return self._responder(novo, TELEFONE_NAO_IDENTIFICADO, emergencia=emergencia, escalar=True)

    async def _passo_cpf(
        self, atual: EstadoAcesso, agora: datetime, texto: str, numero_cru: str, *, emergencia: bool
    ) -> DecisaoAcesso:
        refs, desfecho = await self._candidatos(numero_cru)
        if desfecho == "indisponivel":
            return self._responder(atual, INDISPONIVEL, emergencia=emergencia)
        if desfecho == "nenhum":
            return self._telefone_nao_identificado(atual, agora, emergencia=emergencia)
        cpf = extrair_cpf(texto)
        if cpf is None:
            return self._falha(atual, agora, CPF_INVALIDO, emergencia=emergencia)
        if desfecho == "multiplos":
            # Telefone de mais de uma pessoa: pede tambem o nascimento (o CPF espera em memoria).
            self._pendentes.guardar(atual.conversation_id, cpf)
            novo = replace(atual, estado=AGUARDANDO_NASCIMENTO)
            self._log_transicao(
                atual.conversation_id, atual.estado, AGUARDANDO_NASCIMENTO, "telefone_multiplo"
            )
            return self._responder(novo, PEDIDO_NASCIMENTO, emergencia=emergencia)
        hash_fator = self._hasher.hash_cpf(cpf)
        if hash_fator is None:
            return self._falha(atual, agora, FALHA_CONFERENCIA, emergencia=emergencia)
        veredito, ref = await self._conferir(refs, hash_fator, FATOR_CPF)
        if veredito == "indisponivel":
            return self._responder(atual, INDISPONIVEL, emergencia=emergencia)
        if veredito == "verificado" and ref is not None:
            return await self._sucesso(atual, agora, ref)
        return self._falha(atual, agora, FALHA_CONFERENCIA, emergencia=emergencia)

    async def _passo_nascimento(
        self, atual: EstadoAcesso, agora: datetime, texto: str, numero_cru: str, *, emergencia: bool
    ) -> DecisaoAcesso:
        leitura = ler_nascimento(texto, hoje=agora.date())
        if not leitura.encontrou or leitura.nascimento is None or leitura.sobra_invalida:
            if leitura.sobra_invalida:
                self._pendentes.descartar(atual.conversation_id)
            return self._falha(atual, agora, FALHA_CONFERENCIA, emergencia=emergencia)
        cpf = leitura.cpf or self._pendentes.ver(atual.conversation_id)
        if cpf is None:
            # A memoria do CPF se perdeu (outra replica, reinicio, prazo): pede os dois juntos, sem gastar
            # tentativa.
            return self._responder(atual, PEDIDO_CPF_E_NASCIMENTO, emergencia=emergencia)
        refs, desfecho = await self._candidatos(numero_cru)
        if desfecho == "indisponivel":
            if leitura.cpf is not None:
                self._pendentes.guardar(atual.conversation_id, leitura.cpf)
            return self._responder(atual, INDISPONIVEL, emergencia=emergencia)
        if desfecho == "nenhum":
            return self._telefone_nao_identificado(atual, agora, emergencia=emergencia)
        hash_fator = self._hasher.hash_cpf_nascimento(cpf, leitura.nascimento.strftime("%Y%m%d"))
        if hash_fator is None:
            return self._falha(atual, agora, FALHA_CONFERENCIA, emergencia=emergencia)
        veredito, ref = await self._conferir(refs, hash_fator, FATOR_CPF_NASCIMENTO)
        if veredito == "indisponivel":
            if leitura.cpf is not None:
                self._pendentes.guardar(atual.conversation_id, leitura.cpf)
            return self._responder(atual, INDISPONIVEL, emergencia=emergencia)
        self._pendentes.descartar(atual.conversation_id)
        if veredito == "verificado" and ref is not None:
            return await self._sucesso(atual, agora, ref)
        return self._falha(atual, agora, FALHA_CONFERENCIA, emergencia=emergencia)

    def _falha(self, atual: EstadoAcesso, agora: datetime, texto: str, *, emergencia: bool) -> DecisaoAcesso:
        """Uma tentativa gasta (formato invalido ou nao confere)."""
        tentativas = atual.tentativas + 1
        if tentativas >= MAX_TENTATIVAS:
            self._pendentes.descartar(atual.conversation_id)
            novo = replace(
                atual,
                estado=BLOQUEADO_HUMANO,
                tentativas=MAX_TENTATIVAS,
                bloqueado_ate=agora + self._validade,
            )
            self._log_transicao(atual.conversation_id, atual.estado, BLOQUEADO_HUMANO, "tentativas_esgotadas")
            return self._responder(novo, BLOQUEADO, emergencia=emergencia, escalar=True)
        # Depois de uma falha na verificacao o CPF ja' foi consumido: volta a pedir do comeco. Formato
        # invalido do nascimento mantem o passo (o CPF segue pendente).
        proximo = atual.estado
        if atual.estado == AGUARDANDO_NASCIMENTO and self._pendentes.ver(atual.conversation_id) is None:
            proximo = AGUARDANDO_CPF
        novo = replace(atual, estado=proximo, tentativas=tentativas)
        self._log_transicao(atual.conversation_id, atual.estado, proximo, "tentativa_falha")
        return self._responder(novo, texto, emergencia=emergencia)

    async def _sucesso(self, atual: EstadoAcesso, agora: datetime, ref: str) -> DecisaoAcesso:
        if atual.portable_subject_ref is not None and atual.portable_subject_ref != ref:
            # Telefone de mais de uma pessoa e quem se verificou agora NAO e' quem se verificou antes. O
            # ACEITO guardado e' da pessoa ANTERIOR: nada de consentimento em nome desta (revisao do #700,
            # P1-b), e a memoria da conversa (historico, memoria clinica) e' da outra pessoa (P2). Volta ao
            # consentimento.
            novo = replace(
                atual,
                estado=SEM_CONSENTIMENTO,
                tentativas=0,
                texto_versao=VERSAO_TEXTO_CONSENTIMENTO,
                texto_sha256=SHA256_TEXTO_CONSENTIMENTO,
                consentido_em=None,
                portable_subject_ref=None,
                consent_ref=None,
                consentimento_pendente_gravacao=False,
                verificado_em=None,
                expira_em=None,
                bloqueado_ate=None,
                regravacoes_falhas=0,
            )
            self._log_transicao(atual.conversation_id, atual.estado, SEM_CONSENTIMENTO, "pessoa_mudou")
            return DecisaoAcesso(
                respostas=(TEXTO_CONSENTIMENTO,),
                prosseguir=False,
                novo=novo,
                nova_verificacao=True,
                esquecer_memoria=True,
            )
        novo = replace(
            atual,
            estado=VERIFICADO,
            tentativas=0,
            portable_subject_ref=ref,
            verificado_em=agora,
            expira_em=agora + self._validade,
            bloqueado_ate=None,
        )
        if (
            novo.consent_ref is None
            and novo.consentido_em is not None
            and not novo.consentimento_pendente_gravacao
        ):
            # Primeira verificacao desde o ACEITO (a referencia agora e' conhecida): registra no lago.
            consent_ref = await self._gravar_consentimento(ref, novo, DECISAO_CONCEDIDA, novo.consentido_em)
            if consent_ref is None:
                novo = self._contar_falha_de_gravacao(
                    replace(novo, consentimento_pendente_gravacao=True), DECISAO_CONCEDIDA
                )
            else:
                novo = replace(
                    novo, consent_ref=consent_ref, consentimento_pendente_gravacao=False, regravacoes_falhas=0
                )
        self._log_transicao(atual.conversation_id, atual.estado, VERIFICADO, "verificado")
        return DecisaoAcesso(
            respostas=(RESPOSTA_VERIFICADO,), prosseguir=False, novo=novo, nova_verificacao=True
        )


def textos_fixos() -> dict[str, str]:
    """Todos os textos fixos do acesso (para a cerca de testes e para a leitura do DPO)."""
    return {
        "consentimento": TEXTO_CONSENTIMENTO,
        "pedido_cpf": PEDIDO_CPF,
        "cpf_invalido": CPF_INVALIDO,
        "pedido_nascimento": PEDIDO_NASCIMENTO,
        "pedido_cpf_e_nascimento": PEDIDO_CPF_E_NASCIMENTO,
        "verificado": RESPOSTA_VERIFICADO,
        "falha_conferencia": FALHA_CONFERENCIA,
        "bloqueado": BLOQUEADO,
        "bloqueado_curto": BLOQUEADO_CURTO,
        "indisponivel": INDISPONIVEL,
        "telefone_nao_identificado": TELEFONE_NAO_IDENTIFICADO,
        "revogado": RESPOSTA_REVOGADO,
        "emergencia": TEXTO_EMERGENCIA,
    }


__all__ = [
    "AGUARDANDO_CPF",
    "AGUARDANDO_NASCIMENTO",
    "BLOQUEADO_HUMANO",
    "FATOR_CPF",
    "FATOR_CPF_NASCIMENTO",
    "MAX_REGRAVACOES",
    "ESTADOS",
    "MAX_TENTATIVAS",
    "MOTIVO_ESCALONAMENTO_BLOQUEIO",
    "REVOGADO",
    "SEM_CONSENTIMENTO",
    "SHA256_TEXTO_CONSENTIMENTO",
    "TEXTO_CONSENTIMENTO",
    "TEXTO_EMERGENCIA",
    "VERIFICADO",
    "VERSAO_TEXTO_CONSENTIMENTO",
    "AcessoBeneficiario",
    "AcessoStore",
    "AcessoStoreEmMemoria",
    "CandidatosDoTelefone",
    "DecisaoAcesso",
    "EstadoAcesso",
    "HasherDeVerificacao",
    "chave_de_idempotencia",
    "cpf_valido",
    "eh_aceito",
    "eh_revogar",
    "extrair_cpf",
    "ler_nascimento",
    "textos_fixos",
]
