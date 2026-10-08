package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/**
 * ROOT lane readback of genuine CIB/PG/TLS/UDS execution, never an engine mock.
 * ROOT must first supply the real finite capture and separately qualify custody,
 * PG snapshots, image/process and native admission with its independent reader.
 * This test does not elevate an artifact's declarations into those facts.
 */
@Tag("integration")
class NativeMeasurementProtocolEngineIT {
  private static String env(String name) {
    String value = System.getenv(name); if (value == null || value.isBlank()) throw new IllegalStateException("Real ROOT capture absent: " + name); return value;
  }
  private static String sha(byte[] raw) throws Exception { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw)); }
  private static Path path(String name) throws Exception {
    Path value = Path.of(env(name)); assertTrue(value.isAbsolute()); assertEquals(value, value.toRealPath()); return value;
  }
  private static Map<String, Object> read(Path path, String definition) throws Exception {
    byte[] raw = Files.readAllBytes(path); assertTrue(raw.length > 0 && raw.length <= 1048576);
    var value = Json.parse(raw); NativeMeasurementConfiguration.validate(definition, value); return value;
  }
  @Test void actualMinimalCaptureAndEmissionRecheckHaveSameRawBindingsAndKernelReadback() throws Exception {
    Path directory = path("MAEZO_NATIVE_MEASUREMENT_IT_EVIDENCE_DIRECTORY");
    Map<String, Object> config = read(path("MAEZO_NATIVE_MEASUREMENT_IT_CONFIG"), "Config");
    assertEquals(directory.toString(), config.get("root_evidence_directory"));
    Map<String, Object> provenance = read(directory.resolve("root-provenance.json"), "RootProvenance");
    Map<String, Object> readback = read(directory.resolve("root-terminal-readback.json"), "RootTerminalReadbackRawCapture");
    // Own schema readback locates the outcome at its exact admitted key.
    Map<String, Object> outcome = Json.object(readback.get("outcome"));
    NativeMeasurementConfiguration.validate("FinalOutcome", outcome);
    assertEquals("PRIMARY_COMPLETE_AS_OF_SEAL", outcome.get("result"));
    assertEquals("TRUSTED_JVM_PRIMARY_OPERATION_AS_OF_ONLY_NO_FUTURE_READBACK_CLEANUP_CLAIM", outcome.get("certainty"));
    assertEquals(config.get("session_nonce"), outcome.get("session_nonce"));
    assertEquals(config.get("request_sha256"), outcome.get("request_sha256"));
    assertEquals(config.get("candidate_sha"), outcome.get("candidate_sha"));
    // The real served MAIN path always includes capture and one emission recheck.
    assertEquals(2L, outcome.get("stages_completed"));
    assertEquals(config.get("original_deadline"), outcome.get("original_deadline"));
    Instant previous = Instant.MIN;
    for (String key : List.of("primary_http_closed_at", "primary_uds_closed_at", "primary_postclose_guard_at", "sealed_at")) {
      Instant value = Instant.parse(Json.string(outcome, key)); assertFalse(value.isBefore(previous)); previous = value;
    }
    assertTrue(previous.isBefore(Instant.parse(Json.string(outcome, "original_deadline"))));
    byte[] terminal = Files.readAllBytes(directory.resolve("event-16-TERMINAL.json"));
    byte[] closed = Files.readAllBytes(directory.resolve("event-15-STAGE_CLOSED.json"));
    assertEquals(sha(terminal), outcome.get("terminal_raw_sha256"));
    assertEquals(sha(closed), outcome.get("last_stage_closed_raw_sha256"));
    NativeMeasurementConfiguration.validate("TERMINAL", Json.parse(terminal));
    NativeMeasurementConfiguration.validate("STAGE_CLOSED", Json.parse(closed));
    assertEquals("NOT_OBSERVED_NOT_CLAIMED", readback.get("remote_readback_cleanup_certainty"));
    assertEquals(config.get("candidate_sha"), provenance.get("candidate_sha"));
    assertEquals(config.get("request_sha256"), provenance.get("request_sha256"));
    for (int sequence = 0; sequence < 2; sequence++) {
      for (String endpoint : List.of("ENTRY", "COMMITTING")) {
        for (String phase : List.of("ROOT_FIRST", "ROOT_SECOND")) {
          var pg = read(directory.resolve("pg-s0" + sequence + "-" + endpoint + "-" + phase + ".json"), "RootPgRawCapture");
          assertEquals(endpoint, pg.get("endpoint")); assertEquals(phase, pg.get("phase"));
          assertEquals((long) sequence, pg.get("stage_sequence"));
        }
      }
    }
  }
}
