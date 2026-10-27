"""Testes da cerca das flags dark do programa (W5 CLNT 5.1 — "flags provadamente OFF").

Cinco camadas, no molde de `test_check_portal_direct_completion.py`/`test_check_helm_entrypoints.py`:

1. **Arvore real verde** — `executar()` na arvore rastreada NAO acha nada, e o verde nao e'
   vacuo: os 6 campos registrados existem, o prefixo bate, o `deploy/**` tem arquivos e as
   constantes
   das pontes valem os pinos.
2. **Mutacao do default** (prova de mutacao, sintetica): `lgpd_dsr_channel = True` deixa a
   cerca VERMELHA nomeando o campo — o padrao das non-negotiables da casa (prova de
   mutacao, nao so' verde).
3. **Cerco cego** — campo removido, classe removida, `env_prefix` trocado e modulo das
   pontes com constante renomeada: cada um reprova NOMEANDO, nunca silencio.
4. **Deploy** — qualquer mencao a qualquer um dos 8 nomes de env em `deploy/**` (.tf, .yaml,
   ate .md) reprova, com arquivo:linha; e `deploy/` vazio/ausente reprova (varrer nao e' prova).
   `__pycache__` de sessao pytest fica fora por `path.parts` (classe F-1, mesmo reparo do #985).
5. **Pinos de nome** — os nomes de env pinnados na cerca derivam de `env_prefix` + campo
   real (portal) e das constantes de modulo (pontes); drift em qualquer um dos dois lados
   fica vermelho.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.ci.check_program_dark_flags import (
    CONFIG_PORTAL,
    ENV_FLAGS_PROGRAMA,
    ENV_PREFIX_PORTAL,
    checar_deploy_sem_mencao,
    checar_flags_das_pontes,
    executar,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_REAL = _REPO_ROOT / CONFIG_PORTAL


# ---------------------------------------------------------------------------
# 1. Arvore real verde — e nao-vacua
# ---------------------------------------------------------------------------


def test_cerca_verde_na_arvore_real() -> None:
    assert executar(_REPO_ROOT) == []


def test_verde_nao_e_vacuo_campos_prefixo_e_deploy_existem() -> None:
    # Os 6 campos registrados estao la, exatamente uma vez, com default literal False — se
    # algum
    # sumir/renamear SEM tocar a cerca, o teste acima fica vermelho; este fixa a CONTAGEM.
    texto = _CONFIG_REAL.read_text(encoding="utf-8")
    for _relativo, classe, campo, _esperado in (
        (CONFIG_PORTAL, "PortalSettings", "lgpd_dsr_channel", False),
        (CONFIG_PORTAL, "PortalSettings", "staff_documents_panel", False),
        (CONFIG_PORTAL, "PortalSettings", "staff_operations_panel", False),
        (CONFIG_PORTAL, "PortalSettings", "staff_admin_panel", False),
        (CONFIG_PORTAL, "PortalSettings", "staff_commission_policies", False),
        (CONFIG_PORTAL, "PortalSettings", "collective_legitimacy_screen", False),
    ):
        declaracoes = [linha for linha in texto.splitlines() if linha.strip().startswith(f"{campo}: bool = ")]
        assert len(declaracoes) == 1, f"{classe}.{campo} declarado {len(declaracoes)}x"
        assert declaracoes[0].rstrip().endswith("= False"), declaracoes[0]
    assert ENV_PREFIX_PORTAL == "MAEZO_PORTAL_"
    assert (_REPO_ROOT / "deploy").is_dir()
    assert any((_REPO_ROOT / "deploy").rglob("*")), "deploy vazio — a cerca estaria varrendo nada"


def test_os_nomes_de_env_pinados_sao_os_registrados_na_cerca() -> None:
    assert ENV_FLAGS_PROGRAMA == (
        "MAEZO_PORTAL_LGPD_DSR_CHANNEL",
        "MAEZO_PORTAL_STAFF_DOCUMENTS_PANEL",
        "MAEZO_PORTAL_STAFF_OPERATIONS_PANEL",
        "MAEZO_PORTAL_STAFF_ADMIN_PANEL",
        "MAEZO_PORTAL_STAFF_COMMISSION_POLICIES",
        "MAEZO_PORTAL_COLLECTIVE_LEGITIMACY_SCREEN",
        "MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED",
        "MAEZO_ERASURE_BRIDGE_THREAD_ENABLED",
    )


# ---------------------------------------------------------------------------
# 2/3. Mutacoes sinteticas do codigo do portal
# ---------------------------------------------------------------------------


def _arvore_sintetica(tmp_path: Path) -> Path:
    raiz = tmp_path
    destino = raiz / CONFIG_PORTAL
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(_CONFIG_REAL.read_text(encoding="utf-8"), encoding="utf-8")
    # deploy/ presente e benigno: a cerca de deploy reprova arvore ausente/vazia (varrer nada
    # nao e' prova), entao as fixtures de CODIGO trazem um deploy valido para o ruido nao
    # ofuscar o achado sob teste.
    (raiz / "deploy").mkdir(exist_ok=True)
    (raiz / "deploy" / "values.yaml").write_text("portal:\n  tenant: amh\n", encoding="utf-8")
    return raiz


def test_default_mutado_para_true_fica_vermelho_nomeando_o_campo(tmp_path: Path) -> None:
    raiz = _arvore_sintetica(tmp_path)
    alvo = raiz / CONFIG_PORTAL
    mutado = alvo.read_text(encoding="utf-8").replace(
        "    lgpd_dsr_channel: bool = False", "    lgpd_dsr_channel: bool = True"
    )
    assert mutado != alvo.read_text(encoding="utf-8"), "mudanca nao aplicada — fixture invalida"
    alvo.write_text(mutado, encoding="utf-8")
    achados = executar(raiz)
    assert any("lgpd_dsr_channel" in a and "default literal False" in a for a in achados), achados


def test_default_mutado_por_assignment_de_constante_tambem_reprova(tmp_path: Path) -> None:
    # `= False` virando `= os.environ.get(...)` (nao-literal) NAO passa: so' o literal serve.
    raiz = _arvore_sintetica(tmp_path)
    alvo = raiz / CONFIG_PORTAL
    mutado = alvo.read_text(encoding="utf-8").replace(
        "    staff_operations_panel: bool = False",
        '    staff_operations_panel: bool = bool(os.environ.get("X"))',
    )
    assert mutado != alvo.read_text(encoding="utf-8")
    alvo.write_text(mutado, encoding="utf-8")
    assert any("staff_operations_panel" in a for a in executar(raiz))


def test_campo_removido_e_cerca_cega_reprovada(tmp_path: Path) -> None:
    raiz = _arvore_sintetica(tmp_path)
    alvo = raiz / CONFIG_PORTAL
    linhas = [
        linha
        for linha in alvo.read_text(encoding="utf-8").splitlines(keepends=True)
        if not linha.strip().startswith("staff_documents_panel: bool = ")
    ]
    alvo.write_text("".join(linhas), encoding="utf-8")
    achados = executar(raiz)
    assert any("staff_documents_panel" in a and "uma vez" in a for a in achados), achados


def test_classe_ausente_e_cerca_cega_reprovada(tmp_path: Path) -> None:
    raiz = _arvore_sintetica(tmp_path)
    alvo = raiz / CONFIG_PORTAL
    alvo.write_text("x: int = 1\n", encoding="utf-8")
    achados = executar(raiz)
    assert any("PortalSettings nao encontrada" in a for a in achados), achados


def test_env_prefix_trocado_reprova(tmp_path: Path) -> None:
    raiz = _arvore_sintetica(tmp_path)
    alvo = raiz / CONFIG_PORTAL
    mutado = alvo.read_text(encoding="utf-8").replace(
        'env_prefix="MAEZO_PORTAL_"', 'env_prefix="MAEZO_OUTRO_"'
    )
    assert mutado != alvo.read_text(encoding="utf-8")
    alvo.write_text(mutado, encoding="utf-8")
    achados = executar(raiz)
    assert any("env_prefix" in a for a in achados), achados


def test_arquivo_de_config_ausente_reprova(tmp_path: Path) -> None:
    achados = executar(tmp_path)
    assert any("nao foi possivel analisar" in a for a in achados), achados


# ---------------------------------------------------------------------------
# 4. Deploy: mencao nenhuma; varrer nada nao e' prova
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nome_arquivo", "conteudo"),
    [
        (
            "service-portal.tf",
            'environment = [{ name = "MAEZO_PORTAL_LGPD_DSR_CHANNEL", value = "true" }]\n',
        ),
        ("values.yaml", 'portal:\n  MAEZO_PORTAL_STAFF_DOCUMENTS_PANEL: "1"\n'),
        (
            "deployment.yaml",
            '- name: MAEZO_PORTAL_STAFF_OPERATIONS_PANEL\n          value: "0"\n',
        ),
        ("chart.yaml", 'MAEZO_PORTAL_STAFF_ADMIN_PANEL: "true"\n'),
        (
            "op21-values.yaml",
            "# OP21: MAEZO_PORTAL_COLLECTIVE_LEGITIMACY_SCREEN desligada\n",
        ),
        ("compose.yaml", 'MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED: "true"\n'),
        ("NOTAS.md", "# TODO: ligar MAEZO_ERASURE_BRIDGE_THREAD_ENABLED no proximo ciclo\n"),
    ],
)
def test_qualquer_mencao_em_deploy_reprova_com_arquivo_e_linha(
    tmp_path: Path, nome_arquivo: str, conteudo: str
) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / nome_arquivo).write_text(conteudo, encoding="utf-8")
    achados = checar_deploy_sem_mencao(tmp_path)
    assert len(achados) == 1, achados
    linha_esperada = next(i for i, linha in enumerate(conteudo.splitlines(), start=1) if "MAEZO_" in linha)
    assert achados[0].startswith(f"deploy/{nome_arquivo}:{linha_esperada}:")
    # A mencao nomeia EXATAMENTE a flag — o operador le qual portao foi tocado.
    nome = next(n for n in ENV_FLAGS_PROGRAMA if n in conteudo)
    assert nome in achados[0]


def test_deploy_sem_mencao_nenhuma_e_verde(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "values.yaml").write_text("portal:\n  tenant: amh\n", encoding="utf-8")
    assert checar_deploy_sem_mencao(tmp_path) == []


def test_deploy_ausente_reprova(tmp_path: Path) -> None:
    achados = checar_deploy_sem_mencao(tmp_path)
    assert len(achados) == 1
    assert "varrer nada nao e' prova" in achados[0]


def test_deploy_vazio_reprova(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir()
    achados = checar_deploy_sem_mencao(tmp_path)
    assert len(achados) == 1
    assert "varrer nada nao e' prova" in achados[0]


def test_deploy_subdiretorios_sao_varridos(tmp_path: Path) -> None:
    aninhado = tmp_path / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1"
    aninhado.mkdir(parents=True)
    (aninhado / "service-portal.tf").write_text(
        'MAEZO_PORTAL_STAFF_DOCUMENTS_PANEL = "true"\n', encoding="utf-8"
    )
    achados = checar_deploy_sem_mencao(tmp_path)
    assert len(achados) == 1
    assert "MAEZO_PORTAL_STAFF_DOCUMENTS_PANEL" in achados[0]


def test_pycache_de_sessao_pytest_nao_reprova(tmp_path: Path) -> None:
    # Classe F-1 (o mesmo reparo do #985): uma sessao pytest que importe lambda de deploy
    # planta `deploy/**/__pycache__/*.pyc` — bytes nao-UTF-8, nunca declaracao de deployment.
    # A exclusao e' por `path.parts` e o arquivo legivel de verdade ao lado segue varrido.
    templates = tmp_path / "deploy" / "helm" / "maezo-tenant" / "templates"
    (templates / "__pycache__").mkdir(parents=True)
    (templates / "__pycache__" / "modulo.cpython-314.pyc").write_bytes(b"\x80\x04\x00binario")
    (templates / "values.yaml").write_text("portal:\n  tenant: amh\n", encoding="utf-8")
    assert checar_deploy_sem_mencao(tmp_path) == []


def test_ilegivel_fora_de_pycache_continua_reprovando(tmp_path: Path) -> None:
    # Nao-vacuidade da exclusao F-1: bytes nao-UTF-8 em arquivo REAL de deploy segue
    # reprovando "ilegivel" — o reparo nao amolece a postura fail-closed.
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "dump.bin").write_bytes(b"\x80\x04\x00binario")
    achados = checar_deploy_sem_mencao(tmp_path)
    assert len(achados) == 1
    assert "ilegivel" in achados[0]


def test_deploy_so_com_pycache_reprova_varrer_nada(tmp_path: Path) -> None:
    # A exclusao nao pode esvaziar a varredura: __pycache__ nao conta como prova de flag OFF.
    (tmp_path / "deploy" / "__pycache__").mkdir(parents=True)
    (tmp_path / "deploy" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"\x80\x04")
    achados = checar_deploy_sem_mencao(tmp_path)
    assert len(achados) == 1
    assert "varrer nada nao e' prova" in achados[0]


# ---------------------------------------------------------------------------
# 5. Pinos das pontes (renomear flag no codigo deixa a cerca vermelha)
# ---------------------------------------------------------------------------


def test_pinos_das_pontes_batem_com_o_modulo_real() -> None:
    assert checar_flags_das_pontes(_REPO_ROOT) == []


def test_constante_de_ponte_renomeada_reprova(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import maezo.platform.identity_bridges as ib

    monkeypatch.setattr(ib, "TITULAR_BRIDGE_FLAG_ENV", "MAEZO_ERASURE_BRIDGE_TITULAR_ON")
    achados = checar_flags_das_pontes(tmp_path)
    assert len(achados) == 1
    assert "TITULAR_BRIDGE_FLAG_ENV" in achados[0]
    assert "MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED" in achados[0]


def test_constante_de_ponte_ausente_reprova(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import maezo.platform.identity_bridges as ib

    monkeypatch.delattr(ib, "THREAD_BRIDGE_FLAG_ENV")
    achados = checar_flags_das_pontes(tmp_path)
    assert len(achados) == 1
    assert "THREAD_BRIDGE_FLAG_ENV" in achados[0]


def test_executar_agrega_as_tres_cercas(tmp_path: Path) -> None:
    # Um raiz sem NADA reprova nas tres: defaults (sem config), deploy (sem arvore),
    # pontes (pinos batem — o modulo real continua importavel — mas defaults/deploy falam).
    achados = executar(tmp_path)
    assert any("nao foi possivel analisar" in a for a in achados)
    assert any("varrer nada nao e' prova" in a for a in achados)
    assert not any("TITULAR_BRIDGE_FLAG_ENV" in a for a in achados)


def test_mutacao_de_default_e_mencao_em_deploy_juntas_nomeiam_tudo(tmp_path: Path) -> None:
    raiz = _arvore_sintetica(tmp_path)
    (raiz / "deploy" / "x.tf").write_text('MAEZO_PORTAL_LGPD_DSR_CHANNEL = "true"\n', encoding="utf-8")
    alvo = raiz / CONFIG_PORTAL
    alvo.write_text(
        alvo.read_text(encoding="utf-8").replace(
            "    lgpd_dsr_channel: bool = False", "    lgpd_dsr_channel: bool = True"
        ),
        encoding="utf-8",
    )
    achados = executar(raiz)
    assert any("lgpd_dsr_channel" in a and "default literal False" in a for a in achados)
    assert any("MAEZO_PORTAL_LGPD_DSR_CHANNEL" in a for a in achados)


# ---------------------------------------------------------------------------
# Guarda de regressao da fixture: a copia sintetica e' byte-identica a real
# ---------------------------------------------------------------------------


def test_arvore_sintetica_copia_a_config_real_byte_a_byte(tmp_path: Path) -> None:
    raiz = _arvore_sintetica(tmp_path)
    assert (raiz / CONFIG_PORTAL).read_bytes() == _CONFIG_REAL.read_bytes()
    # e a cerca sobre essa copia intacta continua verde
    assert executar(raiz) == []
