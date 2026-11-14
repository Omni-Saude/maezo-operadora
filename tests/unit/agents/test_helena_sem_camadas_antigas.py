"""CERCA DE AUSENCIA da limpeza da saude, etapa 3 (dono, 02/11/2026; DL-0165, adendo de 02/11/2026).

As camadas de saude posteriores ao desenho original SAIRAM do codigo (base de conhecimento, saude gerativa,
busca por significado, v4, triagem v2, coleta, protocolos, alarme por palavra, frase afirmada, acolhimento por
palavra, cobertura assistida). Esta cerca reprova se um modulo delas reaparecer, se alguem importar um deles,
ou se o grafo e o roteador voltarem a citar os nomes delas. Voltar com outra estrategia = um PR novo que tira
o nome daqui, com decisao do dono registrada (nunca reaproveitar as camadas antigas).
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_SRC = _RAIZ / "src" / "maezo"

#: Os modulos (e pacotes) que sairam do codigo.
_MODULOS_REMOVIDOS: tuple[str, ...] = (
    "maezo.conhecimento",
    "maezo.conhecimento.base_saude",
    "maezo.conhecimento.busca_semantica",
    "maezo.conhecimento.saude_gerativa",
    "maezo.conhecimento.jornadas",
    "maezo.conhecimento.medicamentos_comuns",
    "maezo.conhecimento.catalogo_procedimentos",
    "maezo.conhecimento.protocolos",
    "maezo.runtime.inference.embeddings",
    "maezo.agents.helena.helena_v4",
    "maezo.agents.helena.triagem_v2",
    "maezo.agents.helena.alarme_deterministico",
    "maezo.agents.helena.frase_afirmada",
    "maezo.agents.helena.habilidades.protocolo",
    "maezo.agents.helena.habilidades.saude",
    "maezo.agents.helena.habilidades.sintoma",
)
#: Os dados que so' as camadas liam. Ficam, SEM LEITOR, ate' a etapa 4: `spec/conhecimento/**` (a COPY mora no
#: `deploy/Dockerfile`, revisao de seguranca) e as tabelas `helena_triage_admissibility.dmn` e
#: `triage_sufficiency.dmn` (o censo de variaveis do motor e o deploy das tabelas se movem juntos; ver
#: o adendo do DL-0165).
_DADOS_REMOVIDOS: tuple[str, ...] = ("spec/agents/helena/pacote/protocolos",)
#: Nomes das camadas que o grafo, o roteador, as travas e a porta nunca mais citam (identificadores no AST).
_NOMES_PROIBIDOS: frozenset[str] = frozenset(
    {
        # base de saude, saude gerativa, busca por significado
        "_base_saude",
        "_base_antes_dos_gatilhos",
        "_base_dor_sem_alarme",
        "_base_achar_assunto",
        "_gerativa",
        "_gerativa_para",
        "_gerativa_informar",
        "_gerativa_no_alerta",
        "RedatorDeSaude",
        "BuscaSemantica",
        "e_duvida_educativa",
        # v4, triagem v2, coleta
        "_v4",
        "_v4_antes_dos_gatilhos",
        "_v4_sintoma_leve",
        "_triagem_v2",
        "_triagem_v2_decidir",
        "_coleta_enabled",
        "_avaliar_suficiencia",
        "coleta_prompt",
        "COLETA_PROMPT_VERSION",
        "SUFFICIENCY_DMN_KEY",
        "TRIAGEM_V2_DMN_KEY",
        # protocolos
        "_protocolos",
        "_protocolo",
        "PAPEL_PROTOCOLO",
        # alarme por palavra, alarme do modelo, sinal claro, frase afirmada
        "alarme_no_relato",
        "alarme_afirmado",
        "POPULACAO_DO_ALARME",
        "_SINAL_CLARO",
        "sinal_claro_afirmado",
        "_sinal_claro_pelo_texto",
        "escalar_sinal_claro",
        "_roteador_alarme",
        "_VOCABULARIO_DA_TABELA",
        "_sinal_do_modelo_na_tabela",
        "_sinal_do_modelo_e_novo",
        "motivo_sem_sintoma_do_historico",
        "_sem_sintoma_do_historico",
        "_urgencia_que_piorou",
        "_alarme_do_modelo",
        "_escalar_alarme_do_modelo",
        "texto_afirmado",
        "texto_afirmado_do_turno",
        "sinais_do_modelo_afirmados",
        "_alerta_neonatal",
        "_caso_mantido_se_alarme_ja_tratado",
        "_correcao_de_dado_avaliado",
        # acolhimento por palavra, texto fixo do sintoma sem alerta, sombra fora da porta
        "_com_acolhimento",
        "RESPOSTA_ACOLHIMENTO",
        "RESPOSTA_SINTOMA_SEM_ALERTA",
        "_classify_com_sombra",
        "aquecer_catalogo",
    }
)
#: Os arquivos que a cerca de nomes le' (o desenho que ficou: grafo, roteador, travas, porta, composicao).
_ARQUIVOS_VIGIADOS: tuple[str, ...] = (
    "agents/helena/graph.py",
    "agents/helena/roteador.py",
    "agents/helena/travas_entrada.py",
    "agents/helena/modelo_allowlist.py",
    "agents/helena/habilidades/saude_original/fluxo.py",
    "agents/helena/habilidades/conversa/falha_tecnica.py",
    "agents/helena/habilidades/atendimento_humano/escalar.py",
    "agents/helena/habilidades/plano/consulta.py",
    "platform/webhooks/service.py",
    "platform/webhooks/whatsapp/dispatch.py",
    "platform/agente_remoto/servico.py",
)


@pytest.mark.parametrize("modulo", _MODULOS_REMOVIDOS)
def test_o_modulo_da_camada_antiga_nao_existe(modulo: str) -> None:
    try:
        achado = importlib.util.find_spec(modulo)
    except ModuleNotFoundError:
        achado = None  # o pacote pai tambem saiu
    assert achado is None, f"{modulo} voltou ao codigo (limpeza da saude, etapa 3)"


@pytest.mark.parametrize("caminho", _DADOS_REMOVIDOS)
def test_o_dado_da_camada_antiga_nao_existe(caminho: str) -> None:
    assert not (_RAIZ / caminho).exists(), f"{caminho} voltou ao repositorio"


def _arquivos_python() -> list[Path]:
    saida = subprocess.run(
        ["git", "ls-files", "src/*.py", "scripts/*.py", "tests/*.py"],
        capture_output=True,
        text=True,
        cwd=_RAIZ,
        check=True,
    ).stdout.split()
    return [_RAIZ / p for p in saida if (_RAIZ / p).exists()]


def test_ninguem_importa_um_modulo_removido() -> None:
    removidos = set(_MODULOS_REMOVIDOS)
    achados: list[str] = []
    for arquivo in _arquivos_python():
        try:
            arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for no in ast.walk(arvore):
            nomes: list[str] = []
            if isinstance(no, ast.Import):
                nomes = [a.name for a in no.names]
            elif isinstance(no, ast.ImportFrom) and no.module and no.level == 0:
                nomes = [no.module, *(f"{no.module}.{a.name}" for a in no.names)]
            for nome in nomes:
                if nome in removidos or any(nome.startswith(f"{r}.") for r in removidos):
                    achados.append(f"{arquivo.relative_to(_RAIZ)}:{no.lineno} {nome}")
    assert not achados, "import de camada removida:\n" + "\n".join(achados)


@pytest.mark.parametrize("relativo", _ARQUIVOS_VIGIADOS)
def test_o_grafo_e_o_roteador_nao_citam_as_camadas_antigas(relativo: str) -> None:
    arvore = ast.parse((_SRC / relativo).read_text(encoding="utf-8"))
    citados: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Name):
            citados.add(no.id)
        elif isinstance(no, ast.Attribute):
            citados.add(no.attr)
        elif isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            citados.add(no.name)
        elif isinstance(no, ast.alias):
            citados.add(no.asname or no.name.rsplit(".", 1)[-1])
    proibidos = sorted(citados & _NOMES_PROIBIDOS)
    assert not proibidos, f"{relativo} voltou a citar camadas antigas de saude: {proibidos}"


def test_o_grafo_compilado_nao_tem_o_no_da_coleta() -> None:
    from maezo.agents.helena.graph import HelenaGraph

    grafo = HelenaGraph(
        inference=object(),  # type: ignore[arg-type]
        dmn=object(),  # type: ignore[arg-type]
        cibseven=object(),  # type: ignore[arg-type]
        audit_sink=object(),  # type: ignore[arg-type]
        whatsapp=object(),  # type: ignore[arg-type]
    ).compile_graph()
    assert "collect" not in grafo.nodes
    assert {"receive", "classify", "inform", "escalate", "respond"} <= set(grafo.nodes)


def test_a_cerca_nao_e_vacua() -> None:
    """Controle positivo: a mesma leitura acha um nome do desenho que ficou (a porta unica)."""
    arvore = ast.parse((_SRC / "agents/helena/roteador.py").read_text(encoding="utf-8"))
    nomes = {no.attr for no in ast.walk(arvore) if isinstance(no, ast.Attribute)}
    assert "atender" in nomes and "LEITURA_DO_TURNO" in nomes
