"""E-SF-32r — fontes normativas, variante (a): fonte = Diário Oficial, estado fechado afirmado.

O que esta linha NÃO faz: não acrescenta texto de norma a `docs/compliance/legal/textos-base/`, não
reancora o cancelamento (DMN, BPMN e contrato de SP-OP-CANCEL-001 ficam como estão) e não toca `src/`
nem `spec/`. O que ela fixa, em teste:

1. Todo texto-base de norma aponta para o Diário Oficial (`in.gov.br`) no cabeçalho, exceto uma lista
   EXATA de exceções declaradas como GAP. Hoje a única é a MP 2.177-44/2001, ancorada no Senado: o texto
   do DOU dela não está pinado.
2. O conjunto de textos-base de norma é exatamente o de hoje, e todo arquivo da pasta consta do
   `MANIFEST.sha256` com o hash certo: nenhum texto novo entra sem esta lista mudar no mesmo PR.
3. Não existe texto-base da RN 593: ela não é fonte pinada.
4. As citações de RN 593 e RN 412 nos três artefatos do cancelamento são dívida conhecida e exata:
   cada linha é pinada pelo sha256 do texto da linha (sem copiar o conteúdo). Linha nova que cite uma
   delas falha; linha pinada que deixe de existir também falha, para a lista só encolher.

Cada detector tem autoteste sobre entrada sintética violadora.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_LEGAL = _REPO / "docs/compliance/legal"
_TEXTOS_BASE = _LEGAL / "textos-base"
_MANIFESTO = _LEGAL / "MANIFEST.sha256"

#: Host do Diário Oficial da União (matéria, leitura do jornal e pesquisa).
_HOST_DOU = re.compile(r"^(?:[a-z0-9-]+\.)*in\.gov\.br$")
_URL = re.compile(r"https?://([^/\s)`>\]]+)")

#: Exceções EXATAS à regra "fonte = DOU" (arquivo -> motivo). Só encolhe.
_EXCECOES_DOU: Mapping[str, str] = {
    "MP-2177-44-2001.md": "GAP: âncora no Senado; texto do DOU não pinado",
}

#: Textos-base de norma que existem hoje. Texto novo só entra mudando esta lista no mesmo PR.
_NORMAS_PINADAS: frozenset[str] = frozenset(
    {
        "MP-2177-44-2001.md",
        "RN-195-2009.md",
        "RN-413-2016.md",
        "RN-424-2017.md",
        "RN-489-2022.md",
        "RN-500-2022.md",
        "RN-515-2022.md",
        "RN-528-2022.md",
        "RN-529-2022.md",
        "RN-557-2022.md",
        "RN-558-2022.md",
        "RN-561-2022.md",
        "RN-566-2022.md",
        "RN-623-2024.md",
        "RN-659-2025.md",
    }
)

#: Citação de RN 593 ou RN 412, nas grafias usadas no repositório.
_RN_593_OU_412 = re.compile(r"(?i)\b(?:RN|Resolu[cç][aã]o\s+Normativa)\b[^0-9\n]{0,12}(?:593|412)\b")

#: Dívida conhecida do cancelamento: arquivo -> sha256 (16 hex) de cada linha citante, sem espaços
#: das pontas. Gerado lendo os três arquivos na main 7ae9092b; o conteúdo das linhas não é copiado.
_DIVIDA_DO_CANCELAMENTO: Mapping[str, frozenset[str]] = {
    "spec/processes/dmn/cancel_sla.dmn": frozenset(
        {
            "a131cc5dca6ce17e",
            "6b5048a7a833d99e",
            "c202990b5e4679de",
            "cad416e2aca39236",
            "e8edf54379b078ee",
            "e364b9b745c93217",
            "8eca6de73b903966",
        }
    ),
    "spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn": frozenset(
        {
            "e4e2f110257bb362",
            "eeefb443de59a5fd",
            "c60e9a834395f4b4",
            "39d00596b85015a1",
            "8585970f5988dd9a",
            "edf119c5155ec2be",
            "1a2afae9181d15fc",
            "a52986c5f2739527",
            "157958104b5a7fb6",
            "7ac2be37f7fa98d5",
            "9723ee1db941b264",
            "25f9b4e97689497e",
            "d9f0d1293109b92c",
            "e99422561f32b5ba",
            "ea93655306a043fd",
            "f9fde6315d62ab37",
        }
    ),
    "docs/processes/contracts/SP-OP-CANCEL-001.md": frozenset(
        {
            "98e256e65f1f7a20",
            "b03f9a2341fbfe85",
            "a13fb0daf2bfc2be",
            "713a96c64634db35",
            "4bc77dfc184a67e3",
            "d116184a1cf3d0b4",
            "85ac6795642b929b",
            "a4acc63f1986964e",
            "ab64da326e1b404f",
            "96369413483fa9d8",
            "af25902ab90a08d7",
            "94cced934e0d33d4",
            "ea130def2403a295",
            "a2f2e78302e8722f",
            "973edf3da5197a8e",
            "261cc7b4cf7b4af8",
            "bbe8dd8dbe32a06a",
        }
    ),
}


# --------------------------------------------------------------------------- detectores


def _cabecalho(texto: str) -> str:
    """Texto antes do primeiro separador `---` (o arquivo todo se não houver)."""
    linhas: list[str] = []
    for linha in texto.splitlines():
        if linha.strip() == "---":
            break
        linhas.append(linha)
    return "\n".join(linhas)


def _sem_fonte_dou(arquivos: Mapping[str, str]) -> list[str]:
    """Nomes de arquivo cujo cabeçalho não tem nenhuma URL do Diário Oficial."""
    return sorted(
        nome
        for nome, texto in arquivos.items()
        if not any(_HOST_DOU.match(host.lower()) for host in _URL.findall(_cabecalho(texto)))
    )


def _normas_em(pasta: Path) -> dict[str, str]:
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(pasta.iterdir())
        if p.is_file() and re.match(r"^(RN|MP)-", p.name)
    }


def _divergencias_do_manifesto(pasta: Path, manifesto: str) -> list[str]:
    """Arquivos da pasta fora do manifesto ou com sha256 diferente do registrado."""
    registrados: dict[str, str] = {}
    for linha in manifesto.splitlines():
        partes = linha.split()
        if len(partes) == 2:
            registrados[Path(partes[1].lstrip("*")).as_posix()] = partes[0]
    problemas: list[str] = []
    prefixo = pasta.name
    for p in sorted(pasta.iterdir()):
        if not p.is_file():
            continue
        chave = f"{prefixo}/{p.name}"
        esperado = registrados.get(chave)
        if esperado is None:
            problemas.append(f"{chave}: fora do manifesto")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != esperado:
            problemas.append(f"{chave}: sha256 diverge")
    return problemas


def _textos_da_rn_593(nomes: Iterable[str]) -> list[str]:
    return sorted(n for n in nomes if re.search(r"(?i)\bRN[-_ ]?593\b", n.replace(".", " ")))


def _hash_da_linha(linha: str) -> str:
    return hashlib.sha256(linha.strip().encode("utf-8")).hexdigest()[:16]


def _citacoes_593_412(texto: str) -> frozenset[str]:
    return frozenset(_hash_da_linha(linha) for linha in texto.splitlines() if _RN_593_OU_412.search(linha))


def _divergencias_da_divida(
    atual: Mapping[str, frozenset[str]], pinada: Mapping[str, frozenset[str]]
) -> list[str]:
    problemas: list[str] = []
    for arquivo in sorted(set(atual) | set(pinada)):
        novas = atual.get(arquivo, frozenset()) - pinada.get(arquivo, frozenset())
        sumidas = pinada.get(arquivo, frozenset()) - atual.get(arquivo, frozenset())
        problemas += [f"{arquivo}: citação nova {h}" for h in sorted(novas)]
        problemas += [f"{arquivo}: linha pinada sumiu {h} (encolha a lista)" for h in sorted(sumidas)]
    return problemas


# --------------------------------------------------------------------------- autotestes


def test_detector_de_fonte_dou_acusa_cabecalho_sem_dou() -> None:
    arquivos = {
        "RN-1.md": "# t\n**URL:** https://www.in.gov.br/web/dou/-/x\n---\ncorpo",
        "RN-2.md": "# t\n**URL:** https://pesquisa.in.gov.br/imprensa/x\n",
        "RN-3.md": "# t\n**URL:** https://legis.senado.leg.br/norma/1\n---\nhttps://www.in.gov.br/só-no-corpo",
        "RN-4.md": "# t\n**URL:** https://in.gov.br.exemplo.com/x\n",
        "RN-5.md": "# t sem URL",
    }
    assert _sem_fonte_dou(arquivos) == ["RN-3.md", "RN-4.md", "RN-5.md"]


def test_detector_de_manifesto_acusa_arquivo_fora_ou_alterado(tmp_path: Path) -> None:
    pasta = tmp_path / "textos-base"
    pasta.mkdir()
    (pasta / "RN-1.md").write_text("a", encoding="utf-8")
    (pasta / "RN-2.md").write_text("b", encoding="utf-8")
    (pasta / "RN-3.md").write_text("c", encoding="utf-8")
    sha_a = hashlib.sha256(b"a").hexdigest()
    manifesto = f"{sha_a}  textos-base/RN-1.md\n{'0' * 64}  textos-base/RN-2.md\n"
    assert _divergencias_do_manifesto(pasta, manifesto) == [
        "textos-base/RN-2.md: sha256 diverge",
        "textos-base/RN-3.md: fora do manifesto",
    ]


def test_detector_da_rn_593_acusa_arquivo() -> None:
    assert _textos_da_rn_593(["RN-593-2023.md", "rn_593.md", "RN-5930-2023.md", "RN-561-2022.md"]) == [
        "RN-593-2023.md",
        "rn_593.md",
    ]


def test_detector_de_citacao_593_412_casa_as_grafias() -> None:
    for linha in ("RN 593/2023", "RN nº 412", "Resolução Normativa nº 593", "RN-412/2016", "(RN ANS 593)"):
        assert _RN_593_OU_412.search(linha), linha
    for linha in ("RN 561/2022", "RN 5930", "ARN 593", "art. 412 do CC"):
        assert not _RN_593_OU_412.search(linha), linha


def test_detector_da_divida_acusa_linha_nova_e_linha_sumida() -> None:
    pinada = {"x.md": frozenset({_hash_da_linha("RN 593 velha"), _hash_da_linha("RN 412 velha")})}
    atual = {"x.md": _citacoes_593_412("RN 593 velha\nRN 593 nova\noutra linha\n")}
    problemas = _divergencias_da_divida(atual, pinada)
    assert len(problemas) == 2
    assert any("citação nova" in p for p in problemas)
    assert any("sumiu" in p for p in problemas)
    assert _divergencias_da_divida(pinada, pinada) == []


# --------------------------------------------------------------------------- estado fechado


def test_textos_base_de_norma_sao_exatamente_os_pinados() -> None:
    assert set(_normas_em(_TEXTOS_BASE)) == _NORMAS_PINADAS


def test_todo_texto_base_de_norma_aponta_para_o_dou_salvo_excecao_exata() -> None:
    assert _sem_fonte_dou(_normas_em(_TEXTOS_BASE)) == sorted(_EXCECOES_DOU)


def test_excecoes_ao_dou_existem_e_sao_gap() -> None:
    for nome, motivo in _EXCECOES_DOU.items():
        assert (_TEXTOS_BASE / nome).is_file(), nome
        assert motivo.startswith("GAP:"), nome


def test_todo_arquivo_dos_textos_base_consta_do_manifesto() -> None:
    assert _divergencias_do_manifesto(_TEXTOS_BASE, _MANIFESTO.read_text(encoding="utf-8")) == []


def test_nao_ha_texto_base_da_rn_593() -> None:
    assert _textos_da_rn_593(p.name for p in _TEXTOS_BASE.iterdir()) == []


def test_citacoes_de_rn_593_e_412_no_cancelamento_sao_a_divida_pinada() -> None:
    atual = {
        arquivo: _citacoes_593_412((_REPO / arquivo).read_text(encoding="utf-8"))
        for arquivo in _DIVIDA_DO_CANCELAMENTO
    }
    assert _divergencias_da_divida(atual, _DIVIDA_DO_CANCELAMENTO) == []
