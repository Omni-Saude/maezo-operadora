package br.com.maezo.human;

import java.nio.file.Path;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.cibseven.bpm.engine.impl.variable.serializer.TypedValueSerializer;

/** Explicit installed configuration supplied by protected production composition; no inferred defaults. */
public final class HumanAuthConfiguration {
  private final Map<String,Object> scope;private final String audience;
  private final long maxLifetime;private final int timeout;private final AuthResultSigner signer;
  public HumanAuthConfiguration(Map<String,Object> scope,String audience,long maxLifetime,int timeout,
      Path signingPkcs12,char[] password,String alias,String keyId,String issuer,String expectedSigningSpki) {
    this.scope=PortalReadModels.copy(AuthModels.validate("scope",scope));
    if(audience==null||audience.isBlank()||maxLifetime<1||maxLifetime>300||timeout<1||timeout>10)throw Rejected.invalid();
    this.audience=audience;this.maxLifetime=maxLifetime;this.timeout=timeout;
    this.signer=AuthResultSigner.load(signingPkcs12,password,alias,keyId,issuer,expectedSigningSpki);
  }
  /** Called exactly once during plugin preInit, before CIB initializes its serializer chain. */
  static void registerSerializer(ProcessEngineConfigurationImpl configuration) {
    if(configuration.getVariableSerializers()!=null)throw Rejected.denied();
    var serializers=new ArrayList<TypedValueSerializer>();
    if(configuration.getCustomPreVariableSerializers()!=null)serializers.addAll(configuration.getCustomPreVariableSerializers());
    var all=new ArrayList<TypedValueSerializer>(serializers);
    if(configuration.getCustomPostVariableSerializers()!=null)all.addAll(configuration.getCustomPostVariableSerializers());
    if(all.stream().anyMatch(s->s==null||AuthDecimalSerializer.NAME.equals(s.getName())))throw Rejected.denied();
    serializers.add(new AuthDecimalSerializer());configuration.setCustomPreVariableSerializers(serializers);
  }
  AuthRuntime bind(ProcessEngineConfigurationImpl configuration) {
    if(configuration.getVariableSerializers()==null
        ||configuration.getVariableSerializers().getSerializerByName(AuthDecimalSerializer.NAME).getClass()!=AuthDecimalSerializer.class)throw Rejected.denied();
    return new AuthRuntime(configuration,scope,audience,maxLifetime,timeout,signer);
  }
}
