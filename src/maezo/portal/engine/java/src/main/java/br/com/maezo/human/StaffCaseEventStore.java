package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;

/** SC1 native case revision cut, enlisted with the real AUTH/task/occurrence effect.
 * No reader initializes this head. Existing cases require qualified retained-source
 * reconciliation; a missing head is never silently projected as revision zero.
 */
final class StaffCaseEventStore {
  final AuthStore db;
  StaffCaseEventStore(AuthStore db){this.db=db;}
  Object[] args(Object...extra){var base=new Object[]{db.scope.get("tenant"),db.scope.get("environment"),db.scope.get("engine_name"),db.scope.get("database_incarnation")};return StaffCaseStore.concat(base,extra);}
  Map<String,Object> read(String caseRef){
    var row=db.optional("SELECT * FROM mzo_staff_native_event_head WHERE "+StaffCaseStore.S+" AND case_ref=?",args(caseRef));
    if(row==null||!(row.get("case_revision") instanceof Long revision)||revision<1||!(row.get("observed_at") instanceof Timestamp))throw unavailable();
    return record("case_ref",row.get("case_ref"),"process_instance_id",row.get("process_instance_id"),
      "revision",Long.toString(revision),"observed_at",time(((Timestamp)row.get("observed_at")).toInstant()));
  }
  /** ROOT composes this exact call AFTER AuthStore.claimGuide in the same actual
   * start transaction. It records that admitted cut; it does not claim past history.
   */
  void initializeClaim(String caseRef){
    var claim=db.caseLink(caseRef);if(claim==null)throw unavailable();
    db.write("INSERT INTO mzo_staff_native_event_head(tenant,environment,engine_name,database_incarnation,case_ref,process_instance_id,case_revision,observed_at) VALUES(?,?,?,?,?,?,1,?)",args(caseRef,claim.get("instance_"),Timestamp.from(Instant.now())));
  }
  /** Root also calls this after each accepted native occurrence transition. */
  void advance(String instance){
    var claim=db.instanceLink(instance);if(claim==null)return; // A genuine AUTH claim has not yet been admitted.
    var row=db.optional("SELECT case_revision FROM mzo_staff_native_event_head WHERE "+StaffCaseStore.S+" AND case_ref=? FOR UPDATE",args(claim.get("case_")));
    if(row==null)throw unavailable();long prior=((Number)row.get("case_revision")).longValue();
    db.write("UPDATE mzo_staff_native_event_head SET case_revision=?,observed_at=? WHERE "+StaffCaseStore.S+" AND case_ref=? AND case_revision=?",StaffCaseStore.concat(new Object[]{Math.addExact(prior,1),Timestamp.from(Instant.now())},args(claim.get("case_"),prior)));
  }
}
