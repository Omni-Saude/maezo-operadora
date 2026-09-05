"""AF-14 — o `CredentialVault` na RAIZ DE COMPOSICAO, nao so' nos seus proprios testes.

O defeito: `gateway/credential_vault.py` implementava o mecanismo #3 da ADR-0005 ("a credencial da
acao proibida nao existe no runtime do agente", `docs/adr/0005-hitl-architectural-guarantee.md:12`)
e NADA em `src/` construia um `CredentialVault` — as unicas mencoes fora do modulo que o define
eram os re-exports de `gateway/__init__.py`. `tests/unit/gateway/test_credential_vault.py` provava
a classe contra ela mesma; nenhuma garantia estrutural era verificavel em raiz alguma.

Este arquivo prova a garantia ATRAVES do construtor sancionado (`gateway/tool_registry.py`), que e'
por onde as cinco raizes montam os seams. As tres provas e o mutante que cada uma pega:

  1. `test_a_visao_do_agente_nao_enxerga_uma_credencial_de_particao_humana`
     mutante: achatar o cofre (uma unica dict) -> VERMELHO.
  2. `test_build_agent_seams_recusa_uma_composicao_que_vazaria_credencial_humana`
     mutante: tirar a linha `credentials = build_agent_credential_view(...)` de
     `build_agent_seams` -> VERMELHO (a composicao deixa de recusar).
  3. `test_o_token_do_motor_que_o_seam_recebe_veio_do_cofre`
     mutante: tirar `auth_token=engine_token` dos dois seams -> VERMELHO (nao-vacuidade: a
     credencial realmente flui do cofre ate' o transporte).

AS TRES RAIZES QUE CARREGAM CREDENCIAL (AF-14-F1). `src/` tem QUATRO construcoes de seam gated
contra o motor. Tres passam credencial e as tres passam pelo cofre:
`gateway/tool_registry.py::build_agent_seams`, `runtime/worker_runtime/service.py::
_engine_credential` e `platform/integrations/notifications_bridge.py::_engine_credential`. A
terceira ficou de fora da primeira versao desta mudanca — lia `settings.cibseven_auth_token`
direto — enquanto a docstring de `build_worker_credential_view` ja' a nomeava como coberta. A
quarta, `platform/evidence/dmn_sweep.py::main`, nao passa `auth_token` algum (CLI de diagnostico
nao autenticado), entao nao ha' credencial a governar la'. O transporte de tarefa externa
(`CibSevenWorkerTransport`) nao e' seam gated mas carrega a mesma credencial, e tambem passou a
le-la pelo cofre. Cada raiz tem aqui a sua prova: a recusa (`CredentialSeparationError`) e a
entrega do Bearer do motor.

O FENCE DE SUPERFICIE (secao 7) fecha isso por AST sobre todo `src/`, e o que ele afirma e'
exatamente isto, nem mais: nenhuma leitura LITERAL da credencial do motor — atributo,
`getattr`/`hasattr` com nome literal, ou subscrito com string constante (logo tambem via
`model_dump()`/`__dict__`) — fora do unico par (modulo, funcao) exento. RESIDUAL declarado: um nome
de campo montado em tempo de execucao ele nao ve', de proposito — e' por `getattr` dinamico que o
proprio cofre le' `AGENT_CREDENTIAL_FIELDS`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway import tool_registry
from maezo.gateway.credential_vault import AgentCredentialView, CredentialVault, HumanCredentialPartition
from maezo.gateway.tool_registry import (
    AGENT_CREDENTIAL_FIELDS,
    BRIDGE_PRINCIPAL,
    HUMAN_CREDENTIAL_FIELDS,
    CredentialSeparationError,
    build_agent_credential_view,
    build_agent_seams,
    build_credential_vault,
    build_worker_credential_view,
)

#: Construcoes deterministicas e de baixa entropia — nunca um segredo de verdade (gitleaks).
TOKEN_MOTOR = "tok-" + "q" * 28
CHAVE_NEGATIVA = "neg-" + "z" * 28

#: URL de banco INALCANCAVEL (porta 1): `PostgresAuditSink` so' abre pool no primeiro uso, entao a
#: raiz da ponte constroi sem I/O algum — a mesma URL que o `MAEZO_TEST_DATABASE_URL` da esteira usa.
BANCO_DE_TESTE = "postgresql://maezo:maezo@127.0.0.1:1/maezo"


class _SettingsDouble:
    """Superficie de settings lida por DUCK TYPING, como `build_agent_seams` a le' de verdade.

    Nao e' um duplo de um seam (nada aqui vira transporte): e' um objeto de configuracao, a mesma
    forma que as quatro classes reais de settings expoem para este construtor.
    """

    def __init__(self, **campos: Any) -> None:
        self.tenant_id = "amh"
        self.cibseven_base_url = "http://cibseven.invalid:8080/engine-rest"
        for nome, valor in campos.items():
            setattr(self, nome, valor)


# =================================================================================================
# 1. A separacao, vista pela funcao de producao
# =================================================================================================


def test_a_visao_do_agente_nao_enxerga_uma_credencial_de_particao_humana() -> None:
    settings = _SettingsDouble(
        cibseven_auth_token=TOKEN_MOTOR,
        negativa_assinatura_key=CHAVE_NEGATIVA,
    )
    cofre = build_credential_vault(settings=settings, agent_id="helena")

    # A credencial humana ESTA no cofre, na particao declarada...
    assert (
        cofre.get_human_credential(partition=HumanCredentialPartition.NEGATIVA, key="negativa_assinatura_key")
        == CHAVE_NEGATIVA
    )
    # ...e a visao do agente nem a enumera nem a le'.
    visao = build_agent_credential_view(settings=settings, agent_id="helena")
    assert "negativa_assinatura_key" not in visao.list_keys()
    with pytest.raises(KeyError):
        visao.get("negativa_assinatura_key")
    # Nao-vacuidade: a visao NAO esta vazia — a credencial de agente chegou nela.
    assert visao.get("cibseven_auth_token") == TOKEN_MOTOR


def test_o_principal_daemon_usa_o_mesmo_cofre_e_as_mesmas_particoes() -> None:
    """`worker_runtime` nao e' agente, mas o mecanismo #3 fala do RUNTIME, nao do tipo de principal."""
    settings = _SettingsDouble(cibseven_auth_token=TOKEN_MOTOR, negativa_assinatura_key=CHAVE_NEGATIVA)
    visao = build_worker_credential_view(settings=settings)
    assert visao.get("cibseven_auth_token") == TOKEN_MOTOR
    assert "negativa_assinatura_key" not in visao.list_keys()


