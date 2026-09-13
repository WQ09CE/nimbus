// Bounded projection of public provider evidence. No auth/request objects.
export function searchObservation(d:any){
  const items=Array.isArray(d.search_call_items)&&d.search_call_items.length?d.search_call_items:(d.output??[]);
  const calls:any[]=[];const modes=new Set<string>();let seen=0;
  for(const c of items){
    if(c?.type!=='custom_tool_call'||!['x_keyword_search','x_semantic_search','x_user_search','x_thread_fetch'].includes(c.name))continue;
    seen++;let input:any={};
    if(typeof c.input==='string'&&c.input.length<=8192)try{input=JSON.parse(c.input);}catch{}
    if(!input||typeof input!=='object'||Array.isArray(input))input={};
    const mode=c.name==='x_keyword_search'&&['Top','Latest'].includes(input.mode)?input.mode:null;
    const status=['completed','in_progress','incomplete'].includes(c.status)?c.status:'unknown';
    if(mode&&status==='completed')modes.add(mode);
    if(calls.length<8)calls.push({tool:c.name,status,mode,query:typeof input.query==='string'?input.query.slice(0,300):null});
  }
  return {transport:d.search_transport??'unknown',trace_status:seen?'observed':'unavailable',
    keyword_modes:[...modes],calls,truncated:seen>calls.length||d.search_calls_truncated===true,
    engagement_filter_verified:false,ranking_by_views_verified:false};
}
export function searchResult(d:any, mode:string) {
  const texts:string[]=[],finals:string[]=[],citations:any[]=[];let lastMessage:string[]=[];
  const sources=new Set<string>();let messages=0;
  for(const o of d.output??[])if(o.type==='message'){
    messages++;lastMessage=[];
    for(const c of o.content??[])if(c.type==='output_text'){
      const text=typeof c.text==='string'?c.text:'';texts.push(text);lastMessage.push(text);
      if(o.phase==='final_answer'||o.phase==='final')finals.push(text);
      for(const a of c.annotations??[])if(a.type==='url_citation'&&typeof a.url==='string'&&a.url.startsWith('https://')&&a.url.length<2000){
        sources.add(a.url);
        if(citations.length<40)citations.push({url:a.url,title:typeof a.title==='string'?a.title.slice(0,180):null});
      }
    }
  }
  const full=(finals.length?finals:lastMessage).join('\n\n');const text=full.slice(0,16000);
  const candidates:any[]=[];let structured=false,proposed=0;
  const canon=(url:string)=>{
    try{const u=new URL(url);const post=u.pathname.match(/^\/(?:i|[A-Za-z0-9_]+)\/status\/(\d+)\/?$/);
      if(['x.com','twitter.com','www.x.com','www.twitter.com'].includes(u.hostname)&&post)return 'x:'+post[1];
      u.hash='';return u.href;
    }catch{return '';}
  };
  const cited=new Set([...sources].map(canon));
  if(mode==='discover')try{
    const fences=[...text.matchAll(/```(?:json)?\s*([\s\S]*?)```/g)];
    const parsed=JSON.parse(fences.length===1?fences[0][1]:text);
    if(Array.isArray(parsed.candidates)){
      structured=true;proposed=parsed.candidates.length;
      for(const c of parsed.candidates.slice(0,10)){
        if(!c||typeof c.summary!=='string'||!c.summary.trim()||typeof c.url!=='string'||!c.url.startsWith('https://')||c.url.length>2000||!canon(c.url))continue;
        const date=typeof c.published_at==='string'&&c.published_at.length<50&&/(?:Z|[+-]\d{2}:\d{2})$/.test(c.published_at)&&Number.isFinite(Date.parse(c.published_at))?c.published_at:null;
        candidates.push({summary:c.summary.slice(0,500),url:c.url,
          author:typeof c.author==='string'?c.author.slice(0,100):null,published_at:date,
          evidence_type:['official','research','developer','discussion','unknown'].includes(c.evidence_type)?c.evidence_type:'unknown',
          excerpt:typeof c.excerpt==='string'?c.excerpt.slice(0,400):null,
          citation_bound:cited.has(canon(c.url)),
          verification:cited.has(canon(c.url))?'provider_claim_not_independently_verified':'uncited_lead_requires_lookup'});
      }
    }
  }catch{}
  const partial=!text||!sources.size||full.length>16000||(mode==='discover'&&(!structured||!candidates.length||candidates.length!==proposed||candidates.some(c=>!c.author||!c.published_at||!c.excerpt||!c.citation_bound)));
  const usage:any={};
  for(const key of ['x_search_calls','web_search_calls']){
    const n=d.usage?.server_side_tool_usage_details?.[key];
    if(Number.isSafeInteger(n)&&n>=0)usage[key]=n;
  }
  const result={text,sources:[...sources].slice(0,40),citations,candidates,
    quality:partial?'partial':'evidence_returned',truncated:full.length>16000,
    source_count:sources.size,tool_usage:usage,search_observation:searchObservation(d),
    projection:{version:2,mode,provider_status:d.status,message_count:messages,omitted_progress_messages:!finals.length?Math.max(0,messages-1):0,structured_candidates:structured,candidates_proposed:proposed,
      evidence_note:'Citations and candidate metadata are provider evidence pointers, not independent verification. Missing fields remain unknown.'}};
  while(Buffer.byteLength(JSON.stringify(result),'utf8')>24000){
    result.truncated=true;result.quality='partial';
    if(result.text.length>2000)result.text=result.text.slice(0,Math.floor(result.text.length*.7));
    else if(result.citations.length)result.citations.pop();
    else if(result.sources.length>1)result.sources.pop();
    else if(result.candidates.length)result.candidates.pop();
    else break;
  }
  return result;
}

export function searchFailure(stage:string, diagnostic:any, error:any){
  const http=diagnostic.http_status;
  if(typeof http==='number'&&http!==200)return {code:'upstream_http_error',http_status:http,retryable:http===429||http>=500,
    ...(http===429?{retry_after_seconds:diagnostic.retry_after_seconds??30}:{})};
  if(error?.name==='TimeoutError'||error?.name==='AbortError')return {code:'upstream_timeout',retryable:true};
  if(stage==='search_http')return {code:'upstream_network_error',retryable:true};
  if(error?.message?.startsWith('Search incomplete'))return {code:'provider_incomplete',retryable:false};
  if(error?.message==='No actual search')return {code:'provider_no_search',retryable:false};
  if(stage==='search_response'||stage==='search_body')return {code:'provider_response_invalid',retryable:false};
  return {code:'provider_error',retryable:false};
}
