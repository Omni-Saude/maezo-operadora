package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static org.junit.jupiter.api.Assertions.*;
import java.lang.reflect.*;
import java.sql.*;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;

/** Focal semantic/JDBC boundary controls, not a mocked engine integration test. */
class StaffCaseListRepairTest {
  static final Instant LONG=Instant.parse("2026-09-10T12:00:30Z"),SHORT=LONG.minusSeconds(20);
  Map<String,Object> state(Instant first,Instant second){
    return record("operation","list","pins",List.of(
      StaffCaseReadCommand.pin("case_grant","grant-a","1","a".repeat(64),first),
      StaffCaseReadCommand.pin("case_grant","grant-b","1","b".repeat(64),second)),
      "cursor_template",record("after_ref","case_00000000001"));
  }
  @Test void laterGrantMinimumDoesNotBecomeFalseSemanticDrift(){
    var initial=state(LONG,SHORT);var recollected=state(SHORT,SHORT);
    assertNotEquals(hash(initial),hash(recollected));
    StaffCaseReadCommand.normalizePins(initial,SHORT);StaffCaseReadCommand.normalizePins(recollected,SHORT);
    assertEquals(hash(initial),hash(recollected));
    assertTrue(list(initial.get("pins")).stream().allMatch(p->time(map(p).get("valid_until")).equals(SHORT)));
  }
  @Test void laterQ2MinimumNormalizesAllPinsWithoutChangingRetainedBytes(){
    var initial=state(LONG,SHORT);Instant q2=SHORT.minusSeconds(3);
    StaffCaseReadCommand.normalizePins(initial,q2);byte[] frozen=Jcs.canonical(initial);
    var finalRead=state(q2,q2);StaffCaseReadCommand.normalizePins(finalRead,q2);
    assertArrayEquals(frozen,Jcs.canonical(finalRead));
    var changed=map(list(finalRead.get("pins")).get(0));changed.put("digest","c".repeat(64));
    assertFalse(Arrays.equals(frozen,Jcs.canonical(finalRead)));
  }
  @Test void summaryRequesterCanAuthenticateFinalizeThenAuthorizeRetainedListOnly(){
    var entry=record("role","read_requester","operations",List.of("detail","list"),"projections",List.of("staff_summary.v1"));
    StaffCaseInstallation.requireInitialReadCapabilities(entry,"finalize");
    StaffCaseModels.requireReadCapabilities(entry,"list");
    assertThrows(Rejected.class,()->StaffCaseModels.requireReadCapabilities(entry,"detail"));
    entry.put("operations",List.of("detail"));
    assertThrows(Rejected.class,()->StaffCaseModels.requireReadCapabilities(entry,"list"));
  }
  @Test void expiredActiveGenerationRemainsExactPredecessorWithoutReset(){
    var row=record("generation",7L,"checkpoint_digest","a".repeat(64),"active",true,
      "valid_until",Timestamp.from(Instant.EPOCH));
    StaffCaseStore.requireCheckpointSuccessor(row,8,"a".repeat(64));
    assertThrows(Rejected.class,()->StaffCaseStore.requireCheckpointSuccessor(row,8,null));
    assertThrows(Rejected.class,()->StaffCaseStore.requireCheckpointSuccessor(row,9,"a".repeat(64)));
    assertThrows(Rejected.class,()->StaffCaseStore.requireCheckpointSuccessor(null,8,"a".repeat(64)));
  }
  @Test void actualJdbcAcceptanceTypesHaveClosedCanonicalSemantics(){
    var row=record("tenant","t","environment","e","engine_name","n","database_incarnation","i",
      "checkpoint_ref","checkpoint","generation",7L,"checkpoint_digest","a".repeat(64),"publication_id","p",
      "source_ref","s","principal_identity_digest","b".repeat(64),"issuer","issuer","subject","subject",
      "principal_ref","principal","membership_revision",4L,"kind","authorization","active",true,
      "valid_until",Timestamp.from(SHORT),"canonical_checkpoint","{}");
    assertThrows(Rejected.class,()->copy(row));
    var accepted=StaffCaseReadCommand.acceptedCheckpoint(row);
    assertEquals("7",accepted.get("generation"));assertEquals("4",accepted.get("membership_revision"));
    assertEquals(time(SHORT),accepted.get("valid_until"));assertEquals("s",accepted.get("source_ref"));
    hash(accepted);row.put("generation",7.1);assertThrows(Rejected.class,()->StaffCaseReadCommand.acceptedCheckpoint(row));
  }
  @Test void threeChunksAreReadCompletelyAndResourcesClose(){
    var fixture=new JdbcRows(3);var rows=StaffCaseStore.readCheckpointChunks(fixture.connection(),"synthetic",new Object[]{"ref",4L},3,5);
    assertEquals(3,rows.size());assertEquals(4,fixture.maximum);assertEquals(5,fixture.timeout);
    assertTrue(fixture.resultClosed&&fixture.statementClosed);assertEquals(List.of("ref",4L),fixture.arguments);
  }
  @Test void extraOrMissingChunkCannotBecomeCompletePrefix(){
    for(int actual:List.of(2,4)){var fixture=new JdbcRows(actual);
      assertThrows(Rejected.class,()->StaffCaseStore.readCheckpointChunks(fixture.connection(),"synthetic",new Object[0],3,5));
      assertTrue(fixture.resultClosed&&fixture.statementClosed);}
  }
  @Test void schemaCursorIdentityIncludesOriginalWindow() throws Exception {
    try(var stream=getClass().getResourceAsStream("/staff-case-schema-postgres.sql")){
      String ddl=new String(Objects.requireNonNull(stream).readAllBytes(),java.nio.charset.StandardCharsets.UTF_8);
      String cursor=ddl.substring(ddl.indexOf("CREATE TABLE mzo_staff_case_cursor"),ddl.indexOf("CREATE TABLE mzo_staff_case_policy_version"));
      assertTrue(cursor.contains("checkpoint_digest,query_digest,after_ref,limit_,initial_valid_until)"));
    }
  }
  @Test void acquisitionAndFinalizeReuseExactCursorWithoutAnotherWrite(){
    var initial=state(LONG,SHORT);StaffCaseReadCommand.normalizePins(initial,SHORT);
    var saved=new HashMap<String,Map<String,Object>>();var writes=new int[]{0};
    java.util.function.Function<Map<String,Object>,Map<String,Object>> create=value->{
      writes[0]++;saved.put(str(value,"cursor_ref"),copy(value));return value;};
    String next=StaffCaseReadCommand.materializeCursor(create,saved::get,initial,SHORT,null);
    byte[] frozen=Jcs.canonical(saved.get(next));
    var finalRead=state(SHORT,SHORT);StaffCaseReadCommand.normalizePins(finalRead,SHORT);
    String finalized=StaffCaseReadCommand.materializeCursor(create,saved::get,finalRead,SHORT,record("next_cursor",next));
    assertEquals(next,finalized);assertEquals(1,writes[0]);assertArrayEquals(frozen,Jcs.canonical(saved.get(next)));
    var changed=map(list(finalRead.get("pins")).get(0));changed.put("digest","d".repeat(64));
    assertThrows(Rejected.class,()->StaffCaseReadCommand.materializeCursor(create,saved::get,finalRead,SHORT,record("next_cursor",next)));
  }
  @Test void renewedFirstPageGetsNewWindowWhileOriginalCursorStaysFrozen(){
    var first=state(SHORT,SHORT);StaffCaseReadCommand.normalizePins(first,SHORT);
    var saved=new HashMap<String,Map<String,Object>>();
    java.util.function.Function<Map<String,Object>,Map<String,Object>> create=value->{saved.put(str(value,"cursor_ref"),copy(value));return value;};
    String old=StaffCaseReadCommand.materializeCursor(create,saved::get,first,SHORT,null);
    byte[] frozen=Jcs.canonical(saved.get(old));
    var refresh=state(LONG,LONG);StaffCaseReadCommand.normalizePins(refresh,LONG);
    String fresh=StaffCaseReadCommand.materializeCursor(create,saved::get,refresh,LONG,null);
    assertNotEquals(old,fresh);assertArrayEquals(frozen,Jcs.canonical(saved.get(old)));
    assertEquals(time(LONG),saved.get(fresh).get("initial_valid_until"));
    assertThrows(Rejected.class,()->StaffCaseReadCommand.materializeCursor(create,saved::get,refresh,LONG,record("next_cursor",old)));
  }
  static final class JdbcRows {
    final int count;int index=-1,maximum,timeout;boolean resultClosed,statementClosed;
    final List<Object> arguments=new ArrayList<>();
    JdbcRows(int count){this.count=count;}
    @SuppressWarnings("unchecked") static <T>T proxy(Class<T> kind,InvocationHandler handler){return (T)Proxy.newProxyInstance(kind.getClassLoader(),new Class<?>[]{kind},handler);}
    Connection connection(){
      var metadata=proxy(ResultSetMetaData.class,(p,m,a)->switch(m.getName()){
        case "getColumnCount"->2;case "getColumnLabel"->((Integer)a[0])==1?"chunk_index":"canonical_chunk";default->throw new AssertionError(m.getName());});
      var result=proxy(ResultSet.class,(p,m,a)->switch(m.getName()){
        case "next"->++index<count;case "getMetaData"->metadata;
        case "getObject"->((Integer)a[0])==1?(Object)(long)index:"{}";
        case "close"->{resultClosed=true;yield null;}default->throw new AssertionError(m.getName());});
      var statement=proxy(PreparedStatement.class,(p,m,a)->switch(m.getName()){
        case "setQueryTimeout"->{timeout=(Integer)a[0];yield null;}
        case "setMaxRows"->{maximum=(Integer)a[0];yield null;}
        case "setFetchSize"->null;case "setObject"->{arguments.add(a[1]);yield null;}
        case "executeQuery"->result;case "close"->{statementClosed=true;yield null;}
        default->throw new AssertionError(m.getName());});
      return proxy(Connection.class,(p,m,a)->{if(m.getName().equals("prepareStatement"))return statement;throw new AssertionError(m.getName());});
    }
  }
}
