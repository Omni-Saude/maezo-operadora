"""UnitOnly files/ZIP mechanics; binary placeholders are NOT CIB/JVM evidence."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from scripts.ci import build_provider_native_observation as build

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures/provider_auth_native"
PRODUCTION = ROOT / "deploy/cibseven/secured/descriptors"
JAVA = ROOT / "src/maezo/portal/engine/java"


def file(root: Path, name: str, raw: bytes) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def jar(root: Path, name: str, entries: dict[str, bytes]) -> Path:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for entry, raw in sorted(entries.items()):
            # Identical UnitOnly members must produce identical phase JAR bytes.
            info = zipfile.ZipInfo(entry, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, raw)
    file(root, name, output.getvalue())
    return root / name


@pytest.fixture
def inputs(tmp_path):
    """Plain UnitOnly placeholder bytes test data correspondence, no Java execution."""
    classes = tmp_path / "classes"
    tests = tmp_path / "test-classes"
    for name in (
        "br/com/maezo/workload/WorkloadPlugin.class",
        "br/com/maezo/workload/StartupAdmission.class",
        "br/com/maezo/workload/CertificateAuthenticationProvider.class",
        "br/com/maezo/workload/SecuredFetchAndLockContextListener.class",
        "br/com/maezo/workload/SecuredFetchAndLockContextListener$UnitOnly.class",
    ):
        file(classes, name, b"UnitOnly-not-real-compiled-Java-" + name.encode())
    file(
        classes,
        "secured-startup-vendor.sha256",
        (JAVA / "src/main/resources/secured-startup-vendor.sha256").read_bytes(),
    )
    file(
        classes,
        "secured-startup-descriptors.sha256",
        (JAVA / "src/main/resources/secured-startup-descriptors.sha256").read_bytes(),
    )
    for owner in build.OWNERS:
        file(tests, owner + ".class", b"UnitOnly-not-real-owner-bytecode")
        file(tests, owner + "$UnitOnly.class", b"UnitOnly-not-real-inner-bytecode")
    file(tests, "br/com/maezo/human/OtherUnitTest.class", b"UnitOnly-not-an-owner")
    for phase, resource in zip(build.PHASES, build.DESCRIPTOR_RESOURCES, strict=True):
        file(tests, resource, (FIXTURES / phase / "secured-startup-descriptors.sha256").read_bytes())
    return classes, tests, tmp_path / "support"


def phase_tree(tmp_path, phase, support):
    root = tmp_path / phase
    sources = {
        "conf/server.xml": FIXTURES / "secured/server.xml",
        "conf/bpm-platform.xml": FIXTURES / phase / "bpm-platform.xml",
        "conf/context.xml": PRODUCTION / "context.xml",
        "conf/web.xml": PRODUCTION / "startup-global-web.xml",
        "webapps/engine-rest/WEB-INF/web.xml": PRODUCTION / "startup-engine-rest-web.xml",
        "webapps/maezo-human/WEB-INF/web.xml": PRODUCTION / "human-web.xml",
        **{
            f"webapps/{app}/WEB-INF/web.xml": PRODUCTION / f"{app}-web.xml"
            for app in ("ROOT", "camunda", "docs", "examples", "host-manager", "manager", "webapp")
        },
    }
    for name, source in sources.items():
        file(root, name, source.read_bytes())
    file(root, "lib/provider-auth-test-support.jar", Path(support["support_jar"]).read_bytes())
    jar(root, "lib/UnitOnly-vendor.jar", {"UnitOnly.class": b"UnitOnly-placeholder-not-runtime"})
    file(root, "webapps/engine-rest/WEB-INF/classes/UnitOnly.class", b"UnitOnly-placeholder-not-runtime")
    file(root, "webapps/ROOT/META-INF/context.xml", b"<Context/>")
    # Own artifacts are excluded only by these two EXACT paths, never name wildcard.
    file(root, "lib/maezo-human-command.jar", b"UnitOnly-own-placeholder")
    file(root, "webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar", b"UnitOnly-spi-placeholder")
    return root


def prepare(tmp_path, inputs):
    classes, tests, output = inputs
    manifest = build.build_support(classes, tests, FIXTURES, output)
    support = build.load(manifest)
    phases = [phase_tree(tmp_path, phase, support) for phase in build.PHASES]
    return manifest, support, phases


def finalize_inputs(tmp_path, inputs):
    support_path, support, phases = prepare(tmp_path, inputs)
    vendor_path = build.stage_vendor(support_path, *phases, tmp_path / "provider-native-observation")
    vendor = build.load(vendor_path)
    classes = Path(support["classes"])
    for resource in vendor["generated_resources"]:
        file(classes, resource, (Path(vendor["generated_resources_root"]) / resource).read_bytes())
    main = {
        name: (classes / name).read_bytes()
        for name in build.tree(classes)
        if "CertificateAuthenticationProvider" not in name
        and "SecuredFetchAndLockContextListener" not in name
    }
    spi = {
        name: (classes / name).read_bytes()
        for name in build.tree(classes)
        if "CertificateAuthenticationProvider" in name or "SecuredFetchAndLockContextListener" in name
    }
    main_path = jar(tmp_path, "main.jar", main)
    spi_path = jar(tmp_path, "spi.jar", spi)
    return vendor_path, vendor, support, phases, main_path, spi_path


def test_positive_full_dag_same_compilation_no_selfhash(tmp_path, inputs):
    vendor_path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    final = build.verify_final(vendor_path, main, spi, tmp_path / "final.json")
    result = build.load(final)
    assert result["scope"] == "BUILD_ONLY_NOT_IMAGE_ABI_OR_RUNTIME"
    assert result["main_jar_sha256"] == build.sha(main.read_bytes())
    assert result["support_jar_sha256"] == support["support_jar_sha256"]
    for phase in build.PHASES:
        covered = vendor["phase_inputs"][phase]["covered"]
        assert "lib/provider-auth-test-support.jar" in covered
        assert "webapps/ROOT/META-INF/context.xml" in covered
        assert "webapps/engine-rest/WEB-INF/classes/UnitOnly.class" in covered
        assert not set(build.OWN) & set(covered)
    members = build.zip_entries(Path(support["support_jar"]))
    assert not set(build.VENDOR_RESOURCES) & set(members)
    assert not any(
        b"maezo-human-command.jar" in raw or b"maezo-rest-spi.jar" in raw
        for name, raw in members.items()
        if name.endswith(".sha256")
    )
    assert (
        build.zip_entries(main)["secured-startup-vendor.sha256"]
        == (JAVA / "src/main/resources/secured-startup-vendor.sha256").read_bytes()
    )


@pytest.mark.parametrize("owner", build.OWNERS)
def test_missing_actual_owner_refuses_before_output(inputs, owner):
    classes, tests, output = inputs
    (tests / (owner + ".class")).unlink()
    with pytest.raises(build.NativeObservationBuildError, match="Both actual"):
        build.build_support(classes, tests, FIXTURES, output)
    assert not output.exists()


def test_support_only_finite_owner_members(tmp_path, inputs):
    manifest, support, phases = prepare(tmp_path, inputs)
    members = build.zip_entries(Path(support["support_jar"]))
    assert len(members) == 6
    assert not any("OtherUnitTest" in name for name in members)
    assert all(name.endswith(".class") or name in build.DESCRIPTOR_RESOURCES for name in members)


def test_support_is_deterministic_without_rebuild(tmp_path, inputs):
    classes, tests, first = inputs
    a = build.build_support(classes, tests, FIXTURES, first)
    b = build.build_support(classes, tests, FIXTURES, tmp_path / "second")
    assert build.load(a)["support_jar_sha256"] == build.load(b)["support_jar_sha256"]
    with pytest.raises(build.NativeObservationBuildError, match="Fresh absolute"):
        build.build_support(classes, tests, FIXTURES, first)


@pytest.mark.parametrize("which", ("classes", "test_classes"))
def test_recompile_refuses_before_vendor_effect(tmp_path, inputs, which):
    path, support, phases = prepare(tmp_path, inputs)
    root = Path(support[which])
    file(root, "UnitOnly-new.class", b"UnitOnly-recompiled")
    out = tmp_path / "vendor"
    with pytest.raises(build.NativeObservationBuildError, match="compilation changed"):
        build.stage_vendor(path, *phases, out)
    assert not out.exists()


def test_full_domain_extra_jar_included_not_filtered(tmp_path, inputs):
    path, support, phases = prepare(tmp_path, inputs)
    for root in phases:
        jar(root, "webapps/docs/WEB-INF/lib/UnitOnly-extra.jar", {"Extra.class": b"UnitOnly"})
        jar(root, "lib/maezo-human-command-extra.jar", {"Extra.class": b"UnitOnly"})
    vendor = build.load(build.stage_vendor(path, *phases, tmp_path / "vendor"))
    for phase in build.PHASES:
        assert "webapps/docs/WEB-INF/lib/UnitOnly-extra.jar" in vendor["phase_inputs"][phase]["covered"]
        assert "lib/maezo-human-command-extra.jar" in vendor["phase_inputs"][phase]["covered"]


@pytest.mark.parametrize("name", ("lib/alien.jar", "webapps/engine-rest/WEB-INF/classes/Alien.class"))
def test_phase_binary_drift_refuses(tmp_path, inputs, name):
    path, support, phases = prepare(tmp_path, inputs)
    if name.endswith(".jar"):
        jar(phases[1], name, {"UnitOnlyDifferent.class": b"UnitOnly-different-phase-binary"})
    else:
        file(phases[1], name, b"UnitOnly-different-phase-binary")
    with pytest.raises(build.NativeObservationBuildError, match="Same candidate/vendor"):
        build.stage_vendor(path, *phases, tmp_path / "vendor")


def test_staged_support_drift_refuses(tmp_path, inputs):
    path, support, phases = prepare(tmp_path, inputs)
    file(phases[0], "lib/provider-auth-test-support.jar", b"UnitOnly-different-support")
    with pytest.raises(build.NativeObservationBuildError, match="Actual staged support"):
        build.stage_vendor(path, *phases, tmp_path / "vendor")


@pytest.mark.parametrize("name", ("conf/bpm-platform.xml", "conf/server.xml", "webapps/ROOT/WEB-INF/web.xml"))
def test_descriptor_drift_refuses_before_output(tmp_path, inputs, name):
    path, support, phases = prepare(tmp_path, inputs)
    file(phases[0], name, b"UnitOnly-changed-descriptor")
    with pytest.raises(build.NativeObservationBuildError, match="descriptor bytes"):
        build.stage_vendor(path, *phases, tmp_path / "vendor")
    assert not (tmp_path / "vendor").exists()


@pytest.mark.parametrize(
    "name",
    (
        "lib/camunda.cfg.xml",
        "webapps/ROOT/WEB-INF/classes/META-INF/services/jakarta.servlet.ServletContainerInitializer",
        "webapps/ROOT/WEB-INF/tomcat-web.xml",
    ),
)
def test_alternate_startup_refuses(tmp_path, inputs, name):
    path, support, phases = prepare(tmp_path, inputs)
    file(phases[0], name, b"UnitOnly-unadmitted-startup")
    with pytest.raises(build.NativeObservationBuildError, match="startup resource|engine descriptor"):
        build.stage_vendor(path, *phases, tmp_path / "vendor")


def test_extra_app_refuses(tmp_path, inputs):
    path, support, phases = prepare(tmp_path, inputs)
    (phases[0] / "webapps/alien").mkdir()
    with pytest.raises(build.NativeObservationBuildError, match="webapp set"):
        build.stage_vendor(path, *phases, tmp_path / "vendor")


def test_symlink_refuses(tmp_path, inputs):
    path, support, phases = prepare(tmp_path, inputs)
    (phases[0] / "lib/alien.jar").symlink_to(phases[0] / "lib/UnitOnly-vendor.jar")
    with pytest.raises(build.NativeObservationBuildError, match="Symlink tree"):
        build.stage_vendor(path, *phases, tmp_path / "vendor")


@pytest.mark.parametrize(
    "raw",
    (
        b"",
        b"a" * 64 + b" ../outside\n",
        b"a" * 64 + b" /absolute\n",
        (b"a" * 64 + b" lib/a.jar\n") * 2,
        b"a" * 64 + b" lib/z.jar\n" + b"b" * 64 + b" lib/a.jar\n",
    ),
)
def test_noncanonical_inventory_refused(raw):
    with pytest.raises(build.NativeObservationBuildError):
        build.parse_inventory(raw)


def test_duplicate_jar_entry_refuses(tmp_path):
    out = tmp_path / "duplicate.jar"
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("UnitOnly.class", b"first")
        with pytest.warns(UserWarning):
            archive.writestr("UnitOnly.class", b"second")
    with pytest.raises(build.NativeObservationBuildError, match="Duplicate JAR"):
        build.zip_entries(out)


@pytest.mark.parametrize("target", ("main", "spi", "vendor", "support"))
def test_final_drift_refuses_without_success_receipt(tmp_path, inputs, target):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    if target == "main":
        members = build.zip_entries(main)
        members["br/com/maezo/workload/WorkloadPlugin.class"] = b"UnitOnly-changed-after-compile"
        jar(tmp_path, "main.jar", members)
    elif target == "spi":
        members = build.zip_entries(spi)
        members["UnitOnly-foreign.class"] = b"UnitOnly"
        jar(tmp_path, "spi.jar", members)
    elif target == "vendor":
        file(phases[0], "lib/UnitOnly-vendor.jar", b"UnitOnly-changed-after-inventory")
    else:
        Path(support["support_jar"]).write_bytes(b"UnitOnly-rebuilt-support")
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, tmp_path / "final.json")
    assert not (tmp_path / "final.json").exists()


def test_duplicate_resource_origin_refuses(tmp_path, inputs):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    members = build.zip_entries(main)
    members[build.DESCRIPTOR_RESOURCES[0]] = b"UnitOnly-shadow-map"
    jar(tmp_path, "main.jar", members)
    with pytest.raises(build.NativeObservationBuildError, match="Descriptor origin"):
        build.verify_final(path, main, spi, tmp_path / "final.json")


def test_default_production_resources_never_written(tmp_path, inputs):
    paths = [
        JAVA / "src/main/resources/secured-startup-vendor.sha256",
        JAVA / "src/main/resources/secured-startup-descriptors.sha256",
    ]
    before = [path.read_bytes() for path in paths]
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    build.verify_final(path, main, spi, tmp_path / "final.json")
    assert [path.read_bytes() for path in paths] == before


def test_closed_secured_fixture_and_phase_controls():
    server = ET.parse(FIXTURES / "secured/server.xml").getroot()
    assert (
        len(server.findall("Service"))
        == len(server.findall(".//Connector"))
        == len(server.findall(".//Engine"))
        == len(server.findall(".//Host"))
        == 1
    )
    connector = server.find(".//Connector")
    assert (
        connector.get("port") == "8443"
        and connector.get("secure") == "true"
        and connector.get("allowTrace") == "false"
    )
    ssl = connector.find("SSLHostConfig")
    assert ssl.get("certificateVerification") == "required" and ssl.get("protocols") == "TLSv1.3"
    assert ssl.get("truststoreFile").startswith("/run/maezo/provider-native-observation/")
    jdbc = server.find("GlobalNamingResources/Resource[@name='jdbc/ProcessEngine']")
    assert jdbc.get("password") == "${DB_PASSWORD}"
    assert "sslmode=verify-full" in jdbc.get("url") and "pg-ca.crt" in jdbc.get("url")
    assert jdbc.get("factory") == "br.com.maezo.workload.SecuredDataSourceFactory"
    assert server.find("Service/Engine/Valve").get("className") == "br.com.maezo.workload.TraceRefusalValve"
    for phase, resource in zip(build.PHASES, build.DESCRIPTOR_RESOURCES, strict=True):
        raw = (FIXTURES / phase / "secured-startup-descriptors.sha256").read_bytes()
        assert raw == (JAVA / "src/test/resources" / resource).read_bytes()
        assert set(build.parse_inventory(raw)) == build.DESCRIPTORS
        document = ET.parse(FIXTURES / phase / "bpm-platform.xml")
        classes = [node.text for node in document.getroot().iter() if node.tag.rsplit("}", 1)[-1] == "class"]
        composition = "br.com.maezo.human.ProviderAuthTestComposition"
        assert classes.count(composition) == int(phase == "phase-b")
        if phase == "phase-b":
            assert classes.index(composition) < classes.index("br.com.maezo.human.HumanCommandPlugin")
        assert not set(classes) & {
            "br.com.maezo.human.StaffDeploymentComposition",
            "br.com.maezo.human.PortalReadPlugin",
        }


def test_profile_uses_direct_goals_not_lifecycle_recompile():
    document = ET.parse(JAVA / "pom.xml")
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    profiles = [
        p
        for p in document.findall("m:profiles/m:profile", ns)
        if p.findtext("m:id", namespaces=ns) == "provider-native-observation"
    ]
    assert len(profiles) == 1
    assert profiles[0].find("m:activation", ns) is None
    assert [e.findtext("m:phase", namespaces=ns) for e in profiles[0].findall(".//m:execution", ns)] == [
        "none",
        "none",
    ]


def test_competing_owner_origin_refuses_without_removing_vendor(tmp_path, inputs):
    path, support, phases = prepare(tmp_path, inputs)
    owner = build.OWNERS[0] + ".class"
    for root in phases:
        jar(root, "lib/UnitOnly-competing.jar", {owner: b"UnitOnly-duplicate-owner"})
    with pytest.raises(build.NativeObservationBuildError, match="Competing own class"):
        build.stage_vendor(path, *phases, tmp_path / "vendor")
    assert (phases[0] / "lib/UnitOnly-competing.jar").exists()


def test_unit_vendor_entries_in_separate_webapp_are_preserved(tmp_path, inputs):
    path, support, phases = prepare(tmp_path, inputs)
    for root in phases:
        jar(
            root,
            "webapps/docs/WEB-INF/lib/UnitOnly-separate-loader.jar",
            {"UnitOnly.class": b"UnitOnly-placeholder-not-runtime"},
        )
    vendor = build.load(build.stage_vendor(path, *phases, tmp_path / "vendor"))
    for phase in build.PHASES:
        assert "lib/UnitOnly-vendor.jar" in vendor["phase_inputs"][phase]["covered"]
        assert (
            "webapps/docs/WEB-INF/lib/UnitOnly-separate-loader.jar"
            in vendor["phase_inputs"][phase]["covered"]
        )


def test_finalize_context_has_same_final_jars_and_all_descriptors(tmp_path, inputs):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    context = tmp_path / "image-context"
    result = build.load(build.verify_final(path, main, spi, tmp_path / "final.json", image_context=context))
    assert len(result["image_context_file_sha256"]) == 32
    for phase in build.PHASES:
        assert (context / phase / "maezo-human-command.jar").read_bytes() == main.read_bytes()
        assert (context / phase / "maezo-rest-spi.jar").read_bytes() == spi.read_bytes()
        assert (context / phase / "provider-auth-test-support.jar").read_bytes() == Path(
            support["support_jar"]
        ).read_bytes()
        assert set(build.tree(context / phase / "secured-layout")) == build.DESCRIPTORS


def test_bad_jar_is_explicit_refusal(tmp_path):
    file(tmp_path, "bad.jar", b"UnitOnly-invalid-JAR")
    with pytest.raises(build.NativeObservationBuildError, match="Invalid JAR"):
        build.zip_entries(tmp_path / "bad.jar")


def test_output_parent_symlink_refuses_before_effect(tmp_path, inputs):
    classes, tests, _ = inputs
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "linked"
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(build.NativeObservationBuildError, match="Canonical absolute output|symlink"):
        build.build_support(classes, tests, FIXTURES, parent / "assembly")
    assert not (outside / "assembly").exists()


def test_final_artifact_changed_during_validation_refuses_before_context(tmp_path, inputs, monkeypatch):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    original = build.verify_owner_origins

    def replace_during_validation(root, files, current_support):
        original(root, files, current_support)
        main.write_bytes(b"UnitOnly-concurrently-replaced-final-JAR")

    monkeypatch.setattr(build, "verify_owner_origins", replace_during_validation)
    context = tmp_path / "image-context"
    with pytest.raises(build.NativeObservationBuildError, match="Inputs changed"):
        build.verify_final(path, main, spi, tmp_path / "final.json", image_context=context)
    assert not context.exists() and not (tmp_path / "final.json").exists()


def test_existing_final_result_refuses_before_context(tmp_path, inputs):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    result = tmp_path / "final.json"
    result.write_bytes(b"UnitOnly-existing-custody")
    context = tmp_path / "image-context"
    with pytest.raises(build.NativeObservationBuildError, match="Fresh final output"):
        build.verify_final(path, main, spi, result, image_context=context)
    assert not context.exists() and result.read_bytes() == b"UnitOnly-existing-custody"


@pytest.mark.parametrize(
    "member",
    [
        "br/com/maezo/workload/Uncompiled.class",
        "org/UnitOnly/Uncompiled.class",
        "META-INF/versions/17/br/com/maezo/workload/WorkloadPlugin.class",
        "./br/com/maezo/workload/WorkloadPlugin.class",
        "br//com/maezo/workload/WorkloadPlugin.class",
        "br\\com\\maezo\\workload\\WorkloadPlugin.class",
    ],
)
def test_final_all_class_origins_closed_without_class_filtering(tmp_path, inputs, member):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    entries = build.zip_entries(main)
    entries[member] = b"UnitOnly-uncompiled-invalid-class-not-executable"
    jar(tmp_path, "main.jar", entries)
    result, context = tmp_path / "final.json", tmp_path / "context"
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, result, image_context=context)
    assert not result.exists() and not context.exists()
    assert member in build.zip_entries(main)  # No vendor/class stripping to force green.


@pytest.mark.parametrize(
    "omitted",
    [
        "lib/UnitOnly-vendor.jar",
        "lib/provider-auth-test-support.jar",
        "webapps/engine-rest/WEB-INF/classes/UnitOnly.class",
        "webapps/ROOT/META-INF/context.xml",
        "UnitOnly-not-actually-staged.jar",
    ],
)
def test_packaged_vendor_map_must_equal_every_actual_covered_staging_entry(tmp_path, inputs, omitted):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    resource = build.VENDOR_RESOURCES[0]
    values = build.parse_inventory((Path(vendor["generated_resources_root"]) / resource).read_bytes())
    if omitted in values:
        del values[omitted]
    else:
        values[omitted] = "a" * 64
    raw = build.inventory(values)
    file(Path(vendor["generated_resources_root"]), resource, raw)
    file(Path(support["classes"]), resource, raw)
    entries = build.zip_entries(main)
    entries[resource] = raw
    jar(tmp_path, "main.jar", entries)
    vendor["generated_resources"][resource] = build.sha(raw)
    path.write_bytes(build.json_bytes(vendor))
    result, context = tmp_path / "final.json", tmp_path / "context"
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, result, image_context=context)
    assert not result.exists() and not context.exists()


@pytest.mark.parametrize("resource", ["secured-startup-vendor.sha256", "secured-startup-descriptors.sha256"])
@pytest.mark.parametrize("mutation", ["bytes", "missing", "spi-copy", "mr-copy"])
def test_final_default_production_security_resources_exact_and_exclusive(
    tmp_path, inputs, resource, mutation
):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    before = (JAVA / "src/main/resources" / resource).read_bytes()
    entries = build.zip_entries(main)
    if mutation == "bytes":
        entries[resource] = b"UnitOnly-unadmitted-default"
    elif mutation == "missing":
        del entries[resource]
    elif mutation == "spi-copy":
        spi_entries = build.zip_entries(spi)
        spi_entries[resource] = entries[resource]
        jar(tmp_path, "spi.jar", spi_entries)
    else:
        entries["META-INF/versions/17/" + resource] = entries[resource]
    jar(tmp_path, "main.jar", entries)
    result, context = tmp_path / "final.json", tmp_path / "context"
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, result, image_context=context)
    assert not result.exists() and not context.exists()
    assert (JAVA / "src/main/resources" / resource).read_bytes() == before


@pytest.mark.parametrize(
    "target", ["classes", "tests", "phase", "vendor", "support", "fixtures", "generated", "shade-source"]
)
def test_finalize_output_disjoint_from_all_immutable_inputs_before_effect(tmp_path, inputs, target):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    roots = {
        "classes": Path(support["classes"]),
        "tests": Path(support["test_classes"]),
        "phase": phases[0],
        "vendor": path.parent,
        "support": Path(support["support_jar"]).parent,
        "fixtures": FIXTURES,
        "generated": Path(vendor["generated_resources_root"]),
        "shade-source": JAVA / "src/main/resources",
    }
    output = roots[target] / "UnitOnly-output.json"
    snapshots = {
        str(root): build.tree(root)
        for root in (Path(support["classes"]), Path(support["test_classes"]), *phases)
    }
    context = tmp_path / "context"
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, output, image_context=context)
    assert not output.exists() and not context.exists()
    assert snapshots == {
        str(root): build.tree(root)
        for root in (Path(support["classes"]), Path(support["test_classes"]), *phases)
    }


@pytest.mark.parametrize("stage", ["support", "vendor"])
def test_stage_output_below_compilation_rejected_before_first_write(tmp_path, inputs, stage):
    classes, tests, output = inputs
    if stage == "support":
        before = build.tree(classes)
        forbidden = classes / "support-output"
        with pytest.raises(build.NativeObservationBuildError):
            build.build_support(classes, tests, FIXTURES, forbidden)
    else:
        path, support, phases = prepare(tmp_path, inputs)
        before = build.tree(classes)
        forbidden = classes / "vendor-output"
        with pytest.raises(build.NativeObservationBuildError):
            build.stage_vendor(path, *phases, forbidden)
    assert not forbidden.exists() and build.tree(classes) == before


@pytest.mark.parametrize(
    "relation", ["file-in-context", "context-in-file", "context-in-classes", "symlink-alias"]
)
def test_final_output_and_image_context_boundaries_are_distinct_before_effect(tmp_path, inputs, relation):
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    output, context = tmp_path / "final.json", tmp_path / "context"
    if relation == "file-in-context":
        output = context / "final.json"
    elif relation == "context-in-file":
        context = output / "context"
    elif relation == "context-in-classes":
        context = Path(support["classes"]) / "context"
    else:
        alias = tmp_path / "class-alias"
        alias.symlink_to(Path(support["classes"]), target_is_directory=True)
        output = alias / "final.json"
    before = build.tree(Path(support["classes"]))
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, output, image_context=context)
    assert not output.exists() and not context.exists()
    assert build.tree(Path(support["classes"])) == before


def measured_shade_case(tmp_path, inputs, *, mr=False, module_descriptor=None):
    """UnitOnly inert ZIP records, not actual bytecode/Maven execution evidence."""
    classes, tests, output = inputs
    source = "com/fasterxml/jackson/core/UnitOnlyFactory.class"
    relocated = "br/com/maezo/human/internal/jackson/UnitOnlyFactory.class"
    dependency_members = {source: b"UnitOnly-not-real-dependency-bytecode"}
    if mr:
        dependency_members["META-INF/versions/11/com/fasterxml/jackson/core/UnitOnlyVersioned.class"] = (
            b"UnitOnly-not-real-MR-bytecode"
        )
    if module_descriptor is not None:
        dependency_members["META-INF/versions/9/module-info.class"] = b"UnitOnly-not-real-module-descriptor"
    dependency = jar(tmp_path, "UnitOnly-jackson.jar", dependency_members)
    original = build.tree(classes)
    spi = build.spi_map(original)
    reference_entries = {name: (classes / name).read_bytes() for name in original if name not in spi}
    # Shade changes own class constant pools too; placeholder mutation tests that distinction.
    reference_entries["br/com/maezo/workload/WorkloadPlugin.class"] = (
        b"UnitOnly-postshade-own-constantpool-not-JVM"
    )
    reference_entries[relocated] = b"UnitOnly-relocated-dependency-not-JVM"
    if mr:
        reference_entries[
            "META-INF/versions/11/br/com/maezo/human/internal/jackson/UnitOnlyVersioned.class"
        ] = b"UnitOnly-postshade-MR-not-JVM"
    if mr or module_descriptor is True:
        reference_entries["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\nMulti-Release: true\n\n"
    if module_descriptor is True:
        reference_entries["META-INF/versions/9/module-info.class"] = b"UnitOnly-postshade-module-info-not-JVM"

    reference = jar(tmp_path, "measured-first-shade.jar", reference_entries)
    support_path = build.build_support(
        classes, tests, FIXTURES, output, shade_reference=reference, shade_dependencies=(dependency,)
    )
    support = build.load(support_path)
    phases = [phase_tree(tmp_path, phase, support) for phase in build.PHASES]
    vendor_path = build.stage_vendor(support_path, *phases, tmp_path / "vendor-output")
    vendor = build.load(vendor_path)
    for resource in vendor["generated_resources"]:
        raw = (Path(vendor["generated_resources_root"]) / resource).read_bytes()
        file(classes, resource, raw)
        reference_entries[resource] = raw
    main = jar(tmp_path, "main.jar", reference_entries)
    rest = jar(tmp_path, "spi.jar", {name: (classes / name).read_bytes() for name in spi})
    return vendor_path, support, main, rest, reference, dependency


def test_measured_shade_preserves_real_origin_maps_and_changed_own_bytes(tmp_path, inputs):
    path, support, main, spi, reference, dependency = measured_shade_case(tmp_path, inputs)
    result = build.verify_final(path, main, spi, tmp_path / "final.json")
    measured = support["shade"]
    own = "br/com/maezo/workload/WorkloadPlugin.class"
    assert measured["pre_shade_classes"][own] != measured["post_shade_classes"][own]
    assert build.load(result)["shade_reference"] == measured
    assert measured["reference_sha256"] == build.sha(reference.read_bytes())
    assert measured["dependencies"][0]["sha256"] == build.sha(dependency.read_bytes())
    assert "NOT_PROVEN_BY_THIS_MAP" in measured["required_root_gate"]


@pytest.mark.parametrize(
    "mutation",
    [
        "reference-own-extra",
        "reference-own-mr",
        "dependency-extra",
        "dependency-drift",
        "final-shade-extra",
        "final-shade-byte-drift",
    ],
)
def test_measured_shade_census_and_dependencies_cannot_be_claimed_or_mutated(tmp_path, inputs, mutation):
    path, support, main, spi, reference, dependency = measured_shade_case(tmp_path, inputs)
    if mutation.startswith("reference-"):
        entries = build.zip_entries(reference)
        name = (
            "br/com/maezo/workload/Extra.class"
            if mutation == "reference-own-extra"
            else "META-INF/versions/17/br/com/maezo/workload/WorkloadPlugin.class"
        )
        entries[name] = b"UnitOnly-extra-not-JVM"
        jar(tmp_path, reference.name, entries)
    elif mutation.startswith("dependency-"):
        entries = build.zip_entries(dependency)
        entries["com/fasterxml/jackson/core/Other.class"] = b"UnitOnly-new-not-JVM"
        jar(tmp_path, dependency.name, entries)
    else:
        entries = build.zip_entries(main)
        name = (
            "br/com/maezo/human/internal/jackson/Other.class"
            if mutation == "final-shade-extra"
            else "br/com/maezo/human/internal/jackson/UnitOnlyFactory.class"
        )
        entries[name] = b"UnitOnly-unadmitted-not-JVM"
        jar(tmp_path, "main.jar", entries)
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, tmp_path / "final.json")
    assert not (tmp_path / "final.json").exists()


@pytest.mark.parametrize(
    "resource",
    [
        "secured-startup-vendor.sha256",
        "secured-startup-descriptors.sha256",
        "provider-native/phase-a/secured-startup-vendor.sha256",
        "provider-native/phase-b/secured-startup-descriptors.sha256",
    ],
)
def test_vendor_classpath_cannot_supply_competing_security_resource_origin(tmp_path, inputs, resource):
    path, support, phases = prepare(tmp_path, inputs)
    for phase in phases:
        jar(
            phase,
            "lib/UnitOnly-competing-resource.jar",
            {resource: b"UnitOnly-unadmitted-policy-not-authority"},
        )
    output = tmp_path / "vendor-output"
    with pytest.raises(build.NativeObservationBuildError):
        build.stage_vendor(path, *phases, output)
    assert not output.exists()
    assert all((phase / "lib/UnitOnly-competing-resource.jar").is_file() for phase in phases)


@pytest.mark.parametrize("module_descriptor", [None, False, True])
def test_measured_shade_retains_declared_mr_dependency_and_records_module_outcome(
    tmp_path, inputs, module_descriptor
):
    path, support, main, spi, reference, dependency = measured_shade_case(
        tmp_path, inputs, mr=True, module_descriptor=module_descriptor
    )
    build.verify_final(path, main, spi, tmp_path / "final.json")
    mapped = "META-INF/versions/11/br/com/maezo/human/internal/jackson/UnitOnlyVersioned.class"
    assert mapped in support["shade"]["post_shade_classes"]
    assert mapped in build.zip_entries(main)
    assert bool(support["shade"]["unpackaged_module_descriptors"]) == (module_descriptor is False)
    assert "META-INF/versions/11/com/fasterxml/jackson/core/UnitOnlyVersioned.class" in build.zip_entries(
        dependency
    )


def test_compiled_own_versioned_alias_refused_before_support_output(tmp_path, inputs):
    classes, tests, output = inputs
    file(
        classes,
        "META-INF/versions/17/br/com/maezo/workload/WorkloadPlugin.class",
        b"UnitOnly-unadmitted-versioned-owner",
    )
    before = build.tree(classes)
    with pytest.raises(build.NativeObservationBuildError):
        build.build_support(classes, tests, FIXTURES, output)
    assert not output.exists() and build.tree(classes) == before


@pytest.mark.parametrize(
    ("resource", "origin"),
    [
        (build.VENDOR_RESOURCES[0], "main"),
        (build.VENDOR_RESOURCES[0], "spi"),
        (build.DESCRIPTOR_RESOURCES[0], "main"),
    ],
)
def test_s3_original_mr_counterproof_rejected_before_any_output(tmp_path, inputs, resource, origin):
    """Original reviewer counterexamples; inert ZIP/text, no classfile/JVM execution."""
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    destination = main if origin == "main" else spi
    entries = build.zip_entries(destination)
    entries["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\nMulti-Release: true\n\n"
    entries["META-INF/versions/17/" + resource] = b"UnitOnly-alias-not-authority"
    jar(tmp_path, destination.name, entries)
    result, context = tmp_path / "final.json", tmp_path / "image-context"
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, result, image_context=context)
    assert not result.exists() and not context.exists()


@pytest.mark.parametrize("resource", (*build.VENDOR_RESOURCES, *build.DESCRIPTOR_RESOURCES))
@pytest.mark.parametrize("origin", ["main", "spi", "support"])
@pytest.mark.parametrize("alias", ["mr", "nested-mr", "dot", "double-slash", "backslash"])
def test_s3_all_phase_resource_aliases_and_competitors_refused(tmp_path, inputs, resource, origin, alias):
    """Whole four-resource/origin matrix; preserve literal bytes and all original class data."""
    path, vendor, support, phases, main, spi = finalize_inputs(tmp_path, inputs)
    destination = {"main": main, "spi": spi, "support": Path(support["support_jar"])}[origin]
    entries = build.zip_entries(destination)
    members = {
        "mr": "META-INF/versions/17/" + resource,
        "nested-mr": "META-INF/versions/17/META-INF/versions/11/" + resource,
        "dot": "./" + resource,
        "double-slash": resource.replace("/", "//", 1),
        "backslash": resource.replace("/", "\\"),
    }
    alias_member = members[alias]
    entries["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\nMulti-Release: true\n\n"
    entries[alias_member] = b"UnitOnly-competing-phase-resource-not-authority"
    jar(destination.parent, destination.name, entries)
    result, context = tmp_path / "final.json", tmp_path / "image-context"
    with pytest.raises(build.NativeObservationBuildError):
        build.verify_final(path, main, spi, result, image_context=context)
    assert not result.exists() and not context.exists()
    assert alias_member in build.zip_entries(destination)  # Refuse, never strip the input.


@pytest.mark.parametrize("resource", (*build.VENDOR_RESOURCES, *build.DESCRIPTOR_RESOURCES))
@pytest.mark.parametrize("origin", ["MAIN", "SPI", "SUPPORT"])
def test_s3_literal_origin_and_bytes_census(resource, origin):
    """Direct inert resource model includes every owner and byte-exact expected entry."""
    expected = {
        name: ("MAIN" if name in build.VENDOR_RESOURCES else "SUPPORT", name.encode())
        for name in (*build.VENDOR_RESOURCES, *build.DESCRIPTOR_RESOURCES)
    }
    artifacts = {
        "MAIN": {name: raw for name, (owner, raw) in expected.items() if owner == "MAIN"},
        "SPI": {},
        "SUPPORT": {name: raw for name, (owner, raw) in expected.items() if owner == "SUPPORT"},
    }
    build.phase_resource_census(artifacts, expected)
    artifacts[origin][resource] = b"UnitOnly-different-literal-bytes"
    with pytest.raises(build.NativeObservationBuildError, match="logical origin/bytes"):
        build.phase_resource_census(artifacts, expected)


@pytest.mark.parametrize("resource", (*build.VENDOR_RESOURCES, *build.DESCRIPTOR_RESOURCES))
@pytest.mark.parametrize("seam", ["reference", "dependency"])
@pytest.mark.parametrize("alias", ["literal", "mr", "nested-mr"])
def test_s3_shade_packaging_seams_cannot_supply_phase_resources(
    tmp_path, inputs, monkeypatch, resource, seam, alias
):
    """Inject before support effect into inert measured reference/dependency ZIPs."""
    original_jar = jar
    target = "measured-first-shade.jar" if seam == "reference" else "UnitOnly-jackson.jar"
    member = {
        "literal": resource,
        "mr": "META-INF/versions/17/" + resource,
        "nested-mr": "META-INF/versions/17/META-INF/versions/11/" + resource,
    }[alias]

    def inject(root, name, entries):
        if name == target:
            entries = {**entries, member: b"UnitOnly-phase-resource-in-foreign-packaging-seam"}
        return original_jar(root, name, entries)

    monkeypatch.setitem(globals(), "jar", inject)
    with pytest.raises(build.NativeObservationBuildError, match="Phase security resource"):
        measured_shade_case(tmp_path, inputs, mr=True)
    assert not inputs[2].exists()
    assert member in build.zip_entries(tmp_path / target)


def test_s3_positive_complete_origins_allow_unrelated_resources_and_measured_mr(tmp_path, inputs):
    path, support, main, spi, reference, dependency = measured_shade_case(tmp_path, inputs, mr=True)
    entries = build.zip_entries(main)
    entries["META-INF/versions/17/UnitOnly-unrelated.txt"] = b"UnitOnly-not-a-security-resource"
    jar(tmp_path, main.name, entries)
    result = build.verify_final(path, main, spi, tmp_path / "final.json", image_context=tmp_path / "context")
    assert build.load(result)["shade_reference"] == support["shade"]
    assert "META-INF/versions/11/br/com/maezo/human/internal/jackson/UnitOnlyVersioned.class" in entries
    assert "META-INF/versions/17/UnitOnly-unrelated.txt" in build.zip_entries(main)


@pytest.mark.parametrize("resource", (*build.VENDOR_RESOURCES, *build.DESCRIPTOR_RESOURCES))
@pytest.mark.parametrize("origin", ["jar", "loose"])
def test_s3_staged_dependency_nested_resource_alias_refuses_before_vendor_effect(
    tmp_path, inputs, resource, origin
):
    path, support, phases = prepare(tmp_path, inputs)
    alias = "META-INF/versions/17/META-INF/versions/11/" + resource
    for phase in phases:
        if origin == "jar":
            jar(phase, "lib/UnitOnly-alias.jar", {alias: b"UnitOnly-foreign-security-resource"})
        else:
            file(phase, "webapps/docs/WEB-INF/classes/" + alias, b"UnitOnly-foreign-security-resource")
    output = tmp_path / "vendor-output"
    with pytest.raises(build.NativeObservationBuildError, match="Phase security resource"):
        build.stage_vendor(path, *phases, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "manifest",
    [
        b"",
        b"Manifest-Version: 1.0\n\n",
        b"Multi-Release: false\n\n",
        b"Multi-Release: true\nMulti-release: false\n\n",
        b"Manifest-Version: 1.0\n\nName: x\nMulti-Release: true\n\n",
    ],
)
def test_versioned_classes_require_unambiguous_main_manifest(manifest):
    entries = {
        "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
        "META-INF/MANIFEST.MF": manifest,
    }
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest"):
        build.require_multi_release_manifest(entries)


def test_multi_release_manifest_main_attribute_and_continuation():
    build.require_multi_release_manifest(
        {
            "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
            "META-INF/MANIFEST.MF": (
                b"Manifest-Version: 1.0\r\nMulti-Release: tr\r\n ue\r\n\r\n"
                b"Name: x\r\nMulti-Release: false\r\n\r\n"
            ),
        }
    )


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("continuation", [False, True])
def test_multi_release_manifest_rejects_unterminated_main_header(ending, continuation):
    tail = b"Multi-Release: tr" + ending + b" ue" if continuation else b"Multi-Release: true"
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest invalid"):
        build.require_multi_release_manifest(
            {
                "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
                "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0" + ending + tail,
            }
        )


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_multi_release_manifest_accepts_jdk_line_endings(ending):
    build.require_multi_release_manifest(
        {
            "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
            "META-INF/MANIFEST.MF": ending.join(
                [b"Manifest-Version: 1.0", b"mUlTi-ReLeAsE: tr", b" ue", b"", b""]
            ),
        }
    )


@pytest.mark.parametrize("separator", ["\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"])
def test_multi_release_manifest_unicode_separator_is_not_jdk_newline(separator):
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest"):
        build.require_multi_release_manifest(
            {
                "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
                "META-INF/MANIFEST.MF": (
                    "Manifest-Version: 1.0" + separator + "Multi-Release: true\n\n"
                ).encode(),
            }
        )


def test_unterminated_main_manifest_refuses_shade_reference_before_support_effect(tmp_path, inputs):
    path, support, main, spi, reference, dependency = measured_shade_case(tmp_path, inputs, mr=True)
    entries = build.zip_entries(reference)
    entries["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\nMulti-Release: true"
    jar(tmp_path, reference.name, entries)
    # Return only our inert compilation tree to its measured pre-vendor state.
    for resource in build.VENDOR_RESOURCES:
        (Path(support["classes"]) / resource).unlink()
    output = tmp_path / "rejected-support"
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest invalid"):
        build.build_support(
            Path(support["classes"]),
            Path(support["test_classes"]),
            FIXTURES,
            output,
            shade_reference=reference,
            shade_dependencies=(dependency,),
        )
    assert not output.exists()


def test_unterminated_main_manifest_refuses_final_before_image_context_effect(tmp_path, inputs):
    path, support, main, spi, reference, dependency = measured_shade_case(tmp_path, inputs, mr=True)
    entries = build.zip_entries(main)
    entries["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\nMulti-Release: true"
    jar(tmp_path, main.name, entries)
    output = tmp_path / "rejected-final.json"
    context = tmp_path / "rejected-image-context"
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest invalid"):
        build.verify_final(path, main, spi, output, image_context=context)
    assert not output.exists()
    assert not context.exists()


@pytest.mark.parametrize("manifest", [None, b"Manifest-Version: 1.0\nMulti-Release: false\n\n"])
def test_final_jar_cannot_disable_measured_multi_release_loader(tmp_path, inputs, manifest):
    path, support, main, spi, reference, dependency = measured_shade_case(tmp_path, inputs, mr=True)
    entries = build.zip_entries(main)
    if manifest is None:
        entries.pop("META-INF/MANIFEST.MF")
    else:
        entries["META-INF/MANIFEST.MF"] = manifest
    jar(tmp_path, main.name, entries)
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest required"):
        build.verify_final(path, main, spi, tmp_path / "final-disabled-mr")


# Finite JDK main-attribute grammar; inert ZIP/class placeholders only.
INVALID_MAIN_HEADERS = (
    b"Bad Header: UnitOnly",
    b"Bad\tHeader: UnitOnly",
    b"Bad.Header: UnitOnly",
    b"Bad\x00Header: UnitOnly",
    b"B\xc3\xa4d: UnitOnly",
    b": UnitOnly",
    b"A" * 71 + b": UnitOnly",
    b"Bad:Header: UnitOnly",
    b"Bad : UnitOnly",
    b"Bad:UnitOnly",
    b"Bad:\tUnitOnly",
    b"Bad:",
    b"Bad\n Header: UnitOnly",
    b"Bad:\n  UnitOnly",
    b"Bad: " + b"x" * 507,  # 512 bytes, exceeds JarFile's 512-byte line buffer.
)


@pytest.mark.parametrize("header", INVALID_MAIN_HEADERS)
def test_main_attribute_grammar_rejects_invalid_physical_header(header):
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest invalid"):
        build.require_multi_release_manifest(
            {
                "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
                "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\n" + header + b"\nMulti-Release: true\n\n",
            }
        )


@pytest.mark.parametrize("header", [b"Multi-Relea\n se: true", b"Multi-Release:\n  true"])
def test_continuation_cannot_construct_attribute_name_or_separator(header):
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest invalid"):
        build.require_multi_release_manifest(
            {
                "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
                "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\n" + header + b"\n\n",
            }
        )


@pytest.mark.parametrize("value", [b" true", b"true ", b"\ttrue", b"true\t", b"tr\n  ue", b"true\n  "])
def test_main_multi_release_value_spaces_are_significant(value):
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest required"):
        build.require_multi_release_manifest(
            {
                "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
                "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\nMulti-Release: " + value + b"\n\n",
            }
        )


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize(
    "header", [b"A: ", b"A" * 70 + b": UnitOnly", b"_0-a: UnitOnly", b"Long: " + b"x" * 504]
)
def test_main_attribute_grammar_accepts_jdk_name_and_physical_bounds(ending, header):
    build.require_multi_release_manifest(
        {
            "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
            "META-INF/MANIFEST.MF": ending.join([header, b"mUlTi-ReLeAsE: Tr", b" Ue", b"", b""]),
        }
    )


@pytest.mark.parametrize("seam", ["reference", "final"])
@pytest.mark.parametrize(
    "header",
    [b"Bad Header: UnitOnly", b"A" * 71 + b": UnitOnly", b"Multi-Relea\n se: true", b"Bad: " + b"x" * 507],
)
def test_invalid_main_attribute_refuses_reference_and_final_effect(tmp_path, inputs, seam, header):
    path, support, main, spi, reference, dependency = measured_shade_case(tmp_path, inputs, mr=True)
    artifact = reference if seam == "reference" else main
    entries = build.zip_entries(artifact)
    entries["META-INF/MANIFEST.MF"] = b"Manifest-Version: 1.0\n" + header + b"\nMulti-Release: true\n\n"
    jar(tmp_path, artifact.name, entries)
    output = tmp_path / "rejected-header-output"
    context = tmp_path / "rejected-header-context"
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest invalid"):
        if seam == "reference":
            for resource in build.VENDOR_RESOURCES:
                (Path(support["classes"]) / resource).unlink()
            build.build_support(
                Path(support["classes"]),
                Path(support["test_classes"]),
                FIXTURES,
                output,
                shade_reference=reference,
                shade_dependencies=(dependency,),
            )
        else:
            build.verify_final(path, main, spi, output, image_context=context)
    assert not output.exists() and not context.exists()


@pytest.mark.parametrize("ending", [b"\n", b"\r"])
def test_single_byte_terminator_uses_full_jdk_buffer(ending):
    build.require_multi_release_manifest(
        {
            "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
            "META-INF/MANIFEST.MF": b"Long: "
            + b"x" * 505
            + ending
            + b"Multi-Release: true"
            + ending
            + ending,
        }
    )


def test_crlf_physical_line_must_fit_buffer_with_both_terminator_bytes():
    with pytest.raises(build.NativeObservationBuildError, match="Multi-Release manifest invalid"):
        build.require_multi_release_manifest(
            {
                "META-INF/versions/17/UnitOnly.class": b"UnitOnly-not-bytecode",
                "META-INF/MANIFEST.MF": b"Long: " + b"x" * 505 + b"\r\nMulti-Release: true\r\n\r\n",
            }
        )


# These pins are inert UnitOnly census data, never compiled-custody authority.
def measurement_census():
    return {name: "1" * 64 for name in build.MEASUREMENT_CLASSES}


@pytest.mark.parametrize("mutation", ("missing-inner", "extra-inner", "extra-owner", "versioned-owner"))
def test_measurement_expected_census_is_literal_eleven(mutation):
    expected = measurement_census()
    if mutation == "missing-inner":
        del expected[build.MEASUREMENT_OWNER + "$FinalOutcome.class"]
    elif mutation == "extra-inner":
        expected[build.MEASUREMENT_OWNER + "$Unexpected.class"] = "1" * 64
    elif mutation == "extra-owner":
        expected["br/com/maezo/workload/OtherMeasurement.class"] = "1" * 64
    else:
        expected["META-INF/versions/17/" + build.MEASUREMENT_OWNER + ".class"] = "1" * 64
    with pytest.raises(build.NativeObservationBuildError, match="Closed measurement class map"):
        build.measurement_entries(expected, expected)


@pytest.mark.parametrize(
    "mutation",
    ("extra-inner", "versioned-duplicate", "nested-versioned-duplicate", "changed-byte", "missing-inner"),
)
def test_measurement_actual_census_must_equal_root_pins(mutation):
    expected = measurement_census()
    actual = dict(expected)
    if mutation == "extra-inner":
        actual[build.MEASUREMENT_OWNER + "$Unexpected.class"] = "1" * 64
    elif mutation == "versioned-duplicate":
        actual["META-INF/versions/17/" + build.MEASUREMENT_OWNER + ".class"] = "1" * 64
    elif mutation == "nested-versioned-duplicate":
        actual["META-INF/versions/17/META-INF/versions/17/" + build.MEASUREMENT_OWNER + ".class"] = "1" * 64
    elif mutation == "changed-byte":
        actual[build.MEASUREMENT_OWNER + ".class"] = "2" * 64
    else:
        del actual[build.MEASUREMENT_OWNER + "$State.class"]
    with pytest.raises(build.NativeObservationBuildError, match="Closed measurement class census"):
        build.measurement_entries(actual, expected)


def test_measurement_missing_root_compilation_refuses_unverified_input(tmp_path):
    path = tmp_path / "measurement-inputs.json"
    path.write_bytes(
        build.json_bytes(
            {
                "schema": build.MEASUREMENT_INPUT_SCHEMA,
                "source_freeze": {"path": str(tmp_path / "inert-freeze.json"), "sha256": "1" * 64},
                "protocol_schema": {"name": build.MEASUREMENT_SCHEMA, "sha256": build.MEASUREMENT_SCHEMA_SHA},
                "sources": {},
                "support_classes": measurement_census(),
            }
        )
    )
    with pytest.raises(build.NativeObservationBuildError, match="Measurement input keys differ"):
        build.measurement_inputs(path, {}, {})


@pytest.mark.parametrize(
    "name", (build.MEASUREMENT_SCHEMA, "META-INF/versions/17/" + build.MEASUREMENT_SCHEMA)
)
def test_measurement_schema_shadow_origin_is_refused(name):
    with pytest.raises(build.NativeObservationBuildError, match="Measurement schema logical origin"):
        build.measurement_resource_census({"SUPPORT": {name: b"UnitOnly-shadow"}}, None)


def test_measurement_class_release_preview_refused():
    with pytest.raises(build.NativeObservationBuildError, match="Java 17 without preview"):
        build.class_metadata(b"\xca\xfe\xba\xbe\xff\xff\x00\x3d")


@pytest.mark.parametrize(
    "counts", [{"main": True, "test": 1}, {"main": 1.0, "test": 1}, {"main": 1, "test": True}]
)
def test_root_compile_counts_require_json_integers(monkeypatch, counts):
    record = _unit_compilation_record(counts=counts)
    monkeypatch.setattr(build, "pinned_record", lambda *args: record)
    with pytest.raises(build.NativeObservationBuildError, match="single compilation"):
        build.root_compilation({}, {}, {}, {})


def _unit_compilation_record(*, counts=None, release=17):
    # Boundary counterproof only: no executable/compiler/runtime authority.
    return dict(
        schema="provider-native-independent-measurement-root-compilation.v1",
        scope="ROOT_SINGLE_COMPILATION_CUSTODY_NOT_RUNTIME",
        candidate_sha="a" * 40,
        source_files={},
        main_compilation={},
        test_compilation={},
        support_classes={},
        compilation_commands={"main": "UnitOnly", "test": "UnitOnly"},
        compile_counts={"main": 1, "test": 1} if counts is None else counts,
        compiler={"release": release, "version": "UnitOnly", "executable": {}},
        dependency_jars={},
    )


def test_root_compile_release_requires_json_integer(monkeypatch):
    monkeypatch.setattr(build, "pinned_record", lambda *args: _unit_compilation_record(release=17.0))
    with pytest.raises(build.NativeObservationBuildError, match="release/version"):
        build.root_compilation({}, {}, {}, {})


@pytest.mark.parametrize("loose", [False, True])
def test_nested_mr_vendor_own_class_alias_refused(tmp_path, loose):
    own = "br/com/maezo/workload/StartupAdmission.class"
    member = "META-INF/versions/17/META-INF/versions/11/" + own
    origin = "webapps/docs/WEB-INF/classes/" + member if loose else "lib/vendor.jar"
    if loose:
        file(tmp_path, origin, b"UnitOnly")
    else:
        jar(tmp_path, origin, {member: b"UnitOnly"})
    # No positive origin expected; the hidden alias itself must cause refusal.
    with pytest.raises(build.NativeObservationBuildError, match="own class origin"):
        build.verify_owner_origins(
            tmp_path, {origin: "UnitOnly"}, {"support_classes": {}, "descriptor_resources": {}}
        )
