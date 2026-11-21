"""E-SF-32r — fontes normativas, variante (a): fonte = Diário Oficial, estado fechado afirmado.

O que esta linha NÃO faz: não acrescenta texto de norma a `docs/compliance/legal/textos-base/`, não
reancora o cancelamento (DMN, BPMN e contrato de SP-OP-CANCEL-001 ficam como estão) e não toca `src/`
nem `spec/`. O que ela fixa, em teste:

1. A listagem de `textos-base/` é exatamente a de hoje (todo nome de arquivo, inclusive o que não é
   norma, declarado como tal) e a pasta não tem subpasta. Conjunto de normas = listagem menos as
   não-normas declaradas; todo arquivo consta do `MANIFEST.sha256` com o hash certo.
2. Todo texto-base de norma, qualquer que seja o nome do arquivo, aponta para o Diário Oficial
   (`in.gov.br`) no cabeçalho, exceto uma lista EXATA de exceções declaradas como GAP, cujo próprio
   cabeçalho também declara o GAP. Hoje a única é a MP 2.177-44/2001, ancorada no Senado.
3. Não existe texto-base da RN 593: nenhum nome de arquivo (em qualquer nível) a cita, e a citação
   dela em cabeçalho é dívida exata (hoje só a remissão do cabeçalho da RN 561).
4. As citações de RN 593 e RN 412 nos três artefatos do cancelamento são dívida conhecida e exata:
   cada linha é pinada, como multiconjunto, pelo sha256 do texto da linha (sem copiar o conteúdo).
   Linha nova que cite uma delas falha (inclusive cópia idêntica de linha pinada); linha pinada que
   deixe de existir também falha, para a lista só encolher.

Regra de citação (robusta de propósito): cita a linha que contém 593 ou 412 como número isolado
(sem dígito colado), depois de neutralizar sequências hexadecimais (hashes). Citação partida em duas
linhas ("RN" no fim de uma, o número no começo da outra) marca o par.

Cada detector tem autoteste sobre entrada sintética violadora.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
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

#: Arquivos de `textos-base/` que NÃO são texto de norma (arquivo -> motivo). Só encolhe.
_NAO_NORMAS: Mapping[str, str] = {
    "PARECER-OP21-2026-10-26-LUCAS.txt": "parecer jurídico (instantâneo), não texto de norma",
}

#: Listagem COMPLETA do primeiro nível de `textos-base/` hoje. Arquivo novo só entra mudando esta lista.
_LISTAGEM_PINADA: frozenset[str] = frozenset(
    {
        "MP-2177-44-2001.md",
        "PARECER-OP21-2026-10-26-LUCAS.txt",
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

#: Sequência hexadecimal com ao menos uma letra (hash, inteiro ou abreviado): não é citação.
_HEXA = re.compile(r"(?i)(?<![0-9a-z])(?=[0-9]*[a-f])[0-9a-f]{6,}(?![0-9a-z])")

#: Marcador de norma no fim da linha, para a citação partida em duas linhas.
_MARCADOR_NO_FIM = re.compile(r"(?i)(?<![a-z])(?:RNs?|Resolu[cç][aã]o|Normativa)[\s_.-]*$")

#: Citações de RN 593 em cabeçalho de texto-base: arquivo -> sha256 (16 hex) de cada linha citante.
#: Hoje só a remissão do cabeçalho da RN 561 (cruzamento RN 561 × RN 412/593). Só encolhe.
_CABECALHOS_CITANDO_593: Mapping[str, tuple[str, ...]] = {
    "RN-561-2022.md": ("a1a99f6c42fe799c",),
}

#: Dívida conhecida do cancelamento: arquivo -> sha256 (16 hex) de cada linha citante, sem espaços
#: das pontas, como multiconjunto (hash repetido = linha repetida). Gerado lendo os três arquivos na
#: main 7ae9092b; regenerado com a regra robusta em 5b8d1ea4 (mesmas linhas, nenhuma repetida).
_DIVIDA_DO_CANCELAMENTO: Mapping[str, tuple[str, ...]] = {
    "spec/processes/dmn/cancel_sla.dmn": (
        "a131cc5dca6ce17e",
        "6b5048a7a833d99e",
        "c202990b5e4679de",
        "cad416e2aca39236",
        "e8edf54379b078ee",
        "e364b9b745c93217",
        "8eca6de73b903966",
    ),
    "spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn": (
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
    ),
    "docs/processes/contracts/SP-OP-CANCEL-001.md": (
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
    ),
}


# --------------------------------------------------------------------------- detectores


def _numero_isolado(numeros: Iterable[str]) -> re.Pattern[str]:
    return re.compile(r"(?<!\d)(?:" + "|".join(numeros) + r")(?!\d)")


_CITA_593_OU_412 = _numero_isolado(("593", "412"))
_CITA_593 = _numero_isolado(("593",))


def _cita(linha: str, regra: re.Pattern[str]) -> bool:
    return bool(regra.search(_HEXA.sub(" ", linha)))


def _linhas_citantes(texto: str, regra: re.Pattern[str]) -> list[str]:
    """Linhas que citam; na citação partida, a linha anterior terminada em marcador entra junto."""
    linhas = texto.splitlines()
    marcadas: set[int] = set()
    for i, linha in enumerate(linhas):
        if _cita(linha, regra):
            marcadas.add(i)
            if i > 0 and _MARCADOR_NO_FIM.search(linhas[i - 1]):
                marcadas.add(i - 1)
    return [linhas[i] for i in sorted(marcadas)]


def _hash_da_linha(linha: str) -> str:
    return hashlib.sha256(linha.strip().encode("utf-8")).hexdigest()[:16]


def _citacoes(texto: str, regra: re.Pattern[str] = _CITA_593_OU_412) -> Counter[str]:
    return Counter(_hash_da_linha(linha) for linha in _linhas_citantes(texto, regra))


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


def _divergencias_da_listagem(pasta: Path, pinada: frozenset[str]) -> list[str]:
    """Subpastas (proibidas) e arquivos do primeiro nível fora da listagem pinada ou sumidos."""
    problemas = [f"{p.name}: subpasta proibida" for p in sorted(pasta.iterdir()) if not p.is_file()]
    arquivos = {p.name for p in pasta.iterdir() if p.is_file()}
    problemas += [f"{n}: fora da listagem pinada" for n in sorted(arquivos - pinada)]
    problemas += [f"{n}: sumiu da pasta (encolha a listagem)" for n in sorted(pinada - arquivos)]
    return problemas


def _normas_em(pasta: Path, nao_normas: Iterable[str]) -> dict[str, str]:
    """Todo arquivo do primeiro nível, qualquer que seja o nome, menos as não-normas declaradas."""
    excluidos = set(nao_normas)
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(pasta.iterdir())
        if p.is_file() and p.name not in excluidos
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


def _nome_cita_rn_593(nome: str) -> bool:
    return bool(re.search(r"(?i)\bRN[-_ ]?593\b", nome.replace(".", " "))) or _cita(nome, _CITA_593)


def _textos_da_rn_593(pasta: Path) -> list[str]:
    """Caminhos (em qualquer nível) cujo nome de arquivo ou de pasta cita a RN 593."""
    return sorted(
        p.relative_to(pasta).as_posix()
        for p in pasta.rglob("*")
        if any(_nome_cita_rn_593(parte) for parte in p.relative_to(pasta).parts)
    )


def _cabecalhos_citando_593(pasta: Path) -> dict[str, Counter[str]]:
    """Arquivo (em qualquer nível) -> multiconjunto das linhas do cabeçalho que citam a RN 593."""
    achados: dict[str, Counter[str]] = {}
    for p in sorted(pasta.rglob("*")):
        if p.is_file():
            citacoes = _citacoes(_cabecalho(p.read_text(encoding="utf-8", errors="replace")), _CITA_593)
            if citacoes:
                achados[p.relative_to(pasta).as_posix()] = citacoes
    return achados


def _divergencias_da_divida(
    atual: Mapping[str, Counter[str]], pinada: Mapping[str, Iterable[str]]
) -> list[str]:
    problemas: list[str] = []
    for arquivo in sorted(set(atual) | set(pinada)):
        esperado = Counter(pinada.get(arquivo, ()))
        visto = atual.get(arquivo, Counter())
        novas = visto - esperado
        sumidas = esperado - visto
        problemas += [f"{arquivo}: citação nova {h} (×{n})" for h, n in sorted(novas.items())]
        problemas += [
            f"{arquivo}: linha pinada sumiu {h} (×{n}; encolha a lista)" for h, n in sorted(sumidas.items())
        ]
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


def _pasta_sintetica(tmp_path: Path) -> Path:
    pasta = tmp_path / "textos-base"
    pasta.mkdir()
    (pasta / "RN-1-2020.md").write_text("# t\n**URL:** https://www.in.gov.br/x\n---\ncorpo", encoding="utf-8")
    (pasta / "PARECER.txt").write_text("parecer sem URL", encoding="utf-8")
    return pasta


def test_detector_de_listagem_acusa_norma_nova_de_qualquer_nome_e_subpasta(tmp_path: Path) -> None:
    pasta = _pasta_sintetica(tmp_path)
    pinada = frozenset({"RN-1-2020.md", "PARECER.txt"})
    assert _divergencias_da_listagem(pasta, pinada) == []
    assert set(_normas_em(pasta, {"PARECER.txt"})) == {"RN-1-2020.md"}
    (pasta / "LEI-9656-1998.md").write_text(
        "# Lei\n**URL:** https://www.planalto.gov.br/x\n", encoding="utf-8"
    )
    (pasta / "extra").mkdir()
    (pasta / "extra" / "RN-593-2023.md").write_text("# t", encoding="utf-8")
    assert _divergencias_da_listagem(pasta, pinada) == [
        "extra: subpasta proibida",
        "LEI-9656-1998.md: fora da listagem pinada",
    ]
    normas = _normas_em(pasta, {"PARECER.txt"})
    assert set(normas) == {"RN-1-2020.md", "LEI-9656-1998.md"}
    assert _sem_fonte_dou(normas) == ["LEI-9656-1998.md"]
    (pasta / "RN-1-2020.md").unlink()
    assert "RN-1-2020.md: sumiu da pasta (encolha a listagem)" in _divergencias_da_listagem(pasta, pinada)


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


def test_detector_da_rn_593_acusa_nome_em_qualquer_grafia_e_nivel(tmp_path: Path) -> None:
    pasta = _pasta_sintetica(tmp_path)
    assert _textos_da_rn_593(pasta) == []
    for nome in ("RN-593-2023.md", "rn_593.md", "Resolucao-Normativa-593-2023.md"):
        (pasta / nome).write_text("# t", encoding="utf-8")
    (pasta / "RN-5930-2023.md").write_text("# t", encoding="utf-8")
    (pasta / "extra").mkdir()
    (pasta / "extra" / "RN-593-2023.md").write_text("# t", encoding="utf-8")
    assert _textos_da_rn_593(pasta) == [
        "RN-593-2023.md",
        "Resolucao-Normativa-593-2023.md",
        "extra/RN-593-2023.md",
        "rn_593.md",
    ]


def test_detector_de_cabecalho_acusa_citacao_da_rn_593(tmp_path: Path) -> None:
    pasta = _pasta_sintetica(tmp_path)
    assert _cabecalhos_citando_593(pasta) == {}
    (pasta / "RN-999-2024.md").write_text(
        "# t\n**Norma:** altera a Resolução Normativa nº 593\n---\ncorpo cita RN 593 à vontade",
        encoding="utf-8",
    )
    (pasta / "RN-998-2024.md").write_text("# t\nsha256 `0a593bc4…25d3` e RN 5930\n---\n", encoding="utf-8")
    achados = _cabecalhos_citando_593(pasta)
    assert set(achados) == {"RN-999-2024.md"}
    assert sum(achados["RN-999-2024.md"].values()) == 1
    assert _divergencias_da_divida(achados, {}) != []


def test_detector_de_citacao_593_412_casa_as_grafias() -> None:
    for linha in (
        "RN 593/2023",
        "RN nº 412",
        "Resolução Normativa nº 593",
        "RN-412/2016",
        "(RN ANS 593)",
        "RN412",
        "rn593",
        "RN_593",
        "RNs 593 e 412",
        "RN 195/2009 e 412/2016",
        "RN n&#186; 593",
        "Resolução Normativa da Agência Nacional nº 593",
        'id="rn593_x"',
    ):
        assert _cita(linha, _CITA_593_OU_412), linha
    for linha in ("RN 561/2022", "RN 5930", "15930", "sha256 `a1b2c3593d4e5f6a`"):
        assert not _cita(linha, _CITA_593_OU_412), linha


def test_detector_de_citacao_partida_marca_o_par() -> None:
    for texto in ("antes\nRN\n593/2023\ndepois", "antes\nResolução\nNormativa nº 412\ndepois"):
        linhas = texto.splitlines()
        assert _linhas_citantes(texto, _CITA_593_OU_412) == linhas[1:3], texto
    assert _linhas_citantes("RN 561\nsem número", _CITA_593_OU_412) == []


def test_detector_da_divida_acusa_linha_nova_repetida_e_sumida() -> None:
    pinada = {"x.md": (_hash_da_linha("RN 593 velha"), _hash_da_linha("RN 412 velha"))}
    assert _divergencias_da_divida({"x.md": _citacoes("RN 593 velha\nRN 412 velha\n")}, pinada) == []
    problemas = _divergencias_da_divida(
        {"x.md": _citacoes("RN 593 velha\nRN 593 nova\noutra linha\n")}, pinada
    )
    assert len(problemas) == 2
    assert any("citação nova" in p for p in problemas)
    assert any("sumiu" in p for p in problemas)
    repetida = _divergencias_da_divida(
        {"x.md": _citacoes("RN 593 velha\nRN 412 velha\n  RN 593 velha\n")}, pinada
    )
    assert repetida == [f"x.md: citação nova {_hash_da_linha('RN 593 velha')} (×1)"]
    partida = _divergencias_da_divida(
        {"x.md": _citacoes("RN 593 velha\nRN 412 velha\nRN\n593/2023\n")}, pinada
    )
    assert len(partida) == 2


# --------------------------------------------------------------------------- estado fechado


def test_listagem_dos_textos_base_e_exatamente_a_pinada_e_sem_subpasta() -> None:
    assert _divergencias_da_listagem(_TEXTOS_BASE, _LISTAGEM_PINADA) == []


def test_nao_normas_e_excecoes_estao_na_listagem() -> None:
    assert set(_NAO_NORMAS) <= _LISTAGEM_PINADA
    assert set(_EXCECOES_DOU) <= _LISTAGEM_PINADA - set(_NAO_NORMAS)


def test_todo_texto_base_de_norma_aponta_para_o_dou_salvo_excecao_exata() -> None:
    assert _sem_fonte_dou(_normas_em(_TEXTOS_BASE, _NAO_NORMAS)) == sorted(_EXCECOES_DOU)


def test_excecoes_ao_dou_existem_e_declaram_gap_no_proprio_cabecalho() -> None:
    for nome, motivo in _EXCECOES_DOU.items():
        assert (_TEXTOS_BASE / nome).is_file(), nome
        assert motivo.startswith("GAP:"), nome
        assert re.search(r"\bGAP\b", _cabecalho((_TEXTOS_BASE / nome).read_text(encoding="utf-8"))), nome


def test_todo_arquivo_dos_textos_base_consta_do_manifesto() -> None:
    assert _divergencias_do_manifesto(_TEXTOS_BASE, _MANIFESTO.read_text(encoding="utf-8")) == []


def test_nao_ha_texto_base_da_rn_593() -> None:
    assert _textos_da_rn_593(_TEXTOS_BASE) == []


def test_citacoes_de_rn_593_em_cabecalho_sao_a_divida_pinada() -> None:
    assert _divergencias_da_divida(_cabecalhos_citando_593(_TEXTOS_BASE), _CABECALHOS_CITANDO_593) == []


def test_citacoes_de_rn_593_e_412_no_cancelamento_sao_a_divida_pinada() -> None:
    atual = {
        arquivo: _citacoes((_REPO / arquivo).read_text(encoding="utf-8"))
        for arquivo in _DIVIDA_DO_CANCELAMENTO
    }
    assert _divergencias_da_divida(atual, _DIVIDA_DO_CANCELAMENTO) == []
