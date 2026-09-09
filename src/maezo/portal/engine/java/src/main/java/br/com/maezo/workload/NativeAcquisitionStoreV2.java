package br.com.maezo.workload;

import java.nio.charset.StandardCharsets;
import java.util.*;

/** Discover whole set, then P/T/L. Functions own only technical native storage. */
final class NativeAcquisitionStoreV2 {
  final NativeAdmissionV2 admission;
  private Map<String,Map<String,Object>> tasks=Map.of();
  private Map<String,Map<String,Object>> acquisitions=Map.of();
  NativeAcquisitionStoreV2(NativeAdmissionV2 admission){this.admission=admission;}
  static List<String> ordered(Collection<String> values){
    return values.stream().distinct().sorted((a,b)->Arrays.compareUnsigned(a.getBytes(StandardCharsets.UTF_8),b.getBytes(StandardCharsets.UTF_8))).toList();
  }
  Map<String,Map<String,Object>> rows(Collection<String> ids){
    Map<String,Map<String,Object>> result=new LinkedHashMap<>();
    String encoded=new String(Json.bytes(ordered(ids)),StandardCharsets.UTF_8);
    for(String raw:NativeEnlistedWritesV2.rows(admission.session,"task_rows",encoded)){
      var row=Json.parse(raw.getBytes(StandardCharsets.UTF_8));String id=Json.token(row,"task");
      if(result.put(id,row)!=null)throw Refused.unavailable();
    }return result;
  }
  void lock(Collection<String> ids){lock(ids,List.of());}
  void lock(Collection<String> ids,Collection<String> additionalRoots){
    var wanted=ordered(ids);if(wanted.size()>admission.policy.transport.maxTasks+2)throw Refused.body();
    Map<String,Map<String,Object>> before=rows(wanted);if(!before.keySet().equals(new HashSet<>(wanted)))throw Refused.resource();
    List<String> rootIds=new ArrayList<>(additionalRoots);rootIds.addAll(before.values().stream().map(t->Json.token(t,"process")).toList());
    List<String> roots=ordered(rootIds);
    String tenant=admission.policy.transport.tenant;
    for(var t:before.values())if(!tenant.equals(t.get("tenant")))throw Refused.resource();
    var lockedRoots=NativeEnlistedWritesV2.rows(admission.session,"lock_roots",tenant,new String(Json.bytes(roots),StandardCharsets.UTF_8));
    if(!roots.equals(lockedRoots))throw Refused.resource();
    var lockedTasks=NativeEnlistedWritesV2.rows(admission.session,"lock_tasks",tenant,new String(Json.bytes(wanted),StandardCharsets.UTF_8));
    if(!wanted.equals(lockedTasks))throw Refused.resource();
    tasks=rows(wanted);
    for(String id:wanted){var a=before.get(id);var b=tasks.get(id);if(b==null)throw Refused.resource();
      for(String field:List.of("task","tenant","definition","process","execution","topic"))if(!Objects.equals(a.get(field),b.get(field)))throw Refused.resource();}
    var stored=admission.call("read_acquisitions_v2",Map.of("task_ids",wanted));Json.keys(stored,"acquisitions");
    Map<String,Map<String,Object>> map=new HashMap<>();
    for(Object item:Json.list(stored.get("acquisitions"))){var row=Json.object(item);String ref=NativeOutcomeV2.ref(row,"acquisition_ref");if(map.put(ref,row)!=null)throw Refused.unavailable();}
    acquisitions=Map.copyOf(map);
  }
  Map<String,Object> task(String id){var task=tasks.get(id);if(task==null)throw Refused.resource();return task;}
  Map<String,Object> consume(String id,Object reference,Map<String,Object> target,String worker,Map<String,Object> owner,String nativeUser,String fetchDigest){
    var consumed=NativeOutcomeV2.reference(reference);var row=acquisitions.get(consumed.get("acquisition_ref"));var task=task(id);
    if(row==null || !"live".equals(row.get("state")) || !consumed.get("lease_revision").equals(row.get("lease_revision"))
        || !id.equals(row.get("task_id")) || !owner.equals(row.get("owner_identity")) || !nativeUser.equals(row.get("native_user"))
        || !worker.equals(row.get("worker_id")) || !target.equals(row.get("target"))
        || !Objects.equals(task.get("process"),row.get("process_instance_id")) || !Objects.equals(task.get("execution"),row.get("execution_id"))
        || !admission.policy.admission.get("runtime_generation").equals(row.get("runtime_generation"))
        || !admission.policy.admission.get("decision_digest").equals(row.get("decision_digest"))
        || !admission.policy.admission.get("activation_ref").equals(row.get("activation_ref"))
        || (fetchDigest!=null && !fetchDigest.equals(row.get("fetch_capability_digest"))))throw Refused.resource();
    checkTask(task,target,worker,admission.now());
    if(!task.get("expiry").equals(row.get("lock_expires_at")))throw Refused.resource();return row;
  }
  static void checkTask(Map<String,Object> task,Map<String,Object> target,String worker,long now){
    if(!target.get("definition_id").equals(task.get("definition")) || !target.get("topic").equals(task.get("topic"))
        || !worker.equals(task.get("worker")) || (task.get("suspension")!=null && !Long.valueOf(1).equals(task.get("suspension")))
        || !(task.get("expiry") instanceof Long expiry) || expiry<=now)throw Refused.resource();
  }
  Map<String,Object> snapshot(String role,Map<String,Object> row){return Map.of("role",role,"task_ref",row.get("task_id"),"acquisition_ref",row.get("acquisition_ref"),"lease_revision",row.get("lease_revision"),"lock_expires_at",row.get("lock_expires_at"),"state",row.get("state"));}
}
