package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.nio.file.*;
import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
import java.util.*;

/** Public deployment-qualified companion. Never installs its own trust or privileges. */
final class AssignmentTrust {
  final Map<String,Object> config;
  final String digest,tenant,environment,engine,incarnation;
  final Map<String,Trust.Key> readKeys;
  final PublicKey sourceKey,receiptSourceKey;
  final Trust human;
  AssignmentTrust(Map<String,Object> config,Trust human){
    Jcs.keys(config,"schema","tenant","environment","engine_name","database_incarnation","deployment_receipt","validity_policy","valid_until","source","publisher","approved_policy_pins","approved_binding_pins","read_keys","receipt_disclosure_source","approved_receipt_policy_pins");
    if(!"human-assignment-trust.v1".equals(config.get("schema")))throw invalid();
    this.config=copy(config);this.human=human;digest=hash(config);
    tenant=Jcs.ref(config,"tenant");environment=Jcs.ref(config,"environment");engine=Jcs.ref(config,"engine_name");incarnation=Jcs.ref(config,"database_incarnation");
    if(!tenant.equals(human.tenant)||!engine.equals(human.engineName))throw denied();
    validate("pin",config.get("deployment_receipt"));validate("pin",config.get("validity_policy"));
    number(config.get("valid_until"));
    var source=obj(config,"source");Jcs.keys(source,"owner_ref","source_ref","key_id","public_key_spki_base64","not_before","not_after");
    for(String k:List.of("owner_ref","source_ref","key_id"))Jcs.ref(source,k);
    sourceKey=key(source);lifetime(source);
    var publisher=obj(config,"publisher");Jcs.keys(publisher,"workload_ref","key_fingerprint");
    String workload=Jcs.ref(publisher,"workload_ref"),fingerprint=Jcs.hash(publisher,"key_fingerprint");
    if(human.keys.values().stream().noneMatch(k->k.purpose().equals("human-authority")&&k.workload().equals(workload)&&k.fingerprint().equals(fingerprint)))throw denied();
    var seen=new HashSet<String>();human.keys.values().forEach(k->seen.add(k.fingerprint()));
    if(!seen.add(Jcs.digest(sourceKey.getEncoded())))throw denied();
    var keys=new HashMap<String,Trust.Key>();
    for(Object o:list(config.get("read_keys"))){var m=map(o);Jcs.keys(m,"id","workload","peer_spki_sha256","public_key_spki_base64","not_before","not_after");
      PublicKey pub=key(m);lifetime(m);String fp=Jcs.digest(pub.getEncoded());
      var k=new Trust.Key(Jcs.ref(m,"id"),"human-assignment-read",Jcs.ref(m,"workload"),Jcs.hash(m,"peer_spki_sha256"),pub,fp,number(m.get("not_before")),number(m.get("not_after")));
      if(!seen.add(fp)||keys.putIfAbsent(k.id(),k)!=null||human.keys.containsKey(k.id()))throw denied();
      if(human.keys.values().stream().anyMatch(other->other.workload().equals(k.workload())||other.peerSpki().equals(k.peerSpki())))throw denied();
    }if(keys.isEmpty())throw invalid();readKeys=Map.copyOf(keys);
    if(config.get("receipt_disclosure_source")==null){receiptSourceKey=null;if(!list(config.get("approved_receipt_policy_pins")).isEmpty())throw invalid();}
    else {var owner=obj(config,"receipt_disclosure_source");Jcs.keys(owner,"owner_ref","source_ref","key_id","public_key_spki_base64","not_before","not_after");for(String k:List.of("owner_ref","source_ref","key_id"))Jcs.ref(owner,k);lifetime(owner);receiptSourceKey=key(owner);if(!seen.add(Jcs.digest(receiptSourceKey.getEncoded())))throw denied();}
    for(String kind:List.of("approved_policy_pins","approved_binding_pins","approved_receipt_policy_pins"))for(Object o:list(config.get(kind)))validate("pin",o);
  }
  static void lifetime(Map<String,Object> value){if(number(value.get("not_after"))<=number(value.get("not_before")))throw invalid();}
  static PublicKey key(Map<String,Object> value){try{return KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(Base64.getDecoder().decode(Jcs.string(value,"public_key_spki_base64"))));}catch(GeneralSecurityException|IllegalArgumentException e){throw invalid();}}
  void current(Instant now){AssignmentModels.currentEpoch(config.get("valid_until"),now);}
  void scope(Map<String,Object> value){for(String k:List.of("tenant","environment","engine_name","database_incarnation"))if(!Objects.equals(value.get(k),config.get(k)))throw denied();}
  void approved(String kind,String ref,String digest){if(list(config.get(kind)).stream().noneMatch(o->map(o).get("artifact_ref").equals(ref)&&map(o).get("digest").equals(digest)))throw denied();}
  void source(Map<String,Object> attestation,Instant now,EngineStore db){source(attestation,now,db,false);}
  void source(Map<String,Object> attestation,Instant now,EngineStore db,boolean receipt){
    PublicKey sourceKey=receipt?receiptSourceKey:this.sourceKey;if(sourceKey==null)throw unavailable();
    AssignmentModels.check("attestation",attestation);scope(attestation);sourceCurrent(attestation,now,receipt);
    var owner=obj(config,receipt?"receipt_disclosure_source":"source"); var provenance=obj(attestation,"source");
    if(!attestation.get("source_key_id").equals(owner.get("key_id"))||!provenance.get("publisher_ref").equals(owner.get("owner_ref"))||!provenance.get("source_ref").equals(owner.get("source_ref"))||db.revoked(Jcs.digest(sourceKey.getEncoded())))throw denied();
    // Revocation SELECT can block. Never finish with the caller's earlier clock.
    sourceCurrent(attestation,Instant.now(),receipt);
    var signed=new TreeMap<>(attestation);signed.remove("signature");
    try{byte[] sig=Base64.getUrlDecoder().decode(str(attestation,"signature"));if(sig.length!=64||!Base64.getUrlEncoder().withoutPadding().encodeToString(sig).equals(attestation.get("signature")))throw denied();var verify=Signature.getInstance("Ed25519");verify.initVerify(sourceKey);verify.update(Jcs.canonical(signed));if(!verify.verify(sig))throw denied();}catch(GeneralSecurityException|IllegalArgumentException e){throw denied();}
  }
  /** Pure retained guard: no dependency I/O may follow it at a final transaction boundary. */
  void sourceCurrent(Map<String,Object> attestation,Instant now,boolean receipt){
    current(now);var owner=obj(config,receipt?"receipt_disclosure_source":"source");var provenance=obj(attestation,"source");
    long epoch=now.getEpochSecond();if(epoch<number(owner.get("not_before"))||epoch>=number(owner.get("not_after"))||!now.isBefore(time(provenance.get("valid_until")))||time(provenance.get("observed_at")).isAfter(now))throw denied();
  }
  static AssignmentTrust load(Path path,Trust trust)throws java.io.IOException{return new AssignmentTrust(map(Jcs.parse(Files.readAllBytes(path))),trust);}
}
