import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const base=process.env.NIMBUS_TEST_PI_BRIDGE;
const moduleURL=base?new URL('search_protocol.ts',pathToFileURL(base)):new URL('../src/nimbus_chat_lab/search_protocol.ts',import.meta.url);
const {searchResult,searchFailure}=await import(moduleURL.href);
const candidate={summary:'synthetic event',url:'https://x.com/alice/status/123',author:'alice',published_at:'2026-09-12T00:00:00Z',evidence_type:'developer',excerpt:'synthetic quote'};
function response(text,annotations=[{type:'url_citation',url:'https://x.com/i/status/123',title:'source'}]){
 return {status:'completed',usage:{num_server_side_tools_used:1,server_side_tool_usage_details:{x_search_calls:1,private_field:'SECRET'}},output:[{type:'message',phase:'commentary',content:[{type:'output_text',text:'I will search...'}]},{type:'message',phase:'final_answer',content:[{type:'output_text',text,annotations}]}]};
}
let r=searchResult(response(JSON.stringify({candidates:[candidate]})),'discover');
assert.equal(r.quality,'evidence_returned');assert.equal(r.candidates.length,1);assert.ok(!r.text.includes('I will search'));assert.ok(!JSON.stringify(r).includes('SECRET'));
r=searchResult(response(JSON.stringify({candidates:[{...candidate,url:'https://x.com/invented/status/456'}]})),'discover');
assert.equal(r.quality,'partial');assert.equal(r.candidates.length,1);assert.equal(r.candidates[0].citation_bound,false);assert.equal(r.candidates[0].verification,'uncited_lead_requires_lookup');
const mixed=response('Coverage limitation.\n```json\n'+JSON.stringify({candidates:[candidate]})+'\n```');
delete mixed.output[1].phase;
r=searchResult(mixed,'discover');assert.equal(r.candidates.length,1);assert.ok(!r.text.includes('I will search'));assert.equal(r.projection.omitted_progress_messages,1);
r=searchResult(response('One sentence and no author/time.'),'discover');assert.equal(r.quality,'partial');assert.equal(r.candidates.length,0);
r=searchResult(response(JSON.stringify({candidates:[{...candidate,published_at:'2026-09-12',author:null}]})),'discover');assert.equal(r.quality,'partial');assert.equal(r.candidates[0].published_at,null);
r=searchResult(response('长'.repeat(30000),Array.from({length:60},(_,i)=>({type:'url_citation',url:'https://example.com/'+i+'x'.repeat(1800)}))),'research');assert.ok(Buffer.byteLength(JSON.stringify(r))<=24000);assert.ok(r.truncated);
assert.equal(searchFailure('search_http',{},Object.assign(new Error(),{name:'TimeoutError'})).code,'upstream_timeout');
assert.deepEqual(searchFailure('search_response',{http_status:500},new Error()),{code:'upstream_http_error',http_status:500,retryable:true});
console.log('PASS: structured candidate validation, citation binding, partial evidence, byte bounds and safe failures');

const streamURL=new URL('search_stream.ts',moduleURL);
const {readSearchResponse}=await import(streamURL.href);
const call={id:'tool-1',type:'custom_tool_call',name:'x_keyword_search',status:'completed',input:JSON.stringify({mode:'Top',query:'测试 min_faves:100',Authorization:'SECRET'}),encrypted_content:'SECRET'};
function sse(events,step=17){
 const data=Buffer.from(events.map(e=>'data: '+JSON.stringify(e)+'\r\n\r\n').join('')+'data: [DONE]\n\n');
 return new Response(new ReadableStream({start(c){for(let i=0;i<data.length;i+=step)c.enqueue(data.subarray(i,i+step));c.close();}}),{headers:{'content-type':'text/event-stream'}});
}
const final=response(JSON.stringify({candidates:[candidate]}));
const events=[{type:'response.output_item.done',item:call},{type:'response.reasoning_summary_text.delta',delta:'SECRET'},{type:'response.completed',response:final}];
let diagnostic={};
let d=await readSearchResponse(sse(events),diagnostic);
r=searchResult(d,'discover');
assert.deepEqual(r.search_observation.keyword_modes,['Top']);
assert.equal(r.search_observation.calls[0].query,'测试 min_faves:100');
assert.equal(r.search_observation.ranking_by_views_verified,false);
assert.equal(r.search_observation.engagement_filter_verified,false);
assert.ok(!JSON.stringify(r).includes('SECRET'));
assert.equal(r.candidates.length,1);
assert.equal(r.search_observation.transport,'responses_sse');
await assert.rejects(()=>readSearchResponse(sse(events.slice(0,1)),{}),/Missing search terminal/);
await assert.rejects(()=>readSearchResponse(sse([...events,events.at(-1)]),{}),/Invalid search terminal/);
await assert.rejects(()=>readSearchResponse(sse([{type:'error'}]),{}),/Search stream error/);
await assert.rejects(()=>readSearchResponse(new Response('data: bad\n\n',{headers:{'content-type':'text/event-stream'}}),{}));
diagnostic={};await assert.rejects(()=>readSearchResponse(new Response('x'.repeat(2000001)),diagnostic),/Output bound/);assert.equal(diagnostic.body_truncated,true);
d=await readSearchResponse(new Response(JSON.stringify(final),{headers:{'content-type':'application/json'}}),{});
assert.equal(searchResult(d,'discover').search_observation.trace_status,'unavailable');
d=await readSearchResponse(new Response('{"error":{"message":"private"}}',{status:429}),{});assert.ok(d.error);
d=await readSearchResponse(sse([{type:'response.incomplete',response:{...final,status:'incomplete'}}]),{});assert.equal(d.status,'incomplete');
r=searchResult({...final,search_call_items:Array.from({length:40},(_,i)=>({...call,input:JSON.stringify({mode:i%2?'Top':'Latest',query:'长'.repeat(900)})}))},'discover');
assert.equal(r.search_observation.calls.length,8);assert.equal(r.search_observation.truncated,true);assert.ok(Buffer.byteLength(JSON.stringify(r))<=24000);
r=searchResult({...final,search_call_items:[{...call,input:'not JSON'}]},'discover');assert.deepEqual(r.search_observation.keyword_modes,[]);
r=searchResult({...final,search_call_items:[{...call,status:'in_progress'}]},'discover');assert.deepEqual(r.search_observation.keyword_modes,[]);
console.log('PASS: SSE framing/UTF8/terminal/bounds, safe Top/Latest observation, no invented metric validation');
