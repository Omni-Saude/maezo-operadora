package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * Structural proof obligations G1, G4 and G6 (DESIGN-OPTION-REFUSAL-61-62-v1 §2.1/§4):
 * exactly one FileChannel.open per family member, the census only at pin time, the hot
 * set declared once and pinned in the constructor, the inventory sweep census-free, and
 * no retry/polling token in any new custody body. Runs on every host.
 */
class ContinuousPossessionStructureTest {
  @Test void exactlyOneOpenPerFamilyMemberAndCensusOnlyAtPinTime() throws Exception {
    String instrument = source("ProviderNativeIndependentMeasurement.java");
    String admission = source("RuntimeObservationAdmission.java");
    String policy = source("BoundaryPolicy.java");
    assertEquals(1, count(instrument, "FileChannel.open("));
    assertEquals(1, count(admission, "FileChannel.open("));
    assertEquals(1, count(policy, "FileChannel.open("));
    // Instrument: census lives only inside the pin-time method (declaration + 2 calls).
    assertEquals(3, count(instrument, "descriptorCensus("));
    assertEquals(2, count(body(instrument, "private static byte[] openPinnedFile"), "descriptorCensus("));
    assertEquals(0, count(body(instrument, "private static byte[] reverifyPinnedFile"), "descriptorCensus("));
    assertEquals(0, count(body(instrument, "private static byte[] reverifyPinnedFile"), "FileChannel.open("));
    assertEquals(0, count(body(instrument, "private static void digestInventoryFile"), "descriptorCensus("));
    assertEquals(0, count(body(instrument, "private static void digestInventoryFile"), "FileChannel.open("));
    // Admission: census and attribution live only inside the pin-time method.
    assertEquals(3, count(admission, "descriptorCensus("));
    assertEquals(2, count(admission, "openedDescriptor("));
    assertEquals(0, count(body(admission, "private static byte[] reverifyPossession"), "descriptorCensus("));
    assertEquals(0, count(body(admission, "private static byte[] reverifyPossession"), "FileChannel.open("));
    assertEquals(0, count(body(admission, "private static byte[] readProtected(Path path,String expected,int bound,RuntimeObservationAdmission"), "FileChannel.open("));
    // Boundary policy: enumeration lives only inside the pin-time method.
    assertEquals(3, count(policy, "matchingDescriptors("));
    assertEquals(0, count(body(policy, "private static byte[] reverifyOwned"), "matchingDescriptors("));
    assertEquals(0, count(body(policy, "private static byte[] reverifyOwned"), "FileChannel.open("));
  }
  @Test void repeatedReadsDispatchThroughThePinnedRegistry() throws Exception {
    String instrument = source("ProviderNativeIndependentMeasurement.java");
    String admission = source("RuntimeObservationAdmission.java");
    String policy = source("BoundaryPolicy.java");
    assertTrue(body(instrument, "private static byte[] readFile(Path path, String expected, int bound, long jvmUid")
        .contains("PINNED_FILES.get"));
    assertTrue(body(admission, "private static byte[] readProtected(Path path,String expected,int bound,RuntimeObservationAdmission")
        .contains("POSSESSIONS.get"));
    assertTrue(body(policy, "private static byte[] readOwnBinary(Path path,String hash,long deadline")
        .contains("OWNERSHIP.get"));
    assertTrue(body(instrument, "private void verifyInventory").contains("digestInventoryFile("));
    assertFalse(body(instrument, "private void verifyInventory").contains("readFile("));
  }
  @Test void hotSetIsDeclaredOnceAndPinnedBeforeHello() throws Exception {
    String instrument = source("ProviderNativeIndependentMeasurement.java");
    assertEquals(3, count(instrument, "PINNED_MATERIALS"));
    assertTrue(instrument.contains("for (var material : PINNED_MATERIALS.entrySet())"));
    assertTrue(instrument.contains("opened ONCE here, before HELLO"));
  }
  @Test void noRetryPollingOrSleepTokenInAnyNewCustodyBody() throws Exception {
    Map<String, List<String>> bodies = Map.of(
        "ProviderNativeIndependentMeasurement.java", List.of(
            "private static byte[] openPinnedFile",
            "private static byte[] reverifyPinnedFile",
            "private static void digestInventoryFile"),
        "RuntimeObservationAdmission.java", List.of("private static byte[] reverifyPossession"),
        "BoundaryPolicy.java", List.of(
            "private static byte[] ownOwnership",
            "private static byte[] reverifyOwned"));
    for (var entry : bodies.entrySet()) {
      String holder = source(entry.getKey());
      for (String signature : entry.getValue()) {
        String extracted = body(holder, signature);
        assertFalse(extracted.contains("sleep"), signature);
        assertFalse(extracted.contains("retry"), signature);
        assertFalse(extracted.contains("poll"), signature);
      }
    }
  }
  private static String source(String name) throws Exception {
    Path compiled = Path.of(ContinuousPossessionStructureTest.class.getProtectionDomain()
        .getCodeSource().getLocation().toURI());
    Path module = compiled.getParent().getParent();
    for (String set : List.of("main", "test")) {
      Path candidate = module.resolve("src").resolve(set).resolve("java")
          .resolve("br").resolve("com").resolve("maezo").resolve("workload").resolve(name);
      if (Files.isRegularFile(candidate)) return Files.readString(candidate);
    }
    throw new AssertionError("source not found: " + name);
  }

  private static int count(String source, String token) {
    int total = 0;
    for (int at = source.indexOf(token); at >= 0; at = source.indexOf(token, at + token.length())) {
      total++;
    }
    return total;
  }

  /** Brace-matched body of the first method whose source contains the signature prefix. */
  private static String body(String source, String signature) {
    int at = source.indexOf(signature);
    assertTrue(at >= 0, signature);
    int open = source.indexOf('{', at);
    int depth = 0;
    for (int cursor = open; cursor < source.length(); cursor++) {
      char token = source.charAt(cursor);
      if (token == '{') {
        depth++;
      } else if (token == '}') {
        depth--;
        if (depth == 0) return source.substring(open, cursor + 1);
      }
    }
    throw new AssertionError(signature);
  }
}

