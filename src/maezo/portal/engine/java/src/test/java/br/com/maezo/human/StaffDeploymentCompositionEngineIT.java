package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import java.util.logging.*;
import org.cibseven.bpm.engine.ProcessEngineConfiguration;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;

/** T1.1 against a real PostgreSQL in the D-C2 layout (ACT_* in {@code cibseven}, {@code mzo_*} in
 * {@code maezo_native}, path {@code maezo_native,cibseven}): the embedded engine is built with
 * the composition BEFORE the human plugin, as in {@code Dockerfile.human}.
 *
 * <p>Without a file or with a tampered file the engine is never built. With the valid file the
 * composition runs, logs only the public digest and hands the configuration to the human plugin;
 * the boot then still stops, closed, at {@code PortalReadPlugin.staffLease} because no Q2
 * provider is installed here (T1.7a/C1): the staff profile does not come up half-configured.
 * Explicit integration lane (surefire excludes *EngineIT); settings as in AtomicEngineIT.
 */
@Tag("integration")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class StaffDeploymentCompositionEngineIT {
  static final String ENGINE="cibseven",NATIVE="maezo_native";
  String adminUrl,user,password,database;
  @TempDir Path dir;
  StaffCompositionFixture fixture;
  final List<LogRecord> logged=new ArrayList<>();
  final Handler capture=new Handler(){public void publish(LogRecord r){logged.add(r);}public void flush(){}public void close(){}};

  static String env(String name){String v=System.getenv(name);
    if(v==null||v.isBlank())throw new IllegalStateException("explicit integration setting missing: "+name);return v;}
  String url(String path){
    var m=java.util.regex.Pattern.compile("^(jdbc:postgresql://[^/?]+/)[^/?]+(\\?.*)?$").matcher(adminUrl);
    if(!m.matches())throw new IllegalStateException("integration admin URL must be jdbc:postgresql://host[:port]/database");
    String query=m.group(2)==null?"":m.group(2);
    return m.group(1)+database+(path==null?query:query+(query.isEmpty()?"?":"&")+"currentSchema="+path);
  }
  ProcessEngineConfigurationImpl engine(String path,String update,org.cibseven.bpm.engine.impl.cfg.ProcessEnginePlugin...plugins){
    var c=(ProcessEngineConfigurationImpl)ProcessEngineConfiguration.createStandaloneProcessEngineConfiguration()
      .setProcessEngineName("human-it").setJdbcDriver("org.postgresql.Driver").setJdbcUrl(url(path))
      .setJdbcUsername(user).setJdbcPassword(password).setDatabaseSchemaUpdate(update)
      .setJobExecutorActivate(false).setHistory("full");
    c.setEnforceHistoryTimeToLive(false);c.setMetricsEnabled(false);c.setAuthorizationEnabled(true);c.setTenantCheckEnabled(true);
    c.setProcessEnginePlugins(new ArrayList<>(List.of(plugins)));return c;
  }
  long actTables(String schema)throws SQLException{
    try(var c=DriverManager.getConnection(url(null),user,password);
        var s=c.prepareStatement("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=? AND c.relname LIKE 'act\\_%' AND c.relkind='r'")){
      s.setString(1,schema);try(var r=s.executeQuery()){r.next();return r.getLong(1);}}
  }

  @BeforeAll void install()throws Exception{
    adminUrl=env("MAEZO_HUMAN_IT_JDBC_URL");user=env("MAEZO_HUMAN_IT_DB_USER");password=env("MAEZO_HUMAN_IT_DB_PASSWORD");
    if(adminUrl.matches("(?i).*([?&])(currentSchema|options)=.*"))throw new IllegalStateException("integration admin URL must not override schema");
    database="t11_"+UUID.randomUUID().toString().replace("-","").substring(0,12);
    try(var c=DriverManager.getConnection(adminUrl,user,password);var s=c.createStatement()){s.execute("CREATE DATABASE "+database);}
    try(var c=DriverManager.getConnection(url(null),user,password);var s=c.createStatement()){
      s.execute("CREATE SCHEMA "+ENGINE);s.execute("CREATE SCHEMA "+NATIVE);}
    // Runbook order for an empty database (ADR-0060 Consequence 4): ACT_* created with the engine path alone.
    engine(ENGINE,ProcessEngineConfiguration.DB_SCHEMA_UPDATE_TRUE).buildProcessEngine().close();
    try(var c=DriverManager.getConnection(url(NATIVE),user,password);var s=c.createStatement();
        var in=getClass().getResourceAsStream("/human-schema-postgres.sql")){
      s.execute(new String(Objects.requireNonNull(in).readAllBytes(),StandardCharsets.UTF_8));
      s.execute("INSERT INTO MZO_HUMAN_TENANT VALUES('tenant-test',0)");}
    assertTrue(actTables(ENGINE)>40);assertEquals(0,actTables(NATIVE));assertEquals(0,actTables("public"));
    StaffDeploymentComposition.LOG.addHandler(capture);
  }
  @BeforeEach void fresh()throws Exception{logged.clear();fixture=new StaffCompositionFixture(Files.createTempDirectory(dir,"c"));}
  @AfterAll void drop()throws Exception{
    StaffDeploymentComposition.LOG.removeHandler(capture);if(database==null)return;
    try(var c=DriverManager.getConnection(adminUrl,user,password);var s=c.createStatement()){s.execute("DROP DATABASE IF EXISTS "+database+" WITH (FORCE)");}
  }
  ProcessEngineConfigurationImpl staffEngine(Path file,HumanCommandPlugin human){
    return engine(NATIVE+","+ENGINE,"false",new StaffDeploymentComposition(file),human);
  }

  @Test void noFileTheEngineDoesNotStart(){
    var e=assertThrows(IllegalStateException.class,()->staffEngine(null,new HumanCommandPlugin(new TestKeys().trust())).buildProcessEngine());
    assertEquals("MAEZO_STAFF_COMPOSITION_FILE must be explicitly configured",e.getMessage());assertTrue(logged.isEmpty());
  }
  @Test void tamperedFileTheEngineDoesNotStart()throws Exception{
    var t=fixture.copy();t.put("native_schema","maezo_other");fixture.write(t);
    var e=assertThrows(IllegalStateException.class,()->staffEngine(fixture.file,new HumanCommandPlugin(new TestKeys().trust())).buildProcessEngine());
    assertEquals("staff deployment composition refused",e.getMessage());assertTrue(logged.isEmpty());
  }
  @Test void validFileComposesLogsTheDigestAndTheStaffBootStillFailsClosedWithoutTheQ2Provider(){
    var human=new HumanCommandPlugin(new TestKeys().trust());var config=staffEngine(fixture.file,human);
    var e=assertThrows(RuntimeException.class,config::buildProcessEngine);
    assertFalse(e.getMessage()!=null&&e.getMessage().contains("staff deployment composition"),String.valueOf(e.getMessage()));
    assertEquals(1,logged.size());assertEquals("staff_native_configuration_digest="+fixture.digest,logged.get(0).getMessage());
    // preInit and postInit of the human plugin ran with the composed configuration: a second
    // injection is refused because the engine configuration is already bound.
    assertThrows(Rejected.class,()->human.setStaffCaseConfiguration(null,null));
  }
  @Test void withoutTheCompositionTheSameDatabaseBootsTheNonStaffEngine(){
    var engine=engine(NATIVE+","+ENGINE,"false",new HumanCommandPlugin(new TestKeys().trust())).buildProcessEngine();
    try{assertEquals("human-it",engine.getName());}finally{engine.close();}
    assertTrue(logged.isEmpty());
  }
}
