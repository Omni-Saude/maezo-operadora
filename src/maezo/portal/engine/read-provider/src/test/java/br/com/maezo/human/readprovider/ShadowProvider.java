package br.com.maezo.human.readprovider;

import br.com.maezo.human.PortalReadTrust;
import java.util.Map;

/**
 * A second, deliberately NOT registered provider. The duplicate-registration boot test lists it in
 * a throw-away {@code META-INF/services} file so the plugin sees two registrations. Every method
 * fails loudly: if the plugin ever picked it, the test would not see the bounded 503.
 */
public final class ShadowProvider implements PortalReadTrust.Providers {
  @Override
  public PortalReadTrust.Admission acquire(Map<String, Object> scope, String engine,
      String incarnation, String deployment, String digest, String configurationDigest,
      String purpose) {
    throw new AssertionError("shadow provider must never be selected");
  }

  @Override
  public PortalReadTrust.NativeKeySet continuity(PortalReadTrust.Admission admission) {
    throw new AssertionError("shadow provider must never be selected");
  }

  @Override
  public PortalReadTrust.PublicationQualification qualifyPublication(
      PortalReadTrust.Admission admission, Map<String, Object> publication) {
    throw new AssertionError("shadow provider must never be selected");
  }
}
