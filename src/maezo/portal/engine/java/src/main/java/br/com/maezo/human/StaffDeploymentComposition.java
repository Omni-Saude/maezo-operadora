package br.com.maezo.human;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.*;
import java.util.logging.Logger;
import org.cibseven.bpm.engine.impl.cfg.AbstractProcessEnginePlugin;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;

/** T1.1 / D-E: the protected deployment composition of the staff/AUTH configuration.
 *
 * <p>Registered in {@code bpm-platform.xml} BEFORE {@link HumanCommandPlugin}
 * ({@code Dockerfile.human}, {@code INSTALL_STAFF_COMPOSITION=true}). In {@code preInit} it reads
 * ONE canonical (JCS) composition file named by {@code MAEZO_STAFF_COMPOSITION_FILE}, builds
 * {@link StaffCaseInstallation.Configuration}, {@link HumanAuthConfiguration} and the catalog
 * anchor, and calls the two setters of the single {@code HumanCommandPlugin} that follows it.
 *
 * <p>Fail-closed: no variable, no file, a non-canonical file, an unknown/missing field, a secret
 * file outside the composition directory, a key pair that does not match, or a
 * {@code configuration_digest} that differs from the recomputed
 * {@code Configuration.digest()} all throw, and the engine does not start. There is no default
 * and no discovery. Private material is never in the composition file itself: it names sibling
 * files (mounted read-only) by plain name.
 *
 * <p>On success it emits exactly one public log line,
 * {@code staff_native_configuration_digest=<hex>}, the independent channel of the
 * {@code native_configuration_sha256} pin. Nothing else about the configuration is logged.
 */
public final class StaffDeploymentComposition extends AbstractProcessEnginePlugin {
  static final String ENV="MAEZO_STAFF_COMPOSITION_FILE";
  static final String SCHEMA="staff-deployment-composition.v1";
  static final String LOG_PREFIX="staff_native_configuration_digest=";
  static final int MAX=65536;
  static final Logger LOG=Logger.getLogger(StaffDeploymentComposition.class.getName());
  private final Path explicit;private final boolean fromEnvironment;
  private boolean composed;

  public StaffDeploymentComposition(){explicit=null;fromEnvironment=true;}
  /** Test/harness entry: {@code file==null} is the same "no file" refusal as a missing variable. */
  StaffDeploymentComposition(Path file){explicit=file;fromEnvironment=false;}

  /** What the composition produced; package-private for tests only. */
  record Composed(StaffCaseInstallation.Configuration staff,Map<String,Object> anchor,HumanAuthConfiguration auth){}

  @Override
  public synchronized void preInit(ProcessEngineConfigurationImpl configuration){
    if(composed)throw new IllegalStateException("staff deployment composition refused");
    Path file=explicit;
    if(fromEnvironment){String value=System.getenv(ENV);file=value==null||value.isBlank()?null:Path.of(value);}
    if(file==null)throw new IllegalStateException(ENV+" must be explicitly configured");
    var target=target(configuration);
    Composed result;
    try{result=load(file);}
    catch(RuntimeException|IOException|GeneralSecurityException failure){throw refused();}
    try{target.setHumanAuthConfiguration(result.auth());target.setStaffCaseConfiguration(result.staff(),result.anchor());}
    catch(RuntimeException failure){throw refused();}
    composed=true;
    LOG.info(LOG_PREFIX+result.staff().digest());
  }

  /** Exactly one HumanCommandPlugin, after exactly one composition (this one). */
  HumanCommandPlugin target(ProcessEngineConfigurationImpl configuration){
    var plugins=configuration==null?null:configuration.getProcessEnginePlugins();
    if(plugins==null)throw refused();
    int self=-1,human=-1,selves=0,humans=0;
    for(int i=0;i<plugins.size();i++){var p=plugins.get(i);
      if(p instanceof StaffDeploymentComposition){selves++;if(p==this)self=i;}
      if(p instanceof HumanCommandPlugin){humans++;human=i;}}
    if(selves!=1||humans!=1||self<0||human<self)throw refused();
    return (HumanCommandPlugin)plugins.get(human);
  }

  static IllegalStateException refused(){return new IllegalStateException("staff deployment composition refused");}

