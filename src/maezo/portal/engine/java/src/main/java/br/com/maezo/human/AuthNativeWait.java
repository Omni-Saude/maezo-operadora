package br.com.maezo.human;

import java.time.Instant;
import java.util.*;

/** Exact persisted native wait proof. Never selects a first current message subscription. */
final class AuthNativeWait {
  private AuthNativeWait() {}
  static void require(AuthStore db,Map<String,Object> b,Instant now) {
    AuthModels.validate("binding",b);String instance=Jcs.ref(b,"process_instance_id"),execution=Jcs.ref(b,"subscription_execution_id");
    var subscription=db.one("SELECT * FROM ACT_RU_EVENT_SUBSCR WHERE ID_=? FOR UPDATE",Jcs.ref(b,"subscription_id"));
    if(!db.tenant.equals(subscription.get("tenant_id_"))||!instance.equals(subscription.get("proc_inst_id_"))
        ||!execution.equals(subscription.get("execution_id_"))||!"message".equals(subscription.get("event_type_"))
        ||!"msg.auth.docs_received".equals(subscription.get("event_name_"))||!"ICE_DocsRecebidos".equals(subscription.get("activity_id_"))
        ||((Number)subscription.get("rev_")).longValue()!=PortalReadModels.number(b.get("subscription_revision")))throw Rejected.conflict();
    var e=db.one("SELECT * FROM ACT_RU_EXECUTION WHERE ID_=? FOR UPDATE",execution);
    if(!db.tenant.equals(e.get("tenant_id_"))||!instance.equals(e.get("proc_inst_id_"))
        ||!Jcs.object(b.get("definition")).get("definition_id").equals(e.get("proc_def_id_"))
        ||((Number)e.get("rev_")).longValue()!=PortalReadModels.number(b.get("execution_revision"))
        ||((Number)e.get("suspension_state_")).intValue()!=1
        ||!(b.get("scope_execution_id").equals(execution)||b.get("scope_execution_id").equals(e.get("parent_id_"))))throw Rejected.conflict();
    var timer=db.one("SELECT * FROM ACT_RU_JOB WHERE ID_=? FOR UPDATE",Jcs.ref(b,"timer_job_id"));
    Instant due=((java.sql.Timestamp)timer.get("duedate_")).toInstant();
    if(!db.tenant.equals(timer.get("tenant_id_"))||!instance.equals(timer.get("process_instance_id_"))
        ||!"timer".equals(timer.get("type_"))||!"ICE_PrazoPendencia".equals(timer.get("activity_id_"))
        ||!b.get("scope_execution_id").equals(timer.get("execution_id_"))
        ||!due.equals(PortalReadModels.time(b.get("timer_deadline")))||!now.isBefore(due))throw Rejected.conflict();
  }
}
