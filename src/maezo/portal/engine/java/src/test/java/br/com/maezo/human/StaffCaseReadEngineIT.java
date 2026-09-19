package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.*;

/** Actual CIB 2.1/PostgreSQL Q2 intersection, explicit CI/ROOT engine lane only.
 * Reuses deployed AUTH BPMN and actual native publications from the existing
 * PUBLIC_SYNTHETIC harness. This is not full staff installation/API acceptance.
 * No mocked engine and no skipped success when PostgreSQL configuration is absent.
 */
@Tag("integration")
class StaffCaseReadEngineIT {
  PortalReadEngineIT.Harness h;
  @BeforeEach void start() throws Exception {h=new PortalReadEngineIT.Harness();h.start();}
  @AfterEach void stop() throws Exception {if(h!=null)h.close();}
  StaffCaseReadCommand.Q2Lease lease(){
    var admission=h.trust.acquire("portal-task-read",h.scope);
    var scope=record("tenant",h.scope.get("tenant"),"environment",h.scope.get("environment"),
      "engine_name",h.trust.engine,"database_incarnation",h.trust.incarnation);
    return new StaffCaseReadCommand.Q2Lease(h.trust,admission,scope,h.anchor);
  }
  @Test void actualTaskIntersectionRetainsAuthorityAndOmitsUnqualifiedCreationTime() throws Exception {
    String id=h.task(false);h.resource(id);var lease=lease();
    var task=h.config.getCommandExecutorTxRequired().execute(context->{
      var q2=lease.enter(context,h.principal,lease.admission::requireCurrent);
      var projected=q2.task(id);assertNotNull(projected);assertFalse(projected.containsKey("created_at"));
      assertFalse(list(q2.semantic().get("observations")).isEmpty());q2.finalReads();return projected;
    });
    assertEquals(id,task.get("task_id"));
  }
  @Test void missingActualQ2PublicationCannotBecomeAnEmptyStaffTaskResult() throws Exception {
    String id=h.task(false);var lease=lease();
    Rejected denied=assertThrows(Rejected.class,()->h.config.getCommandExecutorTxRequired().execute(context->{
      var q2=lease.enter(context,h.principal,lease.admission::requireCurrent);return q2.task(id);
    }));
    assertEquals(503,denied.status);
  }
}
