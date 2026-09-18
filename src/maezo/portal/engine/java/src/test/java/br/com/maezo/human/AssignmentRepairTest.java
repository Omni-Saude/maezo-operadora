package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static org.junit.jupiter.api.Assertions.*;

import java.lang.reflect.*;
import java.security.*;
import java.sql.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.cibseven.bpm.engine.impl.cfg.*;
import org.cibseven.bpm.engine.impl.db.sql.*;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Offline unit collaborators only: no engine instance, connection or qualified authority. */
class AssignmentRepairTest {
  @SuppressWarnings("unchecked")
  static <T> T proxy(Class<T> kind, InvocationHandler handler) {
    return (T) Proxy.newProxyInstance(kind.getClassLoader(), new Class<?>[]{kind}, handler);
  }

  static EngineStore syntheticStore(Runnable query) {
    ResultSet rs=proxy(ResultSet.class,(p,m,a)->switch(m.getName()) {
      case "next" -> false;
      case "close" -> null;
      default -> throw new AssertionError(m.getName());
    });
    PreparedStatement ps=proxy(PreparedStatement.class,(p,m,a)->switch(m.getName()) {
      case "executeQuery" -> {query.run();yield rs;}
      case "setObject","close" -> null;
      default -> throw new AssertionError(m.getName());
    });
    DatabaseMetaData md=proxy(DatabaseMetaData.class,(p,m,a)-> {
      assertEquals("getDatabaseProductName",m.getName());return "PostgreSQL";
    });
    Connection connection=proxy(Connection.class,(p,m,a)->switch(m.getName()) {
      case "getAutoCommit" -> false;
      case "getMetaData" -> md;
      case "prepareStatement" -> {assertTrue(((String)a[0]).startsWith("SELECT REV_ FROM MZO_HUMAN_REVOKED_KEY"));yield ps;}
      default -> throw new AssertionError(m.getName());
    });
    SqlSession session=proxy(SqlSession.class,(p,m,a)-> {
      assertEquals("getConnection",m.getName());return connection;
    });
    SqlSessionFactory sessions=proxy(SqlSessionFactory.class,(p,m,a)-> {
      assertEquals("openSession",m.getName());return session;
    });
    var sqlFactory=new DbSqlSessionFactory(false);sqlFactory.setSqlSessionFactory(sessions);
    var sql=new SimpleDbSqlSession(sqlFactory);
    var config=new StandaloneProcessEngineConfiguration();
    var context=new CommandContext(config,c->proxy(TransactionContext.class,(p,m,a)->null)) {
      @Override public DbSqlSession getDbSqlSession(){return sql;}
    };
    return new EngineStore(context,"tenant-test");
  }

