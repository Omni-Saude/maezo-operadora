"""D-04 do red team: 3 falhas de verificacao por CPF em 6 horas, somadas entre todas as conversas.

O defeito medido (13/10/2026): o bloqueio valia por CONVERSA. Nove datas erradas contra o mesmo CPF,
espalhadas
por tres numeros, e o quarto numero verificou com a data certa. Decisao do dono (15/10/2026): 3 falhas por
CPF em
6 h; passado o limite, o CPF fica bloqueado ate' o fim da janela para TODO mundo (inclusive o dono do CPF,
custo
aceito) e o bloqueio abre atendente.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import maezo
from maezo.gateway.amh_interop import AmhSubjectVerifyHasher
from maezo.platform.webhooks.whatsapp import acesso as ac
from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.acesso_limite_cpf import (
    JANELA_POR_CPF,
    MAX_FALHAS_POR_CPF,
    LimitePorCpfEmMemoria,
    LimitePorCpfStoreError,
    PostgresLimitePorCpf,
    chave_do_cpf,
)
from tests.unit.platform.webhooks.whatsapp.test_acesso import (
    CHAVE,
    CPF,
    NASC,
    NASC_TXT,
    OUTRA_REF,
    OUTRO_CPF,
    TENANT_AMH,
    Ambiente,
)

T0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
ERRADA = "01/01/1990"


def _conv(i: int) -> str:
    return f"wa:austa:hk1_{i:016x}"


class AmbienteComLimite(Ambiente):
    """O ambiente do acesso, com o contador por CPF (a composicao de producao sempre o passa)."""

    def __init__(self) -> None:
        self.limite = LimitePorCpfEmMemoria()
        super().__init__()
        self.resolvedor.desfecho = "multiplos"  # o telefone pede o nascimento, como no caso medido
        self.verif.cadastrar_cpf_nasc(CPF, NASC, OUTRA_REF)

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
            limite_cpf=self.limite,
        )

    async def conversa(self, i: int, *mensagens: str) -> list[ac.DecisaoAcesso]:
        """Uma conversa NOVA (outro numero): oi, ACEITO e as mensagens dadas. Devolve cada decisao."""
        decisoes = []
        for texto in ("oi", "ACEITO", *mensagens):
            d = await self.servico.avaliar(
                conversation_id=_conv(i), texto=texto, numero_cru=f"551199999{i:04d}"
            )
            await self.servico.persistir(d)
            decisoes.append(d)
        return decisoes

    async def falhar(self, i: int) -> ac.DecisaoAcesso:
        """Uma tentativa ERRADA contra o CPF (data errada), numa conversa nova."""
        return (await self.conversa(i, CPF, ERRADA))[-1]


@pytest.fixture
def amb() -> AmbienteComLimite:
    return AmbienteComLimite()


# --------------------------------------------------------------------------- a regra do dono


async def test_tres_falhas_em_conversas_diferentes_bloqueiam_a_quarta_mesmo_com_a_data_certa(
    amb: AmbienteComLimite,
) -> None:
    inicio = amb.relogio.agora
    for i in (1, 2, 3):
        d = await amb.falhar(i)
        assert d.respostas == (ac.FALHA_CONFERENCIA,)  # cada conversa so' gastou UMA das suas tres
        assert d.novo.tentativas == 1
    chamadas_antes = len(amb.verif.chamadas)
    # A QUARTA conversa, que antes verificava: agora o CPF esta' bloqueado ANTES de pedir a data.
    decisoes = await amb.conversa(4, CPF)
    d = decisoes[-1]
    assert d.respostas == (ac.BLOQUEADO,)
    assert d.novo.estado == ac.BLOQUEADO_HUMANO and d.escalar
    assert d.novo.bloqueado_ate == inicio + JANELA_POR_CPF  # a janela abre na primeira falha
    # E nao chamou a AMH: o bloqueio e' antes da conferencia.
    assert len(amb.verif.chamadas) == chamadas_antes


async def test_o_limite_estoura_enquanto_a_conversa_espera_a_data_e_a_data_certa_nao_verifica(
    amb: AmbienteComLimite,
) -> None:
    """O CPF ja' estava pendente (aguardando o nascimento) quando outras conversas gastaram as 3 falhas."""
    await amb.falhar(1)
    await amb.falhar(2)
    espera = (await amb.conversa(4, CPF))[-1]
    assert espera.novo.estado == ac.AGUARDANDO_NASCIMENTO
    await amb.falhar(3)  # a terceira falha da janela, em outra conversa
    chamadas_antes = len(amb.verif.chamadas)
    d = await amb.servico.avaliar(conversation_id=_conv(4), texto=NASC_TXT, numero_cru="5511999990004")
    assert d.novo.estado == ac.BLOQUEADO_HUMANO and d.escalar and not d.prosseguir
    assert len(amb.verif.chamadas) == chamadas_antes  # nem a data certa chegou a AMH


