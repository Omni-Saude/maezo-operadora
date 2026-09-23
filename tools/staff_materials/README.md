# `tools/staff_materials` — materiais do plano staff (T1.3)

Plano: `docs/plans/portal-autoridade-nativa-dev.md` (Onda 1, T1.3; Onda 2). Decisões: ADR-0060 e
D-F do plano. Nada aqui fala com AWS, banco ou rede.

**Só roda em POSIX (Linux, WSL ou container).** No Windows, `chmod 0400/0700` não muda a ACL:
`D:\` e `D:\tmp` herdam `Usuários autenticados:(M)`. Por isso a ferramenta recusa antes de
gravar qualquer coisa.

## Quem roda o quê

| Comando | Quem | Onde | O que faz |
|---|---|---|---|
| `python -m tools.staff_materials generate --spec S --out D` | engenharia | estação da engenharia | chaves Ed25519 dos 5 papéis, 2 CAs nativas (chaves das CAs não são gravadas), certificados de servidor e de cliente com AKI/SKI, DSNs com senha gerada e verificador SCRAM, rascunho da designação **sem assinatura** |
| `python -m tools.staff_materials verify --bundle B --pins P` | engenharia ou aprovador | qualquer | roda o **loader do portal** (`decode_bundle`) com os pins que o aprovador escreveu |
| `python -m tools.staff_materials verify --manifest M --print-manifest-digest` | aprovador | máquina dele | o `staff_public_manifest_sha256`, calculado por último |
| `python -m tools.staff_materials.approver root-keygen --out D` | **aprovador** | **máquina dele** | par Ed25519 `installation-root`, com a privada **cifrada por senha** por padrão (`--no-encrypt` só por decisão explícita); ela não sai da máquina dele e a saída fica fora do repositório |
| `python -m tools.staff_materials.approver sign-designation ...` | **aprovador** | **máquina dele** | mostra cada entrada; só assina com `--confirm-digest` igual ao digest exibido |
| `python -m tools.staff_materials.approver sign-admission ...` | **aprovador** | **máquina dele** | assina o `portal-read-admission.v1` (T1.7) no domínio `maezo/portal-read-admission/v1\0` |

Nenhum agente roda os comandos do `approver`. O módulo de engenharia não importa o do aprovador
(`tests/unit/staff_materials/test_separation.py`).

## Saída do `generate` (diretório novo, 0700, fora do repositório)

- `portal/` — 10 dos 12 arquivos do pacote staff do portal. Faltam `installation-root.der` e
  `installation-proof.json`, que vêm do aprovador.
- `engine/` — chave e certificado do listener mTLS, chave `native_result` e chave de continuidade
  de 32 bytes. A CA dos clientes sai em PEM (`native-client-ca.pem`) e como **truststore PKCS12
  sem senha** (`client-ca.p12`), só com essa CA e sem chave privada. O p12 é montado em
  `/run/maezo/native/client-ca.p12`. Medido na T1.2 (#482): o Tomcat 10.1 com JSSE ignora
  `caCertificateFile`, então o listener mTLS lê a CA de clientes deste arquivo. O
  `public/summary.json` traz `native_client_ca_der_sha256` para conferência.
- `issuer/` — chaves `case_issuer` e `publication_importer` e o certificado de cliente do
  importador (T1.6).
- `dba/role-verifiers.json` — verificadores SCRAM dos dois logins novos (Onda 3).
- `public/summary.json` — o que o aprovador confere contra as fontes independentes da §4.

## O que ainda não existe

- `assemble` v2: monta `portal-staff-material.v2` com `native_schema` e `engine_schema` (`cibseven`, T1.8b). Espera a T1.8/Python.
- O shape fechado do `portal-read-admission.v1` é da T1.7a. O `sign-admission` exige só o que o
  plano §3.1 já fixa e mostra o resto ao aprovador.
