"""Testes unitarios do verificador de drift dos /32 do Cognito (`scripts/ops/cognito_egress_check.py`).

Quatro camadas, todas SEM rede — a resolucao DNS real e' trabalho do operador, nao do teste:

1. **Parser** do bloco `https_egress_ipv4_cidrs` sobre fixtures sinteticos: comentarios `//` e
   `#`, bloco alheio no mesmo arquivo, entradas na linha do fechamento, bloco ausente/aberto.
2. **Classificacao pura**: MISSING/STALE com ordem numerica de IP, filtro de IP privativo e a
   leitura de `ips.txt` (`--from-file`).
3. **Arvore real**: o tfvars SHIPPED entrega os pins publicos do Cognito (subset dos 13 do
   runbook) e as ENIs 10.x FORA da comparacao — prova de que o checker le o bloco certo.
4. **CLI**: sai 1 com remedicao citando o runbook + as linhas exatas, 0 no exato/so-STALE e
   2 em erro operacional — sempre via `--from-file`, nunca tocando DNS.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.ops.cognito_egress_check import (
    RUNBOOK,
    carregar_ips_medidos,
    classifica,
    filtra_publicos,
    main,
    parse_https_egress_cidrs,
    remedicao,
    separa_cidrs,
)

# tests/unit/ops/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TFVARS_REAL = _REPO_ROOT / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1" / "portal.auto.tfvars"

# Os 6 /32 que o runbook (portal-dev-provisionamento.md:215-216) documenta por host.
_RUNBOOK_JWKS = {"52.67.144.206", "54.20.171.151", "54.94.110.228"}
_RUNBOOK_TOKEN = {"52.67.250.153", "52.67.98.193", "54.20.130.20"}

_TFVARS_FIXTURE = """\
// variavel alheia, ANTES do bloco — o parser NAO pode le-la
outra_lista = [
  "203.0.113.9/32",
]

https_egress_ipv4_cidrs = [
  // cognito-idp.sa-east-1.amazonaws.com — JWKS do issuer (publico, via NAT).
  "52.67.144.206/32", // [25/09/2026] medido
  "177.71.135.231/32",
  // ENIs do VPC endpoint ecr.api (proprietario, estatico).
  "10.40.40.27/32",
  "10.40.41.104/32",
  "198.51.100.7/24", // nao e' /32: fora do escopo
  "nome-nao-e-ip", // lixo: invalido
]
"""

_IPS_MEDIDOS = """
# dig +short ... de dentro da VPC
52.67.144.206
177.71.135.231/32