async def test_o_bloqueio_vale_ate_o_fim_da_janela_e_depois_o_cpf_volta_a_tentar(
    amb: AmbienteComLimite,
) -> None:
    for i in (1, 2, 3):
        await amb.falhar(i)
    amb.relogio.avancar(hours=5, minutes=59)
    assert (await amb.conversa(4, CPF))[-1].novo.estado == ac.BLOQUEADO_HUMANO
    amb.relogio.avancar(minutes=2)  # a janela de 6 h passou
    decisoes = await amb.conversa(5, CPF, NASC_TXT)
    assert decisoes[-1].novo.estado == ac.VERIFICADO


async def test_duas_falhas_nao_bloqueiam(amb: AmbienteComLimite) -> None:
    await amb.falhar(1)
    await amb.falhar(2)
    assert (await amb.conversa(3, CPF, NASC_TXT))[-1].novo.estado == ac.VERIFICADO


async def test_um_sucesso_nao_zera_o_contador(amb: AmbienteComLimite) -> None:
    """Quem acerta o proprio documento nao limpa as tentativas feitas contra o de outra pessoa."""
    await amb.falhar(1)
    await amb.falhar(2)
    assert (await amb.conversa(3, CPF, NASC_TXT))[-1].novo.estado == ac.VERIFICADO
    await amb.falhar(4)  # a TERCEIRA falha da janela, mesmo depois do sucesso
    assert (await amb.conversa(5, CPF))[-1].novo.estado == ac.BLOQUEADO_HUMANO


async def test_outro_cpf_nao_e_afetado(amb: AmbienteComLimite) -> None:
    for i in (1, 2, 3):
        await amb.falhar(i)
    amb.verif.cadastrar_cpf_nasc(OUTRO_CPF, NASC, OUTRA_REF)
    decisoes = await amb.conversa(9, OUTRO_CPF, NASC_TXT)
    assert decisoes[-1].novo.estado == ac.VERIFICADO


# --------------------------------------------------------------------------- o que NAO conta


async def test_formato_invalido_e_cpf_com_digito_errado_nao_contam(amb: AmbienteComLimite) -> None:
    for i in range(1, 8):
        await amb.conversa(i, "12345678900", "52998224724")  # nao sao CPF validos: recusa local
    assert amb.limite._linhas == {}
    assert (await amb.conversa(20, CPF, NASC_TXT))[-1].novo.estado == ac.VERIFICADO


async def test_amh_fora_do_ar_nao_conta_como_falha(amb: AmbienteComLimite) -> None:
    amb.verif.fora = True
    for i in (1, 2, 3, 4):
        d = (await amb.conversa(i, CPF, NASC_TXT))[-1]
        assert d.respostas == (ac.INDISPONIVEL,)
    assert amb.limite._linhas == {}
    amb.verif.fora = False
    assert (await amb.conversa(9, CPF, NASC_TXT))[-1].novo.estado == ac.VERIFICADO


async def test_sem_o_limite_o_comportamento_e_o_de_antes_quarta_conversa_verifica() -> None:
    """A NAO-VACUIDADE: o limite por CPF e' o que muda o desfecho (sem ele, o defeito medido)."""
    sem_limite = Ambiente()
    sem_limite.resolvedor.desfecho = "multiplos"
    sem_limite.verif.cadastrar_cpf_nasc(CPF, NASC, OUTRA_REF)

    async def tentar(i: int, data: str) -> ac.DecisaoAcesso:
        d: Any = None
        for texto in ("oi", "ACEITO", CPF, data):
            d = await sem_limite.servico.avaliar(
                conversation_id=_conv(i), texto=texto, numero_cru=f"551199999{i:04d}"
            )
            await sem_limite.servico.persistir(d)
        return d

    for i in (1, 2, 3):
        await tentar(i, ERRADA)
    assert (await tentar(4, NASC_TXT)).novo.estado == ac.VERIFICADO