  record SourceFixture(AssignmentTrust trust,Map<String,Object> attestation,Instant sampledAt) {}
  static SourceFixture sourceFixture(boolean receipt,String expired) throws Exception {
    var keys=new TestKeys();var staff=KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
    var disclosure=KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
    var read=KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
    Instant now=Instant.now(),sampled=now.minusSeconds(10),past=now.minusSeconds(5),future=now.plusSeconds(120);
    var config=record("schema","human-assignment-trust.v1","tenant","tenant-test","environment","test",
      "engine_name","human-it","database_incarnation","db-test","deployment_receipt",record("artifact_ref","deployment","digest","a".repeat(64)),
      "validity_policy",record("artifact_ref","validity","digest","b".repeat(64)),"valid_until",Long.toString((expired.equals("configuration")?past:future).getEpochSecond()),
      "publisher",record("workload_ref","publisher-test","key_fingerprint",Jcs.digest(keys.authority.getPublic().getEncoded())),
      "approved_policy_pins",List.of(),"approved_binding_pins",List.of(),"approved_receipt_policy_pins",List.of());
    for(boolean r:List.of(false,true)) {
      var pair=r?disclosure:staff;
      config.put(r?"receipt_disclosure_source":"source",record("owner_ref",r?"receipt-owner":"staff-owner","source_ref",r?"receipt-source":"staff-source",
        "key_id",r?"receipt-key":"staff-key","public_key_spki_base64",Base64.getEncoder().encodeToString(pair.getPublic().getEncoded()),
        "not_before","0","not_after",Long.toString((r==receipt&&expired.equals("source-key")?past:future).getEpochSecond())));
    }
    config.put("read_keys",List.of(record("id","read-key","workload","reader","peer_spki_sha256","d".repeat(64),
      "public_key_spki_base64",Base64.getEncoder().encodeToString(read.getPublic().getEncoded()),"not_before","0","not_after",Long.toString(future.getEpochSecond()))));
    var attestation=record("schema","human-staff-assignment-source.v1","tenant","tenant-test","environment","test","engine_name","human-it",
      "database_incarnation","db-test","source_key_id",receipt?"receipt-key":"staff-key","algorithm","Ed25519","generation_digest","c".repeat(64),
      "source",record("publisher_ref",receipt?"receipt-owner":"staff-owner","source_ref",receipt?"receipt-source":"staff-source","source_revision","1",
        "source_digest","c".repeat(64),"receipt_ref","source-receipt","observed_at",time(sampled.minusSeconds(1)),
        "valid_until",time(expired.equals("provenance")?past:future)));
    Signature signature=Signature.getInstance("Ed25519");signature.initSign((receipt?disclosure:staff).getPrivate());signature.update(Jcs.canonical(attestation));
    attestation.put("signature",Base64.getUrlEncoder().withoutPadding().encodeToString(signature.sign()));
    return new SourceFixture(new AssignmentTrust(config,keys.trust()),attestation,sampled);
  }

  @ParameterizedTest @ValueSource(strings={"provenance","source-key","configuration"})
  void lateRevocationReadCannotUseEarlierStaffClock(String expired) throws Exception {
    lateRead(false,expired);
  }
  @ParameterizedTest @ValueSource(strings={"provenance","source-key","configuration"})
  void lateRevocationReadCannotUseEarlierReceiptClock(String expired) throws Exception {
    lateRead(true,expired);
  }
  void lateRead(boolean receipt,String expired) throws Exception {
    var f=sourceFixture(receipt,expired);var reads=new AtomicInteger();
    var db=syntheticStore(()->{reads.incrementAndGet();assertTrue(Instant.now().isAfter(f.sampledAt().plusSeconds(5)));});
    assertThrows(Rejected.class,()->f.trust().source(f.attestation(),f.sampledAt(),db,receipt));
    assertEquals(1,reads.get());
  }
  @Test void currentSignedSourcesRemainAcceptedInDistinctNamespaces() throws Exception {
    for(boolean receipt:List.of(false,true)) {
      var f=sourceFixture(receipt,"none");var reads=new AtomicInteger();
      f.trust().source(f.attestation(),f.sampledAt(),syntheticStore(reads::incrementAndGet),receipt);
      assertEquals(1,reads.get());
      assertThrows(Rejected.class,()->f.trust().source(f.attestation(),f.sampledAt(),syntheticStore(()->{}),!receipt));
    }
  }