def test_uma_credencial_ausente_nao_vira_string_vazia() -> None:
    """ "Nao injetada" e' um estado real (seam bloqueado) — nunca uma credencial que autentica nada."""
    visao = build_agent_credential_view(settings=_SettingsDouble(), agent_id="helena")
    assert visao.list_keys() == frozenset()
    assert tool_registry.agent_credential(visao, "cibseven_auth_token") is None
    visao_vazia = build_agent_credential_view(
        settings=_SettingsDouble(cibseven_auth_token=""), agent_id="helena"
    )
    assert visao_vazia.list_keys() == frozenset()


def test_as_duas_tabelas_de_credencial_sao_disjuntas() -> None:
    """Um campo em ambas as tabelas seria "agente-visivel E humano-restrito" — nao existe."""
    assert not set(AGENT_CREDENTIAL_FIELDS) & set(HUMAN_CREDENTIAL_FIELDS)
    assert set(HUMAN_CREDENTIAL_FIELDS.values()) == set(HumanCredentialPartition)


# =================================================================================================
# 2. A raiz de composicao RECUSA (a prova de que a fiacao existe)
# =================================================================================================


def test_build_agent_seams_recusa_uma_composicao_que_vazaria_credencial_humana(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se um campo humano-restrito for declarado agente-visivel, a construcao LEVANTA.

    O `monkeypatch` encena exatamente a edicao futura que a guarda existe para pegar: alguem
    acrescenta `negativa_assinatura_key` a tabela de credenciais de agente. A recusa vem de dentro
    de `build_agent_seams`, e e' por isso que este teste fica VERMELHO se a linha
    `credentials = build_agent_credential_view(...)` sair de la' — sem ela a composicao segue,
    monta os seams e a garantia da ADR-0005 volta a nao ter raiz alguma.
    """
    monkeypatch.setitem(
        tool_registry.AGENT_CREDENTIAL_FIELDS,
        "negativa_assinatura_key",
        "negativa_assinatura_key",
    )
    settings = _SettingsDouble(negativa_assinatura_key=CHAVE_NEGATIVA)
    with pytest.raises(CredentialSeparationError) as excinfo:
        build_agent_seams(settings=settings, agent_id="helena")
    assert "negativa_assinatura_key" in str(excinfo.value)


def test_a_raiz_do_worker_tambem_pega_o_token_do_motor_pelo_cofre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`worker_runtime/service.py::_engine_credential` — a MESMA porta, nao um `getattr` paralelo.

    Fica VERMELHO se aquele helper voltar a ler `settings.cibseven_auth_token_value()` direto: sem
    o cofre no caminho, a guarda de separacao nao e' consultada e nada levanta.
    """
    from maezo.runtime.worker_runtime.service import _engine_credential
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    settings = WorkerRuntimeSettings(cibseven_auth_token=TOKEN_MOTOR)
    assert _engine_credential(settings) == TOKEN_MOTOR

    monkeypatch.setitem(
        tool_registry.AGENT_CREDENTIAL_FIELDS,
        "negativa_assinatura_key",
        "negativa_assinatura_key",
    )
    with pytest.raises(CredentialSeparationError):
        _engine_credential(settings)


def test_a_recusa_tambem_vale_para_o_construtor_de_visao_isolado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        tool_registry.AGENT_CREDENTIAL_FIELDS,
        "fraude_investigacao_key",
        "fraude_investigacao_key",
    )
    with pytest.raises(CredentialSeparationError):
        build_agent_credential_view(settings=_SettingsDouble(), agent_id="helena")


