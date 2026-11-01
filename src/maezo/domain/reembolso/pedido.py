"""Pedido de reembolso pelo WhatsApp (SP-OP-REEMBOLSO-001 v0.2.0, DL-0160/DL-0161).

Atras da chave `MAEZO_REEMBOLSO_V1` (default desligada). Desligada, NADA deste modulo roda.

O QUE E'. Uma MAQUINA DE ESTADOS DETERMINISTICA que conduz a coleta do pedido: as perguntas, a leitura
das respostas (menus numerados, data, valor), o laco de documentos ("terminou de enviar esse tipo?") e o
resumo com confirmacao. Sem modelo, sem I/O, sem relogio: a funcao `avancar` recebe o estado e o texto e
devolve o proximo estado, o texto a enviar e (so' na confirmacao) a ordem de abrir o processo.

O QUE NAO E'. O Lucas NUNCA diz se a pessoa tem direito ao reembolso, nunca calcula valor e nunca promete
prazo ou pagamento: quem decide e' a analise humana (DL-0160). Sem OCR: o arquivo nao e' lido; "tenho a nota
fiscal" e' o que a pessoa declara. Nenhum texto livre da pessoa e' guardado — so' enumeracoes fechadas, uma
data, um valor em centavos e a referencia opaca de cada documento (Zona PHI, ADR-0006).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Final, Literal

#: O nome da chave (env do receptor e do simulador; Terraform `reembolso_v1`).
CHAVE_REEMBOLSO_V1: Final[str] = "MAEZO_REEMBOLSO_V1"

AGENT_ID_REEMBOLSO: Final[str] = "lucas"
PROCESS_KEY_REEMBOLSO: Final[str] = "SP-OP-REEMBOLSO-001"

# ------------------------------------------------------------------------------------------------
# Resultados do aviso (o `resultado` que o BPMN publica em `agents.events.process_completed`)
# ------------------------------------------------------------------------------------------------
RESULTADO_APROVADO: Final[str] = "reembolso_aprovado"
RESULTADO_PARCIAL: Final[str] = "reembolso_parcial"
RESULTADO_NEGADO: Final[str] = "reembolso_negado"
RESULTADO_INFO_SOLICITADA: Final[str] = "reembolso_info_solicitada"
RESULTADO_PAGO: Final[str] = "reembolso_pago"
RESULTADOS_DO_REEMBOLSO: Final[frozenset[str]] = frozenset(
    {
        RESULTADO_APROVADO,
        RESULTADO_PARCIAL,
        RESULTADO_NEGADO,
        RESULTADO_INFO_SOLICITADA,
        RESULTADO_PAGO,
    }
)

# ------------------------------------------------------------------------------------------------
# Enumeracoes fechadas (contrato SP-OP-REEMBOLSO-001 v0.2.0, "Variaveis de entrada")
# ------------------------------------------------------------------------------------------------
RELACOES: Final[tuple[str, ...]] = ("titular", "dependente")
CATEGORIAS: Final[tuple[str, ...]] = ("consulta", "exame", "terapia")
TIPOS_REEMBOLSO: Final[tuple[str, ...]] = (
    "livre_escolha",
    "fora_rede",
    "urgencia_emergencia",
    "indisponibilidade_rede",
)
MOTIVOS_NAO_REDE: Final[tuple[str, ...]] = ("urgencia", "sem_prestador_no_prazo", "preferencia_fora_rede")
TIPO_DOC_PEDIDO: Final[str] = "pedido_medico"
TIPO_DOC_NOTA: Final[str] = "nota_fiscal"
TIPOS_DOCUMENTO: Final[tuple[str, ...]] = (TIPO_DOC_PEDIDO, TIPO_DOC_NOTA)
#: DL-0162: arquivo mandado na RESPOSTA a um pedido de informacao da equipe (vai em
#: `documentos_adicionais_refs`).
TIPO_DOC_COMPLEMENTO: Final[str] = "complemento"
#: Teto do texto da resposta ao pedido de informacao (depois do scrub), guardado so' ate' a correlacao.
MAX_RESPOSTA_TEXTO: Final[int] = 1000

#: Teto de arquivos por tipo e de valor (centavos): acima disso a pessoa e' orientada, nada e' gravado.
MAX_ARQUIVOS_POR_TIPO: Final[int] = 10
VALOR_MAXIMO_CENTS: Final[int] = 10_000_000_00  # R$ 10 milhoes: teto de sanidade, nao regra de negocio.

Etapa = Literal[
    "tem_nota",
    "tem_pedido",
    "relacao",
    "categoria",
    "tipo",
    "data",
    "valor",
    "motivo",
    "doc_pedido",
    "doc_nota",
    "confirma",
    "aberto",
    "resposta",
    "encerrado",
]
ETAPAS: Final[tuple[str, ...]] = (
    "tem_nota",
    "tem_pedido",
    "relacao",
    "categoria",
    "tipo",
    "data",
    "valor",
    "motivo",
    "doc_pedido",
    "doc_nota",
    "confirma",
    "aberto",
    "resposta",
    "encerrado",
)
#: As etapas da COLETA (antes de o processo existir). `aberto`/`resposta` sao do processo ja' aberto.
ETAPAS_ATIVAS: Final[frozenset[str]] = frozenset(ETAPAS) - {"aberto", "resposta", "encerrado"}

Acao = Literal["nenhuma", "abrir_processo"]


@dataclass(frozen=True, slots=True)
class Documento:
    tipo: str
    document_ref: str
    #: sha256 do conteudo (ADR-0020, principio do `input_hash`): o portal confere ao entregar o arquivo.
    sha256: str = ""


@dataclass(frozen=True, slots=True)
class PedidoReembolso:
    """O estado da coleta. Imutavel; cada avanco devolve outro. Nada aqui e' texto livre da pessoa."""

    etapa: str = "tem_nota"
    relacao_com_titular: str = ""
    categoria_procedimento: str = ""
    tipo_reembolso: str = ""
    data_atendimento: str = ""  # ISO AAAA-MM-DD
    valor_solicitado_cents: int = 0
    motivo_nao_rede: str = ""
    #: A ultima pergunta do laco de documentos foi "terminou de enviar esse tipo?" (aguardando Sim/Nao).
    perguntou_terminou: bool = False
    #: DL-0162: a resposta da pessoa a um pedido de informacao (JA' pelo scrub de identificadores),
    #: guardada so'
    #: ate' o "terminei" que a entrega ao processo. Fora da etapa `resposta` e' sempre vazia.
    resposta_texto: str = ""
    documentos: tuple[Documento, ...] = field(default_factory=tuple)

    def contagem(self, tipo: str) -> int:
        return sum(1 for d in self.documentos if d.tipo == tipo)

    @property
    def precisa_pedido_medico(self) -> bool:
        return self.categoria_procedimento in ("exame", "terapia")

    @property
    def ativo(self) -> bool:
        return self.etapa in ETAPAS_ATIVAS

    @property
    def espera_documento(self) -> bool:
        return self.etapa in ("doc_pedido", "doc_nota")

    def tipo_do_documento_corrente(self) -> str | None:
        return {"doc_pedido": TIPO_DOC_PEDIDO, "doc_nota": TIPO_DOC_NOTA}.get(self.etapa)


