package br.com.maezo.human;
import static br.com.maezo.human.PortalReadModels.*;
import static org.junit.jupiter.api.Assertions.*;

import java.util.*;
import org.junit.jupiter.api.*;
/** Actual enlisted publisher commit/rollback controls, ROOT-only PostgreSQL fixture. */
@Tag("integration")
class PortalReadPublisherEngineIT {
  PortalReadEngineIT.Harness h;
  @BeforeEach
  void start() throws Exception {
    h = new PortalReadEngineIT.Harness();
    h.start();
  }
  @AfterEach
  void stop() throws Exception {
    if (h != null)
      h.close();
  }
  @Test
  void actualPostFlushRevisionAndLostAckReplay() throws Exception {
    String id = h.task(false);
    var payload = h.resourcePayload(id);
    var request = h.publication("resource", payload);
    long before = h.revision();
    byte[] first = h.plugin.execute(h.signed(request, true), h.publisherPeer, "publications");
    long after = h.config.getCommandExecutorTxRequired().execute(
        c -> (long) c.getTaskManager().findTaskById(id).getRevision());
    assertTrue(after > number(payload.get("observed_task_revision")));
    try (var c = h.connection();
        var p = c.prepareStatement("SELECT PAYLOAD_ FROM MZO_PORTAL_READ_RESOURCE WHERE TASK_=?")) {
      p.setString(1, id);
      try (var r = p.executeQuery()) {
        assertTrue(r.next());
        assertEquals(Long.toString(after),
            PortalReadStore.json(r.getString(1)).get("observed_task_revision"));
      }
    }
    assertEquals(before + 1, h.revision());
    assertArrayEquals(
        first, h.plugin.execute(h.signed(request, true), h.publisherPeer, "publications"));
    assertEquals(before + 1, h.revision());
    var changed = copy(request);
    obj(changed, "payload").put("resource_digest", "f".repeat(64));
    assertEquals(409,
        assertThrows(Rejected.class,
            () -> h.plugin.execute(h.signed(changed, true), h.publisherPeer, "publications"))
            .status);
  }
  @Test
  void failedFinalGuardRollsBackTaskProjectionTenantAndReceipt() throws Exception {
    String id = h.task(false);
    var payload = h.resourcePayload(id);
    var request = h.publication("resource", payload);
    long before = h.revision();
    var verified = PortalReadEnvelope.verify(h.signed(request, true), h.trust,
        "portal-read-publication", h.publisherPeer, java.time.Instant.now());
    var admitted = h.trust.acquire("portal-read-publication", obj(request, "scope"));
    var qualified = h.trust.providers.qualifyPublication(admitted, request);
    boolean[] reached = {false};
    assertThrows(
        RuntimeException.class, () -> h.config.getCommandExecutorTxRequired().execute(context -> {
          var result =
              new PortalReadPublication(h.trust, verified, admitted, qualified).execute(context);
          context.getTransactionContext().addTransactionListener(
              org.cibseven.bpm.engine.impl.cfg.TransactionState.COMMITTING, ignored -> {
                var db = new PortalReadStore(context, h.trust, admitted.statementTimeoutSeconds());
                assertNotNull(
                    db.one("SELECT PAYLOAD_ FROM MZO_PORTAL_READ_RESOURCE WHERE TASK_=?", id));
                assertNotNull(db.one(
                    "SELECT RECEIPT_ FROM MZO_PORTAL_READ_PUBLICATION_RECEIPT WHERE PUBLICATION_=?",
                    request.get("publication_id")));
                reached[0] = true;
                throw unavailable();
              });
          return result;
        }));
    assertTrue(reached[0], "rollback control must reach actual post-write COMMITTING boundary");
    assertEquals(before, h.revision());
    long actual = h.config.getCommandExecutorTxRequired().execute(
        c -> (long) c.getTaskManager().findTaskById(id).getRevision());
    assertEquals(number(payload.get("observed_task_revision")), actual);
    try (var c = h.connection();
        var p = c.prepareStatement("SELECT count(*) FROM MZO_PORTAL_READ_RESOURCE WHERE TASK_=?")) {
      p.setString(1, id);
      try (var r = p.executeQuery()) {
        r.next();
        assertEquals(0, r.getInt(1));
      }
    }
    assertNotNull(h.plugin.execute(h.signed(request, true), h.publisherPeer, "publications"));
  }
  @Test
  void staleCasAndCatalogTombstoneCannotResurrect() throws Exception {
    var request = h.publication(
        "catalog-revoke", record("catalog_ref", "catalog", "expected_catalog_revision", "1"));
    request.put("expected_authority_revision", "0");
    assertEquals(409,
        assertThrows(Rejected.class,
            () -> h.plugin.execute(h.signed(request, true), h.publisherPeer, "publications"))
            .status);
    request.put("expected_authority_revision", Long.toString(h.revision()));
    h.plugin.execute(h.signed(request, true), h.publisherPeer, "publications");
    assertEquals(503,
        assertThrows(Rejected.class, () -> h.read("catalog", record("anchor", h.anchor))).status);
    var p = record("catalog_ref", "catalog", "catalog_revision", "2", "catalog_digest",
        hash(h.artifact), "catalog_artifact_base64",
        Base64.getEncoder().encodeToString(bounded(h.artifact)), "deployment_receipt_ref",
        h.artifact.get("deployment_receipt_ref"), "deployment_receipt_digest",
        h.artifact.get("deployment_receipt_digest"), "valid_until", time(h.until));
    assertEquals(409, assertThrows(Rejected.class, () -> h.publish("catalog-designate", p)).status);
  }
  @Test
  void readSignerCannotPublishAndRevokedReaderCannotRead() throws Exception {
    var request = h.publication(
        "revoke-key", record("key_fingerprint", Jcs.digest(h.readKey.getPublic().getEncoded())));
    assertEquals(403,
        assertThrows(Rejected.class,
            () -> h.plugin.execute(h.signed(request, false), h.readPeer, "publications"))
            .status);
    h.plugin.execute(h.signed(request, true), h.publisherPeer, "publications");
    assertEquals(403,
        assertThrows(Rejected.class, () -> h.read("catalog", record("anchor", h.anchor))).status);
  }
}
