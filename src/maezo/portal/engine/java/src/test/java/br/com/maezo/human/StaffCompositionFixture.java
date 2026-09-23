package br.com.maezo.human;

import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;

/** Synthetic, ephemeral composition directory (T1.1). Keys are generated per fixture; the
 * AUTH PKCS12 comes from the JDK keytool. Nothing here is a real deployment material. */
final class StaffCompositionFixture {
  final Path dir,file;final Map<String,Object> composition;final String digest;
  final KeyPair root,result;

  StaffCompositionFixture(Path dir)throws Exception{
    this.dir=dir;file=dir.resolve("staff-composition.json");
    var g=KeyPairGenerator.getInstance("Ed25519");root=g.generateKeyPair();result=g.generateKeyPair();
    Files.write(dir.resolve("native-result.pk8"),result.getPrivate().getEncoded());
    String password="fixture-"+UUID.randomUUID().toString().replace("-","");
    Files.writeString(dir.resolve("auth-signing.password"),password,StandardCharsets.US_ASCII);
    var p12=dir.resolve("auth-signing.p12");
    var keytool=Path.of(System.getProperty("java.home"),"bin","keytool").toString();
    var proc=new ProcessBuilder(keytool,"-genkeypair","-keyalg","Ed25519","-alias","auth-result","-dname","CN=auth-result",
      "-validity","2","-storetype","PKCS12","-keystore",p12.toString(),"-storepass",password,"-keypass",password)
      .redirectErrorStream(true).start();
    proc.getInputStream().readAllBytes();if(proc.waitFor()!=0)throw new IllegalStateException("keytool failed");
    var store=KeyStore.getInstance("PKCS12");try(var in=Files.newInputStream(p12)){store.load(in,password.toCharArray());}
    String spki=Jcs.digest(store.getCertificate("auth-result").getPublicKey().getEncoded());
    var scope=new TreeMap<String,Object>(Map.of("tenant","tenant-test","environment","dev","engine_name","human-it",
      "database_incarnation","inc-1","installation_ref","installation-1","installation_revision","1"));
    var pins=new TreeMap<String,Object>();long oid=16400;
    for(String t:new TreeSet<>(StaffCaseStore.OWNED))pins.put(t,new TreeMap<String,Object>(Map.of("oid",Long.toString(oid++),"owner","maezo_native_schema_owner")));
    var c=new TreeMap<String,Object>();
    c.put("schema","staff-deployment-composition.v1");c.put("auth_scope",scope);c.put("designation_digest","d".repeat(64));
    c.put("root_public_key",Base64.getEncoder().encodeToString(root.getPublic().getEncoded()));
    c.put("result_public_key",Base64.getEncoder().encodeToString(result.getPublic().getEncoded()));
    c.put("result_private_key_file","native-result.pk8");c.put("native_role","maezo_native_result");
    c.put("native_schema","maezo_native");c.put("engine_schema","cibseven");c.put("relation_pins",pins);c.put("maximum_seconds","10");
    c.put("catalog_anchor",new TreeMap<String,Object>(Map.of("scope",new TreeMap<String,Object>(Map.of("tenant","tenant-test","environment","dev","workload_ref","portal-staff")),
      "catalog_ref","catalog-1","publisher_ref","publisher-1")));
    c.put("auth",new TreeMap<String,Object>(Map.of("audience","engine-test","max_lifetime_seconds","60","timeout_seconds","5",
      "signing_pkcs12_file","auth-signing.p12","signing_password_file","auth-signing.password","alias","auth-result",
      "key_id","auth-result-1","issuer","maezo-engine","signing_spki_sha256",spki)));
    var staff=new StaffCaseInstallation.Configuration(scope,"d".repeat(64),root.getPublic(),result.getPrivate(),result.getPublic(),
      "maezo_native_result","maezo_native","cibseven",pinned(pins),10);
    digest=staff.digest();c.put("configuration_digest",digest);composition=c;write(c);
  }
  static Map<String,StaffCaseStore.RelationPin> pinned(Map<String,Object> pins){
    var out=new TreeMap<String,StaffCaseStore.RelationPin>();
    pins.forEach((k,v)->{var m=PortalReadModels.map(v);out.put(k,new StaffCaseStore.RelationPin(Long.parseLong((String)m.get("oid")),(String)m.get("owner")));});
    return out;
  }
  void write(Map<String,Object> c)throws Exception{Files.write(file,Jcs.canonical(c));}
  /** A deep copy that a test can tamper with and write back. */
  Map<String,Object> copy(){return new TreeMap<>(PortalReadModels.map(Jcs.parse(Jcs.canonical(composition))));}
}
