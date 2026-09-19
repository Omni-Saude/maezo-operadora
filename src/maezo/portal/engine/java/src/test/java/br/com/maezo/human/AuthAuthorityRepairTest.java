package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.security.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.atomic.*;
import org.junit.jupiter.api.Test;

/** Pure real-signature, currentness and cross-language controls; no mocked engine or DB claim. */
class AuthAuthorityRepairTest {
  static final Instant T=Instant.parse("2026-09-10T12:00:00Z");
  static KeyPair pair() throws Exception {return KeyPairGenerator.getInstance("Ed25519").generateKeyPair();}
  static Map<String,Object> designation(KeyPair pair,String id,Instant until) {
    return PortalReadModels.record("schema","human-auth-key-designation.v1","key_id",id,"issuer","publisher",
      "purpose","human-auth-input-publication","peer_spki_sha256","a".repeat(64),
      "public_key_base64",Base64.getEncoder().encodeToString(pair.getPublic().getEncoded()),
      "not_before",PortalReadModels.time(T.minusSeconds(60)),"not_after",PortalReadModels.time(until),
      "source_grants",List.of(Map.of("kind","actor","source_ref","source","publisher_ref","owner","resource_ref","actor")));
  }
  @Test void signedPublicationCannotOutliveKeyOrInstallation() throws Exception {
    var d=designation(pair(),"key",T.plusSeconds(10));
    var key=AuthTrust.publisher(d,"key",PortalReadModels.hash(d),false,T);
    assertDoesNotThrow(()->AuthInputPublication.requireAcceptance(T.plusSeconds(10),T.plusSeconds(60),key,T.plusSeconds(60),T));
    assertThrows(Rejected.class,()->AuthInputPublication.requireAcceptance(T.plusSeconds(11),T.plusSeconds(60),key,T.plusSeconds(60),T));
    assertThrows(Rejected.class,()->AuthInputPublication.requireAcceptance(T.plusSeconds(9),T.plusSeconds(60),key,T.plusSeconds(8),T));
    assertThrows(Rejected.class,()->AuthInputPublication.requireAcceptance(T.plusSeconds(9),T.plusSeconds(8),key,T.plusSeconds(60),T));
    assertEquals(PortalReadModels.time(T.plusSeconds(10)),d.get("not_after"));
  }
  @Test void expiredRevokedOrChangedPublisherCannotAuthorizeItsHeadButUnrelatedKeyRemainsCurrent() throws Exception {
    var first=designation(pair(),"first",T.plusSeconds(10));var other=designation(pair(),"other",T.plusSeconds(100));
    String digest=PortalReadModels.hash(first);
    assertThrows(Rejected.class,()->AuthTrust.publisher(first,"first",digest,true,T));
    assertThrows(Rejected.class,()->AuthTrust.publisher(first,"first",digest,false,T.plusSeconds(10)));
    var changed=PortalReadModels.copy(first);changed.put("not_after",PortalReadModels.time(T.plusSeconds(100)));
    assertThrows(Rejected.class,()->AuthTrust.publisher(changed,"first",digest,false,T));
    var current=AuthTrust.publisher(other,"other",PortalReadModels.hash(other),false,T.plusSeconds(11));
    assertDoesNotThrow(()->current.requireSource("actor",Map.of("source_ref","source","publisher_ref","owner"),"actor"));
    assertThrows(Rejected.class,()->current.requireSource("actor",Map.of("source_ref","source","publisher_ref","owner"),"someone-else"));
  }
  static AuthTrust.Key resultKey(KeyPair pair,Instant until) {
    return new AuthTrust.Key("result","native","human-auth-result","a".repeat(64),pair.getPublic(),
      T.minusSeconds(60),until,List.of(),"b".repeat(64));
  }
  static AuthResultSigner signer(KeyPair pair,java.util.function.Supplier<Instant> clock) {
    return new AuthResultSigner(pair.getPrivate(),pair.getPublic(),"result","native","a".repeat(64),
      T.minusSeconds(60),T.plusSeconds(200),clock);
  }
  @Test void expiryWhileDesignationLookupBlocksRefusesBeforeSigning() throws Exception {
    var pair=pair();var clock=new AtomicReference<>(T);var signer=signer(pair,clock::get);
    assertThrows(Rejected.class,()->signer.sign(Map.of("result","test"),T.plusSeconds(100),"audience","tenant",30,
      ()->{clock.set(T.plusSeconds(11));return resultKey(pair,T.plusSeconds(10));}));
  }
  @Test void expiryDuringSigningCannotReturnAlreadyExpiredEnvelope() throws Exception {
    var pair=pair();var count=new AtomicInteger();
    var signer=signer(pair,()->count.incrementAndGet()<3?T:T.plusSeconds(11));
    assertThrows(Rejected.class,()->signer.sign(Map.of("result","test"),T.plusSeconds(100),"audience","tenant",30,
      ()->resultKey(pair,T.plusSeconds(10))));
  }
  @Test void finalGuardRetainsResultKeyAndExactEnvelopeExpiryAfterOtherWork() throws Exception {
    var pair=pair();var signer=signer(pair,()->T);
    var byKey=signer.sign(Map.of("result","test"),T.plusSeconds(100),"audience","tenant",30,
      ()->resultKey(pair,T.plusSeconds(10)));
    assertDoesNotThrow(()->byKey.current(T.plusSeconds(9)));
    assertThrows(Rejected.class,()->byKey.current(T.plusSeconds(10)));
    var byEnvelope=signer.sign(Map.of("result","test"),T.plusSeconds(5),"audience","tenant",30,
      ()->resultKey(pair,T.plusSeconds(100)));
    assertThrows(Rejected.class,()->byEnvelope.current(T.plusSeconds(5)));
    assertThrows(IllegalStateException.class,()->new AuthRuntime.Result().bytes());
  }
  @Test void liveSigningPreservesExactValidSignatureAndDefensiveBytes() throws Exception {
    var pair=pair();var signed=signer(pair,()->T).sign(Map.of("result","test"),T.plusSeconds(100),"audience","tenant",30,
      ()->resultKey(pair,T.plusSeconds(100)));
    var bytes=signed.bytes();var envelope=Jcs.object(Jcs.parse(bytes));
    byte[] signature=Base64.getUrlDecoder().decode((String)envelope.remove("signature"));
    var verifier=Signature.getInstance("Ed25519");verifier.initVerify(pair.getPublic());verifier.update(Jcs.canonical(envelope));
    assertTrue(verifier.verify(signature));assertEquals(Long.toString(T.plusSeconds(30).getEpochSecond()),envelope.get("expires_at"));
    bytes[0]=0;assertNotEquals(0,signed.bytes()[0]);signed.current(T.plusSeconds(29));
  }
  @Test void exactPythonStartAndDocumentCommandsUseSeparateAdmissionSource() throws Exception {
    try(var stream=getClass().getResourceAsStream("/auth-admission-commands.json")) {
      assertNotNull(stream);var witnesses=PortalReadModels.list(Jcs.parse(stream.readAllBytes()));assertEquals(2,witnesses.size());
      for(Object value:witnesses) {
        var witness=Jcs.object(value);var c=Jcs.object(witness.get("command"));boolean start=c.get("schema").equals("human-auth-start.v1");
        AuthModels.validate(start?"start":"documents",c);
        String operation=start?"auth.start":"auth.documents.respond";
        var pins=new HashSet<String>();for(Object pin:PortalReadModels.list(c.get("input_pins"))) {
          var p=Jcs.object(pin);assertNotEquals("audit_intent",p.get("kind"));pins.add(p.get("kind")+"\n"+p.get("resource_ref"));
        }
        var kinds=start?Set.of("actor","resource_authority","guide","start_facts","document_policy"):
          Set.of("actor","resource_authority","document_policy");
        AuthInputs.exactKinds(pins,kinds,Set.of());
        var intent=Jcs.object(witness.get("audit_intent"));var source=Jcs.object(witness.get("published_source"));
        AuthInputs.admissionIdentity(c,operation,intent,source,T.plusSeconds(1));
        var extra=new HashSet<>(pins);extra.add("audit_intent\n"+intent.get("intent_ref"));
        assertThrows(Rejected.class,()->AuthInputs.exactKinds(extra,kinds,Set.of()));
        for(String field:List.of("intent_ref","command_id","admitted_digest","operation")) {
          var bad=PortalReadModels.copy(intent);bad.put(field,field.equals("admitted_digest")?"f".repeat(64):field.equals("operation")?(start?"auth.documents.respond":"auth.start"):"wrong");
          assertThrows(Rejected.class,()->AuthInputs.admissionIdentity(c,operation,bad,source,T.plusSeconds(1)));
        }
        var badSource=PortalReadModels.copy(source);badSource.put("receipt_ref","wrong");
        assertThrows(Rejected.class,()->AuthInputs.admissionIdentity(c,operation,intent,badSource,T.plusSeconds(1)));
        var badActor=PortalReadModels.copy(intent);var actor=PortalReadModels.copy(Jcs.object(intent.get("actor")));actor.put("principal_ref","wrong");badActor.put("actor",actor);
        assertThrows(Rejected.class,()->AuthInputs.admissionIdentity(c,operation,badActor,source,T.plusSeconds(1)));
      }
    }
  }
  @Test void originalSessionCeilingConstrainsNativeEnvelopeAndFinalAdmission() throws Exception {
    try(var stream=getClass().getResourceAsStream("/auth-admission-commands.json")) {
      assertNotNull(stream);
      for(Object item:PortalReadModels.list(Jcs.parse(stream.readAllBytes()))) {
        var intent=Jcs.object(Jcs.object(item).get("audit_intent"));
        var binding=Jcs.object(intent.get("session_binding"));
        Instant until=PortalReadModels.time(binding.get("authorization_until"));
        assertEquals(until,AuthInputs.admissionCeiling(intent,until,T.plusSeconds(1)));
        assertThrows(Rejected.class,()->AuthInputs.admissionCeiling(intent,until.plusSeconds(1),T.plusSeconds(1)));
        assertThrows(Rejected.class,()->AuthInputs.admissionCeiling(intent,until,until));
        var missing=PortalReadModels.copy(intent);missing.remove("session_binding");
        assertThrows(Rejected.class,()->AuthInputs.admissionCeiling(missing,until,T.plusSeconds(1)));
        var widened=PortalReadModels.copy(intent);var changed=PortalReadModels.copy(binding);
        changed.put("session_expires_at",PortalReadModels.time(until.minusSeconds(1)));
        widened.put("session_binding",changed);
        assertThrows(Rejected.class,()->AuthInputs.admissionCeiling(widened,until,T.plusSeconds(1)));
      }
    }
  }

}
