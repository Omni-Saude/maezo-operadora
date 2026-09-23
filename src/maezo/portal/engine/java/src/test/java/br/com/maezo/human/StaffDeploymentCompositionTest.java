package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.util.*;
import java.util.logging.*;
import org.cibseven.bpm.engine.ProcessEngineConfiguration;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.cibseven.bpm.engine.impl.cfg.ProcessEnginePlugin;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;

/** T1.1 (D-E): no file, tampered file, public-only log line, digest == Configuration.digest(). */
class StaffDeploymentCompositionTest {
  @TempDir Path dir;
  StaffCompositionFixture fixture;
  final List<LogRecord> logged=new ArrayList<>();
  final Handler capture=new Handler(){public void publish(LogRecord r){logged.add(r);}public void flush(){}public void close(){}};

  @BeforeEach void setUp()throws Exception{fixture=new StaffCompositionFixture(dir);StaffDeploymentComposition.LOG.addHandler(capture);}
  @AfterEach void tearDown(){StaffDeploymentComposition.LOG.removeHandler(capture);}

  static ProcessEngineConfigurationImpl engine(ProcessEnginePlugin...plugins){
    var c=(ProcessEngineConfigurationImpl)ProcessEngineConfiguration.createStandaloneInMemProcessEngineConfiguration().setProcessEngineName("human-it");
    c.setProcessEnginePlugins(new ArrayList<>(List.of(plugins)));return c;
  }
  /** Same order the engine uses: every plugin's preInit, in list order. */
  static void preInit(ProcessEngineConfigurationImpl c){for(var p:c.getProcessEnginePlugins())p.preInit(c);}
  HumanCommandPlugin human(){return new HumanCommandPlugin(new TestKeys().trust());}
  void refusedWith(Map<String,Object> tampered)throws Exception{fixture.write(tampered);refused();}
  void refused(){
    var c=engine(new StaffDeploymentComposition(fixture.file),human());
    var e=assertThrows(IllegalStateException.class,()->preInit(c));
    assertEquals("staff deployment composition refused",e.getMessage());assertTrue(logged.isEmpty(),"no digest line on refusal");
  }

  @Test void withoutAFileTheEngineDoesNotStart(){
    var c=engine(new StaffDeploymentComposition(null),human());
    var e=assertThrows(IllegalStateException.class,()->preInit(c));
    assertEquals("MAEZO_STAFF_COMPOSITION_FILE must be explicitly configured",e.getMessage());
    var absent=engine(new StaffDeploymentComposition(dir.resolve("absent.json")),human());
    assertThrows(IllegalStateException.class,()->preInit(absent));assertTrue(logged.isEmpty());
  }
  @Test void validCompositionReachesTheHumanPluginAndLogsOnlyThePublicDigest(){
    var human=human();var c=engine(new StaffDeploymentComposition(fixture.file),human);
    preInit(c);
    assertEquals(1,logged.size());var line=logged.get(0).getMessage();
    assertTrue(line.matches("staff_native_configuration_digest=[0-9a-f]{64}"),line);
    assertEquals("staff_native_configuration_digest="+fixture.digest,line);
    assertNull(logged.get(0).getParameters());assertNull(logged.get(0).getThrown());
    // The human plugin now holds it: a second injection is refused by its own setter.
    assertThrows(Rejected.class,()->human.setHumanAuthConfiguration(null));
  }
  @Test void digestIsConfigurationDigest()throws Exception{
    var composed=StaffDeploymentComposition.load(fixture.file);
    assertEquals(fixture.digest,composed.staff().digest());
    assertEquals("maezo_native",composed.staff().nativeSchema());assertEquals("cibseven",composed.staff().engineSchema());
  }
  @Test void tamperedFieldIsRefused()throws Exception{
    var t=fixture.copy();t.put("designation_digest","e".repeat(64));refusedWith(t);
    t=fixture.copy();t.put("native_schema","maezo_other");refusedWith(t);
    t=fixture.copy();t.put("configuration_digest","0".repeat(64));refusedWith(t);
    t=fixture.copy();var pins=new TreeMap<>(PortalReadModels.map(t.get("relation_pins")));
    pins.put("mzo_staff_case_grant",new TreeMap<String,Object>(Map.of("oid","99999","owner","maezo_native_schema_owner")));t.put("relation_pins",pins);refusedWith(t);
  }
  @Test void nonCanonicalUnknownOrMissingFieldsAreRefused()throws Exception{
    Files.writeString(fixture.file,new String(Jcs.canonical(fixture.composition))+"\n");refused();
    var t=fixture.copy();t.put("extra","x");refusedWith(t);
    t=fixture.copy();t.remove("engine_schema");refusedWith(t);
    t=fixture.copy();t.put("schema","staff-deployment-composition.v2");refusedWith(t);
    t=fixture.copy();t.put("native_schema","public");refusedWith(t);
    t=fixture.copy();t.put("engine_schema","maezo_native");refusedWith(t);
    t=fixture.copy();var pins=new TreeMap<>(PortalReadModels.map(t.get("relation_pins")));pins.remove("mzo_staff_case_grant");t.put("relation_pins",pins);refusedWith(t);
  }
  @Test void secretFilesMustBeMatchingPlainSiblings()throws Exception{
    var t=fixture.copy();t.put("result_private_key_file","../native-result.pk8");refusedWith(t);
    t=fixture.copy();t.put("result_private_key_file","/etc/passwd");refusedWith(t);
    t=fixture.copy();t.put("result_private_key_file","absent.pk8");refusedWith(t);
    var other=java.security.KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
    Files.write(dir.resolve("other.pk8"),other.getPrivate().getEncoded());
    t=fixture.copy();t.put("result_private_key_file","other.pk8");refusedWith(t);
    fixture.write(fixture.composition);Files.writeString(dir.resolve("auth-signing.password"),"wrong-password");refused();
    Files.writeString(dir.resolve("auth-signing.password"),"with-newline\n");refused();
  }
  @Test void wrongPluginOrderIsRefused(){
    var c=engine(human(),new StaffDeploymentComposition(fixture.file));
    assertThrows(IllegalStateException.class,()->c.getProcessEnginePlugins().get(1).preInit(c));
    var alone=engine(new StaffDeploymentComposition(fixture.file));
    assertThrows(IllegalStateException.class,()->preInit(alone));
    var twice=engine(new StaffDeploymentComposition(fixture.file),human(),human());
    assertThrows(IllegalStateException.class,()->preInit(twice));assertTrue(logged.isEmpty());
  }
}
