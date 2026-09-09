from pathlib import Path
import os,subprocess,datetime,json,sys,hashlib
w=Path.cwd();p=w/'docs/audits/maezo-deep-audit/remediation/completion-portal-d7-native-authority-product-repair'; b=Path('/Users/familia/.cache/maezo-portal-jakarta-abi-repair-tooling');env=os.environ.copy();env['JAVA_HOME']=str(b/'jdk-17.0.17+10/Contents/Home');env['PATH']=env['JAVA_HOME']+'/bin:'+env['PATH']
leaf=sys.argv[1];a=[str(b/'apache-maven-3.9.9/bin/mvn'),'-o','-s',str(p/'empty-settings.xml'),'-Dmaven.repo.local=/Users/familia/.m2/repository','-f','src/maezo/portal/engine/java/pom.xml']+sys.argv[2:]+['test']
t=datetime.datetime.now(datetime.timezone.utc).isoformat()
with (p/(leaf+'.log')).open('x') as o:r=subprocess.run(a,env=env,stdout=o,stderr=subprocess.STDOUT)
(p/(leaf+'-run.json')).write_text(json.dumps({'argv':a,'JAVA_HOME':env['JAVA_HOME'],'started':t,'ended':datetime.datetime.now(datetime.timezone.utc).isoformat(),'returncode':r.returncode},indent=2)+'\n')
print('Maven return:',r.returncode); print('\n'.join((p/(leaf+'.log')).read_text().splitlines()[-28:]))
sys.exit(r.returncode)
