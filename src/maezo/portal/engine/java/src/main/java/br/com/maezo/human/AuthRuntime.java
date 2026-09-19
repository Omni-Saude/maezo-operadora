package br.com.maezo.human;

import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** Explicit E04 runtime binding. Shared servlet/plugin registers it only after preflight. */
final class AuthRuntime {
  final ProcessEngineConfigurationImpl configuration;
  final Map<String,Object> scope;
  final String audience;
  final long maxLifetime;
  final int timeout;
  final AuthResultSigner signer;
  final StaffCaseInstallation.Configuration staffConfiguration;
  private final Path loadedJar;
  AuthRuntime(ProcessEngineConfigurationImpl configuration,Map<String,Object> scope,String audience,
      long maxLifetime,int timeout,AuthResultSigner signer) {
    this(configuration,scope,audience,maxLifetime,timeout,signer,null);
  }
  AuthRuntime(ProcessEngineConfigurationImpl configuration,Map<String,Object> scope,String audience,
      long maxLifetime,int timeout,AuthResultSigner signer,StaffCaseInstallation.Configuration staff) {
    if(staff!=null&&!scope.equals(staff.authScope()))throw Rejected.denied();
    this.staffConfiguration=staff;
    AuthModels.validate("scope",scope);this.scope=PortalReadModels.copy(scope);
    this.configuration=configuration;this.audience=audience;this.maxLifetime=maxLifetime;this.timeout=timeout;this.signer=signer;
    if(signer==null||!scope.get("engine_name").equals(configuration.getProcessEngineName())
        ||!configuration.isAuthorizationEnabled()||!configuration.isTenantCheckEnabled())throw Rejected.denied();
    try {
      loadedJar=Path.of(AuthRuntime.class.getProtectionDomain().getCodeSource().getLocation().toURI());
      if(!Files.isRegularFile(loadedJar,LinkOption.NOFOLLOW_LINKS)||Files.isSymbolicLink(loadedJar))throw Rejected.denied();
    }catch(Exception failure){throw Rejected.denied();}
  }
  AuthStore lifecycle(org.cibseven.bpm.engine.impl.persistence.entity.ExecutionEntity execution) {
    if(!scope.get("tenant").equals(execution.getTenantId()))return null;
    var context=org.cibseven.bpm.engine.impl.context.Context.getCommandContext();
    var store=new AuthStore(context,scope,timeout,staffConfiguration);store.lock();
    var qualification=AuthInstallation.runtime(store);
    if(!Jcs.object(qualification.get("definition")).get("definition_id").equals(execution.getProcessDefinitionId()))return null;
    requireQualifiedCode(qualification);return store;
  }
  void requireQualifiedCode(Map<String,Object> qualification) {
    try {
      if(!Instant.now().isBefore(PortalReadModels.time(qualification.get("valid_until")))
          ||Files.isSymbolicLink(loadedJar)||!Files.isRegularFile(loadedJar,LinkOption.NOFOLLOW_LINKS)
          ||Files.size(loadedJar)>67108864||!Jcs.digest(Files.readAllBytes(loadedJar)).equals(qualification.get("native_code_digest")))throw Rejected.denied();
    }catch(java.io.IOException failure){throw Rejected.denied();}
    if(!Instant.now().isBefore(PortalReadModels.time(qualification.get("valid_until"))))throw Rejected.denied();
  }
  final class Invocation {
    final CommandContext context;
    final AuthStore store;
    final AuthTrust trust;
    final AuthEnvelope.Verified envelope;
    final Map<String,Object> qualification;
    final Instant qualificationUntil;
    Invocation(CommandContext context,byte[] raw,String peer,String purpose) {
      this.context=context;store=new AuthStore(context,scope,timeout,staffConfiguration);store.lock();
      qualification=AuthInstallation.runtime(store);qualificationUntil=PortalReadModels.time(qualification.get("valid_until"));
      br.com.maezo.workload.WorkloadPlugin.running().requireHumanAuthPeerSeparated(peer);
      trust=new AuthTrust(store,audience,maxLifetime);envelope=AuthEnvelope.verify(raw,trust,purpose,peer,Instant.now());
      current();
    }
    void current() {
      Instant now=Instant.now();envelope.current(now);signer.current(now);
      if(!now.isBefore(qualificationUntil))throw Rejected.denied();
      try {
        if(Files.isSymbolicLink(loadedJar)||!Files.isRegularFile(loadedJar,LinkOption.NOFOLLOW_LINKS)||Files.size(loadedJar)>67108864
            ||!Jcs.digest(Files.readAllBytes(loadedJar)).equals(qualification.get("native_code_digest")))throw Rejected.denied();
      }catch(java.io.IOException failure){throw Rejected.denied();}
      if(!scope.get("engine_name").equals(configuration.getProcessEngineName())||!configuration.isAuthorizationEnabled()||!configuration.isTenantCheckEnabled())throw Rejected.denied();
      // Recheck elapsed deadlines after local artifact I/O; no database/source round trip here.
      now=Instant.now();envelope.current(now);signer.current(now);if(!now.isBefore(qualificationUntil))throw Rejected.denied();
    }
    void definition(Map<String,Object> definition) {
      AuthModels.validate("definition",definition);
      if(!definition.equals(qualification.get("definition")))throw Rejected.denied();
      var repository=configuration.getRepositoryService();
      var deployed=repository.createProcessDefinitionQuery().processDefinitionId(Jcs.ref(definition,"definition_id")).singleResult();
      if(deployed==null||!scope.get("tenant").equals(deployed.getTenantId())||!"SP-OP-AUTH-001".equals(deployed.getKey())
          ||!definition.get("deployment_id").equals(deployed.getDeploymentId()))throw Rejected.denied();
      try(var stream=repository.getProcessModel(deployed.getId())) {
        if(!PortalReadModels.resourceMatches(stream,definition.get("definition_digest")))throw Rejected.denied();
      }catch(java.io.IOException failure){throw Rejected.denied();}
      current();
    }
    Result finish(java.util.function.Supplier<Map<String,Object>> committedRecord,Runnable check,Instant deadline) {
      var result=new Result();
      // Follow D5: one normal deferred CIB flush; final SQL occurs in COMMITTING, no manual flush.
      // Append this close hook after every synchronous lifecycle callback registered by the
      // effect. Their COMMITTING SQL is registered first; receipt/signature/freshness runs last.
      context.registerCommandContextListener(new org.cibseven.bpm.engine.impl.interceptor.CommandContextListener(){
        @Override public void onCommandContextClose(CommandContext closing) {
          context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
            Map<String,Object> record=committedRecord.get();
            var signed=signer.sign(trust,record,deadline.isBefore(qualificationUntil)?deadline:qualificationUntil);
            check.run();current();
            // Final clock follows all source/signing/qualification JAR work. No SQL after this guard.
            Instant finalNow=Instant.now();envelope.current(finalNow);signed.current(finalNow);
            if(!finalNow.isBefore(deadline))throw Rejected.denied();
            context.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,done->result.committed=signed.bytes());
          });
        }
        @Override public void onCommandFailed(CommandContext closing,Throwable failure){}
      });
      return result;
    }
  }
  static final class Result {
    private byte[] committed;
    byte[] bytes(){if(committed==null)throw EngineStore.unavailable();return committed.clone();}
  }
  byte[] execute(String path,byte[] raw,String peer) {
    if(raw==null||raw.length>65536)throw Rejected.invalid();
    String expected=switch(path) {
      case "/v1/auth-start"->"human-auth-start.v1";case "/v1/auth-documents"->"human-auth-documents.v1";
      case "/v1/auth-receipt"->"human-auth-receipt-query.v1";case "/v1/auth-document-context"->"human-auth-document-context-query.v1";
      case "/v1/auth-input-publication"->"human-auth-input-publication.v1";case "/v1/auth-publication-receipt"->"human-auth-publication-query.v1";
      default->throw Rejected.invalid();
    };
    // Routing-only shape guard; command execution still verifies the entire dedicated signature.
    if(!expected.equals(Jcs.object(Jcs.object(Jcs.parse(raw)).get("command")).get("schema")))throw Rejected.invalid();
    org.cibseven.bpm.engine.impl.interceptor.Command<Result> command=switch(path) {
      case "/v1/auth-start"->new HumanAuthStartCommand(this,raw,peer);
      case "/v1/auth-documents"->new HumanAuthDocumentCommand(this,raw,peer);
      case "/v1/auth-input-publication"->new AuthInputPublication(this,raw,peer);
      case "/v1/auth-publication-receipt"->new AuthReadCommand(this,raw,peer,"human-auth-publication-read");
      default->new AuthReadCommand(this,raw,peer,"human-auth-read");
    };
    return configuration.getCommandExecutorTxRequired().execute(command).bytes();
  }
  Invocation invoke(CommandContext context,byte[] raw,String peer,String purpose){return new Invocation(context,raw,peer,purpose);}
}