# =================================================================================================
# 3. Nao-vacuidade: a credencial do cofre chega mesmo ao transporte gated
# =================================================================================================


def test_o_token_do_motor_que_o_seam_recebe_veio_do_cofre() -> None:
    deps = build_agent_seams(settings=_SettingsDouble(cibseven_auth_token=TOKEN_MOTOR), agent_id="helena")
    dmn_interno = deps["dmn"].inner
    assert dmn_interno._headers["Authorization"] == f"Bearer {TOKEN_MOTOR}"
    engine_interno = deps["cibseven"].inner
    assert engine_interno._client.headers["authorization"] == f"Bearer {TOKEN_MOTOR}"


def test_nenhuma_credencial_humana_alcanca_um_seam_montado() -> None:
    """Varredura do grafo de objetos que a raiz devolve: a chave NEGATIVA nao esta em lugar nenhum."""
    deps = build_agent_seams(
        settings=_SettingsDouble(cibseven_auth_token=TOKEN_MOTOR, negativa_assinatura_key=CHAVE_NEGATIVA),
        agent_id="helena",
    )
    alcancado = _strings_alcancaveis(deps)
    assert not any(CHAVE_NEGATIVA in texto for texto in alcancado)
    # Nao-vacuidade da varredura: ela ENCONTRA a credencial que legitimamente flui.
    assert any(TOKEN_MOTOR in texto for texto in alcancado)


def _objetos_alcancaveis(raiz: object, *, profundidade: int = 10) -> list[object]:
    """Todo objeto alcancavel a partir de `raiz` por atributos/slots/itens, ate' `profundidade`.

    Cobre `__slots__` de TODA a MRO (os wrappers gated usam slots, e um walk que so' olhasse
    `type(obj).__slots__` perderia `GatedSeam._inner` — foi assim que a primeira versao deste teste
    ficou vacuamente verde).
    """
    vistos: set[int] = set()
    encontrados: list[object] = []

    def caminhar(obj: object, nivel: int) -> None:
        if nivel > profundidade or id(obj) in vistos:
            return
        vistos.add(id(obj))
        encontrados.append(obj)
        if isinstance(obj, (str, bytes, bytearray, int, float, bool, type(None))):
            return
        if isinstance(obj, dict):
            for chave, valor in obj.items():
                caminhar(chave, nivel + 1)
                caminhar(valor, nivel + 1)
            return
        if isinstance(obj, (list, tuple, set, frozenset)):
            for item in obj:
                caminhar(item, nivel + 1)
            return
        estado = getattr(obj, "__dict__", None)
        if isinstance(estado, dict):
            caminhar(estado, nivel + 1)
        for classe in type(obj).__mro__:
            slots = getattr(classe, "__slots__", ()) or ()
            for nome in (slots,) if isinstance(slots, str) else slots:
                caminhar(getattr(obj, nome, None), nivel + 1)

    caminhar(raiz, 0)
    return encontrados


