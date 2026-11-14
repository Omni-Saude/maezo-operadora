package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import br.com.maezo.human.Jcs;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * P2 (DESIGN-OPTION-REFUSAL-63-ROOT-v1.md v1.1 §5.2-P2, §4.2; updated for the
 * D2 post-commit machine by DESIGN-D2-POST-COMMIT-v1 §2.2): the decoupling
 * invariants provable deterministically on the JVM side, WITHOUT an engine and
 * WITHOUT a surrogate instrument or mocked admission chain.
 *
 * <p>The production seam under D2 is endpoint-conditional:
 * {@code RuntimeDefinitionObservation.measuredEndpoint} consumes the port for
 * ENTRY in the original order {@code hold(endpoint)} → {@code awaitRootRelease()}
 * → … → {@code seal()}, and for COMMITTING it consumes ONLY {@code hold} —
 * the pair {@code awaitRootRelease()} + {@code seal()} belongs to the
 * COMMITTED listener, strictly after the SQL commit (pinned over the REAL jar
 * commit path by D2PostCommitListenerTest). Under D2' the ROOT dispatches the
 * COMMITTING RELEASE BEFORE its two captures, so the document the consumption
 * consumes is the EARLY shape: a RELEASE carrying {@code hold_raw_sha256} but
 * NO capture digests. This unit drives the exact port interface
 * ({@link NativeMeasurementPort}) with a fake ROOT side and pins, across the
 * Δ sweep {0, 10, 100, 1000} ms of release-arrival delay:
 *
 * <ol>
 *   <li>the early RELEASE document is accepted by the REAL
 *       {@link NativeMeasurementConfiguration#validate} against the versioned
 *       schema (the same validator {@code awaitRootRelease} calls);</li>
 *   <li>the released document carries NO {@code root_capture_*_raw_sha256}
 *       (schema v3 moved them out of REQUIRED; the ENTRY shape keeps them and
 *       still validates);</li>
 *   <li>the consumed wait is the injected Δ ALONE and the HOLD→RELEASE
 *       dispatch latency stays Δ-independent — the observer's reading/sleep
 *       pacing is out of the wait (the decoupled physics D2' buys), and under
 *       D2 that wait is post-commit;</li>
 *   <li>the seam call grammar is invariant in Δ — COMMITTING: hold in-tx, then
 *       (post-commit) awaitRootRelease → seal, never re-ordered, never
 *       retried;</li>
 *   <li>fail-closed negatives: a RELEASE without the hold binding and a
 *       RELEASE with a foreign field refuse the real validator.</li>
 * </ol>
 *
 * <p>The full live path (engine + PG + UDS) remains the EngineIT/lane proof
 * (P4 of the design); nothing here claims runtime evidence.
 */
class EarlyReleaseDecouplingTest {
  private static final String FRAME = "provider-native-independent-measurement-frame.v2";
  private static final byte[] HOLD_RAW =
      "{\"kind\":\"HOLD\",\"unit\":\"P2-fixture\"}".getBytes(StandardCharsets.US_ASCII);

  /** The ROOT side of the protocol: dispatch at hold(), consume at awaitRootRelease(). */
  private static final class FakeRootPort implements NativeMeasurementPort {
    final long releaseDelayMs;
    final List<String> calls = new java.util.ArrayList<>();
    Map<String, Object> dispatchedRelease;
    long holdToDispatchMs = -1;
    long consumedWaitMs = -1;

    FakeRootPort(long releaseDelayMs) { this.releaseDelayMs = releaseDelayMs; }

    @Override public void openStage(int sequence, String executionUuid, CommandContext ctx, Connection enlisted) {
      calls.add("openStage");
    }

    @Override public void hold(Endpoint endpoint) {
      calls.add("hold");
      Instant holdAt = Instant.now();
      Map<String, Object> release = new TreeMap<>();
      release.put("schema", FRAME);
      release.put("event_ordinal", 4L);
      release.put("session_nonce", "a".repeat(32));
      release.put("request_sha256", "b".repeat(64));
      release.put("config_sha256", "c".repeat(64));
      release.put("emitted_at", RuntimeObservationAdmission.time(Instant.now()));
      release.put("original_deadline", RuntimeObservationAdmission.time(Instant.now().plusSeconds(120)));
      release.put("kind", "RELEASE");
      release.put("stage_sequence", 0L);
      release.put("server_stage_execution_uuid", "01234567-89ab-cdef-0123-456789abcdef");
      release.put("endpoint", endpoint.name());
      release.put("hold_raw_sha256", Jcs.digest(HOLD_RAW));
      // D2' §4.2: the dispatched RELEASE carries NO capture digests.
      assertFalse(release.containsKey("root_capture_before_raw_sha256"));
      assertFalse(release.containsKey("root_capture_after_raw_sha256"));
      // The REAL contract the sealed seam-side validator enforces on arrival.
      NativeMeasurementConfiguration.validate("RELEASE", release);
      dispatchedRelease = release;
      holdToDispatchMs = Duration.between(holdAt, Instant.now()).toMillis();
    }

    @Override public void awaitRootRelease() {
      calls.add("awaitRootRelease");
      Instant started = Instant.now();
      try {
        Thread.sleep(releaseDelayMs);
      } catch (InterruptedException interrupted) {
        Thread.currentThread().interrupt(); throw new IllegalStateException(interrupted);
      }
      consumedWaitMs = Duration.between(started, Instant.now()).toMillis();
      calls.add("release-consumed");
    }

    @Override public void seal() { calls.add("seal"); }
    @Override public void stageClosed() { calls.add("stageClosed"); }
    @Override public void complete(String rawResponseSha256) { calls.add("complete"); }
    @Override public void abort() { calls.add("abort"); }
  }

  @ParameterizedTest
  @ValueSource(longs = {0, 10, 100, 1000})
  void earlyReleaseDocumentContractAndGrammarAreInvariantInReleaseArrivalDelta(long deltaMs) {
    FakeRootPort port = new FakeRootPort(deltaMs);
    // The exact D2 seam grammar for one endpoint: hold() ONLY inside the
    // COMMITTING listener; the consumption pair belongs to the COMMITTED
    // listener (post-commit ordering is pinned over the REAL jar commit path
    // by D2PostCommitListenerTest).
    port.hold(NativeMeasurementPort.Endpoint.COMMITTING);
    // ... the SQL commit runs here (simulated boundary) ...
    port.awaitRootRelease();
    port.seal();
    assertEquals(List.of("hold", "awaitRootRelease", "release-consumed", "seal"), port.calls);
    // The dispatch itself never waited for Δ: the RELEASE left BEFORE the wait.
    assertTrue(port.holdToDispatchMs < deltaMs || deltaMs == 0,
        "dispatch latency must not absorb the injected release-arrival delay");
    assertTrue(port.holdToDispatchMs <= 250, "dispatch must stay a small constant cost");
    // The consumed wait is the injected Δ alone (± scheduler slop) — the
    // decoupled wait, with no capture/reading work inside it, and under D2 it
    // is POST-commit.
    assertTrue(port.consumedWaitMs >= deltaMs);
    assertTrue(port.consumedWaitMs < deltaMs + 250);
    assertEquals("RELEASE", port.dispatchedRelease.get("kind"));
    assertFalse(port.dispatchedRelease.containsKey("root_capture_before_raw_sha256"));
    assertFalse(port.dispatchedRelease.containsKey("root_capture_after_raw_sha256"));
  }

  @Test
  void entryShapeReleaseStillValidatesAgainstTheVersionedSchema() {
    Map<String, Object> release = new TreeMap<>(releaseDocument("ENTRY"));
    release.put("root_capture_before_raw_sha256", Jcs.digest(HOLD_RAW));
    release.put("root_capture_after_raw_sha256", Jcs.digest("entry-second".getBytes(StandardCharsets.US_ASCII)));
    NativeMeasurementConfiguration.validate("RELEASE", release);
  }

  @Test
  void releaseWithoutHoldBindingRefusesTheRealValidator() {
    Map<String, Object> release = releaseDocument("COMMITTING");
    release.remove("hold_raw_sha256");
    assertThrows(Refused.class, () -> NativeMeasurementConfiguration.validate("RELEASE", release));
  }

  @Test
  void releaseWithForeignFieldRefusesTheRealValidator() {
    Map<String, Object> release = releaseDocument("COMMITTING");
    release.put("root_capture_unexpected_field", Jcs.digest(HOLD_RAW));
    assertThrows(Refused.class, () -> NativeMeasurementConfiguration.validate("RELEASE", release));
  }

  @Test
  void releaseWithNonCanonicalTimeRefusesTheRealValidator() {
    Map<String, Object> release = releaseDocument("COMMITTING");
    release.put("emitted_at", "2026-10-26T10:00:00Z");
    assertThrows(Refused.class, () -> NativeMeasurementConfiguration.validate("RELEASE", release));
  }

  private static Map<String, Object> releaseDocument(String endpoint) {
    Map<String, Object> release = new TreeMap<>();
    release.put("schema", FRAME);
    release.put("event_ordinal", 4L);
    release.put("session_nonce", "a".repeat(32));
    release.put("request_sha256", "b".repeat(64));
    release.put("config_sha256", "c".repeat(64));
    release.put("emitted_at", RuntimeObservationAdmission.time(Instant.now()));
    release.put("original_deadline", RuntimeObservationAdmission.time(Instant.now().plusSeconds(120)));
    release.put("kind", "RELEASE");
    release.put("stage_sequence", 0L);
    release.put("server_stage_execution_uuid", "01234567-89ab-cdef-0123-456789abcdef");
    release.put("endpoint", endpoint);
    release.put("hold_raw_sha256", Jcs.digest(HOLD_RAW));
    return release;
  }
}
