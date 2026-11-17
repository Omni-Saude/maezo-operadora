"""`maezo.platform.rco_ativacao` (ADR-0077 §1-§4, unidade 5c-1): indicador de ambiente, leitor do estado
manual, tabela da ativacao efetiva, filtro de saida e amostragem HMAC. Logica pura, sem banco nem engine."""

from __future__ import annotations

import hashlib
import hmac
import itertools
from pathlib import Path

import pytest
import structlog

from maezo.platform import rco_ativacao as at

ESTADOS = at.ESTADOS_MANUAIS
ATIVACOES_PACOTE = ("plena", "conferida", "em_correcao")
OVERRIDES = (None, "conferencia_integral")
ORDEM = {"plena": 0, "conferida": 1, "conferencia_integral": 2, "em_correcao": 3}


# --- 1. indicador de ambiente ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(at.CHAVE_EFEITO_K1, raising=False)
    monkeypatch.delenv(at.VARIAVEL_DO_AMBIENTE, raising=False)


def test_nenhum_deploy_ou_config_liga_o_efeito_k1() -> None:
    # DL-0181: o indicador nasce desligado e nao e ligado em nenhum ambiente antes do 5d-1. Nenhum
    # arquivo de deploy, compose, config ou chart pode citar a chave (a unica forma de liga-la).
    raiz = Path(__file__).resolve().parents[3]
    alvos = [
        raiz / "deploy",
        raiz / "config",
        raiz / "docker-compose.yml",
        raiz / ".github",
        raiz / "scripts",
        raiz / "tools",
        raiz / "Makefile",
        *sorted(p for p in raiz.iterdir() if p.is_file() and (".env" in p.name or "compose" in p.name)),
    ]
    # Fora de deploy/, documentacao pode citar a chave (README de config/integrations/rco/ a explica).
    so_doc_fora_de_deploy = {".md"}
    achados = []
    for alvo in alvos:
        if not alvo.exists():
            continue
        arquivos = [alvo] if alvo.is_file() else [p for p in alvo.rglob("*") if p.is_file()]
        for arquivo in arquivos:
            if "__pycache__" in arquivo.parts:
                continue
            if arquivo.suffix in so_doc_fora_de_deploy and (raiz / "deploy") not in arquivo.parents:
                continue
            try:
                texto = arquivo.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if at.CHAVE_EFEITO_K1 in texto:
                achados.append(str(arquivo.relative_to(raiz)))
    assert achados == []
    assert alvos[0].is_dir() and alvos[1].is_dir() and alvos[2].is_file()


def test_allowlist_so_tem_dev() -> None:
    assert frozenset({"dev"}) == at.AMBIENTES_QUE_PODEM_LIGAR


def test_padrao_e_falso_sem_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    assert at.efeito_k1_habilitado() is False
    monkeypatch.setenv(at.VARIAVEL_DO_AMBIENTE, "dev")
    assert at.efeito_k1_habilitado() is False  # dev sem a chave


