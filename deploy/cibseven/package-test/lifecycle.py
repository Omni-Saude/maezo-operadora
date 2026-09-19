#!/usr/bin/env python3
"""ROOT-owned serial package lifecycle; caller retains the canonical engine locks.

No test selectors are changed. Run the five human tests between start-human and
start-d7: the secure cutover deliberately removes the plaintext listener.
All Docker output, environment documents and credentials remain private.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree as ET

import httpx

FAMILIES = {
    "human": ("tests/integration/test_portal_engine_package.py", 5),
    "d7": ("tests/integration/test_portal_engine_d7_package.py", 1119),
    "q2": ("tests/integration/test_portal_engine_reads.py", 3),
    "native-v2": ("tests/integration/test_native_acquisition_v2.py", 10),
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("fixture source module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dump(path: Path, data: object) -> None:
    # Exclusive temporary inode prevents permissive defaults and symlink writes.
    temporary = path.with_name(path.name + "." + uuid4().hex)
    with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as out:
        json.dump(data, out, indent=2)
        out.write("\n")
    os.replace(temporary, path)


def inventory(nodeids: list[str]) -> dict:
    """Account actual pytest collection; counts are diagnostic historical anchors."""
    if not isinstance(nodeids, list) or any(
        not isinstance(node, str) or "::" not in node for node in nodeids
    ):
        raise ValueError("actual collected pytest nodeids required")
    if len(nodeids) != len(set(nodeids)):
        raise ValueError("duplicate collected test identities")
    families = {}
    assigned = set()
    for name, (module, baseline) in FAMILIES.items():
        selected = [node for node in nodeids if node.startswith(module + "::")]
        families[name] = {"nodeids": selected, "count": len(selected), "historical_count": baseline}
        assigned.update(selected)
    return {"families": families, "other_nodeids": [node for node in nodeids if node not in assigned]}


OWNER_LABEL = "org.maezo.fixture-owner"


class OwnershipError(ValueError):
    """Ownership failure never grants logging or removal authority."""


def raise_failures(label: str, failures: list[BaseException]) -> None:
    if len(failures) > 1:
        raise BaseExceptionGroup(label, failures)
    if failures:
        raise failures[0]


def ownership_failure(error: BaseException) -> bool:
    return isinstance(error, OwnershipError) or (
        isinstance(error, BaseExceptionGroup) and any(ownership_failure(exc) for exc in error.exceptions)
    )


def private_file_identity(path: Path) -> dict[str, str | int]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("private single-link capture output required")
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        os.fsync(source.fileno())
    return {"path": path.name, "bytes": info.st_size, "sha256": digest}


class CommandError(RuntimeError):
    def __init__(self, stderr: str):
        super().__init__("fixture command failed; inspect owned private lifecycle log")
        self.stderr = stderr


def private_tree(root: Path) -> None:
    # The fixture directory is caller-owned and mode 0700. Never admit linked
    # child inputs or outputs, including links supplied before a resumed step.
    for path in root.rglob("*"):
        info = path.lstat()
        if info.st_uid != os.geteuid() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("fixture children must be owned regular files or directories")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError("fixture files must not have external hard links")
        if stat.S_ISDIR(info.st_mode) and info.st_mode & 0o077:
            raise ValueError("fixture child directories must be private")


class Lifecycle:
    def __init__(self, root: Path):
        if not root.is_absolute() or root.resolve(strict=True) != root or root.stat().st_mode & 0o077:
            raise ValueError("canonical private fixture directory required")
        if root.stat().st_uid != os.geteuid():
            raise ValueError("fixture must belong to the caller")
        private_tree(root)
        self.root = root
        self.metadata = json.loads((root / "d7-fixture.json").read_text())
        self.state = json.loads((root / "lifecycle.json").read_text())
        self.project = self.state["project"]
        if not re.fullmatch(r"d7-[a-z0-9-]{8,64}", self.project):
            raise ValueError("explicit unique ROOT-owned project required")
        if self.metadata.get("fixture_only") is not True:
            raise ValueError("disposable fixture marker required")
        self.checkout = Path(self.metadata["checkout"])
        self.log = root / "lifecycle-private.log"

    def run(self, argv: list[str], *, capture: bool = False, environment: dict | None = None) -> str:
        private_tree(self.root)
        descriptor = os.open(self.log, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "ab") as log:
            info = os.fstat(log.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("private regular log required")
            result = subprocess.run(
                argv,
                cwd=self.checkout,
                stdout=subprocess.PIPE if capture else log,
                stderr=subprocess.PIPE,
                check=False,
                timeout=300,
                env=environment,
            )
            log.write(result.stderr)
        if result.returncode:
            raise CommandError(result.stderr.decode(errors="replace"))
        return result.stdout.decode().strip() if capture else ""

    def resources(self, project: str) -> list[tuple[str, str]]:
        result = []
        for kind in ("container", "volume", "network"):
            args = ["docker", kind, "ls", "-q"]
            if kind == "container":
                args.extend(["-a", "--no-trunc"])
            ids = self.run([*args, "--filter", "label=com.docker.compose.project=" + project], capture=True)
            result.extend((kind, item) for item in ids.splitlines() if item)
        return result

    def assert_owned(self, kind: str, identity: str) -> dict:
        records = json.loads(self.run(["docker", kind, "inspect", identity], capture=True))
        if len(records) != 1:
            raise ValueError("ambiguous fixture resource")
        record = records[0]
        labels = (
            record.get("Config", {}).get("Labels", {}) if kind == "container" else record.get("Labels", {})
        )
        if not self.state.get("owner") or (labels or {}).get(OWNER_LABEL) != self.state["owner"]:
            raise OwnershipError("refusing mutation of an unowned Docker resource")
        return record

    def verify_project_ownership(self, project: str) -> None:
        for kind, identity in self.resources(project):
            record = self.assert_owned(kind, identity)
            labels = (
                record.get("Config", {}).get("Labels", {})
                if kind == "container"
                else record.get("Labels", {})
            )
            if (labels or {}).get("com.docker.compose.project") != project:
                raise OwnershipError("Docker project label changed")
            if kind == "container" and (
                not re.fullmatch(r"[0-9a-f]{64}", identity) or record.get("Id") != identity
            ):
                raise OwnershipError("exact Docker container identity required")
            if (
                kind == "container"
                and project == self.project
                and (labels or {}).get("com.docker.compose.service") not in {"engine", "postgres"}
            ):
                raise OwnershipError("unexpected service in fixture project")

    def capture_engine_logs(self, reason: str) -> None:
        """Capture actual owned engines before removal; never treat absence as log custody."""
        private_tree(self.root)
        if not re.fullmatch(r"[0-9a-f]{32}", self.state.get("owner", "")):
            raise OwnershipError("exact fixture owner nonce required before log capture")
        directory = self.root / ("engine-logs-" + uuid4().hex)
        directory.mkdir(mode=0o700)
        report: dict[str, Any] = {
            "reason": reason,
            "phase": self.state["phase"],
            "project": self.project,
            "owner_sha256": hashlib.sha256(self.state["owner"].encode()).hexdigest(),
            "started_at_ns": time.time_ns(),
            "outcome": "FAILED",
            "records": [],
        }
        failures: list[BaseException] = []
        try:
            # Admit the entire project before any log read, then inspect each exact engine again.
            self.verify_project_ownership(self.project)
            engines = []
            for kind, identity in self.resources(self.project):
                if kind != "container":
                    continue
                record = self.assert_owned(kind, identity)
                labels = record.get("Config", {}).get("Labels", {}) or {}
                if record.get("Id") != identity or labels.get("com.docker.compose.project") != self.project:
                    raise OwnershipError("engine identity/project drift before log capture")
                if labels.get("com.docker.compose.service") == "engine":
                    if not re.fullmatch(r"[0-9a-f]{64}", identity):
                        raise OwnershipError("full engine identity required before log capture")
                    if not re.fullmatch(r"sha256:[0-9a-f]{64}", record.get("Image", "")):
                        raise ValueError("invalid engine image identity in log capture")
                    engines.append((identity, record.get("Image")))
            report["outcome"] = "ABSENT" if not engines else "CAPTURED"
            report["engine_present"] = bool(engines)
            for identity, image_id in engines:
                entry: dict[str, Any] = {
                    "container_id": identity,
                    "image_id": image_id,
                    "returncode": None,
                    "outcome": "FAILED",
                }
                report["records"].append(entry)
                outputs = {
                    stream: directory / (identity + "." + stream + ".private.log")
                    for stream in ("stdout", "stderr")
                }
                try:
                    # Recheck nonce, project and service at the last boundary before reading logs.
                    current = self.assert_owned("container", identity)
                    labels = current.get("Config", {}).get("Labels", {}) or {}
                    if (
                        current.get("Id") != identity
                        or labels.get("com.docker.compose.project") != self.project
                        or labels.get("com.docker.compose.service") != "engine"
                    ):
                        raise OwnershipError("engine ownership drift before log capture")
                    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
                    with (
                        os.fdopen(os.open(outputs["stdout"], flags, 0o600), "wb") as stdout,
                        os.fdopen(os.open(outputs["stderr"], flags, 0o600), "wb") as stderr,
                    ):
                        completed = subprocess.run(
                            ["docker", "logs", "--timestamps", identity],
                            cwd=self.checkout,
                            stdout=stdout,
                            stderr=stderr,
                            timeout=30,
                            check=False,
                        )
                    entry["returncode"] = completed.returncode
                    if completed.returncode:
                        raise RuntimeError("owned engine log capture failed; private streams retained")
                    entry["outcome"] = "CAPTURED"
                except BaseException as exc:
                    entry["error_type"] = type(exc).__name__
                    failures.append(exc)
                finally:
                    for stream, path in outputs.items():
                        if path.exists():
                            entry[stream] = private_file_identity(path)
                if failures and (ownership_failure(failures[-1]) or not isinstance(failures[-1], Exception)):
                    break
            if not engines and self.state["phase"] in {"human", "human-qualified", "starting-d7", "d7"}:
                failures.append(RuntimeError("engine already absent before capture; log custody missing"))
        except BaseException as exc:
            report["error_type"] = type(exc).__name__
            failures.append(exc)
        if failures:
            report["outcome"] = "FAILED"
        report["finished_at_ns"] = time.time_ns()
        try:
            dump(directory / "capture.json", report)
            private_file_identity(directory / "capture.json")
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException as exc:
            failures.append(exc)
        raise_failures("engine capture failures", failures)

    def jar_hash(self, container: str) -> str:
        # A new private directory prevents docker cp from following preexisting
        # destination links. Keep all extracted bytes within fixture custody.
        with tempfile.TemporaryDirectory(prefix="jar-", dir=self.root) as directory:
            target = Path(directory) / "installed.jar"
            self.run(["docker", "cp", container + ":/camunda/lib/maezo-human-command.jar", str(target)])
            private_tree(self.root)
            target.chmod(0o600)
            return hashlib.sha256(target.read_bytes()).hexdigest()

    def verify_running_identity(self, phase: str) -> dict:
        identities = {}
        private_tree(self.root)
        document = json.loads((self.root / f"{phase}-compose.json").read_text())
        config_hashes = self.configuration_hashes(phase)
        if config_hashes != self.state.get("config_hashes", {}).get(phase):
            raise ValueError("fixture configuration changed since startup")
        identities["configuration_sha256"] = config_hashes
        for service in ("engine", "postgres"):
            ids = self.compose(phase, "ps", "-q", service, capture=True).splitlines()
            if len(ids) != 1:
                raise ValueError("exactly one running fixture service required")
            record = self.assert_owned("container", ids[0])
            expected = document["services"][service]["image"]
            expected_id = self.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", expected], capture=True
            )
            if record.get("Image") != expected_id or record.get("State", {}).get("Running") is not True:
                raise ValueError("running fixture image identity mismatch")
            if service == "engine":
                if expected != self.metadata[phase + "_image"] or expected_id != expected:
                    raise ValueError("running engine does not match candidate receipt")
                jar = self.jar_hash(ids[0])
                if jar != self.metadata["image_receipt"][phase + "_jar_sha256"]:
                    raise ValueError("running JAR differs from candidate build output")
                identities["jar_sha256"] = jar
            config = document["services"][service]
            for key, value in config.get("environment", {}).items():
                if f"{key}={value}" not in record.get("Config", {}).get("Env", []):
                    raise ValueError("running fixture environment mismatch")
            for mount in config.get("volumes", []):
                if isinstance(mount, dict):
                    matches = [
                        item
                        for item in record.get("Mounts", [])
                        if item.get("Destination") == mount["target"]
                    ]
                    if (
                        len(matches) != 1
                        or matches[0].get("Source") != mount["source"]
                        or matches[0].get("RW") is not False
                    ):
                        raise ValueError("running fixture configuration mount mismatch")
                else:
                    destination = mount.split(":")[1]
                    matches = [
                        item for item in record.get("Mounts", []) if item.get("Destination") == destination
                    ]
                    if len(matches) != 1 or matches[0].get("Type") != "volume":
                        raise ValueError("owned database volume required")
                    self.assert_owned("volume", matches[0]["Name"])
                    identities["database_volume"] = matches[0]["Name"]
            identities[service] = {"container": ids[0], "image": expected_id}
        return identities

    def configuration_hashes(self, phase: str) -> dict[str, str]:
        private_tree(self.root)
        path = self.root / f"{phase}-compose.json"
        document = json.loads(path.read_text())
        hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}
        for service in document["services"].values():
            image = service["image"]
            if not re.fullmatch(r"(?:sha256:|[^\s]+@sha256:)[a-f0-9]{64}", image):
                raise ValueError("immutable fixture image required")
            for mount in service.get("volumes", []):
                if isinstance(mount, dict):
                    source = Path(mount["source"])
                    if (
                        source.parent != self.root
                        or source.resolve(strict=True) != source
                        or not source.is_file()
                    ):
                        raise ValueError("owned canonical fixture mount required")
                    if mount.get("read_only") is not True:
                        raise ValueError("configuration mounts must be read only")
                    hashes[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
        return hashes

    def compose(self, phase: str, *args: str, capture: bool = False) -> str:
        return self.run(
            ["docker", "compose", "-p", self.project, "-f", str(self.root / f"{phase}-compose.json"), *args],
            capture=capture,
        )

    def save(self, phase: str) -> None:
        self.state["phase"] = phase
        dump(self.root / "lifecycle.json", self.state)

    def verify_source(self) -> None:
        sha = self.run(["git", "rev-parse", "HEAD"], capture=True)
        dirty = self.run(["git", "status", "--porcelain", "--untracked-files=all"], capture=True)
        if sha != self.metadata["source_sha"] or dirty:
            raise ValueError("candidate source changed after fixture preparation")

    def verify_images(self) -> None:
        receipt = self.metadata["image_receipt"]
        if receipt["source_sha"] != self.metadata["source_sha"]:
            raise ValueError("image source disagrees with fixture")
        for name in ("bootstrap", "secured"):
            image = self.metadata[name + "_image"]
            actual = self.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture=True)
            if actual != image or receipt[name + "_image"] != image:
                raise ValueError("candidate image identity mismatch")
            container = self.project + "-extract-" + uuid4().hex
            self.state.setdefault("extract_containers", []).append(container)
            dump(self.root / "lifecycle.json", self.state)
            try:
                self.run(
                    [
                        "docker",
                        "create",
                        "--name",
                        container,
                        "--label",
                        OWNER_LABEL + "=" + self.state["owner"],
                        image,
                    ]
                )
                self.assert_owned("container", container)
                if self.jar_hash(container) != receipt[name + "_jar_sha256"]:
                    raise ValueError("installed JAR differs from candidate build output")
            finally:
                self.cleanup_extracts()

    def cleanup_extracts(self) -> None:
        for container in list(self.state.get("extract_containers", [])):
            ids = self.run(
                ["docker", "container", "ls", "-aq", "--filter", "name=^/" + container + "$"], capture=True
            )
            if ids:
                record = self.assert_owned("container", container)
                if record.get("State", {}).get("Running") is True:
                    self.run(["docker", "stop", container])
                self.run(["docker", "rm", container])
            self.state["extract_containers"].remove(container)
            dump(self.root / "lifecycle.json", self.state)

    def mount_preflight(self) -> None:
        marker = self.root / "public-mount-marker"
        private_tree(self.root)
        expected = os.urandom(32).hex()
        with os.fdopen(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as output:
            output.write(expected)
        image = self.metadata["bootstrap_image"]
        container = self.project + "-mount-" + uuid4().hex
        self.state.setdefault("extract_containers", []).append(container)
        dump(self.root / "lifecycle.json", self.state)
        try:
            actual = self.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    container,
                    "--label",
                    OWNER_LABEL + "=" + self.state["owner"],
                    "--user",
                    "0:0",
                    "--entrypoint",
                    "cat",
                    "--mount",
                    f"type=bind,src={marker},dst=/probe,readonly",
                    image,
                    "/probe",
                ],
                capture=True,
            )
        finally:
            self.cleanup_extracts()
        if actual != expected:
            raise ValueError("daemon cannot read exact private-root mount bytes")
        missing = self.root / ("missing-" + uuid4().hex)
        probe = self.root / "negative-mount-compose.json"
        project = self.project + "-probe-" + uuid4().hex[:12]
        if self.resources(project):
            raise ValueError("refusing adoption of an existing probe project")
        self.state["probe_project"] = project
        dump(self.root / "lifecycle.json", self.state)
        dump(
            probe,
            {
                "services": {
                    "probe": {
                        "image": image,
                        "labels": {OWNER_LABEL: self.state["owner"]},
                        "entrypoint": ["cat", "/probe"],
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(missing),
                                "target": "/probe",
                                "read_only": True,
                                "bind": {"create_host_path": False},
                            }
                        ],
                    }
                },
                "networks": {"default": {"labels": {OWNER_LABEL: self.state["owner"]}}},
            },
        )
        failed = False
        try:
            self.run(["docker", "compose", "-p", project, "-f", str(probe), "run", "--rm", "probe"])
        except CommandError as error:
            failed = "bind source path does not exist" in error.stderr and str(missing) in error.stderr
            if not failed:
                raise
        finally:
            self.cleanup_probe()
        if not failed or missing.exists():
            raise ValueError("missing bind source must be refused without host-path creation")

    def ready(self, phase: str, *, timeout: int = 120) -> None:
        context = ssl.create_default_context(cafile=self.root / "ca.crt")
        purpose = "command" if phase == "bootstrap" else "agent"
        context.load_cert_chain(self.root / f"{purpose}.crt", self.root / f"{purpose}.key")
        deadline = time.monotonic() + timeout
        with httpx.Client(verify=context, trust_env=False, follow_redirects=False, timeout=5) as client:
            while True:
                try:
                    if phase == "bootstrap":
                        response = client.post("https://localhost:18443/maezo-human/v1/commands", json={})
                        valid = response.status_code == 400 and response.json() == {
                            "error": "INVALID_COMMAND"
                        }
                    else:
                        response = client.get("https://localhost:18443/engine-rest/maezo/v1/readiness")
                        body = response.json()
                        digest = hashlib.sha256((self.root / "boundary.json").read_bytes()).hexdigest()
                        valid = (
                            response.status_code == 200
                            and body.get("ready") is True
                            and bool(body.get("capabilities"))
                            and body.get("policy_digest") == digest
                        )
                    if valid and response.headers.get("cache-control") == "no-store":
                        return
                except (httpx.TransportError, ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("live mTLS plugin/readiness admission failed; private logs retained")
                time.sleep(1)

    def start_human(self) -> None:
        if self.state["phase"] != "prepared":
            raise ValueError("start-human requires newly prepared fixture")
        self.verify_source()
        for port in (15433, 18080, 18443):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        if self.resources(self.project):
            raise ValueError("refusing adoption of an existing Compose project")
        self.state["owner"] = uuid4().hex
        document = json.loads((self.root / "bootstrap-compose.json").read_text())
        labels = {OWNER_LABEL: self.state["owner"]}
        for service in document["services"].values():
            service["labels"] = {**service.get("labels", {}), **labels}
        document["networks"] = {"default": {"labels": labels}}
        document["volumes"] = {"postgres-data": {"labels": labels}}
        document["services"]["postgres"]["volumes"].append("postgres-data:/var/lib/postgresql/data")
        dump(self.root / "bootstrap-compose.json", document)
        self.save("claimed")
        self.verify_images()
        self.mount_preflight()
        self.state.setdefault("config_hashes", {})["bootstrap"] = self.configuration_hashes("bootstrap")
        self.save("starting-human")
        self.compose("bootstrap", "up", "-d", "--wait", "postgres")
        self.compose("bootstrap", "up", "-d", "engine")
        self.ready("bootstrap")
        version = self.compose(
            "bootstrap",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "maezo",
            "-d",
            "maezo",
            "-tAc",
            "SELECT current_setting('server_version_num')",
            capture=True,
        )
        if not version.isdigit() or not 160000 <= int(version) < 170000:
            raise ValueError("the disposable package database must be PostgreSQL 16")
        self.save("human")

    def test_human(self) -> None:
        if self.state["phase"] != "human":
            raise ValueError("admitted human bootstrap required")
        self.verify_source()
        self.verify_running_identity("bootstrap")
        self.ready("bootstrap", timeout=1)
        settings = json.loads((self.root / "test-env.json").read_text())
        junit = self.root / "human-package-junit.xml"
        self.run(
            [
                sys.executable,
                "-m",
                "pytest",
                FAMILIES["human"][0],
                "-q",
                "--no-cov",
                "--junitxml=" + str(junit),
            ],
            environment={**os.environ, **settings},
        )
        cases = ET.parse(junit).findall(".//testcase")
        if len(cases) != 5 or any(
            case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")
        ):
            raise ValueError("all five human package tests must pass before cutover")
        self.verify_source()
        self.save("human-qualified")

    def start_d7(self) -> None:
        if self.state["phase"] != "human-qualified":
            raise ValueError("human bootstrap and five package tests precede secured cutover")
        self.verify_source()
        self.verify_project_ownership(self.project)
        self.verify_running_identity("bootstrap")
        self.ready("bootstrap", timeout=1)
        helper = load_module("secured_fixture", self.checkout / "deploy/cibseven/secured/prepare_fixture.py")
        helper.seed(self.root)
        self.state.setdefault("config_hashes", {})["secured"] = self.configuration_hashes("secured")
        self.save("starting-d7")
        # Only engine is replaced. Never recreate or stop the seeded PostgreSQL.
        self.capture_engine_logs("human-to-d7-cutover")
        self.compose("bootstrap", "stop", "engine")
        self.compose("bootstrap", "rm", "-f", "engine")
        self.compose("secured", "up", "-d", "--no-deps", "engine")
        self.ready("secured")
        env = json.loads((self.root / "test-env.json").read_text())
        env.update(MAEZO_D7_PACKAGE_FIXTURE=str(self.root), MAEZO_D7_COMPOSE_PROJECT=self.project)
        dump(self.root / "d7-test-env.json", env)
        self.save("d7")

    def preflight(self) -> None:
        self.verify_source()
        phase = self.state["phase"]
        if phase not in ("human", "human-qualified", "d7"):
            raise ValueError("running phase required")
        identities = self.verify_running_identity("secured" if phase == "d7" else "bootstrap")
        self.ready("secured" if phase == "d7" else "bootstrap", timeout=1)
        if phase == "d7":
            prior = os.environ.get("MAEZO_D7_PACKAGE_FIXTURE")
            try:
                os.environ["MAEZO_D7_PACKAGE_FIXTURE"] = str(self.root)
                test = load_module("d7_live_preflight", self.checkout / FAMILIES["d7"][0])
                asyncio.run(test.assert_bound_resources())
            finally:
                if prior is None:
                    os.environ.pop("MAEZO_D7_PACKAGE_FIXTURE", None)
                else:
                    os.environ["MAEZO_D7_PACKAGE_FIXTURE"] = prior
        dump(
            self.root / "preflight-receipt.json",
            {
                "source_sha": self.metadata["source_sha"],
                "phase": phase,
                "python": sys.version.split()[0],
                "interpreter": sys.executable,
                "admission": "verified",
                "runtime_identities": identities,
                "broad_tests_executed": False,
            },
        )

    def cleanup_probe(self) -> None:
        project = self.state.get("probe_project")
        if project:
            self.verify_project_ownership(project)
            if self.resources(project):
                self.run(
                    [
                        "docker",
                        "compose",
                        "-p",
                        project,
                        "-f",
                        str(self.root / "negative-mount-compose.json"),
                        "down",
                    ]
                )
            if self.resources(project):
                raise RuntimeError("owned probe resources remain")
            self.state.pop("probe_project")
            dump(self.root / "lifecycle.json", self.state)

    def stop(self) -> None:
        if not self.state.get("owner"):
            if self.state["phase"] != "prepared":
                raise ValueError("missing fixture ownership receipt")
            # A failed admission never grants permission to remove the project.
            return
        failures: list[BaseException] = []
        try:
            self.capture_engine_logs("stop")
        except BaseException as exc:
            if ownership_failure(exc):
                raise  # Foreign identity is not a recoverable logging failure.
            failures.append(exc)
        for cleanup in (self.cleanup_extracts, self.cleanup_probe, self._stop_project):
            try:
                cleanup()
            except BaseException as exc:
                failures.append(exc)
        raise_failures("fixture capture and cleanup failures", failures)

    def _stop_project(self) -> None:
        self.verify_project_ownership(self.project)
        phase = "secured" if (self.root / "secured-compose.json").exists() else "bootstrap"
        if self.resources(self.project):
            self.compose(phase, "down", "-v")
        if self.resources(self.project):
            raise RuntimeError("owned Docker resources remain after teardown")
        self.save("stopped")

    def run_with_cleanup(self, operation: Callable[[], None]) -> None:
        """Outer test runner boundary: original failure/interruption survives failed stop."""
        failures: list[BaseException] = []
        report: dict[str, Any] = {}
        for name, action in (("operation", operation), ("stop", self.stop)):
            try:
                action()
                report[name] = {"outcome": "COMPLETED"}
            except BaseException as exc:
                failures.append(exc)
                report[name] = {"outcome": "FAILED", "error_type": type(exc).__name__}
        try:
            dump(self.root / ("operation-stop-" + uuid4().hex + ".json"), report)
        except BaseException as exc:
            failures.append(exc)
        raise_failures("package operation and stop failures", failures)


def preflight_q2(checkout: Path, environment: Path) -> None:
    """Probe an independently installed Q2 owner package; never manufacture one.

    The native plugin can only return these successes after its ServiceLoader
    provider acquired live admission and validated native publication provenance.
    Both operations below are read-only, and include wrong-purpose refusal.
    """
    values = json.loads(environment.read_text())
    required = {
        "MAEZO_PORTAL_READ_PACKAGED_FIXTURE_FILE",
        "MAEZO_PORTAL_READ_IT_CA_FILE",
        "MAEZO_PORTAL_READ_IT_CERT_FILE",
        "MAEZO_PORTAL_READ_IT_KEY_FILE",
        "MAEZO_PORTAL_READ_IT_ORIGIN",
        "MAEZO_PORTAL_READ_IT_BFF_ORIGIN",
        "MAEZO_PORTAL_READ_IT_BFF_CA_FILE",
    }
    if set(values) != required or any(not isinstance(value, str) or not value for value in values.values()):
        raise ValueError(
            "Q2 requires independently admitted provider, signed envelopes and HTTPS BFF session"
        )
    for key, value in values.items():
        if key.endswith("FILE"):
            file = Path(value)
            if not file.is_absolute() or file.resolve(strict=True) != file or not file.is_file():
                raise ValueError("Q2 explicit canonical fixture files required")
    previous = {key: os.environ.get(key) for key in required}
    try:
        os.environ.update(values)
        module = load_module("q2_live_preflight", checkout / FAMILIES["q2"][0])
        module.test_packaged_direct_mtls_native_task_and_purpose_separation()
        module.test_real_q1_bff_queue_and_detail_keep_private_continuity_out_of_browser()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--fixture", type=Path, required=True)
    init.add_argument("--project", required=True)
    for command in ("start-human", "test-human", "start-d7", "preflight", "stop"):
        sub = commands.add_parser(command)
        sub.add_argument("--fixture", type=Path, required=True)
    inv = commands.add_parser("inventory")
    inv.add_argument("--nodeids", type=Path, required=True, help="JSON list from actual pytest collection")
    inv.add_argument("--output", type=Path, required=True)
    q2 = commands.add_parser("preflight-q2")
    q2.add_argument("--checkout", type=Path, required=True)
    q2.add_argument("--environment", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "inventory":
        dump(args.output, inventory(json.loads(args.nodeids.read_text())))
    elif args.command == "preflight-q2":
        preflight_q2(args.checkout, args.environment)
    elif args.command == "init":
        root = args.fixture
        if not root.is_absolute() or root.resolve(strict=True) != root or root.stat().st_mode & 0o077:
            raise ValueError("canonical mode-0700 prepared fixture required")
        if not re.fullmatch(r"d7-[a-z0-9-]{8,64}", args.project) or (root / "lifecycle.json").exists():
            raise ValueError("new lifecycle and unique project required")
        dump(root / "lifecycle.json", {"project": args.project, "phase": "prepared"})
    else:
        runner = Lifecycle(args.fixture)
        getattr(runner, args.command.replace("-", "_"))()
    print("Package lifecycle step completed; private fixture material retained.")


if __name__ == "__main__":
    main()
