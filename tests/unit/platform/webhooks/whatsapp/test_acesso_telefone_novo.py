"""DL-0084: telefone FORA DO CADASTRO se identifica pelo documento; retencao de 90 dias do estado.

(a) Risco aceito pelo dono contra o alerta da revisao de seguranca do #700: telefone sem candidato -> CPF ->
nascimento -> `resolve-by-document` com os DOIS hashes. `unico` = verificado com aquela ref (so' nesta
conversa); `nenhum` = falha com as mesmas regras de tentativas/bloqueio; indice fora ou 429 = indisponivel
sem gastar tentativa. Telefone unico e compartilhado seguem como no #700 (o documento nem e' perguntado).
(b) O estado e' apagado apos 90 dias sem atividade, exceto revogacao pendente.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog

from maezo.gateway.amh_interop import AmhSubjectVerifyHasher
from maezo.platform.webhooks.whatsapp import acesso as ac
from maezo.platform.webhooks.whatsapp import acesso_retencao as ret
from maezo.platform.webhooks.whatsapp.acesso_store import PostgresAcessoStore
from maezo.ports.document_resolution import DocumentResolution
from maezo.ports.errors import PortFailureReason, PortResult
from tests.unit.platform.webhooks.whatsapp.test_acesso import (
    CONV,
    CPF,
    CPF_PONTUADO,
    NASC,
    NASC_TXT,
    OUTRA_REF,
    OUTRO_CPF,
    REF,
    TENANT_AMH,
    Ambiente,
)

REF_NOVA = "ref-sem-telefone-9"


@dataclass
class FakeDocumentos:
    """A AMH do `resolve-by-document`: (hash cpf, hash cpfdob) -> ref; recusa configuravel."""

    hasher: AmhSubjectVerifyHasher
    base: dict[tuple[str, str], str] = field(default_factory=dict)
    recusa: PortFailureReason | None = None
    chamadas: list[dict[str, Any]] = field(default_factory=list)

    def cadastrar(self, cpf: str, nasc: Any, ref: str) -> None:
        chave = (
            self.hasher.hash_cpf(cpf) or "",
            self.hasher.hash_cpf_nascimento(cpf, nasc.strftime("%Y%m%d")) or "",
        )
        self.base[chave] = ref

    async def resolve_by_document(self, **kw: Any) -> PortResult[DocumentResolution]:
        self.chamadas.append(kw)
        if self.recusa is not None:
            return PortResult.refused(self.recusa)
        ref = self.base.get((kw["document_hash"], kw["document_dob_hash"]))
        if ref is None:
            return PortResult.ok(DocumentResolution(resultado="nenhum"))
        return PortResult.ok(DocumentResolution(resultado="unico", portable_subject_ref=ref))


class AmbienteDocumento(Ambiente):
    def __init__(self) -> None:
        self.docs: FakeDocumentos | None = None
        super().__init__()
        self.resolvedor.desfecho = "nenhum"

    def novo_servico(self) -> ac.AcessoBeneficiario:
        if self.docs is None:
            self.docs = FakeDocumentos(AmhSubjectVerifyHasher(key="k" * 24, amh_tenant=TENANT_AMH))
        servico = super().novo_servico()
        servico._documentos = self.docs
        return servico


@pytest.fixture
def amb() -> AmbienteDocumento:
    return AmbienteDocumento()


def _docs(amb: AmbienteDocumento) -> FakeDocumentos:
    assert amb.docs is not None
    return amb.docs


# --------------------------------------------------------------------------- (a) telefone novo
async def test_telefone_novo_unico_verifica_com_a_ref_do_documento(amb: AmbienteDocumento) -> None:
    _docs(amb).cadastrar(CPF, NASC, REF_NOVA)
    await amb.ate_cpf()
    d = await amb.msg(CPF_PONTUADO)
    assert d.respostas == (ac.PEDIDO_NASCIMENTO,) and d.novo.estado == ac.AGUARDANDO_NASCIMENTO
    assert not d.escalar and _docs(amb).chamadas == [] and amb.verif.chamadas == []
    d = await amb.msg(NASC_TXT)
    assert d.respostas == (ac.RESPOSTA_VERIFICADO,) and d.nova_verificacao
    assert d.novo.estado == ac.VERIFICADO and d.novo.portable_subject_ref == REF_NOVA
    assert d.novo.expira_em == d.novo.verificado_em + timedelta(hours=24)  # type: ignore[operator]
    [chamada] = _docs(amb).chamadas
    assert chamada["document_hash"] == amb.hasher.hash_cpf(CPF)
    assert chamada["document_dob_hash"] == amb.hasher.hash_cpf_nascimento(CPF, "19850307")
    assert chamada["amh_tenant"] == TENANT_AMH and chamada["hash_scheme"] == "amh-subject-verify-v1"
    # CONFERE nunca e' usado no caminho do documento; o consentimento e' gravado com a ref resolvida.
    assert amb.verif.chamadas == [] and [c["ref"] for c in amb.consent.chamadas] == [REF_NOVA]
    d = await amb.msg("qual meu plano?")
    assert d.prosseguir and d.portable_subject_ref == REF_NOVA


async def test_cpf_e_nascimento_na_mesma_mensagem_tambem_resolve(amb: AmbienteDocumento) -> None:
    _docs(amb).cadastrar(CPF, NASC, REF_NOVA)
    await amb.ate_cpf()
    await amb.msg(CPF)
    amb.servico._pendentes.descartar(CONV)  # outra replica: a memoria do CPF se perdeu
    d = await amb.msg(NASC_TXT)
    assert d.respostas == (ac.PEDIDO_CPF_E_NASCIMENTO,) and d.novo.tentativas == 0
    d = await amb.msg(f"{CPF} {NASC_TXT}")
    assert d.novo.estado == ac.VERIFICADO and d.novo.portable_subject_ref == REF_NOVA


async def test_telefone_novo_nenhum_conta_falha_e_bloqueia_na_terceira(amb: AmbienteDocumento) -> None:
    _docs(amb).cadastrar(CPF, NASC, REF_NOVA)
    await amb.ate_cpf()
    for esperado in (1, 2):
        await amb.msg(OUTRO_CPF)
        d = await amb.msg(NASC_TXT)
        assert d.respostas == (ac.FALHA_CONFERENCIA,) and d.novo.tentativas == esperado
        assert d.novo.estado == ac.AGUARDANDO_CPF and not d.escalar
    await amb.msg(CPF)
    d = await amb.msg("01/01/1990")  # nascimento errado para o CPF certo tambem e' "nenhum"
    assert d.escalar and d.novo.estado == ac.BLOQUEADO_HUMANO and d.respostas == (ac.BLOQUEADO,)
    assert d.novo.bloqueado_ate is not None and d.novo.portable_subject_ref is None
    assert (await amb.msg(CPF)).respostas == (ac.BLOQUEADO_CURTO,)
    assert len(_docs(amb).chamadas) == 3


async def test_cpf_invalido_no_telefone_novo_conta_tentativa_sem_consultar(amb: AmbienteDocumento) -> None:
    await amb.ate_cpf()
    d = await amb.msg("123")
    assert d.respostas == (ac.CPF_INVALIDO,) and d.novo.tentativas == 1 and _docs(amb).chamadas == []


@pytest.mark.parametrize(
    "recusa",
    [PortFailureReason.RATE_LIMITED, PortFailureReason.UPSTREAM_UNAVAILABLE, PortFailureReason.TIMEOUT],
)
async def test_429_ou_indice_fora_e_indisponivel_sem_gastar_tentativa(
    amb: AmbienteDocumento, recusa: PortFailureReason
) -> None:
    _docs(amb).cadastrar(CPF, NASC, REF_NOVA)
    await amb.ate_cpf()
    await amb.msg(CPF)
    _docs(amb).recusa = recusa
    d = await amb.msg(NASC_TXT)
    assert d.respostas == (ac.INDISPONIVEL,) and d.novo.tentativas == 0 and d.novo.falhas_janela == 0
    assert d.novo.estado == ac.AGUARDANDO_NASCIMENTO and not d.escalar
    # O CPF segue pendente: com a AMH de volta, so' o nascimento basta.
    _docs(amb).recusa = None
    d = await amb.msg(NASC_TXT)
    assert d.novo.estado == ac.VERIFICADO and d.novo.portable_subject_ref == REF_NOVA


async def test_telefone_unico_e_compartilhado_nunca_usam_o_documento(amb: AmbienteDocumento) -> None:
    amb.resolvedor.desfecho = "unico"
    amb.verif.cadastrar_cpf(CPF, REF)
    await amb.ate_cpf()
    assert (await amb.msg(CPF)).novo.estado == ac.VERIFICADO
    outro = AmbienteDocumento()
    outro.resolvedor.desfecho = "multiplos"
    outro.verif.cadastrar_cpf_nasc(CPF, NASC, OUTRA_REF)
    await outro.ate_cpf()
    await outro.msg(CPF)
    assert (await outro.msg(NASC_TXT)).novo.portable_subject_ref == OUTRA_REF
    assert _docs(amb).chamadas == [] and _docs(outro).chamadas == []


async def test_telefone_de_gente_demais_continua_indo_ao_atendente(amb: AmbienteDocumento) -> None:
    amb.resolvedor.desfecho = "multiplos"
    amb.resolvedor.multiplos = tuple(f"ref-{i}" for i in range(ac.MAX_CANDIDATOS + 1))
    await amb.ate_cpf()
    d = await amb.msg(CPF)
    assert d.respostas == (ac.TELEFONE_NAO_IDENTIFICADO,) and d.escalar and _docs(amb).chamadas == []


async def test_verificacao_por_documento_vence_em_24h_e_recomeca_no_cpf(amb: AmbienteDocumento) -> None:
    _docs(amb).cadastrar(CPF, NASC, REF_NOVA)
    await amb.ate_cpf()
    await amb.msg(CPF)
    await amb.msg(NASC_TXT)
    amb.relogio.avancar(hours=24, seconds=1)
    d = await amb.msg("oi")
    assert d.respostas == (ac.PEDIDO_CPF,) and d.novo.estado == ac.AGUARDANDO_CPF and not d.prosseguir


async def test_cpf_e_nascimento_nunca_em_log_estado_repr_ou_chamada(amb: AmbienteDocumento) -> None:
    _docs(amb).cadastrar(CPF, NASC, REF_NOVA)
    with structlog.testing.capture_logs() as logs:
        await amb.ate_cpf()
        d1 = await amb.msg(CPF_PONTUADO)
        _docs(amb).recusa = PortFailureReason.RATE_LIMITED
        d2 = await amb.msg(NASC_TXT)
        _docs(amb).recusa = None
        d3 = await amb.msg(NASC_TXT)
    rendido = repr(logs) + repr(d1) + repr(d2) + repr(d3) + repr(amb.store._linhas) + repr(amb.servico)
    rendido += repr(_docs(amb).chamadas) + repr(amb.consent.chamadas)
    for proibido in (CPF, CPF_PONTUADO, "529.982", "07/03/1985", "19850307", "07031985", "5511999990000"):
        assert proibido not in rendido, proibido
    eventos = [e for e in logs if e.get("event") == "acesso_transicao"]
    assert {"telefone_sem_cadastro", "verificado_por_documento"} <= {e["motivo"] for e in eventos}
    assert any(
        e.get("event") == "acesso_documento_indisponivel" and e["motivo"] == "rate_limited" for e in logs
    )


# --------------------------------------------------------------------------- ACEITO antes do CPF, sempre
async def test_aceito_e_colhido_antes_de_pedir_o_cpf_em_todos_os_caminhos(amb: AmbienteDocumento) -> None:
    """A AMH #214 documentou que NAO confere o consentimento: o Maezo garante. Nenhum caminho pede ou usa o
    CPF sem ACEITO; um estado de documento sem consentimento registrado volta ao texto de consentimento."""
    _docs(amb).cadastrar(CPF, NASC, REF_NOVA)
    amb.verif.cadastrar_cpf(CPF, REF)
    # 1a mensagem ja' com o CPF (telefone novo, unico e compartilhado): so' o texto de consentimento.
    for desfecho in ("nenhum", "unico", "multiplos"):
        outro = AmbienteDocumento()
        outro.resolvedor.desfecho = desfecho
        d = await outro.msg(CPF)
        assert d.respostas == (ac.TEXTO_CONSENTIMENTO,) and d.novo.estado == ac.SEM_CONSENTIMENTO
        d = await outro.msg(f"{CPF} {NASC_TXT}")
        assert d.respostas == (ac.TEXTO_CONSENTIMENTO,)
        assert _docs(outro).chamadas == [] and outro.verif.chamadas == []
    # REVOGAR no meio do passo do nascimento: CPF/nascimento seguintes nao sao lidos ate' novo ACEITO.
    await amb.ate_cpf()
    await amb.msg(CPF)
    await amb.msg("REVOGAR")
    for texto in (NASC_TXT, CPF, f"{CPF} {NASC_TXT}"):
        assert (await amb.msg(texto)).respostas == (ac.TEXTO_CONSENTIMENTO,)
    assert _docs(amb).chamadas == []
    # Estado gravado sem consentimento (qualquer passo de documento, ou bloqueio vencido): texto de
    # consentimento, nunca o pedido de CPF nem chamada a AMH.
    agora = amb.relogio.agora
    for estado, extra in (
        (ac.AGUARDANDO_CPF, {}),
        (ac.AGUARDANDO_NASCIMENTO, {}),
        (ac.BLOQUEADO_HUMANO, {"bloqueado_ate": agora - timedelta(minutes=1), "tentativas": 3}),
    ):
        await amb.store.gravar(
            ac.EstadoAcesso(conversation_id=CONV, estado=estado, ultima_mensagem_em=agora, **extra)
        )
        d = await amb.msg(f"{CPF} {NASC_TXT}")
        assert d.respostas == (ac.TEXTO_CONSENTIMENTO,) and d.novo.estado == ac.SEM_CONSENTIMENTO, estado
        assert not d.prosseguir
    assert _docs(amb).chamadas == [] and amb.verif.chamadas == []
    # So' depois do ACEITO o CPF e' pedido.
    d = await amb.msg("ACEITO")
    assert d.respostas == (ac.PEDIDO_CPF,) and d.novo.consentido_em is not None


# --------------------------------------------------------------------------- (b) retencao de 90 dias
def _estado(cid: str, ultima: datetime, **kw: Any) -> ac.EstadoAcesso:
    return ac.EstadoAcesso(conversation_id=cid, estado=ac.SEM_CONSENTIMENTO, ultima_mensagem_em=ultima, **kw)


async def test_retencao_apaga_inativo_ha_mais_de_90_dias_menos_revogacao_pendente() -> None:
    amb = Ambiente()
    agora = amb.relogio.agora
    velho = "wa:austa:hk1_" + "01" * 8
    recente = "wa:austa:hk1_" + "02" * 8
    pendente = "wa:austa:hk1_" + "03" * 8
    limite_exato = "wa:austa:hk1_" + "04" * 8
    await amb.store.gravar(_estado(velho, agora - timedelta(days=91)))
    await amb.store.gravar(_estado(recente, agora - timedelta(days=89)))
    await amb.store.gravar(_estado(limite_exato, agora - timedelta(days=90)))
    await amb.store.gravar(
        replace(
            _estado(pendente, agora - timedelta(days=400), revogado_em=agora - timedelta(days=400)),
            estado=ac.REVOGADO,
            revogacao_pendente=True,
            portable_subject_ref=REF,
        )
    )
    assert timedelta(days=90) == ac.RETENCAO_ESTADO
    assert await amb.servico.expurgar_inativos() == 1
    assert set(amb.store._linhas) == {recente, pendente, limite_exato}
    assert await amb.servico.expurgar_inativos() == 0  # idempotente


async def test_conversa_expurgada_recomeca_pelo_consentimento() -> None:
    amb = Ambiente()
    await amb.verificar_pelo_telefone()
    amb.relogio.avancar(days=91)
    assert await amb.servico.expurgar_inativos() == 1
    d = await amb.msg("oi")
    assert d.respostas == (ac.TEXTO_CONSENTIMENTO,) and d.novo.estado == ac.SEM_CONSENTIMENTO


async def test_laco_de_retencao_roda_ja_e_para_no_sinal() -> None:
    chamadas: list[int] = []

    class _Acesso:
        async def expurgar_inativos(self) -> int:
            chamadas.append(1)
            if len(chamadas) == 1:
                raise ConnectionError("banco fora")  # nunca derruba o laco
            parar.set()
            return 0

    parar = asyncio.Event()
    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(ret.laco_de_retencao(_Acesso(), parar, intervalo_s=0.01), timeout=2)
    assert len(chamadas) == 2
    assert [e["erro"] for e in logs if e["event"] == "acesso_retencao_falhou"] == ["ConnectionError"]


class _Conn:
    def __init__(self, respostas: list[str], chamadas: list[tuple[Any, ...]]) -> None:
        self._respostas = respostas
        self._chamadas = chamadas

    async def execute(self, sql: str, *args: Any) -> str:
        self._chamadas.append((sql, *args))
        return self._respostas.pop(0)


class _Pool:
    def __init__(self, respostas: list[str]) -> None:
        self.respostas = respostas
        self.chamadas: list[tuple[Any, ...]] = []

    def acquire(self) -> Any:
        pool = self

        class _Ctx:
            async def __aenter__(self) -> _Conn:
                return _Conn(pool.respostas, pool.chamadas)

            async def __aexit__(self, *_: Any) -> None:
                return None

        return _Ctx()


async def test_store_postgres_expurga_em_lotes_do_proprio_tenant_e_poupa_revogacao_pendente() -> None:
    pool = _Pool(["DELETE 2", "DELETE 2", "DELETE 1"])
    store = PostgresAcessoStore(dsn="postgresql://u@h/db", tenant="austa", pool=pool)
    limite = datetime(2026, 7, 10, tzinfo=UTC)
    assert await store.expurgar_inativos(limite, lote=2) == 5
    assert len(pool.chamadas) == 3
    sql, tenant, lim, lote = pool.chamadas[0]
    assert (tenant, lim, lote) == ("austa", limite, 2)
    assert "NOT revogacao_pendente" in sql and "ultima_mensagem_em < $2" in sql and "tenant = $1" in sql
    with pytest.raises(ValueError, match="lote"):
        await store.expurgar_inativos(limite, lote=0)


def test_plano_de_eliminacao_registra_90_dias_e_a_base_legal_do_consentimento() -> None:
    import yaml

    from maezo.platform.lifecycle import erasure_plan as ep

    dados = yaml.safe_load(ep.resolve_plan_path().read_text(encoding="utf-8"))
    [camada] = [c for c in dados["camadas"] if c["tabela"] == "conversa_acesso_beneficiario"]
    # A decisao do dono esta' escrita (90 dias, consentimento), ainda marcada para a ratificacao do DPO: o
    # DRAFT nao pode carregar um veredito do vocabulario fechado (cerca de `test_erasure_plan.py`).
    assert camada["decisao_dpo"] == "PENDENTE"
    assert "90 dias" in camada["retencao"] and "revogacao_pendente" in camada["retencao"]
    assert "wa-consent-v1" in camada["base_legal"] and "DL-0084" in camada["base_legal"]
    assert "ELIMINADA apos 90 dias" in camada["notas"]


def test_textos_e_hash_inalterados_pelo_caminho_do_documento() -> None:
    """O caminho novo nao cria texto novo: reusa PEDIDO_NASCIMENTO e os textos do #700."""
    assert set(ac.textos_fixos()) == {
        "consentimento", "pedido_cpf", "cpf_invalido", "pedido_nascimento", "pedido_cpf_e_nascimento",
        "verificado", "falha_conferencia", "bloqueado", "bloqueado_curto", "indisponivel",
        "telefone_nao_identificado", "revogado", "emergencia",
    }  # fmt: skip
