package br.com.maezo.human;

import java.time.Instant;
import java.util.*;
import java.util.function.Supplier;
import org.cibseven.bpm.engine.impl.bpmn.behavior.*;
import org.cibseven.bpm.engine.impl.bpmn.parser.AbstractBpmnParseListener;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.cibseven.bpm.engine.impl.persistence.entity.*;
import org.cibseven.bpm.engine.impl.pvm.delegate.*;
import org.cibseven.bpm.engine.impl.pvm.process.*;
import org.cibseven.bpm.engine.impl.util.xml.Element;

/** Installed after ConsumerTaskListener; preserves original native behaviors and stable BPMN IDs. */
final class AuthDocumentLifecycleListener extends AbstractBpmnParseListener {
  private final Supplier<AuthRuntime> runtime;
  AuthDocumentLifecycleListener(Supplier<AuthRuntime> runtime){this.runtime=runtime;}
  @Override public void parseServiceTask(Element element,ScopeImpl scope,ActivityImpl activity) {
    if(!Set.of("ST_SolicitarDocumentos","ST_PublishAuthPended").contains(activity.getId()))return;
    if(!(activity.getActivityBehavior() instanceof ExternalTaskActivityBehavior original))throw Rejected.denied();
    activity.setActivityBehavior(new External(original));
  }
  @Override public void parseEventBasedGateway(Element element,ScopeImpl scope,ActivityImpl activity) {
    if(!activity.getId().equals("GW_AguardarDocs"))return;
    if(!(activity.getActivityBehavior() instanceof EventBasedGatewayActivityBehavior original))throw Rejected.denied();
    activity.setActivityBehavior(new Gateway(original));
  }
  @Override public void parseIntermediateTimerEventDefinition(Element element,ActivityImpl activity) {
    if(!activity.getId().equals("ICE_PrazoPendencia"))return;
    if(!(activity.getActivityBehavior() instanceof IntermediateCatchEventActivityBehavior original))throw Rejected.denied();
    activity.setActivityBehavior(new Timer(original));
  }
  private static ExecutionEntity entity(ActivityExecution value){if(!(value instanceof ExecutionEntity e))throw Rejected.denied();return e;}
  private static void atClose(Runnable action) {
    Context.getCommandContext().registerCommandContextListener(new CommandContextListener(){
      @Override public void onCommandContextClose(CommandContext context){action.run();}
      @Override public void onCommandFailed(CommandContext context,Throwable failure){}
    });
  }
  private AuthStore installed(ExecutionEntity execution) {
    var selected=runtime.get();return selected==null?null:selected.lifecycle(execution);
  }
  private void finalCurrent(AuthStore db) {
    Context.getCommandContext().getTransactionContext().addTransactionListener(TransactionState.COMMITTING,
      ignored->runtime.get().requireQualifiedCode(AuthInstallation.runtime(db)));
  }
  private final class External extends ExternalTaskActivityBehavior {
    private final ExternalTaskActivityBehavior original;
    External(ExternalTaskActivityBehavior original){super(null,original.getPriorityValueProvider());this.original=original;}
    @Override public void execute(ActivityExecution value)throws Exception {
      var execution=entity(value);var before=new HashSet<String>();for(var task:execution.getExternalTasks())before.add(task.getId());
      original.execute(value);var tasks=execution.getExternalTasks().stream().filter(t->!before.contains(t.getId())).toList();
      if(tasks.size()!=1)throw Rejected.denied();var task=tasks.get(0);
      // At close the strict start has created its permanent guide/instance link. No task can be
      // fetched by another transaction before this callback and the normal native commit.
      atClose(()->created(execution,task));
    }
    @Override public void signal(ActivityExecution value,String name,Object payload)throws Exception{original.signal(value,name,payload);}
    @Override public void migrateScope(ActivityExecution value){if(installed(entity(value))!=null)throw Rejected.denied();original.migrateScope(value);}
    @Override public void onParseMigratingInstance(org.cibseven.bpm.engine.impl.migration.instance.parser.MigratingInstanceParseContext context,org.cibseven.bpm.engine.impl.migration.instance.MigratingActivityInstance activity){original.onParseMigratingInstance(context,activity);}
  }
  private void created(ExecutionEntity execution,ExternalTaskEntity task) {
    var db=installed(execution);if(db==null)return;
    var link=db.instanceLink(task.getProcessInstanceId());if(link==null)throw Rejected.denied();
    if(!execution.getId().equals(task.getExecutionId())||!execution.getProcessDefinitionId().equals(task.getProcessDefinitionId()))throw Rejected.denied();
    var head=db.instanceHead(task.getProcessInstanceId());Object current=head.get("current_request_");
    if(task.getActivityId().equals("ST_SolicitarDocumentos")) {
      if(db.producerOccurrence(task.getProcessInstanceId(),task.getId())!=null)return;
      String request=UUID.randomUUID().toString();
      if(current!=null) {
        var prior=db.occurrence((String)current);var record=new TreeMap<>(AuthStore.parse(prior.get("record_")));
        if(Set.of("created","awaiting_publication_worker","bound").contains(record.get("state"))) {
          record.put("state","replaced");record.put("successor_ref",request);db.updateOccurrence(record,((Number)prior.get("rev_")).longValue());
        }
      }
      var occurrence=PortalReadModels.record("request_ref",request,"scope",db.scope,"case_ref",link.get("case_"),
        "process_instance_id",task.getProcessInstanceId(),"definition",AuthStore.parse(link.get("definition_")),
        "generation",Long.toString(AuthStore.next(((Number)head.get("generation_")).longValue())),"request_revision","0","binding_revision","0",
        "creator_execution_id",execution.getId(),"producer_external_task_id",task.getId(),"publication_external_task_id",null,
        "state","created","created_at",PortalReadModels.time(Instant.now()),"policy_ref",null,"policy_digest",null,"binding",null,
        "successor_ref",null,"terminal_command_id",null);
      AuthModels.validate("occurrence",occurrence);db.openOccurrence(occurrence,((Number)head.get("rev_")).longValue());
    } else {
      if(current==null)throw Rejected.denied();var row=db.occurrence((String)current);var record=new TreeMap<>(AuthStore.parse(row.get("record_")));
      if(!record.get("state").equals("created")||!record.get("creator_execution_id").equals(execution.getId()))throw Rejected.conflict();
      var cache=Context.getCommandContext().getDbEntityManager().getDbEntityCache();
      var producer=cache.get(ExternalTaskEntity.class,Jcs.ref(record,"producer_external_task_id"));
      if(producer==null||!cache.isDeleted(producer)||!producer.getActivityId().equals("ST_SolicitarDocumentos"))throw Rejected.denied();
      record.put("publication_external_task_id",task.getId());record.put("state","awaiting_publication_worker");
      AuthModels.validate("occurrence",record);db.updateOccurrence(record,((Number)row.get("rev_")).longValue());
    }
    finalCurrent(db);
  }
  private final class Gateway extends EventBasedGatewayActivityBehavior {
    private final EventBasedGatewayActivityBehavior original;
    Gateway(EventBasedGatewayActivityBehavior original){this.original=original;}
    @Override public void execute(ActivityExecution value)throws Exception {
      var execution=entity(value);var db=installed(execution);
      String request=null,publication=null;
      if(db!=null) {
        var head=db.instanceHead(execution.getProcessInstanceId());request=(String)head.get("current_request_");
        if(request==null)throw Rejected.denied();var record=AuthStore.parse(db.occurrence(request).get("record_"));
        if(!record.get("state").equals("awaiting_publication_worker")||!execution.getId().equals(record.get("creator_execution_id")))throw Rejected.conflict();
        publication=Jcs.ref(record,"publication_external_task_id");
      }
      original.execute(value);
      if(db!=null){String fixedRequest=request,fixedPublication=publication;atClose(()->bind(db,execution,fixedRequest,fixedPublication));}
    }
  }
  private void bind(AuthStore db,ExecutionEntity scope,String request,String publication) {
    var context=Context.getCommandContext();var cache=context.getDbEntityManager().getDbEntityCache();
    var completed=cache.get(ExternalTaskEntity.class,publication);
    if(completed==null||!cache.isDeleted(completed)||!completed.getActivityId().equals("ST_PublishAuthPended")
        ||!scope.getId().equals(completed.getExecutionId()))throw Rejected.denied();
    var subscriptions=cache.getEntitiesByType(EventSubscriptionEntity.class).stream().filter(e->!cache.isDeleted(e)
      &&scope.getProcessInstanceId().equals(e.getProcessInstanceId())&&"ICE_DocsRecebidos".equals(e.getActivityId())
      &&"message".equals(e.getEventType())&&"msg.auth.docs_received".equals(e.getEventName())
      &&(scope.getId().equals(e.getExecutionId())||scope.getId().equals(e.getExecution().getParentId()))).toList();
    var timers=cache.getEntitiesByType(TimerEntity.class).stream().filter(j->!cache.isDeleted(j)
      &&scope.getProcessInstanceId().equals(j.getProcessInstanceId())&&scope.getId().equals(j.getExecutionId())
      &&"ICE_PrazoPendencia".equals(j.getActivityId())).toList();
    if(subscriptions.size()!=1||timers.size()!=1)throw Rejected.denied();
    var subscription=subscriptions.get(0);var timer=timers.get(0);
    if(!cache.isTransient(subscription)||!cache.isTransient(timer)||timer.getDuedate()==null)throw Rejected.denied();
    // IDs come from exact native creation/cache. Persisted revisions are captured after the one
    // normal CIB flush, never guessed as getRevisionNext for a newly inserted entity.
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
      var nativeSub=db.one("SELECT REV_ FROM ACT_RU_EVENT_SUBSCR WHERE ID_=?",subscription.getId());
      var nativeExecution=db.one("SELECT REV_ FROM ACT_RU_EXECUTION WHERE ID_=?",subscription.getExecutionId());
      var row=db.occurrence(request);var occurrence=new TreeMap<>(AuthStore.parse(row.get("record_")));
      if(!occurrence.get("state").equals("awaiting_publication_worker")||!publication.equals(occurrence.get("publication_external_task_id")))throw Rejected.conflict();
      var binding=PortalReadModels.record("request_ref",request,"generation",occurrence.get("generation"),"request_revision",occurrence.get("request_revision"),
        "binding_revision",Long.toString(AuthStore.next(PortalReadModels.number(occurrence.get("binding_revision")))),
        "process_instance_id",scope.getProcessInstanceId(),"definition",occurrence.get("definition"),"scope_execution_id",scope.getId(),
        "subscription_id",subscription.getId(),"subscription_revision",nativeSub.get("rev_").toString(),
        "subscription_execution_id",subscription.getExecutionId(),"execution_revision",nativeExecution.get("rev_").toString(),
        "timer_job_id",timer.getId(),"timer_deadline",PortalReadModels.time(timer.getDuedate().toInstant()));
      AuthModels.validate("binding",binding);AuthNativeWait.require(db,binding,Instant.now());
      occurrence.put("binding",binding);occurrence.put("binding_revision",binding.get("binding_revision"));occurrence.put("state","bound");
      db.updateOccurrence(occurrence,((Number)row.get("rev_")).longValue());
      runtime.get().requireQualifiedCode(AuthInstallation.runtime(db));
      if(!Instant.now().isBefore(timer.getDuedate().toInstant()))throw Rejected.denied();
    });
  }
  private final class Timer extends IntermediateCatchEventActivityBehavior {
    private final IntermediateCatchEventActivityBehavior original;
    Timer(IntermediateCatchEventActivityBehavior original){super(original.isAfterEventBasedGateway());this.original=original;}
    @Override public void execute(ActivityExecution value)throws Exception{original.execute(value);}
    @Override public void signal(ActivityExecution value,String name,Object payload)throws Exception {
      var execution=entity(value);var db=installed(execution);
      if(db!=null) {
        var head=db.instanceHead(execution.getProcessInstanceId());if(head.get("current_request_")==null)throw Rejected.denied();
        var row=db.occurrence((String)head.get("current_request_"));var record=new TreeMap<>(AuthStore.parse(row.get("record_")));
        var binding=Jcs.object(record.get("binding"));
        if(!record.get("state").equals("bound")||!execution.getId().equals(binding.get("scope_execution_id")))throw Rejected.conflict();
        var actualJob=Context.getCommandContext().getCurrentJob();
        if(actualJob==null||!binding.get("timer_job_id").equals(actualJob.getId())
            ||!"ICE_PrazoPendencia".equals(actualJob.getActivityId())
            ||Instant.now().isBefore(PortalReadModels.time(binding.get("timer_deadline"))))throw Rejected.denied();
        record.put("state","expired");db.updateOccurrence(record,((Number)row.get("rev_")).longValue());finalCurrent(db);
      }
      original.signal(value,name,payload);
    }
  }
}
