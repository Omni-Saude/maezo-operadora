{{/*
Common labels for all resources in this chart.
*/}}
{{- define "maezo-tenant.labels" -}}
app.kubernetes.io/name: maezo-tenant
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
maezo.io/tenant: {{ .Values.tenant.id | quote }}
{{- end }}

{{/*
Selector labels for deployments.
*/}}
{{- define "maezo-tenant.selectorLabels" -}}
app.kubernetes.io/name: maezo-tenant
app.kubernetes.io/instance: {{ .Release.Name }}
maezo.io/tenant: {{ .Values.tenant.id | quote }}
{{- end }}

{{/*
ServiceAccount name (shared/legacy default — gateway/fhir-sync/webhook still use it).
*/}}
{{- define "maezo-tenant.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
    {{ default "maezo-agent" .Values.serviceAccount.name }}
{{- else -}}
    {{ .Values.serviceAccount.name }}
{{- end -}}
{{- end }}

{{/*
Per-agent ServiceAccount name. Each agent gets its own SA so IRSA roles are
scoped per agent (no shared cloud identity across PHI / general zones).
Usage: include "maezo-tenant.agentServiceAccountName" (dict "name" $agent.name)
*/}}
{{- define "maezo-tenant.agentServiceAccountName" -}}
maezo-agent-{{ .name }}
{{- end }}

{{/*
network-change-bridge ServiceAccount name (R-198 / NETBRIDGE-SERVICE-PRINCIPAL-IDENTITY): its OWN
service principal, distinct from `maezo-tenant.serviceAccountName` (which the notifications-bridge
still uses) — least privilege, attributable audit trail per bridge.
*/}}
{{- define "maezo-tenant.networkChangeBridgeServiceAccountName" -}}
{{- if .Values.networkChangeBridge.serviceAccount.create -}}
    {{ default "maezo-network-change-bridge" .Values.networkChangeBridge.serviceAccount.name }}
{{- else -}}
    {{ .Values.networkChangeBridge.serviceAccount.name }}
{{- end -}}
{{- end }}

{{/*
Per-agent PHI-zone boolean pod label value.
The task contract is explicit: each agent Deployment carries
`maezo.io/phi-zone: "true|false"`. PHI-zone agents (securityZone=phi) -> "true".
Usage: include "maezo-tenant.agentPhiZoneLabel" (dict "zone" $agent.securityZone)
*/}}
{{- define "maezo-tenant.agentPhiZoneLabel" -}}
{{- if eq (.zone | toString) "phi" -}}true{{- else -}}false{{- end -}}
{{- end }}

{{/*
Per-agent component label (app.kubernetes.io/component value) — "agent-<name>".
Usage: include "maezo-tenant.agentComponent" (dict "name" $agent.name)
*/}}
{{- define "maezo-tenant.agentComponent" -}}
agent-{{ .name }}
{{- end }}

{{/*
Validate the agents list: fail rendering early on a malformed agent entry so a
provisioning mistake never silently produces a mis-zoned pod (ADR-0006 guardrail).

Cross-validation (FIX 3 — defense-in-depth): the values `securityZone` is NOT trusted
verbatim. When an agent carries a mounted `effectiveDefinition` (the merged ConfigMap
content produced by provision_tenant.py), we parse its `security_zone` out of that YAML
and FAIL if it disagrees with `securityZone`. A hand-edited values file that downgrades a
base-PHI agent (e.g. rafael) to `general` would render a general NetworkPolicy for a PHI
agent — this makes that abort rendering instead. The merged definition is the source of
truth; the values zone must MATCH it.

Usage: include "maezo-tenant.validateAgents" .
*/}}
{{- define "maezo-tenant.validateAgents" -}}
{{- range $i, $agent := .Values.agents }}
{{-   if not $agent.name }}
{{-     fail (printf "agents[%d]: missing required field `name`" $i) }}
{{-   end }}
{{-   if not (has ($agent.securityZone | toString) (list "general" "phi")) }}
{{-     fail (printf "agents[%d] (%s): securityZone must be `general` or `phi`, got %q (ADR-0006)" $i $agent.name $agent.securityZone) }}
{{-   end }}
{{-   $defZone := include "maezo-tenant.effectiveDefinitionZone" (dict "agent" $agent) }}
{{-   if $defZone }}
{{-     if ne $defZone ($agent.securityZone | toString) }}
{{-       fail (printf "agents[%d] (%s): securityZone=%q DISAGREES with security_zone=%q parsed from its mounted effectiveDefinition. The merged Agent Definition is the source of truth (ADR-0006 zone routing); fix values or re-provision — NEVER render a general policy for a PHI agent." $i $agent.name ($agent.securityZone | toString) $defZone) }}
{{-     end }}
{{-   end }}
{{- end }}
{{- end }}

{{/*
Parse the `security_zone` field out of an agent's mounted effectiveDefinition (the
ConfigMap content). Returns "" when the agent has no effectiveDefinition (e.g. a values
file authored by hand without provisioning) — callers treat "" as "nothing to cross-check
against". When present, the parsed YAML's `security_zone` is the merged-definition truth.
Usage: include "maezo-tenant.effectiveDefinitionZone" (dict "agent" $agent)
*/}}
{{- define "maezo-tenant.effectiveDefinitionZone" -}}
{{- $agent := .agent -}}
{{- if $agent.effectiveDefinition -}}
{{-   $parsed := fromYaml $agent.effectiveDefinition -}}
{{-   if hasKey $parsed "Error" -}}
{{-     fail (printf "agent %q: effectiveDefinition is not valid YAML — cannot verify its security_zone (ADR-0006)" $agent.name) -}}
{{-   end -}}
{{-   if hasKey $parsed "security_zone" -}}
{{-     get $parsed "security_zone" | toString -}}
{{-   end -}}
{{- end -}}
{{- end }}

{{/*
FIX 1 — require a concrete BR-resident CIDR for every PHI egress endpoint.
Plain K8s NetworkPolicy `ipBlock` cannot express FQDN/hostname allowlists, so a PHI egress
rule can ONLY be a real network guarantee if it pins a concrete CIDR. This helper validates
one endpoint entry and FAILs (fail-closed) when its `cidr` is missing/empty or is the
internet-wide 0.0.0.0/0 — we NEVER ship 0.0.0.0/0 for PHI (ADR-0006). Returns the cidr.
Usage: include "maezo-tenant.requirePhiCidr" (dict "agent" $name "endpoint" $ep)
*/}}
{{- define "maezo-tenant.requirePhiCidr" -}}
{{- $agent := .agent -}}
{{- $ep := .endpoint -}}
{{- $cidr := $ep.cidr | default "" | toString | trim -}}
{{- if not $cidr -}}
{{-   fail (printf "PHI agent %q egress endpoint %q (port %v): MISSING required `cidr`. Plain NetworkPolicy ipBlock cannot allowlist by hostname — a concrete BR-resident CIDR is mandatory (fail-closed; ADR-0006). Set networkPolicy.phiZone.brResidentEndpoints[].cidr or use an egress proxy / Cilium FQDN policy." $agent ($ep.host | default "?") ($ep.port | default "?")) -}}
{{- end -}}
{{- if or (eq $cidr "0.0.0.0/0") (eq $cidr "::/0") -}}
{{-   fail (printf "PHI agent %q egress endpoint %q: cidr=%q permits egress to the WHOLE internet — that is a PHI-isolation NO-OP and is FORBIDDEN (ADR-0006). Pin the concrete NLB/endpoint CIDR." $agent ($ep.host | default "?") $cidr) -}}
{{- end -}}
{{- $cidr -}}
{{- end }}

{{/*
FIX 1 (general zone) — require a concrete CIDR for a general-zone egress endpoint too.
The general zone reaches the cloud LLM; while it only sees PSEUDONYMIZED data, 0.0.0.0/0 is
still not an allowlist. We require an explicit cidr per endpoint and reject 0.0.0.0/0. This
applies the same concrete-CIDR discipline to the cloud-LLM/MSK egress where feasible.
Usage: include "maezo-tenant.requireGeneralCidr" (dict "agent" $name "endpoint" $ep "kind" "cloud-LLM")
*/}}
{{- define "maezo-tenant.requireGeneralCidr" -}}
{{- $agent := .agent -}}
{{- $ep := .endpoint -}}
{{- $kind := .kind | default "endpoint" -}}
{{- $cidr := $ep.cidr | default "" | toString | trim -}}
{{- if not $cidr -}}
{{-   fail (printf "general-zone agent %q %s endpoint %q (port %v): MISSING required `cidr`. NetworkPolicy ipBlock is IP-based, not hostname-based — set a concrete CIDR (egress proxy / Cilium FQDN policy needed for true FQDN allowlisting). 0.0.0.0/0 is not an allowlist." $agent $kind ($ep.host | default "?") ($ep.port | default "?")) -}}
{{- end -}}
{{- if or (eq $cidr "0.0.0.0/0") (eq $cidr "::/0") -}}
{{-   fail (printf "general-zone agent %q %s endpoint %q: cidr=%q is the whole internet — that is not an allowlist (ADR-0006). Pin the concrete CIDR." $agent $kind ($ep.host | default "?") $cidr) -}}
{{- end -}}
{{- $cidr -}}
{{- end }}

{{/*
Namespace name.
*/}}
{{- define "maezo-tenant.namespace" -}}
{{ .Values.namespace.name }}
{{- end }}

{{/*
Image reference.
*/}}
{{- define "maezo-tenant.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end }}

{{/*
OTel environment variables common to all deployments.
*/}}
{{- define "maezo-tenant.otelEnv" -}}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.observability.otel.endpoint | quote }}
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: {{ .Values.observability.otel.protocol | quote }}
- name: OTEL_SERVICE_NAME
  value: "maezo-{{ .Values.tenant.id }}"
- name: OTEL_RESOURCE_ATTRIBUTES
  value: "tenant={{ .Values.tenant.id }},environment={{ .Release.Namespace }}"
{{- end }}
