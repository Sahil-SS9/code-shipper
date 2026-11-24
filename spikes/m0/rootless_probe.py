"""One disposable cached-image confinement probe; fake credentials only, no downloads."""
import json,os,subprocess,tempfile,uuid
from pathlib import Path
root=Path(tempfile.mkdtemp(prefix='cs-containment-',dir='/home/kensei/.hermes/cache/scratch'))
source=root/'source';work=root/'work';source.mkdir();work.mkdir();work.chmod(0o777)
(source/'fixture.txt').write_text('synthetic-source')
(source/'visible-canary.txt').write_text('FAKE-CREDENTIAL-POSITIVE-CONTROL')
canary=root/'outside-canary';canary.write_text('FAKE-CREDENTIAL-MUST-NOT-BE-REACHABLE');canary.chmod(0o600)
name='cs-isolation-'+uuid.uuid4().hex[:10]
image='sha256:'+subprocess.check_output(['docker','--context','rootless','image','inspect','caddy:2-alpine','--format','{{.Id}}'],text=True).strip().removeprefix('sha256:')
script=Path(__file__).with_name('container-probe.sh')
# Non-root worker can read only the synthetic, deliberately mounted inputs.
source.chmod(0o755)
for f in source.iterdir(): f.chmod(0o644)
script.chmod(0o644)
cmd=['docker','--context','rootless','run','--pull','never','--rm','--name',name,
'--network','none','--read-only','--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges',
'--pids-limit','64','--memory','128m','--cpus','0.5','--tmpfs','/tmp:rw,noexec,nosuid,size=8m',
'--mount',f'type=bind,src={source},dst=/source,readonly',
'--mount',f'type=bind,src={work},dst=/work',
'--mount',f'type=bind,src={script},dst=/probe.sh,readonly',
'--env',f'HOST_CANARY={canary}','--env',f'HOST_PID={os.getpid()}',
'--env',f'HOST_NET_NS={os.readlink("/proc/self/ns/net")}',
'--entrypoint','/bin/sh',image,'/probe.sh']
p=subprocess.run(cmd,text=True,capture_output=True,timeout=60)
lines=p.stdout.splitlines()
report={'image':image,'container_name':name,'root':str(root),'argv':cmd,'returncode':p.returncode,
'checks':lines,'stderr':p.stderr,'workspace_receipt':(work/'result.txt').read_text() if (work/'result.txt').exists() else None,
'limits':'requested, not workload stress-tested','scope':'synthetic confinement probe, no model/inference/broker end-to-end proof'}
(root/'report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
assert p.returncode==0 and lines[-1]=='DONE' and not any(l.startswith('FAIL') for l in lines)
print('PASSED_CHECKS',sum(l.startswith('PASS ') for l in lines))
left=subprocess.check_output(['docker','--context','rootless','ps','-aq','--filter',f'name=^{name}$'],text=True).strip()
assert not left
print('CONTAINER_REMOVED',not bool(left));print('REPORT',root/'report.json')
