package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.apache.tomcat.util.descriptor.web.FilterDef;
import org.apache.tomcat.util.descriptor.web.FilterMap;
import org.apache.tomcat.util.descriptor.web.WebXml;
import org.junit.jupiter.api.Test;

/** Exercise the pinned Tomcat descriptor merger, including the old ordering failure. */
class BoundaryMappingMergeTest {
  private void boundary(WebXml xml) {
    add(xml,"maezo-boundary","br.com.maezo.workload.BoundaryFilter");
  }
  private void auth(WebXml xml) {
    add(xml,"maezo-native-auth","org.cibseven.bpm.engine.rest.security.auth.ProcessEngineAuthenticationFilter");
  }
  private void add(WebXml xml,String name,String type) {
    var definition=new FilterDef();definition.setFilterName(name);definition.setFilterClass(type);
    xml.addFilter(definition);
    var mapping=new FilterMap();mapping.setFilterName(name);mapping.addURLPattern("/*");
    for(String dispatcher:List.of("REQUEST","FORWARD","INCLUDE","ERROR","ASYNC"))mapping.setDispatcher(dispatcher);
    xml.addFilterMapping(mapping);
  }
  private List<String> effective(WebXml application) {
    var defaults=new WebXml();defaults.setOverridable(true);boundary(defaults);
    assertTrue(application.merge(Set.of(defaults)));
    return application.getFilterMappings().stream().map(FilterMap::getFilterName).toList();
  }
  @Test void globalOnlyBoundaryIsTooLateForNativeAuthentication() {
    var application=new WebXml();auth(application);
    assertEquals(List.of("maezo-native-auth","maezo-boundary"),effective(application));
  }
  @Test void explicitSameNameApplicationMappingRunsFirstWithoutDefaultDuplication() {
    var application=new WebXml();boundary(application);auth(application);
    assertEquals(List.of("maezo-boundary","maezo-native-auth"),effective(application));
    assertEquals(2,application.getFilters().size());
    var boundary=application.getFilterMappings().iterator().next();
    assertEquals(Set.of("REQUEST","FORWARD","INCLUDE","ERROR","ASYNC"),Set.of(boundary.getDispatcherNames()));
  }
}