  static Composed load(Path file)throws IOException,GeneralSecurityException{
    if(!file.isAbsolute())throw refused();
    byte[] raw=read(file);
    var c=PortalReadModels.map(Jcs.parse(raw));
    if(!Arrays.equals(raw,Jcs.canonical(c)))throw refused();
    Jcs.keys(c,"schema","auth_scope","designation_digest","root_public_key","result_public_key","result_private_key_file",
      "native_role","native_schema","engine_schema","relation_pins","maximum_seconds","catalog_anchor","auth","configuration_digest");
    if(!SCHEMA.equals(c.get("schema")))throw refused();
    Path dir=file.getParent();
    var authScope=PortalReadModels.map(c.get("auth_scope"));
    var rootKey=publicKey(c.get("root_public_key"));var resultPublic=publicKey(c.get("result_public_key"));
    var resultKey=privateKey(read(sibling(dir,c.get("result_private_key_file"))));
    pair(resultKey,resultPublic);
    var pinsIn=PortalReadModels.map(c.get("relation_pins"));
    if(!pinsIn.keySet().equals(StaffCaseStore.OWNED))throw refused();
    var pins=new TreeMap<String,StaffCaseStore.RelationPin>();
    for(var e:pinsIn.entrySet()){var p=PortalReadModels.map(e.getValue());Jcs.keys(p,"oid","owner");
      pins.put(e.getKey(),new StaffCaseStore.RelationPin(Jcs.seconds(p,"oid"),(String)p.get("owner")));}
    long maximum=Jcs.seconds(c,"maximum_seconds");if(maximum>10)throw refused();
    String nativeRole=Jcs.ref(c,"native_role");
    var staff=new StaffCaseInstallation.Configuration(authScope,Jcs.hash(c,"designation_digest"),rootKey,resultKey,resultPublic,
      nativeRole,(String)c.get("native_schema"),(String)c.get("engine_schema"),pins,(int)maximum);
    if(!Jcs.hash(c,"configuration_digest").equals(staff.digest()))throw refused();
    var anchor=PortalReadModels.copy(PortalReadModels.validate("anchor",c.get("catalog_anchor")));
    var a=PortalReadModels.map(c.get("auth"));
    Jcs.keys(a,"audience","max_lifetime_seconds","timeout_seconds","signing_pkcs12_file","signing_password_file","alias","key_id","issuer","signing_spki_sha256");
    long timeout=Jcs.seconds(a,"timeout_seconds");if(timeout>10)throw refused();
    char[] password=password(read(sibling(dir,a.get("signing_password_file"))));
    try{
      var auth=new HumanAuthConfiguration(authScope,Jcs.ref(a,"audience"),Jcs.seconds(a,"max_lifetime_seconds"),(int)timeout,
        sibling(dir,a.get("signing_pkcs12_file")),password,Jcs.ref(a,"alias"),Jcs.ref(a,"key_id"),Jcs.ref(a,"issuer"),Jcs.hash(a,"signing_spki_sha256"));
      return new Composed(staff,Collections.unmodifiableMap(anchor),auth);
    }finally{Arrays.fill(password,'\0');}
  }

  /** A plain sibling name of the composition file: no path, no dot file, no traversal. */
  static Path sibling(Path dir,Object name){
    if(dir==null||!(name instanceof String s)||!s.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,63}"))throw refused();
    var p=dir.resolve(s);if(!dir.equals(p.getParent()))throw refused();return p;
  }
  static byte[] read(Path file)throws IOException{
    if(Files.isSymbolicLink(file)||!Files.isRegularFile(file,LinkOption.NOFOLLOW_LINKS)||Files.size(file)>MAX)throw refused();
    try(InputStream in=Files.newInputStream(file,LinkOption.NOFOLLOW_LINKS)){byte[] b=in.readNBytes(MAX+1);if(b.length>MAX||b.length==0)throw refused();return b;}
  }
  static PublicKey publicKey(Object encoded)throws GeneralSecurityException{
    if(!(encoded instanceof String s))throw refused();
    return KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(Base64.getDecoder().decode(s)));
  }
  /** PKCS#8 DER, Ed25519 only. */
  static PrivateKey privateKey(byte[] der)throws GeneralSecurityException{
    try{return KeyFactory.getInstance("Ed25519").generatePrivate(new PKCS8EncodedKeySpec(der));}finally{Arrays.fill(der,(byte)0);}
  }
  static void pair(PrivateKey privateKey,PublicKey publicKey)throws GeneralSecurityException{
    byte[] challenge=new byte[32];new SecureRandom().nextBytes(challenge);
    var s=Signature.getInstance("Ed25519");s.initSign(privateKey);s.update(challenge);byte[] sig=s.sign();
    var v=Signature.getInstance("Ed25519");v.initVerify(publicKey);v.update(challenge);if(!v.verify(sig))throw refused();
  }
  /** The exact file content, UTF-8, printable ASCII only (no trailing newline accepted). */
  static char[] password(byte[] raw){
    try{for(byte b:raw)if(b<0x21||b>0x7e)throw refused();
      var chars=StandardCharsets.US_ASCII.decode(java.nio.ByteBuffer.wrap(raw));char[] out=new char[chars.remaining()];chars.get(out);return out;}
    finally{Arrays.fill(raw,(byte)0);}
  }
}