@dataclass(frozen=True, slots=True)
class Saida:
    pedido: PedidoReembolso
    texto: str
    acao: str = "nenhuma"


# ------------------------------------------------------------------------------------------------
# Textos (rascunho para dev; revisao juridica e do dono antes de producao). Curtos, sem promessa.
# ------------------------------------------------------------------------------------------------
TEXTO_INICIO: Final[str] = (
    "Posso registrar o seu pedido de reembolso. Quem decide é a nossa equipe de análise. "
    "Você tem a nota fiscal do atendimento? Responda Sim ou Não."
)
TEXTO_SEM_NOTA: Final[str] = (
    "Sem a nota fiscal não consigo registrar o pedido. Quando tiver, é só me chamar aqui. "
    "Nenhum pedido foi aberto."
)
TEXTO_SEM_PEDIDO: Final[str] = (
    "Para exame ou terapia, é preciso o pedido médico. Quando tiver, é só me chamar aqui. "
    "Nenhum pedido foi aberto."
)
TEXTO_PERGUNTA_RELACAO: Final[str] = (
    "O reembolso é do titular do plano ou de um dependente? Responda Titular ou Dependente."
)
TEXTO_PERGUNTA_CATEGORIA: Final[str] = (
    "Qual foi o atendimento? Responda com o número: 1 - Consulta, 2 - Exame, 3 - Terapia."
)
TEXTO_PERGUNTA_PEDIDO: Final[str] = "Você tem o pedido médico? Responda Sim ou Não."
TEXTO_PERGUNTA_TIPO: Final[str] = (
    "Como foi o atendimento? Responda com o número: 1 - Escolhi o profissional por conta própria, "
    "2 - Fora da rede do plano, 3 - Urgência ou emergência, 4 - Não havia prestador da rede disponível."
)
TEXTO_PERGUNTA_DATA: Final[str] = "Qual foi a data do atendimento? Escreva assim: 25/10/2026."
TEXTO_PERGUNTA_VALOR: Final[str] = "Qual o valor que você pagou, em reais? Exemplo: 150,00."
TEXTO_PERGUNTA_MOTIVO: Final[str] = (
    "Por que não usou a rede do plano? Responda com o número: 1 - Urgência, "
    "2 - Não havia prestador no prazo, 3 - Preferi outro profissional."
)
TEXTO_ENVIE_PEDIDO: Final[str] = (
    "Agora envie a foto ou o PDF do pedido médico. Se tiver mais de um arquivo, pode mandar todos."
)
TEXTO_ENVIE_NOTA: Final[str] = (
    "Agora envie a foto ou o PDF da nota fiscal. Se tiver mais de um arquivo, pode mandar todos."
)
TEXTO_PROXIMO_ARQUIVO: Final[str] = "Certo. Pode enviar o próximo arquivo."
TEXTO_ARQUIVO_RECEBIDO: Final[str] = (
    "Recebi o arquivo. Você terminou de enviar o {tipo}? Responda Sim ou Não."
)
TEXTO_PRECISA_ARQUIVO: Final[str] = "Ainda não recebi o {tipo}. Envie a foto ou o PDF por aqui."
TEXTO_LIMITE_ARQUIVOS: Final[str] = (
    "Chegamos ao limite de arquivos desse tipo. Se já enviou tudo, responda Sim."
)
TEXTO_NAO_ENTENDI: Final[str] = "Não entendi. {pergunta}"
TEXTO_DATA_INVALIDA: Final[str] = "Essa data não é válida. {pergunta}"
TEXTO_VALOR_INVALIDO: Final[str] = "Não consegui entender o valor. {pergunta}"
TEXTO_ARQUIVO_INVALIDO: Final[str] = (
    "Esse tipo de arquivo não é aceito. Envie uma foto (JPG ou PNG) ou um PDF."
)
TEXTO_ARQUIVO_FORA_DE_HORA: Final[str] = "Agora preciso de uma resposta por escrito. {pergunta}"
TEXTO_FALHA_AO_RECEBER: Final[str] = "Não consegui receber esse arquivo. Tente enviar de novo."
TEXTO_DESISTIU: Final[str] = "Tudo bem, não registrei nenhum pedido. Se mudar de ideia, é só me chamar aqui."
TEXTO_PEDIDO_RECEBIDO: Final[str] = (
    "Recebi o seu pedido de reembolso. Protocolo: {protocolo}. A nossa equipe vai analisar. "
    "Quando houver uma resposta, você recebe por aqui."
)
TEXTO_PEDIDO_JA_REGISTRADO: Final[str] = (
    "O seu pedido de reembolso já está com a nossa equipe. Protocolo: {protocolo}."
)
TEXTO_FALHA_AO_REGISTRAR: Final[str] = (
    "Não consegui registrar o seu pedido agora. Nada foi enviado para análise. Tente de novo daqui a pouco."
)

