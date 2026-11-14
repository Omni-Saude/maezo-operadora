package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.cfg.standalone.StandaloneTransactionContext;
import org.cibseven.bpm.engine.impl.db.PersistenceSession;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * P1-D2 / P2-D2 / P2-D2-neg (DESIGN-D2-POST-COMMIT-v1 §3.4): the D2 state
 * machine pins, WITHOUT an engine and WITHOUT PostgreSQL.
 *
 * <p>P1-D2 uses the REAL vendored jar class
 * {@code org.cibseven.bpm.engine.impl.cfg.standalone.StandaloneTransactionContext}
 * ({@code org.cibseven.bpm:cibseven-engine:2.2.0}): the D2 consumption listener
 * runs strictly AFTER {@code PersistenceSession.commit()} and strictly AFTER
 * the instrument's own COMMITTED marker listener, because the marker is
 * registered first (openStage) and the consumer second (capture) and the jar
 * fires listeners in registration order. A fake RELEASE that arrives Δ late
 * does NOT move {@code PersistenceSession.commit()} — the Δ sweep
 * {0, 10, 100, 1000} ms is the unit form of the decoupling the D2 design buys
 * (the commit is invariant; only the consumption waits).
 *
 * <p>P2-D2 drives the exact {@link NativeMeasurementPort} interface with a
 * faithful contract mirror of the D2 machine: {@code hold(COMMITTING)} emits
 * the in-tx oath and parks in {@code HOLD_POSTPONED}; the custody-only seal
 * produces {@code COMMIT_SEALED} with ZERO post-commit {@code measure()} and
 * ZERO post-commit {@code actualObjects()} (the F-3 option (i) disposition);
 * the COMMITTING seal before the marker refuses exactly like the real machine
 * (a null {@code committed_marker_at} is refused by the frame schema); and
 * {@code stageClosed()} keeps its pre-D2 preconditions. The mirror is a
 * contract test of the transition table, not runtime evidence; the sealed
 * instrument runs on the engine lane.
 *
 * <p>P2-D2-neg: a consumer whose RELEASE never arrives parks (and, when it
 * finally fails, fails) AFTER the SQL commit — the durable stage is already
 * committed while the consumption pends — and that failure produces exactly
 * ONE {@code PersistenceSession.commit()} and NO rollback: the engine never
 * issues a second commit for a post-commit consumption failure, and the abort
 * unwind stays the caller's business (the instrument never rolls back or
 * commits a caller transaction). R29-D of the design (§6).
 */
final class D2PostCommitListenerTest {
  private D2PostCommitListenerTest() { }

  private static CommandContext stubContext(PersistenceSession session) {
    // The layout-default factory builds exactly this context type; the stub
    // reroutes getPersistenceProvider() to the recorder (same mold as
    // CommittingListenerOrderTest).
    return new CommandContext(new ProcessEngineConfigurationImpl() {
      @Override protected java.util.Collection<org.cibseven.bpm.engine.impl.interceptor.CommandInterceptor> getDefaultCommandInterceptorsTxRequired() { return List.of(); }
      @Override protected java.util.Collection<org.cibseven.bpm.engine.impl.interceptor.CommandInterceptor> getDefaultCommandInterceptorsTxRequiresNew() { return List.of(); }
    }, ctx -> new StandaloneTransactionContext(ctx)) {
      @Override public <T> T getSession(Class<T> type) { return type.cast(session); }
    };
  }

  private static PersistenceSession recordingSession(List<String> order) {
    return new PersistenceSession() {
      @Override public void commit() { order.add("persistence-session-commit"); }
      @Override public void rollback() { order.add("persistence-session-rollback"); }
      @Override public org.cibseven.bpm.engine.impl.db.FlushResult executeDbOperations(
          List<org.cibseven.bpm.engine.impl.db.entitymanager.operation.DbOperation> operations) {
        throw new UnsupportedOperationException("unit");
      }
      @Override public void flushOperations() { }
      @Override public List<?> selectList(String statement, Object parameter) { throw new UnsupportedOperationException("unit"); }
      @Override public <T extends org.cibseven.bpm.engine.impl.db.DbEntity> T selectById(Class<T> type, String id) { throw new UnsupportedOperationException("unit"); }
      @Override public Object selectOne(String statement, Object parameter) { throw new UnsupportedOperationException("unit"); }
      @Override public void lock(String statement, Object parameter) { }
      @Override public int executeNonEmptyUpdateStmt(String statement, Object parameter) { throw new UnsupportedOperationException("unit"); }
      @Override public void dbSchemaCheckVersion() { }
      @Override public void dbSchemaCreate() { }
      @Override public void dbSchemaDrop() { }
      @Override public void dbSchemaPrune() { }
      @Override public void dbSchemaUpdate() { }
      @Override public List<String> getTableNamesPresent() { return List.of(); }
      @Override public void addEntityLoadListener(org.cibseven.bpm.engine.impl.db.EntityLoadListener listener) { }
      @Override public void flush() { }
      @Override public void close() { }
    };
  }

