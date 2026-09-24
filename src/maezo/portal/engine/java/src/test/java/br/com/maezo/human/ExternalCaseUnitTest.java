package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import static br.com.maezo.human.PortalReadModels.*;
import static br.com.maezo.human.ExternalCaseModels.*;

import java.security.*;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.DynamicTest;
import java.util.stream.Stream;

/** Pure native boundary tests; no engine or PostgreSQL privileges are mocked as qualified. */
class ExternalCaseUnitTest {
  static final String H="a".repeat(64),CASE="case_synthetic_only_0001";
  static Map<String,Object> cloneMap(Object value){return map(Jcs.parse(Jcs.canonical(value)));}
  static Map<String,Object> sign(Map<String,Object> value,PrivateKey key)throws Exception{
    var out=new TreeMap<>(value);Signature signature=Signature.getInstance("Ed25519");signature.initSign(key);signature.update(Jcs.canonical(out));
    out.put("signature",Base64.getUrlEncoder().withoutPadding().encodeToString(signature.sign()));return out;
  }
  static class Fixture {
    final KeyPair root,signer;final String pin;final Map<String,Object> scope,identity,owner,grant,source,packet,bundle,installation,checkpoint;
    final Configuration configuration;
    Fixture()throws Exception{
      root=KeyPairGenerator.getInstance("Ed25519").generateKeyPair();signer=KeyPairGenerator.getInstance("Ed25519").generateKeyPair();pin=Jcs.digest(signer.getPublic().getEncoded());
      String start=time(Instant.now().minusSeconds(60)),end=time(Instant.now().plusSeconds(120));
      scope=record("tenant","tenant-test","environment","unit","engine_name","engine-test","database_incarnation","db-test");
      identity=record("upstream_resource_key","resource-test","case_ref",CASE,"process_instance_ref","process-test","process_definition_id","definition-test","process_definition_key","SP-OP-AUTH","process_definition_version","1","process_definition_digest",H,"kind","authorization");
      owner=record("kind","beneficiary","resource_ref","beneficiary-test");
      grant=record("grant_ref","grant-test","identity",identity,"owner",owner,"issuer","https://issuer.test","subject","subject-test","principal_ref","principal-test","membership_revision","2","audience","beneficiary","operations",List.of("list","detail"),"projection_id","portal-external-case-summary.v1","fields",new ArrayList<>(new TreeSet<>(FIELDS)),"consent_scopes",List.of("consent-test"),"grant_revision","1","source_revision","1","decision_receipt_ref","decision-test","decision_digest",H,"signer_fingerprint",pin,"valid_until",end,"state","active");
      source=record("schema","portal-external-case-source.v1","scope",scope,"source_ref","source-test","source_namespace","operator-test","source_revision","1","identity",identity,"owners",List.of(owner),"disclosure_grants",List.of(grant),"ownership_receipt_ref","ownership-test","ownership_receipt_digest",H,"ownership_signer_fingerprint",pin,"observed_at",start,"valid_until",end,"state","active");
      packet=record("schema","portal-external-source-packet.v1","statement",source,"ownership_proof",proof(source,"ownership"),"disclosure_proofs",List.of(proof(grant,"disclosure")));
      var key=record("fingerprint",pin,"public_key_spki_base64",Base64.getUrlEncoder().withoutPadding().encodeToString(signer.getPublic().getEncoded()),"purposes",List.of("ownership","disclosure","completeness"),"source_namespaces",List.of("operator-test"),"kinds",List.of("authorization","reimbursement","account"),"audiences",List.of("beneficiary","provider"),"not_before",start,"not_after",end);
      var complete=record("designation_ref","complete-test","policy_receipt_ref","policy-test","policy_receipt_digest",H,"required_namespaces",List.of("operator-test"),"signer_fingerprints",List.of(pin),"valid_until",end);
      bundle=record("schema","portal-external-authority-designation.v1","scope",scope,"designation_ref","designation-test","designation_revision","1","installation_receipt_ref","installed-test","policy_receipt_ref","policy-test","policy_receipt_digest",H,"signers",List.of(key),"completeness",complete,"observed_at",start,"valid_until",end);
      installation=sign(record("schema","portal-external-authority-installation.v1","scope",scope,"designation_digest",hash(bundle),"receipt_ref","installed-test","observed_at",start,"valid_until",end),root.getPrivate());
      configuration=new Configuration(scope,hash(bundle),root.getPublic(),signer.getPublic(),pin,H,"portal-external-case-publication","cibseven","maezo_native");
      var c=record("schema","portal-external-scope-checkpoint.v1","scope",scope,"designation_digest",hash(bundle),"checkpoint_ref","checkpoint-test","epoch","1","predecessor_checkpoint_digest",null,"upstream_position","position-test","observed_at",start,"valid_until",end,"namespace_positions",List.of(record("namespace","operator-test","upstream_position","position-test")),"heads",List.of(),"heads_count","0","heads_digest",hash(List.of()));
      checkpoint=record("schema","portal-external-checkpoint-packet.v1","statement",c,"completeness_proof",proof(c,"completeness"));
    }
    Map<String,Object> proof(Map<String,Object> value,String purpose)throws Exception{
      return sign(record("schema","portal-external-assertion.v1","purpose",purpose,"algorithm","Ed25519","key_fingerprint",pin,"issued_at",time(Instant.now().minusSeconds(30)),"expires_at",time(Instant.now().plusSeconds(60)),"digest",hash(value)),signer.getPrivate());
    }
    Authority authority(){return new Authority(configuration,Jcs.canonical(bundle),Jcs.canonical(installation),Set.of());}
  }
  @Test void independentlyVerifiesOriginalSourceAndAuthoritativeEmpty()throws Exception{
    var f=new Fixture();assertEquals(f.source,f.authority().source(f.packet));assertEquals(obj(f.checkpoint,"statement"),f.authority().checkpoint(f.checkpoint));
    ExternalCaseStore.checkpointBarrier(obj(f.checkpoint,"statement"),List.of(),0,0);
  }
  /** Ressalva #489: the external configuration pins the native schema and binds both schemas in its digest. */
  @Test void publicationReceiptIsQualifiedByThePinnedSchema(){
    assertEquals("SELECT KEY_FINGERPRINT_ FROM \"maezo_native\".MZO_PORTAL_READ_PUBLICATION_RECEIPT WHERE ",
        ExternalCaseReadCommand.publicationReceiptSql("maezo_native"));
    for(String bad:new String[]{null,"public","pg_temp","Maezo","maezo_native\";--"})
      assertThrows(RuntimeException.class,()->ExternalCaseReadCommand.publicationReceiptSql(bad));
  }
  @Test void configurationPinsTheNativeSchema()throws Exception{
    var f=new Fixture();
    for(String[] bad:new String[][]{{"cibseven",null},{"cibseven","public"},{"cibseven","cibseven"},{"maezo_x","maezo_x"},{"cibseven","Bad"}})
      assertThrows(RuntimeException.class,()->new Configuration(f.scope,hash(f.bundle),f.root.getPublic(),f.signer.getPublic(),f.pin,H,"portal-external-case-publication",bad[0],bad[1]),String.join("/",String.valueOf(bad[0]),String.valueOf(bad[1])));
    var other=new Configuration(f.scope,hash(f.bundle),f.root.getPublic(),f.signer.getPublic(),f.pin,H,"portal-external-case-publication","cibseven","maezo_native_b");
    assertNotEquals(f.configuration.digest(),other.digest());
  }
  @TestFactory Stream<DynamicTest> originalAuthorityAttacks(){return Stream.of("source","grant","purpose","namespace","revocation","root","empty_namespace").map(attack->DynamicTest.dynamicTest(attack,()->{
    var f=new Fixture();var p=cloneMap(f.packet);var a=f.authority();
    switch(attack){
      case "source"->obj(p,"statement").put("ownership_receipt_digest","b".repeat(64));
      case "grant"->map(list(obj(p,"statement").get("disclosure_grants")).get(0)).put("principal_ref","other-principal");
      case "purpose"->obj(p,"ownership_proof").put("purpose","disclosure");
      case "namespace"->obj(p,"statement").put("source_namespace","other-namespace");
      case "revocation"->a=new Authority(f.configuration,Jcs.canonical(f.bundle),Jcs.canonical(f.installation),Set.of(f.pin));
      case "root"->{var c=new Configuration(f.scope,hash(f.bundle),f.signer.getPublic(),f.signer.getPublic(),f.pin,H,"portal-external-case-publication","cibseven","maezo_native");assertThrows(RuntimeException.class,()->new Authority(c,Jcs.canonical(f.bundle),Jcs.canonical(f.installation),Set.of()));return;}
      case "empty_namespace"->{var cp=cloneMap(f.checkpoint);obj(cp,"statement").put("namespace_positions",List.of());cp.put("completeness_proof",f.proof(obj(cp,"statement"),"completeness"));assertThrows(RuntimeException.class,()->f.authority().checkpoint(cp));return;}
      default->throw new AssertionError();
    }
    Authority checked=a;assertThrows(RuntimeException.class,()->checked.source(p));
  }));}
  @Test void sourceUnknownDuplicateAndNumericFieldsRefuse()throws Exception{
    var f=new Fixture();var packet=cloneMap(f.packet);obj(packet,"statement").put("decision","approved");assertThrows(RuntimeException.class,()->f.authority().source(packet));
    assertThrows(RuntimeException.class,()->canonical("{\"x\":\"1\",\"x\":\"2\"}".getBytes()));
    assertThrows(RuntimeException.class,()->canonical("{\"x\":1}".getBytes()));
  }
  @Test void exactCheckpointRejectsPartialExtraAndNonterminalInterleaving()throws Exception{
    var f=new Fixture();var statement=obj(f.checkpoint,"statement");
    assertThrows(RuntimeException.class,()->ExternalCaseStore.checkpointBarrier(statement,List.of(record("source_ref","A")),0,0));
    assertThrows(RuntimeException.class,()->ExternalCaseStore.checkpointBarrier(statement,List.of(),3,1));
    var nonempty=cloneMap(statement);nonempty.put("heads",List.of(record("source_ref","A"),record("source_ref","B")));
    assertThrows(RuntimeException.class,()->ExternalCaseStore.checkpointBarrier(nonempty,List.of(record("source_ref","A")),2,2));
    ExternalCaseStore.checkpointBarrier(nonempty,list(nonempty.get("heads")),3,3);
  }
  @TestFactory Stream<DynamicTest> positiveGrantIsolation(){return Stream.of("principal_ref","subject","issuer","membership_revision","owner","fields","operation","audience","revoked").map(attack->DynamicTest.dynamicTest(attack,()->{
    var f=new Fixture();var principal=record("principal_ref","principal-test","subject","subject-test","issuer","https://issuer.test","membership_revision","2","subject_bindings",List.of(f.owner));
    assertTrue(ExternalCaseReadCommand.eligibleGrant(f.source,f.grant,principal,"beneficiary","list"));
    var grant=cloneMap(f.grant);String operation="list";Object audience="beneficiary";
    switch(attack){case "owner"->grant.put("owner",record("kind","beneficiary","resource_ref","other"));
      case "fields"->grant.put("fields",List.of("case_ref"));case "operation"->operation="unsupported";case "audience"->audience="provider";case "revoked"->grant.put("state","revoked");default->grant.put(attack,"other");}
    assertFalse(ExternalCaseReadCommand.eligibleGrant(f.source,grant,principal,audience,operation));
  }));}
  static Map<String,Object> nativeRow(Fixture f){return record("definition_id","definition-test","definition_key","SP-OP-AUTH","definition_version",1,"definition_tenant","tenant-test","definition_digest",H,"active_id","process-test","active_instance","process-test","active_definition","definition-test","active_tenant","tenant-test","historic_instance","process-test","historic_definition","definition-test","historic_tenant","tenant-test","ended_at",null);}
  @Test void nativeActiveAndEndedAreNeutralAndIndependentOfSource()throws Exception{
    var f=new Fixture();var r=nativeRow(f);assertEquals("active",ExternalCaseStore.nativeProjection(f.identity,"tenant-test",r).get("state"));
    r.put("active_id",null);r.put("ended_at",Timestamp.from(Instant.now()));assertEquals("ended",ExternalCaseStore.nativeProjection(f.identity,"tenant-test",r).get("state"));
  }
  @TestFactory Stream<DynamicTest> nativeMismatchNeverMasqueradesAsEnded(){return Stream.of("definition_tenant","definition_digest","active_definition","active_tenant","historic_definition","historic_tenant","missing","inconsistent").map(attack->DynamicTest.dynamicTest(attack,()->{
    var f=new Fixture();var r=nativeRow(f);switch(attack){case "missing"->{r.put("active_id",null);r.put("historic_instance",null);}case "inconsistent"->r.put("ended_at",Timestamp.from(Instant.now()));default->r.put(attack,"wrong");}
    assertThrows(RuntimeException.class,()->ExternalCaseStore.nativeProjection(f.identity,"tenant-test",r));
  }));}
  @Test void resultsAreInaccessibleBeforeNativeCommitAndDependenciesHaveNoSuccessDefault(){
    assertThrows(RuntimeException.class,()->new ExternalCasePublicationCommand.Result().bytes());
    assertThrows(RuntimeException.class,()->new ExternalCaseReadCommand.Result().bytes());
    assertThrows(RuntimeException.class,()->new ExternalCasePublicationCommand(null));
    assertThrows(RuntimeException.class,()->new ExternalCaseReadCommand(null,null,null,null));
  }
  static final class TestAdmission implements Admission {
    final String digest;boolean active=true;
    TestAdmission(String digest){this.digest=digest;}
    public void requireCurrent(){if(!active)throw unavailable();}
    public Instant validUntil(){return Instant.now().plusSeconds(30);}
    public int statementTimeoutSeconds(){return 2;}
    public String configurationDigest(){return digest;}
    public void requireContinuityKey(String id,String generation,String digest){throw unavailable();}
    public void requireMembershipProvenance(Map<String,Object> source,Map<String,Object> human){throw unavailable();}
  }
  @TestFactory Stream<DynamicTest> externalTransportIsPurposePeerAndActualConfigurationBound(){
    return Stream.of("positive","peer","purpose","configuration","signature","expired","inactive").map(attack->DynamicTest.dynamicTest(attack,()->{
      var f=new Fixture();var admission=new TestAdmission(f.configuration.digest());
      var request=record("scope",f.scope);
      var e=record("schema","portal-external-envelope.v1","purpose","portal-external-case-publication","scope",f.scope,
        "key_fingerprint",f.pin,"configuration_digest",f.configuration.digest(),"issued_at",time(Instant.now().minusSeconds(1)),
        "expires_at",time(Instant.now().plusSeconds(2)),"request",request);
      if(attack.equals("purpose"))e.put("purpose","portal-read-publication");
      if(attack.equals("configuration"))e.put("configuration_digest",H);
      if(attack.equals("expired"))e.put("expires_at",time(Instant.now().minusSeconds(1)));
      if(attack.equals("inactive"))admission.active=false;
      var signed=sign(e,attack.equals("signature")?f.root.getPrivate():f.signer.getPrivate());
      if(attack.equals("positive"))assertEquals(hash(request),verify(f.configuration,admission,Jcs.canonical(signed),H).digest);
      else assertThrows(RuntimeException.class,()->verify(f.configuration,admission,Jcs.canonical(signed),attack.equals("peer")?"b".repeat(64):H));
    }));
  }

