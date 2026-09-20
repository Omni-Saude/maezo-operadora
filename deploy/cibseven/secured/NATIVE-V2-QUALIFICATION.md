# Native-v2 qualification source

This package prepares technical synthetic descriptors and artifact inspection. It
does not install, admit or start an engine. The default packaged registry remains
empty, controller-D remains unwired, and production is not enabled.

## Explicit artifact

The `native-v2-qualification` Maven profile packages the same production classes
with one explicit `engine-schemas-v2.json`. Its resource inputs exclude the default
registry. Its output directory and JAR basename differ from the default build;
never place both artifacts on the runtime classpath. No test-resources overlay,
additional shadow JAR, default profile activation or runtime schema override exists.

The descriptor contains ten schemas for four dedicated synthetic processes:
ABA acquisition fencing, renewal CAS, receipt recovery and command-body conflict.
All variables and read projections are empty. The BPMN fixture is not a business
process or a production process definition. Exact deployment IDs, native users,
capabilities and process instances must come from separately reviewed installation.

After independent review of these exact descriptor bytes, use Java 17 and the
explicit reviewed digest (a digest by itself is not review or authority):

```sh
mvn -B -f src/maezo/portal/engine/java/pom.xml \
  -Pnative-v2-qualification \
  -Dmaezo.nativeQualification.registrySha256=a6b051a5d5986aa41980a0e582e33ba078d4d7f3f4615b70ac04c7fffc78b49d \
  -Dtest=NativeV2QualificationArtifactTest package
```

Default builds omit that profile/property. The new Java test checks the actual
resource and capability parser in both modes. The profile deliberately fails
validation for absent or mismatched digest input. The output JAR is under
`target/native-v2-qualification/` with basename
`human-command-engine-1.0.0-native-v2-qualification.jar`.

`qualification_artifact.py` inspects actual JARs and unpacked classloader roots.
Supply every installed common classloader root with `--classpath`; exactly one
registry resource must exist across them. Use `--mode qualification` and the
reviewed `--registry-sha256`, or `--mode default` and the default registry hash.
Passing `--default-jar` and `--qualification-jar` additionally requires identical
production class names and bytes. The output explicitly disclaims runtime
admission. Source/parser or ZIP inspection is not a live classloader proof.

The default empty registry includes its terminating LF and has SHA-256
`4167a195860c50e1eae8464886a7d468a06ccde05b1a8e39c8a672fdd90a2807`,
matching the unchanged historical native migration manifest. The prior 51-byte
resource omitted that LF and hashed to
`d8e72419a529bc6905b00c76350971db544dc285d8e5ee6fa7b39d314b27135b`.
Restoring exactly that one byte preserves the same empty JSON value and the
historical migration/SQL bytes. Prior artifacts retain their recorded old hash;
rebuild the final candidate and verify actual resource bytes before admission.
The explicit qualification descriptor and its separate hash remain unchanged.

## Native installation readback

`maezo.gateway.native_installation_readback.read_installation` accepts only an
owner-supplied cursor and explicit reviewed identity/source/receipt/catalog pins.
The caller establishes a PostgreSQL 16 `REPEATABLE READ READ ONLY` transaction
and owns connection lifecycle. Credentials remain in the gateway. Readback
requires unchanged session/current user, actual native owner membership, exact
catalogue function owner/source/search path, native installation identity, native
and D PREPARED receipt byte equality, ACT namespace identity and catalog digest.
Missing existing privileges refuse; the adapter never adds privileges.

The returned `maezo.native-v2-installation-readback.v1` bytes are technical evidence.
They deliberately contain neither a native qualification owner-operation ID nor a
`NativePrincipalQualificationBinding`. They cannot be supplied to D as the missing
native-owner qualification receipt. No protected receipt or admission row is written.

## Unresolved admission prerequisites

The inert D installer/contracts/storage are extracted from #391 source `be6906ee`.
The frozen stage-1 specification accompanies their unit vectors. The existing D
SQL resource and secured plan already match that source. Controller orchestration
and DynamoDB adapters are outside this extraction. One migration-test helper uses
the same local initial-root vector without importing the unrelated DynamoDB test
suite; it remains a finite protocol test, never owner authority.

Actual D admission still needs the existing separately qualified `OwnerAuthority`
resource boundary, reserved controller roots and installation/principal metadata.
The current contract requires genuine AWS DynamoDB table identity, ECS task
definition/role metadata and immutable Secrets Manager resource version identity.
There is no approved local resource backend in this package. Fabricated AWS-shaped
identifiers or copied unit vectors cannot satisfy these requirements.

Native `schema_version` retains the migration receipt string, PREPARED bytes and
catalog digest, but not the missing durable native-owner qualification operation.
Its separately reviewed receipt producer and actual `NativeOwnerAuthority`
verification remain required before D records `NATIVE_QUALIFIED`, opens a login,
or admits native capabilities. Unknown authority keeps the fixture unavailable.

The three native Java EngineIT classes and Python native integration suite remain
mandatory once that boundary is satisfied. Their finite coverage does not close
Native30/D53. Later principal rotation, owner ACL refresh and restore promotion
remain deferred unless required controls explicitly exercise them.
