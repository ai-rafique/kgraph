"""Self-contained interactive graph.html (no CDN, works offline)."""
import json

from .report import community_info, loc

MAX_NODES = 900

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>kgraph · __TITLE__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#2a2f3a;--fg:#e6e8ee;--mute:#8b93a7;--acc:#7aa2ff}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font:13px/1.45 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
#c{position:fixed;inset:0}
#side{position:fixed;top:12px;left:12px;width:320px;max-height:calc(100% - 24px);overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
h1{font-size:14px;margin:0 0 4px}.mute{color:var(--mute)}
input[type=search]{width:100%;padding:7px 9px;border-radius:7px;border:1px solid var(--line);background:#0f1115;color:var(--fg);margin:8px 0}
label{display:inline-flex;gap:5px;align-items:center;margin-right:10px;color:var(--mute)}
#info{margin-top:10px;border-top:1px solid var(--line);padding-top:10px;word-break:break-word}
#info .k{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);margin-top:8px}
#info a{color:var(--acc);cursor:pointer;text-decoration:none}
.comm{display:flex;gap:6px;align-items:center;cursor:pointer;padding:2px 0}.dot{width:9px;height:9px;border-radius:50%;flex:none}
#tip{position:fixed;pointer-events:none;background:#000c;padding:4px 7px;border-radius:5px;display:none;max-width:360px}
</style></head><body>
<canvas id="c"></canvas><div id="tip"></div>
<div id="side"><h1>__TITLE__</h1><div class="mute">__STATS__</div>
<input id="q" type="search" placeholder="Search nodes…">
<label><input type="checkbox" id="showC" checked>concepts</label><label><input type="checkbox" id="showE">externals</label>
<div id="info"><div class="mute">Click a node to see its connections. Drag to pan, scroll to zoom.</div><div class="k">Communities</div><div id="comms"></div></div></div>
<script>
const D=__DATA__;
const cv=document.getElementById('c'),cx=cv.getContext('2d'),tip=document.getElementById('tip');
let W,H,dpr=devicePixelRatio||1;function rs(){W=innerWidth;H=innerHeight;cv.width=W*dpr;cv.height=H*dpr;cv.style.width=W+'px';cv.style.height=H+'px'}rs();addEventListener('resize',rs);
const pal=['#7aa2ff','#f7768e','#9ece6a','#e0af68','#bb9af7','#2ac3de','#ff9e64','#73daca','#c0caf5','#db4b4b','#41a6b5','#a9b1d6','#e5c07b','#56b6c2','#c678dd','#98c379'];
const col=c=>c==null||c<0?'#555b68':pal[c%pal.length];
const N=D.nodes,byId={};N.forEach((n,i)=>{n.i=i;byId[n.id]=n;n.x=(Math.random()-.5)*800;n.y=(Math.random()-.5)*800;n.vx=n.vy=0;n.nb=[]});
const E=D.edges.filter(e=>byId[e[0]]&&byId[e[1]]).map(e=>({s:byId[e[0]],t:byId[e[1]],r:e[2],inf:e[3]}));
E.forEach(e=>{e.s.nb.push([e.t,e.r,'→',e.inf]);e.t.nb.push([e.s,e.r,'←',e.inf])});
N.forEach(n=>n.r=3+Math.sqrt(n.nb.length)*1.6);
// seed positions by community for faster convergence
const cc={};N.forEach(n=>{const k=n.c??-1;if(!cc[k])cc[k]={a:Math.random()*6.28,d:150+Math.random()*350};n.x=Math.cos(cc[k].a)*cc[k].d+(Math.random()-.5)*80;n.y=Math.sin(cc[k].a)*cc[k].d+(Math.random()-.5)*80});
let drag=null,pan=null,moved=false,showC=true,showE=false,sel=null,hl=null,cam={x:W/2,y:H/2,k:.9},alpha=1;
const vis=n=>(showC||n.k!=='concept')&&(showE||n.k!=='external');
function step(){const vs=N.filter(vis);const g=new Map();const S=60;
 vs.forEach(n=>{const k=Math.floor(n.x/S)+','+Math.floor(n.y/S);(g.get(k)||g.set(k,[]).get(k)).push(n)});
 vs.forEach(n=>{const gx=Math.floor(n.x/S),gy=Math.floor(n.y/S);for(let i=-2;i<=2;i++)for(let j=-2;j<=2;j++){const b=g.get((gx+i)+','+(gy+j));if(!b)continue;for(const m of b){if(m===n)continue;let dx=n.x-m.x,dy=n.y-m.y,d2=dx*dx+dy*dy+.01;if(d2>14400)continue;const f=900/d2*alpha;n.vx+=dx*f*.05;n.vy+=dy*f*.05}}});
 E.forEach(e=>{if(!vis(e.s)||!vis(e.t))return;const dx=e.t.x-e.s.x,dy=e.t.y-e.s.y,d=Math.sqrt(dx*dx+dy*dy)+.01;const L=e.r==='defines'||e.r==='contains'?30:70;const f=(d-L)/d*.02*alpha*(e.inf?.4:1);e.s.vx+=dx*f;e.s.vy+=dy*f;e.t.vx-=dx*f;e.t.vy-=dy*f});
 const cen={};vs.forEach(n=>{const k=n.c??-1;const c=cen[k]||(cen[k]={x:0,y:0,n:0});c.x+=n.x;c.y+=n.y;c.n++});
 vs.forEach(n=>{const c=cen[n.c??-1];if(c&&c.n>2){n.vx+=(c.x/c.n-n.x)*.006*alpha;n.vy+=(c.y/c.n-n.y)*.006*alpha}});
 vs.forEach(n=>{n.vx-=n.x*.0008*alpha;n.vy-=n.y*.0008*alpha;if(n===drag)return;n.x+=n.vx;n.y+=n.vy;n.vx*=.82;n.vy*=.82});alpha=Math.max(.02,alpha*.994)}
function draw(){cx.setTransform(dpr,0,0,dpr,0,0);cx.clearRect(0,0,W,H);cx.translate(cam.x,cam.y);cx.scale(cam.k,cam.k);
 const act=hl;cx.lineWidth=.6/cam.k;
 E.forEach(e=>{if(!vis(e.s)||!vis(e.t))return;const on=act&&(act.has(e.s)&&act.has(e.t)&&(e.s===sel||e.t===sel));cx.strokeStyle=on?'#ffffffaa':act?'#ffffff08':e.inf?'#ffffff10':'#ffffff22';cx.setLineDash(e.inf?[3,3]:[]);cx.beginPath();cx.moveTo(e.s.x,e.s.y);cx.lineTo(e.t.x,e.t.y);cx.stroke()});cx.setLineDash([]);
 N.forEach(n=>{if(!vis(n))return;const dim=act&&!act.has(n);cx.globalAlpha=dim?.15:1;cx.fillStyle=col(n.c);cx.beginPath();if(n.k==='concept'){cx.rect(n.x-n.r*.8,n.y-n.r*.8,n.r*1.6,n.r*1.6)}else cx.arc(n.x,n.y,n.r,0,6.283);cx.fill();if(n===sel){cx.strokeStyle='#fff';cx.lineWidth=2/cam.k;cx.stroke()}});
 cx.globalAlpha=1;cx.fillStyle='#e6e8ee';cx.font=`${11/cam.k}px system-ui`;
 N.forEach(n=>{if(!vis(n))return;if((act&&act.has(n))||(!act&&n.r*cam.k>9))cx.fillText(n.l.length>40?n.l.slice(0,38)+'…':n.l,n.x+n.r+2,n.y+3)})}
function loop(){if(alpha>.021)step();draw();requestAnimationFrame(loop)}loop();
const w2s=(x,y)=>[(x-cam.x)/cam.k,(y-cam.y)/cam.k];
function pick(mx,my){const[x,y]=w2s(mx,my);let b=null,bd=1e9;N.forEach(n=>{if(!vis(n))return;const d=(n.x-x)**2+(n.y-y)**2;if(d<bd&&d<(n.r+4/cam.k)**2){bd=d;b=n}});return b}
cv.onmousedown=e=>{moved=false;const n=pick(e.clientX,e.clientY);if(n)drag=n;else pan=[e.clientX-cam.x,e.clientY-cam.y]};
onmousemove=e=>{if(drag){const[x,y]=w2s(e.clientX,e.clientY);drag.x=x;drag.y=y;alpha=Math.max(alpha,.3);moved=true}else if(pan){cam.x=e.clientX-pan[0];cam.y=e.clientY-pan[1];moved=true}
 const n=pick(e.clientX,e.clientY);if(n){tip.style.display='block';tip.style.left=e.clientX+12+'px';tip.style.top=e.clientY+12+'px';tip.textContent=n.l+' · '+n.k+(n.f?' · '+n.f:'')}else tip.style.display='none'};
onmouseup=e=>{if(!moved&&e.target===cv)select(pick(e.clientX,e.clientY));drag=pan=null};
cv.onwheel=e=>{e.preventDefault();const f=Math.exp(-e.deltaY*.0015);cam.x=e.clientX-(e.clientX-cam.x)*f;cam.y=e.clientY-(e.clientY-cam.y)*f;cam.k*=f};
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function select(n){sel=n;if(!n){hl=null;renderComms();return}hl=new Set([n,...n.nb.map(x=>x[0])]);
 const grp={};n.nb.forEach(([m,r,d,inf])=>{const k=d==='→'?r+' →':'← '+r;(grp[k]=grp[k]||[]).push([m,inf])});
 let h=`<div><b>${esc(n.l)}</b></div><div class="mute">${n.k}${n.loc?' · '+esc(n.loc):''} · C${n.c}</div>`+(n.s?`<div style="margin-top:6px">${esc(n.s)}</div>`:'');
 for(const k in grp){h+=`<div class="k">${esc(k)} (${grp[k].length})</div>`+grp[k].slice(0,40).map(([m,inf])=>`<div><a data-i="${m.i}">${inf?'~':''}${esc(m.l)}</a></div>`).join('')}
 info.innerHTML=h;info.querySelectorAll('a').forEach(a=>a.onclick=()=>{const m=N[+a.dataset.i];select(m);cam.x=W/2-m.x*cam.k;cam.y=H/2-m.y*cam.k})}
function renderComms(){info.innerHTML='<div class="mute">Click a node to see its connections. Drag to pan, scroll to zoom.</div><div class="k">Communities</div>'+D.comms.map(c=>`<div class="comm" data-c="${c[0]}"><span class="dot" style="background:${col(c[0])}"></span>C${c[0]} ${esc(c[1])} <span class="mute">(${c[2]})</span></div>`).join('');
 info.querySelectorAll('.comm').forEach(d=>d.onclick=()=>{const c=+d.dataset.c;hl=new Set(N.filter(n=>n.c===c));sel=null})}
renderComms();
q.oninput=()=>{const s=q.value.toLowerCase().trim();if(!s){hl=null;return}hl=new Set(N.filter(n=>n.l.toLowerCase().includes(s)||(n.f||'').toLowerCase().includes(s)));sel=null;
 if(hl.size===1){const m=[...hl][0];select(m)}};
const showC_el=document.getElementById('showC');showC_el.onchange=()=>{showC=showC_el.checked;alpha=.5};
const showE_el=document.getElementById('showE');showE_el.onchange=()=>{showE=showE_el.checked;alpha=.5};
</script></body></html>"""


def write_html(g, path):
    nodes = sorted(g.nodes.values(), key=lambda n: -g.degree(n.id))[:MAX_NODES]
    keep = {n.id for n in nodes}
    data = {
        "nodes": [{"id": n.id, "l": n.label, "k": n.kind, "c": n.community, "f": n.file,
                   "loc": loc(n), "s": n.summary[:200]} for n in nodes],
        "edges": [[e.src, e.dst, e.rel, 1 if e.conf == "INFERRED" else 0]
                  for e in g.edges if e.src in keep and e.dst in keep],
        "comms": [[c, d["name"], len(d["members"])] for c, d in
                  sorted(community_info(g).items(), key=lambda kv: -len(kv[1]["members"]))[:40]],
    }
    title = (g.meta.get("root") or "project").rstrip("/").rsplit("/", 1)[-1]
    stats = f"{len(g.nodes)} nodes · {len(g.edges)} edges" + \
        (f" · showing top {MAX_NODES}" if len(g.nodes) > MAX_NODES else "")
    html = (TEMPLATE.replace("__TITLE__", title).replace("__STATS__", stats)
            .replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
