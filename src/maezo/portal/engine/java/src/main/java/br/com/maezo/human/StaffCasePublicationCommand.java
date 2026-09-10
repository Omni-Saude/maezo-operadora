package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import static br.com.maezo.human.StaffCaseModels.*;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Signed staff source publication, actual same-TX CAS/receipt, no subject grant inference. */
public final class StaffCasePublicationCommand implements Command<StaffCasePublicationCommand.Result> {
  public static final class Result {
    byte[] frozen;boolean committed;Runnable current;
    public byte[] bytes(){if(!committed||frozen==null)throw unavailable();current.run();return frozen.clone();}
  }
  final StaffCaseInstallation.Configuration config;final byte[] raw;final String peer;
  final StaffCaseReadCommand.Q2Lease lease;
  StaffCasePublicationCommand(StaffCaseInstallation.Configuration config,byte[] raw,String peer,StaffCaseReadCommand.Q2Lease lease){
    if(raw==null||raw.length>MAX||config==null||lease==null)throw invalid();this.config=config;this.raw=raw.clone();this.peer=peer;this.lease=lease;
  }
  @Override public Result execute(CommandContext context){
    lease.admission.requireCurrent();
    var read=new PortalReadCommand(lease.trust,lease.admission,()->lease.admission.requireCurrent());
    read.context=context;read.db=new PortalReadStore(context,lease.trust,lease.admission.statementTimeoutSeconds());
    read.ceiling("native_admission",lease.admission.providerRef(),lease.admission.providerRevision(),lease.admission.capabilityDigest(),lease.admission.observedAt(),lease.admission.validUntil());
    read.db.lockTenant();
    var store=new StaffCaseStore(context,config.authScope(),lease.admission.statementTimeoutSeconds(),config.nativeRole(),config.relationPins());
    store.auth.lock();var installed=new StaffCaseInstallation(config,store,lease.admission);
    var request=installed.signedPublication(raw,peer);String id=str(request,"publication_id"),requestDigest=hash(request);
    // Current importer is independently authenticated for this exact source. An
    // identical historical command recovers only its technical receipt; it never
    // replays old policy/grant admission or changes an effective pointer.
    var recovered=store.receipt(id,requestDigest);
    var checks=new ArrayList<Runnable>();Map<String,Object> receipt;
    if(recovered!=null)receipt=recovered;
    else {
      var payload=obj(request,"payload");String kind=str(request,"kind");
      if(kind.equals("case_grant")) {
        var witness=obj(request,"membership_witness");
        var membership=installed.membership(read,witness,null);String membershipDigest=hash(membership);
        checks.add(()->{if(!membershipDigest.equals(hash(installed.membership(read,witness,null))))throw conflict();});
        var identityReader=new NativeCaseIdentityReader(store.auth);var identity=identityReader.read(str(payload,"case_ref"));String identityDigest=hash(identity);
        var pins=store.policyPins(payload);
        installed.grant(request,obj(witness,"actor"),obj(identity,"identity"),pins);
        String pinDigest=hash(pins);checks.add(()->{
          var current=store.policyPins(payload);if(!pinDigest.equals(hash(current)))throw conflict();
          for(var pin:current)installed.policy(obj(pin,"head"));
          if(!identityDigest.equals(hash(identityReader.read(str(payload,"case_ref")))))throw conflict();
          store.requireRecordedPolicies(store.grant(str(payload,"grant_ref")),current);
        });
      } else if(kind.equals("policy_head")) {
        installed.policyStatement(payload);
        installed.proof(obj(request,"proof"),withoutProof(request,"proof"),"case_issuer","staff_policy_head",str(request,"source_ref"),str(payload,"policy_ref"));
        checks.add(()->{var current=store.policyHead(str(payload,"policy_ref"));
          if(current==null||!hash(payload).equals(current.get("head_digest")))throw conflict();installed.policyStatement(payload);});
      } else {
        // Explicit issuer-authorized grant tombstone; no policy revocation alias.
        installed.proof(obj(request,"proof"),withoutProof(request,"proof"),"case_issuer","staff_case_grant",str(request,"source_ref"),null);
      }
      installed.fresh(request,"observed_at","valid_until");installed.current();
      Instant committedAt=installed.current();
      receipt=record("schema","staff-case-publication-receipt.v1","publication_id",id,"request_digest",requestDigest,
        "scope",store.scope,"source_ref",request.get("source_ref"),"source_revision",request.get("source_revision"),
        "payload_digest",request.get("payload_digest"),"disposition","committed","committed_at",time(committedAt),
        "native_receipt_ref",UUID.randomUUID().toString(),"valid_until",time(installed.until()));
      receipt.put("proof",installed.resultProof(receipt));shape("receipt",receipt);
      store.publish(request,receipt);
    }
    var out=new Result();out.frozen=bounded(receipt);out.current=()->{installed.current();read.guard();};out.current.run();
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
      out.current.run();for(Runnable check:checks)check.run();installed.finalDesignation();out.current.run();
    });
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->out.committed=true);
    return out;
  }
}
