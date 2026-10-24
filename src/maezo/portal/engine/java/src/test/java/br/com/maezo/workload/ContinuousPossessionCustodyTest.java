package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import br.com.maezo.human.Jcs;

import java.nio.channels.FileChannel;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Custody by continuous possession (DESIGN-OPTION-REFUSAL-61-62-v1 §2.1, proof obligations
 * G1-G7): the mechanical reproducer of the fd-reuse family, the per-read re-digest, and the
 * fail-closed refusals. Any divergence of digest/attrs/continuity refuses; nothing degrades.
 * Linux descriptor surface is required for the custody tests; host capability is explicit
 * and never runtime acceptance.
 */
class ContinuousPossessionCustodyTest {
  private static final Runnable NOP = () -> { };
  @TempDir Path temporary;

  private boolean linux() {
    return Files.isDirectory(Path.of("/proc/self/fd"));
  }

  private Path protectedFile(String name) throws Exception {
    return protectedFile(name, new byte[] {1, 2, 3});
  }

  private Path protectedFile(String name, byte[] content) throws Exception {
    Files.setPosixFilePermissions(temporary, PosixFilePermissions.fromString("rwx------"));
    Path path = temporary.resolve(name);
    Files.write(path, content);
    Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"));
    return path.toRealPath();
  }

  private static String digestOf(byte[] raw) throws Exception {
    return Jcs.digest(raw);
  }

  private static long ownUid() throws Exception {
    return ((Number) Files.getAttribute(Path.of("/proc/self"), "unix:uid")).longValue();
  }

  private static long inodeOf(Path file) throws Exception {
    return ((Number) Files.readAttributes(file, "unix:ino", LinkOption.NOFOLLOW_LINKS).get("ino")).longValue();
  }

  private static byte[] instrumentReadFile(Path path, String expected, int bound) throws Exception {
    Method read = ProviderNativeIndependentMeasurement.class.getDeclaredMethod(
        "readFile", Path.class, String.class, int.class, long.class, Runnable.class);
    read.setAccessible(true);
    try {
      return (byte[]) read.invoke(null, path, expected, bound, ownUid(), NOP);
    } catch (InvocationTargetException wrapped) {
      if (wrapped.getCause() instanceof Exception failure) throw failure;
      throw new IllegalStateException(wrapped.getCause());
    }
  }

  private static void instrumentDigestInventoryFile(Path path, String expected) throws Exception {
    Method digest = ProviderNativeIndependentMeasurement.class.getDeclaredMethod(
        "digestInventoryFile", Path.class, String.class, long.class, Runnable.class);
    digest.setAccessible(true);
    try {
      digest.invoke(null, path, expected, ownUid(), NOP);
    } catch (InvocationTargetException wrapped) {
      if (wrapped.getCause() instanceof Exception failure) throw failure;
      throw new IllegalStateException(wrapped.getCause());
    }
  }

  /** Unchecked views of the reflective calls for assertThrows callables. */
  private static void instrumentReadRefusing(Path path, String expected, int bound) {
    try {
      instrumentReadFile(path, expected, bound);
    } catch (Throwable failure) {
      if (failure instanceof IllegalStateException refused) throw refused;
      if (failure instanceof RuntimeException runtime) throw runtime;
      throw new IllegalStateException(failure);
    }
  }

  private static void instrumentDigestRefusing(Path path, String expected) {
    try {
      instrumentDigestInventoryFile(path, expected);
    } catch (Throwable failure) {
      if (failure instanceof IllegalStateException refused) throw refused;
      if (failure instanceof RuntimeException runtime) throw runtime;
      throw new IllegalStateException(failure);
    }
  }

  @SuppressWarnings("unchecked")
  private static Map<Path, Object> registry(String holder, String field) throws Exception {
    Field registryField = Class.forName("br.com.maezo.workload." + holder).getDeclaredField(field);
    registryField.setAccessible(true);
    return (Map<Path, Object>) registryField.get(null);
  }

  private static FileChannel channelOf(Object possession) throws Exception {
    try {
      Method channel = possession.getClass().getDeclaredMethod("channel");
      channel.setAccessible(true);
      return (FileChannel) channel.invoke(possession);
    } catch (NoSuchMethodException recordAccessor) {
      Field field = possession.getClass().getDeclaredField("channel");
      field.setAccessible(true);
      return (FileChannel) field.get(possession);
    }
  }
  @AfterEach void releaseRegistries() throws Exception {
    for (String holder : List.of("RuntimeObservationAdmission", "BoundaryPolicy",
        "ProviderNativeIndependentMeasurement")) {
      Class<?> type = Class.forName("br.com.maezo.workload." + holder);
      for (String name : List.of("POSSESSIONS", "OWNERSHIP", "PINNED_FILES")) {
        try {
          Field registryField = type.getDeclaredField(name);
          registryField.setAccessible(true);
          @SuppressWarnings("unchecked")
          Map<Path, Object> registry = (Map<Path, Object>) registryField.get(null);
          for (Object possession : registry.values()) {
            try {
              channelOf(possession).close();
            } catch (Exception abandoned) {
              /* registry entries expose no channel; nothing to release */
            }
          }
          registry.clear();
        } catch (NoSuchFieldException absent) {
          /* not every holder owns every registry */
        }
      }
    }
  }