# Retorno (aviso do processo). Textos FIXOS; o texto escrito pelo analista entra entre aspas, como escrito.
TEXTO_RETORNO_APROVADO: Final[str] = (
    "Sobre o seu pedido de reembolso (protocolo {protocolo}): a nossa equipe aprovou. "
    "O financeiro vai fazer o pagamento e você recebe um aviso quando ele for feito."
)
TEXTO_RETORNO_PARCIAL: Final[str] = (
    "Sobre o seu pedido de reembolso (protocolo {protocolo}): a nossa equipe aprovou uma parte do valor. "
    "Mensagem da equipe: “{texto}”. Quando o pagamento for feito, você recebe um aviso."
)
TEXTO_RETORNO_NEGADO: Final[str] = (
    "Sobre o seu pedido de reembolso (protocolo {protocolo}): a nossa equipe analisou e não aprovou. "
    "Mensagem da equipe: “{texto}”."
)
TEXTO_RETORNO_INFO: Final[str] = (
    "Sobre o seu pedido de reembolso (protocolo {protocolo}): a nossa equipe precisa de mais informações. "
    "Mensagem da equipe: “{texto}”. Mande a informação ou os arquivos nesta conversa e, no fim, "
    "escreva TERMINEI."
)
TEXTO_RETORNO_PAGO: Final[str] = (
    "Sobre o seu pedido de reembolso (protocolo {protocolo}): o pagamento foi feito em {data}."
)

