package br.com.maezo.human;

import java.nio.file.*;
import java.sql.*;
import java.time.Instant;
import java.util.*;

/** Own-session CLI test support only. Existing AuthInstallation is the sole schema/designation API. */
public final class ProviderAuthTestOwner {
  private ProviderAuthTestOwner() {}
  public static void main(String[] args) {
    if(args.length!=2)throw ProviderAuthTestComposition.refused();
    try {
      Path requestFile=Path.of(args[0]),resultFile=Path.of(args[1]);
      byte[] raw=ProviderAuthTestComposition.protectedFile(requestFile,
        ProviderAuthTestComposition.requiredEnvironment("MAEZO_PROVIDER_TESTONLY_ACTION_SHA256"));
      var a=Jcs.object(Jcs.parse(raw));
      if(!Arrays.equals(raw,Jcs.canonical(a)))throw ProviderAuthTestComposition.refused();
      Jcs.keys(a,"schema","action","owner","candidate_sha","scope","binding","preliminary_qualification",
        "owner_jdbc_config","designation","revoked_key_id","expected_installation_revision","issued_at","valid_until");
      if(!"provider-auth-testonly-owner-action.v2".equals(a.get("schema"))
          || !Jcs.ref(a,"owner").startsWith("provider-") || !Jcs.ref(a,"candidate_sha").matches("[a-f0-9]{40}"))throw ProviderAuthTestComposition.refused();
      var scope=AuthModels.validate("scope",a.get("scope"));var binding=AuthInstallation.binding(a.get("binding"));
      if(!Jcs.ref(scope,"environment").startsWith("TestOnly-")
          || Jcs.seconds(a,"expected_installation_revision")!=PortalReadModels.number(scope.get("installation_revision")))throw ProviderAuthTestComposition.refused();
      current(a);
      var preliminaryRef=Jcs.object(a.get("preliminary_qualification"));
      // ROOT's consumer must verify signatures/readback and pin this exact artifact before invoking this owner utility.
      String admitted=ProviderAuthTestComposition.requiredEnvironment("MAEZO_PROVIDER_TESTONLY_VERIFIED_PRELIMINARY_SHA256");
      if(!admitted.equals(Jcs.hash(preliminaryRef,"sha256")))throw ProviderAuthTestComposition.refused();
      var preliminary=ProviderAuthTestComposition.artifact(preliminaryRef);
      Jcs.keys(preliminary,"schema","authority_registry","measurements","architecture_receipt","security_receipt","readback_record","qualification");
      if(!"provider-auth-testonly-preliminary-qualification.v2".equals(preliminary.get("schema")))throw ProviderAuthTestComposition.refused();
      var q=AuthInstallation.qualification(preliminary.get("qualification"));
      var manifest=ProviderAuthTestComposition.admittedManifest(preliminary);
      var measured=ProviderAuthTestComposition.admittedMeasurements(preliminary,manifest,scope,Jcs.ref(a,"candidate_sha"));
      if(!binding.equals(Jcs.object(measured.get("binding")))
          ||!a.get("owner_jdbc_config").equals(manifest.get("owner_jdbc_config")))throw ProviderAuthTestComposition.refused();
      var spec=PortalReadModels.record("action",a.get("action"),"designation",a.get("designation"),"revoked_key_id",a.get("revoked_key_id"));
      if(PortalReadModels.list(manifest.get("action_specs")).stream().filter(spec::equals).count()!=1)throw ProviderAuthTestComposition.refused();
      if(PortalReadModels.time(a.get("valid_until")).isAfter(PortalReadModels.time(q.get("valid_until")))
          ||PortalReadModels.time(a.get("valid_until")).isAfter(PortalReadModels.time(manifest.get("valid_until"))))throw ProviderAuthTestComposition.refused();
      var jdbc=ProviderAuthTestComposition.artifact(Jcs.object(a.get("owner_jdbc_config")));
      Jcs.keys(jdbc,"schema","url","user","password");
      if(!"provider-auth-testonly-owner-jdbc.v2".equals(jdbc.get("schema"))
          ||!binding.get("owner_role").equals(jdbc.get("user")))throw ProviderAuthTestComposition.refused();
      String url=Jcs.string(jdbc,"url");
      if(!url.startsWith("jdbc:postgresql://")||!url.contains("sslmode=verify-full")
          ||!url.contains("sslrootcert=")||url.contains("sslmode=disable")||url.contains("options="))throw ProviderAuthTestComposition.refused();
      String action=Jcs.ref(a,"action"),tenant=Jcs.ref(scope,"tenant");
      Map<String,Object> designation=null;
      switch(action) {
        case "install","readback" -> {if(a.get("designation")!=null||a.get("revoked_key_id")!=null)throw ProviderAuthTestComposition.refused();}
        case "designate" -> {if(a.get("designation")==null||a.get("revoked_key_id")!=null)throw ProviderAuthTestComposition.refused();designation=ProviderAuthTestComposition.artifact(Jcs.object(a.get("designation")));}
        case "revoke" -> {if(a.get("designation")!=null||a.get("revoked_key_id")==null)throw ProviderAuthTestComposition.refused();Jcs.ref(a,"revoked_key_id");}
        default -> throw ProviderAuthTestComposition.refused();
      }
      var properties=new Properties();properties.setProperty("user",Jcs.string(jdbc,"user"));properties.setProperty("password",Jcs.string(jdbc,"password"));
      properties.setProperty("connectTimeout","5");properties.setProperty("socketTimeout","5");
      Instant committedAt;
      try(var connection=DriverManager.getConnection(url,properties)) {
        connection.setAutoCommit(false);ConsumerEdgeInstallation.session(connection,binding,true);current(a);
        ProviderAuthTestComposition.current(preliminary,manifest);
        switch(action) {
          case "install" -> AuthInstallation.installSchema(connection,binding,scope,q);
          case "designate" -> AuthInstallation.designate(connection,tenant,designation,Jcs.seconds(a,"expected_installation_revision"));
          case "revoke" -> AuthInstallation.revoke(connection,tenant,Jcs.ref(a,"revoked_key_id"),Jcs.seconds(a,"expected_installation_revision"));
          case "readback" -> {}
          default -> throw ProviderAuthTestComposition.refused();
        }
        current(a);if(!Instant.now().isBefore(PortalReadModels.time(q.get("valid_until"))))throw ProviderAuthTestComposition.refused();
        connection.commit();
        // Host observation immediately after the real commit ACK, before any
        // connection close/readback I/O. This is not a PostgreSQL commit timestamp.
        committedAt=Instant.now();current(a);ProviderAuthTestComposition.current(preliminary,manifest);
      }
      // Fresh owner-authenticated connection checks actual committed rows; no seed or assumed success.
      Map<String,Object> row;String catalogDigest;Instant readBackAt;
      try(var connection=DriverManager.getConnection(url,properties)) {
        connection.setAutoCommit(false);ConsumerEdgeInstallation.session(connection,binding,true);
        row=ConsumerEdgeInstallation.one(connection,"SELECT SCOPE_,BINDING_,QUALIFICATION_,REV_,clock_timestamp() AS observed FROM MZO_AUTH_INSTALLATION WHERE TENANT_=?",tenant);
        if(!scope.equals(AuthStore.parse(row.get("scope_")))||!binding.equals(AuthStore.parse(row.get("binding_")))||!q.equals(AuthStore.parse(row.get("qualification_")))
            || ((Number)row.get("rev_")).longValue()!=Jcs.seconds(a,"expected_installation_revision"))throw ProviderAuthTestComposition.refused();
        AuthInstallation.tables(connection,binding);
        catalogDigest=catalogDigest(connection,binding);
        if(action.equals("designate")) {
          var d=ConsumerEdgeInstallation.one(connection,"SELECT DESIGNATION_ FROM MZO_AUTH_TRUST WHERE TENANT_=? AND KEY_ID_=?",tenant,Jcs.ref(designation,"key_id"));
          if(!designation.equals(AuthStore.parse(d.get("designation_"))))throw ProviderAuthTestComposition.refused();
        }
        if(action.equals("revoke"))ConsumerEdgeInstallation.one(connection,"SELECT KEY_ID_ FROM MZO_AUTH_REVOKED_KEY WHERE TENANT_=? AND KEY_ID_=?",tenant,Jcs.ref(a,"revoked_key_id"));
        current(a);ProviderAuthTestComposition.current(preliminary,manifest);connection.rollback();
        readBackAt=Instant.now();current(a);ProviderAuthTestComposition.current(preliminary,manifest);
      }
      Instant databaseObserved=((java.sql.Timestamp)row.get("observed")).toInstant();
      if(committedAt.isAfter(readBackAt)||Math.abs(java.time.Duration.between(databaseObserved,readBackAt).toMillis())>5000)
        throw ProviderAuthTestComposition.refused();
      var result=PortalReadModels.record("schema","provider-auth-testonly-owner-action-result.v2","action",action,"request_sha256",Jcs.digest(raw),
        "candidate_sha",a.get("candidate_sha"),"scope",scope,"binding_sha256",PortalReadModels.hash(binding),"preliminary_qualification_sha256",admitted,
        "outcome","COMMITTED_AND_READ_BACK","installation_revision",a.get("expected_installation_revision"),"installation_binding_sha256",PortalReadModels.hash(binding),
        "installation_qualification_sha256",PortalReadModels.hash(q),"designation_sha256",designation==null?null:PortalReadModels.hash(designation),
        "revoked_key_id",a.get("revoked_key_id"),"catalog_sha256",catalogDigest,"database_observed_at",PortalReadModels.time(databaseObserved),
        "committed_at",PortalReadModels.time(committedAt),"read_back_at",PortalReadModels.time(readBackAt));
      if(!resultFile.isAbsolute()||Files.exists(resultFile,LinkOption.NOFOLLOW_LINKS))throw ProviderAuthTestComposition.refused();
      current(a);ProviderAuthTestComposition.current(preliminary,manifest);
      try(var channel=Files.newByteChannel(resultFile,Set.of(StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE),
          java.nio.file.attribute.PosixFilePermissions.asFileAttribute(java.nio.file.attribute.PosixFilePermissions.fromString("r--------")))) {
        var bytes=java.nio.ByteBuffer.wrap(Jcs.canonical(result));while(bytes.hasRemaining())channel.write(bytes);
      }
      current(a);ProviderAuthTestComposition.current(preliminary,manifest);
    }catch(Exception failure){throw ProviderAuthTestComposition.refused();}
  }
  static String catalogDigest(Connection c,Map<String,Object> binding) {
    var rows=ConsumerEdgeInstallation.rows(c,"SELECT c.oid::text AS oid,c.relname,pg_get_userbyid(c.relowner) AS owner,c.relkind::text AS kind,c.relrowsecurity,c.relforcerowsecurity,COALESCE(c.relacl::text,'') AS acl FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=? AND c.relname LIKE 'mzo_auth_%' ORDER BY c.relname",Jcs.string(binding,"schema_name"));
    if(rows.size()!=AuthInstallation.TABLES.size())throw ProviderAuthTestComposition.refused();
    return PortalReadModels.hash(rows);
  }
  static void current(Map<String,Object> action) {
    Instant now=Instant.now(),issued=PortalReadModels.time(action.get("issued_at")),until=PortalReadModels.time(action.get("valid_until"));
    if(issued.isAfter(now.plusSeconds(5))||!now.isBefore(until)||!until.isAfter(issued)||java.time.Duration.between(issued,until).toSeconds()>900)throw ProviderAuthTestComposition.refused();
  }
}