  /** P1-D2: the full D2 ordering pin over the REAL jar commit path. */
  @ParameterizedTest
  @ValueSource(longs = {0, 10, 100, 1000})
  void consumptionIsStrictlyPostCommitAndTheCommitIsInvariantInTheReleaseDelta(long deltaMs) throws Exception {
    List<String> order = Collections.synchronizedList(new ArrayList<>());
    CountDownLatch release = new CountDownLatch(1);
    StandaloneTransactionContext transaction = new StandaloneTransactionContext(stubContext(recordingSession(order)));
    transaction.addTransactionListener(TransactionState.COMMITTING, context -> order.add("committing-hold-in-tx"));
    // Registration order == production order: the instrument's marker listener
    // is registered in openStage, BEFORE the observer's consumer listener in
    // capture. The jar must fire them in exactly this order.
    transaction.addTransactionListener(TransactionState.COMMITTED, context -> order.add("instrument-marker-committed"));
    transaction.addTransactionListener(TransactionState.COMMITTED, context -> {
      try {
        // The D2 consumption: waits for the (fake) ROOT RELEASE to arrive.
        if (!release.await(30, TimeUnit.SECONDS)) throw new AssertionError("unit latch timeout");
      } catch (InterruptedException interrupted) {
        Thread.currentThread().interrupt(); throw new AssertionError(interrupted);
      }
      order.add("consumer-await-and-seal");
    });
    Thread committer = new Thread(transaction::commit, "unit-d2-commit-" + deltaMs);
    committer.start();
    if (deltaMs > 0) {
      Thread.sleep(deltaMs);
      // While the consumer is parked on the RELEASE, the SQL commit has ALREADY
      // happened: the blocking site is POST-commit (the D2 property), and the
      // COMMITTING listener ran and RETURNED before the commit (no retention).
      assertEquals(
          List.of("committing-hold-in-tx", "persistence-session-commit",
              "instrument-marker-committed"),
          order);
    }
    release.countDown();
    committer.join(30000);
    assertFalse(committer.isAlive());
    assertEquals(
        List.of("committing-hold-in-tx", "persistence-session-commit",
            "instrument-marker-committed", "consumer-await-and-seal"),
        order);
  }

  /** P1-D2 registration-order half: marker first, consumer second. */
  @Test
  void markerListenerRegisteredFirstFiresBeforeTheConsumer() throws Exception {
    List<String> order = Collections.synchronizedList(new ArrayList<>());
    StandaloneTransactionContext transaction = new StandaloneTransactionContext(stubContext(recordingSession(order)));
    transaction.addTransactionListener(TransactionState.COMMITTED, context -> order.add("marker"));
    transaction.addTransactionListener(TransactionState.COMMITTED, context -> order.add("consumption"));
    transaction.commit();
    assertEquals(List.of("persistence-session-commit", "marker", "consumption"), order);
  }

