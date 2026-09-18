"""A versao de prompt declarada do Lucas e' a que o codigo carrega — e ele tem dono com NOME.

A MESMA CERCA QUE A HELENA GANHOU NA FRENTE 6 (14/09/2026), aplicada ao agente que nao a tinha. E
a deriva que ela encontra no Lucas e' maior que a da Helena: o `agent.yaml` dele declarava DOIS
prompts (`system`, `message`) enquanto o codigo carregava tres — a narrativa do dossie
(`dossier-v1`) existia desde sempre e nunca esteve no yaml. A cerca de saida de 18/09/2026 achou
isso ao precisar declarar a propria versao.

O CASO MAIS FEIO ESTAVA NO ACK DE ESCALACAO. Ele nao tinha nem CONSTANTE de versao no codigo, e e'
o unico texto do Lucas que chega ao beneficiario no caminho ADVERSO — a pessoa cujo caso acabou de
ser encaminhado por inadimplencia ou pedido de cancelamento. Olhando uma mensagem que vazasse, nao
havia como dizer qual redacao a produziu.

POR QUE A CERCA COMPARA AS DUAS FONTES, e nao cada uma contra um literal (mesmo argumento do
`test_helena_dono_declarado.py`): fixar o dicionario esperado dentro do teste faria subir uma
versao exigir editar TRES lugares, e esquecer o terceiro deixaria a cerca verde sobre uma deriva.
Comparando `PROMPT_VERSIONS` com o yaml diretamente, subir a versao no codigo OBRIGA a atualizar o
yaml e nada mais — a cerca nao tem copia propria para envelhecer.

O QUE ESTE ARQUIVO NAO FAZ: declarar dono no lugar de alguem. `reports_to: "atendimento@amh"` e'
endereco de TIME, e a Frente 6 e' explicita sobre isso nao responder as duas perguntas que importam
— quem decide se o Lucas pode dizer determinada frase, e quem atende quando ele erra com um
beneficiario de verdade. Nomear essa pessoa e' ato do dono, nao de um agente, e por isso a cerca de
dono abaixo esta' escrita mas marcada `xfail(strict=False)`: ela vira vermelha util no dia em que
alguem preencher, e nao trava a entrega da cerca de saida hoje.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from maezo.agents.lucas.graph import PROMPT_VERSIONS

RAIZ = Path(__file__).resolve().parents[3]
AGENT_YAML = RAIZ / "spec" / "agents" / "lucas" / "agent.yaml"

#: Um dono e' uma PESSOA. Estas formas sao as que aparecem quando alguem tenta declarar um time.
_FORMAS_DE_TIME = ("@amh", "equipe", "time", "squad", "suporte", "atendimento@", "dev@", "ti@")


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load(AGENT_YAML.read_text(encoding="utf-8"))


def test_as_versoes_declaradas_sao_as_que_o_codigo_carrega(spec: dict) -> None:
    """A deriva que existia em 18/09: yaml com dois prompts, codigo com cinco."""
    assert spec["prompt_versions"] == PROMPT_VERSIONS, (
        "agent.yaml e PROMPT_VERSIONS divergiram. Este numero e' o que diz QUAL texto falou com o "
        "beneficiario; divergente, ele aponta para o texto errado. Suba a versao no codigo e "
        "atualize o yaml na MESMA entrega."
    )


def test_a_cerca_de_saida_esta_declarada_no_yaml(spec: dict) -> None:
    """Mais especifico que o teste acima e por uma razao propria: a cerca decide o que o
    beneficiario le'. Um agente que a perdesse num refactor ficaria com o yaml igualmente
    consistente se a chave simplesmente sumisse dos dois lados — este teste e' o que nomeia a
    cerca como parte do contrato, e nao so' como detalhe de implementacao."""
    assert spec["prompt_versions"].get("recusa_de_saida"), (
        "o Lucas deixou de declarar a cerca de saida. Ela foi removida de proposito? Se sim, "
        "docs/design precisa dizer o que passou a proteger o beneficiario de ser informado de "
        "uma suspensao ou cancelamento antes de um humano decidir."
    )


@pytest.mark.xfail(
    reason=(
        "PENDENTE DO DONO, nao da engenharia. O Lucas declara `reports_to: atendimento@amh`, que "
        "e' endereco de TIME — e um time nao acorda as tres da manha. Nomear a pessoa que decide "
        "o texto do Lucas e a que atende quando ele erra com um beneficiario e' ato do dono, "
        "igual ao `owners.clinico` da Helena. Este teste fica aqui em xfail para que o dia em que "
        "alguem preencher o campo o torne verde, em vez de o campo ser esquecido por nao haver "
        "nada apontando para ele."
    ),
    strict=False,
)
def test_existe_dono_tecnico_com_nome_de_pessoa(spec: dict) -> None:
    tecnico = (spec.get("owners") or {}).get("tecnico", "")
    assert tecnico, "sem dono tecnico declarado — ninguem decide se o texto do Lucas muda"
    plano = tecnico.lower()
    assert not any(f in plano for f in _FORMAS_DE_TIME), (
        f"{tecnico!r} parece um endereco de TIME. A Frente 6 e' explicita: nome de pessoa."
    )
