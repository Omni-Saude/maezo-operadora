package br.com.maezo.human;

import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.AbstractProcessEnginePlugin;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;

/** Test-support JAR only: supplies AUTH to the existing receiver, never staff or SQL installation. */
public final class ProviderAuthTestComposition extends AbstractProcessEnginePlugin {
  private boolean composed;
  static IllegalStateException refused() { return new IllegalStateException("TestOnly AUTH composition refused"); }
  static byte[] protectedFile(Path file, String expected) throws Exception {
    if (!file.isAbsolute() || !file.normalize().equals(file) || Files.isSymbolicLink(file)
        || !Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) || Files.size(file) > 262144) throw refused();
    long currentUid=new com.sun.security.auth.module.UnixSystem().getUid();
    Path parent=file.getParent();
    if(parent==null||Files.isSymbolicLink(parent)||!Files.isDirectory(parent,LinkOption.NOFOLLOW_LINKS))throw refused();
    for(Path ancestor=parent;ancestor!=null;ancestor=ancestor.getParent())if(Files.isSymbolicLink(ancestor))throw refused();
    for(Path item:List.of(parent,file)) {
      long uid=((Number)Files.getAttribute(item,"unix:uid",LinkOption.NOFOLLOW_LINKS)).longValue();
      if(uid!=0&&uid!=currentUid)throw refused();
      var mode=Files.getPosixFilePermissions(item,LinkOption.NOFOLLOW_LINKS);
      if(mode.stream().anyMatch(p->p.name().startsWith("GROUP_")||p.name().startsWith("OTHERS_")))throw refused();
    }
    var permissions = Files.getPosixFilePermissions(file, LinkOption.NOFOLLOW_LINKS);
    if (permissions.stream().anyMatch(p -> p.name().startsWith("GROUP_") || p.name().startsWith("OTHERS_"))) throw refused();
    byte[] raw;
    try (var in = Files.newInputStream(file, LinkOption.NOFOLLOW_LINKS)) { raw = in.readNBytes(262145); }
    if (raw.length == 0 || raw.length > 262144 || !Jcs.digest(raw).equals(expected)) throw refused();
    return raw;
  }
  static void verifyProtectedFile(Path file,String expected) throws Exception {
    byte[] raw=protectedFile(file,expected);Arrays.fill(raw,(byte)0);
  }
  static Map<String,Object> artifact(Map<String,Object> ref) throws Exception {
    Jcs.keys(ref,"ref","path","sha256","media_type");
    byte[] raw=protectedFile(Path.of(Jcs.string(ref,"path")),Jcs.hash(ref,"sha256"));
    var value=Jcs.object(Jcs.parse(raw));
    if (!Arrays.equals(raw,Jcs.canonical(value))) throw refused();
    return value;
  }
  static String requiredEnvironment(String name) {
    String value=System.getenv(name); if(value==null||value.isBlank()) throw refused(); return value;
  }
  static Map<String,Object> admittedManifest(Map<String,Object> preliminary) throws Exception {
    Path path=Path.of(requiredEnvironment("MAEZO_PROVIDER_TESTONLY_RUNTIME_MANIFEST_FILE"));
    String hash=requiredEnvironment("MAEZO_PROVIDER_TESTONLY_VERIFIED_RUNTIME_MANIFEST_SHA256");
    byte[] raw=protectedFile(path,hash);var manifest=Jcs.object(Jcs.parse(raw));
    if(!Arrays.equals(raw,Jcs.canonical(manifest)))throw refused();
    Jcs.keys(manifest,"schema","candidate_sha","scope","java_settings","signing_pkcs12","signing_password",
      "owner_jdbc_config","action_specs","native_database_binding","lifecycle_configuration","origin",
      "owned_resources","issued_at","valid_until");
    if(!"provider-auth-testonly-owner-runtime-manifest.v1".equals(manifest.get("schema")))throw refused();
    for(String key:List.of("architecture_receipt","security_receipt")) {
      var envelope=artifact(Jcs.object(preliminary.get(key)));var record=Jcs.object(envelope.get("record"));
      long matches=PortalReadModels.list(record.get("evidence")).stream().map(Jcs::object)
        .filter(ref->path.toString().equals(ref.get("path"))&&hash.equals(ref.get("sha256"))).count();
      if(matches!=1)throw refused();
    }
    return manifest;
  }
  static Map<String,Object> admittedMeasurements(Map<String,Object> preliminary,Map<String,Object> manifest,
      Map<String,Object> scope,String candidate) throws Exception {
    var measured=artifact(Jcs.object(preliminary.get("measurements")));
    var q=AuthInstallation.qualification(preliminary.get("qualification"));
    if(!candidate.equals(measured.get("candidate_sha"))||!candidate.equals(manifest.get("candidate_sha"))
        ||!scope.equals(measured.get("scope"))||!scope.equals(manifest.get("scope"))
        ||!q.get("definition").equals(measured.get("definition"))
        ||!q.get("native_code_digest").equals(Jcs.object(measured.get("native_jar")).get("sha256"))
        ||!q.get("source_freeze_contract_digest").equals(measured.get("source_bundle_sha256")))throw refused();
    current(preliminary,manifest);return measured;
  }
  static void current(Map<String,Object> preliminary,Map<String,Object> manifest) {
    Instant now=Instant.now();var q=AuthInstallation.qualification(preliminary.get("qualification"));
    Instant start=PortalReadModels.time(manifest.get("issued_at")),end=PortalReadModels.time(manifest.get("valid_until"));
    if(start.isAfter(now.plusSeconds(5))||!now.isBefore(end)||!end.isAfter(start)
        ||java.time.Duration.between(start,end).toSeconds()>900
        ||!now.isBefore(PortalReadModels.time(q.get("valid_until")))
        ||PortalReadModels.time(q.get("valid_until")).isAfter(end))throw refused();
  }
  @Override public synchronized void preInit(ProcessEngineConfigurationImpl configuration) {
    if(composed) throw refused();
    try {
      Path file=Path.of(requiredEnvironment("MAEZO_PROVIDER_TESTONLY_COMPOSITION_FILE"));
      byte[] raw=protectedFile(file,requiredEnvironment("MAEZO_PROVIDER_TESTONLY_COMPOSITION_SHA256"));
      var c=Jcs.object(Jcs.parse(raw));
      if(!Arrays.equals(raw,Jcs.canonical(c))) throw refused();
      Jcs.keys(c,"schema","scope","audience","max_lifetime_seconds","timeout_seconds",
        "signing_pkcs12_file","signing_password_file","alias","key_id","issuer","signing_spki_sha256",
        "preliminary_qualification_sha256","source_candidate_sha","configuration_digest");
      if(!"provider-auth-testonly-java-composition.v2".equals(c.get("schema"))
          || !"provider-native-result".equals(c.get("alias"))
          || !Jcs.string(c,"source_candidate_sha").matches("[a-f0-9]{40}")) throw refused();
      var scope=AuthModels.validate("scope",c.get("scope"));
      if(!Jcs.ref(scope,"environment").startsWith("TestOnly-")
          ||!scope.get("engine_name").equals(configuration.getProcessEngineName())) throw refused();
      var without=new TreeMap<>(c);without.remove("configuration_digest");
      if(!Jcs.hash(c,"configuration_digest").equals(Jcs.digest(Jcs.canonical(without)))) throw refused();
      byte[] prelimRaw=protectedFile(file.getParent().resolve("preliminary-qualification.json"),Jcs.hash(c,"preliminary_qualification_sha256"));
      if(!Jcs.hash(c,"preliminary_qualification_sha256").equals(
          requiredEnvironment("MAEZO_PROVIDER_TESTONLY_VERIFIED_PRELIMINARY_SHA256")))throw refused();
      var prelim=Jcs.object(Jcs.parse(prelimRaw));
      Jcs.keys(prelim,"schema","authority_registry","measurements","architecture_receipt","security_receipt","readback_record","qualification");
      if(!"provider-auth-testonly-preliminary-qualification.v2".equals(prelim.get("schema"))) throw refused();
      var q=AuthInstallation.qualification(prelim.get("qualification"));
      var manifest=admittedManifest(prelim);
      admittedMeasurements(prelim,manifest,scope,Jcs.string(c,"source_candidate_sha"));
      var settings=new TreeMap<>(c);for(String key:List.of("preliminary_qualification_sha256","source_candidate_sha","configuration_digest"))settings.remove(key);
      if(!settings.equals(Jcs.object(manifest.get("java_settings"))))throw refused();
      var plugins=configuration.getProcessEnginePlugins();
      HumanCommandPlugin human=null;int own=-1,target=-1,count=0,self=0;
      for(int i=0;i<plugins.size();i++) {
        var p=plugins.get(i);
        if(p instanceof ProviderAuthTestComposition){self++;if(p==this)own=i;}
        if(p instanceof HumanCommandPlugin h){human=h;target=i;count++;}
        if(p instanceof StaffDeploymentComposition || p instanceof PortalReadPlugin) throw refused();
      }
      if(count!=1||self!=1||own<0||target<=own||human==null) throw refused();
      long lifetime=Jcs.seconds(c,"max_lifetime_seconds"),timeout=Jcs.seconds(c,"timeout_seconds");
      if(lifetime<1||lifetime>300||timeout<1||timeout>10) throw refused();
      Path key=StaffDeploymentComposition.sibling(file.getParent(),c.get("signing_pkcs12_file"));
      Path passwordFile=StaffDeploymentComposition.sibling(file.getParent(),c.get("signing_password_file"));
      // Secret sibling custody is enforced before HumanAuthConfiguration performs its actual PKCS12 key/cert proof.
      var keyRef=Jcs.object(manifest.get("signing_pkcs12"));var passwordRef=Jcs.object(manifest.get("signing_password"));
      if(!key.toString().equals(keyRef.get("path"))||!passwordFile.toString().equals(passwordRef.get("path")))throw refused();
      verifyProtectedFile(key,Jcs.hash(keyRef,"sha256"));
      char[] password=StaffDeploymentComposition.password(protectedFile(passwordFile,Jcs.hash(passwordRef,"sha256")));
      try {
        var installed=new HumanAuthConfiguration(scope,Jcs.ref(c,"audience"),lifetime,(int)timeout,key,password,
          Jcs.ref(c,"alias"),Jcs.ref(c,"key_id"),Jcs.ref(c,"issuer"),Jcs.hash(c,"signing_spki_sha256"));
        verifyProtectedFile(key,Jcs.hash(keyRef,"sha256"));verifyProtectedFile(passwordFile,Jcs.hash(passwordRef,"sha256"));
        current(prelim,manifest);human.setHumanAuthConfiguration(installed);
      } finally { Arrays.fill(password,'\0'); }
      composed=true;
    } catch(Exception failure) { throw refused(); }
  }
}