@pytest.mark.parametrize("valor", ["1", "true", "TRUE", " yes ", "on"])
def test_dev_com_chave_liga(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv(at.VARIAVEL_DO_AMBIENTE, "Dev")
    monkeypatch.setenv(at.CHAVE_EFEITO_K1, valor)
    assert at.efeito_k1_habilitado() is True


@pytest.mark.parametrize("valor", ["0", "false", "", "talvez", "2"])
def test_chave_nao_verdadeira_nao_liga(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv(at.VARIAVEL_DO_AMBIENTE, "dev")
    monkeypatch.setenv(at.CHAVE_EFEITO_K1, valor)
    assert at.efeito_k1_habilitado() is False


@pytest.mark.parametrize("ambiente", ["prod", "production", "", "homolog", "staging", "dev2"])
def test_fora_da_allowlist_nunca_liga_e_avisa(monkeypatch: pytest.MonkeyPatch, ambiente: str) -> None:
    monkeypatch.setenv(at.VARIAVEL_DO_AMBIENTE, ambiente)
    monkeypatch.setenv(at.CHAVE_EFEITO_K1, "true")
    with structlog.testing.capture_logs() as logs:
        assert at.efeito_k1_habilitado() is False
    assert [e["event"] for e in logs] == ["rco_efeito_k1_recusado_ambiente"]
    assert logs[0]["log_level"] == "warning"


def test_ambiente_ausente_com_chave_nao_liga(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(at.CHAVE_EFEITO_K1, "true")
    assert at.efeito_k1_habilitado() is False


def test_configurado_so_vale_true_ou_texto_verdadeiro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(at.VARIAVEL_DO_AMBIENTE, "dev")
    assert at.efeito_k1_habilitado(configurado=True) is True
    assert at.efeito_k1_habilitado(configurado=1) is False
    assert at.efeito_k1_habilitado(configurado=False) is False
    monkeypatch.setenv(at.VARIAVEL_DO_AMBIENTE, "prod")
    assert at.efeito_k1_habilitado(configurado=True) is False


def test_ambiente_e_lido_vivo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(at.CHAVE_EFEITO_K1, "true")
    monkeypatch.setenv(at.VARIAVEL_DO_AMBIENTE, "dev")
    assert at.efeito_k1_habilitado() is True
    monkeypatch.setenv(at.VARIAVEL_DO_AMBIENTE, "prod")
    assert at.efeito_k1_habilitado() is False


def test_nenhum_arquivo_de_deploy_liga_o_indicador() -> None:
    raiz = Path(__file__).resolve().parents[3]
    lidos = 0
    for pasta in ("deploy", ".github"):
        base = raiz / pasta
        assert base.is_dir(), pasta
        for arq in base.rglob("*"):
            if not arq.is_file():
                continue
            texto = arq.read_bytes().decode("utf-8", errors="replace").upper()
            lidos += 1
            assert "RCO_EFEITO" not in texto, arq
    assert lidos > 50


# --- 2. leitor do estado manual -------------------------------------------------------------------

VALIDO = """
version: 1
regras:
  - {idRegra: R-1, versaoRegra: "1.0.0", estado: ativa_l2}
  - {idRegra: R-1, versaoRegra: "1.1.0", estado: desligada}
  - {idRegra: R-2, versaoRegra: "2.0.0", estado: sombra}
"""


def test_arquivo_comitado_esta_vazio_e_tudo_e_sombra() -> None:
    raiz = Path(__file__).resolve().parents[3]
    est = at.carregar_estados(raiz / "spec" / "processes" / "dmn" / at.ARQUIVO_ESTADO_NOME)
    assert est.ilegivel is False
    assert dict(est.estados) == {}
    assert est.estado_de("QUALQUER", "1.0.0") == "sombra"


def test_entradas_validas() -> None:
    est = at.ler_estados(VALIDO)
    assert not est.ilegivel
    assert est.estado_de("R-1", "1.0.0") == "ativa_l2"
    assert est.estado_de("R-1", "1.1.0") == "desligada"
    assert est.estado_de("R-2", "2.0.0") == "sombra"
    assert est.estado_de("R-1", "9.9.9") == "sombra"  # versao nova = regra nova
    assert est.estado_de("R-3", "1.0.0") == "sombra"


def test_arquivo_vazio_de_entradas() -> None:
    assert at.ler_estados("version: 1\nregras: []\n").estados == {}


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "[]",
        "version: 1\n",
        "version: 2\nregras: []\n",
        "version: true\nregras: []\n",
        "version: 1\nregras: {}\n",
        "version: 1\nregras: []\nextra: 1\n",
        "version: 1\nregras:\n  - {idRegra: R-1, versaoRegra: '1.0.0'}\n",
        "version: 1\nregras:\n  - {idRegra: R-1, versaoRegra: '1.0.0', estado: ativa}\n",
        "version: 1\nregras:\n  - {idRegra: R-1, versaoRegra: '1.0.0', estado: Sombra}\n",
        "version: 1\nregras:\n  - {idRegra: R-1, versaoRegra: 1.0, estado: sombra}\n",
        "version: 1\nregras:\n  - {idRegra: R-1, versaoRegra: '1.0', estado: sombra}\n",
        "version: 1\nregras:\n  - {idRegra: 'R 1', versaoRegra: '1.0.0', estado: sombra}\n",
        "version: 1\nregras:\n  - {idRegra: R-1, versaoRegra: '1.0.0', estado: sombra, x: 1}\n",
        "version: 1\nregras:\n  - not-a-mapping\n",
        # repetida (mesma chave) e chave YAML repetida
        "version: 1\nregras:\n  - {idRegra: R-1, versaoRegra: '1.0.0', estado: sombra}\n"
        "  - {idRegra: R-1, versaoRegra: '1.0.0', estado: ativa_l2}\n",
        "version: 1\nversion: 1\nregras: []\n",
        "version: 1\nregras: [\n",
        # revisao 5c-1: chave nao hasheavel, aninhamento profundo e versao float
        "? [a]\n: b\n",
        "[" * 5000,
        "version: 1.0\nregras: []\n",
    ],
)
def test_invalido_vira_tudo_desligado(texto: str) -> None:
    est = at.ler_estados(texto)
    assert est.ilegivel is True
    assert est.motivo
    assert est.estado_de("R-1", "1.0.0") == "desligada"
    assert est.estado_de("QUALQUER", "0.0.1") == "desligada"


def test_arquivo_inexistente_ou_nao_utf8(tmp_path: Path) -> None:
    assert at.carregar_estados(tmp_path / "nao-existe.yaml").ilegivel is True
    ruim = tmp_path / "ruim.yaml"
    ruim.write_bytes(b"\xff\xfe\x00bad")
    assert at.carregar_estados(ruim).ilegivel is True


def test_diretorio_no_lugar_do_arquivo(tmp_path: Path) -> None:
    assert at.carregar_estados(tmp_path).ilegivel is True


def test_carregar_le_do_disco(tmp_path: Path) -> None:
    arq = tmp_path / "e.yaml"
    arq.write_text(VALIDO, encoding="utf-8")
    assert at.carregar_estados(arq).estado_de("R-1", "1.0.0") == "ativa_l2"


def test_nenhuma_variavel_de_ambiente_desvia_o_arquivo_de_estado() -> None:
    # O arquivo decide se uma regra do rco age: o caminho so vem do argumento ou de resolve_spec_dir.
    fonte = Path(at.__file__).read_text(encoding="utf-8")
    assert "REGRAS_ESTADO_PATH" not in fonte


def test_arquivo_ilegivel_forca_desligada_mesmo_com_estado_sombra() -> None:
    r = at.ativacao_efetiva(
        estado_manual="sombra",
        ativacao_pacote="plena",
        override=None,
        efeito_k1_habilitado=True,
        arquivo_ilegivel=True,
    )
    assert r.modo == "desligada" and at.ALERTA_ARQUIVO_ILEGIVEL in r.alertas


def test_ilegivel_registra_erro_estruturado() -> None:
    with structlog.testing.capture_logs() as logs:
        at.ler_estados("[]")
    assert logs and logs[0]["event"] == "rco_regras_estado_ilegivel" and logs[0]["log_level"] == "error"


# --- 3. ativacao efetiva: tabela completa ----------------------------------------------------------


@pytest.mark.parametrize(
    ("estado", "pacote", "override", "habilitado"),
    list(itertools.product(ESTADOS, ATIVACOES_PACOTE, OVERRIDES, (True, False))),
)
def test_tabela_completa(estado: str, pacote: str, override: str | None, habilitado: bool) -> None:
    r = at.ativacao_efetiva(
        estado_manual=estado, ativacao_pacote=pacote, override=override, efeito_k1_habilitado=habilitado
    )
    esperada = max(
        [pacote, *([override] if override else [])],
        key=lambda a: ORDEM[a],
    )
    assert r.ativacao_efetiva == esperada
    if not habilitado:
        assert r.modo == "sombra"
        if estado == "sombra":
            assert r.alertas == ()
        else:
            assert r.alertas == (f"{at.ALERTA_ESTADO_IGNORADO}:{estado}",)
    else:
        assert r.modo == {"sombra": "sombra", "desligada": "desligada", "ativa_l2": "ativa"}[estado]
        assert r.alertas == ()


def test_ordem_de_restricao() -> None:
    def ef(p: str, o: str | None) -> str:
        return at.ativacao_efetiva(
            estado_manual="ativa_l2", ativacao_pacote=p, override=o, efeito_k1_habilitado=True
        ).ativacao_efetiva

    assert ef("plena", None) == "plena"
    assert ef("conferida", None) == "conferida"
    assert ef("plena", "conferencia_integral") == "conferencia_integral"
    assert ef("conferida", "conferencia_integral") == "conferencia_integral"
    assert ef("em_correcao", "conferencia_integral") == "em_correcao"  # nunca promove


def test_pacote_major1_sem_ativacao_e_plena() -> None:
    r = at.ativacao_efetiva(
        estado_manual="ativa_l2", ativacao_pacote=None, override=None, efeito_k1_habilitado=True
    )
    assert (r.modo, r.ativacao_efetiva, r.alertas) == ("ativa", "plena", ())


@pytest.mark.parametrize("pacote", ["", "PLENA", "conferencia_integral", "x"])
def test_ativacao_desconhecida_fecha_para_em_correcao(pacote: str) -> None:
    r = at.ativacao_efetiva(
        estado_manual="ativa_l2", ativacao_pacote=pacote, override=None, efeito_k1_habilitado=True
    )
    assert r.ativacao_efetiva == "em_correcao" and at.ALERTA_ATIVACAO_DESCONHECIDA in r.alertas


def test_override_desconhecido_e_estado_desconhecido_fecham() -> None:
    r = at.ativacao_efetiva(
        estado_manual="ativa_l2", ativacao_pacote="plena", override="lixo", efeito_k1_habilitado=True
    )
    assert r.ativacao_efetiva == "conferencia_integral" and at.ALERTA_OVERRIDE_DESCONHECIDO in r.alertas
    r2 = at.ativacao_efetiva(
        estado_manual="???", ativacao_pacote="plena", override=None, efeito_k1_habilitado=True
    )
    assert r2.modo == "desligada" and "estado_manual_desconhecido" in r2.alertas


def test_arquivo_ilegivel_alerta_sempre() -> None:
    est = at.ler_estados("[]")
    for hab in (True, False):
        r = at.ativacao_efetiva(
            estado_manual=est.estado_de("R", "1.0.0"),
            ativacao_pacote="plena",
            override=None,
            efeito_k1_habilitado=hab,
            arquivo_ilegivel=est.ilegivel,
        )
        assert at.ALERTA_ARQUIVO_ILEGIVEL in r.alertas
        assert r.modo == ("desligada" if hab else "sombra")


# --- 4. filtro de saida ----------------------------------------------------------------------------


def _estado(modo: str, ativacao: str) -> at.EstadoEfetivo:
    return at.EstadoEfetivo(modo, ativacao)


def _f(
    estado: at.EstadoEfetivo,
    decisao: str | None,
    *,
    exige: bool = False,
    cobre: bool | None = None,
    inicial: bool = False,
    amostrada: bool = False,
    fila: str = "faturamento",
    classe: str | None = None,
) -> at.AcaoDeSaida:
    if classe is None:
        classe = {"negar": "administrativo", "recomendar_negativa": "clinico"}.get(decisao or "", "contas")
    return at.filtrar_saida(
        estado,
        decisao=decisao,
        classe_adverso=classe,
        fila=fila,
        exige_aditivo=exige,
        aditivo_cobre=cobre,
        conferencia_inicial=inicial,
        amostrada=amostrada,
    )


@pytest.mark.parametrize("decisao", ["negar", "glosar", "recomendar_negativa", "pagar", None])
@pytest.mark.parametrize("ativacao", at.ATIVACOES_EFETIVAS)
def test_sombra_sempre_so_registra(decisao: str | None, ativacao: str) -> None:
    assert _f(_estado("sombra", ativacao), decisao, exige=True) == at.AcaoDeSaida("registrar_apenas")


@pytest.mark.parametrize("decisao", ["negar", "glosar", "recomendar_negativa"])
@pytest.mark.parametrize("ativacao", at.ATIVACOES_EFETIVAS)
def test_desligada_adversa_vai_ao_humano(decisao: str, ativacao: str) -> None:
    assert _f(_estado("desligada", ativacao), decisao) == at.AcaoDeSaida(
        "revisao_humana", "faturamento", at.MARCA_DESLIGADA
    )


@pytest.mark.parametrize("modo", ["desligada", "ativa"])
@pytest.mark.parametrize("decisao", sorted(at.DECISOES_NAO_ADVERSAS))
def test_nao_adversa_segue_com_efeito(modo: str, decisao: str) -> None:
    assert _f(_estado(modo, "em_correcao"), decisao, exige=True).acao == "efeito"


@pytest.mark.parametrize("modo", ["desligada", "ativa"])
@pytest.mark.parametrize("decisao", [None, "", "pagar", "NEGAR", "nega", "glosa", "x"])
def test_decisao_nula_ou_desconhecida_vai_ao_humano(modo: str, decisao: str | None) -> None:
    assert _f(_estado(modo, "plena"), decisao) == at.AcaoDeSaida(
        "revisao_humana", "faturamento", at.MARCA_DECISAO_DESCONHECIDA
    )


def test_listas_de_decisao_cobrem_o_vocabulario_k1_sem_sobrepor() -> None:
    import yaml

    raiz = Path(__file__).resolve().parents[3]
    voc = yaml.safe_load(
        (raiz / "config/integrations/rco/vocabulario-saida.yaml").read_text(encoding="utf-8")
    )
    assert set(voc["k1"]["decisao"]) == at.DECISOES_ADVERSAS | at.DECISOES_NAO_ADVERSAS
    assert not at.DECISOES_ADVERSAS & at.DECISOES_NAO_ADVERSAS


@pytest.mark.parametrize("ativacao", ["plena", "conferida", "conferencia_integral"])
@pytest.mark.parametrize(
    ("decisao", "classe"),
    [
        ("negar", "clinico"),
        ("glosar", "clinico"),
        ("negar", "contas"),
        ("negar", None),
        ("glosar", None),
        ("glosar", ""),
    ],
)
def test_classe_incoerente_vai_ao_humano(ativacao: str, decisao: str, classe: str | None) -> None:
    acao = at.filtrar_saida(
        _estado("ativa", ativacao),
        decisao=decisao,
        classe_adverso=classe,
        fila="auditoria_medica",
        exige_aditivo=False,
        aditivo_cobre=None,
    )
    assert acao == at.AcaoDeSaida("revisao_humana", "auditoria_medica", at.MARCA_CLASSE_INCOERENTE)


@pytest.mark.parametrize("decisao", ["negar", "glosar", "recomendar_negativa"])
def test_em_correcao_vai_ao_humano(decisao: str) -> None:
    assert _f(_estado("ativa", "em_correcao"), decisao) == at.AcaoDeSaida(
        "revisao_humana", "faturamento", at.MARCA_EM_CORRECAO
    )


@pytest.mark.parametrize("decisao", ["negar", "glosar", "recomendar_negativa"])
@pytest.mark.parametrize("ativacao", ["plena", "conferida", "conferencia_integral"])
@pytest.mark.parametrize("cobre", [None, False])
def test_exige_aditivo_sem_cobertura_vai_ao_humano(decisao: str, ativacao: str, cobre: bool | None) -> None:
    assert _f(_estado("ativa", ativacao), decisao, exige=True, cobre=cobre) == at.AcaoDeSaida(
        "revisao_humana", "faturamento", at.MARCA_SEM_ADITIVO
    )


def test_exige_aditivo_com_cobertura_segue_o_fluxo() -> None:
    assert _f(_estado("ativa", "plena"), "glosar", exige=True, cobre=True).acao == "efeito_com_amostragem"
    assert _f(_estado("ativa", "plena"), "glosar", exige=False, cobre=None).acao == "efeito_com_amostragem"


def test_conferencia_integral_bloqueia_negar_e_glosar() -> None:
    for d in ("negar", "glosar"):
        assert _f(_estado("ativa", "conferencia_integral"), d) == at.AcaoDeSaida("conferencia", "faturamento")


def test_conferida_confere_so_as_primeiras() -> None:
    est = _estado("ativa", "conferida")
    assert _f(est, "glosar", inicial=True).acao == "conferencia"
    assert _f(est, "glosar", inicial=False, amostrada=True) == at.AcaoDeSaida(
        "efeito_com_amostragem", "faturamento", None, True
    )
    assert _f(est, "glosar", inicial=False).amostrada is False


@pytest.mark.parametrize("inicial", [True, False])
@pytest.mark.parametrize("amostrada", [True, False])
def test_negar_em_conferida_nunca_e_automatico(inicial: bool, amostrada: bool) -> None:
    # DL-0181: com o indicador ligado, `negar` de regra `conferida` vai a conferencia em qualquer ordinal.
    acao = _f(_estado("ativa", "conferida"), "negar", inicial=inicial, amostrada=amostrada)
    assert acao == at.AcaoDeSaida("conferencia", "faturamento")
    assert acao.acao not in {"efeito", "efeito_com_amostragem"}


def test_plena_amostra_so_negar_e_glosar_e_clinico_e_decisao() -> None:
    est = _estado("ativa", "plena")
    assert _f(est, "negar").acao == "efeito_com_amostragem"
    assert _f(est, "glosar", amostrada=True).amostrada is True
    # inicial nao bloqueia em plena (a revisao inicial de D1 e depois do efeito)
    assert _f(est, "glosar", inicial=True).acao == "efeito_com_amostragem"
    rec = _f(est, "recomendar_negativa", fila="auditoria_medica", amostrada=True)
    assert rec == at.AcaoDeSaida("revisao_humana", "auditoria_medica", at.MARCA_RECOMENDACAO)


def test_recomendar_negativa_em_conferencia_integral_e_decisao_humana() -> None:
    assert _f(_estado("ativa", "conferencia_integral"), "recomendar_negativa").acao == "revisao_humana"


def test_acoes_so_do_conjunto_fechado() -> None:
    for modo, ativ, dec in itertools.product(
        ("sombra", "desligada", "ativa"),
        at.ATIVACOES_EFETIVAS,
        ("negar", "glosar", "recomendar_negativa", "x"),
    ):
        for exige, cobre, ini, am in itertools.product(
            (False, True), (None, False, True), (False, True), (False, True)
        ):
            assert (
                _f(_estado(modo, ativ), dec, exige=exige, cobre=cobre, inicial=ini, amostrada=am).acao
                in at.ACOES
            )


# --- 5. HMAC ---------------------------------------------------------------------------------------

CHAVE = b"chave-de-teste-0123456789abcdef"


def test_vetor_conhecido_pinado() -> None:
    def campo(v: str) -> bytes:
        b = v.encode()
        return len(b).to_bytes(4, "big") + b

    msg = campo("caso-1") + campo("R-1") + campo("1.0.0")
    assert msg == b"\x00\x00\x00\x06caso-1\x00\x00\x00\x03R-1\x00\x00\x00\x051.0.0"
    digest = hmac.new(CHAVE, msg, hashlib.sha256).digest()
    assert digest.hex() == "e9654ef01fc162e983b8ea732b4ae8ae2748300fd8d01b20e20c336a12479033"
    assert int.from_bytes(digest[:8], "big") % 1_000_000 == 870377
    # fronteira exata: valor 870377 esta abaixo do limiar 870378 e nao do 870377
    assert at.amostrar(CHAVE, "k1", "caso-1", "R-1", "1.0.0", 87.0377) == (False, "")
    assert at.amostrar(CHAVE, "k1", "caso-1", "R-1", "1.0.0", 87.0378) == (True, at.MOTIVO_ALEATORIA)


def test_deterministico() -> None:
    a = at.amostrar(CHAVE, "k1", "caso-1", "R-1", "1.0.0", 50)
    assert all(at.amostrar(CHAVE, "k1", "caso-1", "R-1", "1.0.0", 50) == a for _ in range(5))


def test_prefixo_de_tamanho_evita_ambiguidade() -> None:
    def valor(cn: str, ir: str, vr: str) -> int:
        def campo(v: str) -> bytes:
            b = v.encode()
            return len(b).to_bytes(4, "big") + b

        d = hmac.new(CHAVE, campo(cn) + campo(ir) + campo(vr), hashlib.sha256).digest()
        return int.from_bytes(d[:8], "big")

    assert valor("ab", "c", "1.0.0") != valor("a", "bc", "1.0.0")
    # e a funcao publica reflete isso: as duas combinacoes divergem em algum limiar
    achou = False
    for i in range(1, 200):
        a = at.amostrar(CHAVE, "k1", "ab", "c", "1.0.0", i / 2)
        b = at.amostrar(CHAVE, "k1", "a", "bc", "1.0.0", i / 2)
        achou = achou or a != b
    assert achou


def test_utf8_e_chave_diferente_muda_o_resultado() -> None:
    r1 = [at.amostrar(CHAVE, "k1", f"çã-{i}", "R", "1.0.0", 50)[0] for i in range(200)]
    r2 = [at.amostrar(b"outra-chave", "k2", f"çã-{i}", "R", "1.0.0", 50)[0] for i in range(200)]
    assert r1 != r2


def test_fronteiras_de_pct() -> None:
    for i in range(50):
        assert at.amostrar(CHAVE, "k1", f"c{i}", "R", "1.0.0", 100) == (True, at.MOTIVO_ALEATORIA)
        assert at.amostrar(CHAVE, "k1", f"c{i}", "R", "1.0.0", 0) == (False, "")
    assert at.limiar_amostra(100) == 1_000_000
    assert at.limiar_amostra(5) == 50_000
    assert at.limiar_amostra(0.0001) == 1
    assert at.limiar_amostra(87.0377) == 870_377  # exato, sem erro binario


@pytest.mark.parametrize("pct", [-1, 100.0001, float("nan"), float("inf"), True, "5"])
def test_pct_invalido_levanta(pct: object) -> None:
    with pytest.raises(ValueError):
        at.limiar_amostra(pct)  # type: ignore[arg-type]


@pytest.mark.parametrize("chave", [None, b""])
def test_sem_chave_amostra_tudo(chave: bytes | None) -> None:
    assert at.amostrar(chave, "k1", "c", "R", "1.0.0", 5) == (True, at.MOTIVO_CHAVE_AUSENTE)


@pytest.mark.parametrize(
    ("chave", "negocio", "pct"),
    [("texto", "c", 5), (CHAVE, "", 5), (CHAVE, None, 5), (CHAVE, "c", -1), (CHAVE, "c", float("nan"))],
)
def test_entrada_ruim_amostra_em_vez_de_levantar(chave: object, negocio: object, pct: float) -> None:
    assert at.amostrar(chave, "k1", negocio, "R", "1.0.0", pct) == (True, at.MOTIVO_CHAVE_AUSENTE)  # type: ignore[arg-type]


def test_chave_sem_id_conta_como_ausente() -> None:
    assert at.amostrar(CHAVE, None, "c", "R", "1.0.0", 5) == (True, at.MOTIVO_CHAVE_AUSENTE)
    assert at.amostrar(CHAVE, "", "c", "R", "1.0.0", 5) == (True, at.MOTIVO_CHAVE_AUSENTE)


@pytest.mark.parametrize("pct", [5, 25, 50])
def test_taxa_perto_de_pct(pct: int) -> None:
    n = 20_000
    k = sum(at.amostrar(CHAVE, "k1", f"guia-{i}", "R-1", "1.0.0", pct)[0] for i in range(n))
    esperado = n * pct / 100
    sigma = (n * pct / 100 * (1 - pct / 100)) ** 0.5
    assert abs(k - esperado) < 5 * sigma


def test_amostra_inicial() -> None:
    assert at.amostra_inicial(1, 30) == (True, at.MOTIVO_INICIAL)
    assert at.amostra_inicial(30, 30) == (True, at.MOTIVO_INICIAL)
    assert at.amostra_inicial(31, 30) == (False, "")
    assert at.amostra_inicial(0, 30) == (False, "")
