"""Pure v1 descriptor transformations; the shared v2 descriptors are unchanged."""

from xml.etree import ElementTree as ET


def parse(source: bytes) -> ET.Element:
    return ET.fromstring(source, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))


def serialize(root: ET.Element) -> bytes:
    namespace = root.tag.partition("}")[0].removeprefix("{")
    ET.register_namespace("", namespace)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def rest(source: bytes) -> bytes:
    root = parse(source)
    ns = root.tag.partition("}")[0] + "}"
    root.set("metadata-complete", "true")
    old = "org.cibseven.bpm.engine.rest.impl.FetchAndLockContextListener"
    matches = [n for n in root.iter(ns + "listener-class") if n.text == old]
    if len(matches) != 1 or list(root.iter(ns + "absolute-ordering")):
        raise ValueError("exact original REST listener and no existing ordering required")
    matches[0].text = "br.com.maezo.workload.SecuredFetchAndLockContextListener"
    ET.SubElement(root, ns + "absolute-ordering")
    return serialize(root)


def global_web(source: bytes) -> bytes:
    root = parse(source)
    ns = root.tag.partition("}")[0] + "}"
    removed = []
    for node in list(root):
        if (
            node.tag in (ns + "servlet", ns + "servlet-mapping")
            and node.findtext(ns + "servlet-name") == "jsp"
        ):
            removed.append(node.tag)
            root.remove(node)
    if removed.count(ns + "servlet") != 1 or removed.count(ns + "servlet-mapping") != 1:
        raise ValueError("exact pinned JSP servlet and mapping required")
    return serialize(root)
