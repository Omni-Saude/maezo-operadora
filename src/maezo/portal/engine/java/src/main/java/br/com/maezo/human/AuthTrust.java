package br.com.maezo.human;

import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.time.Instant;
import java.util.*;

/** Owner-designated E04 peer/purpose keys; signing a record never designates its producer. */
final class AuthTrust {
  static final Set<String> PURPOSES=Set.of("human-auth-start","human-auth-documents","human-auth-read",
    "human-auth-input-publication","human-auth-publication-read","human-auth-result");
  final AuthStore store;
  final String audience;
  final long maxLifetime;
  AuthTrust(AuthStore store,String audience,long maxLifetime) {
    if(audience==null||audience.isBlank()||maxLifetime<1||maxLifetime>300)throw Rejected.denied();
    this.store=store;this.audience=audience;this.maxLifetime=maxLifetime;
  }
  record Key(String id,String issuer,String purpose,String peerSpki,PublicKey publicKey,
      Instant notBefore,Instant notAfter,List<Object> sources) {
    void current(Instant now) {
      if(now.isBefore(notBefore)||!now.isBefore(notAfter))throw Rejected.denied();
    }
    void requireSource(String kind,Map<String,Object> source,String resource) {
      for(Object item:sources) {
        var grant=Jcs.object(item);
        if(kind.equals(grant.get("kind"))&&source.get("source_ref").equals(grant.get("source_ref"))
            &&source.get("publisher_ref").equals(grant.get("publisher_ref"))
            &&resource.equals(grant.get("resource_ref")))return;
      }
      throw Rejected.denied();
    }
  }
  static Map<String,Object> designation(Object value) {
    var d=Jcs.object(value);
    Jcs.keys(d,"schema","key_id","issuer","purpose","peer_spki_sha256","public_key_base64",
      "not_before","not_after","source_grants");
    if(!"human-auth-key-designation.v1".equals(d.get("schema"))||!PURPOSES.contains(d.get("purpose")))throw Rejected.invalid();
    for(String key:List.of("key_id","issuer"))Jcs.ref(d,key);
    Jcs.hash(d,"peer_spki_sha256");decode(Jcs.string(d,"public_key_base64"));
    if(!PortalReadModels.time(d.get("not_before")).isBefore(PortalReadModels.time(d.get("not_after"))))throw Rejected.invalid();
    var sources=PortalReadModels.list(d.get("source_grants"));
    if(sources.size()>1024)throw Rejected.invalid();var unique=new HashSet<String>();
    for(Object item:sources) {
      var grant=Jcs.object(item);Jcs.keys(grant,"kind","source_ref","publisher_ref","resource_ref");
      if(!AuthModels.INPUT_KINDS.contains(grant.get("kind")))throw Rejected.invalid();
      for(String key:List.of("source_ref","publisher_ref","resource_ref"))Jcs.ref(grant,key);
      if(!unique.add(PortalReadModels.hash(grant)))throw Rejected.invalid();
    }
    boolean publisher=Set.of("human-auth-input-publication","human-auth-publication-read").contains(d.get("purpose"));
    if(publisher==sources.isEmpty())throw Rejected.invalid();
    return d;
  }
  Key key(String id,String purpose,String issuer,String peer,Instant now) {
    if(!PURPOSES.contains(purpose)||store.revoked(id))throw Rejected.denied();
    var row=store.one("SELECT DESIGNATION_ FROM MZO_AUTH_TRUST WHERE TENANT_=? AND KEY_ID_=?",store.tenant,id);
    var d=designation(AuthStore.parse(row.get("designation_")));
    if(!id.equals(d.get("key_id"))||!purpose.equals(d.get("purpose"))||!issuer.equals(d.get("issuer"))
        ||!peer.equals(d.get("peer_spki_sha256")))throw Rejected.denied();
    var key=new Key(id,issuer,purpose,peer,decode(Jcs.string(d,"public_key_base64")),
      PortalReadModels.time(d.get("not_before")),PortalReadModels.time(d.get("not_after")),
      List.copyOf(PortalReadModels.list(d.get("source_grants"))));key.current(now);return key;
  }
  private static PublicKey decode(String encoded) {
    try {
      byte[] der=Base64.getDecoder().decode(encoded);
      if(!Base64.getEncoder().encodeToString(der).equals(encoded)||der.length!=44)throw Rejected.invalid();
      return KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(der));
    } catch(GeneralSecurityException|IllegalArgumentException failure){throw Rejected.invalid();}
  }
}