#: As cercas de saida barraram o texto escrito pela equipe: o aviso sai SEM ele, e diz isso.
TEXTO_RETORNO_TEXTO_BARRADO: Final[str] = (
    "Sobre o seu pedido de reembolso (protocolo {protocolo}): há uma resposta da nossa equipe, mas não foi "
    "possível repassar o texto por aqui. Para saber o andamento, fale com a central de atendimento do plano."
)

# Resposta a um pedido de informacao da equipe (DL-0162).
TEXTO_RESPOSTA_INICIADA: Final[str] = (
    "Recebi. Se tiver mais alguma informação ou arquivo para a equipe, pode mandar agora. "
    "Quando terminar, responda TERMINEI."
)
TEXTO_RESPOSTA_ANOTADA: Final[str] = "Anotado. Quando terminar, responda TERMINEI."
TEXTO_RESPOSTA_LONGA: Final[str] = (
    "Essa mensagem ficou longa demais para eu repassar. Pode resumir em poucas linhas?"
)
TEXTO_RESPOSTA_VAZIA: Final[str] = (
    "Ainda não recebi nenhuma informação ou arquivo. O que você quer enviar à equipe?"
)
TEXTO_RESPOSTA_ENVIADA: Final[str] = (
    "Enviei a sua resposta para a nossa equipe. Protocolo: {protocolo}. Quando houver uma resposta, você "
    "recebe por aqui."
)
TEXTO_RESPOSTA_FALHOU: Final[str] = (
    "Não consegui enviar a sua resposta agora. Nada se perdeu: responda TERMINEI de novo daqui a pouco."
)
TEXTO_RESPOSTA_ARQUIVO: Final[str] = (
    "Recebi o arquivo. Se tiver mais, pode mandar. Quando terminar, responda TERMINEI."
)

#: Versao dos textos acima (auditoria; sobe a cada mudanca de redacao).
TEXTOS_REEMBOLSO_VERSAO: Final[str] = "reembolso-v2"

_NOME_DO_DOCUMENTO: Final[dict[str, str]] = {
    TIPO_DOC_PEDIDO: "pedido médico",
    TIPO_DOC_NOTA: "nota fiscal",
}

_PERGUNTA_DA_ETAPA: Final[dict[str, str]] = {
    "tem_nota": "Você tem a nota fiscal do atendimento? Responda Sim ou Não.",
    "tem_pedido": TEXTO_PERGUNTA_PEDIDO,
    "relacao": TEXTO_PERGUNTA_RELACAO,
    "categoria": TEXTO_PERGUNTA_CATEGORIA,
    "tipo": TEXTO_PERGUNTA_TIPO,
    "data": TEXTO_PERGUNTA_DATA,
    "valor": TEXTO_PERGUNTA_VALOR,
    "motivo": TEXTO_PERGUNTA_MOTIVO,
}


# ------------------------------------------------------------------------------------------------
# Leitura das respostas
# ------------------------------------------------------------------------------------------------
def normalizar(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in base if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9/,.\s$]", " ", sem_acento.casefold()).strip()


_SIM: Final[frozenset[str]] = frozenset(
    {
        "sim",
        "s",
        "tenho",
        "tenho sim",
        "isso",
        "isso mesmo",
        "ok",
        "claro",
        "confirmo",
        "confirmado",
        "pode",
        "pode ser",
    }
)
_NAO: Final[frozenset[str]] = frozenset(
    {
        "nao",
        "n",
        "nao tenho",
        "ainda nao",
        "nao sei",
        "negativo",
        "nao quero",
        "deixa",
        "deixa pra la",
        "esquece",
    }
)
_DESISTE: Final[frozenset[str]] = frozenset(
    {
        "cancelar",
        "cancela",
        "desisto",
        "deixa pra la",
        "esquece",
        "melhor nao",
        "mudei de ideia",
        "nao quero mais",
    }
)


def ler_sim_nao(texto: str) -> bool | None:
    t = normalizar(texto)
    if t in _SIM:
        return True
    if t in _NAO:
        return False
    return None


_TERMINEI: Final[frozenset[str]] = frozenset(
    {
        "terminei",
        "termino",
        "pronto",
        "so isso",
        "e so isso",
        "acabei",
        "finalizei",
        "enviei tudo",
        "pode enviar",
    }
)


def e_terminei(texto: str) -> bool:
    return normalizar(texto) in _TERMINEI


def e_desistencia(texto: str) -> bool:
    return normalizar(texto) in _DESISTE


def ler_relacao(texto: str) -> str | None:
    t = normalizar(texto)
    if t in ("titular", "eu", "eu mesmo", "eu mesma", "para mim", "pra mim", "meu", "1"):
        return "titular"
    if t in ("dependente", "2") or any(
        p in t.split() for p in ("filho", "filha", "esposa", "esposo", "marido", "mae", "pai", "dependente")
    ):
        return "dependente"
    return None


