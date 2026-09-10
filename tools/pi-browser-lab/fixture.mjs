import { randomBytes, randomInt } from 'node:crypto';

// Random values are generated only after the model calls the tool.
// The model sees canvas pixels, not this HTML or the oracle.
export function makeFixture() {
  const challenge = `ZM-${randomBytes(3).toString('hex').toUpperCase()}`;
  const receipt = `PASS-${randomBytes(4).toString('hex').toUpperCase()}`;
  const start = { x: randomInt(140, 800), y: randomInt(230, 410), w: 290, h: 65 };
  return `<!doctype html><html><head>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'">
<style>body{margin:0;background:#f5f7fb;font-family:sans-serif}canvas{display:block}
input{position:absolute;left:290px;top:330px;width:420px;height:52px;box-sizing:border-box;font:24px monospace;padding:8px;border:2px solid #4f46e5;border-radius:8px;display:none}</style>
</head><body><canvas width="1280" height="800"></canvas><input maxlength="40" autocomplete="off" spellcheck="false">
<script>
const canvas=document.querySelector('canvas'), ctx=canvas.getContext('2d'), field=document.querySelector('input');
const challenge=${JSON.stringify(challenge)}, receipt=${JSON.stringify(receipt)}, start=${JSON.stringify(start)};
let stage=0,error='', trustedClicks=0;
window.__labResult={stage:0,success:false,challenge,receipt:null,trustedClicks:0};
function text(s,x,y,size=24,color='#1e293b'){ctx.fillStyle=color;ctx.font=size+'px sans-serif';ctx.fillText(s,x,y)}
function button(b,label,color='#4f46e5'){ctx.fillStyle=color;ctx.beginPath();ctx.roundRect(b.x,b.y,b.w,b.h,12);ctx.fill();text(label,b.x+20,b.y+42,24,'white')}
function draw(){
ctx.fillStyle='#f5f7fb';ctx.fillRect(0,0,1280,800);
text('Astra / Pi visual interaction lab',65,85,36);
text('Synthetic offline fixture - no real accounts or external network',65,130,20,'#64748b');
if(stage===0){text('Open validation to begin.',65,190);button(start,'Open validation');}
if(stage===1){text('Type the displayed challenge, then click Verify.',150,230,28);text('Challenge: '+challenge,290,295,28);button({x:750,y:430,w:210,h:65},'Verify');text(error,290,420,22,'#b91c1c');}
if(stage===2){text('Validation complete',210,285,38,'#15803d');text(receipt,210,365,34);text('Report the receipt shown above.',210,425,24);}
}
function inside(e,b){return e.offsetX>=b.x&&e.offsetX<=b.x+b.w&&e.offsetY>=b.y&&e.offsetY<=b.y+b.h}
canvas.addEventListener('click',e=>{
if(e.isTrusted)trustedClicks++;
if(stage===0&&inside(e,start)){stage=1;field.style.display='block';}
else if(stage===1&&inside(e,{x:750,y:430,w:210,h:65})){
 if(field.value===challenge){stage=2;field.style.display='none';}else error='Challenge does not match. Try again.';
}
window.__labResult={stage,success:stage===2,challenge,receipt:stage===2?receipt:null,entered:field.value,trustedClicks};draw();
});
draw();
</script></body></html>`;
}