  @Test void supersessionDigestStreamsBeyondPageLimitsWithoutChangingCanonicalProof(){
    var streamed=new ExternalCaseStore.CoverageDigest();var expected=new ArrayList<Object>();
    for(int i=1;i<=1500;i++){var e=record("source_generation",Integer.toString(i),"source_ref","A","source_revision",Integer.toString(i),"source_digest",H);expected.add(e);streamed.add(e);}
    assertEquals(1500,streamed.count);assertEquals(hash(expected),streamed.finish());
    assertEquals(hash(List.of()),new ExternalCaseStore.CoverageDigest().finish());
  }

  @Test void bulkAccountingEnlistsAsAffectDataAndNeverInterpolatesValues(){
    var c=new org.apache.ibatis.session.Configuration();ExternalCaseStore.CountedWrites.install(c);
    var statement=c.getMappedStatement(ExternalCaseStore.CountedWrites.ID);
    assertTrue(statement.isDirtySelect());assertFalse(statement.isUseCache());
    var bound=statement.getBoundSql(new EnlistedWrites.Write("UPDATE protected_table SET value=?",new Object[]{"synthetic-private"}));
    assertFalse(bound.getSql().contains("synthetic-private"));assertEquals(1,bound.getParameterMappings().size());
    assertTrue(bound.getSql().endsWith("SELECT count(*) FROM external_written"));
  }

}
