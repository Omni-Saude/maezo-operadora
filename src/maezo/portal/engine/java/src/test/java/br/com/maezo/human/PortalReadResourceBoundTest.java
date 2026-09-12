package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.io.ByteArrayInputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;

/**
 * Pins the read-dependency bound against the operadora's own deployed models.
 *
 * <p>Regression: {@code PortalReadCommand.resourceDigest} bounded the deployed process/decision
 * model with {@code MAX} (64 KiB), the untrusted-ingress bound. The AUTH BPMN is 69 706 B, so
 * {@code verifyCatalog} refused the operadora's production catalog with
 * READ_DEPENDENCY_UNAVAILABLE before any digest could be compared.
 */
class PortalReadResourceBoundTest {
  /** The BPMN that first exposed the conflated bound (V3-Q3, rows 6-8). */
  private static final String AUTH_BPMN = "SP-OP-AUTH-001_Autorizacao_Previa.bpmn";

  /** Same idiom as {@code ConsumerLineageTest}: surefire sets {@code basedir} to the module dir. */
  private static Path repoRoot() {
    Path root = Path.of(System.getProperty("basedir", ".")).resolve("../../../../..").normalize();
    assertTrue(Files.isRegularFile(root.resolve("spec/processes/bpmn").resolve(AUTH_BPMN)),
        "repository root not found at " + root.toAbsolutePath()
            + "; this test pins deployed BPMN/DMN sizes and must never be skipped");
    return root;
  }

  private static List<Path> deployedModels(Path root) throws Exception {
    var models = new ArrayList<Path>();
    try (Stream<Path> walk = Files.walk(root.resolve("spec/processes"))) {
      walk.filter(Files::isRegularFile)
          .filter(p -> p.toString().endsWith(".bpmn") || p.toString().endsWith(".dmn"))
          .forEach(models::add);
    }
    assertFalse(models.isEmpty(), "no BPMN/DMN found under spec/processes");
    return models;
  }

  @Test
  void authBpmnExceedsTheIngressBoundSoTheSplitIsLoadBearing() throws Exception {
    long size = Files.size(repoRoot().resolve("spec/processes/bpmn").resolve(AUTH_BPMN));
    assertTrue(size > PortalReadModels.MAX,
        AUTH_BPMN + " is " + size + " B; if it ever fits in MAX (" + PortalReadModels.MAX
            + ") this fence stops proving anything — re-derive the bound, do not delete the test");
    assertTrue(size <= PortalReadModels.RESOURCE_MAX,
        AUTH_BPMN + " is " + size + " B, over RESOURCE_MAX (" + PortalReadModels.RESOURCE_MAX
            + "): the portal-read catalog path cannot validate it");
  }

  @Test
  void everyDeployedModelFitsTheResourceBound() throws Exception {
    Path root = repoRoot();
    var over = new ArrayList<String>();
    for (Path model : deployedModels(root)) {
      long size = Files.size(model);
      if (size > PortalReadModels.RESOURCE_MAX)
        over.add(root.relativize(model) + " (" + size + " B)");
    }
    assertEquals(List.of(), over,
        "deployed models over RESOURCE_MAX (" + PortalReadModels.RESOURCE_MAX
            + "): verifyCatalog would refuse them with READ_DEPENDENCY_UNAVAILABLE");
  }

  /**
   * The bound is the one the landed Python reader already applies to the same ACT_GE_BYTEARRAY
   * bytes (gateway/human/decision_binding.py:707,758 {@code octet_length(b.bytes_)<=1048576}).
   * Changing it here without changing it there re-splits the two readers of one artifact.
   */
  @Test
  void resourceBoundMatchesThePythonEngineBytearrayBound() {
    assertEquals(1048576, PortalReadModels.RESOURCE_MAX);
    assertEquals(65536, PortalReadModels.MAX);
    assertTrue(PortalReadModels.RESOURCE_MAX > PortalReadModels.MAX);
  }

  @Test
  void resourceDigestStillRefusesOverTheResourceBound() {
    byte[] tooBig = new byte[PortalReadModels.RESOURCE_MAX + 1];
    var rejected = assertThrows(Rejected.class,
        () -> PortalReadCommand.resourceDigest(new ByteArrayInputStream(tooBig), Jcs.digest(tooBig)));
    assertEquals(503, rejected.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", rejected.code);
  }

  @Test
  void resourceDigestAcceptsAModelLargerThanTheIngressBound() {
    byte[] body = new byte[PortalReadModels.MAX + 4096];
    assertDoesNotThrow(
        () -> PortalReadCommand.resourceDigest(new ByteArrayInputStream(body), Jcs.digest(body)));
  }
}