  /**
   * P2-D2: the D2 machine contract mirrored faithfully from the design (§2.2).
   */
  @Test
  void committingMachinePostponesTheConsumptionAndSealsCustodyOnlyWithoutAnyPostCommitMeasure() {
    D2MachinePort port = new D2MachinePort();
    port.openStage(0, UNIT_UUID, null, null);
    // The stage's ENTRY leg runs in-tx first (the real hold() precondition for
    // COMMITTING requires ENTRY_SEALED), so the machine is driven exactly as
    // the production seam drives it.
    port.hold(NativeMeasurementPort.Endpoint.ENTRY);
    port.awaitRootRelease();
    port.seal();
    assertEquals(D2MachinePort.S.ENTRY_SEALED, port.state);
    // The D2 COMMITTING seam: hold() only. The in-tx oath is emitted HERE.
    port.hold(NativeMeasurementPort.Endpoint.COMMITTING);
    assertEquals(D2MachinePort.S.HOLD_POSTPONED, port.state);
    assertEquals(
        List.of("openStage", "actualObjects-in-tx",
            "hold:ENTRY", "measure-in-tx", "awaitRootRelease", "emit-hold-in-tx", "actualObjects-in-tx",
            "seal", "measure-in-tx", "measure-in-tx",
            "hold:COMMITTING", "measure-in-tx", "emit-hold-in-tx"),
        port.calls);
    // The consumption pair belongs to the COMMITTED listener. Before the
    // marker, the COMMITTING seal refuses exactly like the real machine: it
    // would emit a null committed_marker_at, refused by the frame schema.
    // Driven on a fresh machine (after awaitRootRelease the original is past
    // RELEASED and cannot re-await).
    D2MachinePort fresh = new D2MachinePort();
    fresh.openStage(0, UNIT_UUID, null, null);
    fresh.hold(NativeMeasurementPort.Endpoint.ENTRY);
    fresh.awaitRootRelease();
    fresh.seal();
    fresh.hold(NativeMeasurementPort.Endpoint.COMMITTING);
    fresh.awaitRootRelease();
    IllegalStateException preMarker = assertThrows(IllegalStateException.class, fresh::seal);
    assertTrue(preMarker.getMessage().contains("committed_marker_at"));
    // Marker (instrument COMMITTED listener, registered first) — set AFTER the
    // SQL commit in the real firing order — then the consumption.
    port.sqlCommitted = true;
    port.committedMarkerAt = MARKER_AT;
    port.committedMarkerSeen = true;
    port.contextClosed = false;
    port.awaitRootRelease();
    port.seal();
    assertEquals(D2MachinePort.S.COMMIT_SEALED, port.state);
    // F-3 option (i): NOTHING re-measures after the SQL commit on this path.
    // In-tx total: the ENTRY oath + the ENTRY seal's two re-measures + the
    // COMMITTING oath — all before the commit boundary.
    assertEquals(4, port.measureInTxCount);
    assertEquals(0, port.measureAfterSqlCommitCount);
    assertEquals(0, port.actualObjectsAfterSqlCommitCount);
    assertEquals(2, port.entrySealRemeasureCount);
    // seal() cannot run twice (state), and stageClosed() is UNCHANGED: same
    // preconditions, same close.
    IllegalStateException alreadySealed = assertThrows(IllegalStateException.class, port::seal);
    assertTrue(alreadySealed.getMessage().contains("refused (state)"));
    IllegalStateException openContext = assertThrows(IllegalStateException.class, port::stageClosed);
    assertTrue(openContext.getMessage().contains("Context.getCommandContext() == null"));
    port.contextClosed = true;
    port.stageClosed();
    assertEquals(D2MachinePort.S.STAGE_CLOSED, port.state);
  }

  /**
   * P2-D2 (ENTRY half): the ENTRY endpoint is the pre-D2 machine unchanged.
   */
  @Test
  void entryEndpointKeepsTheFullInTxConsumeAndMeasureSeal() {
    D2MachinePort port = new D2MachinePort();
    port.openStage(0, UNIT_UUID, null, null);
    port.hold(NativeMeasurementPort.Endpoint.ENTRY);
    assertEquals(D2MachinePort.S.HOLD_READY, port.state);
    port.awaitRootRelease();
    port.seal();
    assertEquals(D2MachinePort.S.ENTRY_SEALED, port.state);
    // The ENTRY seal re-measures twice (fresh + post-write guard read), all
    // in-tx; the F-3 identity claim ran in-tx as always.
    assertEquals(3, port.measureInTxCount);
    assertEquals(0, port.measureAfterSqlCommitCount);
    assertEquals(0, port.actualObjectsAfterSqlCommitCount);
    // The stage still needs its COMMITTING leg before stageClosed (the real
    // machine requires COMMIT_SEALED): hold in-tx, then the post-commit
    // custody-only consumption, with the same zero-post-commit-measure result.
    port.hold(NativeMeasurementPort.Endpoint.COMMITTING);
    port.sqlCommitted = true;
    port.committedMarkerAt = MARKER_AT;
    port.committedMarkerSeen = true;
    port.contextClosed = false;
    port.awaitRootRelease();
    port.seal();
    assertEquals(D2MachinePort.S.COMMIT_SEALED, port.state);
    assertEquals(0, port.measureAfterSqlCommitCount);
    assertEquals(0, port.actualObjectsAfterSqlCommitCount);
    port.contextClosed = true;
    port.stageClosed();
    assertEquals(D2MachinePort.S.STAGE_CLOSED, port.state);
  }