def _opcao(texto: str, opcoes: tuple[str, ...]) -> str | None:
    t = normalizar(texto)
    if t.isdigit() and 1 <= int(t) <= len(opcoes):
        return opcoes[int(t) - 1]
    return None


def ler_categoria(texto: str) -> str | None:
    escolha = _opcao(texto, CATEGORIAS)
    if escolha:
        return escolha
    t = normalizar(texto)
    return t if t in CATEGORIAS else None


def ler_tipo(texto: str) -> str | None:
    return _opcao(texto, TIPOS_REEMBOLSO)


def ler_motivo(texto: str) -> str | None:
    return _opcao(texto, MOTIVOS_NAO_REDE)


_DATA: Final[re.Pattern[str]] = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$")


def ler_data(texto: str, *, hoje: date) -> str | None:
    """`DD/MM/AAAA` (ou `DD/MM/AA`) -> `AAAA-MM-DD`. Data invalida, futura ou anterior a 5 anos -> `None`."""
    m = _DATA.match(texto.strip())
    if not m:
        return None
    dia, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if ano < 100:
        ano += 2000
    try:
        data = date(ano, mes, dia)
    except ValueError:
        return None
    if data > hoje or (hoje - data).days > 365 * 5:
        return None
    return data.isoformat()


_VALOR: Final[re.Pattern[str]] = re.compile(r"^(?:r\$\s*)?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?$")


def ler_valor_cents(texto: str) -> int | None:
    """`150`, `150,50`, `R$ 1.200,00` -> centavos inteiros. Zero, negativo ou acima do teto -> `None`."""
    m = _VALOR.match(texto.strip().casefold())
    if not m:
        return None
    reais = int(m.group(1).replace(".", ""))
    centavos = int((m.group(2) or "0").ljust(2, "0"))
    total = reais * 100 + centavos
    return total if 0 < total <= VALOR_MAXIMO_CENTS else None


# ------------------------------------------------------------------------------------------------
# Protocolo e variaveis do processo
# ------------------------------------------------------------------------------------------------
_SEMENTE_PROTOCOLO: Final[str] = "maezo-reembolso-protocolo/v1"
_PROTOCOLO: Final[re.Pattern[str]] = re.compile(r"^REE-[0-9A-F]{4}-[0-9A-F]{4}$")


def protocolo_do_pedido(conversation_id: str, pedido: PedidoReembolso) -> str:
    """`REE-XXXX-XXXX`, derivado da conversa e do CONTEUDO do pedido (nunca de PII em claro).

    Estavel: o mesmo pedido confirmado duas vezes gera o mesmo protocolo (e a mesma chave de negocio), e o
    start idempotente reencontra a instancia viva. Um segundo pedido da mesma pessoa (outra data, valor ou
    documento) gera outro protocolo."""
    if not conversation_id.strip():
        raise ValueError("protocolo_do_pedido: conversation_id ausente")
    docs = ",".join(f"{d.tipo}:{d.document_ref}" for d in pedido.documentos)
    base = "|".join(
        (
            _SEMENTE_PROTOCOLO,
            conversation_id.strip(),
            pedido.data_atendimento,
            str(pedido.valor_solicitado_cents),
            pedido.categoria_procedimento,
            docs,
        )
    )
    digest = hashlib.sha256(base.encode()).hexdigest().upper()
    return f"REE-{digest[:4]}-{digest[4:8]}"


def protocolo_valido(valor: object) -> bool:
    return isinstance(valor, str) and _PROTOCOLO.match(valor) is not None


def pedido_completo(pedido: PedidoReembolso) -> bool:
    """Todos os campos do contrato preenchidos e ao menos um arquivo de cada tipo exigido."""
    if not (
        pedido.relacao_com_titular in RELACOES
        and pedido.categoria_procedimento in CATEGORIAS
        and pedido.tipo_reembolso in TIPOS_REEMBOLSO
        and pedido.data_atendimento
        and pedido.valor_solicitado_cents > 0
    ):
        return False
    if pedido.tipo_reembolso != "livre_escolha" and pedido.motivo_nao_rede not in MOTIVOS_NAO_REDE:
        return False
    if pedido.contagem(TIPO_DOC_NOTA) < 1:
        return False
    return not (pedido.precisa_pedido_medico and pedido.contagem(TIPO_DOC_PEDIDO) < 1)


