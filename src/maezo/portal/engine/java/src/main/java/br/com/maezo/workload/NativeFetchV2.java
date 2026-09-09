package br.com.maezo.workload;

import java.util.*;
import org.cibseven.bpm.engine.externaltask.LockedExternalTask;
import org.cibseven.bpm.engine.impl.externaltask.LockedExternalTaskImpl;
import org.cibseven.bpm.engine.impl.persistence.entity.ExternalTaskEntity;

/** Native per-task lock, no bulk-fetch optimistic listener and no postcommit shrinking. */
final class NativeFetchV2 {
  final NativeAdmissionV2 admission;
  final CapabilityV2 cap;
  final Map<String,Object> parameters;
  final List<String> ids;
  final Map<String,LockedExternalTask> projections=new LinkedHashMap<>();
  final Map<String,String> refs=new LinkedHashMap<>();
  NativeFetchV2(NativeAdmissionV2 admission,CapabilityV2 cap,Map<String,Object> parameters){
    this.admission=admission;this.cap=cap;this.parameters=parameters;
    long count=Json.number(parameters,"maxTasks"),duration=Json.number(parameters,"lockDuration"),poll=Json.number(parameters,"asyncResponseTimeout");
    if(count>admission.policy.transport.maxTasks || duration>admission.policy.transport.maxLockMillis || poll>admission.policy.transport.maxPollMillis)throw Refused.body();
    ids=NativeEnlistedWritesV2.rows(admission.session,"candidates",admission.policy.transport.tenant,cap.target.get("definition_id"),cap.target.get("topic"),(int)count);
  }
  static boolean eligible(Map<String,Object> task,long now){
    return (task.get("expiry")==null || task.get("expiry") instanceof Long n && n<=now)
        && (task.get("suspension")==null || Long.valueOf(1).equals(task.get("suspension")))
        && (task.get("retries")==null || task.get("retries") instanceof Long n && n>0);
  }
  void acquire(NativeAcquisitionStoreV2 store){
    @SuppressWarnings("unchecked") List<String> projection=(List<String>)Json.list(parameters.get("variables"));
    for(String id:ids){
      var row=store.task(id);
      if(!eligible(row,admission.now()) || !cap.target.get("definition_id").equals(row.get("definition"))
          || !cap.target.get("topic").equals(row.get("topic")))throw Refused.resource();
      admission.current();admission.engine.getExternalTaskService().lock(id,cap.worker,Json.number(parameters,"lockDuration"));
      ExternalTaskEntity entity=admission.context.getExternalTaskManager().findExternalTaskById(id);
      if(entity==null)throw Refused.resource();
      // Native serializer: deserializeVariables=false, localVariables=false, includeExtensionProperties=false.
      projections.put(id,LockedExternalTaskImpl.fromEntity(entity,projection,false,false,false));refs.put(id,NativeOutcomeV2.newRef());
    }
  }
  Map<String,Object> finalizeTask(String id,Map<String,Object> command,Map<String,Object> actual){
    NativeAcquisitionStoreV2.checkTask(actual,cap.target,cap.worker,admission.now());
    Map<String,Object> row=new HashMap<>();row.put("acquisition_ref",refs.get(id));row.put("task_id",id);row.put("owner_identity",cap.identity);
    row.put("native_user",admission.peer.engineUser());row.put("worker_id",cap.worker);row.put("target",cap.target);row.put("process_instance_id",actual.get("process"));row.put("execution_id",actual.get("execution"));
    row.put("activation_ref",command.get("activation_ref"));row.put("runtime_generation",admission.policy.admission.get("runtime_generation"));row.put("decision_digest",admission.policy.admission.get("decision_digest"));
    row.put("fetch_capability_digest",cap.digest);row.put("command_id",command.get("command_id"));row.put("request_digest",command.get("request_digest"));row.put("lease_revision",1L);row.put("lock_expires_at",actual.get("expiry"));row.put("state","live");
    return admission.call("insert_acquisition_v2",Map.of("acquisition",row));
  }
  Object value(Map<String,Map<String,Object>> actual){
    List<Object> result=new ArrayList<>();
    for(String id:ids){
      LockedExternalTask task=projections.get(id);var row=actual.get(id);
      if(task==null || row==null)throw Refused.unavailable();Map<String,Object> values=new HashMap<>();
      for(Object name:Json.list(parameters.get("variables")))if(task.getVariables().containsKey(name))values.put((String)name,WorkloadCommand.wire(task.getVariables().getValueTyped((String)name)));
      Map<String,Object> output=new HashMap<>();output.put("id",id);output.put("definition_id",row.get("definition"));output.put("process_instance_id",row.get("process"));output.put("execution_id",row.get("execution"));output.put("topic",row.get("topic"));output.put("worker_id",row.get("worker"));output.put("lock_expires_at",row.get("expiry"));output.put("variables",values);output.put("retries",row.get("retries"));result.add(output);
    }return result;
  }
}
