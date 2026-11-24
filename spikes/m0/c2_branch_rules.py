"""Probe protected sandbox only. Never prints credentials. No settings mutations."""
import base64,json,os,subprocess,urllib.request,urllib.error
from pathlib import Path
R='Sahil-SS9/code-shipper-sandbox'
t=Path.home().joinpath('.config/code-shipper/tokens/fixer').read_text().strip()
rows=[]
def api(method,path,body=None):
    req=urllib.request.Request('https://api.github.com/'+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+t,'Accept':'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)
def probe(label,method,path,body):
    s,b=api(method,path,body);row={'operation':label,'status':s,'response':b};rows.append(row);print(label,s,json.dumps(b)[:450]);return b
pr=api('GET',f'repos/{R}/pulls/3')[1]
probe('REST merge with green CI, no human approval','PUT',f'repos/{R}/pulls/3/merge',{'sha':pr['head']['sha'],'merge_method':'merge'})
probe('GraphQL merge','POST','graphql',{'query':'mutation($id:ID!){mergePullRequest(input:{pullRequestId:$id}){pullRequest{merged}}}','variables':{'id':pr['node_id']}})
probe('GraphQL auto-merge','POST','graphql',{'query':'mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id}){clientMutationId}}','variables':{'id':pr['node_id']}})
# Create a candidate commit without checking out or altering the local clone.
cwd=str(Path.home()/'repos/code-shipper-sandbox')
subprocess.run(['git','fetch','origin'],cwd=cwd,check=True,capture_output=True)
base=subprocess.check_output(['git','rev-parse','origin/main'],cwd=cwd,text=True).strip()
tree=subprocess.check_output(['git','rev-parse',base+'^{tree}'],cwd=cwd,text=True).strip()
commit=subprocess.check_output(['git','commit-tree',tree,'-p',base,'-m','Protected push probe (same tree)'],cwd=cwd,text=True).strip()
basic=base64.b64encode(('x-access-token:'+t).encode()).decode()
env=dict(os.environ,GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_NOSYSTEM='1',GIT_TERMINAL_PROMPT='0',GIT_ASKPASS='/bin/false')
for label,ref in [('push protected main',f'{commit}:refs/heads/main'),('push allowed PR branch',f'{commit}:refs/heads/spike/protection-positive')]:
    p=subprocess.run(['git','-c','credential.helper=','-c',f'http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}','push',f'https://github.com/{R}.git',ref],cwd=cwd,env=env,text=True,capture_output=True)
    row={'operation':label,'returncode':p.returncode,'response':p.stderr.replace(t,'***')};rows.append(row);print(label,p.returncode,row['response'][-600:])
Path(__file__).parent.joinpath('out/c2-results.json').write_text(json.dumps(rows,indent=2))
