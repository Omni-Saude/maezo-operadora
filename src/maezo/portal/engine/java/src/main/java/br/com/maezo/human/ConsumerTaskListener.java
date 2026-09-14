package br.com.maezo.human;

import java.util.*;
import org.cibseven.bpm.engine.impl.bpmn.behavior.ExternalTaskActivityBehavior;
import org.cibseven.bpm.engine.impl.bpmn.parser.AbstractBpmnParseListener;
import org.cibseven.bpm.engine.impl.migration.instance.MigratingActivityInstance;
import org.cibseven.bpm.engine.impl.migration.instance.parser.MigratingInstanceParseContext;
import org.cibseven.bpm.engine.impl.persistence.entity.ExecutionEntity;
import org.cibseven.bpm.engine.impl.pvm.delegate.ActivityExecution;
import org.cibseven.bpm.engine.impl.pvm.process.ActivityImpl;
import org.cibseven.bpm.engine.impl.pvm.process.ScopeImpl;
import org.cibseven.bpm.engine.impl.pvm.process.TransitionImpl;
import org.cibseven.bpm.engine.delegate.ExecutionListener;
import org.cibseven.bpm.engine.impl.util.xml.Element;

/** CIB2.1 post-parse behavior wrapper: capture after actual native external-task insertion. */
final class ConsumerTaskListener extends AbstractBpmnParseListener {
  @Override public void parseUserTask(Element element,ScopeImpl scope,ActivityImpl activity){
    activity.addListener("end",(ExecutionListener)value->ConsumerContinuation.ended((ExecutionEntity)value));
  }
  @Override public void parseSequenceFlow(Element element,ScopeImpl scope,TransitionImpl transition){
    transition.addListener("take",(ExecutionListener)value->ConsumerContinuation.taken((ExecutionEntity)value));
  }
  @Override public void parseServiceTask(Element element,ScopeImpl scope,ActivityImpl activity){
    if(activity.getActivityBehavior() instanceof ExternalTaskActivityBehavior original){
      activity.addListener("start",(ExecutionListener)value->ConsumerContinuation.started((ExecutionEntity)value));
      activity.setActivityBehavior(new LinkedBehavior(original));
    }
  }
  private static final class LinkedBehavior extends ExternalTaskActivityBehavior {
    private final ExternalTaskActivityBehavior original;
    LinkedBehavior(ExternalTaskActivityBehavior original){super(null,original.getPriorityValueProvider());this.original=original;}
    @Override public void execute(ActivityExecution value)throws Exception{
      ConsumerLineage.require(value instanceof ExecutionEntity);ExecutionEntity execution=(ExecutionEntity)value;
      Set<String> before=new HashSet<>();for(var task:execution.getExternalTasks())before.add(task.getId());
      // Retain original topic/priority evaluation and native createAndInsert, including history.
      original.execute(value);
      var created=execution.getExternalTasks().stream().filter(t->!before.contains(t.getId())).toList();
      ConsumerLineage.require(created.size()==1);ConsumerLineage.created(execution,created.get(0));
    }
    @Override public void signal(ActivityExecution execution,String name,Object value)throws Exception{original.signal(execution,name,value);}
    @Override public void migrateScope(ActivityExecution execution){original.migrateScope(execution);}
    @Override public void onParseMigratingInstance(MigratingInstanceParseContext context,MigratingActivityInstance activity){
      original.onParseMigratingInstance(context,activity);
    }
  }
}
