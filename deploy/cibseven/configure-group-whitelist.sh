#!/bin/sh
# CIB Seven 2.1 group-only admission; ADR-0049 + ESCALATION-001.
# Accept alphanumeric segments separated by single hyphens. This admits static
# and contract-derived dynamic identities; membership/authorization is separate.
# https://docs.cibseven.org/manual/2.1/user-guide/process-engine/identity-service/
# Build context: deploy/cibseven. Refuse descriptor drift rather than silently
# widening general/user/tenant patterns or overriding an existing group policy.
set -eu
config=${1:?bpm-platform.xml path required}
tmp=$(mktemp "${config}.groups.XXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
awk -v pattern='[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*' '
    /groupResourceWhitelistPattern/ { invalid = 1 }
    /<process-engine[[:space:]]/ {
        engines++
        if ($0 !~ /^[[:space:]]*<process-engine name="default">[[:space:]]*$/) invalid = 1
        inside = 1
    }
    { print }
    # Somente o PRIMEIRO <properties> do engine: o descriptor tem mais dois dentro dos blocos LDAP
    # COMENTADOS (awk nao le comentario XML), e inserir em todos fazia inserted=3 -> recusa.
    # Medido em 09/09/2026 contra o bpm-platform.xml de cibseven/cibseven:2.1.0.
    inside && !inserted && /^[[:space:]]*<properties>[[:space:]]*$/ {
        inserted++
        print "      <property name=\"groupResourceWhitelistPattern\">" pattern "</property>"
    }
    /<\/process-engine>/ { inside = 0; closed++ }
    END {
        if (invalid || engines != 1 || closed != 1 || inserted != 1 || inside) exit 1
    }
' "$config" > "$tmp" || {
    echo 'Refusing unexpected CIB Seven descriptor/group whitelist' >&2
    exit 1
}
# Keep the original descriptor ownership/mode and all pre-existing bytes.
cat "$tmp" > "$config"
