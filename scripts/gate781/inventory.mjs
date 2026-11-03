// Gate781: GitHub metadata only. No protected Environment, secret, Railway or mutation.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const NAMES=Object.freeze(['gate745-isolated-fixture','agent-ops-disposable-backup-fixture']);
const REPOS=Object.freeze({'markmalina/gate':{id:1293746267,pr:766,branch:'agent/736-autonomous-watch-queue'},'markmalina/ops':{id:1331657982,pr:414,branch:'agent/gate736-s1-watch0119'}});
const SHA=/^[a-f0-9]{40}$/u;
export function authorize(env,event){
 const p=REPOS[env.GITHUB_REPOSITORY],r=event?.repository,pr=event?.pull_request;
 if(!p||env.GITHUB_EVENT_NAME!=='pull_request'||env.GITHUB_RUN_ATTEMPT!=='1'||env.GITHUB_ACTOR_ID!=='23397294'||
 r?.id!==p.id||r.full_name!==env.GITHUB_REPOSITORY||r.owner?.id!==23397294||event.sender?.id!==23397294||
 !['opened','synchronize','reopened'].includes(event.action)||pr?.number!==p.pr||pr.head?.repo?.id!==p.id||pr.base?.repo?.id!==p.id||
 pr.head.ref!==p.branch||!SHA.test(pr.head.sha??'')||env.EXPECTED_HEAD_SHA!==pr.head.sha||!SHA.test(env.GITHUB_SHA??'')||
 !/^[1-9][0-9]{0,19}$/u.test(env.GITHUB_RUN_ID??''))throw Error('E_GITHUB_INVENTORY_CALLER');
 return p;
}
export async function inspect({env,event,fetchFn=fetch,now=()=>new Date().toISOString()}){
 authorize(env,event);
 const receipt={schema:'gate781.github-fixture-environments/v1',repository:env.GITHUB_REPOSITORY,repositoryId:event.repository.id,
 sourceSha:env.EXPECTED_HEAD_SHA,workflowSha:env.GITHUB_SHA,runId:env.GITHUB_RUN_ID,runAttempt:1,startedAt:now(),finishedAt:null,
 result:'UNKNOWN',reason:null,requests:0,environments:NAMES.map(name=>({name,presence:'UNKNOWN'})),
 protectedBinding:'NOT_OBSERVED',railwayResourceExistence:'NOT_OBSERVED',providerCalls:0,mutations:0};
 let total=null;const found=new Set(),seen=new Set();
 try{
  if(typeof env.GH_TOKEN!=='string'||env.GH_TOKEN.length<1)throw Error('E_GITHUB_METADATA_TOKEN');
  for(let page=1;page<=2;page++){
   receipt.requests++;
   let response;
   try{response=await fetchFn(`https://api.github.com/repos/${env.GITHUB_REPOSITORY}/environments?per_page=100&page=${page}`,
    {method:'GET',redirect:'error',headers:{Authorization:`Bearer ${env.GH_TOKEN}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},signal:AbortSignal.timeout(15000)});}catch{throw Error('E_GITHUB_METADATA_NETWORK');}
   if(!response.ok)throw Error('E_GITHUB_METADATA_UNAVAILABLE');
   // Do not retain or print raw API bytes; environment objects can contain reviewers.
   const raw=await response.text();if(Buffer.byteLength(raw)>1048576)throw Error('E_GITHUB_METADATA_SHAPE');
   let d;try{d=JSON.parse(raw)}catch{throw Error('E_GITHUB_METADATA_SHAPE');}
   if(!Number.isSafeInteger(d.total_count)||d.total_count<0||d.total_count>200||!Array.isArray(d.environments)||d.environments.length>100||
    (total!==null&&d.total_count!==total))throw Error('E_GITHUB_METADATA_INCOMPLETE');
   total=d.total_count;
   if(d.environments.length!==Math.min(100,total-(page-1)*100))throw Error('E_GITHUB_METADATA_INCOMPLETE');
   for(const item of d.environments){
    if(!Number.isSafeInteger(item.id)||item.id<1||typeof item.name!=='string'||seen.has(item.id))throw Error('E_GITHUB_METADATA_SHAPE');
    seen.add(item.id);if(NAMES.includes(item.name)){if(found.has(item.name))throw Error('E_GITHUB_METADATA_SHAPE');found.add(item.name);}
   }
   if(seen.size===total){receipt.result='OBSERVED';receipt.environments=NAMES.map(name=>({name,presence:found.has(name)?'PRESENT':'ABSENT'}));break;}
  }
 }catch(e){receipt.result='UNKNOWN';receipt.reason=/^E_GITHUB_(?:METADATA_TOKEN|METADATA_NETWORK|METADATA_UNAVAILABLE|METADATA_SHAPE|METADATA_INCOMPLETE)$/u.test(e.message)?e.message:'E_GITHUB_METADATA_SHAPE';}
 receipt.finishedAt=now();return receipt;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 const event=JSON.parse(await readFile(process.env.GITHUB_EVENT_PATH,'utf8'));
 const receipt=await inspect({env:process.env,event});
 await mkdir('.agent-ops/out',{recursive:true});
 await writeFile('.agent-ops/out/gate781-github-environments.json',JSON.stringify(receipt)+'\n',{flag:'wx',mode:0o600});
 process.stdout.write(JSON.stringify(receipt)+'\n');
 if(receipt.result!=='OBSERVED')process.exitCode=1;
}