def _strings_alcancaveis(raiz: object, *, profundidade: int = 10) -> list[str]:
    """Os textos do grafo de :func:`_objetos_alcancaveis`, com `bytes` decodificados.

    Os headers do `httpx` sao `bytes`; sem decodificar, uma credencial que chegou ao transporte
    passaria despercebida e a varredura ficaria vacuamente verde.
    """
    textos: list[str] = []
    for obj in _objetos_alcancaveis(raiz, profundidade=profundidade):
        if isinstance(obj, str):
            textos.append(obj)
        elif isinstance(obj, (bytes, bytearray)):
            textos.append(bytes(obj).decode("utf-8", "replace"))
    return textos


# =================================================================================================
# 4. AF-14-F1 — a TERCEIRA raiz: a ponte de notificacoes
# =================================================================================================


def test_a_raiz_da_ponte_de_notificacoes_pega_o_token_do_motor_pelo_cofre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`notifications_bridge.py::_engine_credential` — a MESMA porta das outras duas raizes.

    Fica VERMELHO se aquela funcao voltar a ler `settings.cibseven_auth_token` direto: sem o cofre
    no caminho a guarda de separacao nao e' consultada e nada levanta. Era exatamente esse o estado
    da ponte antes de AF-14-F1, com a docstring de `build_worker_credential_view` ja' afirmando
    que ela estava coberta.
    """
    from maezo.platform.integrations.notifications_bridge import (
        NotificationsBridgeSettings,
        _engine_credential,
    )

    settings = NotificationsBridgeSettings(cibseven_auth_token=TOKEN_MOTOR)
    assert _engine_credential(settings) == TOKEN_MOTOR

    monkeypatch.setitem(
        tool_registry.AGENT_CREDENTIAL_FIELDS,
        "negativa_assinatura_key",
        "negativa_assinatura_key",
    )
    with pytest.raises(CredentialSeparationError):
        _engine_credential(settings)


def test_a_ponte_recusa_subir_quando_a_composicao_vazaria_credencial_humana(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-2 nesta raiz: `build_bridge` NAO captura a recusa — a ponte nao sobe, e' isso.

    Mesma forma fail-closed do `DATABASE_URL` ausente que esta raiz ja' documentava: "replica nao
    pronta", nunca "replica pronta e vazando".
    """
    from maezo.platform.integrations.notifications_bridge import NotificationsBridgeSettings, build_bridge

    settings = NotificationsBridgeSettings(cibseven_auth_token=TOKEN_MOTOR, database_url=BANCO_DE_TESTE)
    monkeypatch.setitem(
        tool_registry.AGENT_CREDENTIAL_FIELDS,
        "fraude_investigacao_key",
        "fraude_investigacao_key",
    )
    with pytest.raises(CredentialSeparationError):
        build_bridge(settings)


def test_o_seam_gated_da_ponte_carrega_o_bearer_que_veio_do_cofre() -> None:
    """Nao-vacuidade da fiacao da ponte: o token do cofre chega mesmo ao transporte gated.

    Fica VERMELHO se `auth_token=_engine_credential(settings)` sair de `build_bridge`.
    """
    from maezo.platform.integrations.notifications_bridge import NotificationsBridgeSettings, build_bridge

    settings = NotificationsBridgeSettings(cibseven_auth_token=TOKEN_MOTOR, database_url=BANCO_DE_TESTE)
    _ponte, transporte, _sink = build_bridge(settings)
    assert transporte.inner._client.headers["authorization"] == f"Bearer {TOKEN_MOTOR}"


def test_nenhum_cofre_e_alcancavel_a_partir_do_seam_da_ponte() -> None:
    """A separacao e' ESTRUTURAL: o seam recebe a VISAO, nunca o `CredentialVault`.

    A visao nao tem referencia de volta ao cofre, logo nao ha' caminho de atributo de um seam ate'
    `CredentialVault.get_human_credential`. Esta e' a metade que uma varredura por VALOR nao pode
    provar aqui — `NotificationsBridgeSettings` nao tem (e nao deve ter) campo de particao humana,
    entao procurar por uma chave NEGATIVA nesta raiz seria vacuo.
    """
    from maezo.platform.integrations.notifications_bridge import NotificationsBridgeSettings, build_bridge

    settings = NotificationsBridgeSettings(cibseven_auth_token=TOKEN_MOTOR, database_url=BANCO_DE_TESTE)
    _ponte, transporte, _sink = build_bridge(settings)
    alcancados = _objetos_alcancaveis(transporte)
    assert not any(isinstance(obj, CredentialVault) for obj in alcancados)
    assert not any(isinstance(obj, AgentCredentialView) for obj in alcancados)
    # Nao-vacuidade da varredura: ela ALCANCA a credencial que legitimamente flui.
    assert any(TOKEN_MOTOR in texto for texto in _strings_alcancaveis(transporte))


