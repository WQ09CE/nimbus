// Responses SSE collector. Total body bound and EOF/terminal checks apply before success.
// Keep raw bytes only in the existing private error diagnostic; never return reasoning traces.
export async function readSearchResponse(res:any, diagnostic:any){
  const reader=res.body?.getReader();if(!reader)throw new Error('No body');
  const chunks:Uint8Array[]=[];let size=0,buffer='',terminal:any=null,terminals=0;
  const decoder=new TextDecoder();const calls:any[]=[];let callsSeen=0;
  const streaming=res.status===200&&String(res.headers.get('content-type')??'').includes('text/event-stream');
  let eventData:string[]=[];
  function event(){
    if(!eventData.length)return;
    const raw=eventData.join('\n');eventData=[];
    if(raw==='[DONE]')return;
    const e=JSON.parse(raw);
    if(e.type==='response.output_item.done'&&e.item?.type==='custom_tool_call'){
      callsSeen++;
      if(calls.length<32)calls.push(e.item);
    }
    if(['response.completed','response.incomplete','response.failed'].includes(e.type)){
      terminals++;if(terminals!==1||!e.response||typeof e.response!=='object')throw new Error('Invalid search terminal');
      terminal=e.response;
    }
    if(e.type==='error')throw new Error('Search stream error');
  }
  function line(s:string){
    if(s.endsWith('\r'))s=s.slice(0,-1);
    if(!s){event();return;}
    if(s.startsWith('data:'))eventData.push(s.slice(5).replace(/^ /,''));
  }
  try{
    while(true){
      const {done,value}=await reader.read();if(done)break;
      size+=value.length;
      if(size>2000000){await reader.cancel();diagnostic.body_truncated=true;throw new Error('Output bound');}
      chunks.push(value);
      if(streaming){
        buffer+=decoder.decode(value,{stream:true});let n;
        while((n=buffer.indexOf('\n'))>=0){line(buffer.slice(0,n));buffer=buffer.slice(n+1);}
      }
    }
    if(streaming){
      buffer+=decoder.decode();if(buffer)line(buffer);event();
      if(terminals!==1)throw new Error('Missing search terminal');
      // Keep output from the authoritative terminal response. Tool inputs may only appear
      // in item.done events; expose their safe projection separately, not reasoning.
      if(!Array.isArray(terminal.output))throw new Error('Missing search output');
      terminal.search_call_items=calls;
      terminal.search_calls_truncated=callsSeen>calls.length;
      terminal.search_transport='responses_sse';
      return terminal;
    }
    const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    data.search_transport='responses_json';return data;
  }catch(error){
    try{await reader.cancel();}catch{}
    throw error;
  }finally{
    diagnostic.response_body=Buffer.concat(chunks).toString('utf8');
    reader.releaseLock?.();
  }
}
