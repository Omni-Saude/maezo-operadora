package br.com.maezo.human;

import java.util.*;
import org.apache.ibatis.mapping.*;
import org.apache.ibatis.session.Configuration;

/** PostgreSQL DML RETURNING with MyBatis affectData=true, including CIB's BATCH executor. */
final class EnlistedWrites implements SqlSource {
  static final String ID = "br.com.maezo.human.enlistedWrite.v1";
  record Write(String sql, Object[] arguments) {}

  private final Configuration configuration;

  private EnlistedWrites(Configuration configuration) {
    this.configuration = configuration;
  }

  static void install(Configuration configuration) {
    synchronized (configuration) {
      if (configuration.hasStatement(ID, false)) {
        if (!(configuration.getMappedStatement(ID).getSqlSource() instanceof EnlistedWrites))
          throw new IllegalStateException("human write mapping collision");
        return;
      }
      configuration.addMappedStatement(
          new MappedStatement.Builder(configuration, ID, new EnlistedWrites(configuration), SqlCommandType.SELECT)
              .dirtySelect(false)
              .flushCacheRequired(true)
              .useCache(false)
              .resultMaps(List.of(new ResultMap.Builder(configuration, ID + ".count", Integer.class, List.of()).build()))
              .build());
    }
  }

  @Override
  public BoundSql getBoundSql(Object parameter) {
    Write write = (Write) parameter;
    List<ParameterMapping> mappings = new ArrayList<>();
    for (int i = 0; i < write.arguments().length; i++)
      mappings.add(new ParameterMapping.Builder(configuration, "p" + i, Object.class).build());
    // SQL comes only from package-owned statements; all values remain prepared parameters.
    BoundSql bound = new BoundSql(configuration, write.sql() + " RETURNING 1", mappings, parameter);
    for (int i = 0; i < write.arguments().length; i++)
      bound.setAdditionalParameter("p" + i, write.arguments()[i]);
    return bound;
  }
}
