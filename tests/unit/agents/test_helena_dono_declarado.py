"""A Helena tem dono com NOME, e a versao do prompt declarada e' a que o codigo carrega.

FRENTE 6 (14/09/2026). `agent.yaml` declarava `reports_to: "gestor.atendimento@amh"` e mais nada.
Endereco de papel nao responde as duas perguntas que importam num agente que fala com paciente:
quem decide se ela pode dizer determinada frase, e quem atende quando ela erra com um
beneficiario de verdade. Um time nao acorda as tres da manha.

E A DERIVA QUE ESTE ARQUIVO TAMBEM FECHA. O yaml declarava `response: response-v2` enquanto o
codigo ja estava em `response-v3` — dois dias de descompasso que ninguem viu, porque a Helena nao
tinha cerca nenhuma ligando as duas fontes (`test_andre.py` tem uma para o Andre; para ela, nao
havia). Esse numero e' o que responderia a um auditor da ANS sobre qual texto falou com o
beneficiario, e apontava para o texto errado.

POR QUE A CERCA COMPARA AS DUAS FONTES, e nao cada uma contra um literal. O jeito do Andre fixa o
dicionario esperado no proprio teste: subir uma versao exige editar TRES lugares, e esquecer o
terceiro deixa a cerca verde sobre uma deriva. Comparando `PROMPT_VERSIONS` com o yaml
diretamente, subir a versao no codigo OBRIGA a atualizar o yaml e nada mais — a cerca nao tem
copia propria para envelhecer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from maezo.agents.helena.graph import PROMPT_VERSIONS

RAIZ = Path(__file__).resolve().parents[3]
AGENT_YAML = RAIZ / "spec" / "agents" / "helena" / "agent.yaml"
SIGNOFF = RAIZ / "docs" / "processes" / "contracts" / "signoffs" / "SP-OP-ESCALATION-001.signoff.yaml"

#: Um dono e' uma PESSOA. Estas formas sao as que aparecem quando alguem tenta declarar um time.
_FORMAS_DE_TIME = ("@amh", "equipe", "time", "squad", "suporte", "atendimento@", "dev@", "ti@")


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load(AGENT_YAML.read_text(encoding="utf-8"))


def test_as_versoes_declaradas_sao_as_que_o_codigo_carrega(spec: dict) -> None:
    """A deriva que existia em 14/09: yaml em `response-v2`, codigo em `response-v3`."""
    assert spec["prompt_versions"] == PROMPT_VERSIONS, (
        "agent.yaml e PROMPT_VERSIONS divergiram. Este numero e' o que diz QUAL texto falou com o "
        "beneficiario; divergente, ele aponta para o texto errado. Suba a versao no codigo e "
        "atualize o yaml na MESMA entrega."
    )


def test_existe_dono_tecnico_com_nome_de_pessoa(spec: dict) -> None:
    tecnico = (spec.get("owners") or {}).get("tecnico", "")

    assert tecnico, "sem dono tecnico declarado — ninguem decide se o texto da Helena muda"
    assert re.search(r"<[^@>]+@[^>]+>", tecnico), "o dono tecnico precisa de e-mail entre <>"
    plano = tecnico.lower()
    assert not any(f in plano for f in _FORMAS_DE_TIME), (
        f"{tecnico!r} parece um endereco de TIME. O documento da Frente 6 e' explicito: nome de "
        "pessoa, porque um time nao acorda as tres da manha."
    )


def test_o_dono_clinico_nasce_do_signoff_e_nao_antes(spec: dict) -> None:
    """O acoplamento e' o ponto: quem assina as 34 regras E' o dono clinico.

    Sem o signoff, `clinico` vazio e' o estado HONESTO — declarar um nome ali antes de alguem ter
    revisado regra nenhuma seria a quinta casca vazia deste projeto (a jornada com estados que nao
    existem, o porto sem implementacao, a memoria episodica declarada em 11 agentes e usada em
    zero, a tabela de suficiencia so' em documento).

    COM o signoff, o campo passa a ser OBRIGATORIO e tem de casar com quem assinou: um signoff
    cujo revisor nao e' o dono clinico declarado deixaria a pergunta "quem responde por isso" sem
    resposta, que e' exatamente o que esta frente existe para fechar.
    """
    clinico = (spec.get("owners") or {}).get("clinico", "")

    if not SIGNOFF.exists():
        assert clinico == "", (
            "ha dono clinico declarado sem signoff nenhum. Quem responde pelas regras e' quem as "
            "assinou; sem assinatura nao ha o que declarar."
        )
        return

    assert clinico, "o signoff existe, entao ha alguem que assinou — declare-o como dono clinico"
    registros = yaml.safe_load(SIGNOFF.read_text(encoding="utf-8")) or []
    assinantes = {str(r.get("reviewer_name", "")).strip() for r in registros}
    nome = clinico.split("<")[0].strip()
    assert nome in assinantes, (
        f"o dono clinico declarado ({nome!r}) nao esta entre quem assinou o signoff "
        f"({sorted(assinantes)})."
    )


def test_o_runbook_de_incidente_existe_e_esta_declarado(spec: dict) -> None:
    """Ter um dono sem procedimento e' ter a quem ligar e nada a dizer."""
    caminho = (spec.get("owners") or {}).get("runbook_incidente", "")

    assert caminho, "sem runbook de incidente declarado"
    assert (RAIZ / caminho).is_file(), f"runbook declarado nao existe: {caminho}"