def test_a_visao_do_principal_da_ponte_tambem_exclui_a_particao_humana() -> None:
    """O principal da ponte usa o mesmo cofre e as mesmas tabelas fechadas do `worker_runtime`."""
    settings = _SettingsDouble(cibseven_auth_token=TOKEN_MOTOR, negativa_assinatura_key=CHAVE_NEGATIVA)
    visao = build_worker_credential_view(settings=settings, principal=BRIDGE_PRINCIPAL)
    assert visao.get("cibseven_auth_token") == TOKEN_MOTOR
    assert "negativa_assinatura_key" not in visao.list_keys()


# =================================================================================================
# 5. AF-14-F2 — a tabela de credenciais de agente descreve campos que EXISTEM
# =================================================================================================


def test_todo_campo_agente_visivel_existe_em_alguma_classe_de_settings_real() -> None:
    """A docstring de `AGENT_CREDENTIAL_FIELDS` afirma isso; ate' AF-14-F2 era falso.

    `fhir_auth_token` estava na tabela e nao existia em classe de settings alguma nem em lugar
    nenhum de `src/` — uma linha que nunca poderia carregar credencial, afirmada como se pudesse.
    Este teste e' a afirmacao virando prova: nenhuma linha da tabela pode voltar a ser decorativa.
    """
    from maezo.gateway.settings import GatewaySettings
    from maezo.platform.integrations.notifications_bridge import NotificationsBridgeSettings
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    classes = (
        AgentRuntimeSettings,
        GatewaySettings,
        NotificationsBridgeSettings,
        WhatsAppWebhookSettings,
        WorkerRuntimeSettings,
    )
    orfaos = [
        campo
        for campo in AGENT_CREDENTIAL_FIELDS
        if not any(campo in classe.model_fields for classe in classes)
    ]
    assert orfaos == [], f"campos agente-visiveis sem classe de settings real: {orfaos}"


def test_nenhum_campo_humano_restrito_existe_ainda_numa_classe_de_settings() -> None:
    """A outra metade da mesma honestidade: `HUMAN_CREDENTIAL_FIELDS` e' DESTINO declarado.

    Nenhuma implantacao injeta chave de negativa/fraude hoje. Se um dia injetar, este teste fica
    VERMELHO e obriga quem injetou a olhar a tabela — que e' exatamente o efeito desejado, porque
    a partir dali a particao humana deixa de ser hipotetica.
    """
    from maezo.gateway.settings import GatewaySettings
    from maezo.platform.integrations.notifications_bridge import NotificationsBridgeSettings
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    classes = (
        AgentRuntimeSettings,
        GatewaySettings,
        NotificationsBridgeSettings,
        WhatsAppWebhookSettings,
        WorkerRuntimeSettings,
    )
    presentes = [
        campo for campo in HUMAN_CREDENTIAL_FIELDS if any(campo in classe.model_fields for classe in classes)
    ]
    assert presentes == [], (
        f"campo humano-restrito agora existe em settings: {presentes} — a particao deixou de ser "
        "hipotetica; revisar a custodia (RN 259) antes de relaxar esta prova."
    )


# =================================================================================================
# 6. AF-14-F3 — a guarda de vazamento da visao NAO e' codigo morto
# =================================================================================================


