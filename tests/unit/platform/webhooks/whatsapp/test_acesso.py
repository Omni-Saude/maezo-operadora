"""Acesso do beneficiario (DL-0083): maquina de estados, validadores, consentimento no lago, PII fora do log.

Sem rede e sem Postgres: as portas da AMH sao dublês, o store e' o de memoria (e o teste de reinicio usa DOIS
servicos sobre o MESMO store, que e' o que a persistencia no Postgres faz entre processos).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import structlog

from maezo.gateway.amh_interop import AmhSubjectVerifyHasher
from maezo.platform.webhooks.whatsapp import acesso as ac
from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.ports.consent_record import ConsentRecordReceipt
from maezo.ports.errors import PortFailureReason, PortResult
from maezo.ports.subject_verification import SubjectVerification

CONV = "wa:austa:hk1_" + "ab" * 8
CPF = "52998224725"
CPF_PONTUADO = "529.982.247-25"
OUTRO_CPF = "11144477735"
NASC = date(1985, 3, 7)
NASC_TXT = "07/03/1985"
REF = "ref-titular-1"
OUTRA_REF = "ref-dependente-2"
CHAVE = "k" * 24
TENANT_AMH = "austa"


class Relogio:
    def __init__(self) -> None:
        self.agora = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.agora

    def avancar(self, **kw: Any) -> None:
        self.agora += timedelta(**kw)


@dataclass
class FakeVerificacao:
    """A AMH do contrato CONFERE (#212): recebe a ref candidata e diz so' se o fator e' DELA."""

    hasher: AmhSubjectVerifyHasher
    #: (fator, hash) -> as referencias cujo cadastro tem esse fator.
    base: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    fora: bool = False
    #: (fator, hash, ref perguntada)
    chamadas: list[tuple[str, str, str]] = field(default_factory=list)

    def cadastrar_cpf(self, cpf: str, ref: str) -> None:
        self.base.setdefault(("cpf", self.hasher.hash_cpf(cpf) or ""), set()).add(ref)

    def cadastrar_cpf_nasc(self, cpf: str, nasc: date, ref: str) -> None:
        h = self.hasher.hash_cpf_nascimento(cpf, nasc.strftime("%Y%m%d")) or ""
        self.base.setdefault(("cpfdob", h), set()).add(ref)

    async def verify(
        self,
        portable_subject_ref: str,
        *,
        fator: str,
        verification_hash: str,
        hash_scheme: str,
        purpose_of_use: str,
        timeout_seconds: float = 5.0,
    ) -> PortResult[SubjectVerification]:
        self.chamadas.append((fator, verification_hash, portable_subject_ref))
        assert hash_scheme == "amh-subject-verify-v1"
        if self.fora:
            return PortResult.refused(PortFailureReason.UPSTREAM_UNAVAILABLE)
        confere = portable_subject_ref in self.base.get((fator, verification_hash), set())
        return PortResult.ok(SubjectVerification(portable_subject_ref=portable_subject_ref, confere=confere))


@dataclass
class FakeConsentimentos:
    falhar: bool = False
    chamadas: list[dict[str, Any]] = field(default_factory=list)

    async def record(self, portable_subject_ref: str, **kw: Any) -> PortResult[ConsentRecordReceipt]:
        self.chamadas.append({"ref": portable_subject_ref, **kw})
        if self.falhar:
            return PortResult.refused(PortFailureReason.UPSTREAM_UNAVAILABLE)
        return PortResult.ok(ConsentRecordReceipt(consent_ref="consent-1", recorded=True))


@dataclass
class FakeResolvedor:
    ref: str | None = REF
    desfecho: str = "unico"
    #: Os candidatos de um telefone compartilhado (titular + dependente).
    multiplos: tuple[str, ...] = (REF, OUTRA_REF)

    async def candidatos_com_desfecho(
        self, pseudo_id: str, *, phone_hash: str | None
    ) -> tuple[tuple[str, ...], str]:
        if self.desfecho == "unico":
            return ((self.ref,) if self.ref else ()), "unico"
        if self.desfecho == "multiplos":
            return self.multiplos, "multiplos"
        return (), self.desfecho


class Ambiente:
    def __init__(self) -> None:
        self.relogio = Relogio()
        self.store = ac.AcessoStoreEmMemoria()
        self.hasher = AmhSubjectVerifyHasher(key=CHAVE, amh_tenant=TENANT_AMH)
        self.verif = FakeVerificacao(self.hasher)
        self.consent = FakeConsentimentos()
        self.resolvedor = FakeResolvedor()
        self.servico = self.novo_servico()

    def novo_servico(self) -> ac.AcessoBeneficiario:
        return ac.AcessoBeneficiario(
            store=self.store,
            verification=self.verif,
            consents=self.consent,
            resolvedor=self.resolvedor,
            hash_telefone=lambda numero: "a" * 64,
            hasher=self.hasher,
            lexicos=pre_roteamento.carregar(),
            amh_tenant=TENANT_AMH,
            hash_scheme="amh-subject-verify-v1",
            purpose_of_use="sharing_amh_internal",
            relogio=self.relogio,
        )

    async def msg(self, texto: str) -> ac.DecisaoAcesso:
        d = await self.servico.avaliar(conversation_id=CONV, texto=texto, numero_cru="5511999990000")
        await self.servico.persistir(d)
        return d

    async def ate_cpf(self) -> None:
        await self.msg("oi")
        await self.msg("ACEITO")

    async def verificar_pelo_telefone(self) -> ac.DecisaoAcesso:
        self.verif.cadastrar_cpf(CPF, REF)
        await self.ate_cpf()
        return await self.msg(CPF)


@pytest.fixture
def amb() -> Ambiente:
    return Ambiente()


# --------------------------------------------------------------------------- validadores
@pytest.mark.parametrize("texto", [CPF, CPF_PONTUADO, " 529.982.24725 ", "529 982 247 25"])
def test_cpf_aceita_com_ou_sem_pontuacao(texto: str) -> None:
    assert ac.extrair_cpf(texto) == CPF


@pytest.mark.parametrize(
    "texto", ["52998224724", "11111111111", "123", "abc", "5299822472", "529982247250", "529.982.247-2x", ""]
)
def test_cpf_invalido_e_recusado(texto: str) -> None:
    assert ac.extrair_cpf(texto) is None


def test_cpf_digitos_verificadores() -> None:
    assert ac.cpf_valido(CPF) and ac.cpf_valido(OUTRO_CPF)
    assert not ac.cpf_valido("52998224725"[:-1] + "6")


HOJE = date(2026, 10, 8)


@pytest.mark.parametrize("texto", ["07/03/1985", "7/3/1985", "07-03-1985", "07.03.1985", "07031985"])
def test_nascimento_valido(texto: str) -> None:
    leitura = ac.ler_nascimento(texto, hoje=HOJE)
    assert leitura.nascimento == NASC and leitura.cpf is None and not leitura.sobra_invalida


@pytest.mark.parametrize(
    "texto", ["31/02/1990", "07/03/2030", "08/10/2027", "01/01/1899", "ontem", "99/99/9999", ""]
)
def test_nascimento_invalido(texto: str) -> None:
    assert ac.ler_nascimento(texto, hoje=HOJE).nascimento is None


def test_nascimento_com_cpf_na_mesma_mensagem() -> None:
    leitura = ac.ler_nascimento(f"{CPF_PONTUADO} {NASC_TXT}", hoje=HOJE)
    assert leitura.nascimento == NASC and leitura.cpf == CPF
    assert ac.ler_nascimento("lixo 07/03/1985", hoje=HOJE).sobra_invalida
    assert ac.ler_nascimento(f"{CPF} 07031985", hoje=HOJE).cpf == CPF


def test_hoje_e_aceito_mas_futuro_nao() -> None:
    assert ac.ler_nascimento("08/10/2026", hoje=HOJE).nascimento == HOJE
    assert ac.ler_nascimento("09/10/2026", hoje=HOJE).nascimento is None


@pytest.mark.parametrize("texto", ["ACEITO", "aceito", "Aceito.", "aceito!", "  ÁCEITO ", "Aceitó"])
def test_aceito_variantes(texto: str) -> None:
    assert ac.eh_aceito(texto)


@pytest.mark.parametrize("texto", ["aceito sim", "nao aceito", "ok", "", "aceitar"])
def test_nao_e_aceito(texto: str) -> None:
    assert not ac.eh_aceito(texto)


@pytest.mark.parametrize(
    "texto", ["REVOGAR", "revogar.", "Revogar meu consentimento", "REVOGAR MEU CONSENTIMENTO!"]
)
def test_revogar_variantes(texto: str) -> None:
    assert ac.eh_revogar(texto)


def test_texto_de_consentimento_exato_e_sha() -> None:
    assert ac.TEXTO_CONSENTIMENTO.startswith("Olá! Sou a assistente da Austa Clínicas.")
    assert ac.TEXTO_CONSENTIMENTO.endswith("pelo aplicativo Austa Clínicas ou pelo portal do plano.")
    import hashlib

    assert hashlib.sha256(ac.TEXTO_CONSENTIMENTO.encode("utf-8")).hexdigest() == ac.SHA256_TEXTO_CONSENTIMENTO
    assert ac.VERSAO_TEXTO_CONSENTIMENTO == "wa-consent-v2"


# --------------------------------------------------------------------------- consentimento
async def test_primeira_mensagem_qualquer_conteudo_pede_consentimento(amb: Ambiente) -> None:
    d = await amb.msg("meu plano esta ativo?")
    assert d.respostas == (ac.TEXTO_CONSENTIMENTO,) and not d.prosseguir
    assert d.novo.estado == ac.SEM_CONSENTIMENTO
    assert d.novo.texto_versao == "wa-consent-v2" and d.novo.texto_sha256 == ac.SHA256_TEXTO_CONSENTIMENTO


async def test_primeira_mensagem_aceito_tambem_recebe_o_texto(amb: Ambiente) -> None:
    d = await amb.msg("ACEITO")
    assert d.respostas == (ac.TEXTO_CONSENTIMENTO,) and d.novo.estado == ac.SEM_CONSENTIMENTO


async def test_nao_aceito_repete_o_pedido_e_agentes_nao_rodam(amb: Ambiente) -> None:
    await amb.msg("oi")
    for texto in ("quero falar do boleto", "nao", "123"):
        d = await amb.msg(texto)
        assert d.respostas == (ac.TEXTO_CONSENTIMENTO,) and not d.prosseguir
        assert d.novo.estado == ac.SEM_CONSENTIMENTO


async def test_aceito_leva_ao_cpf_e_guarda_o_instante(amb: Ambiente) -> None:
    await amb.msg("oi")
    d = await amb.msg("aceito.")
    assert d.respostas == (ac.PEDIDO_CPF,) and d.novo.estado == ac.AGUARDANDO_CPF
    assert d.novo.consentido_em == amb.relogio.agora


# --------------------------------------------------------------------------- CPF
async def test_cpf_invalido_conta_tentativa(amb: Ambiente) -> None:
    await amb.ate_cpf()
    d = await amb.msg("123")
    assert d.respostas == (ac.CPF_INVALIDO,) and d.novo.tentativas == 1 and d.novo.estado == ac.AGUARDANDO_CPF
    assert amb.verif.chamadas == []


async def test_telefone_unico_e_cpf_confere_verifica_e_registra_consentimento(amb: Ambiente) -> None:
    d = await amb.verificar_pelo_telefone()
    assert d.respostas == (ac.RESPOSTA_VERIFICADO,) and not d.prosseguir and d.nova_verificacao
    assert d.novo.estado == ac.VERIFICADO and d.novo.portable_subject_ref == REF
    assert amb.verif.chamadas == [("cpf", amb.hasher.hash_cpf(CPF), REF)]  # SO' contra a ref do telefone
    assert len(amb.consent.chamadas) == 1
    c = amb.consent.chamadas[0]
    assert c["ref"] == REF and c["decision"] == "granted" and c["scope"] == "atendimento_whatsapp"
    assert c["channel"] == "whatsapp" and c["consent_text_version"] == "wa-consent-v2"
    assert c["consent_text_sha256"] == ac.SHA256_TEXTO_CONSENTIMENTO
    assert c["decided_at"] == "2026-10-08T12:00:00Z"
    assert c["idempotency_key"] == ac.chave_de_idempotencia(
        CONV, "wa-consent-v2", "granted", d.novo.consentido_em
    )  # type: ignore[arg-type]
    assert d.novo.consent_ref == "consent-1" and not d.novo.consentimento_pendente_gravacao


async def test_hash_enviado_e_o_do_esquema(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    import hashlib
    import hmac

    esperado = hmac.new(CHAVE.encode(), f"{TENANT_AMH}:cpf:{CPF}".encode(), hashlib.sha256).hexdigest()
    assert amb.verif.chamadas[0][1] == esperado


async def test_ref_diferente_da_do_telefone_falha(amb: Ambiente) -> None:
    amb.verif.cadastrar_cpf(CPF, OUTRA_REF)
    await amb.ate_cpf()
    d = await amb.msg(CPF)
    assert (
        d.respostas == (ac.FALHA_CONFERENCIA,)
        and d.novo.tentativas == 1
        and d.novo.estado == ac.AGUARDANDO_CPF
    )
    assert amb.consent.chamadas == []


async def test_cpf_nao_cadastrado_falha(amb: Ambiente) -> None:
    await amb.ate_cpf()
    d = await amb.msg(OUTRO_CPF)
    assert d.respostas == (ac.FALHA_CONFERENCIA,) and d.novo.tentativas == 1


async def test_telefone_compartilhado_pede_nascimento(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    await amb.ate_cpf()
    d = await amb.msg(CPF)
    assert d.respostas == (ac.PEDIDO_NASCIMENTO,) and d.novo.estado == ac.AGUARDANDO_NASCIMENTO
    assert d.novo.tentativas == 0 and amb.verif.chamadas == []


async def test_nascimento_confere_cpfdob_contra_cada_candidato_do_telefone(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, OUTRA_REF)
    await amb.ate_cpf()
    await amb.msg(CPF_PONTUADO)
    d = await amb.msg(NASC_TXT)
    assert d.novo.estado == ac.VERIFICADO and d.novo.portable_subject_ref == OUTRA_REF
    assert [c[0] for c in amb.verif.chamadas] == ["cpfdob", "cpfdob"]
    assert {c[2] for c in amb.verif.chamadas} == {REF, OUTRA_REF}  # SO' os candidatos do telefone
    import hashlib
    import hmac

    esperado = hmac.new(
        CHAVE.encode(), f"{TENANT_AMH}:cpfdob:{CPF}:19850307".encode(), hashlib.sha256
    ).hexdigest()
    assert amb.verif.chamadas[0][1] == esperado
    assert amb.consent.chamadas[0]["ref"] == OUTRA_REF


async def test_nascimento_errado_gasta_tentativa_e_volta_ao_cpf(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, OUTRA_REF)
    await amb.ate_cpf()
    await amb.msg(CPF)
    d = await amb.msg("01/01/1990")
    assert (
        d.respostas == (ac.FALHA_CONFERENCIA,)
        and d.novo.tentativas == 1
        and d.novo.estado == ac.AGUARDANDO_CPF
    )


async def test_nascimento_invalido_gasta_tentativa_e_mantem_o_passo(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    await amb.ate_cpf()
    await amb.msg(CPF)
    d = await amb.msg("31/02/1990")
    assert d.respostas == (ac.FALHA_CONFERENCIA,) and d.novo.tentativas == 1
    assert d.novo.estado == ac.AGUARDANDO_NASCIMENTO and amb.verif.chamadas == []


async def test_cpf_e_nascimento_na_mesma_mensagem_funciona_sem_a_memoria(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, REF)
    await amb.ate_cpf()
    await amb.msg(CPF)
    # outra replica / reinicio: o CPF pendente (memoria de processo) se perde
    outro = amb.novo_servico()
    d = await outro.avaliar(conversation_id=CONV, texto=NASC_TXT, numero_cru="5511999990000")
    assert d.respostas == (ac.PEDIDO_CPF_E_NASCIMENTO,) and d.novo.tentativas == 0
    d = await outro.avaliar(conversation_id=CONV, texto=f"{CPF} {NASC_TXT}", numero_cru="5511999990000")
    assert d.novo.estado == ac.VERIFICADO


# --------------------------------------------------------------------------- bloqueio e escalonamento
async def test_tres_falhas_bloqueiam_e_escalam(amb: Ambiente) -> None:
    await amb.ate_cpf()
    d1 = await amb.msg(OUTRO_CPF)
    d2 = await amb.msg("123")
    assert not d1.escalar and not d2.escalar
    d3 = await amb.msg(OUTRO_CPF)
    assert d3.escalar and d3.respostas == (ac.BLOQUEADO,) and d3.novo.estado == ac.BLOQUEADO_HUMANO
    d4 = await amb.msg("alguem ai?")
    assert d4.respostas == (ac.BLOQUEADO_CURTO,) and not d4.escalar and not d4.prosseguir


async def test_bloqueio_termina_com_a_validade_e_o_consentimento_fica(amb: Ambiente) -> None:
    await amb.ate_cpf()
    for _ in range(3):
        await amb.msg("123")
    amb.relogio.avancar(hours=25)
    d = await amb.msg("oi")
    assert d.respostas == (ac.PEDIDO_CPF,) and d.novo.estado == ac.AGUARDANDO_CPF and d.novo.tentativas == 0
    assert d.novo.consentido_em is not None


async def test_amh_fora_na_verificacao_nao_conta_tentativa(amb: Ambiente) -> None:
    amb.verif.fora = True
    await amb.ate_cpf()
    d = await amb.msg(CPF)
    assert d.respostas == (ac.INDISPONIVEL,) and d.novo.tentativas == 0 and d.novo.estado == ac.AGUARDANDO_CPF
    amb.verif.fora = False
    amb.verif.cadastrar_cpf(CPF, REF)
    assert (await amb.msg(CPF)).novo.estado == ac.VERIFICADO


async def test_amh_fora_no_passo_do_nascimento_mantem_estado_e_cpf(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.verif.fora = True
    await amb.ate_cpf()
    await amb.msg(CPF)
    d = await amb.msg(NASC_TXT)
    assert (
        d.respostas == (ac.INDISPONIVEL,)
        and d.novo.tentativas == 0
        and d.novo.estado == ac.AGUARDANDO_NASCIMENTO
    )
    amb.verif.fora = False
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, REF)
    assert (await amb.msg(NASC_TXT)).novo.estado == ac.VERIFICADO


# --------------------------------------------------------------------------- verificado e validade
async def test_verificado_segue_para_os_agentes_com_a_referencia(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    d = await amb.msg("estou com dor de cabeca")
    assert (
        d.prosseguir and d.respostas == () and d.portable_subject_ref == REF and d.consent_ref == "consent-1"
    )


async def test_validade_vence_e_recomeca_no_cpf_sem_regravar_consentimento(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    amb.relogio.avancar(hours=23)
    assert (await amb.msg("oi")).prosseguir
    amb.relogio.avancar(hours=2)
    d = await amb.msg("oi")
    assert not d.prosseguir and d.respostas == (ac.PEDIDO_CPF,) and d.novo.estado == ac.AGUARDANDO_CPF
    d = await amb.msg(CPF)
    assert d.novo.estado == ac.VERIFICADO
    assert len(amb.consent.chamadas) == 1  # consentimento ja' no lago: nao regrava


async def test_validade_configuravel(amb: Ambiente) -> None:
    amb.servico = ac.AcessoBeneficiario(
        store=amb.store, verification=amb.verif, consents=amb.consent, resolvedor=amb.resolvedor,
        hash_telefone=lambda n: "a" * 64, hasher=amb.hasher, lexicos=pre_roteamento.carregar(),
        amh_tenant=TENANT_AMH, hash_scheme="amh-subject-verify-v1", purpose_of_use="x",
        validade=timedelta(hours=1), relogio=amb.relogio,
    )  # fmt: skip
    await amb.verificar_pelo_telefone()
    amb.relogio.avancar(minutes=61)
    assert not (await amb.msg("oi")).prosseguir


# --------------------------------------------------------------------------- gravacao do consentimento
async def test_falha_ao_gravar_consentimento_nao_nega_acesso_e_repete(amb: Ambiente) -> None:
    amb.consent.falhar = True
    d = await amb.verificar_pelo_telefone()
    assert (
        d.novo.estado == ac.VERIFICADO
        and d.novo.consentimento_pendente_gravacao
        and d.novo.consent_ref is None
    )
    d = await amb.msg("oi")  # acesso permitido mesmo com a gravacao pendente
    assert d.prosseguir and d.novo.consentimento_pendente_gravacao
    amb.consent.falhar = False
    d = await amb.msg("oi")
    assert d.prosseguir and not d.novo.consentimento_pendente_gravacao and d.consent_ref == "consent-1"
    chaves = {c["idempotency_key"] for c in amb.consent.chamadas}
    assert len(chaves) == 1  # a MESMA chave deterministica em todas as tentativas


# --------------------------------------------------------------------------- revogacao
async def test_revogar_grava_revoked_limpa_e_aceito_recomeca_no_cpf(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    d = await amb.msg("Revogar meu consentimento")
    assert d.respostas == (ac.RESPOSTA_REVOGADO,) and d.novo.estado == ac.REVOGADO
    assert amb.consent.chamadas[-1]["decision"] == "revoked" and amb.consent.chamadas[-1]["ref"] == REF
    assert d.novo.portable_subject_ref is None and d.novo.consent_ref is None and not d.prosseguir
    assert (await amb.msg("oi")).respostas == (ac.TEXTO_CONSENTIMENTO,)
    d = await amb.msg("ACEITO")
    assert d.novo.estado == ac.AGUARDANDO_CPF and d.respostas == (ac.PEDIDO_CPF,)


@pytest.mark.parametrize("antes", ["sem_consentimento", "aguardando_cpf"])
async def test_revogar_sem_referencia_so_limpa_o_estado(amb: Ambiente, antes: str) -> None:
    await amb.msg("oi")
    if antes == "aguardando_cpf":
        await amb.msg("ACEITO")
    d = await amb.msg("REVOGAR")
    assert d.novo.estado == ac.REVOGADO and amb.consent.chamadas == []


async def test_revogacao_pendente_repete_e_bloqueia_novo_aceito_ate_gravar(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    amb.consent.falhar = True
    d = await amb.msg("REVOGAR")
    assert d.novo.revogacao_pendente and d.novo.portable_subject_ref == REF
    d = await amb.msg("ACEITO")
    assert d.respostas == (ac.REVOGACAO_EM_PROCESSAMENTO,) and d.novo.estado == ac.REVOGADO
    assert not d.prosseguir
    amb.consent.falhar = False
    d = await amb.msg("ACEITO")
    assert d.novo.estado == ac.AGUARDANDO_CPF and not d.novo.revogacao_pendente


# --------------------------------------------------------------------------- emergencia
@pytest.mark.parametrize("passo", ["primeira", "consentimento", "cpf", "bloqueado"])
async def test_emergencia_vem_antes_e_nunca_no_lugar(amb: Ambiente, passo: str) -> None:
    if passo == "consentimento":
        await amb.msg("oi")
    if passo in ("cpf", "bloqueado"):
        await amb.ate_cpf()
    if passo == "bloqueado":
        for _ in range(3):
            await amb.msg("123")
    d = await amb.msg("estou com dor no peito")
    assert d.respostas[0] == ac.TEXTO_EMERGENCIA and len(d.respostas) == 2
    assert d.respostas[1] != ac.TEXTO_EMERGENCIA and not d.prosseguir


async def test_emergencia_nao_e_enviada_quando_verificado(amb: Ambiente) -> None:
    await amb.verificar_pelo_telefone()
    d = await amb.msg("estou com dor no peito")
    assert d.prosseguir and d.respostas == ()  # o grafo da Helena cuida da red flag


async def test_emergencia_nao_usa_dado_de_beneficiario(amb: Ambiente) -> None:
    amb.resolvedor.ref = REF
    d = await amb.msg("quero me matar")
    assert d.respostas[0] == ac.TEXTO_EMERGENCIA and amb.verif.chamadas == [] and amb.consent.chamadas == []


# --------------------------------------------------------------------------- persistencia
async def test_reinicio_nao_perde_o_estado(amb: Ambiente) -> None:
    await amb.ate_cpf()
    await amb.msg("123")
    outro = amb.novo_servico()  # "processo novo" sobre o mesmo store
    d = await outro.avaliar(conversation_id=CONV, texto="456", numero_cru="5511999990000")
    assert d.novo.estado == ac.AGUARDANDO_CPF and d.novo.tentativas == 2


# --------------------------------------------------------------------------- PII
async def test_cpf_e_nascimento_nao_aparecem_em_log_estado_nem_repr(amb: Ambiente) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.verif.cadastrar_cpf_nasc(CPF, NASC, REF)
    with structlog.testing.capture_logs() as logs:
        await amb.ate_cpf()
        d1 = await amb.msg(CPF_PONTUADO)
        d2 = await amb.msg(NASC_TXT)
        d3 = await amb.msg("529.982.247-24")
        await amb.msg("REVOGAR")
    rendido = repr(logs) + repr(d1) + repr(d2) + repr(d3) + repr(amb.store._linhas) + repr(amb.servico)
    rendido += repr(amb.consent.chamadas) + repr(amb.verif.chamadas)
    for proibido in (CPF, CPF_PONTUADO, "529.982", "07/03/1985", "19850307", "07031985", "5511999990000"):
        assert proibido not in rendido, proibido
    # o que e' guardado: so' os campos do estado
    assert {f for f in ac.EstadoAcesso.__dataclass_fields__} == {
        "conversation_id", "estado", "ultima_mensagem_em", "tentativas", "texto_versao", "texto_sha256",
        "consentido_em", "revogado_em", "portable_subject_ref", "consent_ref",
        "consentimento_pendente_gravacao", "revogacao_pendente", "verificado_em", "expira_em",
        "bloqueado_ate", "regravacoes_falhas", "falhas_janela", "janela_inicio",
    }  # fmt: skip


def test_hasher_nao_expoe_a_chave_nem_aceita_formato_ruim() -> None:
    h = AmhSubjectVerifyHasher(key=CHAVE, amh_tenant=TENANT_AMH)
    assert CHAVE not in repr(h)
    assert h.hash_cpf("123") is None and h.hash_cpf_nascimento(CPF, "1985-03-07") is None
    assert h.hash_cpf(CPF) != h.hash_cpf_nascimento(CPF, "19850307")
    with pytest.raises(Exception):  # noqa: B017 - chave curta recusa
        AmhSubjectVerifyHasher(key="curta", amh_tenant=TENANT_AMH)


def test_textos_fixos_sao_exatamente_os_do_dono() -> None:
    """Os textos do tom humanizado (DL-0087, 10/10/2026). A substancia obrigatoria de cada um e' cobrada
    tambem em `test_tom_humanizado.py`, que nao depende da redacao exata."""
    t = ac.textos_fixos()
    assert t["pedido_cpf"] == "Obrigada! Para confirmar que é você, me envie seu CPF (só os números)."
    assert t["cpf_invalido"] == "Esse CPF não parece válido. Pode conferir e enviar de novo?"
    assert t["pedido_nascimento"] == "Por segurança, me envie também sua data de nascimento (dd/mm/aaaa)."
    assert t["verificado"] == "Pronto, já confirmei que é você 😊 Em que posso ajudar?"
    assert t["falha_conferencia"] == "Não consegui confirmar seus dados. Pode conferir e tentar de novo?"
    assert t["bloqueado"] == (
        "Não consegui confirmar que é você. Vou passar seu atendimento para alguém da nossa equipe."
    )
    assert t["bloqueado_curto"] == "Seu atendimento já foi passado para alguém da nossa equipe."
    assert (
        t["indisponivel"]
        == "Não consegui confirmar seus dados agora. Tente de novo em alguns minutos, por favor."
    )
    assert t["emergencia"] == (
        "Se for uma emergência, ligue agora para o 192 (SAMU) ou vá ao pronto-socorro mais próximo."
    )
    assert t["revogado"].endswith("Se quiser voltar a conversar, é só escrever ACEITO.")
