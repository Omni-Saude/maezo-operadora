"""Bootstrap de identidade do engine BPMN: administrador real, sem legado de demonstração.

A imagem oficial do CIB Seven embute um showcase que cria `demo`, `john`, `mary` e
`peter` — e põe `demo` no grupo `camunda-admin`. Medido no engine em execução em
18/08/2026. Enquanto isso vale, qualquer pessoa que passe pelo Cloudflare Access tem
**administração do motor de processos**.

`deploy/cibseven/Dockerfile` impede a RECRIAÇÃO (o webapp não existe mais na imagem).
Este módulo limpa o que já está no banco e cria o administrador que substitui o `demo`.

Ordem que importa, e o motivo: o admin real é criado **antes** de o `demo` ser apagado.
Se a ordem invertesse, o Cockpit ficaria sem nenhum membro de `camunda-admin` e o webapp
cairia na tela de "criar o primeiro usuário" — que, atrás do Access, entrega
administração do motor à primeira pessoa que abrir a página.

Além disso, cria o grupo `maezoleitura` com as autorizações de LEITURA do motor. Ele
nasce vazio, e é isso que o torna útil: convidar alguém para o Cockpit passa a ser criar
o usuário da pessoa e pô-la no grupo, em vez de compartilhar a senha do administrador —
que é a única alternativa enquanto `maezoadmin` for o único usuário que existe. Mesmo
desenho dos grupos vazios do AWS Identity Center (`deploy/aws-identity-center/`): a
permissão é revisada agora, a pessoa entra depois.

Uso:

    ENGINE_REST_URL=... ADMIN_USER=... ADMIN_PASSWORD=... \\
        python -m maezo.platform.engine_bootstrap          # DRY RUN: só lista

    CONFIRMAR=1 ... python -m maezo.platform.engine_bootstrap   # executa

Dry run é o padrão de propósito: uma ferramenta que apaga por omissão é uma ferramenta
que alguém roda por engano.
"""

from .bootstrap import main, planejar

__all__ = ["main", "planejar"]
