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
| `python -m tools.staff_materials native-secret --materials G --approver A --input I --out D [--human-keys K]` | engenharia | estação da engenharia | segredo `engine/native-materials` da Onda 4; com `--human-keys` (o `human-keys-summary.json` abaixo) a chave de leitura e a de comando saem do resumo, não da entrada, e o `client-ca.p12` soma a CA de clientes do pacote humano |
| `python -m tools.staff_materials human-bundle keys --spec S --out D` | engenharia | estação da engenharia | 1º tempo do pacote `portal-human-material.v1` do job (T1.5): 3 chaves Ed25519 por propósito, CA de clientes própria (chave não gravada), certificados `read`/`command`, chaves de cursor; `private/` 0400 e `public/human-keys-summary.json` (SPKIs, fingerprints, peers) |
| `python -m tools.staff_materials human-bundle package --spec S --keys D --materials G --approver A --dsns N --out P` | engenharia | estação da engenharia | 2º tempo: `P/current/` (0500, 15 arquivos 0400) com a raiz PÚBLICA e o `human-read-admission.json` já assinado do aprovador; passa pelo `verify_materials` do job antes de gravar e imprime `MAEZO_HUMAN_MATERIAL_VERSION_ID` e `MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256` |
| `python -m tools.staff_materials.approver root-keygen --out D` | **aprovador** | **máquina dele** | par Ed25519 `installation-root`, com a privada **cifrada por senha** por padrão (`--no-encrypt` só por decisão explícita); ela não sai da máquina dele e a saída fica fora do repositório |
| `python -m tools.staff_materials.approver sign-designation ...` | **aprovador** | **máquina dele** | mostra cada entrada; só assina com `--confirm-digest` igual ao digest exibido |
| `python -m tools.staff_materials.approver sign-admission ...` | **aprovador** | **máquina dele** | assina o `portal-read-admission.v1` (T1.7) no domínio `maezo/portal-read-admission/v1\0` |
| `python -m tools.staff_materials.approver sign-human-admission --record F --root-key K --out human-read-admission.json` | **aprovador** | **máquina dele** | mostra o `ReadAdmission` do pacote humano e o digest (sai 2); com `--confirm-digest` grava o `portal-human-read-admission.v1` (Ed25519 sobre o JCS do `record`, sem domínio), que o `human-bundle package` recebe em `--approver` |

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

## `assemble` (v2)

`python -m tools.staff_materials assemble --materials <saida do generate> --spec <spec> --approver <dir> --input <staff-materials-assemble.v1> --out <dir novo>`
monta `portal-staff-material.v2` (`native_schema`, `engine_schema`). Do aprovador entram só
`installation-root.der` (chave PÚBLICA) e `installation-proof.json`; a raiz privada nunca passa
aqui. Recusa se a designação não for a do `generate`, se `read_requester` não tiver
`operations ["detail","list"]` ou se o `identity_verifier` não tiver chave própria (D-H.2).
O manifesto passa pelo `PublicManifest` do loader antes de sair; confira com o `verify` antes de pinar.

## O que ainda não existe

- A `audience` das chaves humanas é `OpaqueRef` no loader do job (sem `/`): a forma URL da D-L
  (`https://engine-native...`) é recusada pelo spec do `human-bundle`; no dev vale `engine-native.maezo-operadora-dev.internal` (emenda D-L de 24/09).
- O shape fechado do `portal-read-admission.v1` é da T1.7a. O `sign-admission` exige só o que o
  plano §3.1 já fixa e mostra o resto ao aprovador.