def test_a_guarda_de_vazamento_levanta_se_o_cofre_for_achatado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guarda `leaked` de `build_agent_credential_view`, exercitada de verdade.

    A docstring dela dizia ser "a assercao que fica VERMELHA se uma edicao futura 'simplificar' o
    cofre numa unica dict" — e nenhuma prova a exercitava: o mutante `leaked = []` passava com o
    arquivo inteiro verde. Aqui a simplificacao e' ENCENADA (a `get_agent_view` achatada) e a
    guarda tem de recusar. Mutante `leaked = []` -> VERMELHO neste teste.
    """
    settings = _SettingsDouble(cibseven_auth_token=TOKEN_MOTOR, negativa_assinatura_key=CHAVE_NEGATIVA)

    def _visao_achatada(self: CredentialVault, agent_id: str) -> AgentCredentialView:
        plano = dict(self._agent_credentials.get(agent_id, {}))
        for particao in self._human_credentials.values():
            plano.update(particao)
        return AgentCredentialView(agent_id, plano)

    monkeypatch.setattr(CredentialVault, "get_agent_view", _visao_achatada)
    with pytest.raises(CredentialSeparationError) as excinfo:
        build_agent_credential_view(settings=settings, agent_id="helena")
    assert "negativa_assinatura_key" in str(excinfo.value)


# =================================================================================================
# 7. A superficie inteira: nenhuma leitura da credencial do motor por fora do cofre
# =================================================================================================


def test_o_transporte_de_tarefa_externa_do_worker_tambem_passa_pelo_cofre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CibSevenWorkerTransport` nao e' seam gated, mas carrega a MESMA credencial do motor.

    Deixa-lo lendo `settings.cibseven_auth_token_value()` direto manteria um `getattr` que tabela
    nenhuma governa DENTRO do proprio arquivo que esta mudanca reescreveu — o defeito de AF-14 em
    miniatura, uma raiz de cada vez, que foi exatamente como AF-14-F1 aconteceu.

    A assercao e' sobre o TEXTO da funcao porque `_bring_up_dependencies` nao pode ser exercitada
    aqui: ela sobe Kafka, banco e servidor de health, e cada bloco e' isolado por `try/except`, de
    modo que um transporte construido com a credencial errada nao levantaria nada observavel. O
    repo ja' usa varredura de fonte como fence (`scripts/ci/check_start_process_fence.py`,
    `check_effect_chokepoint_fence.py`). Fica VERMELHO se aquele bloco voltar as settings.
    """
    import inspect

    import maezo.runtime.worker_runtime.service as servico
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    fonte = inspect.getsource(servico._bring_up_dependencies)
    bloco = fonte[fonte.index("CibSevenWorkerTransport(") :]
    # Ate' o parentese que FECHA a chamada (o unico `)` seguido de quebra de linha no bloco).
    bloco = bloco[: bloco.index(")\n")]
    assert "auth_token=_engine_credential(settings)" in bloco
    assert "cibseven_auth_token" not in bloco

    # E o valor entregue e' o MESMO de antes: nenhuma mudanca de comportamento em deployment algum.
    settings = WorkerRuntimeSettings(cibseven_auth_token=TOKEN_MOTOR)
    assert servico._engine_credential(settings) == settings.cibseven_auth_token_value()

    # E a guarda de separacao passa a valer tambem para este transporte.
    monkeypatch.setitem(
        tool_registry.AGENT_CREDENTIAL_FIELDS,
        "negativa_assinatura_key",
        "negativa_assinatura_key",
    )
    with pytest.raises(CredentialSeparationError):
        servico._engine_credential(settings)


#: Todo nome de campo que carrega a credencial do motor. O casamento e' por PREFIXO, entao
#: `cibseven_auth_token_value` (a propriedade) entra sem precisar de linha propria.
_PREFIXO_CREDENCIAL = "cibseven_auth_token"

#: As unicas (caminho de modulo, funcao) autorizadas a tocar o campo cru. Par, e nao so' o NOME da
#: funcao: uma exencao por nome valeria para qualquer `_engine_credential` que alguem escrevesse em
#: qualquer modulo — que e' a forma exata de como AF-14-F1 aconteceu (uma raiz por vez).
_EXENCOES: frozenset[tuple[str, str]] = frozenset(
    {("runtime/worker_runtime/settings.py", "cibseven_auth_token_value")}
)