def documentos_refs(pedido: PedidoReembolso) -> list[dict[str, str]]:
    """O valor de `documentos_refs` do contrato: `{tipo, document_ref}`, pedido medico antes da nota."""
    ordem = {TIPO_DOC_PEDIDO: 0, TIPO_DOC_NOTA: 1}
    return [
        {"tipo": d.tipo, "document_ref": d.document_ref, "sha256": d.sha256}
        for d in sorted(pedido.documentos, key=lambda d: ordem.get(d.tipo, 9))
    ]


def _motivo_do_processo(pedido: PedidoReembolso) -> str:
    """`motivo_nao_rede` e obrigatorio no contrato; sem motivo vale `preferencia_fora_rede`."""
    return pedido.motivo_nao_rede or "preferencia_fora_rede"


def variaveis_do_processo(
    pedido: PedidoReembolso,
    *,
    tenant_id: str,
    conversation_id: str,
    beneficiario_pseudo_id: str,
    hoje: date,
) -> dict[str, object]:
    """As variaveis de entrada de SP-OP-REEMBOLSO-001 que o Lucas conhece (contrato v0.2.0).

    Nada de calculo: `valor_solicitado_cents` e' o que a pessoa disse que pagou. A chave do aviso ao WhatsApp
    (`aviso_beneficiario_whatsapp`/`agent_id`/`conversation_id`) liga os avisos do BPMN a esta conversa."""
    if not pedido_completo(pedido):
        raise ValueError("variaveis_do_processo: pedido incompleto")
    return {
        "tenant_id": tenant_id,
        "protocolo_reembolso": protocolo_do_pedido(conversation_id, pedido),
        "beneficiario_pseudo_id": beneficiario_pseudo_id,
        "matricula_beneficiario": beneficiario_pseudo_id,
        "relacao_com_titular": pedido.relacao_com_titular,
        "tipo_reembolso": pedido.tipo_reembolso,
        "motivo_nao_rede": _motivo_do_processo(pedido),
        "categoria_procedimento": pedido.categoria_procedimento,
        "valor_solicitado_cents": pedido.valor_solicitado_cents,
        "data_atendimento": pedido.data_atendimento,
        "data_solicitacao": hoje.isoformat(),
        "documentos_refs": documentos_refs(pedido),
        "documentacao_completa": True,
        "aviso_beneficiario_whatsapp": True,
        "agent_id": AGENT_ID_REEMBOLSO,
        "conversation_id": conversation_id,
    }


# ------------------------------------------------------------------------------------------------
# A maquina
# ------------------------------------------------------------------------------------------------
def iniciar() -> Saida:
    return Saida(PedidoReembolso(etapa="tem_nota"), TEXTO_INICIO)


def _encerrar(pedido: PedidoReembolso, texto: str) -> Saida:
    return Saida(replace(pedido, etapa="encerrado"), texto)


def _pergunta(etapa: str) -> str:
    return _PERGUNTA_DA_ETAPA.get(etapa, "")


def _resumo(pedido: PedidoReembolso) -> str:
    valor = (
        f"R$ {pedido.valor_solicitado_cents // 100:,}".replace(",", ".")
        + f",{pedido.valor_solicitado_cents % 100:02d}"
    )
    ano, mes, dia = pedido.data_atendimento.split("-")
    arquivos = pedido.contagem(TIPO_DOC_NOTA) + pedido.contagem(TIPO_DOC_PEDIDO)
    return (
        f"Confira o seu pedido: atendimento de {dia}/{mes}/{ano}, valor de {valor}, "
        f"{arquivos} arquivo(s) enviado(s). "
        "Posso registrar? Responda Sim para confirmar."
    )


def _depois_dos_dados(pedido: PedidoReembolso) -> Saida:
    """Passa dos dados para os documentos: pedido medico (se exigido) e depois a nota fiscal."""
    if pedido.precisa_pedido_medico:
        return Saida(replace(pedido, etapa="doc_pedido", perguntou_terminou=False), TEXTO_ENVIE_PEDIDO)
    return Saida(replace(pedido, etapa="doc_nota", perguntou_terminou=False), TEXTO_ENVIE_NOTA)


