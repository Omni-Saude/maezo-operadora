package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;

import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
import java.util.*;

/** W6A closed profiles and independent original-assertion verification. No Q2 purpose alias. */
public final class ExternalCaseModels {
  private ExternalCaseModels() {}
  static final Set<String> FIELDS = Set.of("case_ref", "kind", "state", "record_revision", "state_observed_at");
  static final Map<String,String> SHAPES = Map.ofEntries(
    Map.entry("scope", "tenant:r environment:r engine_name:r database_incarnation:r"),
    Map.entry("owner", "kind:beneficiary|provider resource_ref:r"),
    Map.entry("identity", "upstream_resource_key:r case_ref:case process_instance_ref:r process_definition_id:r process_definition_key:r process_definition_version:n process_definition_digest:h kind:authorization|reimbursement|account"),
    Map.entry("grant", "grant_ref:r identity:@identity owner:@owner issuer:r subject:r principal_ref:r membership_revision:n audience:beneficiary|provider operations:[list|detail projection_id:portal-external-case-summary.v1 fields:[field consent_scopes:[r grant_revision:n source_revision:n decision_receipt_ref:r decision_digest:h signer_fingerprint:h valid_until:t state:active|revoked"),
    Map.entry("source", "schema:portal-external-case-source.v1 scope:@scope source_ref:r source_namespace:r source_revision:n identity:@identity owners:[@owner disclosure_grants:[@grant ownership_receipt_ref:r ownership_receipt_digest:h ownership_signer_fingerprint:h observed_at:t valid_until:t state:active|revoked"),
    Map.entry("proof", "schema:portal-external-assertion.v1 purpose:ownership|disclosure|completeness algorithm:Ed25519 key_fingerprint:h issued_at:t expires_at:t digest:h signature:url64"),
    Map.entry("sourcepacket", "schema:portal-external-source-packet.v1 statement:@source ownership_proof:@proof disclosure_proofs:[@proof"),
    Map.entry("head", "namespace:r source_ref:r source_revision:n source_digest:h upstream_resource_key:r case_ref:case state:active|revoked"),
    Map.entry("position", "namespace:r upstream_position:r"),
    Map.entry("checkpoint", "schema:portal-external-scope-checkpoint.v1 scope:@scope designation_digest:h checkpoint_ref:r epoch:n predecessor_checkpoint_digest:?h upstream_position:r observed_at:t valid_until:t namespace_positions:[@position heads:[@head heads_count:n heads_digest:h"),
    Map.entry("checkpointpacket", "schema:portal-external-checkpoint-packet.v1 statement:@checkpoint completeness_proof:@proof"),
    Map.entry("sourceingress", "schema:portal-external-source-ingress-receipt.v1 scope:@scope ingress_id:r request_digest:h source_ref:r source_revision:n source_digest:h upstream_receipts_digest:h source_generation:n committed_at:t"),
    Map.entry("checkpointingress", "schema:portal-external-checkpoint-ingress-receipt.v1 scope:@scope ingress_id:r request_digest:h checkpoint_ref:r epoch:n checkpoint_digest:h designation_digest:h committed_at:t"),
    Map.entry("signer", "fingerprint:h public_key_spki_base64:url64 purposes:[ownership|disclosure|completeness source_namespaces:[r kinds:[authorization|reimbursement|account audiences:[beneficiary|provider not_before:t not_after:t"),
    Map.entry("completeness", "designation_ref:r policy_receipt_ref:r policy_receipt_digest:h required_namespaces:[r signer_fingerprints:[h valid_until:t"),
    Map.entry("bundle", "schema:portal-external-authority-designation.v1 scope:@scope designation_ref:r designation_revision:n installation_receipt_ref:r policy_receipt_ref:r policy_receipt_digest:h signers:[@signer completeness:@completeness observed_at:t valid_until:t"),
    Map.entry("installation", "schema:portal-external-authority-installation.v1 scope:@scope designation_digest:h receipt_ref:r observed_at:t valid_until:t signature:url64")
  );
  static Map<String,Object> shape(String name, Object value) {
    var m=map(value); var fields=new HashSet<String>();
    for (String part: SHAPES.get(name).split(" ")) {
      String[] f=part.split(":",2); fields.add(f[0]); check(f[1],m.get(f[0]));
    }
    if (!m.keySet().equals(fields)) throw invalid();
    return m;
  }
  static void check(String type, Object value) {
    if(type.startsWith("?")){ if(value!=null)check(type.substring(1),value);return; }
    if(type.startsWith("[")){var seen=new HashSet<String>();for(Object v:list(value)){
      check(type.substring(1),v);if(!seen.add(hash(v)))throw invalid();}return;}
    if(type.startsWith("@")){shape(type.substring(1),value);return;}
    if(type.equals("case")){if(!(value instanceof String s)||!s.matches("[A-Za-z0-9_-]{16,128}"))throw invalid();return;}
    if(type.equals("field")){if(!FIELDS.contains(value))throw invalid();return;}
    if(type.equals("url64")){url64(value);return;}
    PortalReadModels.type(type,value);
  }
  static byte[] url64(Object value){
    if(!(value instanceof String s))throw invalid();
    try{byte[] b=Base64.getUrlDecoder().decode(s);
      if(!Base64.getUrlEncoder().withoutPadding().encodeToString(b).equals(s))throw invalid();return b;
    }catch(IllegalArgumentException ex){throw invalid();}
  }
  static Map<String,Object> canonical(byte[] raw){
    var m=map(Jcs.parse(raw));if(!Arrays.equals(raw,Jcs.canonical(m)))throw invalid();return m;
  }
  static void signature(PublicKey key, Map<String,Object> value){
    try{var signed=new TreeMap<>(value);byte[] sig=url64(signed.remove("signature"));
      Signature verifier=Signature.getInstance("Ed25519");verifier.initVerify(key);
      verifier.update(Jcs.canonical(signed));if(!verifier.verify(sig))throw denied();
    }catch(GeneralSecurityException ex){throw denied();}
  }
  static PublicKey publicKey(Object encoded){
    try{return KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(url64(encoded)));}
    catch(GeneralSecurityException ex){throw denied();}
  }
  static Set<String> strings(Object v){var result=new TreeSet<String>();for(Object x:list(v))result.add((String)x);return result;}
  static boolean compatible(Object audience,Object kind){return audience.equals("beneficiary")
    ? Set.of("authorization","reimbursement").contains(kind)
    : audience.equals("provider") && Set.of("authorization","account").contains(kind);}

  /** Acquired by installation composition BEFORE native locks. Missing provider refuses.
   * Implementations must bind actual external deployment, mTLS peer, key/purpose and
   * membership provisioning/committed provenance; guards are local after acquisition.
   * No implementation or activation default is supplied by this source package.
   */
  public interface Admission {
    void requireCurrent();
    Instant validUntil();
    int statementTimeoutSeconds();
    String configurationDigest();
    /** Dedicated external continuity qualification; this is not Q2 admission. No I/O here. */
    void requireContinuityKey(String id,String generation,String digest);
    void requireMembershipProvenance(Map<String,Object> membershipSource, Map<String,Object> nativeHuman);
  }
  public record Configuration(Map<String,Object> scope, String designationDigest,
      PublicKey installationKey, PublicKey transportKey, String transportFingerprint,
      String tlsPeerFingerprint, String purpose, String engineSchema) {
    public Configuration {
      scope=Collections.unmodifiableMap(new TreeMap<>(shape("scope",scope)));
      check("h",designationDigest);check("h",transportFingerprint);check("h",tlsPeerFingerprint);EngineSchema.require(engineSchema,null);
      if(installationKey==null||transportKey==null||!Jcs.digest(transportKey.getEncoded()).equals(transportFingerprint)
          ||!Set.of("portal-external-case-publication","portal-external-case-read").contains(purpose))throw unavailable();
    }
    public String digest(){return hash(record("schema","portal-external-native-configuration.v1","scope",scope,
      "designation_digest",designationDigest,"installation_key_spki_base64",Base64.getUrlEncoder().withoutPadding().encodeToString(installationKey.getEncoded()),
      "transport_key_spki_base64",Base64.getUrlEncoder().withoutPadding().encodeToString(transportKey.getEncoded()),
      "transport_fingerprint",transportFingerprint,"tls_peer_fingerprint",tlsPeerFingerprint,"purpose",purpose));}
  }
  public static final class Verified {
    final Configuration configuration;final Map<String,Object> request;final String digest;
    final Admission admission;final Instant until;
    private Verified(Configuration c,Map<String,Object> r,Admission a,Instant u){configuration=c;request=r;digest=hash(r);admission=a;until=u;}
    void current(){admission.requireCurrent();Instant n=Instant.now();if(!n.isBefore(until)||!n.isBefore(admission.validUntil()))throw unavailable();}
  }
  public static Verified verify(Configuration c,Admission a,byte[] raw,String actualTlsPeer){
    if(c==null||a==null||!c.digest().equals(a.configurationDigest()))throw unavailable();
    var e=canonical(raw);Jcs.keys(e,"schema","purpose","scope","key_fingerprint","configuration_digest","issued_at","expires_at","request","signature");
    if(!"portal-external-envelope.v1".equals(e.get("schema"))||!c.purpose.equals(e.get("purpose"))
        ||!c.scope.equals(e.get("scope"))||!c.transportFingerprint.equals(e.get("key_fingerprint"))
        ||!c.tlsPeerFingerprint.equals(actualTlsPeer)||!a.configurationDigest().equals(e.get("configuration_digest")))throw denied();
    Instant start=time(e.get("issued_at")),end=time(e.get("expires_at")),now=Instant.now();
    if(start.isAfter(now)||!now.isBefore(end)||end.isAfter(start.plusSeconds(10)))throw denied();
    signature(c.transportKey,e);var request=map(e.get("request"));
    if(!c.scope.equals(request.get("scope")))throw denied();
    Verified result=new Verified(c,Collections.unmodifiableMap(new TreeMap<>(request)),a,end);result.current();return result;
  }

  static final class Authority {
    final Map<String,Object> bundle;final String designation;final Set<String> revoked;
    final List<Instant> ceilings=new ArrayList<>();
    Authority(Configuration c,byte[] raw,byte[] receipt,Set<String> revoked){
      bundle=shape("bundle",canonical(raw));var installation=shape("installation",canonical(receipt));
      designation=Jcs.digest(raw);this.revoked=Set.copyOf(revoked);
      if(!designation.equals(c.designationDigest)||!designation.equals(installation.get("designation_digest"))
          ||!c.scope.equals(bundle.get("scope"))||!c.scope.equals(installation.get("scope"))
          ||!bundle.get("installation_receipt_ref").equals(installation.get("receipt_ref")))throw denied();
      signature(c.installationKey,installation);fresh(bundle,"observed_at","valid_until");fresh(installation,"observed_at","valid_until");
      Set<String> pins=new HashSet<>();for(Object item:list(bundle.get("signers"))){var signer=map(item);
        if(!pins.add(str(signer,"fingerprint"))||!hashKey(signer).equals(signer.get("fingerprint")))throw denied();
        for(String k:List.of("purposes","source_namespaces","kinds","audiences"))if(list(signer.get(k)).isEmpty())throw denied();}
      var complete=obj(bundle,"completeness");if(pins.isEmpty()||list(complete.get("required_namespaces")).isEmpty()
          ||list(complete.get("signer_fingerprints")).isEmpty()||!pins.containsAll(strings(complete.get("signer_fingerprints"))))throw denied();
    }
    String hashKey(Map<String,Object> signer){return Jcs.digest(publicKey(signer.get("public_key_spki_base64")).getEncoded());}
    void fresh(Map<String,Object> m,String start,String end){Instant n=Instant.now(),a=time(m.get(start)),b=time(m.get(end));
      if(a.isAfter(n)||!n.isBefore(b)||!a.isBefore(b))throw unavailable();ceilings.add(b);}
    void current(){Instant now=Instant.now();for(Instant end:ceilings)if(!now.isBefore(end))throw unavailable();}
    Instant until(){current();return ceilings.stream().min(Comparator.naturalOrder()).orElseThrow();}
    void proof(Map<String,Object> p,String purpose,Map<String,Object> payload,Set<String> namespaces,Object kind,Object audience){
      var signer=list(bundle.get("signers")).stream().map(PortalReadModels::map)
        .filter(s->s.get("fingerprint").equals(p.get("key_fingerprint"))).findFirst().orElseThrow(PortalReadModels::denied);
      if(revoked.contains(p.get("key_fingerprint"))||!purpose.equals(p.get("purpose"))
          ||!strings(signer.get("purposes")).contains(purpose)||!hash(payload).equals(p.get("digest"))
          ||!strings(signer.get("source_namespaces")).containsAll(namespaces)
          ||kind!=null&&!list(signer.get("kinds")).contains(kind)
          ||audience!=null&&!list(signer.get("audiences")).contains(audience))throw denied();
      fresh(p,"issued_at","expires_at");if(time(p.get("issued_at")).isBefore(time(signer.get("not_before")))
          ||time(p.get("expires_at")).isAfter(time(signer.get("not_after"))))throw denied();
      signature(publicKey(signer.get("public_key_spki_base64")),p);
    }
    Map<String,Object> source(Object value){
      var packet=shape("sourcepacket",value);var s=obj(packet,"statement");
      if(!bundle.get("scope").equals(s.get("scope"))||list(s.get("owners")).isEmpty())throw denied();
      fresh(s,"observed_at","valid_until");var own=obj(packet,"ownership_proof");var identity=obj(s,"identity");
      if(!own.get("key_fingerprint").equals(s.get("ownership_signer_fingerprint")))throw denied();
      proof(own,"ownership",s,Set.of(str(s,"source_namespace")),identity.get("kind"),null);
      var proofs=new HashMap<String,Map<String,Object>>();for(Object v:list(packet.get("disclosure_proofs"))){var p=map(v);
        if(proofs.put(str(p,"digest"),p)!=null)throw denied();}
      var grantRefs=new HashSet<Object>();
      if(proofs.size()!=list(s.get("disclosure_grants")).size())throw denied();
      for(Object v:list(s.get("disclosure_grants"))){var g=map(v);var p=proofs.get(hash(g));
        if(p==null||!p.get("key_fingerprint").equals(g.get("signer_fingerprint"))||!grantRefs.add(g.get("grant_ref"))
            ||!identity.equals(g.get("identity"))||!list(s.get("owners")).contains(g.get("owner"))
            ||!s.get("source_revision").equals(g.get("source_revision"))
            ||!obj(g,"owner").get("kind").equals(g.get("audience"))||!compatible(g.get("audience"),identity.get("kind"))
            ||s.get("state").equals("revoked")&&!g.get("state").equals("revoked")
            ||time(g.get("valid_until")).isAfter(time(s.get("valid_until"))))throw denied();
        ceilings.add(time(g.get("valid_until")));proof(p,"disclosure",g,Set.of(str(s,"source_namespace")),identity.get("kind"),g.get("audience"));
      }
      current();return s;
    }
    Map<String,Object> checkpoint(Object value){
      var packet=shape("checkpointpacket",value);var c=obj(packet,"statement");var complete=obj(bundle,"completeness");
      Set<String> namespaces=new TreeSet<>();String prior="";for(Object v:list(c.get("namespace_positions"))){var p=map(v);String n=str(p,"namespace");
        if(n.compareTo(prior)<=0||!namespaces.add(n))throw denied();prior=n;}
      if(!bundle.get("scope").equals(c.get("scope"))||!designation.equals(c.get("designation_digest"))
          ||!namespaces.equals(strings(complete.get("required_namespaces")))
          ||!list(complete.get("signer_fingerprints")).contains(obj(packet,"completeness_proof").get("key_fingerprint"))
          ||number(c.get("heads_count"))!=list(c.get("heads")).size()||!hash(c.get("heads")).equals(c.get("heads_digest")))throw denied();
      prior="";Set<String> cases=new HashSet<>();for(Object v:list(c.get("heads"))){var h=map(v);String ref=str(h,"source_ref");
        if(ref.compareTo(prior)<=0||!cases.add(str(h,"case_ref"))||!namespaces.contains(h.get("namespace")))throw denied();prior=ref;}
      fresh(c,"observed_at","valid_until");ceilings.add(time(complete.get("valid_until")));
      proof(obj(packet,"completeness_proof"),"completeness",c,namespaces,null,null);current();return c;
    }
  }
  static Map<String,Object> publication(Object value){
    var r=map(value);Jcs.keys(r,"schema","scope","publication_id","requester_fingerprint","captured_provenance_digest","kind","packet","ingress_receipt");
    if(!"portal-external-publication-request.v1".equals(r.get("schema")))throw invalid();shape("scope",r.get("scope"));
    check("r",r.get("publication_id"));check("h",r.get("requester_fingerprint"));check("h",r.get("captured_provenance_digest"));
    boolean source="case".equals(r.get("kind"));if(!source&&!"checkpoint".equals(r.get("kind")))throw invalid();
    var p=shape(source?"sourcepacket":"checkpointpacket",r.get("packet"));var i=shape(source?"sourceingress":"checkpointingress",r.get("ingress_receipt"));
    var s=obj(p,"statement");String d=hash(p);
    if(!r.get("scope").equals(s.get("scope"))||!r.get("scope").equals(i.get("scope"))
        ||!d.equals(i.get("request_digest"))||!hash(i).equals(r.get("captured_provenance_digest"))
        ||!d.equals(i.get(source?"source_digest":"checkpoint_digest")))throw denied();
    for(String field:source?List.of("source_ref","source_revision"):List.of("checkpoint_ref","epoch","designation_digest"))
      if(!s.get(field).equals(i.get(field)))throw denied();
    if(source){var ds=new ArrayList<Object>();for(Object g:list(s.get("disclosure_grants")))ds.add(map(g).get("decision_digest"));
      if(!hash(record("ownership",s.get("ownership_receipt_digest"),"disclosure",ds)).equals(i.get("upstream_receipts_digest")))throw denied();}
    return r;
  }
}
