#!/bin/sh
# CIB Seven 2.1 group-only admission; ADR-0049 + ESCALATION-001.
# Accept alphanumeric segments separated by single hyphens. This admits static
# and contract-derived dynamic identities; membership/authorization is separate.
# https://docs.cibseven.org/manual/2.1/user-guide/process-engine/identity-service/
# Repository-root build context. Refuse descriptor drift rather than silently
# widening general/user/tenant patterns or overriding an existing group policy.
set -eu
config=${1:?bpm-platform.xml path required}
tmp=$(mktemp "${config}.groups.XXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
# The sentinel retains a possible final newline through command substitution.
last=$(tail -c 1 "$config"; printf '.')
newline=$(printf '\n.')
final_newline=0
[ "$last" != "$newline" ] || final_newline=1
# A literal heredoc keeps the parser's comments independent of shell quoting.
# Parse the supported descriptor structure before producing any replacement.
if ! LC_ALL=C awk -v pattern='[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*' \
    -v final_newline="$final_newline" -f - "$config" > "$tmp" <<'AWK'
function refuse() { invalid = 1; exit 1 }
function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
# Attribute values remain literal: encoded/ambiguous target names are refused.
function attributes(s,    key,q,n,value,seen) {
    attr_name = ""
    while (length(s)) {
        if (s !~ /^[[:space:]]/) refuse()
        s = trim(s)
        if (!length(s)) break
        if (!match(s, /^[A-Za-z_][A-Za-z0-9_.:-]*/)) refuse()
        key = substr(s, 1, RLENGTH)
        if (seen[key]++) refuse()
        s = substr(s, RLENGTH + 1); sub(/^[[:space:]]*/, "", s)
        if (substr(s, 1, 1) != "=") refuse()
        s = substr(s, 2); sub(/^[[:space:]]*/, "", s)
        q = substr(s, 1, 1)
        if (q != "\"" && q != "\047") refuse()
        s = substr(s, 2); n = index(s, q)
        if (!n) refuse()
        value = substr(s, 1, n - 1)
        if (value ~ /[<&]/) refuse()
        if (key == "xmlns" && (depth || value != "http://www.camunda.org/schema/1.0/BpmPlatform")) refuse()
        if (key == "name") attr_name = value
        s = substr(s, n + 1)
    }
}
{ xml = xml $0 "\n" }
END {
    if (invalid) exit 1
    if (!final_newline) xml = substr(xml, 1, length(xml) - 1)
    for (i = 1; i <= length(xml);) {
        if (substr(xml, i, 4) == "<!--") {
            rest = substr(xml, i + 4); n = index(rest, "-->")
            if (!n || substr(rest, 1, n - 1) ~ /--/) refuse()
            i += n + 6
            continue
        }
        if (substr(xml, i, 2) == "<?") {
            # Only an initial XML declaration is supported; no processing hooks.
            n = index(substr(xml, i), "?>")
            if (i != 1 || substr(xml, i, 6) != "<?xml " || !n) refuse()
            i += n + 1
            continue
        }
        if (substr(xml, i, 1) != "<") {
            if (!depth && substr(xml, i, 1) !~ /[[:space:]]/) refuse()
            i++
            continue
        }
        # Quotes delimit attributes; a quoted > cannot terminate an element.
        quote = ""
        for (j = i + 1; j <= length(xml); j++) {
            c = substr(xml, j, 1)
            if (quote) { if (c == quote) quote = "" }
            else if (c == "\"" || c == "\047") quote = c
            else if (c == ">") break
        }
        if (j > length(xml) || quote) refuse()
        tag = substr(xml, i + 1, j - i - 1)
        closing = substr(tag, 1, 1) == "/"
        if (closing) tag = substr(tag, 2)
        empty = !closing && tag ~ /\/$/
        if (empty) tag = substr(tag, 1, length(tag) - 1)
        if (!match(tag, /^[A-Za-z_][A-Za-z0-9_.-]*/)) refuse()
        name = substr(tag, 1, RLENGTH); attrs = substr(tag, RLENGTH + 1)
        if (closing) {
            if (trim(attrs) != "" || depth < 1 || stack[depth] != name) refuse()
            delete stack[depth--]
        } else {
            attributes(attrs)
            if (!depth) {
                if (name != "bpm-platform" || roots++) refuse()
            }
            if (name == "process-engine") {
                if (++engines != 1 || depth != 1 || attr_name != "default" || empty) refuse()
                engine_depth = depth + 1
            }
            if (name == "properties" && depth == engine_depth && stack[depth] == "process-engine") {
                if (++blocks != 1 || empty) refuse()
                insertion = j
            }
            if (name == "property" && attr_name == "groupResourceWhitelistPattern") refuse()
            if (!empty) stack[++depth] = name
        }
        i = j + 1
    }
    if (depth || roots != 1 || engines != 1 || blocks != 1) refuse()
    addition = "<property name=\"groupResourceWhitelistPattern\">" pattern "</property>"
    if (substr(xml, insertion + 1, 2) == "\r\n") {
        insertion += 2; addition = "      " addition "\r\n"
    } else if (substr(xml, insertion + 1, 1) == "\n") {
        insertion++; addition = "      " addition "\n"
    }
    printf "%s%s%s", substr(xml, 1, insertion), addition, substr(xml, insertion + 1)
}
AWK
then
    echo 'Refusing unexpected CIB Seven descriptor/group whitelist' >&2
    exit 1
fi
# Keep the original descriptor ownership/mode and all pre-existing bytes.
cat "$tmp" > "$config"