def avancar(pedido: PedidoReembolso, texto: str, *, hoje: date) -> Saida:
    """Um turno de TEXTO da pessoa. Devolve o novo estado, o texto a enviar e a acao (so' na confirmacao)."""
    if not pedido.ativo:
        return Saida(pedido, "")
    if e_desistencia(texto) and pedido.etapa != "confirma":
        return _encerrar(pedido, TEXTO_DESISTIU)
    etapa = pedido.etapa

    if etapa == "tem_nota":
        resposta = ler_sim_nao(texto)
        if resposta is None:
            return Saida(pedido, TEXTO_NAO_ENTENDI.format(pergunta=_pergunta(etapa)))
        if not resposta:
            return _encerrar(pedido, TEXTO_SEM_NOTA)
        return Saida(replace(pedido, etapa="relacao"), TEXTO_PERGUNTA_RELACAO)

    if etapa == "relacao":
        relacao = ler_relacao(texto)
        if relacao is None:
            return Saida(pedido, TEXTO_NAO_ENTENDI.format(pergunta=_pergunta(etapa)))
        return Saida(
            replace(pedido, relacao_com_titular=relacao, etapa="categoria"), TEXTO_PERGUNTA_CATEGORIA
        )

    if etapa == "categoria":
        categoria = ler_categoria(texto)
        if categoria is None:
            return Saida(pedido, TEXTO_NAO_ENTENDI.format(pergunta=_pergunta(etapa)))
        proxima = "tem_pedido" if categoria in ("exame", "terapia") else "tipo"
        return Saida(replace(pedido, categoria_procedimento=categoria, etapa=proxima), _pergunta(proxima))

    if etapa == "tem_pedido":
        resposta = ler_sim_nao(texto)
        if resposta is None:
            return Saida(pedido, TEXTO_NAO_ENTENDI.format(pergunta=_pergunta(etapa)))
        if not resposta:
            return _encerrar(pedido, TEXTO_SEM_PEDIDO)
        return Saida(replace(pedido, etapa="tipo"), TEXTO_PERGUNTA_TIPO)

    if etapa == "tipo":
        tipo = ler_tipo(texto)
        if tipo is None:
            return Saida(pedido, TEXTO_NAO_ENTENDI.format(pergunta=_pergunta(etapa)))
        return Saida(replace(pedido, tipo_reembolso=tipo, etapa="data"), TEXTO_PERGUNTA_DATA)

    if etapa == "data":
        data = ler_data(texto, hoje=hoje)
        if data is None:
            return Saida(pedido, TEXTO_DATA_INVALIDA.format(pergunta=_pergunta(etapa)))
        return Saida(replace(pedido, data_atendimento=data, etapa="valor"), TEXTO_PERGUNTA_VALOR)

    if etapa == "valor":
        valor = ler_valor_cents(texto)
        if valor is None:
            return Saida(pedido, TEXTO_VALOR_INVALIDO.format(pergunta=_pergunta(etapa)))
        novo = replace(pedido, valor_solicitado_cents=valor)
        if novo.tipo_reembolso == "livre_escolha":
            return _depois_dos_dados(novo)
        return Saida(replace(novo, etapa="motivo"), TEXTO_PERGUNTA_MOTIVO)

    if etapa == "motivo":
        motivo = ler_motivo(texto)
        if motivo is None:
            return Saida(pedido, TEXTO_NAO_ENTENDI.format(pergunta=_pergunta(etapa)))
        return _depois_dos_dados(replace(pedido, motivo_nao_rede=motivo))

    if etapa in ("doc_pedido", "doc_nota"):
        return _texto_no_laco_de_documentos(pedido, texto)

    if etapa == "confirma":
        resposta = ler_sim_nao(texto)
        if resposta is True:
            return Saida(pedido, "", "abrir_processo")
        if resposta is False or e_desistencia(texto):
            return _encerrar(pedido, TEXTO_DESISTIU)
        return Saida(pedido, TEXTO_NAO_ENTENDI.format(pergunta=_resumo(pedido)))

    return Saida(pedido, "")


def _texto_no_laco_de_documentos(pedido: PedidoReembolso, texto: str) -> Saida:
    tipo = pedido.tipo_do_documento_corrente()
    if tipo is None:
        raise AssertionError("laco de documentos sem documento corrente")
    nome = _NOME_DO_DOCUMENTO[tipo]
    resposta = ler_sim_nao(texto)
    if pedido.contagem(tipo) == 0:
        # Ainda nao ha arquivo deste tipo: texto nao adianta nada; repete o pedido do arquivo.
        return Saida(replace(pedido, perguntou_terminou=False), TEXTO_PRECISA_ARQUIVO.format(tipo=nome))
    if not pedido.perguntou_terminou:
        return Saida(replace(pedido, perguntou_terminou=True), TEXTO_ARQUIVO_RECEBIDO.format(tipo=nome))
    if resposta is None:
        return Saida(pedido, TEXTO_NAO_ENTENDI.format(pergunta=TEXTO_ARQUIVO_RECEBIDO.format(tipo=nome)))
    if resposta is False:
        return Saida(replace(pedido, perguntou_terminou=False), TEXTO_PROXIMO_ARQUIVO)
    # Sim: terminou este tipo.
    if pedido.etapa == "doc_pedido":
        return Saida(replace(pedido, etapa="doc_nota", perguntou_terminou=False), TEXTO_ENVIE_NOTA)
    return Saida(replace(pedido, etapa="confirma", perguntou_terminou=False), _resumo(pedido))


