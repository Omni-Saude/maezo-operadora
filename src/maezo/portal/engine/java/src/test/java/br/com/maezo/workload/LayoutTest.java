package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class LayoutTest {
  @TempDir Path temp;
  private Map<String,String> files() {
    String mappings="<url-pattern>/*</url-pattern><dispatcher>REQUEST</dispatcher><dispatcher>FORWARD</dispatcher><dispatcher>INCLUDE</dispatcher><dispatcher>ERROR</dispatcher><dispatcher>ASYNC</dispatcher>";
    return new HashMap<>(Map.of(
      "conf/server.xml","<Server><Service><Connector SSLEnabled=\"true\" scheme=\"https\" secure=\"true\" port=\"8443\"><SSLHostConfig certificateVerification=\"required\"/></Connector><Engine><Host autoDeploy=\"false\"/></Engine></Service></Server>",
      "conf/web.xml","<web-app><filter><filter-name>maezo-boundary</filter-name><filter-class>br.com.maezo.workload.BoundaryFilter</filter-class></filter><filter-mapping><filter-name>maezo-boundary</filter-name>"+mappings+"</filter-mapping></web-app>",
      "conf/bpm-platform.xml","<bpm-platform><process-engine><properties><property name=\"authorizationEnabled\">true</property></properties><plugins><plugin><class>br.com.maezo.human.HumanCommandPlugin</class></plugin><plugin><class>br.com.maezo.workload.WorkloadPlugin</class></plugin></plugins></process-engine></bpm-platform>",
      "webapps/engine-rest/WEB-INF/web.xml","<web-app><filter><filter-name>maezo-native-auth</filter-name><filter-class>org.cibseven.bpm.engine.rest.security.auth.ProcessEngineAuthenticationFilter</filter-class><init-param><param-name>authentication-provider</param-name><param-value>br.com.maezo.workload.CertificateAuthenticationProvider</param-value></init-param></filter><filter-mapping><filter-name>maezo-native-auth</filter-name>"+mappings+"</filter-mapping></web-app>",
      "webapps/maezo-human/WEB-INF/web.xml","<web-app/>","webapps/camunda/WEB-INF/web.xml","<web-app metadata-complete=\"true\"><absolute-ordering/><servlet><servlet-class>br.com.maezo.workload.ClosedServlet</servlet-class></servlet><servlet-mapping><url-pattern>/*</url-pattern></servlet-mapping></web-app>"));
  }
  private BoundaryPolicy policy(Map<String,String> files)throws Exception {
    var manifest=Fixtures.manifest(temp);var pins=new ArrayList<Object>();
    for(var entry:files.entrySet()) {
      Path file=temp.toRealPath().resolve(entry.getKey());Files.createDirectories(file.getParent());Files.writeString(file,entry.getValue());
      pins.add(Map.of("path",file.toString(),"sha256",br.com.maezo.human.Jcs.digest(Files.readAllBytes(file))));
    }
    manifest.put("files",pins);return Fixtures.load(temp,manifest);
  }
  @Test void validPinnedLayoutAccepted()throws Exception {var policy=policy(files());assertDoesNotThrow(()->SecureLayout.verify(policy,temp.toRealPath()));}
  @ParameterizedTest @ValueSource(strings={"optional","HTTP","provider","dispatcher","authorization","plugin","mount"})
  void configurationMutationsFailClosed(String mutation)throws Exception {
    var files=files();
    switch(mutation) {
      case "optional" -> files.compute("conf/server.xml",(k,v)->v.replace("required","optional"));
      case "HTTP" -> files.compute("conf/server.xml",(k,v)->v.replace("<Engine>","<Connector port=\"8080\"/><Engine>"));
      case "provider" -> files.compute("webapps/engine-rest/WEB-INF/web.xml",(k,v)->v.replace("br.com.maezo.workload.CertificateAuthenticationProvider","org.cibseven.bpm.engine.rest.security.auth.impl.PseudoAuthenticationProvider"));
      case "dispatcher" -> files.compute("conf/web.xml",(k,v)->v.replace("<dispatcher>ASYNC</dispatcher>",""));
      case "authorization" -> files.compute("conf/bpm-platform.xml",(k,v)->v.replace(">true<",">false<"));
      case "plugin" -> files.compute("conf/bpm-platform.xml",(k,v)->v.replace("br.com.maezo.workload.WorkloadPlugin","unknown"));
      case "mount" -> files.remove("webapps/camunda/WEB-INF/web.xml");
    }
    var policy=policy(files);assertThrows(Refused.class,()->SecureLayout.verify(policy,temp.toRealPath()));
  }
}
