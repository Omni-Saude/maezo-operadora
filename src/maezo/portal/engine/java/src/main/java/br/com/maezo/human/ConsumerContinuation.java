package br.com.maezo.human;

import java.util.*;
import org.cibseven.bpm.engine.impl.bpmn.behavior.ExclusiveGatewayActivityBehavior;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.cibseven.bpm.engine.impl.persistence.entity.ExecutionEntity;
import org.cibseven.bpm.engine.impl.pvm.process.*;

/** ADR0049D5: proof of one native synchronous continuation, never an authority or parent HEAD. */
final class ConsumerContinuation implements Session, CommandContextListener {
  enum Phase { SEEDED, ENDED, TAKEN, STARTED, CONSUMED, LINKED, CLOSED }
  private CommandContext owner;
  private ExecutionEntity original, token, consumer, instance;
  private ProcessDefinitionImpl definition;
  private String tenant;
  private ActivityImpl source, destination;
  private List<TransitionImpl> path;
  private Map<String,Object> pointer;
  private Phase phase=Phase.SEEDED;
  private int next;
  private boolean destroysScope;

  private ConsumerContinuation(CommandContext owner,ExecutionEntity original,
      Map<String,Object> pointer,ActivityImpl destination,List<TransitionImpl> path) {
    this.owner=owner;this.original=original;this.source=original.getActivity();
    this.definition=original.getProcessDefinition();this.instance=original.getProcessInstance();this.tenant=original.getTenantId();
    this.pointer=Map.copyOf(pointer);this.destination=destination;this.path=List.copyOf(path);
  }

  static void seed(CommandContext context,ExecutionEntity original,Map<String,Object> pointer,
      Map<String,Object> target,List<String> selectedIds) {
    need(context==Context.getCommandContext() && !context.getSessions().containsKey(ConsumerContinuation.class));
    var edges=((List<?>)target.get("edges")).stream().map(Jcs::object)
        .filter(e->pointer.get("outcome").equals(e.get("outcome"))).toList();
    // A nonadverse decision has no classified consumer to create.
    if(edges.isEmpty()){need(selectedIds.isEmpty());return;}
    need(edges.size()==1 && original.getId().equals(pointer.get("execution_id"))
        && original.getProcessDefinitionId().equals(pointer.get("process_definition_id"))
        && original.getProcessInstanceId().equals(pointer.get("process_instance_id"))
        && original.getActivityId().equals(pointer.get("original_task_key")));
    ActivityImpl destination=original.getProcessDefinition().findActivity(Jcs.ref(edges.get(0),"activity_id"));
    need(destination!=null);
    need(!selectedIds.isEmpty() && selectedIds.size()<=32);
    var path=new ArrayList<TransitionImpl>();var seen=new HashSet<ActivityImpl>();
    ActivityImpl cursor=original.getActivity();
    for(String id:selectedIds) {
      need(seen.add(cursor) && (cursor==original.getActivity()
          || cursor.getActivityBehavior() instanceof ExclusiveGatewayActivityBehavior));
      var matches=cursor.getOutgoingTransitions().stream().filter(t->id.equals(t.getId())).toList();
      need(matches.size()==1);var flow=matches.get(0);path.add(flow);cursor=(ActivityImpl)flow.getDestination();
    }
    need(cursor==destination);
    var witness=new ConsumerContinuation(context,original,pointer,destination,path);
    // Direct map insertion intentionally has no resource/sessionList lifecycle: explicit hooks below
    // validate BEFORE native flush/commit and erase references on close AND failure.
    context.getSessions().put(ConsumerContinuation.class,witness);
    context.registerCommandContextListener(witness);
  }