  // Exercises the real CIB interceptor/context/standalone transaction lifecycle with synthetic persistence.
  static CommandExecutor offlineExecutor(List<String> events,String fail) {
    return offlineExecutor(events,fail,false);
  }
  static CommandExecutor offlineExecutor(List<String> events,String fail,boolean managed) {
    var config=new StandaloneProcessEngineConfiguration();
    var sqlConfig=new org.apache.ibatis.session.Configuration();
    sqlConfig.setEnvironment(new org.apache.ibatis.mapping.Environment("synthetic",
      managed?new org.apache.ibatis.transaction.managed.ManagedTransactionFactory():new org.apache.ibatis.transaction.jdbc.JdbcTransactionFactory(),
      proxy(javax.sql.DataSource.class,(p,m,a)->{throw new AssertionError("no real connections");})));
    var sql=proxy(SqlSession.class,(p,m,a)->switch(m.getName()) {
      case "getConfiguration" -> sqlConfig;
      case "close","commit","rollback" -> null;
      default -> throw new AssertionError(m.getName());
    });
    var sqlFactory=new DbSqlSessionFactory(false);
    sqlFactory.setSqlSessionFactory(proxy(SqlSessionFactory.class,(p,m,a)->{
      assertEquals("openSession",m.getName());return sql;
    }));
    config.setTransactionContextFactory(c->new org.cibseven.bpm.engine.impl.cfg.standalone.StandaloneTransactionContext(c));
    var persistence=proxy(org.cibseven.bpm.engine.impl.db.PersistenceSession.class,(p,m,a)-> {
      if(Set.of("flush","commit","rollback","close").contains(m.getName())) {
        events.add(m.getName());if(m.getName().equals(fail))throw new IllegalStateException("synthetic "+fail);
        return null;
      }
      throw new AssertionError(m.getName());
    });
    config.setSessionFactories(Map.of(DbSqlSession.class,sqlFactory,org.cibseven.bpm.engine.impl.db.PersistenceSession.class,new SessionFactory(){
      public Class<?> getSessionType(){return org.cibseven.bpm.engine.impl.db.PersistenceSession.class;}
      public Session openSession(){return persistence;}
    }));
    var factory=new CommandContextFactory();factory.setProcessEngineConfiguration(config);
    var context=new CommandContextInterceptor(factory,config);context.setNext(new CommandExecutorImpl());
    var log=new LogInterceptor();log.setNext(context);return log;
  }
  static void privateDemand() {
    try {
      var method=GovernedAssignment.class.getDeclaredMethod("requireConstraints");method.setAccessible(true);method.invoke(null);
      throw new AssertionError("demand missing");
    } catch(InvocationTargetException e){throw (RuntimeException)e.getCause();}
    catch(ReflectiveOperationException e){throw new AssertionError(e);}
  }
  static void openSyntheticPersistence(CommandContext context){context.getSession(org.cibseven.bpm.engine.impl.db.PersistenceSession.class);}

