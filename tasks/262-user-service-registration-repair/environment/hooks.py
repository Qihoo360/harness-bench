"""Native trusted hook: private services and isolated code execution.

quiesce_environment runs for completed, timeout, and protocol/model error rounds
before the standard runner captures the final workspace. No final-state writes
occur in cleanup. Candidate containers never mount private state or Docker.
"""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

TASK=Path(__file__).resolve().parents[1]
CONFIG=json.loads((TASK/'tests/ground_truth/native.json').read_text())
LEGACY=TASK/'tests/ground_truth/legacy'
RECEIPT='.hb_gold20_execution.json'

def run(argv,timeout=180,env=None):
    r=subprocess.run([str(x) for x in argv],capture_output=True,text=True,timeout=timeout,
                     env=env or {**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    if r.returncode not in (0,1):raise RuntimeError(f'trusted command failed: {Path(str(argv[1])).name}: {r.stderr[-500:]}')
    return r

def command(parts,state):
    vals={'c':str(LEGACY),'w':str(state['_work']),'a':str(state['_private']),
          'r':str(state['_private']/'verdict.json')}
    return [sys.executable,'-B',str(LEGACY/parts[0]),*[x.format(**vals) for x in parts[1:]]]

def stop(p):
    if p is not None and p.poll() is None:
        p.terminate()
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)

def rpc(sock,body):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(15);s.connect(str(sock));s.sendall(body+b'\n');s.shutdown(socket.SHUT_WR)
        with s.makefile('rb') as f: response=f.readline(4194305)
    if len(response)>4194304:raise ValueError('response limit')
    return response

def prepare_runtime(context):
    workspace=Path(context['workspace']).resolve();runtime=Path(context['sandbox']).resolve()
    n=CONFIG['case_number']
    # Keep service Unix socket paths below the kernel's 108-byte bound.
    private=Path(tempfile.mkdtemp(prefix='g20-',dir=None if n>=15 else workspace.parent));private.chmod(0o700)
    if private.is_relative_to(workspace) or private.is_relative_to(runtime):raise RuntimeError('private mount overlap')
    state={'_private':private,'_work':private/'work','_process':None,'_server':None}
    n=CONFIG['case_number']
    if n<15:return state
    try:
        reset=run(command(['environment/reset.py',*CONFIG['reset']],state))
        if reset.returncode:raise RuntimeError('state reset failed')
        work=state['_work']; sock=private/'service.sock';env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost'}
        if n==15:
            with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            env.update(CASE_DB_PATH=str(work/'state.sqlite'),CASE_CATALOG_PATH=str(LEGACY/'fixtures/catalog.json'),CASE_BIND_HOST='127.0.0.1',CASE_PORT=str(port))
            argv=[sys.executable,str(LEGACY/'environment/server.py')];state['_local_cart']=f'http://127.0.0.1:{port}'
        elif n==16:
            argv=[sys.executable,str(LEGACY/'environment/tool_server.py'),'--work-root',str(work),'--socket-path',str(sock),'--max-requests','100','--idle-timeout-seconds','7260']
        elif n==17:
            argv=[sys.executable,str(TASK/'environment/expense_service.py'),'--workspace',str(work),'--tools',str(LEGACY/'environment/tools.py'),'--socket',str(sock)]
        else:
            sock=work/'service.sock';argv=[sys.executable,str(LEGACY/'environment/service.py'),'--work-root',str(work)]
        state['_socket']=sock
        state['_log']=(private/'service.log').open('w')
        state['_process']=subprocess.Popen(argv,env=env,stdin=subprocess.DEVNULL,stdout=state['_log'],stderr=subprocess.STDOUT)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if state['_process'].poll() is not None:raise RuntimeError('service stopped during startup')
            if n==15:
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                except OSError:pass
            elif sock.is_socket():break
            time.sleep(.1)
        else:raise RuntimeError('service readiness timeout')
        capability='/gold20/'+secrets.token_hex(24)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def route(self):
                if self.path!=capability and not self.path.startswith(capability+'/'):
                    self.send_error(404);return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if size<0 or size>65536:raise ValueError('request bound')
                    body=self.rfile.read(size)
                    if n==15:
                        target=state['_local_cart']+self.path[len(capability):]
                        req=urllib.request.Request(target,data=body if self.command=='POST' else None,headers={'Content-Type':'application/json'},method=self.command)
                        try:
                            with opener.open(req,timeout=15) as response:data=response.read(4194305);status=response.status
                        except urllib.error.HTTPError as exc:data=exc.read(4194305);status=exc.code
                    else:
                        if self.command!='POST':raise ValueError('POST required')
                        json.loads(body);data=rpc(sock,body);status=200
                    if len(data)>4194304:raise ValueError('response bound')
                    self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                except (ValueError,OSError):self.send_error(400)
            do_GET=route
            do_POST=route
        bind=str(context.get('service_bind_host') or '127.0.0.1')
        server=http.server.HTTPServer((bind,0),Handler);state['_server']=server
        threading.Thread(target=server.serve_forever,daemon=True).start()
        public=str(context.get('service_public_host') or ('127.0.0.1' if bind=='0.0.0.0' else bind))
        state['GOLD20_TOOL_URL']=f'http://{public}:{server.server_port}{capability}'
        state['_local_tool_url']=f'http://127.0.0.1:{server.server_port}{capability}'
        runtime.mkdir(parents=True,exist_ok=True)
        (runtime/'gold20-service.json').write_text(json.dumps({'url':state['GOLD20_TOOL_URL']}))
        return state
    except BaseException:
        cleanup_runtime(context,state);raise

def code_verdict(context,state):
    private=state['_private'];work=private/'execution';work.mkdir()
    case=private/'case';shutil.copytree(LEGACY,case,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    # The suite snapshot is frozen read-only and copytree preserves those modes;
    # this private copy is rewritten below (image digest, contract hash), so add
    # the owner write bit while keeping every other mode bit intact.
    for copied in [case,*case.rglob('*')]:
        copied.chmod(copied.stat().st_mode | 0o200)
    n=CONFIG['case_number'];parent=os.environ.get('CPUQ_CONTAINER') or 'hb-native-'+secrets.token_hex(8)
    controller=parent+'-g20-'+secrets.token_hex(5)
    # Preserve the same host path inside controllers except Node's fixed /runtime contract.
    inner=Path('/runtime') if n==10 else work
    docker_bin=shutil.which('docker')
    if not docker_bin:raise RuntimeError('code tasks require a trusted Docker controller')
    def docker(args,timeout=60):
        r=subprocess.run([docker_bin,*map(str,args)],capture_output=True,text=True,timeout=timeout)
        if r.returncode:raise RuntimeError('Docker '+str(args[0])+': '+r.stderr[-700:])
        return r.stdout.strip()
    try:image=docker(['image','inspect',CONFIG['candidate_image'],'--format','{{.Id}}'])
    except RuntimeError:
        # Pulled or rebuilt from digest-pinned inputs, the image keeps its contents but its local ID
        # depends on the Docker image store; the sandbox contracts below are bound to that ID.
        source=TASK/'environment/candidate_image.json'
        if not source.is_file():raise
        spec=json.loads(source.read_text())
        if 'pull' in spec:
            ref=spec['pull'];docker(['pull',ref],1800)
        else:
            build=spec['build'];ref=f'harnessbench/{TASK.name}-candidate:local'
            args=['build','--network','none','-t',ref,'-f',str(TASK/build['dockerfile'])]
            for key,value in build.get('args',{}).items():args+=['--build-arg',f'{key}={value}']
            docker([*args,str(TASK/build['context'])],1800)
        image=docker(['image','inspect',ref,'--format','{{.Id}}'])
    contract_name='candidate_sandbox_contract.json' if n in [9,10] else 'sandbox_contract.json'
    cp=case/'environment'/contract_name;c=json.loads(cp.read_text())
    if n==10:c['image']['digest']=image
    elif n in [9,11]:c['image_digest']=image
    if n in [9,10]:
        payload={k:v for k,v in c.items() if k!='contract_sha256'}
        c['contract_sha256']=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    cp.write_text(json.dumps(c,ensure_ascii=False,indent=2))
    mounts=[(work,inner,False),(case,case,True),(TASK/'environment/code_driver.py','/entry.py',True),
            (context['workspace'],'/native-input',True),('/var/run/docker.sock','/var/run/docker.sock',False),(docker_bin,'/usr/bin/docker',True)]
    args=['create','--name',controller,'--label','cpuq.parent='+parent,'--network','none','--user','0:4007','--read-only','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--security-opt','no-new-privileges:true','--cpus','2','--memory','2g','--memory-swap','2g','--pids-limit','128','--tmpfs','/tmp:rw,nosuid,nodev,size=128m']
    for source,destination,ro in mounts:args+=['--mount',f'type=bind,src={source},dst={destination}'+(',readonly' if ro else '')]
    env={'CPUQ_CONTAINER':parent,'CPUQ_NODES':os.environ.get('CPUQ_NODES','local-docker'),'G20_CASE':str(case),'G20_RUNTIME':str(inner),'G20_HOST_RUNTIME':str(work),'G20_CODE_IMAGE':image,'G20_CASE_NUMBER':str(n),'G20_CONTROLLER_NAME':controller,'PYTHONDONTWRITEBYTECODE':'1'}
    for k,v in env.items():args+=['-e',k+'='+v]
    args += [CONFIG['controller_image'],'python3','/entry.py','--mode','model']
    cid=docker(args);(work/'controller-id').write_text(cid)
    try:
        docker(['start',cid]);rc=docker(['wait',cid],1200)
        result_path=work/'result.json'
        if not os.access(result_path,os.R_OK):
            # The controller writes result.json as uid 2048 mode 0600, which a
            # non-root host user cannot read. Extract a copy through the Docker
            # CLI, which materializes the archive for this user.
            result_path=work/'result.host.json'
            docker(['cp',f'{cid}:{inner}/result.json',str(result_path)])
        result=json.loads(result_path.read_text())
        if rc!='0' or result.get('status')!='finished':raise RuntimeError('code verifier infrastructure failure: '+str(result.get('error',result.get('cleanup_errors','unknown'))))
        return result['verdict'],result['score']==1
    finally:
        if docker(['inspect',cid,'--format','{{index .Config.Labels "cpuq.parent"}}'])!=parent:raise RuntimeError('controller ownership mismatch')
        docker(['rm','-f',cid])

def quiesce_environment(context,state,round_index):
    if state.get('_manifest'):return
    module_spec=importlib.util.spec_from_file_location('g20_snapshot_inventory',TASK/'tests/test_outcome.py')
    mod=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(mod)
    workspace=Path(context['workspace']);dest=workspace/RECEIPT
    forged=dest.exists() or dest.is_symlink();unsafe=False
    try:files=mod.inventory(workspace)
    except ValueError:files={};unsafe=True
    if forged or unsafe:verdict={'checks':[]};accepted=False
    elif CONFIG['case_number']<15:verdict,accepted=code_verdict(context,state)
    else:
        server=state.get('_server')
        if server:server.shutdown();server.server_close();state['_server']=None
        stop(state.get('_process'));state['_process']=None
        if CONFIG['case_number']==20:
            target=state['_work']/'workspace'
            for directory,child_dirs,child_files in os.walk(target,followlinks=False):Path(directory).chmod(0o700)
            shutil.rmtree(target)
            shutil.copytree(workspace,target,ignore=shutil.ignore_patterns('hb_tools',RECEIPT))
            # Translate the native hash-protected inputs to the legacy readonly view.
            # Candidate bytes are retained and independently verified above/below.
            for p in (target/'inputs').iterdir():
                if p.is_file():p.chmod(0o444)
            (target/'inputs').chmod(0o555)
        r=run(command(CONFIG['verify'],state))
        verdict=json.loads((state['_private']/'verdict.json').read_text())
        if verdict.get('verdict') in ['invalid','error']:raise RuntimeError('state verifier infrastructure failure')
        accepted=r.returncode==0
    checks={}
    for c in verdict.get('checks',[]):
        key=c.get('check_id',c.get('id'))
        if not isinstance(key,str) or key in checks:raise RuntimeError('invalid verifier checks')
        checks[key]=c.get('passed') is True
    receipt={'schema_version':1,'status':'finished','case_number':CONFIG['case_number'],
             'config_sha256':hashlib.sha256((TASK/'tests/ground_truth/native.json').read_bytes()).hexdigest(),
             'workspace_files':files,'checks':checks,'legacy_accepted':accepted,
             'forged_snapshot':forged,'unsafe_workspace':unsafe}
    if forged:
        if dest.is_dir() and not dest.is_symlink():shutil.rmtree(dest)
        else:dest.unlink()
    data=(json.dumps(receipt,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    with dest.open('xb') as f:f.write(data)
    dest.chmod(0o444)
    state['_manifest']={'kind':'file','sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}

def environment_mutations(context,state,round_index):
    return {'created':{RECEIPT:state['_manifest']},'modified':{},'deleted':{}}

def cleanup_runtime(context,state):
    if state.get('_keepalive_stop'):state['_keepalive_stop'].set()
    server=state.get('_server')
    if server:server.shutdown();server.server_close()
    stop(state.get('_process'))
    if state.get('_log'):state['_log'].close()
    private=state.get('_private')
    if CONFIG['case_number']>=15 and isinstance(private,Path) and private.exists():
        evidence=Path(context['workspace']).parent/'service-evidence';evidence.mkdir(exist_ok=True)
        for name in ['service.log','verdict.json']:
            if (private/name).is_file():shutil.copyfile(private/name,evidence/name)
        for parent,dirs,files in os.walk(private,followlinks=False):
            Path(parent).chmod(0o700)
        shutil.rmtree(private)
    # Code-controller artifacts stay in the caller's private result directory.
