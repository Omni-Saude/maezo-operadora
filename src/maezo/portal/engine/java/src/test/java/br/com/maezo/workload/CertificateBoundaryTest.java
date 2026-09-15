package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.security.cert.X509Certificate;
import java.time.Instant;
import java.util.*;
import jakarta.servlet.DispatcherType;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Public synthetic certificates and servlet-only doubles. This is not engine integration. */
class CertificateBoundaryTest {
  @TempDir Path temp;
  @Test void validCertificateAuthenticatesExactImmutableIdentity()throws Exception {
    var policy=Fixtures.load(temp,Fixtures.manifest(temp));
    var peer=policy.authenticate(Fixtures.requestProxy(Fixtures.requestValues()));
    assertEquals("fixture-helena",peer.engineUser());assertEquals(Fixtures.identity(),peer.identity());
  }
  @Test void forwardedCertificateAndPrincipalNeverAuthenticate()throws Exception {
    var policy=Fixtures.load(temp,Fixtures.manifest(temp));var r=Fixtures.requestValues();
    r.remove("attribute:jakarta.servlet.request.X509Certificate");r.put("header:X-Forwarded-Client-Cert","fixture");r.put("header:Authorization","Basic fixture");
    r.put("header:X-Tenant-Id","tenant-test");r.put("header:X-Principal-Id","fixture-helena");
    assertThrows(Refused.class,()->policy.authenticate(Fixtures.requestProxy(r)));
  }
  @ParameterizedTest @ValueSource(strings={"wrong-client","no-eku","expired"})
  void certificateAlternativesRefuse(String name)throws Exception {
    var policy=Fixtures.load(temp,Fixtures.manifest(temp));var r=Fixtures.requestValues();
    r.put("attribute:jakarta.servlet.request.X509Certificate",new X509Certificate[]{Fixtures.certificate(name),Fixtures.certificate("ca")});
    assertThrows(Refused.class,()->policy.authenticate(Fixtures.requestProxy(r)));
  }
  @ParameterizedTest @ValueSource(strings={"isSecure","getScheme","getLocalPort"})
  void noHttpOrWrongListenerFallback(String name)throws Exception {
    var policy=Fixtures.load(temp,Fixtures.manifest(temp));var r=Fixtures.requestValues();
    r.put(name,switch(name){case "isSecure"->false;case "getLocalPort"->8080;default->"http";});
    assertThrows(Refused.class,()->policy.authenticate(Fixtures.requestProxy(r)));
  }
  @Test void policyMountRemovalAndSameBytesRestoration()throws Exception {
    var policy=Fixtures.load(temp,Fixtures.manifest(temp));byte[] original=Files.readAllBytes(policy.path);Files.delete(policy.path);
    assertThrows(Refused.class,()->policy.authenticate(Fixtures.requestProxy(Fixtures.requestValues())));
    Files.write(policy.path,original);assertDoesNotThrow(()->policy.authenticate(Fixtures.requestProxy(Fixtures.requestValues())));
  }
  @Test void caMountRemovalRefusesCachedIdentity()throws Exception {
    var policy=Fixtures.load(temp,Fixtures.manifest(temp));Files.delete(temp.resolve("ca.pem"));
    assertThrows(Refused.class,()->policy.authenticate(Fixtures.requestProxy(Fixtures.requestValues())));
  }
  @Test void policyTamperingHasNoFallback()throws Exception {
    var policy=Fixtures.load(temp,Fixtures.manifest(temp));Files.writeString(policy.path,"{}");assertThrows(Refused.class,()->policy.current(null));
  }
  @ParameterizedTest @ValueSource(strings={"tenant","environment"})
  void wrongDeploymentIdentityCannotLoad(String field)throws Exception {
    var manifest=Fixtures.manifest(temp);manifest.put(field,"wrong");assertThrows(Refused.class,()->Fixtures.load(temp,manifest));
  }
  @Test void expiredTrustCannotLoad()throws Exception {
    var manifest=Fixtures.manifest(temp);manifest.put("expires_at",Instant.now().getEpochSecond()-1);assertThrows(Refused.class,()->Fixtures.load(temp,manifest));
  }
  @Test void engineVersionIsOnlyRawAllowedRoute()throws Exception {
    var r=Fixtures.requestValues();r.put("getRequestURI","/engine-rest/version");r.put("getServletPath","");r.put("getPathInfo","/version");r.put("getMethod","GET");
    assertDoesNotThrow(()->BoundaryFilter.route(Fixtures.requestProxy(r),"observer"));
  }
  @Test void validTypedPostAndReadinessRoutes()throws Exception {
    var r=Fixtures.requestValues();assertDoesNotThrow(()->BoundaryFilter.route(Fixtures.requestProxy(r),"nonhuman"));
    r.put("getRequestURI","/engine-rest/maezo/v1/readiness");r.put("getPathInfo","/v1/readiness");r.put("getMethod","GET");
    assertDoesNotThrow(()->BoundaryFilter.route(Fixtures.requestProxy(r),"nonhuman"));
  }
  @ParameterizedTest @ValueSource(strings={"human-relay","observer","bootstrap","deployment"})
  void otherPurposesCannotUseWorkloadOperations(String purpose)throws Exception {assertThrows(Refused.class,()->BoundaryFilter.route(Fixtures.requestProxy(Fixtures.requestValues()),purpose));}
  @ParameterizedTest @ValueSource(strings={"/engine-rest/task/t/complete","/engine-rest/task/t/claim","/engine-rest/task/t/variables","/engine-rest/task/t/localVariables/x/data","/engine-rest/execution/x/localVariables","/engine-rest/process-instance/x/variables","/engine-rest/process-instance/x/modification","/engine-rest/process-instance/restart","/engine-rest/deployment/create","/engine-rest/message","/engine-rest/external-task/fetchAndLock","/engine-rest/external-task/x/complete","/engine-rest/engine","/engine-rest/identity/verify","/engine-rest/engine/default/identity/verify","/engine-rest/engine/default/task/x/complete","/camunda/api/engine/engine/default/task/x/complete","/camunda/api/tasklist/x","/camunda/api/cockpit/x","/camunda/api/admin/x","/manager/html","/host-manager/html","/unknown/servlet"})
  void aliasesAndRawMutationsRefuse(String uri)throws Exception {
    var r=Fixtures.requestValues();r.put("getRequestURI",uri);r.put("getContextPath","");r.put("getServletPath",uri);r.put("getPathInfo","");
    assertThrows(Refused.class,()->BoundaryFilter.route(Fixtures.requestProxy(r),"nonhuman"));
  }
  @ParameterizedTest @ValueSource(strings={"GET","PUT","PATCH","DELETE","HEAD","OPTIONS","TRACE"})
  void unknownMethodRefuses(String method)throws Exception {var r=Fixtures.requestValues();r.put("getMethod",method);assertThrows(Refused.class,()->BoundaryFilter.route(Fixtures.requestProxy(r),"nonhuman"));}
  @ParameterizedTest @ValueSource(strings={"application/json; charset=UTF-8","multipart/form-data","application/octet-stream","text/plain","application/x-www-form-urlencoded"})
  void alternateContentTypeRefuses(String type)throws Exception {var r=Fixtures.requestValues();r.put("getContentType",type);assertThrows(Refused.class,()->BoundaryFilter.route(Fixtures.requestProxy(r),"nonhuman"));}
  @ParameterizedTest @ValueSource(strings={"FORWARD","INCLUDE","ERROR","ASYNC"})
  void redispatchRefuses(String dispatcher)throws Exception {var r=Fixtures.requestValues();r.put("getDispatcherType",DispatcherType.valueOf(dispatcher));assertThrows(Refused.class,()->BoundaryFilter.route(Fixtures.requestProxy(r),"nonhuman"));}
  @ParameterizedTest @ValueSource(strings={"/engine-rest/maezo/%76%31/operations","/engine-rest/maezo/../maezo/v1/operations","/engine-rest/maezo//v1/operations","/engine-rest/maezo/v1/operations;anything","/engine-rest/maezo/v1/operations%00"})
  void encodedAndAmbiguousPathRefuses(String path)throws Exception {var r=Fixtures.requestValues();r.put("getRequestURI",path);assertThrows(Refused.class,()->BoundaryFilter.route(Fixtures.requestProxy(r),"nonhuman"));}
  @Test void queryOverridesRefuse()throws Exception {var r=Fixtures.requestValues();r.put("getQueryString","tenantId=other");assertThrows(Refused.class,()->BoundaryFilter.route(Fixtures.requestProxy(r),"nonhuman"));}
  @Test void nativeSpiBridgeIsPublicAcrossTomcatClassloaders()throws Exception {
    assertTrue(java.lang.reflect.Modifier.isPublic(WorkloadPlugin.class.getMethod("nativePeer",jakarta.servlet.http.HttpServletRequest.class,String.class).getModifiers()));
    assertTrue(org.cibseven.bpm.engine.rest.security.auth.AuthenticationProvider.class.isAssignableFrom(CertificateAuthenticationProvider.class));
  }
  @Test void explicitRotationAndRevocationHasNoOldPolicyFallback()throws Exception {
    var manifest=Fixtures.manifest(temp);var old=Json.object(Json.list(manifest.get("peers")).get(0));
    var replacement=new HashMap<>(old);var leaf=Fixtures.certificate("wrong-client");
    replacement.put("certificate_sha256",BoundaryPolicy.hashCertificate(leaf));
    replacement.put("spki_sha256",br.com.maezo.human.Jcs.digest(leaf.getPublicKey().getEncoded()));
    replacement.put("subject_dn",leaf.getSubjectX500Principal().getName());
    manifest.put("peers",List.of(old,replacement));var overlap=Fixtures.load(temp,manifest);
    var originalRequest=Fixtures.requestValues();var rotatedRequest=Fixtures.requestValues();
    rotatedRequest.put("attribute:jakarta.servlet.request.X509Certificate",new X509Certificate[]{leaf,Fixtures.certificate("ca")});
    assertEquals("fixture-helena",overlap.authenticate(Fixtures.requestProxy(originalRequest)).engineUser());
    assertEquals("fixture-helena",overlap.authenticate(Fixtures.requestProxy(rotatedRequest)).engineUser());
    manifest.put("peers",List.of(replacement));var revoked=Fixtures.load(temp,manifest);
    assertThrows(Refused.class,()->overlap.authenticate(Fixtures.requestProxy(rotatedRequest)));
    assertThrows(Refused.class,()->revoked.authenticate(Fixtures.requestProxy(originalRequest)));
    assertEquals("fixture-helena",revoked.authenticate(Fixtures.requestProxy(rotatedRequest)).engineUser());
  }
}
