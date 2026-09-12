package br.com.maezo.human;

import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** Enlisted deployment-owner installation pin; acquire BEFORE the tenant lock. */
final class AssignmentInstallation {
  private final AssignmentTrust trust;
  private final long until;
  private AssignmentInstallation(AssignmentTrust trust,long until){this.trust=trust;this.until=until;}
  static AssignmentInstallation acquire(CommandContext context,EngineStore db,AssignmentTrust trust){
    if(trust==null)throw new Rejected(503,"HUMAN_ENGINE_UNAVAILABLE");
    var rows=db.rows("SELECT i.*, (SELECT oid FROM pg_database WHERE datname=current_database()) AS actual_db_, (SELECT oid FROM pg_namespace WHERE nspname=current_schema()) AS actual_schema_, (SELECT oid FROM pg_roles WHERE rolname=session_user) AS actual_login_, has_table_privilege(session_user,'MZO_HUMAN_ASSIGNMENT_INSTALLATION','INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') AS mutable_ FROM MZO_HUMAN_ASSIGNMENT_INSTALLATION i WHERE TENANT_=? FOR SHARE",trust.tenant);
    if(rows.size()!=1)throw EngineStore.unavailable();var r=rows.get(0);var receipt=PortalReadModels.obj(trust.config,"deployment_receipt");
    if(!"qualified".equals(r.get("state_"))||!trust.environment.equals(r.get("environment_"))||!trust.engine.equals(r.get("engine_name_"))||!trust.engine.equals(context.getProcessEngineConfiguration().getProcessEngineName())||!trust.incarnation.equals(r.get("database_incarnation_"))||!trust.digest.equals(r.get("configuration_digest_"))||!receipt.get("artifact_ref").equals(r.get("deployment_receipt_ref_"))||!receipt.get("digest").equals(r.get("deployment_receipt_digest_"))||Boolean.TRUE.equals(r.get("mutable_")))throw Rejected.denied();
    for(String[] pair:List.of(new String[]{"database_oid_","actual_db_"},new String[]{"engine_schema_oid_","actual_schema_"},new String[]{"engine_login_oid_","actual_login_"}))if(((Number)r.get(pair[0])).longValue()!=((Number)r.get(pair[1])).longValue())throw Rejected.denied();
    var installed=new AssignmentInstallation(trust,((Number)r.get("valid_until_")).longValue());installed.current();return installed;
  }
  Instant deadline(){return Instant.ofEpochSecond(Math.min(until,PortalReadModels.number(trust.config.get("valid_until"))));}
  void current(){var now=Instant.now();trust.current(now);if(now.getEpochSecond()>=until)throw Rejected.conflict();}
}
