# Proveniência dos transcripts de cosign (fixtures do portão SBOM/assinatura)

Capturados em **2026-10-27** com o cosign real (`GitVersion: v3.1.4`,
`GitCommit: 0e48502cf5ef34e2e60bbd3617aa19945712f705`), contra um registry
`registry:2` local (`127.0.0.1:5000`, HTTP, por isso o `--allow-insecure-registry`
extra — o script de guarda não passa essa flag; no ECR ela não é necessária).
Chaves geradas na hora com `cosign generate-key-pair` (`key-a` = chave certa,
`key-b` = chave errada); assinatura e atestação com as MESMAS flags do
`.github/workflows/supply-chain.yml` (`--use-signing-config=false
--tlog-upload=false --new-bundle-format=false --registry-referrers-mode=legacy`).

Cada `<caso>/{sig,att}.{rc,err,out}` é a saída VERBATIM de UM comando real:

| caso | comando que gerou `sig.*` | que gerou `att.*` |
|---|---|---|
| `ok` | `cosign verify --key key-a.pub` sobre `127.0.0.1:5000/w7/red@sha256:365a051f…` assinado e atestado (rc 0) | `cosign verify-attestation --key key-a.pub --type spdxjson` sobre o mesmo digest (rc 0) |
| `no_signature` | `cosign verify --key key-a.pub` sobre o mesmo digest ANTES de qualquer assinatura (rc 10) | (perna não alcançada pelo guarda; cópia do `ok` para completar o par) |
| `no_sbom` | o mesmo `verify` verde de `ok` (a assinatura existe) | `cosign verify-attestation` após assinar mas ANTES do `attest` (rc 1) |
| `wrong_key` | `cosign verify --key key-b.pub` sobre o digest assinado por `key-a` (rc 12) | (perna não alcançada; cópia do `no_sbom`) |
| `divergent_digest` | `cosign verify --key key-a.pub` sobre `127.0.0.1:5000/w7/other@sha256:ef0e6d90…` — OUTRO digest, sem artefato de assinatura próprio, enquanto `w7/red@365a051f…` estava totalmente assinado e atestado no MESMO registry (rc 10) | (perna não alcançada; cópia do `ok`) |

Os dois digests são de imagens distintas (o `w7/other` foi construído com um
layer próprio só para isso); as tags do registry na hora da captura provam o
binding: `w7/red` tinha `sha256-365a051f….sig` e `.att`; `w7/other` não tinha
nenhuma.

O transcript de sucesso contém o marcador que o veredito do guarda lê
(`The following checks were performed on each of these signatures:`) — o mesmo
wording vale para `verify` e para `verify-attestation` no cosign testado, que é
o que faz o `grep` de `verify_image_signature.sh` funcionar para as duas pernas.

sha256 de cada arquivo (conferir antes de editar qualquer transcript):

```
9415578c3825d59e71997822e8bf235f33f3374310b2388411ae64c7ed3df3ce  divergent_digest/att.err
e378e1070f3a0763fd7dc44d1fe090e2cf604e7e20fe4bc1275da99132021951  divergent_digest/att.out
9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa  divergent_digest/att.rc
c75446368b3b0bf817af44bf39bc95f02f4d6114cd8d11b4dfed8b2c405d737d  divergent_digest/sig.err
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  divergent_digest/sig.out
917df3320d778ddbaa5c5c7742bc4046bf803c36ed2b050f30844ed206783469  divergent_digest/sig.rc
7fc1b052b2669cefc662b87286713ccb4f7adda41c1bc0d9008d853ab459adcc  no_sbom/att.err
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  no_sbom/att.out
4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865  no_sbom/att.rc
0cc53503b54e317862bf5bfe46c11f49d713dfd5aff691dd9abdb80e655a7483  no_sbom/sig.err
27b2bc150ca5597bc2d49407e7f7d8748a8b59f85b7bc62d1e5bb01229fac680  no_sbom/sig.out
9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa  no_sbom/sig.rc
9415578c3825d59e71997822e8bf235f33f3374310b2388411ae64c7ed3df3ce  no_signature/att.err
e378e1070f3a0763fd7dc44d1fe090e2cf604e7e20fe4bc1275da99132021951  no_signature/att.out
9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa  no_signature/att.rc
c75446368b3b0bf817af44bf39bc95f02f4d6114cd8d11b4dfed8b2c405d737d  no_signature/sig.err
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  no_signature/sig.out
917df3320d778ddbaa5c5c7742bc4046bf803c36ed2b050f30844ed206783469  no_signature/sig.rc
9415578c3825d59e71997822e8bf235f33f3374310b2388411ae64c7ed3df3ce  ok/att.err
e378e1070f3a0763fd7dc44d1fe090e2cf604e7e20fe4bc1275da99132021951  ok/att.out
9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa  ok/att.rc
0cc53503b54e317862bf5bfe46c11f49d713dfd5aff691dd9abdb80e655a7483  ok/sig.err
27b2bc150ca5597bc2d49407e7f7d8748a8b59f85b7bc62d1e5bb01229fac680  ok/sig.out
9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa  ok/sig.rc
7fc1b052b2669cefc662b87286713ccb4f7adda41c1bc0d9008d853ab459adcc  wrong_key/att.err
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  wrong_key/att.out
4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865  wrong_key/att.rc
e5bd96e55b6367e0b27a3a95a3e6e051edae831d37f565874657fb5ace8e4575  wrong_key/sig.err
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  wrong_key/sig.out
a1fb50e6c86fae1679ef3351296fd6713411a08cf8dd1790a4fd05fae8688164  wrong_key/sig.rc
```
