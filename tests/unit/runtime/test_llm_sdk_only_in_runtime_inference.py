"""Cerca estrutural da AGENTS.md regra 6: SDK de modelo so' dentro de `maezo.runtime.inference` (R-040).

DE ONDE VEM. O achado F-051 (auditoria forense 2026-10) apontou `boto3.client("bedrock-runtime")` em
`src/maezo/conhecimento/busca_semantica.py` — a chamada de EMBEDDINGS fora de `runtime.inference`. O dono
decidiu (DEC-20, opcao (a), issue #1084, 31/10/2026) mover a chamada para `runtime/inference/embeddings.py`.
Ate' entao nenhuma cerca cobria a regra: `tests/unit/ports/test_ports_purity.py` e
`tests/unit/domain/integration/test_identity.py` so' vigiam os proprios pacotes.

O QUE A CERCA RECUSA, em todo `src/maezo/**/*.py` FORA de `src/maezo/runtime/inference/` (AST, nunca grep):
1. `import X` / `from X import ...` de um SDK de modelo (`_SDKS_DE_MODELO`), em qualquer nivel (inclusive
   import tardio dentro de funcao, como era o caso do F-051);
2. o NOME DE SERVICO de invocacao de modelo da AWS (`_SERVICOS_DE_MODELO_AWS`, ex.: `"bedrock-runtime"`)
   como literal de texto em qualquer lugar do codigo — e' o que transforma o `boto3` generico (S3, KMS,
   legitimos fora daqui) em um cliente de modelo.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_RAIZ: Final[Path] = Path(__file__).resolve().parents[3]
_SRC: Final[Path] = _RAIZ / "src" / "maezo"
_PACOTE_DE_INFERENCIA: Final[Path] = _SRC / "runtime" / "inference"

#: Raizes de import de SDKs de modelo (gerativo OU de embeddings). Comparacao pelo prefixo pontuado.
_SDKS_DE_MODELO: Final[frozenset[str]] = frozenset(
    {
        "anthropic",
        "openai",
        "cohere",
        "mistralai",
        "litellm",
        "voyageai",
        "ollama",
        "vertexai",
        "google.generativeai",
        "google.genai",
        "langchain_anthropic",
        "langchain_openai",
        "langchain_aws",
        "langchain_google_genai",
        "sentence_transformers",
    }
)
#: Servicos AWS de INVOCACAO de modelo (o argumento de `boto3.client(...)`).
_SERVICOS_DE_MODELO_AWS: Final[frozenset[str]] = frozenset(
    {"bedrock", "bedrock-runtime", "bedrock-agent", "bedrock-agent-runtime", "sagemaker-runtime"}
)


def _e_sdk_de_modelo(modulo: str) -> bool:
    return any(modulo == sdk or modulo.startswith(sdk + ".") for sdk in _SDKS_DE_MODELO)


def _ofensas_em_fonte(fonte: str, rotulo: str) -> list[str]:
    ofensas: list[str] = []
    for no in ast.walk(ast.parse(fonte)):
        if isinstance(no, ast.Import):
            ofensas.extend(
                f"{rotulo}:{no.lineno}: import do SDK de modelo `{a.name}`"
                for a in no.names
                if _e_sdk_de_modelo(a.name)
            )
        elif isinstance(no, ast.ImportFrom) and no.level == 0 and no.module and _e_sdk_de_modelo(no.module):
            ofensas.append(f"{rotulo}:{no.lineno}: import do SDK de modelo `{no.module}`")
        elif isinstance(no, ast.Constant) and no.value in _SERVICOS_DE_MODELO_AWS:
            ofensas.append(f"{rotulo}:{no.lineno}: servico AWS de modelo {no.value!r} fora da inferencia")
    return ofensas


def _arquivos_fora_da_inferencia() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if _PACOTE_DE_INFERENCIA not in p.parents)


def test_nenhum_sdk_de_modelo_fora_de_runtime_inference() -> None:
    arquivos = _arquivos_fora_da_inferencia()
    assert len(arquivos) > 500, f"so' {len(arquivos)} arquivos varridos — a cerca estaria quase vazia"
    ofensas: list[str] = []
    for caminho in arquivos:
        rotulo = str(caminho.relative_to(_RAIZ))
        ofensas.extend(_ofensas_em_fonte(caminho.read_text(encoding="utf-8"), rotulo))
    assert not ofensas, "AGENTS.md regra 6 (SDK de modelo so' em runtime.inference):\n" + "\n".join(ofensas)


def test_cerca_recusa_o_padrao_exato_do_f051() -> None:
    """Negativo: o trecho de `busca_semantica.py` anterior a R-040 (import tardio + `bedrock-runtime`)."""
    fonte = (
        "class TransporteBedrockRegional:\n"
        "    def __init__(self):\n"
        "        import boto3\n"
        "        self._client = boto3.client('bedrock-runtime', region_name='sa-east-1')\n"
    )
    ofensas = _ofensas_em_fonte(fonte, "sintetico.py")
    assert ofensas == ["sintetico.py:4: servico AWS de modelo 'bedrock-runtime' fora da inferencia"]


def test_cerca_recusa_import_de_sdk_gerativo_e_aceita_boto3_generico() -> None:
    assert len(_ofensas_em_fonte("import anthropic\nfrom openai import OpenAI\n", "s.py")) == 2
    assert len(_ofensas_em_fonte("from anthropic.types import Message\n", "s.py")) == 1
    assert len(_ofensas_em_fonte("import google.genai as g\n", "s.py")) == 1
    # boto3 para S3/KMS e nomes parecidos nao sao SDK de modelo.
    assert _ofensas_em_fonte("import boto3\nboto3.client('s3')\nimport anthropicx\n", "s.py") == []
    assert _ofensas_em_fonte("PROVEDOR = 'bedrock_br'\n", "s.py") == []


def test_o_pacote_de_inferencia_contem_o_que_a_cerca_procura() -> None:
    """Nao-vacuidade: a mesma varredura, aplicada ao pacote isento, acha o SDK e o servico de embeddings —
    a cerca enxerga o padrao; so' a isencao do pacote o permite."""
    ofensas = _ofensas_em_fonte((_PACOTE_DE_INFERENCIA / "embeddings.py").read_text(encoding="utf-8"), "e.py")
    assert any("'bedrock-runtime'" in o for o in ofensas)
    todas = [
        o
        for p in sorted(_PACOTE_DE_INFERENCIA.rglob("*.py"))
        for o in _ofensas_em_fonte(p.read_text(encoding="utf-8"), p.name)
    ]
    assert any("`anthropic`" in o for o in todas)
