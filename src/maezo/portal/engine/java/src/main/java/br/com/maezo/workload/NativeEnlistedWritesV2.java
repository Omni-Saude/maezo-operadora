package br.com.maezo.workload;

import java.util.*;
import org.apache.ibatis.mapping.*;
import org.apache.ibatis.session.*;

/** Fixed native v2 statements on the engine's enlisted session, never caller SQL. */
final class NativeEnlistedWritesV2 implements SqlSource {
  static final String PREFIX = "br.com.maezo.workload.nativeV2.";
  private final Configuration configuration;
  private final String sql;
  private final int arguments;
  private NativeEnlistedWritesV2(Configuration configuration, String sql, int arguments) {
    this.configuration=configuration;this.sql=sql;this.arguments=arguments;
  }
  static String identifier(String value) {
    if(value==null || !value.matches("[A-Za-z_][A-Za-z0-9_]{0,62}"))throw Refused.unavailable();
    return "\""+value+"\"";
  }
  static void install(Configuration config,String actSchema) {
    String act=identifier(actSchema)+".";
    Map<String,String> statements=new LinkedHashMap<>();
    for(String function:List.of("guard_runtime_v2","validate_guard_v2","read_receipt_v2","insert_acquisition_v2",
        "renew_acquisition_v2","close_acquisition_v2","append_receipt_v2","read_acquisitions_v2"))
      statements.put(function,"SELECT maezo_native_v2."+function+"(?::jsonb)::text");
    statements.put("connection", "SELECT json_build_object('session_user',session_user,'current_user',current_user,'login_oid',(SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=session_user),'database_oid',(SELECT oid::bigint FROM pg_catalog.pg_database WHERE datname=current_database()),'act_schema_oid',(SELECT oid::bigint FROM pg_catalog.pg_namespace WHERE nspname=?),'now',(extract(epoch FROM clock_timestamp())*1000)::bigint)::text");
    statements.put("candidates", "SELECT id_ FROM "+act+"act_ru_ext_task WHERE tenant_id_=? AND proc_def_id_=? AND topic_name_=? AND (lock_exp_time_ IS NULL OR lock_exp_time_<=clock_timestamp()) AND (suspension_state_ IS NULL OR suspension_state_=1) AND (retries_ IS NULL OR retries_>0) ORDER BY convert_to(id_,'UTF8') LIMIT ?");
    statements.put("task_rows", "SELECT json_build_object('task',id_,'tenant',tenant_id_,'definition',proc_def_id_,'process',proc_inst_id_,'execution',execution_id_,'topic',topic_name_,'worker',worker_id_,'expiry',CASE WHEN lock_exp_time_ IS NULL THEN NULL ELSE (extract(epoch FROM lock_exp_time_)*1000)::bigint END,'suspension',suspension_state_,'retries',retries_,'native_revision',rev_)::text FROM "+act+"act_ru_ext_task WHERE id_ IN (SELECT jsonb_array_elements_text(?::jsonb)) ORDER BY convert_to(id_,'UTF8')");
    statements.put("lock_roots", "SELECT id_ FROM "+act+"act_ru_execution WHERE tenant_id_=? AND id_ IN (SELECT jsonb_array_elements_text(?::jsonb)) ORDER BY convert_to(id_,'UTF8') FOR UPDATE");
    statements.put("lock_tasks", "SELECT id_ FROM "+act+"act_ru_ext_task WHERE tenant_id_=? AND id_ IN (SELECT jsonb_array_elements_text(?::jsonb)) ORDER BY convert_to(id_,'UTF8') FOR UPDATE");
    statements.put("timeouts", "SELECT json_build_object('lock',set_config('lock_timeout',?,true),'statement',set_config('statement_timeout',?,true))::text");
    synchronized(config) {
      for(var entry:statements.entrySet()) {
        String id=PREFIX+entry.getKey(),sql=entry.getValue();
        if(config.hasStatement(id,false)) {
          var old=config.getMappedStatement(id).getSqlSource();
          if(!(old instanceof NativeEnlistedWritesV2 v) || !v.sql.equals(sql))throw Refused.unavailable();
          continue;
        }
        int n=(int)sql.chars().filter(c->c=='?').count();
        config.addMappedStatement(new MappedStatement.Builder(config,id,new NativeEnlistedWritesV2(config,sql,n),SqlCommandType.SELECT)
            .dirtySelect(true).flushCacheRequired(true).useCache(false)
            .resultMaps(List.of(new ResultMap.Builder(config,id+".value",String.class,List.of()).build())).build());
      }
    }
  }
  static List<String> rows(SqlSession session,String name,Object... values) {
    if(!Set.of("connection","candidates","task_rows","lock_roots","lock_tasks","timeouts").contains(name))throw Refused.unavailable();
    return session.selectList(PREFIX+name,values);
  }
  static Map<String,Object> call(SqlSession session,String name,Map<String,Object> data) {
    if(!Set.of("guard_runtime_v2","validate_guard_v2","read_receipt_v2","insert_acquisition_v2","renew_acquisition_v2",
        "close_acquisition_v2","append_receipt_v2","read_acquisitions_v2").contains(name))throw Refused.unavailable();
    List<String> values=session.selectList(PREFIX+name,new Object[]{new String(Json.bytes(data),java.nio.charset.StandardCharsets.UTF_8)});
    if(values.size()!=1 || values.get(0)==null)throw Refused.unavailable();
    var result=Json.parse(values.get(0).getBytes(java.nio.charset.StandardCharsets.UTF_8));
    if(name.equals("read_acquisitions_v2"))NativeAcquisitionStoreV2.checkSelection(data,result);
    return result;
  }
  @Override public BoundSql getBoundSql(Object parameter) {
    if(!(parameter instanceof Object[] values) || values.length!=arguments)throw Refused.unavailable();
    List<ParameterMapping> mappings=new ArrayList<>();
    for(int i=0;i<arguments;i++)mappings.add(new ParameterMapping.Builder(configuration,"p"+i,Object.class).build());
    BoundSql bound=new BoundSql(configuration,sql,mappings,parameter);
    for(int i=0;i<arguments;i++)bound.setAdditionalParameter("p"+i,values[i]);
    return bound;
  }
}
