"""Cria o administrador real do engine e remove o legado de demonstração."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

#: Usuários que o showcase da imagem oficial cria. Lista fechada de propósito: a task não
#: apaga "todos os usuários que não reconheço" — apaga estes, nomeados.
USUARIOS_DE_DEMONSTRACAO: Final[tuple[str, ...]] = ("demo", "john", "mary", "peter")

#: Grupos de fluxo que o mesmo showcase cria. `camunda-admin` NÃO está aqui: é grupo de
#: sistema e continua existindo — o que muda é quem pertence a ele.
GRUPOS_DE_DEMONSTRACAO: Final[tuple[str, ...]] = ("accounting", "management", "sales")

#: O grupo de administração do engine.
GRUPO_ADMIN: Final[str] = "camunda-admin"

#: Grupo de LEITURA do motor — o análogo, aqui dentro, do grupo vazio criado no AWS
#: Identity Center (`maezo-leitura`). Existe para que convidar alguém ao Cockpit não
#: signifique compartilhar a senha do administrador: cria-se o usuário da pessoa e
#: coloca-se neste grupo, sem tocar em permissão.
#:
#: Sem hífen porque a lista branca de identificadores do engine é `[a-zA-Z0-9]+` — o
#: `camunda-admin` é exceção embutida, o resto não pode.
GRUPO_LEITURA: Final[str] = "maezoleitura"

#: O que o grupo de leitura pode: ver o motor, e nada mais.
#:
#: `(resourceType, resourceId, permissões)`. Os tipos são os do engine — 0 aplicação,
#: 6 process-definition, 7 task, 8 process-instance, 9 deployment, 10 decision-definition,
#: 14 decision-requirements-definition.
#:
#: Não há UPDATE, CREATE nem DELETE em lugar nenhum, e `admin` (a aplicação de gestão de
#: usuários) não está na lista: quem entra por aqui NÃO administra identidade. Completar
#: tarefa também fica de fora — quem dirige processo no ambiente é o Canal de Teste, e
#: dar UPDATE em `task` seria permitir concluir autorização de procedimento pela tela.
GRANTS_DO_GRUPO_DE_LEITURA: Final[tuple[tuple[int, str, tuple[str, ...]], ...]] = (
    (0, "cockpit", ("ACCESS",)),
    (0, "tasklist", ("ACCESS",)),
    (6, "*", ("READ", "READ_HISTORY")),
    (7, "*", ("READ",)),
    (8, "*", ("READ",)),
    (9, "*", ("READ",)),
    (10, "*", ("READ",)),
    (14, "*", ("READ",)),
)


@dataclass
class Plano:
    """O que será feito. Existe para poder ser IMPRESSO antes de ser executado."""

    admin_a_criar: str | None = None
    admin_ja_existe: bool = False
    usuarios_a_apagar: list[str] = field(default_factory=list)
    grupos_a_apagar: list[str] = field(default_factory=list)
    deployments_a_apagar: list[tuple[str, str]] = field(default_factory=list)
    deployments_preservados: list[tuple[str, str]] = field(default_factory=list)
    filtros_a_apagar: list[tuple[str, str]] = field(default_factory=list)
    autorizacoes_a_apagar: list[tuple[str, str]] = field(default_factory=list)
    grupo_leitura_a_criar: bool = False
    grants_de_leitura_a_criar: list[str] = field(default_factory=list)


def _base() -> str:
    return os.environ["ENGINE_REST_URL"].rstrip("/")


def _exigir_ok(resposta: httpx.Response, o_que: str) -> None:
    """Levanta com o CORPO da resposta, nao apenas com o codigo.

    Escrito depois de um HTTP 500 em `/user/create` cujo motivo real — "'maezo-admin' is
    not a valid resource identifier", a lista branca do engine rejeita hifen em id — SO'
    aparecia no log do Tomcat. `raise_for_status()` sozinho custou duas idas ao
    CloudWatch para ler o que a propria resposta dizia.
    """
    if resposta.status_code >= 300:
        raise RuntimeError(f"{o_que} falhou: HTTP {resposta.status_code} — {resposta.text[:400]}")


def _apagar(
    http: httpx.Client,
    url: str,
    o_que: str,
    *,
    tolerar_403: str | None = None,
    pendencias: list[str] | None = None,
    **kwargs: Any,
) -> None:
    """DELETE que trata 404 como sucesso e, quando pedido, 403 como pendência.

    404: o plano é montado antes da execução, e a execução muda o mundo — apagar um
    filtro leva junto as autorizações dele, então parte da lista pode já ter sumido
    quando chega a vez dela. Tratar isso como erro faria a ferramenta falhar justamente
    por ter funcionado, e num restore com estado parcial seria a regra, não a exceção.

    403: só para quem passa `tolerar_403`, e com o motivo escrito. Não é para engolir
    erro — é para não deixar um resíduo COSMÉTICO abortar a remoção de permissão, que é
    a parte que importa. A pendência é acumulada e impressa no fim, alta.
    """
    r = http.delete(url, **kwargs)
    if r.status_code == 404:
        print(f"  {o_que}: já não existia")
        return
    if r.status_code == 403 and tolerar_403 is not None:
        print(f"  NAO APAGADO: {o_que} — {tolerar_403}")
        if pendencias is not None:
            pendencias.append(o_que)
        return
    _exigir_ok(r, f"apagar {o_que}")
    print(f"  apagado: {o_que}")


def planejar(http: httpx.Client, *, admin: str, preservar: str) -> Plano:
    """Lê o estado do engine e monta o plano. NÃO altera nada."""
    base = _base()
    plano = Plano()

    usuarios = {u["id"] for u in http.get(base + "/user").json()}
    plano.admin_ja_existe = admin in usuarios
    if not plano.admin_ja_existe:
        plano.admin_a_criar = admin
    plano.usuarios_a_apagar = [u for u in USUARIOS_DE_DEMONSTRACAO if u in usuarios]

    grupos = {g["id"] for g in http.get(base + "/group").json()}
    plano.grupos_a_apagar = [g for g in GRUPOS_DE_DEMONSTRACAO if g in grupos]

    for d in http.get(base + "/deployment").json():
        nome = d.get("name")
        par = (str(d["id"]), str(nome))
        # Preserva por NOME e nunca por exclusão: só o deployment nomeado sobrevive.
        # Um filtro do tipo "apaga o que parece demo" apagaria o nosso no dia em que
        # alguém renomeasse algo.
        if nome == preservar:
            plano.deployments_preservados.append(par)
        else:
            plano.deployments_a_apagar.append(par)

    _planejar_residuo(http, plano, usuarios=usuarios, grupos=grupos)
    _planejar_grupo_de_leitura(http, plano, grupos=grupos)

    return plano


def _planejar_grupo_de_leitura(http: httpx.Client, plano: Plano, *, grupos: set[str]) -> None:
    """O grupo de leitura e os grants que ainda faltam nele.

    Idempotente por comparação, não por tentativa: o engine aceita criar a MESMA
    autorização duas vezes e passa a ter duas linhas equivalentes, então "cria e ignora
    o erro" produziria lixo crescente a cada execução.
    """
    plano.grupo_leitura_a_criar = GRUPO_LEITURA not in grupos

    existentes = {
        (a["resourceType"], str(a["resourceId"]))
        for a in http.get(_base() + "/authorization", params={"maxResults": 2000}).json()
        if a.get("groupId") == GRUPO_LEITURA and a["type"] == 1
    }
    for tipo, alvo, permissoes in GRANTS_DO_GRUPO_DE_LEITURA:
        if (tipo, alvo) not in existentes:
            plano.grants_de_leitura_a_criar.append(f"tipo {tipo} alvo {alvo!r} {list(permissoes)}")


#: `resourceType` de filtro no engine. Os demais tipos não são tratados aqui de
#: propósito — ver o comentário em `_planejar_residuo`.
TIPO_RECURSO_FILTRO: Final[int] = 5


def _planejar_residuo(http: httpx.Client, plano: Plano, *, usuarios: set[str], grupos: set[str]) -> None:
    """Filtros e autorizações que sobram quando um usuário some.

    APAGAR USUÁRIO NO ENGINE NÃO APAGA AS AUTORIZAÇÕES DELE. Medido depois da primeira
    execução deste bootstrap: `demo`, `mary`, `sales`, `accounting` e `management` já não
    existiam e mesmo assim 15 autorizações continuavam gravadas com o nome deles. Uma
    delas dava a `mary` READ e UPDATE em `task` com alvo `*` — TODAS as tarefas do motor,
    incluindo as do fluxo AUTH.

    Isso não é sujeira cosmética: é uma armadilha armada. No dia em que alguém criar um
    usuário chamado `mary` — nome comum, e o engine não avisa que o id já teve
    autorizações — essa pessoa herda a permissão sem ninguém ter concedido nada.

    A regra usa quem SOBREVIVE ao plano, não quem existe agora: na primeira execução o
    `demo` ainda está lá e as autorizações dele já precisam entrar na lista.
    """
    sobrevivem_usuarios = usuarios - set(plano.usuarios_a_apagar)
    sobrevivem_grupos = grupos - set(plano.grupos_a_apagar)

    filtros = http.get(_base() + "/filter").json()
    filtros_sobreviventes = set()
    for f in filtros:
        dono = f.get("owner")
        if dono is not None and dono not in sobrevivem_usuarios:
            plano.filtros_a_apagar.append((str(f["id"]), str(f.get("name"))))
        else:
            filtros_sobreviventes.add(str(f["id"]))

    for a in http.get(_base() + "/authorization", params={"maxResults": 2000}).json():
        aid = str(a["id"])
        principal = a.get("userId") or a.get("groupId")

        if a["type"] == 0:  # GLOBAL: vale para todo usuário autenticado
            # Global sobre filtro que vai deixar de existir é referência pendurada, e
            # some junto. Global sobre QUALQUER OUTRO recurso não é mexido aqui: uma
            # concessão a todo mundo é decisão deliberada de alguém, e uma ferramenta de
            # limpeza que revoga acesso amplo por conta própria derruba o ambiente.
            if a["resourceType"] == TIPO_RECURSO_FILTRO and str(a["resourceId"]) not in filtros_sobreviventes:
                plano.autorizacoes_a_apagar.append((aid, f"global sobre filtro {a['resourceId']}"))
            continue

        if principal in (None, "*"):
            continue

        vivo = principal in sobrevivem_usuarios if a.get("userId") else principal in sobrevivem_grupos
        if not vivo:
            plano.autorizacoes_a_apagar.append(
                (
                    aid,
                    f"{principal!r} -> tipo {a['resourceType']} alvo {a['resourceId']!r} {a['permissions']}",
                )
            )


def _imprimir(plano: Plano, *, executar: bool) -> None:
    modo = "EXECUTANDO" if executar else "DRY RUN (nada será alterado)"
    print("")
    print("=" * 78)
    print(f"BOOTSTRAP DE IDENTIDADE DO ENGINE — {modo}")
    print("=" * 78)
    if plano.admin_ja_existe:
        print("  admin real: JÁ EXISTE (nada a criar)")
    else:
        print(f"  admin real a criar: {plano.admin_a_criar}  (+ membro de {GRUPO_ADMIN})")
    print(f"  usuários de demonstração a apagar: {plano.usuarios_a_apagar or '(nenhum)'}")
    print(f"  grupos de demonstração a apagar:   {plano.grupos_a_apagar or '(nenhum)'}")
    print("  deployments a apagar (cascade):")
    for did, nome in plano.deployments_a_apagar or []:
        print(f"    - {did[:8]}  nome={nome!r}")
    if not plano.deployments_a_apagar:
        print("    (nenhum)")
    print("  deployments PRESERVADOS:")
    for did, nome in plano.deployments_preservados or []:
        print(f"    - {did[:8]}  nome={nome!r}")
    print(f"  filtros orfaos a apagar: {len(plano.filtros_a_apagar)}")
    for fid, nome in plano.filtros_a_apagar:
        print(f"    - {fid[:8]}  nome={nome!r}")
    print(f"  autorizacoes orfas a apagar: {len(plano.autorizacoes_a_apagar)}")
    for aid, desc in plano.autorizacoes_a_apagar:
        print(f"    - {aid[:8]}  {desc}")
    criar = "SIM" if plano.grupo_leitura_a_criar else "ja existe"
    print(f"  grupo de leitura {GRUPO_LEITURA!r}: {criar}")
    print(f"  grants de leitura a criar: {len(plano.grants_de_leitura_a_criar)}")
    for g in plano.grants_de_leitura_a_criar:
        print(f"    - {g}")


def _executar(http: httpx.Client, plano: Plano, *, admin: str, senha: str) -> None:
    base = _base()

    # 1) O admin PRIMEIRO. Ver o docstring do pacote para o motivo da ordem.
    if plano.admin_a_criar:
        _exigir_ok(
            http.post(
                base + "/user/create",
                json={
                    "profile": {"id": admin, "firstName": "Administrador", "lastName": "MAEZO"},
                    "credentials": {"password": senha},
                },
            ),
            f"criar usuario {admin}",
        )
        print(f"  criado usuário {admin}")

    # O PUT de membro NÃO é idempotente: repetido para quem já pertence ao grupo, o
    # engine tenta inserir a mesma chave duas vezes e devolve HTTP 500
    # (ProcessEnginePersistenceException). Uma ferramenta escrita para ser re-executada
    # num restore não pode quebrar na segunda execução — então perguntamos antes.
    ja_membro = {u["id"] for u in http.get(base + "/user", params={"memberOfGroup": GRUPO_ADMIN}).json()}
    if admin in ja_membro:
        print(f"  {admin} já pertence a {GRUPO_ADMIN} (nada a fazer)")
    else:
        _exigir_ok(
            http.put(f"{base}/group/{GRUPO_ADMIN}/members/{admin}"),
            f"tornar {admin} membro de {GRUPO_ADMIN}",
        )
        print(f"  {admin} agora pertence a {GRUPO_ADMIN}")

    # 2) Confirma que o admin ficou mesmo no grupo ANTES de apagar o `demo`. Sem esta
    #    verificação, uma falha silenciosa no passo acima deixaria o engine sem
    #    administrador nenhum.
    membros = {u["id"] for u in http.get(base + "/user", params={"memberOfGroup": GRUPO_ADMIN}).json()}
    if admin not in membros:
        raise RuntimeError(
            f"{admin} não aparece em {GRUPO_ADMIN} depois do PUT — abortando antes de "
            "apagar o usuário de demonstração, para não deixar o engine sem administrador"
        )
    print(f"  verificado: membros de {GRUPO_ADMIN} = {sorted(membros)}")

    # 3) Deployments de demonstração, com cascade (leva as instâncias de exemplo).
    for did, nome in plano.deployments_a_apagar:
        _apagar(
            http,
            f"{base}/deployment/{did}",
            f"deployment {did[:8]} ({nome!r})",
            params={"cascade": "true", "skipCustomListeners": "true"},
        )

    # 4) Usuários e grupos de demonstração.
    for uid in plano.usuarios_a_apagar:
        _apagar(http, f"{base}/user/{uid}", f"usuário {uid}")
    for gid in plano.grupos_a_apagar:
        _apagar(http, f"{base}/group/{gid}", f"grupo {gid}")

    # 5) Resíduo: o que sobra quando um usuário some. Por último de propósito — se algo
    #    acima falhar, a limpeza não roda com um plano feito sobre outro estado.
    #    Filtro é o único recurso desta lista que o engine recusa a apagar por REST sem
    #    usuário autenticado — medido: 403 "The user with id '' does not have 'DELETE'
    #    permission ... of type 'Filter'", inclusive enviando Basic auth, porque o filtro
    #    de autenticação do engine-rest não está habilitado nesta distribuição.
    #    Autorização, usuário, grupo e deployment apagam anônimos (204).
    #
    #    Isso NÃO bloqueia a limpeza: um filtro sem nenhuma autorização que o conceda é
    #    inerte — some da Tasklist de todo mundo que não seja `camunda-admin`. A remoção
    #    definitiva é um clique na Tasklist do admin, ou vem de graça no dia em que o
    #    engine-rest passar a exigir credencial (registrado no plano de acesso).
    pendencias: list[str] = []
    for fid, nome in plano.filtros_a_apagar:
        _apagar(
            http,
            f"{base}/filter/{fid}",
            f"filtro {fid[:8]} ({nome!r})",
            tolerar_403="engine-rest sem usuário autenticado não apaga filtro; apague pela Tasklist",
            pendencias=pendencias,
        )
    for aid, desc in plano.autorizacoes_a_apagar:
        _apagar(http, f"{base}/authorization/{aid}", f"autorização {aid[:8]} ({desc})")

    # 6) O grupo de leitura. Depois da limpeza de propósito: criar permissão antes de
    #    remover a antiga deixaria as duas coexistindo se algo falhasse no meio.
    if plano.grupo_leitura_a_criar:
        _exigir_ok(
            http.post(
                base + "/group/create",
                json={"id": GRUPO_LEITURA, "name": "MAEZO — leitura do motor", "type": "WORKFLOW"},
            ),
            f"criar grupo {GRUPO_LEITURA}",
        )
        print(f"  criado grupo {GRUPO_LEITURA}")

    for tipo, alvo, permissoes in GRANTS_DO_GRUPO_DE_LEITURA:
        if f"tipo {tipo} alvo {alvo!r} {list(permissoes)}" not in plano.grants_de_leitura_a_criar:
            continue
        _exigir_ok(
            http.post(
                base + "/authorization/create",
                json={
                    "type": 1,  # GRANT
                    "permissions": list(permissoes),
                    "groupId": GRUPO_LEITURA,
                    "resourceType": tipo,
                    "resourceId": alvo,
                },
            ),
            f"conceder {list(permissoes)} em tipo {tipo} alvo {alvo!r} a {GRUPO_LEITURA}",
        )
        print(f"  concedido a {GRUPO_LEITURA}: tipo {tipo} alvo {alvo!r} {list(permissoes)}")

    if pendencias:
        print("")
        print("  PENDENTE DE ACAO MANUAL (nao impede o resto):")
        for p in pendencias:
            print(f"    - {p}")


def _conferir(http: httpx.Client, *, admin: str) -> None:
    base = _base()
    print("")
    print("=" * 78)
    print("ESTADO FINAL")
    print("=" * 78)
    usuarios = [u["id"] for u in http.get(base + "/user").json()]
    print(f"  usuários: {usuarios}")
    membros = [u["id"] for u in http.get(base + "/user", params={"memberOfGroup": GRUPO_ADMIN}).json()]
    print(f"  {GRUPO_ADMIN}: {membros}")
    grupos = [g["id"] for g in http.get(base + "/group").json()]
    print(f"  grupos: {grupos}")

    # Conferência do resíduo: um número diferente de zero aqui significa que existe
    # permissão gravada em nome de alguém que não existe — esperando o dia em que
    # alguém criar um usuário com aquele id.
    vivos = set(usuarios) | set(grupos)
    autorizacoes = http.get(base + "/authorization", params={"maxResults": 2000}).json()
    orfas = [
        a
        for a in autorizacoes
        if a["type"] != 0 and (a.get("userId") or a.get("groupId")) not in vivos | {"*", None}
    ]
    print(f"  autorizacoes: {len(autorizacoes)}  (orfas: {len(orfas)})")
    for a in orfas:
        print(f"    ORFA: {a.get('userId') or a.get('groupId')!r} -> {a['resourceId']!r} {a['permissions']}")
    # "visíveis" e não "existentes", e a diferença é a lição: esta chamada é anônima, e o
    # engine filtra o que devolve pela autorização de quem pergunta. Depois de apagar as
    # autorizações do showcase, os filtros dele somem daqui — mas continuam no banco,
    # apenas inertes. Escrever "filtros: []" seria afirmar uma remoção que não houve.
    visiveis = [f.get("name") for f in http.get(base + "/filter").json()]
    print(f"  filtros visíveis sem autenticação: {visiveis}")
    defs: list[dict[str, Any]] = http.get(
        base + "/process-definition", params={"latestVersion": "true", "maxResults": 200}
    ).json()
    nossas = [d["key"] for d in defs if str(d["key"]).startswith("SP-OP")]
    outras = [d["key"] for d in defs if not str(d["key"]).startswith("SP-OP")]
    print(f"  definições SP-OP: {len(nossas)}")
    print(f"  definições NÃO-SP-OP: {outras or '(nenhuma)'}")
    if admin not in membros:
        print("  ATENÇÃO: o administrador real NÃO está no grupo de administração.")


#: O engine tem lista branca de identificadores: `[a-zA-Z0-9]+` (mais o `camunda-admin`
#: embutido). Hifen e ponto sao RECUSADOS com HTTP 500 e uma mensagem que so' aparece no
#: log do Tomcat. Validar aqui transforma isso num erro imediato e legivel.
_ID_VALIDO: Final[str] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def main() -> int:
    admin = os.environ["ADMIN_USER"]
    invalidos = sorted({c for c in admin if c not in _ID_VALIDO})
    if invalidos:
        print(
            f"ADMIN_USER={admin!r} tem caractere que o engine recusa: {invalidos}. "
            "Use apenas letras e digitos (a lista branca do engine e' [a-zA-Z0-9]+)."
        )
        return 1
    senha = os.environ["ADMIN_PASSWORD"]
    preservar = os.environ.get("DEPLOYMENT_PRESERVAR", "maezo-spec-processes")
    executar = os.environ.get("CONFIRMAR", "").strip().lower() in {"1", "true", "yes"}

    with httpx.Client(timeout=30.0) as http:
        plano = planejar(http, admin=admin, preservar=preservar)
        _imprimir(plano, executar=executar)

        if not plano.deployments_preservados:
            print("")
            print(f"  ABORTANDO: nenhum deployment chamado {preservar!r} foi encontrado.")
            print("  Apagar os demais deixaria o engine sem NENHUM processo SP-OP.")
            return 1

        if not executar:
            print("")
            print("  DRY RUN — nada foi alterado. Rode com CONFIRMAR=1 para executar.")
            return 0

        _executar(http, plano, admin=admin, senha=senha)
        _conferir(http, admin=admin)

    return 0