15.228.100.9 # fora da lista pinada do fixture -> MISSING (IP GLOBAL: TEST-NET contaria como privativo)
host-que-nao-e-ip
"""


def _escreve(tmp_path: Path, nome: str, conteudo: str) -> Path:
    alvo = tmp_path / nome
    alvo.write_text(conteudo, encoding="utf-8")
    return alvo


def _pins_do_fixture() -> set[str]:
    _, pares = parse_https_egress_cidrs(_TFVARS_FIXTURE)
    pins, _, _ = separa_cidrs(cidr for _, cidr in pares)
    return pins


# --- 1. parser ------------------------------------------------------------------------


def test_parser_le_so_o_bloco_pedido_e_guarda_a_linha() -> None:
    abertura, pares = parse_https_egress_cidrs(_TFVARS_FIXTURE)
    chaves = [linha for linha, _ in pares]

    assert abertura == 6  # a linha do `https_egress_ipv4_cidrs = [` (0-based +1)
    assert "203.0.113.9/32" not in [valor for _, valor in pares]  # bloco alheio ignorado
    assert chaves == sorted(chaves)  # numeros de linha crescentes
    assert (chaves[0] - abertura) >= 1  # a primeira entrada vem DEPOIS da abertura


def test_parser_descarta_comentario_e_aceita_entrada_na_linha_do_fechamento() -> None:
    texto = 'https_egress_ipv4_cidrs = [\n  "1.2.3.4/32", // x\n  "5.6.7.8/32"]\n'
    _, pares = parse_https_egress_cidrs(texto)

    assert [valor for _, valor in pares] == ["1.2.3.4/32", "5.6.7.8/32"]
    assert pares[1][0] == pares[0][0] + 1  # cada entrada aponta a propria linha


def test_parser_rejeita_bloco_ausente() -> None:
    with pytest.raises(ValueError, match="nao encontrado"):
        parse_https_egress_cidrs("outra_coisa = [\n  '1.2.3.4/32',\n]\n")


def test_parser_rejeita_bloco_sem_fechamento() -> None:
    with pytest.raises(ValueError, match="fechamento"):
        parse_https_egress_cidrs("https_egress_ipv4_cidrs = [\n  '1.2.3.4/32',\n")


# --- 2. classificacao pura ------------------------------------------------------------


def test_separa_cidrs_divide_publico_proprietario_e_invalido() -> None:
    pins, proprietarios, invalidos = separa_cidrs(
        [
            "52.67.144.206/32",
            "10.40.40.27/32",
            "203.0.113.9/32",  # TEST-NET-3: o `ipaddress` a trata como privativa — assim mesmo
            "198.51.100.7/24",
            "lixo",
            "52.67.144.206/32",  # duplicata
        ]
    )

    assert pins == {"52.67.144.206"}  # sem /32, no formato do DNS; duplicata colapsada
    assert proprietarios == {"10.40.40.27", "203.0.113.9"}  # 10.x e FAIXAS RESERVADAS (TEST-NET-3)
    assert invalidos == ["198.51.100.7/24", "lixo"]


def test_classifica_exato_nao_tem_nada_a_dizer() -> None:
    pins = _pins_do_fixture()

    assert classifica(pins, pins) == ([], [])


def test_classifica_marca_resolvido_nao_pinado_como_missing() -> None:
    pins = _pins_do_fixture()

    faltando, obsoletos = classifica(pins, pins | {"203.0.113.9"})

    assert faltando == ["203.0.113.9"]
    assert obsoletos == []


def test_classifica_marca_pinado_nao_resolvido_como_stale() -> None:
    pins = _pins_do_fixture()

    faltando, obsoletos = classifica(pins, pins - {"177.71.135.231"})

    assert faltando == []
    assert obsoletos == ["177.71.135.231"]


def test_classifica_ordena_por_valor_numerico_e_nao_lexicografico() -> None:
    faltando, _ = classifica(set(), {"18.228.162.125", "177.71.135.231", "54.232.187.39"})

    assert faltando == ["18.228.162.125", "54.232.187.39", "177.71.135.231"]  # "177..." ia na frente


def test_filtra_publicos_separa_o_privativo() -> None:
    publicos, privativos = filtra_publicos({"10.40.40.27", "52.67.144.206"})

    assert publicos == {"52.67.144.206"}
    assert privativos == {"10.40.40.27"}


def test_carregar_ips_medidos_aceita_ip_ou_cidr_e_rejeita_lixo() -> None:
    ips, rejeitadas = carregar_ips_medidos(_IPS_MEDIDOS)

    assert ips == {"52.67.144.206", "177.71.135.231", "15.228.100.9"}  # `/32` e comentario comedos
    assert rejeitadas == ["host-que-nao-e-ip"]


# --- 3. arvore real -------------------------------------------------------------------


def test_tfvars_real_tem_os_pins_do_runbook_e_enis_fora_da_comparacao() -> None:
    abertura, pares = parse_https_egress_cidrs(_TFVARS_REAL.read_text(encoding="utf-8"))
    pins, proprietarios, invalidos = separa_cidrs(cidr for _, cidr in pares)

    assert abertura >= 100  # o bloco vive no miolo do portal.auto.tfvars
    assert pins >= (_RUNBOOK_JWKS | _RUNBOOK_TOKEN)  # os 6 do runbook estao pinados
    assert len(pins) >= 13  # as rodadas de 25/09 e 28/09 so ACRESCENTARAM
    assert proprietarios and all(ip.startswith("10.") for ip in proprietarios)  # ENIs de VPCE
    assert invalidos == []


def test_arvore_real_menos_um_pin_produz_missing_red_sintetico() -> None:
    # Replica o exercicio RED em memoria (o arquivo NUNCA e' tocado): tire UM /32 hoje
    # resolvido da lista pinada e a resolucao de vira MISSING — e' exatamente o que acontece
    # no dia em que a AWS traz um endereco novo e o tfvars ficou para tras.
    _, pares = parse_https_egress_cidrs(_TFVARS_REAL.read_text(encoding="utf-8"))
    pins, _, _ = separa_cidrs(cidr for _, cidr in pares)
    alvo = sorted(pins, key=lambda ip: tuple(int(parte) for parte in ip.split(".")))[0]

    faltando, obsoletos = classifica(pins - {alvo}, pins)

    assert faltando == [alvo]
    assert obsoletos == []


def test_runbook_citado_pelo_checker_existe_de_verdade() -> None:
    caminho = _REPO_ROOT / RUNBOOK.split(":")[0]

    assert RUNBOOK.endswith("portal-dev-provisionamento.md:210-217")
    assert caminho.is_file()
    assert "de dentro da VPC" in caminho.read_text(encoding="utf-8")


# --- 4. CLI (sempre --from-file: nenhum teste toca DNS) -------------------------------


def test_cli_sai_1_com_remedicao_e_linhas_exatas(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tfvars = _escreve(tmp_path, "portal.auto.tfvars", _TFVARS_FIXTURE)
    medidos = _escreve(tmp_path, "ips.txt", _IPS_MEDIDOS)

    assert main(["--tfvars", str(tfvars), "--from-file", str(medidos)]) == 1

    saida = capsys.readouterr().out
    assert "MISSING (1): resolvido(s) HOJE e NAO pinado(s) -> 15.228.100.9" in saida
    assert '    "15.228.100.9/32",' in saida  # a linha EXATA a colar no tfvars
    assert RUNBOOK in saida  # a remedicao aponta o runbook
    assert "0.0.0.0/0" in saida  # ...e veta o alargamento


def test_cli_exato_sai_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tfvars = _escreve(tmp_path, "portal.auto.tfvars", _TFVARS_FIXTURE)
    pins = _pins_do_fixture()
    medidos = _escreve(tmp_path, "ips.txt", "\n".join(sorted(pins)) + "\n")

    assert main(["--tfvars", str(tfvars), "--from-file", str(medidos)]) == 0
    assert "EXATO" in capsys.readouterr().out


def test_cli_so_stale_sai_0_com_nota_de_higiene(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tfvars = _escreve(tmp_path, "portal.auto.tfvars", _TFVARS_FIXTURE)
    pins = _pins_do_fixture()
    alvo = sorted(pins)[0]
    medidos = _escreve(tmp_path, "ips.txt", "\n".join(sorted(pins - {alvo})) + "\n")

    assert main(["--tfvars", str(tfvars), "--from-file", str(medidos)]) == 0

    saida = capsys.readouterr().out
    assert f"STALE   (1): pinado(s) que nao resolve(m) mais (cruft) -> {alvo}" in saida
    assert "Higiene" in saida


def test_cli_entrada_privativa_nao_vira_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Um `dig` de dentro da VPC pode devolver IP de ENI (10.x) — ele e' proprietario, nao drift.
    tfvars = _escreve(tmp_path, "portal.auto.tfvars", _TFVARS_FIXTURE)
    pins = _pins_do_fixture()
    medidos = _escreve(tmp_path, "ips.txt", "\n".join(sorted(pins)) + "\n10.40.40.27\n")

    assert main(["--tfvars", str(tfvars), "--from-file", str(medidos)]) == 0

    saida = capsys.readouterr().out
    assert "EXATO" in saida
    assert "10.40.40.27" in saida  # ...mas o checker DIZ que ignorou


def test_cli_arquivo_sem_ip_valido_sai_2(tmp_path: Path) -> None:
    tfvars = _escreve(tmp_path, "portal.auto.tfvars", _TFVARS_FIXTURE)
    medidos = _escreve(tmp_path, "ips.txt", "so-lixo\n\n# comentario\n")

    assert main(["--tfvars", str(tfvars), "--from-file", str(medidos)]) == 2


def test_cli_tfvars_ausente_sai_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    medidos = _escreve(tmp_path, "ips.txt", "52.67.144.206\n")

    assert main(["--tfvars", str(tmp_path / "nao-existe.tfvars"), "--from-file", str(medidos)]) == 2

    saida, erro = capsys.readouterr()
    assert saida == ""
    assert "ERRO" in erro


def test_remedicao_formata_as_entradas_na_identacao_do_tfvars() -> None:
    texto = remedicao(["203.0.113.9", "18.228.162.125"], Path("portal.auto.tfvars"), 155)

    assert '\n    "203.0.113.9/32",\n    "18.228.162.125/32",\n' in texto
    assert "portal.auto.tfvars" in texto
    assert "linha\n     155" in texto  # aponta a abertura do bloco
