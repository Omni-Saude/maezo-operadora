package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.atomic.*;
import java.util.function.Consumer;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.transform.*;
import javax.xml.transform.dom.DOMSource;
import javax.xml.transform.stream.StreamResult;
import org.cibseven.bpm.engine.delegate.ExecutionListener;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.persistence.entity.ExecutionEntity;
import org.cibseven.bpm.engine.impl.pvm.process.ActivityImpl;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.w3c.dom.*;

/** Actual CIB/PostgreSQL TLS, synthetic qualified deployments. No mock engine or production enablement. */
@Tag("integration")
class ConsumerContinuationEngineIT {
  static final String AUTH="SP-OP-AUTH-001",SOURCE="UT_AnaliseMedicoAuditor",DEST="ST_EnviarNegativaFormal";
  static final String BPMN="http://www.omg.org/spec/BPMN/20100524/MODEL",CAMUNDA="http://camunda.org/schema/1.0/bpmn";
  final ClassifiedDecisionEngineIT original=new ClassifiedDecisionEngineIT();
  ConsumerLineageEngineIT.Fixture f;
  @BeforeEach void start()throws Exception{original.start();f=original.f;}
  @AfterEach void stop()throws Exception{original.stop();}
  String taskId(Map<String,Object> c){return Jcs.ref(Jcs.object(c.get("target")),"task_id");}
  ExecutionEntity nativeExecution(Map<String,Object> c){return f.config.getCommandExecutorTxRequired().execute(ctx->{
    var execution=ctx.getTaskManager().findTaskById(taskId(c)).getExecution();
    // These native references are lazy and require the actual active engine context to resolve.
    // Later test observations read already-loaded model metadata and scalar IDs only.
    execution.getProcessDefinition();execution.getActivity();return execution;
  });}
  ActivityImpl activity(Map<String,Object> c,String id){return nativeExecution(c).getProcessDefinition().findActivity(id);}
  void atStart(ActivityImpl activity,Consumer<ExecutionEntity> observer){
    // Observation only, before production START: no authority, pointer or native-state mutation.
    activity.addListener("start",(ExecutionListener)value->observer.accept((ExecutionEntity)value),0);
  }
  static void provenanceRefused(Runnable action) {
    var failure=assertThrows(RuntimeException.class,action::run);boolean reached=false;
    for(Throwable error=failure;error!=null;error=error.getCause())
      reached|=Arrays.stream(error.getStackTrace()).anyMatch(s->s.getClassName().equals(ConsumerContinuation.class.getName()));
    assertTrue(reached,"Must reach the native continuation provenance guard, not fail fixture qualification");
  }
  void unchangedAfterRefusal(Map<String,Object> c)throws Exception{
    assertNotNull(f.engine.getTaskService().createTaskQuery().taskId(taskId(c)).singleResult());
    assertEquals(0,f.countReceipts());assertEquals(0,f.count("POINTER"));assertEquals(0,f.count("LINK"));
  }
  Map<String,Object> qualified()throws Exception{var c=original.command();original.installTestQualification(c);assertEquals(6,ConsumerEdgeInstallation.targets(f.current().body()).size());return c;}