def receber_documento(pedido: PedidoReembolso, document_ref: str, sha256: str = "") -> Saida:
    """Um ARQUIVO aceito e ja' custodiado (`document_ref` opaco). So' vale na etapa de documentos."""
    tipo = pedido.tipo_do_documento_corrente()
    if tipo is None:
        return Saida(pedido, TEXTO_ARQUIVO_FORA_DE_HORA.format(pergunta=_pergunta(pedido.etapa) or ""))
    if not document_ref.strip():
        raise ValueError("receber_documento: document_ref ausente")
    if pedido.contagem(tipo) >= MAX_ARQUIVOS_POR_TIPO:
        return Saida(replace(pedido, perguntou_terminou=True), TEXTO_LIMITE_ARQUIVOS)
    novo = replace(
        pedido,
        documentos=(
            *pedido.documentos,
            Documento(tipo=tipo, document_ref=document_ref.strip(), sha256=sha256),
        ),
        perguntou_terminou=True,
    )
    return Saida(novo, TEXTO_ARQUIVO_RECEBIDO.format(tipo=_NOME_DO_DOCUMENTO[tipo]))


def arquivo_nao_esperado(pedido: PedidoReembolso) -> str:
    """O texto quando chega um arquivo fora da etapa de documentos (nada e' guardado)."""
    return TEXTO_ARQUIVO_FORA_DE_HORA.format(pergunta=_pergunta(pedido.etapa) or "")


# ------------------------------------------------------------------------------------------------
# Gatilho
# ------------------------------------------------------------------------------------------------
_GATILHO: Final[re.Pattern[str]] = re.compile(r"\breembols\w*|\bressarcimento\b|\bressarcir\b")


def pede_reembolso(texto: str) -> bool:
    """A mensagem fala de reembolso (lexico fechado). Quem decide se abre a coleta e' o despachante, que
    tambem confere a chave e que nao haja pedido de pessoa ou sinal de saude na mesma mensagem."""
    return _GATILHO.search(normalizar(texto)) is not None


# ------------------------------------------------------------------------------------------------
# Retorno (aviso do processo)
# ------------------------------------------------------------------------------------------------
def data_br(iso: object) -> str | None:
    """`AAAA-MM-DD` -> `DD/MM/AAAA`; qualquer outra forma -> `None` (nunca se inventa data)."""
    if not isinstance(iso, str) or len(iso.strip()) != 10:
        return None
    try:
        dia = date.fromisoformat(iso.strip())
    except ValueError:
        return None
    return dia.strftime("%d/%m/%Y")


def texto_do_retorno(
    resultado: str, *, protocolo: str, texto_da_equipe: str = "", data_pagamento_iso: object = None
) -> str:
    """O texto de cada retorno. Levanta `ValueError` quando falta o fato que o texto afirma (protocolo,
    texto da equipe, data do pagamento): quem chama nao envia nada em vez de inventar."""
    if not protocolo_valido(protocolo):
        raise ValueError("texto_do_retorno: protocolo fora da forma")
    equipe = texto_da_equipe.strip()
    if resultado == RESULTADO_APROVADO:
        return TEXTO_RETORNO_APROVADO.format(protocolo=protocolo)
    if resultado == RESULTADO_PAGO:
        data = data_br(data_pagamento_iso)
        if data is None:
            raise ValueError("texto_do_retorno: pagamento sem data valida")
        return TEXTO_RETORNO_PAGO.format(protocolo=protocolo, data=data)
    if resultado in (RESULTADO_PARCIAL, RESULTADO_NEGADO, RESULTADO_INFO_SOLICITADA):
        if not equipe:
            raise ValueError("texto_do_retorno: falta o texto escrito pela equipe")
        modelo = {
            RESULTADO_PARCIAL: TEXTO_RETORNO_PARCIAL,
            RESULTADO_NEGADO: TEXTO_RETORNO_NEGADO,
            RESULTADO_INFO_SOLICITADA: TEXTO_RETORNO_INFO,
        }[resultado]
        return modelo.format(protocolo=protocolo, texto=equipe)
    raise ValueError("texto_do_retorno: resultado fora do contrato")