  @Test void completedFirstCommandNeverAcquiresUnrelatedQ2() {
    var events=new ArrayList<String>();var bytes=new byte[]{1,2};var commands=new AtomicInteger();
    assertArrayEquals(bytes,GovernedAssignment.withConstraints(offlineExecutor(events,""),lease->{
      assertNull(lease);commands.incrementAndGet();return c->{openSyntheticPersistence(c);return bytes;};
    },()->{throw new AssertionError("Q2 must stay unrequested for completed none/recovery branch");}));
    assertEquals(1,commands.get());assertEquals(List.of("flush","commit","close"),events);
  }
  @Test void demandedFirstTransactionCommitsAndClosesBeforeQ2ThenNewCommand() {
    var events=new ArrayList<String>();var commands=new ArrayList<Command<byte[]>>();
    // This token is only a scheduling collaborator, never verified as Q2 authority.
    var token=new PortalReadPlugin.AssignmentConstraintLease(null,null);
    byte[] result=GovernedAssignment.withConstraints(offlineExecutor(events,""),lease->{
      Command<byte[]> command=c->{openSyntheticPersistence(c);if(lease==null)privateDemand();assertSame(token,lease);return new byte[]{3};};
      commands.add(command);return command;
    },()->{
      assertNull(org.cibseven.bpm.engine.impl.context.Context.getCommandContext());
      assertNull(org.cibseven.bpm.engine.impl.context.Context.getCommandInvocationContext());
      assertEquals(List.of("flush","commit","close"),events);events.add("acquire");return token;
    });
    assertArrayEquals(new byte[]{3},result);assertEquals(2,commands.size());assertNotSame(commands.get(0),commands.get(1));
    assertEquals(List.of("flush","commit","close","acquire","flush","commit","close"),events);
  }
  @ParameterizedTest @ValueSource(strings={"commit","close","flush"})
  void failedDemandTransactionNeverAcquiresOrRetries(String failure) {
    var events=new ArrayList<String>();var acquired=new AtomicInteger();var calls=new AtomicInteger();
    assertThrows(IllegalStateException.class,()->GovernedAssignment.withConstraints(offlineExecutor(events,failure),lease->c->{
      calls.incrementAndGet();openSyntheticPersistence(c);privateDemand();return null;
    },()->{acquired.incrementAndGet();return null;}));
    assertEquals(0,acquired.get());assertEquals(1,calls.get());
    assertNull(org.cibseven.bpm.engine.impl.context.Context.getCommandContext());
  }
  @Test void unqualifiedMissingQ2RefusesAfterOneReadOnlyPass() {
    var calls=new AtomicInteger();var acquired=new AtomicInteger();
    assertThrows(Rejected.class,()->GovernedAssignment.withConstraints(offlineExecutor(new ArrayList<>(),""),lease->c->{
      calls.incrementAndGet();openSyntheticPersistence(c);privateDemand();return null;
    },()->{acquired.incrementAndGet();return null;}));
    assertEquals(1,calls.get());assertEquals(1,acquired.get());
  }
  @Test void secondDemandDoesNotRetryAndOtherErrorsAreNotMatchedByCause() {
    var calls=new AtomicInteger();var acquired=new AtomicInteger();
    assertThrows(RuntimeException.class,()->GovernedAssignment.withConstraints(offlineExecutor(new ArrayList<>(),""),lease->c->{
      calls.incrementAndGet();openSyntheticPersistence(c);privateDemand();return null;
    },()->{acquired.incrementAndGet();return new PortalReadPlugin.AssignmentConstraintLease(null,null);}));
    assertEquals(2,calls.get());assertEquals(1,acquired.get());
    var expected=new IllegalStateException("unrelated");
    var actual=assertThrows(IllegalStateException.class,()->GovernedAssignment.withConstraints(offlineExecutor(new ArrayList<>(),""),lease->c->{
      openSyntheticPersistence(c);try{privateDemand();}catch(RuntimeException demand){expected.initCause(demand);throw expected;}return null;
    },()->{throw new AssertionError("cause-chain matching forbidden");}));
    assertSame(expected,actual);
  }
  @Test void actualCibMasksRollbackFailureBehindOriginalException() {
    var events=new ArrayList<String>();var first=new IllegalArgumentException("first");
    assertSame(first,assertThrows(IllegalArgumentException.class,()->offlineExecutor(events,"rollback").execute(c->{
      openSyntheticPersistence(c);throw first;
    })));
    assertEquals(List.of("rollback","close"),events);
    assertNull(org.cibseven.bpm.engine.impl.context.Context.getCommandContext());
  }
  @Test void nestedContextsCannotAcquireQ2UnderOuterLocks() {
    offlineExecutor(new ArrayList<>(),"").execute(c->{
      openSyntheticPersistence(c);
      assertThrows(Rejected.class,()->GovernedAssignment.withConstraints(offlineExecutor(new ArrayList<>(),""),lease->inner->{throw new AssertionError();},()->{throw new AssertionError();}));
      return null;
    });
  }
  @Test void managedTransactionCannotBeTreatedAsCompletedDemand() {
    var events=new ArrayList<String>();var calls=new AtomicInteger();
    assertThrows(Rejected.class,()->GovernedAssignment.withConstraints(offlineExecutor(events,"",true),lease->c->{
      calls.incrementAndGet();openSyntheticPersistence(c);privateDemand();return null;
    },()->{throw new AssertionError("managed outer transaction cannot admit Q2");}));
    assertEquals(1,calls.get());assertEquals(List.of("rollback","close"),events);
  }

}
