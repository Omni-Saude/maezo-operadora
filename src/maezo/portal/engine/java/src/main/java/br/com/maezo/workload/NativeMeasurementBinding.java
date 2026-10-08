package br.com.maezo.workload;

import java.lang.reflect.Modifier;
import java.nio.file.*;
import java.util.*;

/** One fixed concrete TestOnly class. No registry, factory, arbitrary reflection or authority callback. */
final class NativeMeasurementBinding {
  private static final String CLASS="br.com.maezo.workload.ProviderNativeIndependentMeasurement";
  private static final String ENTRY="br/com/maezo/workload/ProviderNativeIndependentMeasurement.class";
  private static Bound bound;
  private record Bound(NativeMeasurementConfiguration config,NativeMeasurementPort port,Path root,
      Map<String,Object> configFile,Map<String,Object> jarFile,Class<?> concrete) {}
  private NativeMeasurementBinding(){}
  static synchronized void startup(StartupCustody custody,Map<String,Object> boundary) {
    var found=custody.files.stream().filter(f->Path.of(Json.string(f,"path")).getFileName().toString()
        .equals(NativeMeasurementConfiguration.FILE)).toList();
    if(found.isEmpty())return;
    require(found.size()==1 && bound==null);
    var config=NativeMeasurementConfiguration.load(found.get(0),boundary);config.timeOnly();
    var admission=RuntimeObservationAdmission.named(custody.files,RuntimeObservationAdmission.ADMISSION_FILE);
    var observed=Json.parse(RuntimeObservationAdmission.readProtected(Path.of(Json.string(admission,"path")),Json.string(admission,"sha256"),65536));
    require(config.observationAdmissionSha256().equals(admission.get("sha256"))
        && config.supportJarSha256().equals(observed.get("support_jar_sha256"))
        && config.originalDeadline().equals(observed.get("original_deadline"))
        && config.sessionNonce().equals(observed.get("nonce"))
        && config.candidateSha().equals(observed.get("candidate_sha"))
        && config.expectedImageId().equals(observed.get("expected_image_id"))
        && config.observationSchemaSha256().equals(observed.get("observation_schema_sha256"))
        && config.rootPgPrebootWitnessSha256().equals(Json.object(observed.get("expected_database")).get("root_pg_preboot_witness_sha256")));
    Path jar=custody.root.resolve("lib/provider-auth-test-support.jar");
    var refs=custody.files.stream().filter(f->jar.toString().equals(f.get("path"))
        && config.supportJarSha256().equals(f.get("sha256"))).toList();require(refs.size()==1);
    try {
      require(System.getProperty("os.name").equals("Linux"));
      require(config.jvmUid()==((Number)Files.getAttribute(Path.of("/proc/self"),"unix:uid")).longValue()
          && config.jvmGid()==((Number)Files.getAttribute(Path.of("/proc/self"),"unix:gid")).longValue());
      String schema=RuntimeDefinitionObservation.resourceDigest(NativeMeasurementConfiguration.class,
          NativeMeasurementConfiguration.SCHEMA,custody.root.resolve("lib/maezo-human-command.jar"));
      require(schema.equals(config.protocolSchemaSha256()));
      config.timeOnly();BoundaryPolicy.readFile(refs.get(0),custody.root,java.time.Instant.parse(config.originalDeadline()).toEpochMilli());config.timeOnly();
      Class<?> concrete=Class.forName(CLASS,false,NativeMeasurementBinding.class.getClassLoader());
      verifyType(concrete,jar,config);
      var constructors=concrete.getConstructors();
      require(constructors.length==1 && Arrays.equals(constructors[0].getParameterTypes(),new Class<?>[]{NativeMeasurementConfiguration.class}));
      var instance=concrete.getConstructor(NativeMeasurementConfiguration.class).newInstance(config);
      require(instance instanceof NativeMeasurementPort);config.timeOnly();verifyType(concrete,jar,config);
      BoundaryPolicy.readFile(refs.get(0),custody.root,java.time.Instant.parse(config.originalDeadline()).toEpochMilli());config.timeOnly();
      bound=new Bound(config,(NativeMeasurementPort)instance,custody.root,Map.copyOf(found.get(0)),Map.copyOf(refs.get(0)),concrete);
      current(config);
    }catch(Exception failure){throw Refused.unavailable();}
  }
  private static void verifyType(Class<?> concrete,Path jar,NativeMeasurementConfiguration config)throws Exception {
    require(concrete.getName().equals(CLASS) && Modifier.isPublic(concrete.getModifiers()) && Modifier.isFinal(concrete.getModifiers())
        && concrete.getClassLoader()==NativeMeasurementBinding.class.getClassLoader()
        && concrete.getInterfaces().length==1 && concrete.getInterfaces()[0]==NativeMeasurementPort.class
        && concrete.getSuperclass()==Object.class
        && Path.of(concrete.getProtectionDomain().getCodeSource().getLocation().toURI()).equals(jar)
        && jar.equals(jar.toRealPath()));
    require(RuntimeDefinitionObservation.resourceDigest(concrete,ENTRY,jar).equals(config.instrumentClassSha256()));
    for(var field:concrete.getFields())require(!Modifier.isPublic(field.getModifiers()) || Modifier.isFinal(field.getModifiers()));
  }
  static synchronized void current(NativeMeasurementConfiguration config) {
    require(bound!=null && bound.config==config);config.timeOnly();
    require(config.digest().equals(bound.configFile.get("sha256"))
        && Arrays.equals(config.rawConfiguration(),RuntimeObservationAdmission.readProtected(
            Path.of(Json.string(bound.configFile,"path")),config.digest(),65536)));
    BoundaryPolicy.readFile(bound.jarFile,bound.root,java.time.Instant.parse(config.originalDeadline()).toEpochMilli());
    try{verifyType(bound.concrete,bound.root.resolve("lib/provider-auth-test-support.jar"),config);}
    catch(Exception failure){throw Refused.unavailable();}finally{config.timeOnly();}
  }
  static synchronized NativeMeasurementPort forObservation(RuntimeObservationAdmission admission,
      WorkloadServlet.VerifiedObservationRequest request) {
    var refs=admission.policy.files.stream().filter(f->Path.of(Json.string(f,"path")).getFileName().toString()
        .equals(NativeMeasurementConfiguration.FILE)).toList();
    if(refs.isEmpty())return null;
    require(refs.size()==1 && bound!=null);var config=bound.config;config.current();
    require(config.digest().equals(refs.get(0).get("sha256")) && config.requestSha256().equals(request.digest())
        && config.observationAdmissionSha256().equals(admission.digest)
        && config.sessionNonce().equals(admission.document.get("nonce"))
        && config.originalDeadline().equals(admission.document.get("original_deadline"))
        && config.tenant().equals(admission.policy.tenant) && config.environment().equals(admission.policy.environment)
        && config.engineName().equals(admission.policy.engine));
    return bound.port;
  }
  private static void require(boolean value){RuntimeObservationAdmission.require(value);}
}
