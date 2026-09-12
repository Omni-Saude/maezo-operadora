#!/usr/bin/env python3
"""Build-time patch of the inspected pinned Tomcat layout. No credentials or runtime cutover."""

from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path("/camunda")
DISPATCHERS = ("REQUEST", "FORWARD", "INCLUDE", "ERROR", "ASYNC")


def load(path):
    tree = ET.parse(path, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    root = tree.getroot()
    ns = root.tag.partition("}")[0] + "}" if root.tag.startswith("{") else ""
    if ns:
        ET.register_namespace("", ns[1:-1])
    return tree, root, ns


def write(tree, path):
    tree.write(path, encoding="utf-8", xml_declaration=True)
    path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")


def child(parent, ns, name, text=None):
    node = ET.SubElement(parent, ns + name)
    if text is not None:
        node.text = text
    return node


def filter_nodes(root, ns, name, klass, provider=None):
    f = child(root, ns, "filter")
    child(f, ns, "filter-name", name)
    child(f, ns, "filter-class", klass)
    child(f, ns, "async-supported", "false")
    if provider:
        param = child(f, ns, "init-param")
        child(param, ns, "param-name", "authentication-provider")
        child(param, ns, "param-value", provider)
    m = child(root, ns, "filter-mapping")
    child(m, ns, "filter-name", name)
    child(m, ns, "url-pattern", "/*")
    for dispatcher in DISPATCHERS:
        child(m, ns, "dispatcher", dispatcher)


def install():
    global_path = ROOT / "conf/web.xml"
    tree, root, ns = load(global_path)
    filter_nodes(root, ns, "maezo-boundary", "br.com.maezo.workload.BoundaryFilter")
    write(tree, global_path)

    native = ROOT / "webapps/engine-rest/WEB-INF/web.xml"
    tree, root, ns = load(native)
    # All active native filters are replaced: the actual pseudo filter is scoped only /filter/*.
    for node in list(root):
        if node.tag in (ns + "filter", ns + "filter-mapping"):
            root.remove(node)
    filter_nodes(
        root,
        ns,
        "maezo-native-auth",
        "org.cibseven.bpm.engine.rest.security.auth.ProcessEngineAuthenticationFilter",
        "br.com.maezo.workload.CertificateAuthenticationProvider",
    )
    servlet = child(root, ns, "servlet")
    child(servlet, ns, "servlet-name", "maezo-workload")
    child(servlet, ns, "servlet-class", "br.com.maezo.workload.WorkloadServlet")
    child(servlet, ns, "load-on-startup", "1")
    mapping = child(root, ns, "servlet-mapping")
    child(mapping, ns, "servlet-name", "maezo-workload")
    child(mapping, ns, "url-pattern", "/maezo/*")
    write(tree, native)

    bpm = ROOT / "conf/bpm-platform.xml"
    tree, root, ns = load(bpm)
    plugins = root.findall(".//" + ns + "plugins")
    if len(plugins) != 1:
        raise SystemExit("unexpected pinned bpm-platform plugin layout")
    plugin = child(plugins[0], ns, "plugin")
    child(plugin, ns, "class", "br.com.maezo.workload.WorkloadPlugin")
    write(tree, bpm)


if __name__ == "__main__":
    install()
