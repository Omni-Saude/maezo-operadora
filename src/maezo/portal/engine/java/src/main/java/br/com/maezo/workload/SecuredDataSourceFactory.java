package br.com.maezo.workload;

import java.util.Hashtable;
import javax.naming.Context;
import javax.naming.Name;
import javax.naming.spi.ObjectFactory;

/** Inert factory; the actual object-bound owner admits every resolution before vendor construction. */
public final class SecuredDataSourceFactory implements ObjectFactory {
  @Override public Object getObjectInstance(Object obj,Name name,Context context,Hashtable<?,?> environment) throws Exception {
    return SecuredBpmPlatformBootstrap.naming().resolve(obj,name,context,environment);
  }
}
