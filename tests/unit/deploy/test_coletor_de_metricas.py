"""A cerca do coletor: o que ele raspa tem de existir, na porta em que existe.

POR QUE ESTE ARQUIVO EXISTE (Frente 5 de `HELENA_EM_PRODUCAO_O_QUE_FALTA.md`). O defeito que a
frente corrige nao foi um coletor errado — foi coletor NENHUM, por meses, com as regras de
agregacao prontas e o contador emitido. O modo de falha e' silencioso por natureza: um alvo que
some nao levanta excecao, nao derruba task e nao aparece em log de aplicacao. Produz um grafico
vazio, que e' exatamente o que um canal sem trafego tambem produz.

As duas maneiras de reproduzir esse silencio depois do coletor no ar sao mecanicas, e sao as duas
que este arquivo tranca:

  1. RENOMEAR um servico no Cloud Map (ou acrescentar um agente) sem mexer na config do coletor.
     O `dns_sd_configs` passa a resolver um nome que nao existe, o job fica sem alvo, e nada
     reclama.
  2. MUDAR A PORTA do `/metrics` de um componente. O nome resolve, o alvo existe, a conexao e'
     recusada — e o resultado, de novo, e' serie ausente.

O QUE ESTA CERCA NAO AFIRMA: que o coletor esta' no ar, que o workspace aceita a escrita, ou que
alguem olha o painel. Essas sao afirmacoes sobre o ambiente, nao sobre o repo, e a Frente 5 as
trata por medicao apos o apply — nao por teste de unidade que mentiria sobre estar provando.

METODO, e a licao de 13/09 embutida: os nomes sao localizados pela DECLARACAO
(`aws_service_discovery_service ... name = "..."`), nunca por um literal repetido no teste. Uma
cerca que guarda a propria copia da lista envelhece junto com o defeito que deveria pegar.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_ENV_DIR: Final[Path] = _REPO_ROOT / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1"
_CONFIG: Final[Path] = _REPO_ROOT / "deploy" / "observability" / "otel-collector-ecs-dev.yaml"

#: O sufixo do Cloud Map entra por variavel de ambiente para a config nao carregar o nome do
#: ambiente dentro. Aqui ele e' removido para sobrar o nome do SERVICO, que e' o que se compara.
_SUFIXO_NAMESPACE: Final[str] = ".${env:MAEZO_NAMESPACE}"


def _config() -> dict[str, Any]:
    carregada = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(carregada, dict), f"{_CONFIG} nao carregou como mapa"
    return carregada


def _scrape_configs() -> list[dict[str, Any]]:
    jobs = _config()["receivers"]["prometheus"]["config"]["scrape_configs"]
    assert isinstance(jobs, list) and jobs, "nenhum job de raspagem na config do coletor"
    return jobs


def _alvos_por_dns() -> dict[str, int]:
    """`{nome-do-servico-no-cloud-map: porta}` de todo `dns_sd_configs` da config."""
    alvos: dict[str, int] = {}
    for job in _scrape_configs():
        for descoberta in job.get("dns_sd_configs", []):
            porta = descoberta["port"]
            for nome in descoberta["names"]:
                assert nome.endswith(_SUFIXO_NAMESPACE), (
                    f"alvo {nome!r} nao usa o sufixo por variavel de ambiente — um namespace "
                    f"escrito a mao fixa o ambiente dentro de um arquivo que serve aos dois"
                )
                alvos[nome[: -len(_SUFIXO_NAMESPACE)]] = porta
    assert alvos, "a config nao descobre nada pelo Cloud Map — a frente inteira depende disso"
    return alvos


def _servicos_declarados_no_cloud_map() -> set[str]:
    """Os nomes que o Terraform REGISTRA no Cloud Map, lidos das declaracoes.

    `agent-${each.key}` e' expandido com as chaves do mapa `agentes` do proprio `.tf` — nao com
    uma lista escrita aqui, pelo mesmo motivo que a cerca da DMN localiza a coluna pelo
    `inputExpression` e nunca pelo `label`.
    """
    nomes: set[str] = set()
    for arquivo in sorted(_ENV_DIR.glob("*.tf")):
        texto = arquivo.read_text(encoding="utf-8")
        for bloco in re.finditer(
            r'resource\s+"aws_service_discovery_service"\s+"[^"]+"\s*\{(.*?)\n\}',
            texto,
            re.DOTALL,
        ):
            declarado = re.search(r'\n\s*name\s*=\s*"([^"]+)"', bloco.group(1))
            if declarado is None:
                continue
            nome = declarado.group(1)
            if nome == "agent-${each.key}":
                nomes.update(f"agent-{chave}" for chave in _chaves_dos_agentes())
            else:
                assert "${" not in nome, (
                    f"nome de servico com interpolacao nao prevista: {nome!r} — esta cerca "
                    f"precisa saber expandi-lo para nao absolver um alvo inexistente"
                )
                nomes.add(nome)
    assert nomes, "nenhum aws_service_discovery_service encontrado — o regex quebrou?"
    return nomes


def _chaves_dos_agentes() -> set[str]:
    texto = (_ENV_DIR / "service-agents.tf").read_text(encoding="utf-8")
    bloco = re.search(r"\n  agentes = \{(.*?)\n  \}\n", texto, re.DOTALL)
    assert bloco is not None, "bloco `agentes = {` nao encontrado em service-agents.tf"
    chaves = set(re.findall(r"^    (\w+) = \{$", bloco.group(1), re.MULTILINE))
    assert chaves, "nenhum agente lido do mapa `agentes`"
    return chaves


def _porta_do_container(arquivo: str, familia: str) -> int:
    """A `containerPort` da task definition cuja `family` termina em `familia`."""
    texto = (_ENV_DIR / arquivo).read_text(encoding="utf-8")
    portas = re.findall(r"portMappings\s*=\s*\[\{\s*containerPort\s*=\s*(\d+)", texto)
    assert len(portas) == 1, f"{arquivo}: esperava uma portMappings, achei {len(portas)}"
    return int(portas[0])


def test_todo_alvo_do_coletor_existe_no_cloud_map() -> None:
    """Nenhum job raspa um nome que o Terraform nao registra.

    E' a primeira das duas formas de o silencio voltar: renomear um servico, ou apontar para um
    que nunca existiu. O `dns_sd_configs` nao falha alto quando o nome nao resolve — ele
    simplesmente nao produz alvo.
    """
    alvos = set(_alvos_por_dns())
    declarados = _servicos_declarados_no_cloud_map()
    fantasmas = alvos - declarados
    assert not fantasmas, (
        f"o coletor raspa nome(s) que nenhum aws_service_discovery_service registra: "
        f"{sorted(fantasmas)}. Registrados hoje: {sorted(declarados)}"
    )


def test_todo_agente_declarado_e_raspado() -> None:
    """A direcao contraria, que e' a que pega o agente NOVO.

    Acrescentar um agente ao mapa `agentes` cria servico, task e registro no Cloud Map sem tocar
    na config do coletor — e o agente sobe invisivel. Esta e' a metade da identidade de conjunto
    que a cerca da DMN ensinou a nao esquecer.
    """
    alvos = set(_alvos_por_dns())
    esperados = {f"agent-{chave}" for chave in _chaves_dos_agentes()}
    faltando = esperados - alvos
    assert not faltando, (
        f"agente(s) declarado(s) no Terraform e nao raspado(s) pelo coletor: {sorted(faltando)}. "
        f"Um agente no ar sem coleta e' um agente cujo desfecho ninguem sabe."
    )


def test_o_receptor_e_raspado_porque_e_onde_a_helena_roda() -> None:
    """Explicito, e nao como consequencia da lista de agentes.

    `agent-helena` NAO executa turno (`agent_graph_execution_not_performed_here`): quem conduz o
    turno da Helena e' o receptor de webhook, em processo. Um coletor que raspasse so' os agentes
    teria `maezo_agent_desfecho_total` sempre em zero e pareceria canal sem trafego. Este teste
    existe para que essa confusao nao possa ser reintroduzida por quem nao conhece a distincao.
    """
    assert "webhook-receiver" in _alvos_por_dns(), (
        "o receptor nao esta' entre os alvos do coletor — e' o componente que realmente executa "
        "a Helena; sem ele a frente mede os tres daemons e nao o canal"
    )


def test_as_portas_raspadas_sao_as_portas_das_task_definitions() -> None:
    """A segunda forma do silencio: nome certo, porta errada.

    Uma `containerPort` alterada numa task definition nao tem relacao sintatica nenhuma com a
    config do coletor — os dois arquivos nunca se leem. Esta cerca e' o unico lugar em que a
    mudanca de um obriga o outro.
    """
    alvos = _alvos_por_dns()
    esperadas = {
        "agent-helena": _porta_do_container("service-agents.tf", "agente"),
        "webhook-receiver": _porta_do_container("service-webhook-receiver.tf", "webhook-receiver"),
    }
    divergentes = {
        nome: (alvos[nome], porta)
        for nome, porta in esperadas.items()
        if nome in alvos and alvos[nome] != porta
    }
    assert not divergentes, (
        f"porta raspada != porta do container (alvo, task definition): {divergentes}. "
        f"O nome resolve, a conexao e' recusada, e a serie some sem erro em lugar nenhum."
    )


def test_a_escrita_remota_e_assinada_e_nao_carrega_credencial() -> None:
    """O workspace gerenciado so' aceita SigV4 pela role da task.

    Um `prometheusremotewrite` sem `auth` seria recusado com 403 a cada lote — visivel apenas na
    auto-telemetria do coletor, que e' precisamente o sinal que ninguem olhava antes desta frente.
    E uma credencial estatica aqui seria segredo em arquivo versionado, que e' outro problema.
    """
    exportador = _config()["exporters"]["prometheusremotewrite"]
    assert exportador.get("auth", {}).get("authenticator") == "sigv4auth", (
        "a escrita remota precisa ser assinada pela extensao sigv4auth (role da task)"
    )
    assert "sigv4auth" in _config()["service"]["extensions"], (
        "sigv4auth declarada mas nao habilitada em service.extensions — o coletor sobe e escreve "
        "sem assinatura, que e' 403 silencioso a cada lote"
    )
    texto = _CONFIG.read_text(encoding="utf-8")
    for proibido in ("aws_access_key", "secret_key", "AKIA"):
        assert proibido not in texto, f"credencial aparente na config do coletor: {proibido!r}"


def test_o_coletor_raspa_a_si_mesmo() -> None:
    """Sem auto-telemetria nao ha como distinguir 'canal quieto' de 'coletor parado'.

    As duas coisas produzem o mesmo grafico vazio, e a segunda e' literalmente o estado que durou
    meses antes desta frente. `otelcol_exporter_send_failed_metric_points` so' existe se este job
    existir.
    """
    jobs = {job["job_name"] for job in _scrape_configs()}
    assert "otel-collector" in jobs, (
        "o coletor nao raspa a si mesmo — um coletor mudo e um canal sem conversa desenham o "
        "mesmo grafico, e foi essa ambiguidade que escondeu a ausencia de coleta"
    )