def _fugas_de_credencial(codigo: str, *, caminho: str) -> list[str]:
    """Toda leitura LITERAL da credencial do motor em `codigo`, fora das exencoes.

    Cobre, por AST (nunca por texto — prosa e comentario citam o nome do campo o tempo todo):

      * acesso de atributo: `settings.cibseven_auth_token`, `settings.cibseven_auth_token_value()`;
      * `getattr`/`hasattr` com o nome LITERAL: `getattr(settings, "cibseven_auth_token", None)`;
      * subscrito com string CONSTANTE: `dados["cibseven_auth_token"]`, e portanto tambem
        `settings.model_dump()["cibseven_auth_token"]` e `settings.__dict__["cibseven_auth_token"]`.

    NAO cobre — e este e' o residual, dito aqui e nos tres textos do codigo que citam este fence:
    um nome de campo montado ou escolhido em tempo de execucao (`getattr(settings, campo, None)`
    com `campo` variavel). E' deliberado: e' exatamente assim que
    `tool_registry.build_credential_vault` le' a tabela `AGENT_CREDENTIAL_FIELDS`, isto e', a
    propria porta do cofre. Um fence que proibisse `getattr` dinamico proibiria o cofre.

    Tambem NAO sao fuga, e nao sao flagradas: uma chave de dict literal
    (`AGENT_CREDENTIAL_FIELDS = {"cibseven_auth_token": ...}`) e o nome passado a
    `agent_credential(view, "cibseven_auth_token")` — nenhuma le' settings; ambas nomeiam a chave
    DENTRO do cofre, que e' o caminho autorizado.
    """
    fugas: list[str] = []

    def _e_o_campo(valor: object) -> bool:
        return isinstance(valor, str) and valor.startswith(_PREFIXO_CREDENCIAL)

    class _Varredura(ast.NodeVisitor):
        def _funcao(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            if (caminho, node.name) in _EXENCOES:
                return
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._funcao(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._funcao(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if _e_o_campo(node.attr):
                fugas.append(f"{caminho}:{node.lineno}: .{node.attr}")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            alvo = node.func
            if (
                isinstance(alvo, ast.Name)
                and alvo.id in {"getattr", "hasattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and _e_o_campo(node.args[1].value)
            ):
                fugas.append(f"{caminho}:{node.lineno}: {alvo.id}(..., {node.args[1].value!r})")
            self.generic_visit(node)

        def visit_Subscript(self, node: ast.Subscript) -> None:
            indice = node.slice
            if isinstance(indice, ast.Constant) and _e_o_campo(indice.value):
                fugas.append(f"{caminho}:{node.lineno}: [{indice.value!r}]")
            self.generic_visit(node)

    _Varredura().visit(ast.parse(codigo))
    return sorted(fugas)


#: Ids dos casos abaixo. Nomeados aqui (e sem espaco) porque o node id entra no hash do ledger —
#: ver o comentario no `ids=` do primeiro parametrize.
_IDS_DE_IDIOMA = frozenset(
    {"atributo", "propriedade", "getattr", "hasattr", "model_dump", "dunder_dict", "subscrito"}
)
_IDS_DE_NAO_FUGA = frozenset({"getattr_dinamico_do_cofre", "chave_de_dict_literal", "chave_do_cofre"})


@pytest.mark.parametrize(
    ("idioma", "codigo"),
    [
        ("atributo", "def f(settings):\n    return settings.cibseven_auth_token\n"),
        ("propriedade", "def f(settings):\n    return settings.cibseven_auth_token_value()\n"),
        ("getattr", 'def f(settings):\n    return getattr(settings, "cibseven_auth_token", None)\n'),
        ("hasattr", 'def f(settings):\n    return hasattr(settings, "cibseven_auth_token")\n'),
        ("model_dump", 'def f(settings):\n    return settings.model_dump()["cibseven_auth_token"]\n'),
        ("dunder_dict", 'def f(settings):\n    return settings.__dict__["cibseven_auth_token"]\n'),
        ("subscrito", 'def f(dados):\n    return dados["cibseven_auth_token_value"]\n'),
    ],
    # IDs EXPLICITOS, sem espaco em branco. O id automatico do pytest embutiria o snippet inteiro
    # (com `\n` e indentacao) no node id, e a receita de hash do ledger casa
    # `^(\S+::\S+ (?:PASSED|FAILED)...)$` (`scripts/ci/check_evidence_ledger_hashes.py`): um node
    # id com espaco NAO casa, e a linha some do hash em silencio. Foi assim que este arquivo passou
    # a declarar 20 linhas de resultado para 30 testes antes desta correcao.
    ids=lambda valor: valor if valor in _IDS_DE_IDIOMA else "",
)
def test_o_fence_de_credencial_pega_cada_idioma_de_leitura(idioma: str, codigo: str) -> None:
    """Cada idioma que a primeira versao do fence deixava passar, agora VERMELHO.

    A primeira versao so' olhava acesso de atributo: `getattr(settings, "cibseven_auth_token")`,
    `settings.model_dump()[...]` e `settings.__dict__[...]` atravessavam sem ser vistos, e a
    exencao casava pelo NOME da funcao em qualquer modulo. Estes casos sao a prova de que nao
    atravessam mais; remover o ramo correspondente de `_fugas_de_credencial` deixa um deles
    VERMELHO.
    """
    assert _fugas_de_credencial(codigo, caminho="qualquer/modulo.py"), f"idioma nao detectado: {idioma}"


@pytest.mark.parametrize(
    ("idioma", "codigo"),
    [
        ("getattr_dinamico_do_cofre", "def f(s, campo):\n    return getattr(s, campo, None)\n"),
        ("chave_de_dict_literal", 'TABELA = {"cibseven_auth_token": "cibseven_auth_token"}\n'),
        ("chave_do_cofre", 'def f(v):\n    return agent_credential(v, "cibseven_auth_token")\n'),
    ],
    ids=lambda valor: valor if valor in _IDS_DE_NAO_FUGA else "",
)
def test_o_fence_nao_flagra_o_que_nao_le_settings(idioma: str, codigo: str) -> None:
    """O residual e as nao-fugas, escritos como prova para nao virarem folclore."""
    assert _fugas_de_credencial(codigo, caminho="qualquer/modulo.py") == [], idioma


def test_a_exencao_do_fence_e_por_par_modulo_funcao_e_nao_por_nome() -> None:
    """Uma exencao por NOME valeria para qualquer `_engine_credential` em qualquer modulo.

    Este teste e' a diferenca entre as duas leituras: o MESMO corpo, no modulo exento e num modulo
    qualquer.
    """
    codigo = "def cibseven_auth_token_value(self):\n    return self.cibseven_auth_token\n"
    assert _fugas_de_credencial(codigo, caminho="runtime/worker_runtime/settings.py") == []
    assert _fugas_de_credencial(codigo, caminho="runtime/outro/settings.py") != []


def test_nenhuma_leitura_da_credencial_do_motor_escapa_do_cofre_em_src() -> None:
    """A afirmacao das docstrings de `build_worker_credential_view` e de `_engine_credential`,
    virada prova sobre TODO `src/`.

    O que este fence garante, exatamente (nem mais): nenhuma leitura LITERAL da credencial do motor
    — atributo, `getattr`/`hasattr` com nome literal, ou subscrito com string constante, inclusive
    via `model_dump()`/`__dict__` — existe em `src/` fora do par (modulo, funcao) exento. O que ele
    NAO ve': um nome montado em tempo de execucao. Ver `_fugas_de_credencial` para por que essa
    lacuna e' deliberada (e' por `getattr` dinamico que o proprio cofre le' a tabela).

    Uma leitura nova por fora derruba este teste — a unica forma de a garantia nao voltar a erodir
    uma raiz por vez, que foi como AF-14-F1 aconteceu.
    """
    raiz = Path(tool_registry.__file__).resolve().parent.parent
    fugas: list[str] = []
    fugas_sem_exencao: list[str] = []
    arquivos_com_o_campo = 0
    exencoes_usadas: set[tuple[str, str]] = set()
    for arquivo in sorted(raiz.rglob("*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        if _PREFIXO_CREDENCIAL not in texto:
            continue
        arquivos_com_o_campo += 1
        caminho = arquivo.relative_to(raiz).as_posix()
        deste_arquivo = _fugas_de_credencial(texto, caminho=caminho)
        # O MESMO arquivo com um caminho que nao casa exencao alguma: a diferenca entre os dois e'
        # exatamente o que cada exencao suprime.
        deste_arquivo_sem_exencao = _fugas_de_credencial(texto, caminho=f"__sem_exencao__/{caminho}")
        fugas.extend(deste_arquivo)
        fugas_sem_exencao.extend(deste_arquivo_sem_exencao)
        if len(deste_arquivo_sem_exencao) > len(deste_arquivo):
            exencoes_usadas.update(par for par in _EXENCOES if par[0] == caminho)

    assert arquivos_com_o_campo >= 3, "varredura vacua: nem os arquivos conhecidos foram lidos"
    # Nao-vacuidade da varredura: sem as exencoes ela ENCONTRA leitura — nao esta calada por bug.
    assert fugas_sem_exencao, "varredura vacua: nem a leitura conhecida do settings foi vista"
    assert fugas == [], (
        "leitura da credencial do motor por fora do cofre (ADR-0005 mecanismo #3):\n" + "\n".join(fugas)
    )
    # Nao-vacuidade das exencoes: uma exencao que nao suprime nada e' uma porta aberta esquecida.
    assert exencoes_usadas == _EXENCOES, f"exencao declarada e nao usada: {_EXENCOES - exencoes_usadas}"
