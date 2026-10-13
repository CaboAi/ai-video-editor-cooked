// Claude's questions in this page (askhook.py). Loaded by home.html and review.html; uses each page's own
// colour tokens, so it follows the review page's dark mode. Polls GET /ask every second.
(()=>{
const css=`#askq{position:fixed;inset:0;z-index:9999;display:none;align-items:center;justify-content:center;padding:16px;background:rgba(12,10,9,.36);backdrop-filter:blur(2px)}
#askq.on{display:flex}
#askq .aq-card{--a-panel:var(--panel,#fff);--a-ink:var(--ink,#0C0A09);--a-line:var(--line,#E7E5E4);--a-muted:var(--muted,var(--t3,#78716C));
 --a-body:var(--body,var(--t2,#57534E));--a-soft:var(--chip,var(--soft,#F5F5F4));--a-onink:var(--onink,#fff);
 width:100%;max-width:560px;max-height:calc(100vh - 32px);overflow:auto;background:var(--a-panel);color:var(--a-ink);border:1px solid var(--a-line);
 border-radius:20px;padding:26px 26px 18px;box-shadow:0 12px 40px rgba(0,0,0,.18);font:14px/20px system-ui,-apple-system,"Segoe UI",sans-serif;text-align:left}
#askq .aq-top{display:flex;justify-content:space-between;gap:12px;font-size:13px;line-height:16px;color:var(--a-muted)}
#askq .aq-hd{display:inline-block;margin-top:14px;padding:3px 9px;border-radius:7px;background:var(--a-soft);color:var(--a-body);font-size:12px;line-height:16px;font-weight:600}
#askq h2{margin:10px 0 18px;font-size:20px;line-height:27px;font-weight:600;letter-spacing:-.01em;color:var(--a-ink)}
#askq .aq-opts{display:flex;flex-direction:column;gap:8px}
#askq .aq-opt{display:flex;gap:12px;align-items:flex-start;width:100%;margin:0;padding:13px 16px;border:1px solid var(--a-line);border-radius:14px;background:none;color:inherit;font:inherit;text-align:left;cursor:pointer}
#askq .aq-opt:hover,#askq .aq-opt:focus-visible{border-color:var(--a-ink);outline:none}
#askq .aq-opt.aq-sel{border:1.5px solid var(--a-ink);padding:12.5px 15.5px}
#askq .aq-opt b{display:block;font-size:15px;line-height:20px;font-weight:600}
#askq .aq-opt span{display:block;margin-top:2px;color:var(--a-body);font-size:13px;line-height:18px}
#askq .aq-box{width:18px;height:18px;margin-top:1px;flex-shrink:0;border:1.5px solid var(--a-line);border-radius:5px;display:flex;align-items:center;justify-content:center}
#askq .aq-sel .aq-box{background:var(--a-ink);border-color:var(--a-ink);color:var(--a-onink)}
#askq .aq-other{display:flex;gap:8px;margin-top:14px}
#askq input{flex:1;min-width:0;height:44px;margin:0;padding:0 14px;border:1px solid var(--a-line);border-radius:12px;background:none;color:inherit;font:inherit}
#askq input:focus{outline:none;border:1.5px solid var(--a-ink);padding:0 13.5px}
#askq .aq-send{height:44px;margin:0;padding:0 20px;border:0;border-radius:12px;background:var(--a-ink);color:var(--a-onink);font:inherit;font-weight:600;cursor:pointer;flex-shrink:0}
#askq .aq-send:disabled{opacity:.35;cursor:default}
#askq .aq-clip{width:44px;height:44px;margin:0;padding:0;border:1px solid var(--a-line);border-radius:12px;background:none;color:var(--a-body);display:grid;place-items:center;cursor:pointer;flex-shrink:0}
#askq .aq-clip:hover,#askq .aq-clip:focus-visible{border-color:var(--a-ink);color:var(--a-ink);outline:none}
#askq .aq-pics{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}#askq .aq-pics:empty{display:none}
#askq .aq-pics span{position:relative}#askq .aq-pics img{height:56px;max-width:120px;object-fit:cover;border-radius:8px;border:1px solid var(--a-line);display:block}
#askq .aq-pics button{position:absolute;top:-7px;right:-7px;width:20px;height:20px;margin:0;padding:0;border:0;border-radius:50%;background:var(--a-ink);color:var(--a-onink);font:12px/20px system-ui,sans-serif;cursor:pointer}
#askq .aq-card.aq-drag{border-color:var(--a-ink)}
#askq .aq-wide{width:100%;margin-top:12px}
#askq .aq-foot{margin-top:16px;padding:0;border:0;text-align:center}
#askq .aq-term{border:0;background:none;margin:0;padding:4px;color:var(--a-muted);font:inherit;font-size:13px;text-decoration:underline 1px;text-underline-offset:3px;cursor:pointer}
#askq .aq-done{padding:18px 0 10px;font-size:15px;color:var(--a-body);text-align:center}
@media (max-width:520px){#askq{align-items:flex-end}#askq .aq-card{padding:22px 18px 14px;border-radius:18px}#askq h2{font-size:18px;line-height:25px}}`;
const CLIP='<svg width="16" height="16" viewBox="0 0 16 16"><path d="M13 7.5l-5.3 5.3a3.2 3.2 0 01-4.5-4.5l5.6-5.6a2.1 2.1 0 013 3l-5.5 5.5a1 1 0 01-1.5-1.5L9.9 4.6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
const TYPES=/^image\/(png|jpeg|gif|webp)$/,MAXB=15<<20,MAXN=6;   // the review notes' limits (askhook.save_images)
const TICK='<svg width="11" height="11" viewBox="0 0 12 12"><path d="M2 6.2l2.6 2.6L10 3.4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const st=document.createElement('style');st.textContent=css;document.head.appendChild(st);
const el=document.createElement('div');el.id='askq';el.setAttribute('role','dialog');el.setAttribute('aria-modal','true');el.setAttribute('aria-label','Claude has a question');
el.addEventListener('keydown',e=>e.stopPropagation());   // the page's own shortcuts stay off while typing here
document.body.appendChild(el);
let A=null,i=0,ans=[],imgs=[],pics=[],sel=new Set(),title=null;
// images on this answer: picked, pasted or dropped; data urls until Send
function showPics(){const b=el.querySelector('.aq-pics');if(!b)return;b.innerHTML=pics.map((p,k)=>`<span><img src="${p}" alt="attached image ${k+1}"><button data-rm="${k}" aria-label="remove image" title="remove">×</button></span>`).join('');el.querySelector('#asko')?.dispatchEvent(new Event('input'))}
function addFiles(fs){[...fs].filter(f=>TYPES.test(f.type)&&f.size<=MAXB).slice(0,MAXN-pics.length).forEach(f=>{const r=new FileReader();r.onload=()=>{if(A&&pics.length<MAXN){pics.push(r.result);showPics()}};r.readAsDataURL(f)})}
el.addEventListener('paste',e=>{const fs=[...(e.clipboardData?.files||[])];if(A&&fs.length){e.preventDefault();addFiles(fs)}});
el.addEventListener('dragover',e=>{if(!A)return;e.preventDefault();el.querySelector('.aq-card')?.classList.add('aq-drag')});
el.addEventListener('dragleave',e=>{if(!el.contains(e.relatedTarget))el.querySelector('.aq-card')?.classList.remove('aq-drag')});
el.addEventListener('drop',e=>{if(!A)return;e.preventDefault();el.querySelector('.aq-card')?.classList.remove('aq-drag');addFiles(e.dataTransfer.files)});
const TITLE='(1) Claude has a question';
function chime(){try{const c=new AudioContext(),o=c.createOscillator(),g=c.createGain();o.frequency.value=660;g.gain.setValueAtTime(.0001,c.currentTime);
  g.gain.exponentialRampToValueAtTime(.06,c.currentTime+.02);g.gain.exponentialRampToValueAtTime(.0001,c.currentTime+.35);o.connect(g).connect(c.destination);o.start();o.stop(c.currentTime+.4)}catch(e){}}
async function send(b){try{await fetch('/ask/answer',{method:'POST',body:JSON.stringify(Object.assign({id:A.id},b))})}catch(e){}}
function close(msg){
  if(msg){el.innerHTML=`<div class="aq-card"><div class="aq-done">${esc(msg)}</div></div>`;setTimeout(()=>{if(!A)el.classList.remove('on')},1400)}else el.classList.remove('on');
  A=null;if(title!==null&&document.title===TITLE)document.title=title;title=null}
function next(v){ans[i]=v;imgs[i]=pics;pics=[];i++;sel=new Set();el.innerHTML='';if(i<A.questions.length)return draw();
  send(imgs.some(m=>m.length)?{answers:ans,images:imgs}:{answers:ans});close('Sent. Claude has your answer.')}
function draw(){
  const q=A.questions[i],n=A.questions.length,multi=!!q.multiSelect,opts=Array.isArray(q.options)?q.options:[];
  const own=el.querySelector('#asko')?.value||'';
  el.innerHTML=`<div class="aq-card"><div class="aq-top"><span>${A.about?`Claude (${esc(A.about)}) asks`:'Claude has a question'}</span><span>${n>1?`Question ${i+1} of ${n}`:''}</span></div>
  ${q.header?`<div class="aq-hd">${esc(q.header)}</div>`:''}<h2${q.header?'':' style="margin-top:14px"'}>${esc(q.question)}</h2>
  <div class="aq-opts">${opts.map((o,k)=>`<button class="aq-opt${sel.has(k)?' aq-sel':''}" data-k="${k}"${multi?` aria-pressed="${sel.has(k)}"`:''}>${multi?`<i class="aq-box">${sel.has(k)?TICK:''}</i>`:''}<div><b>${esc(o.label)}</b>${o.description?`<span>${esc(o.description)}</span>`:''}</div></button>`).join('')}</div>
  <div class="aq-other"><input id="asko" placeholder="${multi?'Other: add your own words':'Other: type your own answer'}" aria-label="Other answer"><button class="aq-clip" id="askp" aria-label="attach an image" title="attach an image (or paste or drop one)">${CLIP}</button><input type="file" id="aski" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden>${multi?'':'<button class="aq-send" id="asks" disabled>Send</button>'}</div>
  <div class="aq-pics"></div>
  ${multi?'<button class="aq-send aq-wide" id="asks" disabled>Send</button>':''}
  <div class="aq-foot"><button class="aq-term" id="askt">Answer in Claude Code instead</button></div></div>`;
  const $o=el.querySelector('#asko'),$s=el.querySelector('#asks');
  $o.value=own;
  const value=()=>multi?[...sel].sort((a,b)=>a-b).map(k=>opts[k].label).concat($o.value.trim()?[$o.value.trim()]:[]).join(', '):$o.value.trim();
  const ready=()=>$s.disabled=!value()&&!pics.length;
  showPics();ready();
  el.querySelector('#askp').onclick=()=>el.querySelector('#aski').click();
  el.querySelector('#aski').onchange=e=>{addFiles(e.target.files);e.target.value=''};
  el.querySelector('.aq-pics').onclick=e=>{const b=e.target.closest('[data-rm]');if(b){pics.splice(+b.dataset.rm,1);showPics()}};
  el.querySelectorAll('.aq-opt').forEach(b=>b.onclick=()=>{const k=+b.dataset.k;if(!multi)return next(opts[k].label);sel.has(k)?sel.delete(k):sel.add(k);draw()});
  $o.oninput=ready;
  $o.onkeydown=e=>{if(e.key==='Enter'&&!$s.disabled)$s.click()};
  $s.onclick=()=>{if(value()||pics.length)next(value())};
  el.querySelector('#askt').onclick=()=>{send({terminal:true});close('Answer it in Claude Code.')};
  el.classList.add('on');
}
async function poll(){
  let a=null;
  try{const r=await fetch('/ask'+(document.visibilityState==='visible'?'?v=1':''));if(!r.ok)return;a=(await r.json()).ask}catch(e){return}
  if(!a){if(A)close();return}
  if(document.title!==TITLE){title=document.title;document.title=TITLE}
  if(A&&A.id===a.id)return;
  A=a;i=0;ans=[];imgs=[];pics=[];sel=new Set();el.innerHTML='';draw();chime();
}
poll();setInterval(poll,1000);document.addEventListener('visibilitychange',poll);
addEventListener('pagehide',()=>{try{navigator.sendBeacon('/ask/gone')}catch(e){}});   // a closed tab: Claude's next question goes to the terminal at once
})();
