# Helm — per-tenant agent factory (`maezo-tenant`)

The `maezo-tenant` chart provisions a complete tenant from a single set of values: a namespace,
the shared services (gateway, fhir-sync, webhook-receiver), and a **per-agent runtime** — one
Deployment + ServiceAccount + NetworkPolicy + ConfigMap **per agent**, ranged over `.Values.agents`.

## Per-agent model (ADR-0004 / ADR-0006)

Each entry in `.Values.agents` (`name`, `securityZone`, `replicaCount`, `resources`, `irsaRoleArn`,
`env`, `definitionHash`, `effectiveDefinition`) renders:

- a **Deployment** `agent-<name>` carrying a `maezo.io/phi-zone: "true|false"` pod label;
- a **ServiceAccount** `maezo-agent-<name>` with that agent's own IRSA role (no shared cloud identity);
- a **NetworkPolicy** `agent-<name>-egress` (ADR-0006 HARD guardrail):
  - `securityZone: phi` (rafael, marina) → egress **RESTRICTED** to the approved BR-resident PHI
    endpoints + the in-namespace gateway. **No** general cloud-LLM egress rule.
  - `securityZone: general` (helena) → egress to the pseudonymized cloud-LLM path + MSK + gateway.

## Network-egress enforcement model — read this before editing the NetworkPolicy

Plain Kubernetes `NetworkPolicy` is **IP/CIDR-based**. Its `ipBlock` selector **cannot express
an FQDN/hostname allowlist** — there is no `fqdn:` field. A NetworkPolicy egress rule of
`ipBlock.cidr: 0.0.0.0/0` therefore does **not** restrict anything: it permits egress to the
**entire internet** on that port, including the general cloud LLM. Shipping that for a PHI agent
is a **PHI-isolation no-op** and violates ADR-0006 ("**NetworkPolicy K8s impede egress fora da
allowlist** — garantia de rede").

Consequently this chart **never renders `0.0.0.0/0`** and enforces, at `helm template` time
(fail-closed — rendering ABORTS, so CI catches it before any apply):

1. **FIX 1 — concrete CIDRs, no internet-wide egress.** Every PHI endpoint
   (`networkPolicy.phiZone.brResidentEndpoints[]`) **must** carry a concrete BR-resident `cidr`.
   A missing `cidr`, `0.0.0.0/0`, or `::/0` makes `helm template` **fail**. The same discipline
   applies to general-zone `llmEndpoints[].cidr` and `mskEndpoint.cidr` (the general zone only
   sees pseudonymized data, but `0.0.0.0/0` is still not an allowlist).

   > **Real FQDN allowlisting** (e.g. `api.anthropic.com` whose IPs rotate) requires one of:
   > an **IP-pinned CIDR** for the endpoint's NLB/VIP (what `cidr:` holds here), an **egress
   > proxy** the pods are forced through, or a **Cilium FQDN-aware policy** (`toFQDNs`). Plain
   > NetworkPolicy alone cannot do hostname allowlists — pin the CIDR and/or front it with one
   > of those. The `cidr` values in `values-amh.yaml` are environment-specific; re-pin per VPC.

2. **FIX 2 — no PHI→general relay.** `allow-intra-namespace` egress is **scoped by
   `app.kubernetes.io/component`** (`networkPolicy.intraNamespaceEgressComponents`, default
   `[gateway]`), **not** `podSelector: {}`. A blanket intra-namespace egress would let a PHI pod
   reach the **general-zone agent pod** (which holds the cloud-LLM egress rule) and relay PHI out
   via `PHI-pod → general-pod → cloud`. Agents may egress to the gateway (the pseudonymizing PEP)
   and shared infra **only**; agent ↔ peer-agent egress stays denied. **Do not** add `agent-*`
   components to that list.

3. **FIX 3 — `securityZone` cross-validated against the merged definition.** The chart does **not**
   trust `agents[].securityZone` verbatim. When an agent carries a mounted `effectiveDefinition`
   (the merged ConfigMap content `provision_tenant.py` injects), the chart parses its
   `security_zone` and **fails** if it disagrees with `securityZone`. A hand-edited values file
   that downgrades a base-PHI agent (e.g. `rafael`) to `general` cannot silently render a general
   policy for a PHI agent — it aborts. The merged Agent Definition is the source of truth; the
   values zone is defense-in-depth that **must match** it.
- a **ConfigMap** `agent-def-<name>` delivering the **effective** (merged L0+overlay) Agent
  Definition the runtime loads, with the version hash (ADR-0007) as a rolling-restart annotation.

Disabled agents (`enabled: false`, e.g. `gustavo`/`lucas` until their wave lands) render nothing.
The gateway / fhir-sync / webhook-receiver Deployments are unchanged.

## One-command provisioning (≤1 day; ADR-0011)

`scripts/provision_tenant.py` computes each agent's effective definition (via the W0.4 merge
engine `maezo.platform.tenancy.agent_def_merge.merge_agent_definition`), writes a per-tenant
`values-<tenant>.yaml` with the merged definitions + hashes + PHI-zone routing, and prints a plan
plus the exact `helm template ... --dry-run` command. It does **not** apply (AWS-blocked).

```sh
python scripts/provision_tenant.py \
  --tenant amh \
  --overlay src/maezo/policies/autonomy/tenants-amh.yaml \
  --agents helena:general rafael:phi marina:phi \
  --out deploy/helm/maezo-tenant/values-amh-provisioned.yaml
```

## CI validate-helm gate

```sh
helm lint deploy/helm/maezo-tenant --strict -f deploy/helm/maezo-tenant/values-amh.yaml
helm template maezo-amh deploy/helm/maezo-tenant -f deploy/helm/maezo-tenant/values-amh.yaml --debug > /dev/null
```
