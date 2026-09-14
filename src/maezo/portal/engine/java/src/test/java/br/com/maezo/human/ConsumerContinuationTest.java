package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.StandaloneProcessEngineConfiguration;
import org.cibseven.bpm.engine.impl.cfg.standalone.StandaloneTransactionContextFactory;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.cibseven.bpm.engine.impl.persistence.entity.*;
import org.cibseven.bpm.engine.impl.pvm.process.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Finite native-object state vectors only. Actual scope creation/timers remain EngineIT obligations. */
class ConsumerContinuationTest {
  CommandContext context;
  ProcessDefinitionEntity definition;
  ActivityImpl source,destination;
  TransitionImpl flow;
  ExecutionEntity execution;
  Map<String,Object> pointer,target;
  List<String> selectedIds=List.of("flow");
  @BeforeEach void setup() {
    var config=new StandaloneProcessEngineConfiguration();
    context=new CommandContext(config,new StandaloneTransactionContextFactory());
    Context.setCommandContext(context);Context.setProcessEngineConfiguration(config);
    definition=new ProcessDefinitionEntity();definition.setId("definition");
    source=definition.createActivity("original");destination=definition.createActivity("consumer");
    flow=source.createOutgoingTransition("flow");flow.setDestination(destination);
    execution=entity("execution");execution.setProcessInstance(execution);execution.setActivity(source);execution.setScope(false);
    pointer=Map.of("execution_id","execution","process_definition_id","definition","process_instance_id","execution",
        "original_task_key","original","outcome","NEGAR","original_task_id","task","command_ref","command");
    target=Map.of("edges",List.of(Map.of("outcome","NEGAR","activity_id","consumer")));
  }
  ExecutionEntity entity(String id) {
    var value=new ExecutionEntity();value.setId(id);value.setTenantId("tenant");
    value.setProcessDefinition(definition);return value;
  }
  @AfterEach void stop(){Context.removeCommandContext();Context.removeProcessEngineConfiguration();}
  void seed(){ConsumerContinuation.seed(context,execution,pointer,target,selectedIds);}
  ConsumerContinuation witness(){return (ConsumerContinuation)context.getSessions().get(ConsumerContinuation.class);}
  void end(){execution.setTransitionsToTake(List.of(flow));ConsumerContinuation.ended(execution);}
  void take(){execution.setTransition(flow);ConsumerContinuation.taken(execution);}
  void start(){execution.setActivity(destination);execution.setTransition(null);ConsumerContinuation.started(execution);}
  @Test void ordinaryNativeObjectsConsumeOnceAndCloseBeforeCommit() {
    seed();end();take();start();assertEquals(pointer,ConsumerContinuation.consume(execution));
    ConsumerContinuation.linked(execution);witness().onCommandContextClose(context);
    assertThrows(IllegalStateException.class,this::seed);
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.consume(execution));
  }
  @Test void closedWitnessLeavesUnrelatedObservationsInertButNeverAuthorizesReuse() {
    seed();end();take();start();ConsumerContinuation.consume(execution);ConsumerContinuation.linked(execution);
    witness().onCommandContextClose(context);
    assertDoesNotThrow(()->ConsumerContinuation.ended(execution));
    assertDoesNotThrow(()->ConsumerContinuation.taken(execution));
    assertDoesNotThrow(()->ConsumerContinuation.started(execution));
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.consume(execution));
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.linked(execution));
    assertThrows(IllegalStateException.class,this::seed);
  }
  @Test void incompleteInsertCannotPassPrecommit() {
    seed();end();take();start();ConsumerContinuation.consume(execution);
    assertThrows(IllegalStateException.class,()->witness().onCommandContextClose(context));
    assertThrows(IllegalStateException.class,this::seed);
  }
  @Test void nonadverseOutcomeHasNoArmedWitness() {
    var approved=new HashMap<>(pointer);approved.put("outcome","APROVAR");
    ConsumerContinuation.seed(context,execution,approved,target,List.of());assertFalse(context.getSessions().containsKey(ConsumerContinuation.class));
  }
  @ParameterizedTest @ValueSource(strings={"end","take","start","consume","linked"})
  void eachNativeStageRejectsDuplicate(String stage) {
    seed();end();if(stage.equals("end")){assertThrows(IllegalStateException.class,this::end);return;}
    take();if(stage.equals("take")){assertThrows(IllegalStateException.class,this::take);return;}
    start();if(stage.equals("start")){assertThrows(IllegalStateException.class,this::start);return;}
    ConsumerContinuation.consume(execution);
    if(stage.equals("consume")){assertThrows(IllegalStateException.class,()->ConsumerContinuation.consume(execution));return;}
    ConsumerContinuation.linked(execution);assertThrows(IllegalStateException.class,()->ConsumerContinuation.linked(execution));
  }
  @Test void sameIdentifiersOnAnotherNativeObjectCannotEndOriginal() {
    seed();var clone=entity(execution.getId());clone.setProcessInstance(execution);clone.setActivity(source);
    clone.setScope(false);clone.setTransitionsToTake(List.of(flow));
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.ended(clone));
  }
  @Test void wrongTokenCannotTakeTheSameNativeTransition() {
    seed();end();var sibling=entity("sibling");sibling.setProcessInstance(execution);sibling.setActivity(source);sibling.setTransition(flow);
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.taken(sibling));
  }
  @Test void ambiguousOriginalOutgoingTransitionsRefuse() {
    seed();execution.setTransitionsToTake(List.of(flow,flow));
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.ended(execution));
  }
  @ParameterizedTest @ValueSource(strings={"cancelled","terminated","concurrent"})
  void originalAbnormalEndCannotHandoff(String mode) {
    seed();if(mode.equals("cancelled"))execution.setCanceled(true);
    if(mode.equals("terminated"))execution.setExternallyTerminated(true);
    if(mode.equals("concurrent"))execution.setConcurrent(true);
    assertThrows(IllegalStateException.class,this::end);
  }
  @ParameterizedTest @ValueSource(strings={"tenant","definition","instance"})
  void mutatedNativeIdentityCannotBorrowAdmittedOriginal(String mode) {
    seed();
    if(mode.equals("tenant"))execution.setTenantId("other-tenant");
    if(mode.equals("definition")){var other=new ProcessDefinitionEntity();other.setId(definition.getId());execution.setProcessDefinition(other);}
    if(mode.equals("instance")){var other=entity(execution.getId());other.setProcessInstance(other);execution.setProcessInstance(other);}
    assertThrows(IllegalStateException.class,this::end);
  }
  @Test void unexpectedGatewayRouteCannotAdvanceWitness() {
    seed();end();var wrong=source.createOutgoingTransition("wrong");wrong.setDestination(destination);execution.setTransition(wrong);
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.taken(execution));
  }
  @Test void unselectedAlternativeDoesNotOverrideQualifiedPath() {
    var duplicate=source.createOutgoingTransition("unselected");duplicate.setDestination(destination);
    assertDoesNotThrow(this::seed);end();take();start();ConsumerContinuation.consume(execution);ConsumerContinuation.linked(execution);
    assertDoesNotThrow(()->witness().onCommandContextClose(context));
  }
  @Test void duplicateSelectedNativeTransitionIsUnavailable() {
    source.getOutgoingTransitions().add(flow);assertThrows(IllegalStateException.class,this::seed);
  }
  @ParameterizedTest @ValueSource(strings={"reconverging","unselected-cycle","default"})
  void qualificationReturnsOnlyItsExistingOutcomeSelectedPath(String mode) {
    String route=mode.equals("unselected-cycle")?"other":"consumer";
    String condition=mode.equals("default")?"APROVAR":"NEGAR";
    String defaultFlow=mode.equals("default")?"deny":"alternate";
    String conditioned=mode.equals("default")?"alternate":"deny";
    String xml="<definitions xmlns='http://www.omg.org/spec/BPMN/20100524/MODEL' xmlns:camunda='http://camunda.org/schema/1.0/bpmn'>"
        +"<process id='SP-OP-AUTH-001'><userTask id='original' camunda:candidateGroups='group'/>"
        +"<exclusiveGateway id='gateway' default='"+defaultFlow+"'/><exclusiveGateway id='other'/>"
        +"<serviceTask id='consumer' camunda:type='external' camunda:topic='operadora.auth.send_denial_notice'/>"
        +"<sequenceFlow id='first' sourceRef='original' targetRef='gateway'/>"
        +"<sequenceFlow id='"+conditioned+"' sourceRef='gateway' targetRef='"+(mode.equals("default")?"other":"consumer")+"'>"
        +"<conditionExpression>${decisao_auditor == '"+condition+"'}</conditionExpression></sequenceFlow>"
        +"<sequenceFlow id='"+defaultFlow+"' sourceRef='gateway' targetRef='"+(mode.equals("default")?"consumer":"other")+"'/>"
        +"<sequenceFlow id='irrelevant' sourceRef='other' targetRef='"+route+"'/></process></definitions>";
    var admitted=Map.<String,Object>of("process_key","SP-OP-AUTH-001","task_key","original","edges",List.of(
        Map.of("outcome","NEGAR","activity_id","consumer","topic","operadora.auth.send_denial_notice")));
    var result=ConsumerEdgeInstallation.topology(xml.getBytes(java.nio.charset.StandardCharsets.UTF_8),admitted,"group");
    assertEquals(Map.of("NEGAR",List.of("first","deny")),result);
  }
  @Test void nativePathBoundIsEnforcedBeforeArming() {
    var previous=source;var ids=new ArrayList<String>();ids.add("flow");
    for(int i=0;i<33;i++) {
      var gateway=definition.createActivity("gateway-"+i);
      gateway.setActivityBehavior(new org.cibseven.bpm.engine.impl.bpmn.behavior.ExclusiveGatewayActivityBehavior());
      if(i==0)flow.setDestination(gateway);
      else {var next=previous.createOutgoingTransition("next-"+i);next.setDestination(gateway);ids.add(next.getId());}
      previous=gateway;
    }
    previous.createOutgoingTransition("last").setDestination(destination);ids.add("last");selectedIds=ids;
    assertThrows(IllegalStateException.class,this::seed);
    assertFalse(context.getSessions().containsKey(ConsumerContinuation.class));
  }
  @Test void nativeReplacementCannotTransferAdmittedOriginal() {
    var replacement=execution;
    execution=new ExecutionEntity(){@Override public ExecutionEntity getReplacedBy(){return replacement;}};
    execution.setId("execution");execution.setTenantId("tenant");execution.setProcessDefinition(definition);
    execution.setProcessInstance(execution);execution.setActivity(source);execution.setScope(false);
    seed();assertThrows(IllegalStateException.class,this::end);
  }
  @Test void crossContextAndReseedAreUnavailable() {
    seed();assertThrows(IllegalStateException.class,this::seed);
    var other=new CommandContext(new StandaloneProcessEngineConfiguration(),new StandaloneTransactionContextFactory());
    other.getSessions().put(ConsumerContinuation.class,witness());Context.setCommandContext(other);
    try{assertThrows(IllegalStateException.class,()->ConsumerContinuation.consume(execution));}
    finally{Context.removeCommandContext();}
  }
  @Test void failureCleanupNeverMasksOriginalAndLeavesClosedTombstone() {
    seed();var failure=new IllegalArgumentException("original-failure");
    assertDoesNotThrow(()->witness().onCommandFailed(context,failure));
    assertThrows(IllegalStateException.class,this::seed);
    assertDoesNotThrow(()->witness().onCommandFailed(context,failure));
  }
  ExecutionEntity scopedArrival() {
    destination.setScope(true);seed();end();take();
    var manager=new org.cibseven.bpm.engine.impl.db.entitymanager.DbEntityManager(null,null);
    manager.setDbEntityCache(new org.cibseven.bpm.engine.impl.db.entitymanager.cache.DbEntityCache());
    context.getSessions().put(org.cibseven.bpm.engine.impl.db.entitymanager.DbEntityManager.class,manager);
    var child=entity("child");child.setProcessInstance(execution);child.setParent(execution);
    child.setActivity(destination);child.setScope(true);child.setTransition(flow);
    execution.setActive(false);execution.setTransition(null);manager.getDbEntityCache().putTransient(child);
    return child;
  }
  @Test void exactPreclearScopedArrivalThenSameObjectStartConsumes() {
    var child=scopedArrival();ConsumerContinuation.arriving(child);child.setTransition(null);
    ConsumerContinuation.started(child);assertEquals(pointer,ConsumerContinuation.consume(child));
    ConsumerContinuation.linked(child);assertDoesNotThrow(()->witness().onCommandContextClose(context));
  }
  @ParameterizedTest @ValueSource(strings={"missing","duplicate","wrong-flow","same-id-object","changed-parent","before-start","not-cleared"})
  void scopedHandoffCannotBeInferredOrReplayed(String mode) {
    var child=scopedArrival();
    if(mode.equals("wrong-flow")) {
      var wrong=source.createOutgoingTransition(flow.getId());wrong.setDestination(destination);child.setTransition(wrong);
      assertThrows(IllegalStateException.class,()->ConsumerContinuation.arriving(child));return;
    }
    if(!mode.equals("missing"))ConsumerContinuation.arriving(child);
    if(mode.equals("duplicate")){assertThrows(IllegalStateException.class,()->ConsumerContinuation.arriving(child));return;}
    if(mode.equals("before-start")){assertThrows(IllegalStateException.class,()->ConsumerContinuation.consume(child));return;}
    if(!mode.equals("not-cleared"))child.setTransition(null);
    if(mode.equals("same-id-object")) {
      var other=entity(child.getId());other.setProcessInstance(execution);other.setParent(execution);
      other.setActivity(destination);other.setScope(true);
      assertThrows(IllegalStateException.class,()->ConsumerContinuation.started(other));return;
    }
    if(mode.equals("changed-parent")){var other=entity("other-parent");other.setProcessInstance(execution);child.setParent(other);}
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.started(child));
  }
  @Test void transientCacheIdCannotSubstituteAnotherObjectAtArrival() {
    var child=scopedArrival();var other=entity(child.getId());other.setProcessInstance(execution);
    other.setParent(execution);other.setActivity(destination);other.setScope(true);other.setTransition(flow);
    assertTrue(context.getDbEntityManager().getDbEntityCache().isTransient(other));
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.arriving(other));
  }
  @Test void nonscopeSameIdsCannotSubstituteForFinalTakeObject() {
    seed();end();take();var other=entity(execution.getId());other.setProcessInstance(execution);
    other.setActivity(destination);other.setScope(false);
    assertThrows(IllegalStateException.class,()->ConsumerContinuation.started(other));
  }
  @Test void closedArrivalObservationIsInertAndCannotReseed() {
    var child=scopedArrival();witness().onCommandFailed(context,new IllegalArgumentException());
    assertDoesNotThrow(()->ConsumerContinuation.arriving(child));assertThrows(IllegalStateException.class,this::seed);
  }
  @Test void mappingDecoratorPreservesNativeDelegationMetadataAndOriginalFailure() {
    var calls=new ArrayList<String>();
    var original=new org.cibseven.bpm.engine.impl.core.variable.mapping.IoMapping(){
      @Override public void executeInputParameters(org.cibseven.bpm.engine.impl.core.variable.scope.AbstractVariableScope scope){assertSame(execution,scope);calls.add("input");}
      @Override public void executeOutputParameters(org.cibseven.bpm.engine.impl.core.variable.scope.AbstractVariableScope scope){assertSame(execution,scope);calls.add("output");}
    };
    var decorated=new ConsumerTaskListener.ArrivalMapping(original);
    decorated.executeInputParameters(execution);decorated.executeOutputParameters(execution);assertEquals(List.of("input","output"),calls);
    var inputs=new ArrayList<org.cibseven.bpm.engine.impl.core.variable.mapping.InputParameter>();
    var outputs=new ArrayList<org.cibseven.bpm.engine.impl.core.variable.mapping.OutputParameter>();
    decorated.setInputParameters(inputs);decorated.setOuputParameters(outputs);
    var input=new org.cibseven.bpm.engine.impl.core.variable.mapping.InputParameter("input",new org.cibseven.bpm.engine.impl.core.variable.mapping.value.ConstantValueProvider("value"));
    var output=new org.cibseven.bpm.engine.impl.core.variable.mapping.OutputParameter("output",new org.cibseven.bpm.engine.impl.core.variable.mapping.value.ConstantValueProvider("value"));
    decorated.addInputParameter(input);decorated.addOutputParameter(output);
    assertSame(inputs,decorated.getInputParameters());assertSame(outputs,decorated.getOutputParameters());
    assertSame(inputs,original.getInputParameters());assertSame(outputs,original.getOutputParameters());
    assertEquals(List.of(input),inputs);assertEquals(List.of(output),outputs);
    var failure=new IllegalArgumentException("native-input-failure");
    var throwing=new ConsumerTaskListener.ArrivalMapping(new org.cibseven.bpm.engine.impl.core.variable.mapping.IoMapping(){
      @Override public void executeInputParameters(org.cibseven.bpm.engine.impl.core.variable.scope.AbstractVariableScope scope){throw failure;}
    });
    seed();assertSame(failure,assertThrows(IllegalArgumentException.class,()->throwing.executeInputParameters(execution)));
  }
  @Test void postParseDecorationPreservesScopesAndTraversesNestedActivities() {
    var nested=definition.createActivity("nested");nested.setScope(true);
    var external=nested.createActivity("external");external.setScope(true);
    external.setActivityBehavior(new org.cibseven.bpm.engine.impl.bpmn.behavior.ExternalTaskActivityBehavior(null,null));
    destination.setScope(false);destination.setActivityBehavior(new org.cibseven.bpm.engine.impl.bpmn.behavior.ExternalTaskActivityBehavior(null,null));
    new ConsumerTaskListener().parseProcess(null,definition);
    assertTrue(nested.isScope());assertTrue(external.isScope());assertFalse(destination.isScope());
    assertNull(destination.getIoMapping());assertInstanceOf(ConsumerTaskListener.ArrivalMapping.class,external.getIoMapping());
    assertTrue(external.getIoMapping().getInputParameters().isEmpty());assertTrue(external.getIoMapping().getOutputParameters().isEmpty());
    assertDoesNotThrow(()->external.getIoMapping().executeInputParameters(execution));
    assertDoesNotThrow(()->external.getIoMapping().executeOutputParameters(execution));
  }

}
