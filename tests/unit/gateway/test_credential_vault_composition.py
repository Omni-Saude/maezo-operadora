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

AS TRES RAIZES (AF-14-F1). Existem exatamente TRES construcoes de seam gated contra o motor na
arvore, e as tres passam pelo cofre: `gateway/tool_registry.py::build_agent_seams`,
`runtime/worker_runtime/service.py::_engine_credential` e
`platform/integrations/notifications_bridge.py::_engine_credential`. A terceira ficou de fora da
primeira versao desta mudanca — lia `settings.cibseven_auth_token` direto — enquanto a docstring
de `build_worker_credential_view` ja' a nomeava. Cada raiz tem aqui a sua prova: a recusa
(`CredentialSeparationError`) e a entrega do Bearer do motor.
"""

from __future__ import annotations

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
