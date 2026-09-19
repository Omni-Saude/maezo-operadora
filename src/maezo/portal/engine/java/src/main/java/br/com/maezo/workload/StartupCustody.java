package br.com.maezo.workload;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;

/** ADR-0053: independently verify transport artifact custody before policy admission. */
final class StartupCustody {
  final Path root, policyPath;
  final String policyDigest;
  final int port;
  final List<Map<String,Object>> files;

  private StartupCustody(Path root,Path policyPath,String digest) {
    this.root=root;this.policyPath=policyPath;this.policyDigest=digest;
    var document=Json.parse(BoundaryPolicy.read(policyPath,digest));
    Json.keys(document,"protocol","tenant","environment","engine_name","not_before","expires_at",
        "roots","peers","files","listener_port","max_tasks","max_lock_millis","max_poll_millis");
    if(!"maezo.engine-boundary.v1".equals(document.get("protocol")))throw Refused.unavailable();
    long number=Json.number(document,"listener_port");
    if(number<1 || number>65535)throw Refused.unavailable();
    port=(int)number;
    var entries=new ArrayList<Map<String,Object>>();var paths=new HashSet<String>();
    for(Object item:Json.list(document.get("files"))) {
      var entry=Json.object(item);Json.keys(entry,"path","sha256");
      if(!paths.add(Json.string(entry,"path")))throw Refused.unavailable();
      BoundaryPolicy.readFile(entry);entries.add(Map.copyOf(entry));
    }
    files=List.copyOf(entries);
    current();
  }

  static StartupCustody environment() {
    String base=System.getenv("CATALINA_BASE"), file=System.getenv("MAEZO_ENGINE_BOUNDARY_FILE"),
        digest=System.getenv("MAEZO_ENGINE_BOUNDARY_SHA256");
    if(base==null || file==null || digest==null || BoundaryPolicyV2.configured())throw Refused.unavailable();
    return load(Path.of(base),Path.of(file),digest);
  }

  static StartupCustody load(Path root,Path file,String digest) {
    try {
      if(!root.isAbsolute() || !root.equals(root.toRealPath()))throw Refused.unavailable();
      return new StartupCustody(root,file,digest);
    } catch(java.io.IOException e) {throw Refused.unavailable();}
  }

  void current() {
    BoundaryPolicy.read(policyPath,policyDigest);
    for(var entry:files)BoundaryPolicy.readFile(entry);
    SecureLayout.verifyArtifacts(files,port,root);
  }
}