  /**
   * P2-D2-neg: a consumer whose RELEASE never arrives parks (and, when it
   * finally fails, fails) AFTER the SQL commit, and that failure produces
   * exactly ONE engine commit and NO rollback (R29-D, design §6).
   */
  @ParameterizedTest
  @ValueSource(booleans = {false, true})
  void neverDeliveredReleaseParksPostCommitAndTheFailureIssuesNoSecondCommitOrRollback(boolean deliver) throws Exception {
    List<String> order = Collections.synchronizedList(new ArrayList<>());
    CountDownLatch release = new CountDownLatch(1);
    StandaloneTransactionContext transaction = new StandaloneTransactionContext(stubContext(recordingSession(order)));
    transaction.addTransactionListener(TransactionState.COMMITTING, context -> order.add("committing-hold-in-tx"));
    transaction.addTransactionListener(TransactionState.COMMITTED, context -> order.add("instrument-marker-committed"));
    transaction.addTransactionListener(TransactionState.COMMITTED, context -> {
      try {
        if (!release.await(30, TimeUnit.SECONDS)) throw new AssertionError("unit latch timeout");
      } catch (InterruptedException interrupted) {
        Thread.currentThread().interrupt(); throw new AssertionError(interrupted);
      }
      if (!deliver) throw new IllegalStateException("Independent measurement refused");
      order.add("consumer-await-and-seal");
    });
    Thread committer = new Thread(() -> {
      if (deliver) {
        transaction.commit();
      } else {
        assertThrows(IllegalStateException.class, transaction::commit);
      }
    }, "unit-d2-neg-" + deliver);
    committer.start();
    Thread.sleep(100);
    if (!deliver) {
      // The consumer is parked (and headed for its failure) AFTER the SQL
      // commit: the stage is already durable while the consumption pends.
      assertEquals(
          List.of("committing-hold-in-tx", "persistence-session-commit",
              "instrument-marker-committed"),
          order);
    }
    release.countDown();
    committer.join(30000);
    assertFalse(committer.isAlive());
    // Exactly ONE engine commit in both endings, and NO rollback: a post-commit
    // consumption failure never makes the engine issue a second commit or a
    // rollback of the caller transaction.
    assertEquals(1, order.stream().filter("persistence-session-commit"::equals).count());
    assertFalse(order.contains("persistence-session-rollback"));
    if (deliver) {
      assertEquals(
          List.of("committing-hold-in-tx", "persistence-session-commit",
              "instrument-marker-committed", "consumer-await-and-seal"),
          order);
    }
  }

  /** Shared fixture constants for the machine-mirror tests. */
  private static final String UNIT_UUID = "01234567-89ab-cdef-0123-456789abcdef";
  private static final String MARKER_AT = "2026-10-27T12:00:00.000000Z";

  /**
   * A faithful contract mirror of the D2 machine (design §2.2) over the exact
   * {@link NativeMeasurementPort} interface. It mirrors the transition table,
   * keeping the two boundaries DISTINCT the way the real machine has them: the
   * SQL commit (the custody-counter boundary) and the COMMITTED marker (the
   * {@code stage.committed} fact the seal and stageClosed consume; set after
   * the SQL commit for COMMITTING, at the following listener for ENTRY) —
   * including the COMMITTING-only null-marker seal refusal the real machine
   * reaches through schema validation of the COMMITTING SEAL frame. Not
   * runtime evidence; the sealed instrument runs on the engine lane.
   */
  private static final class D2MachinePort implements NativeMeasurementPort {
    enum S { READY, OPEN, HOLD_READY, HOLD_POSTPONED, RELEASED, ENTRY_SEALED, COMMIT_SEALED, STAGE_CLOSED }
    final List<String> calls = new ArrayList<>();
    S state = S.READY;
    boolean sqlCommitted;
    boolean committedMarkerSeen;
    String committedMarkerAt;
    boolean contextClosed;
    String holdRawSha;
    int measureInTxCount;
    int measureAfterSqlCommitCount;
    int actualObjectsAfterSqlCommitCount;
    int entrySealRemeasureCount;