  /** Synthetic alternate bytes are deployed and qualified normally; no canonical file is edited. */
  void authVariant(Consumer<Document> edit)throws Exception{
    var factory=DocumentBuilderFactory.newInstance();factory.setNamespaceAware(true);
    factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl",true);
    var document=factory.newDocumentBuilder().parse(Path.of(System.getProperty("maezo.repo.root"),"spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn").toFile());
    edit.accept(document);var output=new java.io.ByteArrayOutputStream();
    TransformerFactory.newInstance().newTransformer().transform(new DOMSource(document),new StreamResult(output));
    byte[] bytes=output.toByteArray();String old=f.definitions.get(AUTH);
    var definition=f.engine.getRepositoryService().createProcessDefinitionQuery().processDefinitionId(old).singleResult();
    // No process instances exist yet; keeping native version1 preserves existing fixture contracts.
    f.engine.getRepositoryService().deleteDeployment(definition.getDeploymentId());
    var deployment=f.engine.getRepositoryService().createDeployment().tenantId("tenant-test")
        .addInputStream("synthetic-continuation.bpmn",new java.io.ByteArrayInputStream(bytes)).deploy();
    var replacement=f.engine.getRepositoryService().createProcessDefinitionQuery().deploymentId(deployment.getId()).singleResult();
    assertEquals(1,replacement.getVersion());f.definitions.put(AUTH,replacement.getId());f.digests.put(AUTH,Jcs.digest(bytes));
  }
  static Element node(Document doc,String id){
    var nodes=doc.getElementsByTagNameNS(BPMN,"*");
    for(int i=0;i<nodes.getLength();i++){var e=(Element)nodes.item(i);if(id.equals(e.getAttribute("id")))return e;}
    throw new AssertionError(id);
  }
  void nonscope()throws Exception{
    authVariant(doc->{
      Set<String> remove=new HashSet<>();var nodes=doc.getElementsByTagNameNS(BPMN,"boundaryEvent");
      for(int i=0;i<nodes.getLength();i++){var e=(Element)nodes.item(i);if(Set.of(SOURCE,DEST).contains(e.getAttribute("attachedToRef")))remove.add(e.getAttribute("id"));}
      var flows=doc.getElementsByTagNameNS(BPMN,"sequenceFlow");
      for(int i=0;i<flows.getLength();i++){var e=(Element)flows.item(i);if(remove.contains(e.getAttribute("sourceRef")))remove.add(e.getAttribute("id"));}
      var all=doc.getElementsByTagNameNS("*","*");List<Node> erase=new ArrayList<>();
      for(int i=0;i<all.getLength();i++){var e=(Element)all.item(i);
        if(remove.contains(e.getAttribute("id"))||remove.contains(e.getAttribute("bpmnElement"))
            ||(Set.of("incoming","outgoing").contains(e.getLocalName())&&remove.contains(e.getTextContent())))erase.add(e);}
      for(var e:erase)if(e.getParentNode()!=null)e.getParentNode().removeChild(e);
    });
  }