# --------------------------------------------------------------------------- retencao


async def test_a_retencao_apaga_a_linha_quando_a_janela_passa(amb: AmbienteComLimite) -> None:
    await amb.falhar(1)
    assert len(amb.limite._linhas) == 1
    amb.relogio.avancar(hours=7)
    await amb.servico.expurgar_inativos()
    assert amb.limite._linhas == {}


# --------------------------------------------------------------------------- o limitador


def test_a_regra_do_dono_sao_tres_falhas_em_seis_horas() -> None:
    assert (3, timedelta(hours=6)) == (MAX_FALHAS_POR_CPF, JANELA_POR_CPF)


def test_a_chave_e_derivada_e_nao_o_hash_de_verificacao() -> None:
    hasher = AmhSubjectVerifyHasher(key=CHAVE, amh_tenant=TENANT_AMH)
    hash_cpf = hasher.hash_cpf(CPF)
    assert hash_cpf is not None
    chave = chave_do_cpf(hash_cpf)
    assert chave != hash_cpf and re.fullmatch(r"[0-9a-f]{64}", chave)
    assert chave == chave_do_cpf(hash_cpf)  # deterministica
    assert chave != chave_do_cpf(hasher.hash_cpf(OUTRO_CPF) or "")
    assert CPF not in chave


async def test_a_janela_e_fixa_e_a_contagem_reabre_depois_dela() -> None:
    limite = LimitePorCpfEmMemoria()
    chave = "a" * 64
    await limite.registrar_falha(chave, T0)
    await limite.registrar_falha(chave, T0 + timedelta(hours=5))
    assert await limite.bloqueado_ate(chave, T0 + timedelta(hours=5)) is None
    await limite.registrar_falha(chave, T0 + timedelta(hours=5, minutes=30))
    # bloqueado ate' T0 + 6 h (a janela abre na PRIMEIRA falha e nao anda com as outras)
    assert await limite.bloqueado_ate(chave, T0 + timedelta(hours=5, minutes=31)) == T0 + timedelta(hours=6)
    assert await limite.bloqueado_ate(chave, T0 + timedelta(hours=6)) is None
    await limite.registrar_falha(chave, T0 + timedelta(hours=7))  # janela nova: 1 falha
    assert await limite.bloqueado_ate(chave, T0 + timedelta(hours=7)) is None


def test_o_repr_nao_expoe_nada() -> None:
    assert "redacted" in repr(LimitePorCpfEmMemoria())
    assert "redacted" in repr(PostgresLimitePorCpf(dsn="postgresql://u:p@h/db", tenant="amh"))


class _Conexao:
    def __init__(self, linha: dict[str, Any] | None) -> None:
        self.linha = linha
        self.chamadas: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.chamadas.append((sql, args))
        return self.linha

    async def execute(self, sql: str, *args: Any) -> str:
        self.chamadas.append((sql, args))
        return "DELETE 2"


class _Aquisicao:
    def __init__(self, conn: _Conexao) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Conexao:
        return self.conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Pool:
    def __init__(self, conn: _Conexao) -> None:
        self.conn = conn

    def acquire(self) -> _Aquisicao:
        return _Aquisicao(self.conn)


CHAVE_HEX = "ab" * 32


async def test_postgres_registra_com_o_tenant_a_chave_o_instante_e_a_janela() -> None:
    conn = _Conexao(None)
    limite = PostgresLimitePorCpf(dsn="postgresql://u:p@h/db", tenant="amh", pool=_Pool(conn))
    await limite.registrar_falha(CHAVE_HEX, T0)
    [(sql, args)] = conn.chamadas
    assert "ON CONFLICT (tenant, chave) DO UPDATE" in sql  # uma instrucao atomica
    assert args == ("amh", CHAVE_HEX, T0, JANELA_POR_CPF)