  @Test void admissionPinnedReadReopensNothingAndRedigestsEveryTime() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("admission.json", "{\"nonce\":\"one\"}".getBytes(StandardCharsets.US_ASCII));
    String digest = digestOf(Files.readAllBytes(path));
    var before = RuntimeObservationAdmission.descriptorCensus();
    assertArrayEquals(Files.readAllBytes(path), RuntimeObservationAdmission.readProtected(path, digest, 65536));
    var possessed = RuntimeObservationAdmission.descriptorCensus();
    assertEquals(before.size() + 1, possessed.size());
    for (int round = 0; round < 3; round++) {
      assertArrayEquals(Files.readAllBytes(path), RuntimeObservationAdmission.readProtected(path, digest, 65536));
      assertEquals(possessed, RuntimeObservationAdmission.descriptorCensus());
    }
    assertTrue(registry("RuntimeObservationAdmission", "POSSESSIONS").containsKey(path));
  }
  @Test void admissionPinnedReadRefusesWhenPinnedDescriptorClosedExternally() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("closed.json");
    String digest = digestOf(Files.readAllBytes(path));
    RuntimeObservationAdmission.readProtected(path, digest, 65536);
    channelOf(registry("RuntimeObservationAdmission", "POSSESSIONS").get(path)).close();
    assertThrows(Refused.class, () -> RuntimeObservationAdmission.readProtected(path, digest, 65536));
  }

  @Test void admissionPinnedReadRefusesOnInodeSwapRenameOver() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("swap.json");
    String digest = digestOf(Files.readAllBytes(path));
    RuntimeObservationAdmission.readProtected(path, digest, 65536);
    Path substitute = protectedFile("substitute.json", "other-content".getBytes(StandardCharsets.US_ASCII));
    Files.move(substitute, path, StandardCopyOption.REPLACE_EXISTING);
    assertThrows(Refused.class, () -> RuntimeObservationAdmission.readProtected(path, digest, 65536));
  }

  @Test void admissionPinnedReadRefusesOnInPlaceWriteSameSize() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("rewrite.json", new byte[] {1, 2, 3, 4});
    String digest = digestOf(Files.readAllBytes(path));
    RuntimeObservationAdmission.readProtected(path, digest, 65536);
    try (var channel = FileChannel.open(path, StandardOpenOption.WRITE)) {
      channel.position(2);
      channel.write(java.nio.ByteBuffer.wrap(new byte[] {9}));
      channel.force(true);
    }
    assertThrows(Refused.class, () -> RuntimeObservationAdmission.readProtected(path, digest, 65536));
  }
  @Test void admissionPinnedReadRefusesOnDivergentMode() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("mode.json");
    String digest = digestOf(Files.readAllBytes(path));
    RuntimeObservationAdmission.readProtected(path, digest, 65536);
    Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-r-----"));
    assertThrows(Refused.class, () -> RuntimeObservationAdmission.readProtected(path, digest, 65536));
  }

  @Test void admissionPinnedReadRefusesOnHardlink() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("nlink.json");
    String digest = digestOf(Files.readAllBytes(path));
    RuntimeObservationAdmission.readProtected(path, digest, 65536);
    Files.createLink(temporary.resolve("second-link.json"), path);
    assertThrows(Refused.class, () -> RuntimeObservationAdmission.readProtected(path, digest, 65536));
  }

  @Test void admissionPinnedReadRefusesOnWrongDigest() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("digest.json");
    String digest = digestOf(Files.readAllBytes(path));
    RuntimeObservationAdmission.readProtected(path, digest, 65536);
    assertThrows(Refused.class, () -> RuntimeObservationAdmission.readProtected(path, digestOf(new byte[] {9, 9, 9}), 65536));
  }
  /**
   * Mechanical reproducer of run 38 (#61). The kernel recycles a census-recorded
   * descriptor number: the retained pin-time primitive refuses exactly there (the prior
   * doctrine), while the continuous-possession read passes under the same ambient state.
   */
  @Test void ambientChurnCannotRefuseAPossessedAdmissionRead_reproducesRun38() throws Exception {
    assumeTrue(linux());
    Path target = protectedFile("possessed.json");
    String digest = digestOf(Files.readAllBytes(target));
    RuntimeObservationAdmission.readProtected(target, digest, 65536);
    Path dummy = protectedFile("dummy.json");
    Path filler = protectedFile("filler.json");
    Map<Path, Map<String, Object>> before = null;
    Map<Path, Map<String, Object>> duringReuse = null;
    boolean reused = false;
    for (int attempt = 0; attempt < 64 && !reused; attempt++) {
      try (var held = FileChannel.open(dummy, StandardOpenOption.READ)) {
        before = RuntimeObservationAdmission.descriptorCensus();
      }
      var moved = FileChannel.open(filler, StandardOpenOption.READ);
      try {
        duringReuse = RuntimeObservationAdmission.descriptorCensus();
        Path dummyNumber = numberInCensus(before, dummy);
        Path fillerNumber = numberInCensus(duringReuse, filler);
        reused = dummyNumber != null && fillerNumber != null && dummyNumber.equals(fillerNumber);
      } finally {
        moved.close();
      }
    }
    assertTrue(reused, "bounded loop: the kernel must recycle the recorded descriptor number");
    // The mechanical #61 refusal: the recycled number is a survivor of both censuses
    // whose identity (dev/ino/mode/uid/nlink) diverged. Deterministic on the captured
    // maps; no timing involved.
    var recorded = before;
    var recycled = duringReuse;
    assertThrows(Refused.class, () -> RuntimeObservationAdmission.openedDescriptor(recorded, recycled));
    // The continuous-possession read passes under the same ambient state: it neither
    // reopens nor censuses, so the recycled number cannot manufacture a refusal.
    assertArrayEquals(Files.readAllBytes(target), RuntimeObservationAdmission.readProtected(target, digest, 65536));
  }

  private static Path numberInCensus(Map<Path, Map<String, Object>> census, Path file) throws Exception {
    for (var entry : census.entrySet()) {
      if (entry.getValue().get("ino").equals(inodeOf(file))) return entry.getKey();
    }
    return null;
  }
  @Test void instrumentPinnedReadReopensNothingAndRedigestsEveryTime() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("material.jar", new byte[] {5, 6, 7, 8});
    String digest = digestOf(Files.readAllBytes(path));
    var before = RuntimeObservationAdmission.descriptorCensus();
    assertArrayEquals(Files.readAllBytes(path), instrumentReadFile(path, digest, 32 * 1024 * 1024));
    var possessed = RuntimeObservationAdmission.descriptorCensus();
    assertEquals(before.size() + 1, possessed.size());
    for (int round = 0; round < 3; round++) {
      assertArrayEquals(Files.readAllBytes(path), instrumentReadFile(path, digest, 32 * 1024 * 1024));
      assertEquals(possessed, RuntimeObservationAdmission.descriptorCensus());
    }
    assertTrue(registry("ProviderNativeIndependentMeasurement", "PINNED_FILES").containsKey(path));
  }

  @Test void instrumentPinnedReadRefusesOnInPlaceWriteSameSize() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("material-rewrite.jar", new byte[] {1, 2, 3, 4});
    String digest = digestOf(Files.readAllBytes(path));
    instrumentReadFile(path, digest, 32 * 1024 * 1024);
    try (var channel = FileChannel.open(path, StandardOpenOption.WRITE)) {
      channel.position(2);
      channel.write(java.nio.ByteBuffer.wrap(new byte[] {9}));
      channel.force(true);
    }
    assertThrows(IllegalStateException.class, () -> instrumentReadRefusing(path, digest, 32 * 1024 * 1024));
  }

  @Test void instrumentPinnedReadRefusesOnInodeSwap() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("material-swap.jar", new byte[] {1, 2, 3, 4});
    String digest = digestOf(Files.readAllBytes(path));
    instrumentReadFile(path, digest, 32 * 1024 * 1024);
    Path substitute = protectedFile("substitute.jar", "other-content".getBytes(StandardCharsets.US_ASCII));
    Files.move(substitute, path, StandardCopyOption.REPLACE_EXISTING);
    assertThrows(IllegalStateException.class, () -> instrumentReadRefusing(path, digest, 32 * 1024 * 1024));
  }
  @Test void instrumentPinnedReadRefusesOnDivergentMode() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("material-mode.jar", new byte[] {1, 2, 3, 4});
    String digest = digestOf(Files.readAllBytes(path));
    instrumentReadFile(path, digest, 32 * 1024 * 1024);
    Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-r-----"));
    assertThrows(IllegalStateException.class, () -> instrumentReadRefusing(path, digest, 32 * 1024 * 1024));
  }

  @Test void instrumentPinnedReadRefusesOnHardlink() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("material-nlink.jar", new byte[] {1, 2, 3, 4});
    String digest = digestOf(Files.readAllBytes(path));
    instrumentReadFile(path, digest, 32 * 1024 * 1024);
    Files.createLink(temporary.resolve("second-link.jar"), path);
    assertThrows(IllegalStateException.class, () -> instrumentReadRefusing(path, digest, 32 * 1024 * 1024));
  }

  @Test void instrumentPinnedReadRefusesOnClosedPinnedChannel() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("material-closed.jar", new byte[] {1, 2, 3, 4});
    String digest = digestOf(Files.readAllBytes(path));
    instrumentReadFile(path, digest, 32 * 1024 * 1024);
    channelOf(registry("ProviderNativeIndependentMeasurement", "PINNED_FILES").get(path)).close();
    assertThrows(IllegalStateException.class, () -> instrumentReadRefusing(path, digest, 32 * 1024 * 1024));
  }
  /**
   * Mechanical reproducer of run 40 (#62). The kernel recycles a number that a
   * census recorded as a regular file: the prior readFile refused here
   * (require(!matches.isEmpty())). The possessed read passes under the same ambient state.
   */
  @Test void instrumentAmbientChurnCannotRefuseRepeatedRead_reproducesRun40() throws Exception {
    assumeTrue(linux());
    Path target = protectedFile("possessed.jar", new byte[] {5, 6, 7, 8});
    String digest = digestOf(Files.readAllBytes(target));
    instrumentReadFile(target, digest, 32 * 1024 * 1024);
    Path dummy = protectedFile("dummy.jar");
    Path filler = protectedFile("filler.jar");
    Map<Path, Map<String, Object>> before = null;
    boolean reused = false;
    for (int attempt = 0; attempt < 64 && !reused; attempt++) {
      try (var held = FileChannel.open(dummy, StandardOpenOption.READ)) {
        before = RuntimeObservationAdmission.descriptorCensus();
      }
      try (var moved = FileChannel.open(filler, StandardOpenOption.READ)) {
        var after = RuntimeObservationAdmission.descriptorCensus();
        Path dummyNumber = numberInCensus(before, dummy);
        Path fillerNumber = numberInCensus(after, filler);
        reused = dummyNumber != null && fillerNumber != null && dummyNumber.equals(fillerNumber);
      }
    }
    assertTrue(reused);
    assertArrayEquals(Files.readAllBytes(target), instrumentReadFile(target, digest, 32 * 1024 * 1024));
  }
  @Test void inventorySweepIsCensusFreeAndGrantsNoChannelCustody() throws Exception {
    assumeTrue(linux());
    Path first = protectedFile("lib-one.jar", new byte[] {1, 2, 3});
    Path second = protectedFile("lib-two.jar", new byte[] {4, 5, 6});
    var before = RuntimeObservationAdmission.descriptorCensus();
    instrumentDigestInventoryFile(first, digestOf(Files.readAllBytes(first)));
    instrumentDigestInventoryFile(second, digestOf(Files.readAllBytes(second)));
    assertEquals(before, RuntimeObservationAdmission.descriptorCensus());
    assertTrue(registry("ProviderNativeIndependentMeasurement", "PINNED_FILES").isEmpty());
    assertThrows(IllegalStateException.class, () -> instrumentDigestRefusing(first, digestOf(new byte[] {9, 9, 9})));
  }

  @Test void inventorySweepRefusesWhenRenamedOverFileHasDifferentContent() throws Exception {
    assumeTrue(linux());
    Path path = protectedFile("lib-swap.jar", new byte[] {1, 2, 3});
    instrumentDigestInventoryFile(path, digestOf(new byte[] {1, 2, 3}));
    Path substitute = protectedFile("substitute-sweep.jar", new byte[] {4, 4, 4});
    Files.move(substitute, path, StandardCopyOption.REPLACE_EXISTING);
    assertThrows(IllegalStateException.class, () -> instrumentDigestRefusing(path, digestOf(new byte[] {1, 2, 3})));
  }
  /** R2 of the design doc: the pinned set is closed and countable; cycling grows no fds. */
  @Test void pinnedCycleGrowsNoDescriptors() throws Exception {
    assumeTrue(linux());
    List<Path> materials = List.of(
        protectedFile("cycle-a.json"), protectedFile("cycle-b.json"),
        protectedFile("cycle-c.json"), protectedFile("cycle-d.json"));
    for (Path material : materials) {
      instrumentReadFile(material, digestOf(Files.readAllBytes(material)), 1 << 25);
    }
    var possessed = RuntimeObservationAdmission.descriptorCensus();
    for (int round = 0; round < 20; round++) {
      for (Path material : materials) {
        assertArrayEquals(Files.readAllBytes(material), instrumentReadFile(material, digestOf(Files.readAllBytes(material)), 1 << 25));
      }
      assertEquals(possessed, RuntimeObservationAdmission.descriptorCensus());
    }
    assertEquals(materials.size(), registry("ProviderNativeIndependentMeasurement", "PINNED_FILES").size());
  }
}