  @Test void destroyedOriginalScopeRemainsImmutableAndNewNativeScopeOwnsLink()throws Exception{
    var c=qualified();var source=nativeExecution(c);String sourceId=source.getId(),parent=source.getParentId();
    assertTrue(source.isScope());AtomicReference<Map<String,String>> observed=new AtomicReference<>();
    atStart(activity(c,DEST),e->{assertTrue(e.isScope());assertNotNull(e.getTransition());
      observed.set(Map.of("execution",e.getId(),"parent",e.getParentId(),"flow",e.getTransition().getId()));});
    byte[] receipt=f.send(c);assertArrayEquals(receipt,f.send(c));
    assertNotEquals(sourceId,observed.get().get("execution"));assertEquals(parent,observed.get().get("parent"));
    assertEquals("Flow_GWDec_Negar",observed.get().get("flow"));
    try(var db=f.connection();var s=db.createStatement();var rows=s.executeQuery("SELECT P.EXECUTION_,P.POINTER_,L.LINK_,E.EXECUTION_ID_ FROM MZO_HUMAN_CONSUMER_POINTER P JOIN MZO_HUMAN_CONSUMER_LINK L ON L.TASK_=P.TASK_ AND L.COMMAND_=P.COMMAND_ JOIN ACT_RU_EXT_TASK E ON E.ID_=L.EXTERNAL_TASK_")){
      assertTrue(rows.next());assertEquals(sourceId,rows.getString(1));
      assertEquals(sourceId,ConsumerLineage.object(rows.getString(2).getBytes(StandardCharsets.UTF_8)).get("execution_id"));
      assertEquals(rows.getString(4),ConsumerLineage.object(rows.getString(3).getBytes(StandardCharsets.UTF_8)).get("execution_id"));assertFalse(rows.next());
    }
    assertEquals(1,f.countReceipts());assertEquals(1,f.count("POINTER"));assertEquals(1,f.count("LINK"));
  }
  @Test void ordinarySameExecutionCompletesButOldHeadCannotAuthorizeAnotherConsumer()throws Exception{
    nonscope();var c=qualified();var source=nativeExecution(c);assertFalse(source.getActivity().isScope());
    String execution=source.getId(),instance=source.getProcessInstanceId();var target=activity(c,DEST);assertFalse(target.isScope());
    AtomicReference<String> started=new AtomicReference<>();atStart(target,e->started.set(e.getId()));
    f.send(c);assertEquals(execution,started.get());assertEquals(1,f.count("LINK"));
    var originalTask=f.engine.getExternalTaskService().createExternalTaskQuery().processInstanceId(instance).singleResult();
    provenanceRefused(()->f.engine.getRuntimeService().createProcessInstanceModification(instance)
        .cancelAllForActivity(DEST).startBeforeActivity(DEST).execute());
    assertEquals(1,f.count("POINTER"));assertEquals(1,f.count("LINK"));assertEquals(1,f.countReceipts());
    assertNotNull(f.engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(originalTask.getId()).singleResult());
  }
  @Test void nonadverseNativeCompletionDoesNotArmConsumerWitness()throws Exception{
    var c=original.command();c.put("outcome",Map.of("kind","auth_decisao","decisao_auditor","APROVAR"));original.installTestQualification(c);
    byte[] receipt=f.send(c);assertArrayEquals(receipt,f.send(c));assertEquals(1,f.count("POINTER"));assertEquals(0,f.count("LINK"));assertEquals(1,f.countReceipts());
    assertEquals("operadora.auth.issue_authorization",f.engine.getExternalTaskService().createExternalTaskQuery().singleResult().getTopicName());
  }
  @Test void independentInstancesKeepDistinctOriginalPointers()throws Exception{
    var first=original.command();var second=original.command();original.installTestQualification(second);
    Jcs.object(first.get("target")).put("expected_authority_revision",Jcs.object(second.get("target")).get("expected_authority_revision"));
    f.send(first);f.send(second);assertEquals(2,f.count("LINK"));assertEquals(2,f.countReceipts());
    try(var db=f.connection();var s=db.createStatement();var rows=s.executeQuery("SELECT count(DISTINCT P.POINTER_::jsonb->>'process_instance_id'),count(DISTINCT P.TASK_) FROM MZO_HUMAN_CONSUMER_POINTER P JOIN MZO_HUMAN_CONSUMER_LINK L ON L.TASK_=P.TASK_ AND L.COMMAND_=P.COMMAND_ WHERE P.POINTER_::jsonb->>'process_instance_id'=L.LINK_::jsonb->>'process_instance_id'")){
      assertTrue(rows.next());assertEquals(2,rows.getInt(1));assertEquals(2,rows.getInt(2));
    }
  }
  @Test void actualParallelSiblingDirectStartHasNoCausalWitness()throws Exception{
    var c=qualified();var originalExecution=nativeExecution(c);String instance=originalExecution.getProcessInstanceId();
    AtomicBoolean reached=new AtomicBoolean(),concurrent=new AtomicBoolean();
    atStart(activity(c,DEST),e->{reached.set(true);assertEquals(instance,e.getProcessInstanceId());assertNotEquals(originalExecution.getId(),e.getId());
      for(var cursor=e;cursor!=null;cursor=cursor.getParent())if(cursor.isConcurrent())concurrent.set(true);});
    provenanceRefused(()->f.engine.getRuntimeService().createProcessInstanceModification(instance).startBeforeActivity(DEST).execute());
    assertTrue(reached.get());assertTrue(concurrent.get(),"Actual native parallel branch must be observed");unchangedAfterRefusal(c);
  }
  @Test void directNewInstanceAtQualifiedConsumerCannotInventOriginal()throws Exception{
    var c=qualified();AtomicBoolean reached=new AtomicBoolean();atStart(activity(c,DEST),e->reached.set(true));
    provenanceRefused(()->f.engine.getRuntimeService().createProcessInstanceById(f.definitions.get(AUTH)).startBeforeActivity(DEST).execute());
    assertTrue(reached.get());unchangedAfterRefusal(c);
  }
  @Test void actualGatewayTakingAnotherOutcomeCannotUseAdmittedPointer()throws Exception{
    var c=qualified();AtomicBoolean changed=new AtomicBoolean();
    // Adversarial native listener changes routing after original admitted END. The signed command
    // remains NEGAR; only the actual CIB gateway chooses JUNTA_MEDICA from the changed variable.
    activity(c,SOURCE).addListener("end",(ExecutionListener)e->{e.setVariable("decisao_auditor","JUNTA_MEDICA");changed.set(true);});
    provenanceRefused(()->f.send(c));assertTrue(changed.get());assertEquals("NEGAR",HumanCommand.parse(c).outcome());unchangedAfterRefusal(c);
  }
  @ParameterizedTest @ValueSource(booleans={false,true})
  void actualTimerBranchCannotBorrowHumanDecision(boolean interrupting)throws Exception{
    String boundary=interrupting?"BT_SlaAnalise":"BT_AlertaSla",flow=interrupting?"Flow_SlaEstourado_Pub":"Flow_Alerta_Notify";
    authVariant(doc->node(doc,flow).setAttribute("targetRef",DEST));
    var c=qualified();var nativeOriginal=nativeExecution(c);String instance=nativeOriginal.getProcessInstanceId(),execution=nativeOriginal.getId();
    AtomicBoolean reached=new AtomicBoolean(),ended=new AtomicBoolean();
    atStart(activity(c,DEST),e->{reached.set(true);
      var cached=Context.getCommandContext().getDbEntityManager().getCachedEntity(ExecutionEntity.class,execution);
      assertNotNull(cached);ended.set(cached.isEnded());});
    var jobs=f.engine.getManagementService().createJobQuery().processInstanceId(instance).list();assertEquals(2,jobs.size());
    var job=jobs.stream().filter(j->boundary.equals(f.engine.getManagementService().createJobDefinitionQuery()
        .jobDefinitionId(j.getJobDefinitionId()).singleResult().getActivityId())).findFirst().orElseThrow();
    provenanceRefused(()->f.engine.getManagementService().executeJob(job.getId()));
    assertTrue(reached.get());assertEquals(interrupting,ended.get(),"Witness must observe actual native interrupt semantics");unchangedAfterRefusal(c);
  }
  @ParameterizedTest @ValueSource(strings={"source-after","consumer-before"})
  void actualAsyncBoundaryRollsBackUnconsumedAdverseCommand(String mode)throws Exception{
    authVariant(doc->node(doc,mode.equals("source-after")?SOURCE:DEST).setAttributeNS(CAMUNDA,
        mode.equals("source-after")?"camunda:asyncAfter":"camunda:asyncBefore","true"));
    var c=qualified();AtomicBoolean endReached=new AtomicBoolean();
    activity(c,SOURCE).addListener("end",(ExecutionListener)e->endReached.set(true),0);
    provenanceRefused(()->f.send(c));assertTrue(endReached.get());unchangedAfterRefusal(c);
    assertEquals(0,f.engine.getManagementService().createJobQuery().messages().count(),"No async continuation authority may survive rollback");
    f.config.getCommandExecutorTxRequired().execute(ctx->{assertFalse(ctx.getSessions().containsKey(ConsumerContinuation.class));return null;});
  }
}