async def test_postgres_bloqueia_com_tres_falhas_dentro_da_janela_e_so_ate_o_fim_dela() -> None:
    conn = _Conexao({"falhas": 3, "janela_inicio": T0})
    limite = PostgresLimitePorCpf(dsn="postgresql://u:p@h/db", tenant="amh", pool=_Pool(conn))
    assert await limite.bloqueado_ate(CHAVE_HEX, T0 + timedelta(hours=1)) == T0 + timedelta(hours=6)
    assert await limite.bloqueado_ate(CHAVE_HEX, T0 + timedelta(hours=6)) is None
    conn.linha = {"falhas": 2, "janela_inicio": T0}
    assert await limite.bloqueado_ate(CHAVE_HEX, T0 + timedelta(hours=1)) is None
    conn.linha = None
    assert await limite.bloqueado_ate(CHAVE_HEX, T0) is None


async def test_postgres_recusa_chave_fora_do_formato_antes_de_qualquer_sql() -> None:
    conn = _Conexao(None)
    limite = PostgresLimitePorCpf(dsn="postgresql://u:p@h/db", tenant="amh", pool=_Pool(conn))
    for ruim in (CPF, "ab" * 31, "AB" * 32, "", "gg" * 32):
        with pytest.raises(LimitePorCpfStoreError):
            await limite.registrar_falha(ruim, T0)
        with pytest.raises(LimitePorCpfStoreError):
            await limite.bloqueado_ate(ruim, T0)
    assert conn.chamadas == []


async def test_postgres_expurga_pela_janela_vencida_e_devolve_quantas() -> None:
    conn = _Conexao(None)
    limite = PostgresLimitePorCpf(dsn="postgresql://u:p@h/db", tenant="amh", pool=_Pool(conn))
    assert await limite.expurgar(T0) == 2
    [(sql, args)] = conn.chamadas
    assert sql.strip().startswith("DELETE FROM acesso_falhas_por_cpf") and args == ("amh", T0, JANELA_POR_CPF)


# --------------------------------------------------------------------------- a migration

_VERSIONS = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_MIGRACAO = _VERSIONS / "0028_acesso_falhas_por_cpf.py"
_FONTE = _MIGRACAO.read_text(encoding="utf-8")
_CODIGO = _FONTE.split('"""', 2)[2]


def test_a_migration_encadeia_em_0027_e_e_a_unica_que_revisa_a_0027() -> None:
    assert re.search(r'^revision: str = "0028"$', _FONTE, re.MULTILINE)
    assert re.search(r'^down_revision: str \| None = "0027"$', _FONTE, re.MULTILINE)
    revisam = [
        p.name
        for p in sorted(_VERSIONS.glob("0*.py"))
        if re.search(r'^down_revision: str \| None = "0027"$', p.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert revisam == ["0028_acesso_falhas_por_cpf.py"]


def test_a_tabela_so_tem_chave_contagem_e_carimbos() -> None:
    criacao = re.search(r"CREATE TABLE acesso_falhas_por_cpf \((.*?)\n        \)\n", _CODIGO, re.DOTALL)
    assert criacao
    colunas = set(
        re.findall(r"^\s{12}([a-z_]+) (?:text|smallint|timestamptz)\b", criacao.group(1), re.MULTILINE)
    )
    assert colunas == {"tenant", "chave", "falhas", "janela_inicio", "ultima_em"}
    proibidas = {"cpf", "nascimento", "nome", "telefone", "phone", "texto", "hash", "conversation_id"}
    assert colunas.isdisjoint(proibidas)


def test_a_chave_so_aceita_64_hexadecimais_e_a_tabela_e_revogada_do_publico() -> None:
    assert "CHECK (chave ~ '^[0-9a-f]{64}$')" in _CODIGO
    assert "PRIMARY KEY (tenant, chave)" in _CODIGO
    assert "REVOKE ALL ON TABLE acesso_falhas_por_cpf FROM PUBLIC" in _CODIGO


def test_o_downgrade_apaga_a_tabela_porque_ela_e_descartavel() -> None:
    assert "DROP TABLE acesso_falhas_por_cpf" in _CODIGO.split("def downgrade", 1)[1]
