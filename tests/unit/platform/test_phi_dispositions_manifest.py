"""O manifesto CODEOWNED de disposicoes PHI e o seu fecho com a cerca (decisao do dono R-199).

O que esta suite prova, e por que cada grupo existe:

1. **`TestManifestoEmbarcado`** — o arquivo que este PR entrega esta VAZIO no sentido que
   importa: nenhuma `disposicao` escolhida, nenhuma `ratificacao` preenchida, o rotulo de
   pendencia presente em toda linha. E a prova de que a engenharia nao decidiu nada.
2. **`TestPortaDura`** — o carregador RECUSA, nunca degrada. O caso central e o que a decisao
   R-199 tornou possivel: uma copia com `status: RATIFICADO` e os campos de `ratificacao`
   vazios tem de ser refutada, senao a porta seria decorativa.
3. **`TestFechoCodigoManifesto`** — os dois sentidos do fecho. Sao os testes que ficam
   VERMELHOS se alguem tirar um nome de um dos lados; a sonda de mutacao do relatorio e
   exatamente `test_nome_do_codigo_ausente_do_manifesto_e_vermelho`.
4. **`TestEfeitoDaAssinatura`** — o gate que o preenchimento LIGA: `LISTAR_EM_CONJUNTO_PHI`
   ratificado exige a listagem do nome; `REGISTRAR_NAO_PHI` nao exige ato nenhum. Sem o
   segundo caso o primeiro seria "toda ratificacao e vermelha", que nao e um gate.
5. **`TestCodeownersDeFato`** — a regra que este PR acrescenta e derivada da arvore e passada
   pelo MATCHER DO PROPRIO REPO (`scripts/ci/check_flip_path_review.owners_for_path`), nao por
   um `in` sobre o texto: o `floor_note` de R-199 dizia que sem regra o manifesto seria
   CODEOWNED apenas no papel, e "papel" e exatamente o que um `in` mediria.
6. **`TestGateDeArtefatos`** — `make validate-artifacts` valida o arquivo de verdade.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from scripts.ci.check_flip_path_review import owners_for_path, parse_codeowners

from maezo.platform.validation import phi_completeness as fence
from maezo.platform.validation import policy
from maezo.platform.validation.cli import validate_artifacts
from maezo.platform.validation.phi_completeness import DISPOSITIONS, PHI_LISTED_NAMES
from maezo.platform.validation.policy import (
    PHI_DISPOSICAO_LISTAR,
    PHI_DISPOSICAO_NAO_PHI,
    PHI_DISPOSITIONS_FILENAME,
    PHI_DISPOSITIONS_ID,
    PhiDispositionsError,
    load_phi_dispositions,
    validate_phi_dispositions_dir,
)
from maezo.platform.validation.result import Report
from maezo.tools.workers import phi_vars
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST = _REPO_ROOT / "spec" / "policies" / "phi" / PHI_DISPOSITIONS_FILENAME
_CODEOWNERS = _REPO_ROOT / ".github" / "CODEOWNERS"
_REVIEW_QUEUE = _REPO_ROOT / "docs" / "review-queue.md"

_ROTULO = "recomendação — pendente de assinatura do DPO"


def _raw() -> dict[str, object]:
    loaded = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write(tmp_path: Path, data: object) -> Path:
    target = tmp_path / PHI_DISPOSITIONS_FILENAME
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def _assinada(disposicao: str) -> dict[str, object]:
    """Um bloco de linha ASSINADA, usado so para exercitar o gate — nunca escrito no repo."""
    return {
        "disposicao": disposicao,
        "ratificacao": {
            "ratificado": True,
            "revisor": "DPO de teste (nao e uma assinatura real)",
            "ratificado_em": "2026-01-01",
        },
    }


# ---------------------------------------------------------------------------
# 1. O manifesto embarcado
# ---------------------------------------------------------------------------


class TestManifestoEmbarcado:
    def test_o_arquivo_existe_e_carrega(self) -> None:
        manifesto = load_phi_dispositions(_MANIFEST)
        assert manifesto.status == policy.PHI_DRAFT_STATUS
        assert manifesto.ratificado is False

    def test_o_id_e_o_do_ato_de_migracao(self) -> None:
        assert _raw()["id"] == PHI_DISPOSITIONS_ID == "PHI-DISPOSICOES-MIGRACAO-MANIFESTO"

    def test_nenhuma_disposicao_foi_escolhida(self) -> None:
        """A engenharia nao decide conteudo: todo `disposicao` nasce e permanece nulo."""
        manifesto = load_phi_dispositions(_MANIFEST)
        assert [item.disposicao for item in manifesto.itens] == [None] * len(manifesto.itens)

    def test_nenhuma_ratificacao_foi_preenchida(self) -> None:
        manifesto = load_phi_dispositions(_MANIFEST)
        for item in manifesto.itens:
            assert item.ratificado is False, item.nome
            assert item.revisor is None, item.nome
            assert item.ratificado_em is None, item.nome
        assert manifesto.ratificadas == {}

    def test_toda_linha_carrega_o_rotulo_de_pendencia(self) -> None:
        data = _raw()
        assert data["rotulo"] == _ROTULO
        itens = data["disposicoes"]
        assert isinstance(itens, list)
        for entry in itens:
            assert isinstance(entry, dict)
            assert entry["rotulo"] == _ROTULO, entry.get("nome")

    def test_o_manifesto_aponta_onde_a_pergunta_esta_escrita(self) -> None:
        """A pergunta NAO foi copiada para ca; o manifesto cita o simbolo que a guarda."""
        assert _raw()["pergunta_em"] == ("src/maezo/platform/validation/phi_completeness.py::DISPOSITIONS")
        modulo = Path(str(fence.__file__))
        assert modulo.is_file()
        assert "DISPOSITIONS" in modulo.read_text(encoding="utf-8")

    def test_nenhum_nome_disposto_entrou_em_conjunto_phi(self) -> None:
        """A regra que a fila de revisao ja declarava, medida: uma disposicao nao lista nada."""
        assert set(DISPOSITIONS).isdisjoint(PHI_LISTED_NAMES)


# ---------------------------------------------------------------------------
# 2. A porta dura do carregador
# ---------------------------------------------------------------------------


class TestPortaDura:
    def test_status_ratificado_com_campos_vazios_e_recusado(self, tmp_path: Path) -> None:
        """O caso central da porta: assinar o status sem assinar os campos nao ratifica nada."""
        data = _raw()
        data["status"] = policy.PHI_RATIFIED_STATUS
        with pytest.raises(PhiDispositionsError, match="ratificacao.ratificado"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_ratificacao_parcial_e_recusada(self, tmp_path: Path) -> None:
        data = _raw()
        data["status"] = policy.PHI_RATIFIED_STATUS
        data["ratificacao"] = {"ratificado": True, "revisor": "alguem", "ratificado_em": "  "}
        with pytest.raises(PhiDispositionsError, match="INCOMPLETA"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_ratificado_como_string_nao_conta(self, tmp_path: Path) -> None:
        data = _raw()
        data["ratificacao"] = {"ratificado": "true", "revisor": None, "ratificado_em": None}
        with pytest.raises(PhiDispositionsError, match="booleano literal"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_linha_ratificada_sem_disposicao_e_recusada(self, tmp_path: Path) -> None:
        data = _raw()
        data["disposicoes"][0].update(_assinada(PHI_DISPOSICAO_LISTAR))
        data["disposicoes"][0]["disposicao"] = None
        with pytest.raises(PhiDispositionsError, match="ratificada sem `disposicao`"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_disposicao_preenchida_sem_assinatura_e_recusada(self, tmp_path: Path) -> None:
        """O anti-lavagem: um valor encostado no campo sem assinatura seria a engenharia
        decidindo pelo DPO."""
        data = _raw()
        data["disposicoes"][0]["disposicao"] = PHI_DISPOSICAO_NAO_PHI
        with pytest.raises(PhiDispositionsError, match="sem ratificacao"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_token_de_disposicao_fora_do_vocabulario_e_recusado(self, tmp_path: Path) -> None:
        data = _raw()
        data["disposicoes"][0].update(_assinada("TALVEZ"))
        with pytest.raises(PhiDispositionsError, match="fora do vocabulario"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_nome_duplicado_e_recusado(self, tmp_path: Path) -> None:
        data = _raw()
        data["disposicoes"].append(dict(data["disposicoes"][0]))
        with pytest.raises(PhiDispositionsError, match="duplicado"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_marcador_de_template_recusa_o_arquivo_inteiro(self, tmp_path: Path) -> None:
        data = _raw()
        data[policy.PHI_TEMPLATE_MARKER] = True
        with pytest.raises(PhiDispositionsError, match="template"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_rotulo_ausente_em_manifesto_nao_ratificado_e_recusado(self, tmp_path: Path) -> None:
        data = _raw()
        del data["rotulo"]
        with pytest.raises(PhiDispositionsError, match="rotulo"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_id_errado_e_recusado(self, tmp_path: Path) -> None:
        data = _raw()
        data["id"] = "OUTRO-MANIFESTO"
        with pytest.raises(PhiDispositionsError, match="`id` tem de ser"):
            load_phi_dispositions(_write(tmp_path, data))

    def test_arquivo_ausente_e_recusado(self, tmp_path: Path) -> None:
        with pytest.raises(PhiDispositionsError, match="ausente"):
            load_phi_dispositions(tmp_path / PHI_DISPOSITIONS_FILENAME)

    def test_yaml_malformado_e_recusado(self, tmp_path: Path) -> None:
        target = tmp_path / PHI_DISPOSITIONS_FILENAME
        target.write_text("disposicoes: [\n", encoding="utf-8")
        with pytest.raises(PhiDispositionsError, match="malformed YAML"):
            load_phi_dispositions(target)

    def test_disposicoes_vazia_e_recusada(self, tmp_path: Path) -> None:
        data = _raw()
        data["disposicoes"] = []
        with pytest.raises(PhiDispositionsError, match="`disposicoes` ausente"):
            load_phi_dispositions(_write(tmp_path, data))


# ---------------------------------------------------------------------------
# 3. O fecho codigo <-> manifesto
# ---------------------------------------------------------------------------


def _sweep_vazio() -> fence.Sweep:
    """Uma varredura sem nenhum nome: isola o fecho das outras duas regras da cerca."""
    return fence.Sweep((), ())


class TestFechoCodigoManifesto:
    def test_o_manifesto_embarcado_fecha_com_a_tabela_do_codigo(self) -> None:
        manifesto = load_phi_dispositions(_MANIFEST)
        assert manifesto.nomes == frozenset(DISPOSITIONS)

    def test_a_cerca_fica_verde_com_o_manifesto_do_repo(self) -> None:
        report = Report()
        fence.check_sweep(_sweep_vazio(), report)
        assert report.findings == []

    def test_nome_do_codigo_ausente_do_manifesto_e_vermelho(self, tmp_path: Path) -> None:
        """A sonda nomeada no relatorio: tirar UMA linha do manifesto tem de ficar VERMELHO."""
        data = _raw()
        removido = data["disposicoes"].pop(0)["nome"]
        report = Report()
        fence.check_sweep(_sweep_vazio(), report, manifest_path=_write(tmp_path, data))
        assert len(report.findings) == 1
        assert removido in report.findings[0].message
        assert "NO line in the CODEOWNED dispositions manifest" in report.findings[0].message

    def test_linha_do_manifesto_sem_pergunta_viva_e_vermelha(self, tmp_path: Path) -> None:
        data = _raw()
        data["disposicoes"].append(
            {
                "nome": "nome_que_o_codigo_nao_dispoe",
                "disposicao": None,
                "ratificacao": {"ratificado": False, "revisor": None, "ratificado_em": None},
                "rotulo": _ROTULO,
            }
        )
        report = Report()
        fence.check_sweep(_sweep_vazio(), report, manifest_path=_write(tmp_path, data))
        assert len(report.findings) == 1
        assert "nome_que_o_codigo_nao_dispoe" in report.findings[0].message
        assert "no entry in phi_completeness.DISPOSITIONS" in report.findings[0].message

    def test_manifesto_ausente_e_um_achado_bloqueante_nao_um_check_pulado(self, tmp_path: Path) -> None:
        report = Report()
        fence.check_sweep(_sweep_vazio(), report, manifest_path=tmp_path / "nao-existe.yaml")
        assert len(report.findings) == 1
        assert "ausente" in report.findings[0].message

    def test_o_caminho_default_aponta_para_o_manifesto_do_repo(self) -> None:
        assert fence.default_manifest_path() == _MANIFEST


# ---------------------------------------------------------------------------
# 4. O efeito que a assinatura liga
# ---------------------------------------------------------------------------


class TestEfeitoDaAssinatura:
    def test_listar_ratificado_exige_a_listagem_do_nome(self, tmp_path: Path) -> None:
        data = _raw()
        alvo = data["disposicoes"][0]
        alvo.update(_assinada(PHI_DISPOSICAO_LISTAR))
        assert alvo["nome"] not in PHI_LISTED_NAMES
        report = Report()
        fence.check_sweep(_sweep_vazio(), report, manifest_path=_write(tmp_path, data))
        assert len(report.findings) == 1
        assert PHI_DISPOSICAO_LISTAR in report.findings[0].message
        assert str(alvo["nome"]) in report.findings[0].message

    def test_registrar_nao_phi_ratificado_nao_exige_ato_algum(self, tmp_path: Path) -> None:
        """Sem este caso o teste acima seria so 'toda ratificacao e vermelha', que nao e um gate."""
        data = _raw()
        data["disposicoes"][0].update(_assinada(PHI_DISPOSICAO_NAO_PHI))
        report = Report()
        fence.check_sweep(_sweep_vazio(), report, manifest_path=_write(tmp_path, data))
        assert report.findings == []

    def test_linha_ratificada_sob_status_draft_ja_conta_e_acende_o_gate(self, tmp_path: Path) -> None:
        """`status: DRAFT` na raiz NAO e uma porta que barra as linhas (achado F1 do
        verificador, forjadura V6): uma disposicao com a sua propria `ratificacao` completa ja
        entra em `manifesto.ratificadas` e ja acende o gate, sem esperar o `status` da raiz virar
        RATIFICADO — porque `PhiDispositionsManifest.ratificadas` nunca consulta `self.status`."""
        data = _raw()
        assert data["status"] == policy.PHI_DRAFT_STATUS
        alvo = data["disposicoes"][0]
        alvo.update(_assinada(PHI_DISPOSICAO_LISTAR))
        assert alvo["nome"] not in PHI_LISTED_NAMES
        manifest_path = _write(tmp_path, data)

        manifesto = load_phi_dispositions(manifest_path)
        assert manifesto.status == policy.PHI_DRAFT_STATUS
        assert manifesto.ratificado is False
        assert manifesto.ratificadas == {str(alvo["nome"]): PHI_DISPOSICAO_LISTAR}

        report = Report()
        fence.check_sweep(_sweep_vazio(), report, manifest_path=manifest_path)
        assert len(report.findings) == 1
        assert PHI_DISPOSICAO_LISTAR in report.findings[0].message
        assert str(alvo["nome"]) in report.findings[0].message


# ---------------------------------------------------------------------------
# 5. CODEOWNERS de fato, nao no papel
# ---------------------------------------------------------------------------


class TestCodeownersDeFato:
    def test_o_manifesto_casa_uma_regra_pelo_matcher_do_proprio_repo(self) -> None:
        rules = parse_codeowners(_CODEOWNERS.read_text(encoding="utf-8"))
        rel = str(_MANIFEST.relative_to(_REPO_ROOT))
        rule = owners_for_path(rules, rel)
        assert rule is not None, f"{rel} nao casa regra alguma — CODEOWNED so no papel"
        assert rule.pattern == "/spec/policies/phi/"

    def test_a_regra_carrega_o_par_de_donos_das_linhas_de_conteudo(self) -> None:
        """Superset, nao igualdade (achado F2 do verificador): os dois donos mandatorios tem de
        estar SEMPRE presentes, mas a regra pode crescer (ex.: a DPO `@lucasreisEvah`, item de
        registro pendente de decisao do dono, §10) sem que essa alteracao tambem precise editar
        este teste so por adicionar um owner — o que este teste protege e a AUSENCIA dos dois
        donos originais, nunca a contagem exata deles."""
        rules = parse_codeowners(_CODEOWNERS.read_text(encoding="utf-8"))
        rule = owners_for_path(rules, str(_MANIFEST.relative_to(_REPO_ROOT)))
        assert rule is not None
        owners = {owner.raw for owner in rule.owners}
        assert {"@rodaquino-OMNI", "@Omni-Saude/security-team"} <= owners

    def test_todo_arquivo_de_spec_policies_phi_esta_coberto(self) -> None:
        """Derivado da arvore, nao de uma lista: a regra e de DIRETORIO por isso mesmo."""
        rules = parse_codeowners(_CODEOWNERS.read_text(encoding="utf-8"))
        arquivos = sorted((_REPO_ROOT / "spec" / "policies" / "phi").glob("*.yaml"))
        assert arquivos, "spec/policies/phi/ vazio — a derivacao seria vacua"
        for path in arquivos:
            rule = owners_for_path(rules, str(path.relative_to(_REPO_ROOT)))
            assert rule is not None and rule.pattern == "/spec/policies/phi/", path.name


# ---------------------------------------------------------------------------
# 6. O gate de artefatos
# ---------------------------------------------------------------------------


class TestGateDeArtefatos:
    def test_o_diretorio_real_passa(self) -> None:
        report = Report()
        validate_phi_dispositions_dir(_MANIFEST.parent, report)
        assert report.findings == []

    def test_um_manifesto_quebrado_vira_achado_bloqueante(self, tmp_path: Path) -> None:
        data = _raw()
        data["version"] = 2
        _write(tmp_path, data)
        report = Report()
        validate_phi_dispositions_dir(tmp_path, report)
        assert len(report.findings) == 1
        assert "`version` tem de ser 1" in report.findings[0].message

    def test_diretorio_ausente_vira_achado_bloqueante(self, tmp_path: Path) -> None:
        report = Report()
        validate_phi_dispositions_dir(tmp_path / "nao-existe", report)
        assert len(report.findings) == 1

    def _policies_copy(self, tmp_path: Path) -> Path:
        destino = tmp_path / "policies"
        shutil.copytree(_REPO_ROOT / "spec" / "policies", destino)
        return destino

    def test_o_cli_aprova_a_arvore_de_politicas_intacta(self, tmp_path: Path) -> None:
        assert validate_artifacts([str(self._policies_copy(tmp_path))]) == 0

    def test_o_cli_reprova_um_manifesto_quebrado(self, tmp_path: Path) -> None:
        """Prova a FIACAO, nao so o validador: sem a chamada em `cli._validate_policies_root`
        este caso voltaria a devolver 0 com o manifesto quebrado em disco."""
        destino = self._policies_copy(tmp_path)
        data = _raw()
        data["version"] = 2
        (destino / "phi" / PHI_DISPOSITIONS_FILENAME).write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        assert validate_artifacts([str(destino)]) == 1

    def test_a_fila_de_revisao_aponta_para_o_manifesto(self) -> None:
        """A tabela provisoria saiu, mas nunca sem ponteiro de substituicao."""
        texto = _REVIEW_QUEUE.read_text(encoding="utf-8")
        assert f"spec/policies/phi/{PHI_DISPOSITIONS_FILENAME}" in texto
        assert "phi_completeness.DISPOSITIONS" in texto


# ---------------------------------------------------------------------------
# 7. R-066 — a decisao que so cobre MANTER
# ---------------------------------------------------------------------------


class TestDecisaoR066:
    """`laudo`/`diagnostico` ficam em `PHI_PROCESS_VARS` como defesa em profundidade.

    A assimetria e o ponto e esta no `floor_note` da propria decisao: MANTER foi decidido pelo
    dono (R-066, 2026-09-04) para tirar da fila do DPO um ato que so conserva protecao;
    qualquer proposta de REMOVER volta ao encarregado. Estes dois testes prendem os dois lados
    disso — as entradas e o registro escrito da decisao ao lado delas.
    """

    def test_as_duas_entradas_seguem_no_conjunto(self) -> None:
        assert {"laudo", "diagnostico"} <= PHI_PROCESS_VARS

    def test_a_decisao_esta_registrada_ao_lado_das_entradas(self) -> None:
        linhas = Path(str(phi_vars.__file__)).read_text(encoding="utf-8").splitlines()
        marcadas = [i for i, line in enumerate(linhas) if "R-066" in line]
        assert len(marcadas) == 1, "o registro da decisao R-066 sumiu ou foi duplicado"
        vizinhas = linhas[marcadas[0] : marcadas[0] + 3]
        assert any('"laudo"' in line for line in vizinhas), vizinhas
        assert any('"diagnostico"' in line for line in vizinhas), vizinhas