    private void measure() {
      // Mirror of measure(): the in-tx oath at hold() time; any call after the
      // SQL commit is a post-commit re-measure (the fabricated custody the D2
      // design refuses — NO-GO-2).
      if (sqlCommitted) measureAfterSqlCommitCount++; else measureInTxCount++;
      calls.add(sqlCommitted ? "measure-after-commit" : "measure-in-tx");
    }

    private void actualObjects() {
      // Mirror of the F-3 site: the in-tx identity claim, counted after the SQL
      // commit whenever it would run there (it must never, option (i)).
      if (sqlCommitted) actualObjectsAfterSqlCommitCount++;
      calls.add(sqlCommitted ? "actualObjects-after-commit" : "actualObjects-in-tx");
    }

    @Override public void openStage(int sequence, String executionUuid, CommandContext ctx, java.sql.Connection enlisted) {
      calls.add("openStage");
      actualObjects();
      state = S.OPEN;
    }

    @Override public void hold(Endpoint endpoint) {
      // Mirror of the real hold() preconditions: ENTRY needs OPEN, COMMITTING
      // needs ENTRY_SEALED (the stage's ENTRY leg must have sealed first).
      if (endpoint == Endpoint.ENTRY ? state != S.OPEN : state != S.ENTRY_SEALED) {
        throw new IllegalStateException("Independent measurement refused (state)");
      }
      calls.add("hold:" + endpoint.name());
      measure(); // the in-tx oath, BOTH endpoints (unchanged measurand, §2.5)
      if (endpoint == Endpoint.ENTRY) {
        state = S.HOLD_READY; // the ENTRY oath is emitted at awaitRootRelease (unchanged shape)
      } else {
        calls.add("emit-hold-in-tx");
        holdRawSha = "hold-raw-" + endpoint.name();
        state = S.HOLD_POSTPONED;
      }
    }

    @Override public void awaitRootRelease() {
      if (state != S.HOLD_READY && state != S.HOLD_POSTPONED) {
        throw new IllegalStateException("Independent measurement refused (state)");
      }
      calls.add("awaitRootRelease");
      if (state == S.HOLD_READY) {
        holdRawSha = "hold-raw-ENTRY";
        calls.add("emit-hold-in-tx");
      }
      // The receive consumes the (fake) dispatched RELEASE.
      boolean entry = holdRawSha.endsWith(Endpoint.ENTRY.name());
      state = S.RELEASED;
      // F-3 option (i) mirror: the identity claim runs on ENTRY only.
      if (entry) actualObjects();
    }

    @Override public void seal() {
      if (state != S.RELEASED) {
        throw new IllegalStateException("Independent measurement refused (state)");
      }
      calls.add("seal");
      if (holdRawSha.endsWith(Endpoint.ENTRY.name())) {
        // ENTRY: unchanged re-measuring seal (fresh + post-write guard read).
        // It runs inside the capture command, BEFORE the marker listener, and
        // its frame carries no marker at all.
        measure();
        measure();
        entrySealRemeasureCount += 2;
        state = S.ENTRY_SEALED;
      } else {
        if (committedMarkerAt == null) {
          // COMMITTING-only mirror of the real machine: the custody-only seal
          // would emit a null marker and the frame schema refuses
          // `committed_marker_at` — fail-closed, never fabricated.
          throw new IllegalStateException("Independent measurement refused (committed_marker_at)");
        }
        // COMMITTING: custody-only. NO measure(), NO actualObjects().
        state = S.COMMIT_SEALED;
      }
    }

    @Override public void stageClosed() {
      // Unchanged preconditions (instrument stageClosed): COMMIT_SEALED, the
      // marker seen, the stage document retained, and the command context closed.
      if (state != S.COMMIT_SEALED || !committedMarkerSeen || committedMarkerAt == null || !contextClosed) {
        throw new IllegalStateException("Independent measurement refused (Context.getCommandContext() == null)");
      }
      calls.add("stageClosed");
      state = S.STAGE_CLOSED;
    }

    @Override public void complete(String rawResponseSha256) { calls.add("complete"); }

    @Override public void abort() { calls.add("abort"); }
  }
}
