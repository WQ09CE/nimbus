// Actual extension handler with scripted registry/fetch; no Pi credentials/provider calls.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const path = process.env.NIMBUS_TEST_PI_BRIDGE;
const {default: extension} = await import(path ? pathToFileURL(path).href : new URL('../src/nimbus_chat_lab/pi_bridge.ts', import.meta.url).href);
const handlers = new Map();
extension({on: (name, fn) => handlers.set(name, fn)});
const captured = [];
const originalLog = console.log;
console.log = value => captured.push(JSON.parse(value));
const registry = {
  find: () => ({}), isUsingOAuth: () => true,
  getProviderAuth: async () => ({auth:{apiKey:'FIXTURE_AUTH_DO_NOT_COLLECT'}}),
  complete: async () => ({stopReason:'error',errorMessage:'original model error',content:[]}),
};
async function invoke(payload) {
  captured.length = 0;
  const result = await handlers.get('input')({text:JSON.stringify(payload)}, {modelRegistry:registry});
  assert.deepEqual(result,{action:'handled'});
  assert.equal(captured.length,1);
  return captured[0];
}
try {
  let result = await invoke({op:'model',messages:[]});
  assert.equal(result.diagnostic.stage,'model_response');
  assert.equal(result.diagnostic.response.errorMessage,'original model error');
  assert.equal(result.diagnostic.response.stopReason,'error');
  assert.match(result.diagnostic.error.stack,/original model error/);
  registry.complete = async () => {throw new Error('original transport failure', {cause:Object.assign(new Error('socket reset'),{code:'ECONNRESET'})});};
  result = await invoke({op:'model',messages:[]});
  assert.equal(result.diagnostic.stage,'model_call');
  assert.equal(result.diagnostic.error.cause.code,'ECONNRESET');
  globalThis.fetch = async () => new Response('original upstream HTTP body',{status:503});
  result = await invoke({op:'search',query:'synthetic',source:'x'});
  assert.equal(result.diagnostic.http_status,503);
  assert.equal(result.diagnostic.response_body,'original upstream HTTP body');
  assert.ok(!JSON.stringify(result).includes('FIXTURE_AUTH_DO_NOT_COLLECT'));
  globalThis.fetch = async () => new Response('{invalid JSON',{status:200});
  result = await invoke({op:'search',query:'synthetic',source:'x'});
  assert.equal(result.diagnostic.response_body,'{invalid JSON');
  assert.equal(result.diagnostic.error.name,'SyntaxError');
  let submitted;
  globalThis.fetch=async (url,init)=>{submitted=JSON.parse(init.body);return new Response(JSON.stringify({status:'completed',usage:{num_server_side_tools_used:1},output:[{type:'message',content:[{type:'output_text',text:'{"candidates":[]}',annotations:[]}]}]}),{status:200});};
  result=await invoke({op:'search',query:'public',source:'x',mode:'discover',x_filters:{from_date:'2026-09-11',to_date:'2026-09-12',allowed_x_handles:['OpenAIDevs']}});
  assert.equal(submitted.tools[0].from_date,'2026-09-11');assert.deepEqual(submitted.tools[0].allowed_x_handles,['OpenAIDevs']);assert.equal(submitted.max_turns,2);
  assert.equal(result.result.quality,'partial');
  result=await invoke({op:'search',query:'public',source:'x',x_filters:{from_date:'2026-02-30'}});assert.equal(result.error,'provider_request_failed');
  registry.complete = async () => ({stopReason:'stop',content:[{type:'text',text:'okay'}],usage:{input:1,output:1}});
  result = await invoke({op:'model',messages:[]});
  assert.equal(result.result.content,'okay');
  assert.equal(result.diagnostic,undefined);
  assert.throws(() => handlers.get('before_provider_request')(),/No outer Pi agent turn/);
} finally {console.log=originalLog;}
console.log('PASS: native handler error details, causes, HTTP body/status, success and outer-loop gate');
