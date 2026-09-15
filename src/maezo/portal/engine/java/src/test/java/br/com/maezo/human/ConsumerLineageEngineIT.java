package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.sql.*;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.junit.jupiter.api.*;

/** ROOT-only actual CIB/PostgreSQL, explicit TLS owner/runtime identities, synthetic TEST issuers.
 * No skipped tests, mock engine, production authority claim or classified consumer success.
 * Opt-in profile must load reviewed production classes/resources from its real JAR.
 */
@Tag("integration")
class ConsumerLineageEngineIT {
  /** Shared with the existing classified atomic compatibility tests. Each case owns a new schema. */
  static final class Fixture extends AtomicEngineIT {
    final Map<String,String> definitions=new TreeMap<>(),digests=new TreeMap<>();
    Map<String,Object> database,scope;
    ConsumerLineageTest.SignedFixture signing;
    byte[] envelope;
    String runtimeUser,runtimePassword;
    @Override void start()throws Exception{
      if(System.getProperty("maezo.consumer.jar")==null)throw new IllegalStateException("phi-consumer-engine-it JAR profile required");
      new ConsumerLineageTest().optedInJarClasspathHasActualPinnedBytesAndSql();
      runtimeUser=env("MAEZO_CONSUMER_IT_RUNTIME_USER");runtimePassword=env("MAEZO_CONSUMER_IT_RUNTIME_PASSWORD");
      super.start();
      assertNotEquals(user,runtimeUser);
      Path root=Path.of(Objects.requireNonNull(System.getProperty("maezo.repo.root")));assertTrue(root.isAbsolute());
      for(String key:List.of("SP-OP-AUTH-001","SP-OP-ESCALATION-001","SP-OP-PAGTO-001")){
        Path file;try(var paths=Files.list(root.resolve("spec/processes/bpmn"))){file=paths.filter(p->p.getFileName().toString().startsWith(key+"_")).findFirst().orElseThrow();}
        byte[] xml=Files.readAllBytes(file);
        var deployment=engine.getRepositoryService().createDeployment().tenantId("tenant-test").addInputStream(file.getFileName().toString(),new java.io.ByteArrayInputStream(xml)).deploy();
        definitions.put(key,engine.getRepositoryService().createProcessDefinitionQuery().deploymentId(deployment.getId()).singleResult().getId());digests.put(key,Jcs.digest(xml));
      }
      scope=new TreeMap<>(Map.of("environment","test","tenant","tenant-test","engine_name","human-it","database_incarnation","synthetic-"+UUID.randomUUID(),"database_binding_digest","a".repeat(64)));
      database=new TreeMap<>(Map.of("schema","phi-consumer-native-database.v1","scope",scope,"database_name","pending","database_oid","1","schema_name",schema,"schema_oid","1","owner_role",user,"runtime_role",runtimeUser));
      try(var c=connection();var s=c.createStatement()){
        try(var r=s.executeQuery("SELECT current_database(),d.oid::text,n.oid::text FROM pg_database d JOIN pg_namespace n ON n.nspname=current_schema() WHERE d.datname=current_database()")){
          assertTrue(r.next());database.put("database_name",r.getString(1));database.put("database_oid",r.getString(2));database.put("schema_oid",r.getString(3));
        }
        String role=ConsumerEdgeInstallation.identifier(runtimeUser);
        s.execute("REVOKE ALL ON SCHEMA "+schema+" FROM PUBLIC,"+role);s.execute("GRANT USAGE ON SCHEMA "+schema+" TO "+role);
        // Original engine and authority fixture operations are real; edge qualification remains SELECT-only.
        s.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "+schema+" TO "+role);
        s.execute("REVOKE INSERT,UPDATE,DELETE ON MZO_HUMAN_DECISION_BINDING FROM "+role);
        c.setAutoCommit(false);ConsumerEdgeInstallation.installSchema(c,database);c.commit();
      }
      engine.close();engine=null;
      plugin=new HumanCommandPlugin(keys.trust());
      config=(ProcessEngineConfigurationImpl)ProcessEngineConfiguration.createStandaloneProcessEngineConfiguration()
          .setProcessEngineName("human-it").setJdbcDriver("org.postgresql.Driver").setJdbcUrl(url)
          .setJdbcUsername(runtimeUser).setJdbcPassword(runtimePassword).setDatabaseSchemaUpdate("false")
          .setJobExecutorActivate(false).setHistory("full");
      config.setEnforceHistoryTimeToLive(false);config.setMetricsEnabled(false);config.setProcessEnginePlugins(List.of(plugin));engine=config.buildProcessEngine();
      signing=ConsumerLineageTest.signedFixture();long now=Instant.now().toEpochMilli();
      signing.designation().put("valid_from_ms",Long.toString(now-1000));signing.designation().put("valid_until_ms",Long.toString(now+600000));
      try(var c=connection()){c.setAutoCommit(false);ConsumerEdgeInstallation.designate(c,"tenant-test",signing.designation(),0);c.commit();}
    }
    void qualify(Map<String,Object> raw)throws Exception{
      HumanCommand command=HumanCommand.parse(raw);var d=command.classified();
      var targets=ConsumerLineageTest.targets();long deadline=Instant.now().getEpochSecond()+300;
      try(var c=connection()){
        c.setAutoCommit(false);long revision=ConsumerEdgeInstallation.lock(c,"tenant-test")+1;
        ConsumerEdgeInstallation.write(c,"UPDATE MZO_HUMAN_TENANT SET REV_=? WHERE TENANT_=?",revision,"tenant-test");
        for(var target:targets){
          String process=(String)target.get("process_key"),task=(String)target.get("task_key");boolean original=task.equals(command.taskKey());
          String kind=ConsumerLineage.SOURCES.get(process+"/"+task);
          target.put("process_definition_id",definitions.get(process));target.put("process_digest",digests.get(process));
          target.put("binding_digest",original?d.bindingDigest():"a".repeat(64));target.put("consumer_digest","1".repeat(64));
          String group=switch(task){case "UT_AnaliseMedicoAuditor"->"medico-auditor";case "UT_CoordenacaoAssume"->"coordenacao-auditoria-medica";case "UT_RegistrarParecerJunta"->"junta-medica";case "UT_AnaliseAdmissibilidade"->"coordenacao-financeira";case "UT_TratarEscalonamento"->"synthetic-resolved-group";default->"supervisao-atendimento";};
          ConsumerEdgeInstallation.write(c,"INSERT INTO MZO_HUMAN_DECISION_BINDING VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              "tenant-test","test",definitions.get(process),process,"1",digests.get(process),task,kind,"1",original?command.formDigest():"b".repeat(64),target.get("binding_digest"),"1".repeat(64),kind,group,command.workloadRef(),revision,true,deadline);
        }
        Jcs.object(raw.get("target")).put("expected_authority_revision",Long.toString(revision));
        signing.body().put("expected_authority_revision",Long.toString(revision));c.commit();
      }
      signing.body().put("scope",scope);signing.body().put("targets",targets);
      signing.body().put("valid_from_ms",Long.toString(Instant.now().toEpochMilli()-1000));signing.body().put("valid_until_ms",Long.toString(deadline*1000));
      String jar=Jcs.digest(Files.readAllBytes(Path.of(System.getProperty("maezo.consumer.jar"))));
      signing.body().put("native_build_digest",jar);Jcs.object(signing.body().get("source_freeze")).put("native_build_digest",jar);
      installNext();
    }
    void installNext()throws Exception{
      try(var c=connection()){
        c.setAutoCommit(false);ConsumerEdgeInstallation.lock(c,"tenant-test");
        long generation=((Number)ConsumerEdgeInstallation.one(c,"SELECT GENERATION_ FROM MZO_HUMAN_CONSUMER_HEAD WHERE TENANT_='tenant-test'").get("generation_")).longValue();
        signing.body().put("expected_generation",Long.toString(generation));signing.body().put("qualification_ref","synthetic-"+UUID.randomUUID());
        envelope=signing.sign();ConsumerEdgeInstallation.install(c,"tenant-test",envelope);c.commit();
      }
      assertEquals(6,ConsumerEdgeInstallation.targets(readback(envelope).body()).size());
    }
    ConsumerEdgeInstallation.Installed readback(byte[] expected)throws Exception{
      try(var c=connection()){c.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);c.setReadOnly(true);c.setAutoCommit(false);
        var value=ConsumerEdgeInstallation.readback(c,"tenant-test","human-it",expected);c.commit();return value;}
    }
    ConsumerEdgeInstallation.Installed current(){return config.getCommandExecutorTxRequired().execute(ctx->ConsumerEdgeInstallation.current(ctx,"tenant-test"));}
    long count(String table)throws Exception{
      assertTrue(Set.of("POINTER","LINK","QUALIFICATION").contains(table));
      try(var c=connection();var s=c.createStatement();var r=s.executeQuery("SELECT count(*) FROM MZO_HUMAN_CONSUMER_"+table)){assertTrue(r.next());return r.getLong(1);}
    }
  }
  final ClassifiedDecisionEngineIT original=new ClassifiedDecisionEngineIT();
  @BeforeEach void start()throws Exception{original.start();}
  @AfterEach void stop()throws Exception{original.stop();}
  @Test void actualCompletionAtomicallyCreatesPointerReceiptAndImmutableNativeLink()throws Exception{
    var c=original.command();original.installTestQualification(c);Fixture f=original.f;
    byte[] receipt=f.send(c);assertArrayEquals(receipt,f.send(c));assertEquals(1,f.countReceipts());assertEquals(1,f.count("POINTER"));assertEquals(1,f.count("LINK"));
    try(var db=f.connection();var s=db.createStatement();var r=s.executeQuery("SELECT L.LINK_,L.LINK_DIGEST_,L.EXTERNAL_TASK_,E.EXECUTION_ID_,E.ACT_ID_ FROM MZO_HUMAN_CONSUMER_LINK L JOIN ACT_RU_EXT_TASK E ON E.ID_=L.EXTERNAL_TASK_")){
      assertTrue(r.next());var link=ConsumerLineage.object(r.getString(1).getBytes(java.nio.charset.StandardCharsets.UTF_8));assertEquals(ConsumerLineage.linkHash(link),r.getString(2));
      assertEquals(r.getString(3),link.get("external_task_id"));assertEquals(r.getString(4),link.get("execution_id"));assertEquals(r.getString(5),link.get("activity_id"));assertFalse(r.next());
    }
    try(var db=f.connection();var s=db.createStatement()){assertThrows(SQLException.class,()->s.execute("UPDATE MZO_HUMAN_CONSUMER_LINK SET LINK_='{}'"));}
  }
  @Test void sixCoexistAndLaterGenerationPreservesOldRowsWithoutRelabeling()throws Exception{
    var c=original.command();original.installTestQualification(c);Fixture f=original.f;f.send(c);
    long first=f.current().generation();f.installNext();assertEquals(first+1,f.current().generation());assertEquals(2,f.count("QUALIFICATION"));
    try(var db=f.connection();var s=db.createStatement();var r=s.executeQuery("SELECT GENERATION_ FROM MZO_HUMAN_CONSUMER_LINK")){assertTrue(r.next());assertEquals(first,r.getLong(1));}
    assertEquals(6,ConsumerEdgeInstallation.targets(f.current().body()).size());
  }
  @Test void currentAnchorRevocationRefusesEveryNewUse()throws Exception{
    var c=original.command();original.installTestQualification(c);Fixture f=original.f;long generation=f.current().generation();
    try(var db=f.connection()){db.setAutoCommit(false);ConsumerEdgeInstallation.revoke(db,"tenant-test","synthetic-key",generation);db.commit();}
    assertThrows(RuntimeException.class,f::current);assertThrows(RuntimeException.class,()->f.send(c));assertEquals(0,f.count("POINTER"));
  }
  @Test void laterOriginalTenantRevisionInvalidatesEntireEdgeSet()throws Exception{
    var c=original.command();original.installTestQualification(c);Fixture f=original.f;
    f.principal("human-test",true,List.of("medico-auditor"));
    assertThrows(RuntimeException.class,f::current);assertEquals(0,f.count("POINTER"));
  }
  @Test void expiredSignedSuccessorCannotReplaceCurrentQualification()throws Exception{
    var c=original.command();original.installTestQualification(c);Fixture f=original.f;long generation=f.current().generation();
    f.signing.body().put("expected_generation",Long.toString(generation));f.signing.body().put("valid_until_ms",Long.toString(Instant.now().toEpochMilli()-1));
    byte[] expired=f.signing.sign();
    try(var db=f.connection()){db.setAutoCommit(false);assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.install(db,"tenant-test",expired));db.rollback();}
    assertEquals(generation,f.current().generation());assertEquals(1,f.count("QUALIFICATION"));
  }
  @Test void changedNativeCandidateRefusesBeforePointer()throws Exception{
    var c=original.command();original.installTestQualification(c);Fixture f=original.f;String task=HumanCommand.parse(c).taskId();
    f.engine.getTaskService().deleteCandidateGroup(task,"medico-auditor");f.engine.getTaskService().addCandidateGroup(task,"different-actual-group");
    assertThrows(RuntimeException.class,()->f.send(c));assertEquals(0,f.count("POINTER"));assertEquals(0,f.countReceipts());
  }
  @Test void canonicalEscExpressionStillRequiresActualResolvedCandidate()throws Exception{
    Fixture f=original.f;f.principal("human-test",true,List.of("synthetic-resolved-group","different-actual-group"));
    var c=original.command("SP-OP-ESCALATION-001","UT_TratarEscalonamento",Map.of("roteamento",new HashMap<>(Map.of("grupo_atendimento","different-actual-group","sla_ack","PT1H","sla_resolucao","PT2H"))));
    original.installTestQualification(c);
    assertTrue(f.engine.getTaskService().getIdentityLinksForTask(HumanCommand.parse(c).taskId()).stream().anyMatch(link->"different-actual-group".equals(link.getGroupId())));
    assertThrows(RuntimeException.class,()->f.send(c));assertEquals(0,f.count("POINTER"));assertEquals(0,f.countReceipts());
  }
  @Test void committedReadbackReconcilesLostAcknowledgementButNeverDifferentEnvelope()throws Exception{
    var c=original.command();original.installTestQualification(c);Fixture f=original.f;byte[] committed=f.envelope.clone();
    assertArrayEquals(committed,f.readback(committed).envelope());
    f.signing.body().put("qualification_ref","not-committed");byte[] other=f.signing.sign();assertThrows(RuntimeException.class,()->f.readback(other));
    try(var db=f.connection()){db.setAutoCommit(false);assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.readback(db,"tenant-test","human-it",committed));}
  }
  @Test void runtimePrivilegeDriftIsUnavailable()throws Exception{
    var c=original.command();original.installTestQualification(c);Fixture f=original.f;
    try(var db=f.connection();var s=db.createStatement()){s.execute("GRANT DELETE ON MZO_HUMAN_CONSUMER_LINK TO "+ConsumerEdgeInstallation.identifier(f.runtimeUser));}
    assertThrows(RuntimeException.class,f::current);assertThrows(RuntimeException.class,()->f.send(c));assertEquals(0,f.countReceipts());
  }
}
