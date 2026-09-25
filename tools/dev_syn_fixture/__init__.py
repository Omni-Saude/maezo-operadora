"""Fixture SINTETICA do intake AUTH (guia `SYN-`): nucleo compartilhado pelo harness C1 e pelo dev.

- `core`: construcao dos payloads (6 publicacoes, `human-auth-start.v1`, designacoes, qualificacao,
  escalacao) e as operacoes de REST/banco, sem nenhum caminho, host ou segredo embutido;
- `guards`: as cercas fail-closed do uso em dev (conta 203312548462, ambiente `dev`, tenant `amh`,
  guia `^SYN-[A-Z0-9]+$`, nenhuma guia nao-`SYN-` tocada);
- `__main__`: o executor de dev (`python -m tools.dev_syn_fixture`), decisao do dono de 24/09.

O harness (`deploy/c1-local/c1/auth_install.py`, `auth_fixture.py`, `issuer.py`) chama o MESMO `core`
com as referencias do C1 e as cercas proprias dele (Postgres descartavel).
"""
