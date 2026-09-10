package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static org.junit.jupiter.api.Assertions.*;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;

/** Focal codec/result controls; not a mock engine integration test. */
class StaffCaseReadTest {
  @Test void currentReadCredentialMustCoverExactEndpointProjections(){
    var entry=record("role","read_requester","operations",List.of("detail"),"projections",new ArrayList<>(StaffCaseModels.FIELDS.keySet()));
    StaffCaseModels.requireReadCapabilities(entry);
    entry.put("projections",List.of("staff_summary.v1","staff_identity.v1"));
    assertThrows(Rejected.class,()->StaffCaseModels.requireReadCapabilities(entry));
  }
  @Test void listCredentialRequiresExplicitSummaryAndListCapability(){
    var entry=record("role","read_requester","operations",List.of("detail","list"),"projections",List.of("staff_summary.v1"));
    StaffCaseModels.requireReadCapabilities(entry,"list");entry.put("operations",List.of("detail"));
    assertThrows(Rejected.class,()->StaffCaseModels.requireReadCapabilities(entry,"list"));
  }
  @Test void actualTaskResourceAndRevisionChangeCreationPin(){
    var scope=record("tenant","tenant","environment","test","engine_name","engine","database_incarnation","inc");
    var resource=record("scope",scope,"case_ref","case_00000000001","process_instance_id","instance","task_id","task","task_definition_key","UT_Task");
    var value=record("resource",resource,"task_revision","9","created_at","2026-09-10T12:00:00.000000Z");
    StaffCaseModels.shape("task_created",value);
    var other=copy(value);other.put("task_revision","10");assertNotEquals(hash(value),hash(other));
    other=copy(value);var changed=copy(resource);changed.put("task_id","other");other.put("resource",changed);assertNotEquals(hash(value),hash(other));
    other=copy(value);other.put("task_revision",9L);var invalid=other;assertThrows(Rejected.class,()->StaffCaseModels.shape("task_created",invalid));
  }
  @Test void resultNeverEscapesBeforeCommitOrAfterOriginalGuardExpires(){
    var result=new StaffCaseReadCommand.Result();result.frozen=new byte[]{1};result.current=()->{};
    assertThrows(Rejected.class,result::bytes);result.committed=true;assertArrayEquals(new byte[]{1},result.bytes());
    result.current=()->{throw unavailable();};assertThrows(Rejected.class,result::bytes);
  }
  @Test void exactDetailPinsUseStringRevisions(){
    var until=Instant.parse("2026-09-10T12:00:10Z");
    var pin=StaffCaseReadCommand.pin("native_task_created_at","task","9","a".repeat(64),until);
    assertEquals("9",pin.get("revision"));assertThrows(Rejected.class,()->StaffCaseReadCommand.pin("native_task_created_at","task",9L,"a".repeat(64),until));
  }
  @Test void noArbitraryDetailQueryOrLimit(){
    var query=record("case_ref","case_00000000001","task_limit","100","task_cursor",null);
    StaffCaseModels.shape("detail",query);query.put("task_limit","101");assertThrows(Rejected.class,()->StaffCaseModels.shape("detail",query));
    query.put("task_limit","25");query.put("tenant","another");assertThrows(Rejected.class,()->StaffCaseModels.shape("detail",query));
  }
  @Test void listQueryAndOpaqueCursorAreClosedAndNumberFree(){
    var query=record("kind","authorization","limit","100","cursor",null);StaffCaseModels.shape("list",query);
    query.put("limit","101");assertThrows(Rejected.class,()->StaffCaseModels.shape("list",query));
    query.put("limit","25");query.put("search","anything");assertThrows(Rejected.class,()->StaffCaseModels.shape("list",query));
    var scope=record("tenant","tenant","environment","test","engine_name","engine","database_incarnation","inc");
    var pin=StaffCaseReadCommand.pin("checkpoint","checkpoint","4","a".repeat(64),Instant.parse("2026-09-10T12:00:10Z"));
    var cursor=record("cursor_ref","opaque","scope",scope,"principal_identity_digest","b".repeat(64),"membership_revision","7",
      "session_ref","session","operation","list","query_digest","c".repeat(64),"kind","authorization","checkpoint_ref","checkpoint",
      "generation","4","checkpoint_digest","d".repeat(64),"case_ref",null,"native_revision",null,"after_ref","case_00000000001",
      "limit","25","source_pins",List.of(pin),"initial_valid_until","2026-09-10T12:00:10.000000Z");
    StaffCaseModels.shape("cursor",cursor);cursor.put("membership_revision",7L);assertThrows(Rejected.class,()->StaffCaseModels.shape("cursor",cursor));
  }
  @Test void exactIdentityReconciliationPreservesCanonicalUpstreamWithoutChangingInstance(){
    var id=record("upstream_resource_key","guide","case_ref","case_00000000001","process_instance_ref","instance",
      "process_definition_id","definition","process_definition_key","SP-OP-AUTH-001","process_definition_version","3",
      "process_definition_digest","a".repeat(64),"kind","authorization");
    var external=copy(id);external.put("upstream_resource_key","existing-canonical");
    assertEquals(external,NativeCaseIdentityReader.reconcile(id,external));external.put("process_instance_ref","other");
    assertThrows(Rejected.class,()->NativeCaseIdentityReader.reconcile(id,external));
  }
}
