"""Pin de versao e de hash dos prompts e das cercas da Helena: mudar o TEXTO sem subir a VERSAO quebra o CI.

POR QUE ISTO EXISTE. A convencao do repositorio e' "o texto muda, a versao sobe no MESMO commit"
(`test_helena_dono_declarado.py` compara as constantes com `agent.yaml`). Mas nada comparava a versao
com o TEXTO: o #577 (DL-0052) mudou o `classify_prompt` e deixou `classify-v5`, e os commits de
01/10/2026 das cercas de finalidade e de privacidade mudaram veredito de texto e deixaram
`recusa-v8`. O numero que vai para o log e para o contador dizia uma coisa e o texto valia outra.

COMO USAR. Se este teste caiu, voce mudou um prompt ou uma cerca. (1) Suba a versao em `prompts.py` e
em `spec/agents/helena/agent.yaml`; (2) acrescente a linha da DL em `docs/decisions-log.md`; (3) so'
entao atualize o par `(versao, sha256)` abaixo. O hash e' do texto que VAI ao modelo, nao do arquivo:
reformatar o fonte nao o muda.

O plano do Lucas (`docs/plans/lucas-numero-unico.md` §4) pede "desligado = byte a byte o de hoje, com o
sha256 do prompt fixado em teste": este e' esse pin, para o `classify`, e ele passa a existir antes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from maezo.agents.helena import prompts

#: `(versao, sha256 do texto)` do que o modelo recebe. `SYSTEM_PROMPT` e' parte dos outros tres.
PROMPTS_PINADOS: dict[str, tuple[str, str]] = {
    "system": ("system-v1", "16660021eca9f853c45a191286a3e7a0f3c96ea985333bc6f008748306f958c1"),
    "classify": ("classify-v5.2", "2f8eb9ca08166d3266aa59c0c14b9e1e1b6f20715208fcca23552d86f105e144"),
    "response": ("response-v10", "2af3650c30b558f3d68c65af3e9d260ebdd29e17fab3fad7167f7a82bfd5d7f8"),
    "classify_roteador": (
        "classify-v6.1",
        "b04a2c79e53e8bb1c102c1a147e8bbae2d5ac30fca8f13751dff17ae7afbcb67",
    ),
    "coleta": ("coleta-v1", "a825d71380d522a6cbfc89720739cd1712fe93ea197fc4bc2c86bddb07c73665"),
}
#: `(versao, sha256)` das listas e padroes das cercas de saida (o que decide se um texto sai).
CERCAS_PINADAS: tuple[str, str] = (
    "recusa-v9",
    "4dca770f45587d8c88c901d15bc98113e74f46be0a1169542bcb90503ae44522",
)


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _texto_atual() -> dict[str, tuple[str, str]]:
    return {
        "system": (prompts.SYSTEM_PROMPT_VERSION, prompts.SYSTEM_PROMPT),
        "classify": (prompts.CLASSIFY_PROMPT_VERSION, prompts.classify_prompt()),
        "classify_roteador": (
            prompts.CLASSIFY_PROMPT_VERSION_ROTEADOR,
            prompts.classify_prompt(roteador_lucas=True),
        ),
        "response": (prompts.RESPONSE_PROMPT_VERSION, prompts.response_prompt()),
        "coleta": (prompts.COLETA_PROMPT_VERSION, prompts.coleta_prompt()),
    }


def _canonico(valor: Any) -> Any:
    if hasattr(valor, "pattern"):
        return valor.pattern
    if isinstance(valor, tuple):
        return [_canonico(item) for item in valor]
    return valor


def hash_das_cercas() -> str:
    """Hash canonico das listas e dos padroes que decidem se um texto sai."""
    partes = {
        "negativa_clinica": prompts.NEGATIVA_CLINICA_PROIBIDA,
        "promessa_de_humano": prompts.PROMESSA_DE_HUMANO_PROIBIDA,
        "promessa_de_capacidade": prompts.PROMESSA_DE_CAPACIDADE_PROIBIDA,
        "mencao_de_encaminhamento": prompts.MENCAO_DE_ENCAMINHAMENTO_OBRIGATORIA,
        "canal_nao_confirmado": prompts.CANAL_NAO_CONFIRMADO_PROIBIDO,
        "canal_termo_que_exige_o_nome": prompts.CANAL_TERMO_QUE_EXIGE_O_NOME,
        "finalidade_canal": prompts._CANAL_CITADO,
        "finalidade_assunto": prompts._ASSUNTO_QUE_O_APP_NAO_RESOLVE,
    }
    return _sha(json.dumps({k: _canonico(v) for k, v in partes.items()}, ensure_ascii=False, sort_keys=True))


@pytest.mark.parametrize("nome", sorted(PROMPTS_PINADOS))
def test_o_texto_do_prompt_so_muda_junto_com_a_versao(nome: str) -> None:
    versao_atual, texto = _texto_atual()[nome]
    versao_pinada, hash_pinado = PROMPTS_PINADOS[nome]

    assert _sha(texto) == hash_pinado or versao_atual != versao_pinada, (
        f"o texto do prompt {nome!r} mudou e a versao continua {versao_atual!r}: suba a versao em "
        "`prompts.py` e em `agent.yaml` no MESMO commit (e a linha da DL), depois atualize este pin"
    )
    assert (versao_atual, _sha(texto)) == (versao_pinada, hash_pinado), (
        f"o pin de {nome!r} esta' desatualizado: versao {versao_atual!r}, sha256 {_sha(texto)}"
    )


def test_as_cercas_so_mudam_junto_com_a_versao_da_recusa() -> None:
    versao_pinada, hash_pinado = CERCAS_PINADAS

    assert hash_das_cercas() == hash_pinado or versao_pinada != prompts.RECUSA_DE_SAIDA_VERSION, (
        f"uma lista ou um padrao das cercas mudou e a versao continua {prompts.RECUSA_DE_SAIDA_VERSION!r}: "
        "`RECUSA_DE_SAIDA_VERSION` sobe junto com qualquer alteracao nos padroes (e `agent.yaml` com ela)"
    )
    assert (prompts.RECUSA_DE_SAIDA_VERSION, hash_das_cercas()) == (versao_pinada, hash_pinado), (
        f"o pin das cercas esta' desatualizado: versao {prompts.RECUSA_DE_SAIDA_VERSION!r}, "
        f"sha256 {hash_das_cercas()}"
    )


def test_o_hash_e_do_texto_que_vai_ao_modelo_e_nao_do_fonte() -> None:
    """Reformatar o `.py` nao muda o hash: ele e' calculado sobre a string que a funcao devolve."""
    assert _sha(prompts.classify_prompt()) == _sha(prompts.classify_prompt())
    assert len(PROMPTS_PINADOS["classify"][1]) == 64
