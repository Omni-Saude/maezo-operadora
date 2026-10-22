"""O FLUXO da cobertura assistida (DL-0128; reordenacao e estado da conversa, DL-0129; conversa simples,
DL-0131): orquestra extracao, traducao para o nome oficial, escolha entre os candidatos, confirmacao, opcoes,
para quem, tentativas e a consulta da AMH. As dependencias de rede vem INJETADAS (o grafo as fornece), entao o
fluxo e' testavel sem AWS. Textos e leituras puras em `cobertura_assistida.py`.

Contrato com o grafo: `responder(...)` devolve o `update` do turno (texto, pendencia, contexto) — sempre um
texto, nunca `None`, para a pergunta de cobertura na allowlist.

TRADUCAO (DL-0129): a busca RECUPERA ~20 candidatos (significado + lexico, so' itens assistenciais) e o GLM-5
ESCOLHE um da lista com uma confianca: alta = consulta direta; media = confirmacao de uma linha; baixa =
ate' 3 opcoes ou pedido de outra forma. O modelo so' escolhe da lista; quem diz se cobre e' o cadastro. Sem
o modelo, a decisao por nota de antes. Uma LISTA vai ao modelo numa chamada so' (DL-0131).

CONVERSA SIMPLES (DL-0131, decisao do dono de 20/10/2026):
  * TITULAR POR PADRAO: nunca se pergunta "para quem" quando a mensagem nao cita outra pessoa; a resposta diz
    "Para voce (titular)" e convida a dizer o nome do dependente. A pessoa escolhida e' lembrada na conversa.
  * A intencao sem o nome do exame pede a lista; a LISTA inteira e' respondida (ate' 15), sem limite
    escondido; o que nao foi localizado sai numa linha, com a oferta da equipe UMA vez no fim.
  * Pendencia: so' a resposta CURTA e' lida contra ela (o grafo deixa a pergunta nova escapar). "Eu ja' disse"
    e' resposta, com um pedido de desculpas curto; irritacao pede desculpa e tenta de novo, nunca abre caso.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import structlog

from maezo.agents.helena import cobertura_assistida as ca
from maezo.agents.helena.cobertura_rol import AVISO_DO_DEPENDENTE, literais_da_cobertura, termo_da_continuacao
from maezo.conhecimento.catalogo_procedimentos import BuscaDeProcedimentos, Candidato, administrativo
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

PEND_RESPONDIDO = "COB2:RESPONDIDO"
_TITULAR = re.compile(
    r"^(?:1|um|opcao 1|para mim|pra mim|eu|e para mim|e pra mim|mim|titular|o titular|a titular|para o "
    r"titular"
    r"|pra eu|para eu|e pra eu|sou eu|e eu|e pra mim mesmo|pra mim mesmo|para mim mesmo|para mim mesma"
    r"|pra mim mesma)$"
)
#: DL-0131: o titular DITO no meio de uma frase ("eu ja' tinha dito que e' pra mim, titular").
_TITULAR_NO_TEXTO = re.compile(
    r"\b(?:titular|para mim|pra mim|eu mesm[oa]|sou eu|e pra mim|e para mim|em mim)\b"
)
_GRAUS_POR_PALAVRA: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:filh[oa]s?|menino|menina|bebe)\b"), "filho"),
    (re.compile(r"\b(?:esposa|marido|conjuge|mulher|companheir[oa])\b"), "conjuge"),
    (re.compile(r"\b(?:entead[oa])\b"), "enteado"),
    (re.compile(r"\b(?:net[oa])\b"), "neto"),
)
_TERCEIRO = re.compile(
    r"\b(?:minha mae|meu pai|minha avo|meu avo|meu irmao|minha irma|minha tia|meu tio|minha sogra|meu sogro"
    r"|um amigo|uma amiga|meu amigo|minha amiga|minha prima|meu primo|para (?:a |o )?(?:mae|pai))\b"
)
#: Decisoes da traducao de um termo.
DIRETO, CONFIRMA, OPCOES, NENHUM, VAGO = "direto", "confirma", "opcoes", "nenhum", "vago"
#: Conferencia da escolha "alta": o item escolhido precisa dividir PALAVRAS com o termo da pessoa ou com o
#: nome tecnico (nota lexica). Abaixo disso a escolha vira confirmacao de uma linha (nunca consulta direta de
#: um item que nao tem nada a ver com o que foi escrito). Regra geral, sem termo.
LEXICO_MINIMO_PARA_DIRETO = 0.3
#: DL-0131 (velocidade): leituras de cobertura da AMH em paralelo por resposta.
LEITURAS_AMH_SIMULTANEAS = 4


def quem_pelo_texto(texto: str, graus: Sequence[str], primeiros_nomes: Mapping[int, str]) -> str | None:
    """`titular`, `dependente:N` (1..), `terceiro` ou `None` (ainda nao da' para saber)."""
    plano = ca.normalizar(texto)
    plano = re.sub(r"^(?:e |eh |e para |e pra |para |pra |pro |pra a |pro o )", "", plano).strip()
    if _TITULAR.match(plano) or _TITULAR.match(ca.normalizar(texto)):
        return "titular"
    m = re.fullmatch(r"(?:opcao )?([2-9])", plano)
    if m:
        n = int(m.group(1)) - 1
        return f"dependente:{n}" if 1 <= n <= len(graus) else None
    palavras = set(plano.split())
    por_nome = [n for n, primeiro in primeiros_nomes.items() if primeiro and primeiro in palavras]
    if len(por_nome) == 1:
        return f"dependente:{por_nome[0]}"
    if _TERCEIRO.search(plano):
        return "terceiro"
    for padrao, grau in _GRAUS_POR_PALAVRA:
        if padrao.search(plano):
            iguais = [i for i, g in enumerate(graus, start=1) if grau in ca.normalizar(g)]
            if len(iguais) == 1:
                return f"dependente:{iguais[0]}"
            if not iguais:
                return "fora_do_plano"
            return None
    if _TITULAR_NO_TEXTO.search(plano):
        return "titular"
    return None


#: Quantos itens do catalogo precisam conter o termo para ele contar como CATEGORIA ("ultrassom",
#: "ressonancia").
ITENS_MINIMOS_DA_CATEGORIA = 2


def _e_categoria(
    busca: BuscaDeProcedimentos, termo: str, tecnico: str, candidatos: Sequence[Candidato]
) -> bool:
    """O termo (ou o nome tecnico) aparece, pelo limiar lexico da escolha direta, em pelo menos
    `ITENS_MINIMOS_DA_CATEGORIA` candidatos do catalogo — so' entao "tem varios tipos" e' verdade."""

    def nota(c: Candidato) -> float:
        return max(
            busca.lexico(termo, c.procedimento), busca.lexico(tecnico, c.procedimento) if tecnico else 0.0
        )

    return sum(1 for c in candidatos if nota(c) >= LEXICO_MINIMO_PARA_DIRETO) >= ITENS_MINIMOS_DA_CATEGORIA


def cita_outra_pessoa(texto: str) -> bool:
    """DL-0131: a frase cita alguem que nao e' o titular ("meu filho", "minha esposa", "minha mae")."""
    plano = ca.normalizar(texto)
    return bool(_TERCEIRO.search(plano)) or any(p.search(plano) for p, _ in _GRAUS_POR_PALAVRA)


@dataclass
class Dependencias:
    """O que o fluxo precisa do mundo (o grafo fornece)."""

    extrair: Callable[[str], Awaitable[ca.Pedido | None]]
    cobertura: Callable[[str], Awaitable[Any]]
    dependentes: Callable[[], Awaitable[tuple[list[str], bool, Mapping[int, str]]]]
    elegibilidade: Callable[[], Awaitable[Mapping[str, Any] | None]]
    busca: BuscaDeProcedimentos | None
    pergunta_para_quem: Callable[[list[str], bool], str]
    texto_geral: Callable[[], Awaitable[str]]
    #: (termos do pedido, termo [+ nome tecnico], nomes dos candidatos) -> a escolha do modelo, ou `None`.
    escolher: Callable[[Sequence[str], str, Sequence[str]], Awaitable[ca.Escolha | None]] | None = None
    #: A CPT vigente do TITULAR verificado (DL-0130; `CptVigenteView` ou `None`), lida no maximo uma vez por
    #: resposta (o grafo guarda em memoria). Sem ela, nenhum aviso de CPT.
    cpt: Callable[[], Awaitable[Any | None]] | None = None
    #: DL-0131: a escolha de VARIOS termos numa chamada so' ([(termo, nomes dos candidatos)]) -> uma escolha
    #: por termo (`None` onde o modelo nao respondeu). Sem ela, uma chamada por termo.
    escolher_varios: (
        Callable[[Sequence[tuple[str, Sequence[str]]]], Awaitable[list[ca.Escolha | None]]] | None
    ) = None
    #: DL-0131: a pessoa escolhida nesta conversa ("titular"/"dependente:N"), lembrada pelo grafo.
    quem_lembrado: Callable[[], str | None] | None = None
    lembrar_quem: Callable[[str], None] | None = None
    #: DL-0131: o rotulo de quem ("Para voce (titular)", "Para <nome>", "Para o seu filho").
    rotulo_de_quem: Callable[[str], str] | None = None


@dataclass(frozen=True)
class Decisao:
    tipo: str
    opcoes: tuple[tuple[str, str], ...] = ()  # (codigo, nome); a primeira e' a escolhida
    nome_leigo: str = ""
    #: A decisao veio do modelo (sem ele, a nota): "opcoes" do modelo nunca viram resposta unica.
    pelo_modelo: bool = True


def _update(texto: str, pendente: str | None, ctx: ca.Contexto | None, **extra: Any) -> dict[str, Any]:
    saida: dict[str, Any] = {
        "response_text": texto,
        "response_kind": "inform",
        "coleta_pendente": pendente,
        "coleta_contexto": ctx.codificar() if (ctx is not None and pendente) else "",
        "coleta_rodadas": 0,
    }
    saida.update(extra)
    return saida


def _situacao_bloqueada(fatos: Mapping[str, Any] | None) -> str | None:
    """C2: o atendimento NAO esta' liberado no cadastro (suspenso, inadimplente, inativo)? O texto, ou
    `None`."""
    if not fatos:
        return None
    vinculos = [v for v in fatos.get("vinculos") or [] if isinstance(v, Mapping)]
    if not vinculos or any(v.get("atendimento_liberado") is not False for v in vinculos):
        return None
    situacao = next(
        (str(v.get("situacao_beneficiario")) for v in vinculos if v.get("situacao_beneficiario")), ""
    )
    return ca.TEXTO_PLANO_SEM_ATENDIMENTO.format(situacao=f" (situação: {situacao})" if situacao else "")


def _com_rede(ctx: ca.Contexto, texto: str) -> str:
    """D2: a pergunta citou um hospital/clinica/laboratorio: a resposta de cobertura diz onde ver a rede."""
    return f"{texto} {ca.TEXTO_REDE_DO_PROCEDIMENTO}" if ctx.assunto == "rede" else texto


class FluxoDeCobertura:
    def __init__(self, deps: Dependencias) -> None:
        self.d = deps
        self._elegibilidade: asyncio.Future[Mapping[str, Any] | None] | None = None
        self._cpt: asyncio.Future[Any] | None = None
        self._excedente = 0
        #: DL-0131: no maximo 4 leituras de cobertura da AMH ao mesmo tempo (a lista de 15 derrubava a rota
        #: por prazo); o resto espera a vez.
        self._leituras_amh = asyncio.Semaphore(LEITURAS_AMH_SIMULTANEAS)

    # --- entrada --------------------------------------------------------------------------------------------
    async def responder(self, texto: str, pendente: str, contexto: object) -> dict[str, Any]:
        ctx = ca.Contexto.decodificar(contexto) if pendente.startswith(ca.PREFIXO) else ca.Contexto()
        respondidos = list(ctx.respondidos) if pendente == PEND_RESPONDIDO else []
        resposta = ca.ler_resposta(texto) if pendente.startswith(ca.PREFIXO) else None
        if resposta == "encerrar":
            logger.info("helena_cobertura_resposta", pendencia=pendente, leitura=resposta)
            return _update(ca.TEXTO_ASSUNTO_ENCERRADO, None, None)
        if resposta is not None:
            logger.info("helena_cobertura_resposta", pendencia=pendente, leitura=resposta)
        if pendente == PEND_RESPONDIDO:
            depois = await self._depois_da_resposta(texto, ctx)
            if depois is not None:
                return depois
        if pendente.startswith(ca.PREFIXO) and resposta is None and ca.e_reclamacao(texto):
            # DL-0131: irritacao / "nao entendi" / "eu ja' disse": desculpa curta e de novo. Nunca abre caso.
            resposta_reclamacao = await self._de_novo(texto, pendente, ctx)
            if resposta_reclamacao is not None:
                return resposta_reclamacao
        if (
            pendente in (ca.PEND_EQUIPE, ca.PEND_FALHA)
            and ca.e_cortesia(texto)
            and not re.search(r"\b(?:sim|quero|pode|aceito|tenta|tentar)\b", ca.normalizar(texto))
        ):
            # TRAVA `caso_so_com_sim` (ADR-0066): "ok, obrigada" / "beleza, valeu" a uma oferta da equipe e'
            # cortesia, nao "sim" nem pedido novo: resposta curta, sem caso e sem busca.
            return _update(ca.resposta_de_cortesia(texto), None, None)
        if pendente == ca.PEND_EQUIPE:
            if resposta == "nao" or ca.e_nao(texto):
                return _update(ca.TEXTO_SEM_EQUIPE, None, None)
            if resposta in ("nao_sei", "sim"):
                return _update(ca.TEXTO_OFERTA_EQUIPE, ca.PEND_EQUIPE, ctx)
        if pendente == ca.PEND_FALHA:
            if ca.tentar_de_novo(texto) or resposta == "sim":
                return await self._resolver(ctx, prefixo="")
            if resposta in ("nao", "nao_sei"):
                return _update(ca.TEXTO_ASSUNTO_ENCERRADO, None, None)
        if pendente == ca.PEND_QUEM:
            graus, com_nome, primeiros = await self.d.dependentes()
            quem = quem_pelo_texto(texto, graus, primeiros)
            if quem is not None:
                return await self._com_quem(ctx, quem, graus)
            if resposta is not None:
                return _update(self.d.pergunta_para_quem(graus, com_nome), ca.PEND_QUEM, ctx)
        if pendente == ca.PEND_CONFIRMA and ctx.opcoes:
            if resposta == "sim":
                codigo, nome = ctx.opcoes[0]
                return await self._consultar_um(ctx, codigo or nome, nome, prefixo="")
            if resposta == "nao" or ca.escolha_da_opcao(texto, ctx.opcoes[:1]) == -1:
                alternativas = list(ctx.opcoes[1:])
                termo = ctx.termos[0] if ctx.termos else "esse procedimento"
                if alternativas:
                    return self._opcoes(ctx, termo, alternativas, prefixo="")
                return self._falhou(ctx, termo)
            if resposta == "nao_sei":
                return self._falhou(ctx, ctx.termos[0] if ctx.termos else "esse procedimento", nao_sabe=True)
        if pendente == ca.PEND_OPCOES and ctx.opcoes:
            escolha = ca.escolha_da_opcao(texto, ctx.opcoes)
            if escolha is not None and escolha >= 0:
                codigo, nome = ctx.opcoes[escolha]
                return await self._consultar_um(ctx, codigo or nome, nome, prefixo="")
            termo = ctx.termos[0] if ctx.termos else "esse procedimento"
            if escolha == -1 or resposta == "nao":
                return self._falhou(ctx, termo)
            if resposta == "nao_sei":
                return self._falhou(ctx, termo, nao_sabe=True)
            if resposta == "sim":
                if len(ctx.opcoes) == 1:
                    codigo, nome = ctx.opcoes[0]
                    return await self._consultar_um(ctx, codigo or nome, nome, prefixo="")
                return _update(ca.pergunta_das_opcoes(termo, [n for _, n in ctx.opcoes]), ca.PEND_OPCOES, ctx)
        if pendente == ca.PEND_FORMA:
            termo = ctx.termos[0] if ctx.termos else "esse procedimento"
            if resposta == "nao_sei":
                return self._falhou(ctx, termo, nao_sabe=True)
            if resposta == "nao":
                return _update(ca.TEXTO_ASSUNTO_ENCERRADO, None, None)
            if resposta == "sim":
                if not ctx.termos:
                    return _update(ca.TEXTO_PEDE_LISTA, ca.PEND_FORMA, ctx)
                return _update(ca.pergunta_da_forma(termo), ca.PEND_FORMA, ctx)
        if pendente in (ca.PEND_OPCOES, ca.PEND_FORMA, ca.PEND_CONFIRMA):
            # A pessoa escreveu outro nome (ou a lista pedida): o mesmo pedido (para quem, tentativas).
            return await self._outro_nome(texto, ctx)
        return await self._nova_pergunta(texto, ca.Contexto(quem=ctx.quem), respondidos)

    async def _depois_da_resposta(self, texto: str, ctx: ca.Contexto) -> dict[str, Any] | None:
        """Avaliacao de 21/10/2026: a fala logo DEPOIS de uma resposta de cobertura. Cortesia ("ok", "valeu")
        recebe uma resposta curta; "faltou algum?" confere a lista anterior; "e' pra minha esposa" / "e' pra
        mim
        mesma" REFAZ a consulta para a pessoa citada. `None` = segue (pedido novo)."""
        if ca.e_cortesia(texto):
            return _update(ca.resposta_de_cortesia(texto), None, None)
        if ca.pergunta_se_faltou(texto) and ctx.termos:
            return _update(ca.texto_do_que_respondi(ctx.termos), PEND_RESPONDIDO, ctx)
        if (
            ctx.termos
            and len(ca.normalizar(texto).split()) <= 10
            and (cita_outra_pessoa(texto) or _TITULAR_NO_TEXTO.search(ca.normalizar(texto)))
        ):
            graus, com_nome, primeiros = await self.d.dependentes()
            quem = quem_pelo_texto(texto, graus, primeiros)
            logger.info("helena_cobertura_troca_de_pessoa", decidiu=quem is not None)
            novo = replace(ctx, respondidos=[], opcoes=[], tentativas=0, padrao=False)
            if quem is None:
                return _update(self.d.pergunta_para_quem(graus, com_nome), ca.PEND_QUEM, novo)
            return await self._com_quem(novo, quem, graus)
        return None

    async def _de_novo(self, texto: str, pendente: str, ctx: ca.Contexto) -> dict[str, Any] | None:
        """DL-0131: a reclamacao com a pergunta pendente. "Eu ja' disse que e' pra mim" responde o "para
        quem"; com o pedido guardado, a consulta e' refeita (desculpa curta na frente); sem pedido, a
        desculpa e o pedido de repetir."""
        logger.info("helena_cobertura_desculpa", pendencia=pendente)
        desculpa = f"{ca.TEXTO_DESCULPA} "
        if pendente == ca.PEND_QUEM:
            graus, _com_nome, primeiros = await self.d.dependentes()
            quem = quem_pelo_texto(texto, graus, primeiros)
            if quem is not None:
                return await self._com_quem(ctx, quem, graus, prefixo=desculpa)
            return None
        if ctx.termos:
            quem = ctx.quem or self._lembrado() or "titular"
            novo = replace(ctx, quem=quem, opcoes=[], tentativas=0, respondidos=[])
            return await self._com_quem(novo, quem, (), prefixo=desculpa)
        return _update(ca.TEXTO_DESCULPA_SEM_ASSUNTO, None, None)

    async def _extrair(self, texto: str) -> ca.Pedido | None:
        try:
            return await self.d.extrair(texto)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return None

    def _lembrado(self) -> str | None:
        return self.d.quem_lembrado() if self.d.quem_lembrado is not None else None

    def _ler_elegibilidade_cedo(self) -> None:
        """DL-0131 (velocidade): a elegibilidade comeca a ser lida JUNTO com a extracao."""
        if self._elegibilidade is None:
            self._elegibilidade = asyncio.ensure_future(self.d.elegibilidade())

    async def _fatos_de_elegibilidade(self) -> Mapping[str, Any] | None:
        self._ler_elegibilidade_cedo()
        assert self._elegibilidade is not None
        try:
            return await self._elegibilidade
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return None

    async def _outro_nome(self, texto: str, ctx: ca.Contexto) -> dict[str, Any]:
        """Resposta a "como esta' escrito no pedido?"/opcoes/"quais exames?" com um NOME (ou a lista): o
        termo vem da extracao (sem as palavras de conversa) ou da continuacao; sem nome, a pergunta e'
        repetida (nunca busca de lixo)."""
        modelo = await self._extrair(texto)
        termos = list(modelo.procedimentos) if modelo is not None else []
        tecnicos = list(modelo.tecnicos) if modelo is not None else []
        self._excedente = max(0, (modelo.total if modelo is not None else 0) - ca.MAX_PROCEDIMENTOS)
        if not termos:
            novo = termo_da_continuacao(texto)
            termos = [novo] if novo else []
        if not termos:
            termo = ctx.termos[0] if ctx.termos else "esse procedimento"
            if ctx.opcoes:
                return _update(ca.pergunta_das_opcoes(termo, [n for _, n in ctx.opcoes]), ca.PEND_OPCOES, ctx)
            if not ctx.termos:
                return _update(ca.TEXTO_PEDE_LISTA, ca.PEND_FORMA, ctx)
            return _update(ca.pergunta_da_forma(termo), ca.PEND_FORMA, ctx)
        novo_ctx = replace(ctx, termos=termos[: ca.MAX_PROCEDIMENTOS], tecnicos=tecnicos, opcoes=[])
        if novo_ctx.quem is None:
            quem, padrao = self._quem_sem_mencao()
            return await self._com_quem(replace(novo_ctx, padrao=padrao), quem, ())
        return await self._resolver(novo_ctx, prefixo="")

    def _quem_sem_mencao(self) -> tuple[str, bool]:
        """DL-0131: sem citar ninguem, a pessoa lembrada na conversa; sem ninguem lembrado, o TITULAR (por
        padrao)."""
        lembrado = self._lembrado()
        return (lembrado, False) if lembrado else ("titular", True)

    # --- pergunta nova --------------------------------------------------------------------------------------
    async def _nova_pergunta(self, texto: str, ctx: ca.Contexto, respondidos: list[str]) -> dict[str, Any]:
        from maezo.agents.helena.cobertura_rol import termo_da_mensagem

        self._ler_elegibilidade_cedo()
        reserva_termos = [t for t in (termo_da_mensagem(texto), termo_da_continuacao(texto)) if t]
        reserva = ca.pedido_deterministico(texto, reserva_termos)
        modelo = await self._extrair(texto)
        pedido = ca.confirmar_pelo_texto(ca.combinar(modelo, reserva), texto)
        self._excedente = max(0, pedido.total - ca.MAX_PROCEDIMENTOS)
        logger.info(
            "helena_cobertura_extraida",
            pelo_modelo=pedido.pelo_modelo,
            procedimentos=len(pedido.procedimentos),
            tipo=pedido.tipo,
            assunto=pedido.assunto,
            urgencia=pedido.urgencia,
            para_quem=pedido.para_quem,
            exclusao=pedido.exclusao_legal,
            discorda=pedido.discorda,
        )
        prefixo = (ca.PREFIXO_URGENCIA if pedido.urgencia else "") + (
            ca.PREFIXO_DISCORDA if pedido.discorda else ""
        )
        ctx = ca.Contexto(
            termos=list(pedido.procedimentos),
            tecnicos=list(pedido.tecnicos),
            assunto=pedido.assunto,
            urgencia=pedido.urgencia,
            respondidos=respondidos,
            quem=ctx.quem,
        )
        if pedido.assunto == "autorizacao":
            return {"desviar_para": "autorizacao"}
        if pedido.assunto == "rede" and not pedido.procedimentos:
            return {"desviar_para": "rede"}
        if pedido.assunto == "coparticipacao":
            return _update(prefixo + ca.TEXTO_COPARTICIPACAO, None, None)
        if pedido.assunto == "reembolso":
            return _update(prefixo + ca.TEXTO_REEMBOLSO, ca.PEND_EQUIPE, ctx)
        if pedido.exclusao_legal in ca.TEXTO_EXCLUSAO:
            return _update(prefixo + ca.TEXTO_EXCLUSAO[pedido.exclusao_legal], ca.PEND_EQUIPE, ctx)
        ctx = replace(ctx, outros=list(pedido.outros))
        if pedido.tipo == "remedio" and not pedido.procedimentos and len(pedido.outros) <= 1:
            return _update(prefixo + ca.TEXTO_REMEDIO, ca.PEND_EQUIPE, ctx)
        if pedido.tipo == "material" and not pedido.procedimentos:
            return _update(prefixo + ca.TEXTO_MATERIAL, ca.PEND_EQUIPE, ctx)
        if pedido.recem_nascido:
            return _update(prefixo + ca.TEXTO_RECEM_NASCIDO, ca.PEND_EQUIPE, ctx)
        if pedido.para_quem == "terceiro":
            return _update(prefixo + ca.TEXTO_TERCEIRO, ca.PEND_EQUIPE, ctx)
        if not pedido.procedimentos and pedido.outros:
            # Avaliacao de 21/10/2026: so' itens com regra propria (ex.: remedio + estetica): cada um pelo seu
            # caminho, numa resposta so'.
            return await self._varios(ctx, prefixo=prefixo)
        if not pedido.procedimentos and pedido.discorda:
            # Avaliacao de 21/10/2026: a discordancia sem procedimento termina na explicacao e na equipe, sem
            # o pedido da lista colado.
            return _update(
                f"{ca.PREFIXO_DISCORDA.strip()} {ca.TEXTO_OFERTA_DA_DISCORDANCIA}", ca.PEND_EQUIPE, ctx
            )
        if not pedido.procedimentos:
            # DL-0131: a intencao sem o nome do exame abre o assunto e pede a lista (nunca o texto geral).
            return _update(prefixo + ca.TEXTO_PEDE_LISTA, ca.PEND_FORMA, replace(ctx, termos=[], tecnicos=[]))
        # PARA QUEM (DL-0131): so' se pergunta quando a frase cita OUTRA pessoa e nao da' para saber qual.
        if pedido.para_quem == "titular":
            return await self._com_quem(ctx, "titular", (), prefixo=prefixo)
        if pedido.para_quem == "dependente" or pedido.quem_texto or cita_outra_pessoa(texto):
            graus, com_nome, primeiros = await self.d.dependentes()
            quem = quem_pelo_texto(pedido.quem_texto or texto, graus, primeiros)
            if quem is None and pedido.quem_texto:
                quem = quem_pelo_texto(texto, graus, primeiros)
            if quem is None and len(graus) == 1 and pedido.para_quem == "dependente":
                quem = "dependente:1"
            if quem is None and not graus:
                quem = "titular"
            if quem is None:
                return _update(prefixo + self.d.pergunta_para_quem(graus, com_nome), ca.PEND_QUEM, ctx)
            return await self._com_quem(ctx, quem, graus, prefixo=prefixo)
        quem, padrao = (ctx.quem, False) if ctx.quem else self._quem_sem_mencao()
        return await self._com_quem(replace(ctx, padrao=padrao), quem, (), prefixo=prefixo)

    async def _com_quem(
        self, ctx: ca.Contexto, quem: str, graus: Sequence[str], *, prefixo: str = ""
    ) -> dict[str, Any]:
        if quem == "terceiro":
            return _update(prefixo + ca.TEXTO_TERCEIRO, ca.PEND_EQUIPE, ctx)
        if quem == "fora_do_plano":
            return _update(prefixo + ca.TEXTO_DEPENDENTE_NAO_ATIVO, ca.PEND_EQUIPE, ctx)
        ctx = replace(ctx, quem=quem, padrao=ctx.padrao and quem == "titular")
        if self.d.lembrar_quem is not None:
            self.d.lembrar_quem(quem)
        if quem == "titular" and self.d.cpt is not None and self._cpt is None:
            # DL-0131 (velocidade): a CPT do titular e' lida JUNTO com a traducao e a cobertura (em memoria
            # 5 min no grafo); o aviso so' sai se algum procedimento for respondido.
            self._cpt = asyncio.ensure_future(self.d.cpt())
        bloqueio = _situacao_bloqueada(await self._fatos_de_elegibilidade())
        if bloqueio:
            prefixo = f"{prefixo}{bloqueio} "
        return await self._resolver(ctx, prefixo=prefixo)

    # --- traducao: recuperar + escolher ---------------------------------------------------------------------
    async def _escolher(
        self, termos: Sequence[str], termo: str, candidatos: Sequence[Candidato]
    ) -> ca.Escolha | None:
        if self.d.escolher is None or not candidatos:
            return None
        try:
            return await self.d.escolher(termos, termo, [c.procedimento.nome for c in candidatos])
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return None

    async def _recuperar(
        self, busca: BuscaDeProcedimentos, termo: str, tecnico: str, *, limite: int = 30
    ) -> tuple[Candidato, ...]:
        """Os candidatos do termo da pessoa e do nome tecnico provavel (intercalados, sem repetir nome)."""
        if not tecnico or ca.normalizar(tecnico) == ca.normalizar(termo):
            return (await busca.recuperar(termo))[:limite]
        do_termo, do_tecnico = await asyncio.gather(busca.recuperar(termo), busca.recuperar(tecnico))
        saida: list[Candidato] = []
        vistos: set[str] = set()
        for par in zip(do_tecnico, do_termo, strict=False):
            for c in par:
                chave = ca.normalizar(c.procedimento.nome)
                if chave not in vistos:
                    vistos.add(chave)
                    saida.append(c)
        curto = min(len(do_tecnico), len(do_termo))
        resto = (*do_tecnico[curto:], *do_termo[curto:])
        for c in resto:
            chave = ca.normalizar(c.procedimento.nome)
            if chave not in vistos:
                vistos.add(chave)
                saida.append(c)
        return tuple(saida[:limite])

    async def _decidir(self, termos: Sequence[str], termo: str, tecnico: str = "") -> Decisao | None:
        """Direto, confirmacao, opcoes ou nenhum. `None` = sem catalogo (busca por texto da AMH)."""
        busca = self.d.busca
        if busca is None or not busca.pronto:
            return None
        candidatos = await self._recuperar(busca, termo, tecnico)
        rotulo = f"{termo} (nome tecnico provavel: {tecnico})" if tecnico else termo
        escolha = await self._escolher(termos, rotulo, candidatos)
        return await self._da_escolha(busca, termo, tecnico, candidatos, escolha)

    async def _da_escolha(
        self,
        busca: BuscaDeProcedimentos,
        termo: str,
        tecnico: str,
        candidatos: Sequence[Candidato],
        escolha: ca.Escolha | None,
    ) -> Decisao:
        if escolha is None:
            return await self._decidir_pela_nota(busca, termo)

        def par(i: int) -> tuple[str, str]:
            return (candidatos[i].procedimento.codigo, candidatos[i].procedimento.nome)

        logger.info(
            "helena_cobertura_escolha",
            confianca=escolha.confianca,
            escolheu=escolha.indice is not None,
            alternativas=len(escolha.alternativas),
            candidatos=len(candidatos),
        )
        if escolha.vago:
            # Avaliacao de 21/10/2026: "tem varios tipos" AFIRMA que o termo existe no catalogo. So' sai
            # quando o
            # termo e' uma CATEGORIA reconhecida (as palavras dele aparecem em pelo menos dois itens do
            # catalogo,
            # com o mesmo limiar lexico da escolha direta). Termo inventado = "nao localizei" (regra geral).
            if _e_categoria(busca, termo, tecnico, candidatos):
                return Decisao(VAGO)
            logger.info("helena_cobertura_vago_sem_categoria", termo=ca.normalizar(termo)[:60])
            return Decisao(NENHUM)
        if escolha.indice is not None and escolha.confianca == "alta":
            escolhido = candidatos[escolha.indice].procedimento
            lexico = max(busca.lexico(termo, escolhido), busca.lexico(tecnico, escolhido) if tecnico else 0.0)
            if lexico >= LEXICO_MINIMO_PARA_DIRETO:
                return Decisao(DIRETO, (par(escolha.indice),), escolha.nome_leigo)
            logger.info("helena_cobertura_escolha_conferida", lexico=round(lexico, 2))
            escolha = ca.Escolha(escolha.indice, "media", escolha.alternativas, escolha.nome_leigo)
        ordem = ([escolha.indice] if escolha.indice is not None else []) + list(escolha.alternativas)
        opcoes = tuple(par(i) for i in dict.fromkeys(ordem))[:3]
        if escolha.indice is not None and escolha.confianca == "media":
            return Decisao(CONFIRMA, opcoes, escolha.nome_leigo)
        if opcoes:
            return Decisao(OPCOES, opcoes)
        logger.info("helena_cobertura_termo_nao_localizado", termo=ca.normalizar(termo)[:60], etapa="escolha")
        return Decisao(NENHUM)

    async def _decidir_varios(self, ctx: ca.Contexto, termos: Sequence[str]) -> list[Decisao | None]:
        """DL-0131: a lista inteira com UMA chamada ao modelo (candidatos em paralelo). Sem a chamada em lote
        (ou com ela falhando), uma decisao por termo, em paralelo."""
        busca = self.d.busca
        if busca is None or not busca.pronto:
            return [None] * len(termos)
        candidatos = await asyncio.gather(
            *(self._recuperar(busca, t, ctx.tecnico_de(t), limite=20) for t in termos)
        )
        escolhas: list[ca.Escolha | None] = [None] * len(termos)
        if self.d.escolher_varios is not None:
            pedidos = []
            for t, cands in zip(termos, candidatos, strict=True):
                tecnico = ctx.tecnico_de(t)
                rotulo = f"{t} (nome tecnico provavel: {tecnico})" if tecnico else t
                pedidos.append((rotulo, [c.procedimento.nome for c in cands]))
            try:
                escolhas = list(await self.d.escolher_varios(pedidos))
            except PROGRAMMING_ERRORS:
                raise
            except EXTERNAL_DEPENDENCY_FAILURES:
                escolhas = [None] * len(termos)
            if len(escolhas) != len(termos):
                escolhas = [None] * len(termos)
        faltam = [i for i, e in enumerate(escolhas) if e is None and candidatos[i]]
        if faltam and self.d.escolher is not None:
            avulsas = await asyncio.gather(
                *(self._escolher(termos, termos[i], candidatos[i]) for i in faltam)
            )
            for i, e in zip(faltam, avulsas, strict=True):
                escolhas[i] = e
        decisoes: list[Decisao | None] = list(
            await asyncio.gather(
                *(
                    self._da_escolha(busca, t, ctx.tecnico_de(t), cands, e)
                    for t, cands, e in zip(termos, candidatos, escolhas, strict=True)
                )
            )
        )
        # Avaliacao de 21/10/2026: o item que o LOTE nao achou (dentro da lista) ganha a busca de item unico
        # (mais candidatos, uma chamada so' dele) — a lista nao pode achar menos do que o item sozinho.
        fracos = [i for i, d in enumerate(decisoes) if d is not None and d.tipo not in (DIRETO, CONFIRMA)]
        if fracos and self.d.escolher is not None:
            refeitas = await asyncio.gather(
                *(self._decidir([termos[i]], termos[i], ctx.tecnico_de(termos[i])) for i in fracos)
            )
            for i, d in zip(fracos, refeitas, strict=True):
                if d is not None and d.tipo in (DIRETO, CONFIRMA):
                    decisoes[i] = d
            logger.info("helena_cobertura_lote_refeito", itens=len(fracos))
        return decisoes

    async def _decidir_pela_nota(self, busca: BuscaDeProcedimentos, termo: str) -> Decisao:
        """Sem o modelo: a decisao por nota (certo = direto; senao, opcoes)."""
        resultado = await busca.buscar(termo)
        if resultado.vazio:
            return Decisao(NENHUM)
        opcoes = tuple((c.procedimento.codigo, c.procedimento.nome) for c in resultado.candidatos)
        if resultado.certo or len(opcoes) == 1:
            return Decisao(DIRETO, opcoes[:1], pelo_modelo=False)
        return Decisao(OPCOES, opcoes[:3], pelo_modelo=False)

    # --- resolver os procedimentos --------------------------------------------------------------------------
    def _montar(
        self, ctx: ca.Contexto, frases: Sequence[str], *, view: Any, oferece: bool, prefixo: str
    ) -> str:
        """A resposta com PARA QUEM (DL-0131) e, por padrao para o titular, o convite para o dependente."""
        para = self.d.rotulo_de_quem(ctx.quem) if (self.d.rotulo_de_quem and ctx.quem) else None
        return ca.montar_resposta(
            frases,
            view=view,
            oferece_equipe=oferece,
            prefixo=prefixo,
            para=para,
            titular_por_padrao=ctx.padrao and ctx.quem == "titular",
        )

    async def _resolver(self, ctx: ca.Contexto, *, prefixo: str) -> dict[str, Any]:
        if not ctx.termos:
            return _update(prefixo + ca.TEXTO_PEDE_LISTA, ca.PEND_FORMA, ctx)
        if len(ctx.termos) > 1 or ctx.outros:
            return await self._varios(ctx, prefixo=prefixo)
        termo = ctx.termos[0]
        decisao = await self._decidir(ctx.termos, termo, ctx.tecnico_de(termo))
        if decisao is None:
            # Sem catalogo: a busca por TEXTO da propria rota de cobertura (as opcoes saem das descricoes).
            return await self._consultar_um(ctx, termo, None, prefixo=prefixo, por_texto=True)
        if decisao.tipo == DIRETO:
            codigo, nome = decisao.opcoes[0]
            return await self._consultar_um(ctx, codigo, nome, prefixo=prefixo)
        if decisao.tipo == CONFIRMA:
            nome = decisao.nome_leigo or ca.nome_legivel(decisao.opcoes[0][1])
            novo = replace(ctx, opcoes=list(decisao.opcoes), tentados=[*ctx.tentados, termo])
            logger.info("helena_cobertura_confirmacao", alternativas=len(decisao.opcoes) - 1)
            return _update(prefixo + ca.TEXTO_CONFIRMA.format(nome=nome), ca.PEND_CONFIRMA, novo)
        if decisao.tipo == OPCOES:
            opcoes = list(decisao.opcoes)
            if len(opcoes) == 1:
                return await self._consultar_um(ctx, opcoes[0][0], opcoes[0][1], prefixo=prefixo)
            if not decisao.pelo_modelo:
                # Sem o modelo: parecidos com a MESMA resposta do cadastro respondem uma vez so'.
                iguais = await self._mesma_resposta(ctx, opcoes, prefixo=prefixo)
                if iguais is not None:
                    return iguais
            return self._opcoes(ctx, termo, opcoes, prefixo=prefixo)
        if decisao.tipo == VAGO:
            novo = replace(ctx, tentativas=ctx.tentativas + 1, tentados=[*ctx.tentados, termo], opcoes=[])
            logger.info("helena_cobertura_pedido_vago", tentativa=novo.tentativas)
            if novo.tentativas >= ca.MAX_TENTATIVAS:
                return _update(prefixo + ca.TEXTO_OFERTA_EQUIPE, ca.PEND_EQUIPE, novo)
            return _update(prefixo + ca.pergunta_do_tipo(termo), ca.PEND_FORMA, novo)
        return self._falhou(ctx, termo, prefixo=prefixo)

    async def _mesma_resposta(
        self, ctx: ca.Contexto, opcoes: list[tuple[str, str]], *, prefixo: str
    ) -> dict[str, Any] | None:
        """Varios parecidos com a MESMA resposta do cadastro (mesma situacao, diretriz e carencia): responde
        uma vez, citando os nomes, sem fazer a pessoa escolher. Qualquer diferenca ou falha = `None`
        (opcoes)."""
        leituras = await asyncio.gather(*(self._ler(codigo) for codigo, _ in opcoes))
        assinaturas = set()
        primeiro: tuple[Any, Any] | None = None
        for (_codigo, nome), (view, falhou) in zip(opcoes, leituras, strict=True):
            item = ca.item_escolhido(view, nome) if not falhou else None
            if item is None:
                return None
            pendentes = tuple(
                getattr(c, "termina_em", None)
                for c in getattr(item, "carencias", ()) or ()
                if getattr(c, "cumprida", None) is False
            )
            assinaturas.add(
                (getattr(item, "situacao", None), bool(getattr(item, "tem_diretriz", False)), pendentes)
            )
            if primeiro is None:
                primeiro = (view, item)
        if len(assinaturas) != 1 or primeiro is None:
            return None
        view, item = primeiro
        nomes = " / ".join(ca.nome_legivel(n) for _, n in opcoes)
        logger.info("helena_cobertura_parecidos_com_a_mesma_resposta", opcoes=len(opcoes))
        frase, oferece = ca.texto_de_um_resultado(view, item, nomes)
        texto = self._montar(ctx, [frase], view=view, oferece=oferece, prefixo=prefixo)
        if (ctx.quem or "").startswith("dependente"):
            texto = f"{texto} {AVISO_DO_DEPENDENTE}"
        aviso_cpt = await self._aviso_cpt(ctx)
        if aviso_cpt:
            texto = f"{texto} {aviso_cpt}"
        return _update(
            _com_rede(ctx, texto),
            ca.PEND_EQUIPE if oferece else PEND_RESPONDIDO,
            replace(ctx, opcoes=[], tentativas=0, respondidos=[*ctx.respondidos, nomes][-4:]),
            consulta_literais=literais_da_cobertura(view),
            resposta_com_dados_do_plano=True,
        )

    def _opcoes(
        self, ctx: ca.Contexto, termo: str, opcoes: list[tuple[str, str]], *, prefixo: str
    ) -> dict[str, Any]:
        opcoes = [o for o in opcoes if not administrativo(o[1])][:3]
        if not opcoes:
            return self._falhou(ctx, termo, prefixo=prefixo)
        ctx = replace(ctx, opcoes=opcoes, tentativas=ctx.tentativas + 1, tentados=[*ctx.tentados, termo])
        logger.info("helena_cobertura_opcoes", opcoes=len(ctx.opcoes), tentativa=ctx.tentativas)
        return _update(
            prefixo + ca.pergunta_das_opcoes(termo, [n for _, n in ctx.opcoes]), ca.PEND_OPCOES, ctx
        )

    def _falhou(
        self, ctx: ca.Contexto, termo: str, *, prefixo: str = "", nao_sabe: bool = False
    ) -> dict[str, Any]:
        """Uma tentativa a mais; na 2a (ou quando a pessoa diz que nao sabe o nome), a oferta da equipe."""
        tentativas = ca.MAX_TENTATIVAS if nao_sabe else ctx.tentativas + 1
        ctx = replace(ctx, tentativas=tentativas, tentados=[*ctx.tentados, termo], opcoes=[])
        logger.info(
            "helena_cobertura_termo_nao_localizado",
            termo=ca.normalizar(termo)[:60],
            tentativa=ctx.tentativas,
            nao_sabe=nao_sabe,
        )
        if ctx.tentativas >= ca.MAX_TENTATIVAS:
            return _update(prefixo + ca.TEXTO_OFERTA_EQUIPE, ca.PEND_EQUIPE, ctx)
        return _update(prefixo + ca.pergunta_da_forma(termo), ca.PEND_FORMA, ctx)

    async def _aviso_cpt(self, ctx: ca.Contexto) -> str:
        """DL-0130: o aviso GERAL de CPT, so' para o TITULAR com CPT vigente; `""` em qualquer outro caso
        (dependente, sem leitura, falha, sem CPT). Nada da CPT vai ao estado, ao contexto nem a log."""
        if ctx.quem != "titular" or self.d.cpt is None:
            return ""
        try:
            view = await (self._cpt if self._cpt is not None else self.d.cpt())
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            view = None
        aviso = ca.texto_cpt(view)
        logger.info("helena_cobertura_cpt", aviso=bool(aviso))
        return aviso

    async def _ler(self, procedimento: str) -> tuple[Any, bool]:
        """(view, falhou). Falha/prazo da AMH = `falhou` (nunca "nao encontrei")."""
        try:
            async with self._leituras_amh:
                view = await self.d.cobertura(procedimento)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            view = None
        return view, view is None

    async def _consultar_um(
        self,
        ctx: ca.Contexto,
        procedimento: str,
        nome: str | None,
        *,
        prefixo: str,
        por_texto: bool = False,
    ) -> dict[str, Any]:
        termo = ctx.termos[0] if ctx.termos else (nome or procedimento)
        consulta = procedimento if not por_texto else (ca.normalizar(procedimento)[:80] or procedimento)
        if por_texto and not 3 <= len(consulta) <= 80:
            return self._falhou(ctx, termo, prefixo=prefixo)
        view, falhou = await self._ler(consulta)
        if falhou:
            ctx = replace(ctx, opcoes=[])
            return _update(prefixo + ca.TEXTO_FALHA_DA_CONSULTA, ca.PEND_FALHA, ctx)
        itens = tuple(getattr(view, "itens", ()) or ())
        if not itens:
            return self._falhou(ctx, termo, prefixo=prefixo)
        item = ca.item_escolhido(view, nome)
        if item is None:
            descricoes = list(
                dict.fromkeys(" ".join(str(getattr(i, "descricao", "") or "").split()) for i in itens)
            )
            descricoes = [d for d in descricoes if d and not administrativo(d)][:3]
            if len(descricoes) == 1:
                item = next(i for i in itens if " ".join(str(i.descricao or "").split()) == descricoes[0])
            elif len(descricoes) > 1 and ctx.tentativas < ca.MAX_TENTATIVAS:
                return self._opcoes(ctx, termo, [("", d) for d in descricoes], prefixo=prefixo)
            else:
                return self._falhou(ctx, termo, prefixo=prefixo)
        nome_final = nome or getattr(item, "descricao", None)
        if nome_final and ca.normalizar(str(nome_final)) in {ca.normalizar(t) for t in ctx.respondidos if t}:
            # E3: a MESMA pergunta de novo: nao repete o texto inteiro; retoma a conclusao em uma linha
            # (adendo de 22/10/2026) e oferece o proximo passo.
            logger.info("helena_cobertura_repetida")
            texto_e3 = ca.texto_repetida(view, item, ca.nome_legivel(str(nome_final)))
            return _update(prefixo + texto_e3, ca.PEND_EQUIPE, ctx)
        frase, oferece = ca.texto_de_um_resultado(
            view, item, ca.nome_legivel(str(nome_final)) if nome_final else None
        )
        texto = self._montar(ctx, [frase], view=view, oferece=oferece, prefixo=prefixo)
        if (ctx.quem or "").startswith("dependente"):
            texto = f"{texto} {AVISO_DO_DEPENDENTE}"
        aviso_cpt = await self._aviso_cpt(ctx)
        if aviso_cpt:
            texto = f"{texto} {aviso_cpt}"
        logger.info(
            "helena_cobertura_respondida",
            situacao=str(getattr(item, "situacao", "")),
            por_codigo=not por_texto,
            dependente=(ctx.quem or "").startswith("dependente"),
        )
        pendente = ca.PEND_EQUIPE if oferece else PEND_RESPONDIDO
        respondido = replace(
            ctx, opcoes=[], tentativas=0, respondidos=[*ctx.respondidos, str(nome_final or termo)][-4:]
        )
        return _update(
            _com_rede(ctx, texto),
            pendente,
            respondido,
            consulta_literais=literais_da_cobertura(view),
            resposta_com_dados_do_plano=True,
        )

    async def _varios(self, ctx: ca.Contexto, *, prefixo: str) -> dict[str, Any]:
        """B1 + DL-0131: TODOS os itens da lista sao traduzidos (uma chamada ao modelo) e consultados em
        paralelo; uma linha por item respondido; os nao localizados numa linha so', com a oferta da equipe
        UMA vez no fim; acima de 15, os 15 primeiros e um aviso (nunca cala sobre os outros)."""
        frases: list[str] = []
        nao_localizados: list[str] = []
        ultima_view: Any = None
        oferece_algum = False
        literais: list[str] = []
        termos = ctx.termos[: ca.MAX_PROCEDIMENTOS]
        decisoes = await self._decidir_varios(ctx, termos) if termos else []

        async def _um(termo: str, decisao: Decisao | None) -> tuple[str, str | None, Any, bool]:
            nome: str | None = None
            if decisao is not None and decisao.tipo in (DIRETO, CONFIRMA):
                codigo, nome = decisao.opcoes[0]
                view, falhou = await self._ler(codigo)
            elif decisao is None:
                view, falhou = await self._ler(ca.normalizar(termo)[:80])
            else:
                return termo, None, None, False
            if falhou:
                return termo, None, None, True
            item = ca.item_escolhido(view, nome) if view is not None else None
            if item is None:
                return termo, None, view, False
            rotulo = ca.nome_legivel(nome) if nome else getattr(item, "descricao", None)
            frase, oferece = ca.texto_de_um_resultado(view, item, rotulo)
            return termo, frase + ("\x00" if oferece else ""), view, False

        resultados = await asyncio.gather(*(_um(t, d) for t, d in zip(termos, decisoes, strict=True)))
        falhou_algum = False
        for termo, frase, view, falhou in resultados:
            if falhou:
                falhou_algum = True
                frases.append(f"{termo}: não consegui consultar agora.")
                continue
            if frase is None:
                nao_localizados.append(termo)
                continue
            oferece_algum = oferece_algum or frase.endswith("\x00")
            frases.append(frase.rstrip("\x00"))
            ultima_view = view
            literais.extend(literais_da_cobertura(view))
        logger.info(
            "helena_cobertura_varios",
            termos=len(termos),
            respondidos=len(frases),
            nao_localizados=len(nao_localizados),
            excedente=self._excedente,
        )
        partes: list[str] = []
        # Avaliacao de 21/10/2026: os itens com regra propria da mesma lista, cada um pelo seu caminho.
        frases_outros = [ca.frase_de_outro_item(n, t) for n, t in ctx.outros]
        if frases_outros and not frases:
            if prefixo:
                partes.append(prefixo.strip())
            partes.append("\n".join(frases_outros))
        frases = [*frases, *frases_outros] if frases else frases
        if frases:
            texto = self._montar(ctx, frases, view=ultima_view, oferece=True, prefixo=prefixo)
            if (ctx.quem or "").startswith("dependente"):
                texto = f"{texto} {AVISO_DO_DEPENDENTE}"
            # DL-0130: o aviso de CPT UMA vez, so' se ao menos um procedimento foi respondido.
            aviso_cpt = await self._aviso_cpt(ctx) if ultima_view is not None else ""
            if aviso_cpt:
                texto = f"{texto} {aviso_cpt}"
            partes.append(_com_rede(ctx, texto))
        elif prefixo:
            partes.append(prefixo.strip())
        if self._excedente:
            partes.append(ca.aviso_do_limite(len(termos) + self._excedente))
        novo = replace(ctx, respondidos=[*ctx.respondidos, *termos][-4:], opcoes=[], tentativas=0)
        if nao_localizados:
            partes.append(ca.texto_dos_nao_localizados(nao_localizados))
            novo = replace(novo, tentados=[*ctx.tentados, *nao_localizados], termos=nao_localizados)
            return _update(" ".join(partes), ca.PEND_EQUIPE, novo, consulta_literais=tuple(literais))
        if falhou_algum:
            partes.append("Quer que eu tente de novo os que não consegui consultar?")
            return _update(" ".join(partes), ca.PEND_FALHA, ctx, consulta_literais=tuple(literais))
        if ctx.outros:
            partes.append(ca.TEXTO_EQUIPE_PARA_OS_OUTROS)
            return _update(" ".join(partes), ca.PEND_EQUIPE, novo, consulta_literais=tuple(literais))
        if not oferece_algum:
            partes.append(ca.AVISO_FINAL)
        return _update(
            " ".join(partes),
            ca.PEND_EQUIPE if oferece_algum else PEND_RESPONDIDO,
            novo,
            consulta_literais=tuple(literais),
            resposta_com_dados_do_plano=True,
        )


__all__ = [
    "PEND_RESPONDIDO",
    "Decisao",
    "Dependencias",
    "FluxoDeCobertura",
    "cita_outra_pessoa",
    "quem_pelo_texto",
]
