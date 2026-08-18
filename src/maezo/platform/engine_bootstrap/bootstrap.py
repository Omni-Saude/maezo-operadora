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


@dataclass
class Plano:
    """O que será feito. Existe para poder ser IMPRESSO antes de ser executado."""

    admin_a_criar: str | None = None
    admin_ja_existe: bool = False
    usuarios_a_apagar: list[str] = field(default_factory=list)
    grupos_a_apagar: list[str] = field(default_factory=list)
    deployments_a_apagar: list[tuple[str, str]] = field(default_factory=list)
    deployments_preservados: list[tuple[str, str]] = field(default_factory=list)


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

    return plano


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
        _exigir_ok(
            http.delete(
                f"{base}/deployment/{did}",
                params={"cascade": "true", "skipCustomListeners": "true"},
            ),
            f"apagar deployment {did[:8]}",
        )
        print(f"  apagado deployment {did[:8]} ({nome!r})")

    # 4) Usuários e grupos de demonstração.
    for uid in plano.usuarios_a_apagar:
        _exigir_ok(http.delete(f"{base}/user/{uid}"), f"apagar usuario {uid}")
        print(f"  apagado usuário {uid}")
    for gid in plano.grupos_a_apagar:
        _exigir_ok(http.delete(f"{base}/group/{gid}"), f"apagar grupo {gid}")
        print(f"  apagado grupo {gid}")


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
