package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

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

/**
 * P1 (DESIGN-OPTION-REFUSAL-63-ROOT-v1.md v1.1 §5.2-P1, §1.3): the listener→SQL
 * commit ORDER, proven with the REAL vendored jar class
 * ({@code org.cibseven.bpm:cibseven-engine:2.2.0}, scope provided, pom.xml:9) —
 * no engine, no PG, no network.
 *
 * <p>{@code StandaloneTransactionContext.commit()} fires the COMMITTING listeners
 * on the caller thread BEFORE {@code PersistenceSession.commit()} and the
 * COMMITTED listeners after it, with no try/catch around
 * {@code fireTransactionEvent} (jar bytecode §1.3 of the design). This pins the
 * ORDERING that makes a blocking COMMITTING listener a retention site for the
 * engine transaction — the site whose retention the D2' early RELEASE removes
 * on the observer side (§4.2). The residual time channel of the variant in ar
 * was identified by P0 (teardown-abort; §10.1-§10.3) and is discriminated on
 * the lane by P4; this unit only pins the jar's ordering contract.
 */
class CommittingListenerOrderTest {
  private static CommandContext stubContext(PersistenceSession session) {
    // The layout-default factory (no <transactionManager>) builds exactly this
    // context type; the stub reroutes getPersistenceProvider() to the recorder.
    // The configuration is a concrete inert shell: the transaction path under
    // test never reads it beyond the constructor field assignments.
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

  @Test
  void committingListenerBlocksPersistenceSessionCommitInJarOrder() throws Exception {
    for (long releaseAfterMs : new long[] {0, 10, 100, 1000}) {
      List<String> order = Collections.synchronizedList(new ArrayList<>());
      CountDownLatch release = new CountDownLatch(1);
      StandaloneTransactionContext transaction = new StandaloneTransactionContext(stubContext(recordingSession(order)));
      transaction.addTransactionListener(TransactionState.COMMITTING, context -> {
        order.add("committing-listener-entered");
        try {
          if (!release.await(30, TimeUnit.SECONDS)) throw new AssertionError("unit latch timeout");
        } catch (InterruptedException interrupted) {
          Thread.currentThread().interrupt(); throw new AssertionError(interrupted);
        }
        order.add("committing-listener-returned");
      });
      transaction.addTransactionListener(TransactionState.COMMITTED, context -> order.add("committed-listener"));
      Thread committer = new Thread(transaction::commit, "unit-commit-" + releaseAfterMs);
      committer.start();
      if (releaseAfterMs > 0) {
        Thread.sleep(releaseAfterMs);
        // While the COMMITTING listener is parked, the SQL commit has NOT run:
        // a blocking listener retains the engine transaction (§1.3).
        assertEquals(List.of("committing-listener-entered"), order);
      }
      release.countDown();
      committer.join(30000);
      assertFalse(committer.isAlive());
      assertEquals(
          List.of("committing-listener-entered", "committing-listener-returned",
              "persistence-session-commit", "committed-listener"),
          order);
    }
  }

  @Test
  void jarCommitPathShapeIsPinned() throws Exception {
    // The layout-default product takes exactly one CommandContext and fires the
    // state events from its own commit(); the ordering pin depends on this shape.
    assertEquals(1, StandaloneTransactionContext.class.getConstructors().length);
    assertEquals(CommandContext.class, StandaloneTransactionContext.class.getConstructors()[0].getParameterTypes()[0]);
    assertEquals("commit", StandaloneTransactionContext.class.getMethod("commit").getName());
    assertEquals("fireTransactionEvent",
        StandaloneTransactionContext.class.getDeclaredMethod("fireTransactionEvent", TransactionState.class).getName());
    assertEquals("getPersistenceProvider",
        StandaloneTransactionContext.class.getDeclaredMethod("getPersistenceProvider").getName());
  }
}
