package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.util.Map;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

/** Independent offline deadline semantics; not an engine/PG integration test. */
class DeltaFreshnessTest {
  @ParameterizedTest
  @CsvSource({"99,false", "100,true", "159,true", "160,false", "161,false"})
  void aVerifiedSignedEnvelopeCannotRenewItsDeadlineAtLaterUse(long current, boolean allowed) {
    TestKeys keys = new TestKeys();
    var body = Map.<String,Object>of("tenant", "tenant-test", "workload_ref", "gateway-test");
    byte[] signed = keys.sign(body, "human-command", 100, 160);
    var verified = Envelope.verify(signed, keys.trust(), "human-command", keys.peer, 100, fp -> false);
    assertEquals(100, verified.issuedAt());
    assertEquals(160, verified.expiresAt());
    if (allowed) assertDoesNotThrow(() -> verified.requireCurrent(current));
    else {
      Rejected error = assertThrows(Rejected.class, () -> verified.requireCurrent(current));
      assertEquals(403, error.status);
      assertEquals("AUTHORITY_DENIED", error.code);
    }
  }

  @ParameterizedTest
  @CsvSource({"159,true,true", "160,true,false", "161,true,false", "159,false,false"})
  void principalAuthorityUsesExclusiveDeadlineAndActiveFlag(long current, boolean active, boolean allowed) {
    var principal = Map.<String,Object>of("active_", active, "valid_until_", 160L);
    if (allowed) assertDoesNotThrow(() -> EngineStore.requireCurrentPrincipal(principal, current));
    else {
      Rejected error = assertThrows(Rejected.class, () -> EngineStore.requireCurrentPrincipal(principal, current));
      assertEquals(403, error.status);
      assertEquals("AUTHORITY_DENIED", error.code);
    }
  }
}
