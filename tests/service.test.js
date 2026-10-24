// Run the actual QML service method bodies with a fake process boundary.
// Fixtures never call helpers, D-Bus, the desktop or clipboard.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../Service.qml'),'utf8');
const modelSource = fs.readFileSync(path.join(__dirname,'../Model.js'),'utf8').replace('.pragma library','');
const Model = new Function(modelSource + '; return {parseList,hiddenEntries,normalizePrefs,updatePrefs};')();
function service() {
  const root = {settings:{},demoMode:false,helperPath:'/fixture/bin/protonauth-list',ok:false,entries:[],error:null,updatedAt:'',now:0,vaultMtime:0,refreshing:false,prefs:Model.normalizePrefs({}),prefsLoaded:true,prefsError:'',revealId:'',revealOwner:null,prefsRevision:0,prefsEpoch:0,owners:[{}],generation:0,requestSerial:0,pending:[],current:null,_out:'',_timedOut:false,_oversized:false,_started:true,copyEvents:[]};
  const process = {running:false,command:[],stdinEnabled:false};
  Object.defineProperty(root,'privacy',{get:()=>!root.prefsLoaded || root.prefs.privacy !== false});
  const deferred = [];
  const Qt = {callLater:fn=>deferred.push(fn)};
  root.copyFinished = (...args)=>root.copyEvents.push(args);
  // Methods use two-space closing braces; inner blocks are indented farther.
  for (const match of source.matchAll(/^  function (\w+)\(([^)]*)\) \{([^\n]*\}|[\s\S]*?\n  \})/gm)) {
    const name = match[1], body = match[3].replace(/\}\s*$/, '');
    root[name] = new Function('Model','root','listProcess','Qt',...match[2].split(',').map(x=>x.trim()).filter(Boolean),'with(root){'+body+'}').bind(null,Model,root,process,Qt);
  }
  const modeBody=source.match(/onDemoModeChanged:\s*\{([\s\S]*?)\n  \}/)[1];
  const modeChanged=new Function('Model','root','listProcess','Qt','with(root){'+modeBody+'}').bind(null,Model,root,process,Qt);
  return {root,process,deferred,modeChanged};
}
const row = {id:'fixture-A',issuer:'Fixture',account:'test@example.invalid',kind:'totp',code:'123456',nextCode:'654321',remaining:24,period:30,digits:6};
function reply(root,kind,extra={}) {
  root.current = {kind,generation:root.generation,serial:root.requestSerial,prefsRevision:root.prefsRevision,prefsEpoch:root.prefsEpoch,args:[],accountId:'fixture-A',...extra};
  root._started = true;
  root._out = JSON.stringify({ok:true,entries:[row],error:null});
}
test('closing during a code request discards its late reply and output',()=>{
  const {root}=service(); reply(root,'codes');
  root.forget(); root.owners=[];
  root._out=JSON.stringify({ok:true,entries:[row],error:null}); root.finish();
  assert.deepEqual(root.entries,[]); assert.equal(root._out,'');
});
test('a superseded reveal reply never replaces the latest view',()=>{
  const {root}=service(); reply(root,'codes'); root.requestSerial++;
  root.finish(); assert.deepEqual(root.entries,[]);
});
test('closing the revealing owner hides its code while another panel stays open',()=>{
  const {root}=service(); const a={},b={};root.owners=[a,b];root.revealOwner=a;root.revealId=row.id;root.entries=[row];
  root.panelClosed(a); assert.equal(root.revealId,'');assert.equal(root.entries[0].code,null);assert.deepEqual(root.owners,[b]);
});
test('old preference acknowledgment cannot erase a newer pin edit',()=>{
  const {root}=service();root.editPrefs('pin','A');const old=root.current;
  root.editPrefs('pin','B'); root.current=old;root._out=JSON.stringify({ok:true,entries:[],prefs:{privacy:true,pinned:['A'],recent:[]},error:null});root._started=true;root.finish();
  root.editPrefs('pin','C');assert.deepEqual(root.prefs.pinned,['A','B','C']);
  assert.deepEqual(JSON.parse(root.pending.find(p=>p.kind==='prefs-set').input).pinned,['A','B','C']);
});
test('failed initial preferences keep privacy on and offer a retry',()=>{
  const {root,process}=service();root.prefsLoaded=false;reply(root,'prefs-get');root._out=JSON.stringify({ok:false,entries:[],error:{code:'PREFS_FAILED'}});root.finish();
  assert.match(root.prefsError,/unavailable/);assert.equal(root.prefs.privacy,true);
  process.running=false;root.retryPrefs();assert.equal(root.current.kind,'prefs-get');
});
test('failed preference persistence retains newer state and retries that state',()=>{
  const {root,process}=service();root.prefs={privacy:true,pinned:['A'],recent:[]};reply(root,'prefs-set');root._out=JSON.stringify({ok:false,entries:[],error:{code:'PREFS_FAILED'}});root.finish();
  assert.match(root.prefsError,/not saved/);process.running=false;root.retryPrefs();assert.deepEqual(JSON.parse(root.current.input).pinned,['A']);
});
test('keyring locking invalidates pending codes and hides already displayed codes',()=>{
  const {root}=service();root.entries=[row];reply(root,'check');root._out=JSON.stringify({ok:false,entries:[],error:{code:'KEYRING_LOCKED'}});root.finish();
  assert.equal(root.entries[0].code,null);assert.equal(root.generation,1);
});
test('closing the panel cannot suppress a current privacy-save failure',()=>{
  const {root}=service();const owner=root.owners[0];root.prefs.privacy=false;
  root.editPrefs('privacy',true);
  root.panelClosed(owner);
  root._started=true;root._out=JSON.stringify({ok:false,entries:[],error:{code:'PREFS_WRITE_FAILED'}});
  root.finish();
  assert.equal(root.prefs.privacy,true);
  assert.match(root.prefsError,/not saved/);
});
test('real/demo switch rejects old preference successes and failures',()=>{
  for(const kind of ['prefs-get','prefs-set']) for(const succeeds of [true,false]) {
    const {root,modeChanged}=service();root.owners=[];reply(root,kind);
    root.demoMode=true;modeChanged();root._started=true;
    root._out=JSON.stringify(succeeds ? {ok:true,entries:[],prefs:{privacy:false,pinned:['real-account'],recent:[]},error:null}
      : {ok:false,entries:[],error:{code:'PREFS_WRITE_FAILED'}});
    root.finish();assert.equal(root.prefs.privacy,true);assert.deepEqual(root.prefs.pinned,[]);assert.equal(root.prefsError,'');
  }
});