  private static ConsumerContinuation current() {
    var context=Context.getCommandContext();need(context!=null);
    var value=(ConsumerContinuation)context.getSessions().get(ConsumerContinuation.class);
    // Closed scopes remain tombstones for seed(), but unrelated late native observations are inert.
    if(value!=null && value.phase==Phase.CLOSED)return null;
    if(value!=null)need(value.owner==context);
    return value;
  }
  private void actual(ExecutionEntity execution) {
    need(owner==Context.getCommandContext() && phase!=Phase.CLOSED && execution!=null
        && execution.getProcessDefinition()==definition
        && execution.getProcessInstance()==instance
        && Objects.equals(execution.getTenantId(),tenant)
        && execution.getReplacedBy()==null && !execution.isCanceled() && !execution.isExternallyTerminated());
  }
  static void ended(ExecutionEntity execution) {
    var witness=current();if(witness==null || execution.getActivity()!=witness.source)return;
    witness.actual(execution);
    need(witness.phase==Phase.SEEDED && execution==witness.original && !execution.isRemoved()
        && !execution.isConcurrent());
    var transitions=execution.getTransitionsToTake();
    need(transitions!=null && transitions.size()==1 && transitions.get(0)==witness.path.get(0));
    witness.destroysScope=execution.isScope() && witness.source.isScope();
    witness.token=witness.destroysScope?execution.getParent():execution;
    need(witness.token!=null);witness.actual(witness.token);
    witness.phase=Phase.ENDED;
  }
  static void taken(ExecutionEntity execution) {
    var witness=current();if(witness==null)return;
    var flow=execution.getTransition();
    // Unrelated native tokens stay inert; a token attempting the classified path cannot borrow it.
    if(execution!=witness.token && !witness.path.contains(flow))return;
    witness.actual(execution);
    need(execution==witness.token && witness.next<witness.path.size()
        && flow==witness.path.get(witness.next)
        && execution.getActivity()==flow.getSource()
        && (witness.phase==Phase.ENDED || witness.phase==Phase.TAKEN));
    if(witness.next==0 && witness.destroysScope)
      need(witness.original.isEnded() && witness.original.isRemoved() && !witness.original.isScope()
          && witness.original.getParent()==execution && witness.original.getReplacedBy()==null);
    witness.next++;witness.phase=Phase.TAKEN;
  }
  static void started(ExecutionEntity execution) {
    var witness=current();if(witness==null)return;
    if(execution.getActivity()!=witness.destination)return;
    witness.actual(execution);
    need(witness.phase==Phase.TAKEN && witness.next==witness.path.size()
        && execution.getTransition()==witness.path.get(witness.path.size()-1));
    if(witness.destination.isScope()) {
      var cache=witness.owner.getDbEntityManager().getDbEntityCache();
      need(execution!=witness.token && execution.getParent()==witness.token && execution.isScope()
          && !execution.isConcurrent() && cache.isTransient(execution)
          && !witness.token.isActive() && witness.token.getTransition()==null);
    } else need(execution==witness.token);
    witness.consumer=execution;witness.phase=Phase.STARTED;
  }
  static Map<String,Object> consume(ExecutionEntity execution) {
    var witness=current();need(witness!=null);witness.actual(execution);
    need(witness.phase==Phase.STARTED && witness.consumer==execution);
    witness.phase=Phase.CONSUMED;return witness.pointer;
  }

  static void linked(ExecutionEntity execution) {
    var witness=current();need(witness!=null);witness.actual(execution);
    need(witness.phase==Phase.CONSUMED && witness.consumer==execution);witness.phase=Phase.LINKED;
  }

  @Override public void onCommandContextClose(CommandContext context) {
    boolean complete=owner==context && phase==Phase.LINKED;
    erase();need(complete);
  }
  @Override public void onCommandFailed(CommandContext context,Throwable failure){erase();}
  private void erase() {
    // Retain only a closed tombstone in this dying context: later callbacks cannot reseed/reuse it.
    phase=Phase.CLOSED;owner=null;original=null;token=null;consumer=null;
    source=null;destination=null;path=null;pointer=null;instance=null;definition=null;tenant=null;
  }
  @Override public void flush(){}
  @Override public void close(){}
  private static void need(boolean value){ConsumerLineage.require(value);}
}
