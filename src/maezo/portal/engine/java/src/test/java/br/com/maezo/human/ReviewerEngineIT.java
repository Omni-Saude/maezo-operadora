package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.sql.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import org.cibseven.bpm.engine.task.Task;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** ROOT-only real PG/CIB probes. Select these two methods explicitly; inherited fixture only. */
@Tag("integration")
class ReviewerEngineIT extends AtomicEngineIT {
  @ParameterizedTest @ValueSource(strings={"claim","release","decision"})
  void finalCommitFailureRollsBackReceiptTaskRevisionAndVariables(String operation) throws Exception {
    Task t=operation.equals("claim")?task():claimed();
    var command=command(t,operation,"eir-final-commit");
    long before=countReceipts(); String assignee=t.getAssignee();
    try(var c=connection();var s=c.createStatement()) {
      s.execute("CREATE FUNCTION eir_reject_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.COMMAND_='eir-final-commit' THEN RAISE EXCEPTION 'eir-deferred-receipt'; END IF; RETURN NEW; END $$");
      s.execute("CREATE CONSTRAINT TRIGGER eir_commit_fault AFTER INSERT ON MZO_HUMAN_RECEIPT DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION eir_reject_commit()");
    }
    try {
      RuntimeException error=assertThrows(RuntimeException.class,()->send(command));
      StringBuilder causes=new StringBuilder();
      for(Throwable e=error;e!=null;e=e.getCause()) causes.append(e.getMessage());
      assertTrue(causes.toString().contains("eir-deferred-receipt"),"Must reach deferred final-commit fault, not fail at an earlier unrelated SQL statement");
      assertEquals(before,countReceipts());
      Task after=engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult();
      assertNotNull(after); assertEquals(assignee,after.getAssignee());
      assertEquals(command.get("task_revision"),command(after,operation,"readback").get("task_revision"));
      for(String v:List.of("synthetic_acknowledged","synthetic_human_ref","synthetic_workload_ref","synthetic_command_ref"))
        assertNull(engine.getRuntimeService().getVariable(t.getProcessInstanceId(),v));
    } finally {
      try(var c=connection();var s=c.createStatement()) {
        s.execute("DROP TRIGGER eir_commit_fault ON MZO_HUMAN_RECEIPT");s.execute("DROP FUNCTION eir_reject_commit()");
      }
    }
    byte[] result=send(command);assertEquals("committed",Jcs.object(Jcs.parse(result)).get("status"));
    assertArrayEquals(result,send(command));assertEquals(before+1,countReceipts());
    if(operation.equals("decision")) {
      assertNull(engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult());
      assertEquals(true,engine.getRuntimeService().getVariable(t.getProcessInstanceId(),"synthetic_acknowledged"));
    }
  }

  @ParameterizedTest @ValueSource(strings={"envelope","principal","evidence"})
  void timeBoundAuthorityCannotExpireWhileLaterEvidenceReadIsBlocked(String kind) throws Exception {
    Task task=task();long deadline=Instant.now().getEpochSecond()+4;
    if(kind.equals("principal")) {
      var p=authority("principal");p.putAll(Map.of("principal_ref","human-test","issuer","https://issuer.example.test/",
        "subject","subject-human-test","active",true,"valid_until",Long.toString(deadline),"groups",List.of("synthetic-reviewers")));publish(p);
    } else if(kind.equals("evidence")) {
      var e=authority("evidence");e.putAll(Map.of("task_id",task.getId(),"process_definition_id",processId,
        "evidence_ref","snapshot-test","evidence_digest","a".repeat(64),"valid_until",Long.toString(deadline)));publish(e);
    }
    var command=command(task,"claim","eir-expiry-"+kind);
    long now=Instant.now().getEpochSecond();
    byte[] raw=keys.sign(command,"human-command",now,kind.equals("envelope")?deadline:now+60);
    var pool=Executors.newSingleThreadExecutor();
    try(var blocker=connection()) {
      blocker.setAutoCommit(false);int blockerPid;
      try(var s=blocker.createStatement();var r=s.executeQuery("SELECT pg_backend_pid()")){r.next();blockerPid=r.getInt(1);}
      try(var s=blocker.createStatement()){s.execute("LOCK TABLE MZO_HUMAN_EVIDENCE IN ACCESS EXCLUSIVE MODE");}
      var pending=pool.submit(()->plugin.execute(raw,keys.peer,"human-command"));
      boolean observed=false;long observeLimit=System.nanoTime()+TimeUnit.SECONDS.toNanos(2);
      while(System.nanoTime()<observeLimit && !observed) {
        try(var c=connection();var s=c.prepareStatement("SELECT COUNT(*) FROM pg_stat_activity WHERE ? = ANY(pg_blocking_pids(pid)) AND query LIKE 'SELECT * FROM MZO_HUMAN_EVIDENCE%'");) {
          s.setInt(1,blockerPid);try(var r=s.executeQuery()){r.next();observed=r.getLong(1)>0;}
        }
        if(!observed)Thread.sleep(10);
      }
      assertTrue(observed,"Observe actual evidence SQL blocked after command validation; no mock or presumed scheduling");
      while(Instant.now().getEpochSecond()<=deadline)Thread.sleep(20);
      blocker.commit();
      ExecutionException failure=assertThrows(ExecutionException.class,()->pending.get(10,TimeUnit.SECONDS));
      assertInstanceOf(Rejected.class,failure.getCause(),"Expired authority must reject before mutation, not hit an unrelated persistence failure");
      assertEquals(0,countReceipts());
      var unchanged=engine.getTaskService().createTaskQuery().taskId(task.getId()).singleResult();
      assertNotNull(unchanged);assertNull(unchanged.getAssignee());
      assertEquals(command.get("task_revision"),command(unchanged,"claim","readback").get("task_revision"));
    } finally {pool.shutdownNow();assertTrue(pool.awaitTermination(15,TimeUnit.SECONDS));}
  }
}
