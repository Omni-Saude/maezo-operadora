package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class StartupCustodyTest {
  @TempDir Path temp;
  BoundaryPolicy installed()throws Exception {
    var fixture=new LayoutTest();fixture.temp=temp;
    return fixture.policy(fixture.files());
  }

  @ParameterizedTest @ValueSource(strings={"tenant","environment"})
  void transportCustodyNeverAdmitsAnInconsistentIdentity(String field)throws Exception {
    var policy=installed();var document=new HashMap<>(policy.document);document.put(field,"foreign");
    byte[] bytes=Json.bytes(document);Files.write(policy.path,bytes);
    String digest=br.com.maezo.human.Jcs.digest(bytes);
    assertDoesNotThrow(()->StartupCustody.load(temp.toRealPath(),policy.path,digest));
    assertThrows(Refused.class,()->BoundaryPolicy.load(policy.path,digest));
  }

  @Test void missingBoundaryRootDoesNotBypassRealPolicyAdmission()throws Exception {
    var policy=installed();var root=Json.object(Json.list(policy.document.get("roots")).get(0));
    Files.delete(Path.of(Json.string(root,"path")));
    assertDoesNotThrow(()->StartupCustody.load(temp.toRealPath(),policy.path,policy.digest));
    assertThrows(Refused.class,()->BoundaryPolicy.load(policy.path,policy.digest));
  }

  @Test void changedActualTransportArtifactRefusesBeforePolicyAdmission()throws Exception {
    var policy=installed();var custody=StartupCustody.load(temp.toRealPath(),policy.path,policy.digest);
    Files.writeString(temp.resolve("conf/server.xml"),"<Server/>");
    assertThrows(Refused.class,custody::current);
  }

  @Test void policyRecoveryCannotChangeRetainedCustodyIdentity()throws Exception {
    var policy=installed();var custody=StartupCustody.load(temp.toRealPath(),policy.path,policy.digest);
    Files.writeString(policy.path,"{}");
    assertThrows(Refused.class,custody::current);
  }

  @Test void unsafeButConsistentlyPinnedTransportStillRefuses()throws Exception {
    var fixture=new LayoutTest();fixture.temp=temp;var files=fixture.files();
    files.compute("conf/server.xml",(k,v)->v.replace("scheme=\"https\"","scheme=\"http\""));
    var policy=fixture.policy(files);
    assertThrows(Refused.class,()->StartupCustody.load(temp.toRealPath(),policy.path,policy.digest));
  }
}
