package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import org.junit.jupiter.api.Test;

/** Actual closed parser/topology vectors; no database/engine/issuer qualification. */
class ConsumerLineageTest {
  static final String SCOPE_WIRE = "{\"database_binding_digest\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"database_incarnation\":\"synthetic-incarnation\",\"engine_name\":\"synthetic-engine\",\"environment\":\"synthetic-env\",\"tenant\":\"synthetic-tenant\"}";
  static final String SCOPE_HASH = "328c87cdd68076895800c5ef10b3cf2c4f45db06f16c11b78240de3b9c464f86";
  static final String LINK_WIRE = "{\"activity_id\":\"synthetic-activity\",\"binding_digest\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"classified_command_digest\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"command_ref\":\"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\",\"decision_workload_ref\":\"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\",\"execution_id\":\"synthetic-execution\",\"external_task_id\":\"synthetic-external-task\",\"original_task_id\":\"synthetic-human-task\",\"original_task_key\":\"UT_AnaliseMedicoAuditor\",\"principal_ref\":\"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\",\"process_definition_id\":\"synthetic-definition\",\"process_instance_id\":\"synthetic-instance\",\"scope_digest\":\"328c87cdd68076895800c5ef10b3cf2c4f45db06f16c11b78240de3b9c464f86\"}";
  static final String LINK_HASH = "d61b533d1ec3cbaafa5ceb7dab3960278d945d48a29f4b05be4c355e8230ed0d";
  static Map<String,Object> parse(String v){return Jcs.object(Jcs.parse(v.getBytes(StandardCharsets.UTF_8)));}
  @Test void approvedD2DomainsMatchExactFrozenVectors(){
    assertEquals(SCOPE_HASH,ConsumerLineage.scopeHash(parse(SCOPE_WIRE)));
    assertEquals(LINK_HASH,ConsumerLineage.linkHash(parse(LINK_WIRE)));
    assertNotEquals(SCOPE_HASH,Jcs.digest(Jcs.canonical(parse(SCOPE_WIRE))));
    assertNotEquals(LINK_HASH,Jcs.digest(Jcs.canonical(parse(LINK_WIRE))));
  }
  @Test void wrappersAndUnknownFieldsCannotBecomeChildren(){
    for(String extra:List.of("schema","human_approved","business_key")){
      var s=parse(SCOPE_WIRE);s.put(extra,"wrong");assertThrows(RuntimeException.class,()->ConsumerLineage.scopeHash(s));
      var l=parse(LINK_WIRE);l.put(extra,"wrong");assertThrows(RuntimeException.class,()->ConsumerLineage.linkHash(l));
    }
    assertThrows(RuntimeException.class,()->ConsumerLineage.linkHash(parse(SCOPE_WIRE)));
    assertThrows(RuntimeException.class,()->ConsumerLineage.scopeHash(parse(LINK_WIRE)));
  }
  @Test void scalarBoundsNeverCoerceOrRound(){
    for(Object value:List.of(1,1L,true,"-1","01","9223372036854775808","1.0"))
      assertThrows(RuntimeException.class,()->ConsumerLineage.decimal(Map.of("n",value),"n",false));
    assertEquals(Long.MAX_VALUE,ConsumerLineage.decimal(Map.of("n","9223372036854775807"),"n",true));
    assertThrows(RuntimeException.class,()->ConsumerLineage.decimal(Map.of("n","0"),"n",true));
    assertThrows(RuntimeException.class,()->ConsumerLineage.ref(Map.of("id","é".repeat(128)),"id"));
    assertThrows(RuntimeException.class,()->ConsumerLineage.ref(Map.of("id","control\n"),"id"));
  }
  @Test void exactCanonicalBytesAndDepthAreRequired(){
    assertThrows(RuntimeException.class,()->ConsumerLineage.object("{ \"x\":\"y\"}".getBytes(StandardCharsets.UTF_8)));
    assertThrows(RuntimeException.class,()->ConsumerLineage.object("{\"x\":\"y\",\"x\":\"z\"}".getBytes(StandardCharsets.UTF_8)));
    Object value="leaf";for(int i=0;i<17;i++)value=Map.of("child",value);
    byte[] raw=Jcs.canonical(value);assertThrows(RuntimeException.class,()->ConsumerLineage.object(raw));
  }
  static List<Map<String,Object>> targets(){
    List<Map<String,Object>> values=new ArrayList<>();
    for(String key:ConsumerLineage.LEGACY_SOURCES.keySet().stream().sorted().toList()){
      String[] parts=key.split("/");String kind=ConsumerLineage.LEGACY_SOURCES.get(key);
      var row=new TreeMap<String,Object>();row.put("process_definition_id",parts[0]+":1:synthetic");row.put("process_key",parts[0]);row.put("task_key",parts[1]);
      for(String field:List.of("binding_digest","consumer_digest","process_digest"))row.put(field,"a".repeat(64));
      List<Map<String,Object>> edges=new ArrayList<>();
      if(kind.equals("auth_decisao"))edges.add(Map.of("outcome","JUNTA_MEDICA","activity_id","ST_ConvocarJunta","topic","operadora.auth.convene_junta","consumer_kind","auth_junta_forward"));
      if(kind.startsWith("auth_"))edges.add(Map.of("outcome","NEGAR","activity_id","ST_EnviarNegativaFormal","topic","operadora.auth.send_denial_notice","consumer_kind","auth_denial_record"));
      if(kind.equals("pagto_admissibilidade"))edges.add(Map.of("outcome","DEVOLVER","activity_id","ST_RegisterPaymentRefusal","topic","operadora.pagto.register_payment_refusal","consumer_kind","pagto_admissibility_return"));
      row.put("edges",edges);values.add(row);
    }return values;
  }
  @Test void sixTargetsAndOnlyApprovedOutcomeEdges(){
    var good=targets();assertEquals(6,ConsumerEdgeInstallation.targets(Map.of("targets",good)).size());
    var missing=new ArrayList<>(good);missing.remove(0);assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.targets(Map.of("targets",missing)));
    var duplicate=new ArrayList<>(good);duplicate.set(1,duplicate.get(0));assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.targets(Map.of("targets",duplicate)));
    var seventh=new ArrayList<>(good);seventh.add(good.get(0));assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.targets(Map.of("targets",seventh)));
    var changed=targets();changed.get(0).put("edges",List.of(Map.of("outcome","APROVAR","activity_id","ST_EnviarNegativaFormal","topic","operadora.auth.send_denial_notice","consumer_kind","auth_denial_record")));
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.targets(Map.of("targets",changed)));
    var wrongActivity=targets();wrongActivity.get(0).put("edges",List.of(Map.of("outcome","JUNTA_MEDICA","activity_id","arbitrary-task","topic","operadora.auth.convene_junta","consumer_kind","auth_junta_forward"),Map.of("outcome","NEGAR","activity_id","ST_EnviarNegativaFormal","topic","operadora.auth.send_denial_notice","consumer_kind","auth_denial_record")));
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.targets(Map.of("targets",wrongActivity)));
  }
  @Test void exactSourceBpmnProvesAllSixTargetsAndSixConsumerEdges()throws Exception{
    Path root=Path.of(System.getProperty("basedir",".")).resolve("../../../../../spec/processes/bpmn").normalize();
    for(var target:targets()){
      String process=(String)target.get("process_key");Path file;
      try(var candidates=Files.list(root)){file=candidates.filter(p->p.getFileName().toString().startsWith(process+"_")).findFirst().orElseThrow();}
      String group=switch((String)target.get("task_key")){
        case "UT_AnaliseMedicoAuditor"->"medico-auditor";case "UT_CoordenacaoAssume"->"coordenacao-auditoria-medica";case "UT_RegistrarParecerJunta"->"junta-medica";
        case "UT_AnaliseAdmissibilidade"->"coordenacao-financeira";case "UT_TratarEscalonamento"->"SYNTHETIC-owner-qualified-resolved-group";default->"supervisao-atendimento";};
      ConsumerEdgeInstallation.topology(Files.readAllBytes(file),target,group);
    }
  }
  @Test void dynamicGroupRecognitionIsNotGeneric()throws Exception{
    Path root=Path.of(System.getProperty("basedir",".")).resolve("../../../../../spec/processes/bpmn").normalize();
    Path file;try(var files=Files.list(root)){file=files.filter(p->p.getFileName().toString().startsWith("SP-OP-ESCALATION-001_")).findFirst().orElseThrow();}
    String xml=Files.readString(file);var target=targets().stream().filter(t->t.get("task_key").equals("UT_TratarEscalonamento")).findFirst().orElseThrow();
    ConsumerEdgeInstallation.topology(xml.getBytes(StandardCharsets.UTF_8),target,"SYNTHETIC-qualified");
    String wrong=xml.replace("${roteamento.grupo_atendimento}","${caller.group}");
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.topology(wrong.getBytes(StandardCharsets.UTF_8),target,"SYNTHETIC-qualified"));
    var supervisor=targets().stream().filter(t->t.get("task_key").equals("UT_SupervisorAssume")).findFirst().orElseThrow();
    String wrongTask=xml.replace("candidateGroups=\"supervisao-atendimento\"","candidateGroups=\"${roteamento.grupo_atendimento}\"");
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.topology(wrongTask.getBytes(StandardCharsets.UTF_8),supervisor,"SYNTHETIC-qualified"));
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.topology(xml.getBytes(StandardCharsets.UTF_8),supervisor,"wrong-static-group"));
  }
  @Test void nativeBuildCannotBeQualifiedByHashMetadataAlone(){
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.nativeBuild(Map.of("native_build_digest","a".repeat(64))));
  }
  record SignedFixture(KeyPair pair,Map<String,Object> designation,Map<String,Object> envelope){
    byte[] sign()throws Exception{
      var unsigned=new TreeMap<>(envelope);unsigned.remove("signature");
      Signature signer=Signature.getInstance("Ed25519");signer.initSign(pair.getPrivate());signer.update(Jcs.canonical(unsigned));
      unsigned.put("signature",Base64.getUrlEncoder().withoutPadding().encodeToString(signer.sign()));return Jcs.canonical(unsigned);
    }
    Map<String,Object> body(){return Jcs.object(envelope.get("body"));}
  }
  static SignedFixture signedFixture()throws Exception{
    var pair=KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
    byte[] encoded=pair.getPublic().getEncoded();
    var trust=new TreeMap<String,Object>();
    trust.put("schema","phi-consumer-edge-trust.v1");trust.put("issuer","synthetic-issuer");trust.put("key_id","synthetic-key");
    trust.put("public_key",Base64.getUrlEncoder().withoutPadding().encodeToString(Arrays.copyOfRange(encoded,encoded.length-32,encoded.length)));
    trust.put("purpose",ConsumerEdgeInstallation.PURPOSE);trust.put("authority_ref","synthetic-authority");
    trust.put("contract_digest",ConsumerEdgeInstallation.CONTRACT);trust.put("source_freeze_contract_digest","b".repeat(64));
    trust.put("valid_from_ms","1000");trust.put("valid_until_ms","10000");
    var body=new TreeMap<String,Object>();body.put("schema","phi-consumer-edge-qualification.v1");body.put("scope",parse(SCOPE_WIRE));
    body.put("qualification_ref","synthetic-qualification");body.put("expected_generation","1");body.put("expected_authority_revision","2");
    body.put("native_build_digest","c".repeat(64));body.put("phi_build_digest","d".repeat(64));
    body.put("source_freeze",new TreeMap<>(Map.of("issuer_contract_digest","b".repeat(64),"authority_ref","synthetic-authority","source_commit","e".repeat(40),"source_tree","f".repeat(40),"native_build_digest","c".repeat(64),"phi_build_digest","d".repeat(64),"source_artifacts_digest","a".repeat(64))));
    body.put("targets",targets());body.put("valid_from_ms","2000");body.put("valid_until_ms","9000");
    var envelope=new TreeMap<String,Object>();envelope.put("schema","phi-consumer-edge-qualification-envelope.v1");
    for(String k:List.of("issuer","key_id","purpose","authority_ref","contract_digest"))envelope.put(k,trust.get(k));
    envelope.put("body",body);return new SignedFixture(pair,trust,envelope);
  }
  @Test void realEd25519OnlyAuthenticatesDesignatedClosedPacket()throws Exception{
    var fixture=signedFixture();byte[] raw=fixture.sign();
    assertEquals(fixture.body(),ConsumerEdgeInstallation.signed("synthetic-tenant",raw,fixture.designation(),3000));
    var tampered=parse(new String(raw,StandardCharsets.UTF_8));Jcs.object(tampered.get("body")).put("qualification_ref","tampered");
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",Jcs.canonical(tampered),fixture.designation(),3000));
    var other=signedFixture();assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",raw,other.designation(),3000));
    for(String field:List.of("purpose","issuer","key_id","authority_ref","contract_digest")){
      var f=signedFixture();f.envelope().put(field,"other");byte[] bad=f.sign();
      assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",bad,f.designation(),3000),field);
    }
  }
  @Test void validSignatureNeverSubstitutesFreezeScopeOrFreshness()throws Exception{
    for(String field:List.of("issuer_contract_digest","authority_ref","native_build_digest","phi_build_digest","source_commit","source_tree")){
      var f=signedFixture();Jcs.object(f.body().get("source_freeze")).put(field,"wrong");byte[] bad=f.sign();
      assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",bad,f.designation(),3000),field);
    }
    var f=signedFixture();byte[] raw=f.sign();
    for(long now:List.of(999L,1999L,9000L,10000L))assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",raw,f.designation(),now));
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("other-tenant",raw,f.designation(),3000));
    f.body().put("valid_until_ms","10001");byte[] beyond=f.sign();assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",beyond,f.designation(),3000));
  }
  @Test void signedPacketCannotSmuggleNumbersUnknownFieldsOrOmittedTargets()throws Exception{
    for(Object value:List.of(true,"01","-1")){
      var f=signedFixture();f.body().put("expected_generation",value);byte[] bad=f.sign();
      assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",bad,f.designation(),3000));
    }
    var numeric=signedFixture();numeric.body().put("expected_generation",1L);assertThrows(RuntimeException.class,numeric::sign);
    var f=signedFixture();f.body().put("source_frozen_at","3000");byte[] unknown=f.sign();
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",unknown,f.designation(),3000));
    var partial=signedFixture();partial.body().put("targets",targets().subList(0,5));byte[] omitted=partial.sign();
    assertThrows(RuntimeException.class,()->ConsumerEdgeInstallation.signed("synthetic-tenant",omitted,partial.designation(),3000));
  }

  @Test void optedInJarClasspathHasActualPinnedBytesAndSql()throws Exception{
    String expected=System.getProperty("maezo.consumer.jar");
    if(expected==null){assertTrue(Files.isDirectory(Path.of(ConsumerLineage.class.getProtectionDomain().getCodeSource().getLocation().toURI())));return;}
    Path jar=Path.of(expected).toRealPath();
    assertEquals(jar,Path.of(ConsumerLineage.class.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath());
    String classpath=System.getProperty("surefire.test.class.path",System.getProperty("java.class.path"));
    for(String part:classpath.split(java.util.regex.Pattern.quote(java.io.File.pathSeparator)))
      assertFalse(part.replace('\\','/').matches(".*/target/classes/?"),"Production classes directory in test classpath");
    assertEquals("jar",ConsumerLineage.class.getResource("/human-consumer-lineage-postgres.sql").getProtocol());
    try(var in=ConsumerLineage.class.getResourceAsStream("/human-consumer-lineage-postgres.sql")){
      assertArrayEquals(Files.readAllBytes(Path.of(System.getProperty("basedir","."),"src/main/resources/human-consumer-lineage-postgres.sql")),Objects.requireNonNull(in).readAllBytes());
    }
    ConsumerEdgeInstallation.nativeBuild(Map.of("native_build_digest",Jcs.digest(Files.readAllBytes(jar))));
  }

}
