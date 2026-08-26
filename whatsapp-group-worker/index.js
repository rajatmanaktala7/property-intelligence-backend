import makeWASocket,{DisconnectReason,fetchLatestBaileysVersion,useMultiFileAuthState}from"@whiskeysockets/baileys";
import P from"pino";
import QRCode from"qrcode";
import fs from"fs/promises";
import http from"http";
import crypto from"crypto";

const BACKEND_URL=(process.env.BACKEND_URL||"").replace(/\/+$/,"");
const WA_BRIDGE_TOKEN=process.env.WA_BRIDGE_TOKEN||"";
const ACCOUNT_PHONE=(process.env.ACCOUNT_PHONE||"").trim();
const AUTH_DIR=process.env.WA_AUTH_DIR||"/data/wa_auth";
const PORT=Number(process.env.PORT||8080);
const QR_PAGE_TOKEN=(process.env.QR_PAGE_TOKEN||"").trim();
const GROUP_ALLOWLIST=(process.env.GROUP_ALLOWLIST||"").split("|").map(x=>x.trim()).filter(Boolean);
const SEND_OLD_MESSAGES=/^(1|true|yes|on)$/i.test(process.env.SEND_OLD_MESSAGES||"false");

if(!BACKEND_URL)throw new Error("BACKEND_URL is required");
if(!WA_BRIDGE_TOKEN)throw new Error("WA_BRIDGE_TOKEN is required");
if(!ACCOUNT_PHONE)throw new Error("ACCOUNT_PHONE is required");
if(!QR_PAGE_TOKEN)throw new Error("QR_PAGE_TOKEN is required");
await fs.mkdir(AUTH_DIR,{recursive:true});

const logger=P({level:process.env.LOG_LEVEL||"info"});
const recentIds=new Map();
const stateView={status:"STARTING",qrDataUrl:null,qrGeneratedAt:null,connectedAt:null,lastMessageAt:null,lastGroupName:null,deliveredCount:0,lastError:null,startedAt:new Date().toISOString()};

function safeEqual(a,b){const A=Buffer.from(String(a||"")),B=Buffer.from(String(b||""));return A.length===B.length&&crypto.timingSafeEqual(A,B)}
function maskPhone(p){const s=String(p||"");return s.length<=4?"****":"*".repeat(Math.max(4,s.length-4))+s.slice(-4)}
function remember(id){const n=Date.now();recentIds.set(id,n);for(const[k,t]of recentIds)if(n-t>21600000)recentIds.delete(k)}
function alreadySeen(id){return recentIds.has(id)}
function getText(m){if(!m)return"";return m.conversation||m.extendedTextMessage?.text||m.imageMessage?.caption||m.videoMessage?.caption||m.documentMessage?.caption||m.buttonsResponseMessage?.selectedDisplayText||m.listResponseMessage?.title||""}
function normalizePhone(jid){if(!jid)return"";return String(jid).split("@")[0].split(":")[0].replace(/\D/g,"")}
function esc(v){return String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}
function fmt(v){if(!v)return"—";try{return new Date(v).toLocaleString("en-IN",{timeZone:"Asia/Kolkata",dateStyle:"medium",timeStyle:"medium"})}catch{return String(v)}}

function page(){
 const connected=stateView.status==="CONNECTED";
 const qr=stateView.qrDataUrl&&!connected;
 return`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="8"><title>Alliance WhatsApp Live</title><style>
 body{margin:0;padding:28px 16px;background:#f3f7f5;color:#14231b;font-family:system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:760px;margin:auto}.card{background:white;border:1px solid #dfe8e3;border-radius:22px;padding:28px;box-shadow:0 12px 35px #00000012}.brand{font-size:13px;font-weight:800;letter-spacing:.08em;color:#537064;text-transform:uppercase;margin-bottom:10px}h1{margin:0 0 8px;font-size:30px}.sub{color:#66766e;margin-bottom:20px}.status{display:inline-block;padding:9px 14px;border-radius:999px;font-weight:800;background:${connected?"#e5f7ed":"#fff3d4"};color:${connected?"#087443":"#805b00"};margin-bottom:20px}.qr{display:block;width:min(520px,100%);margin:10px auto;border:16px solid white;border-radius:16px;box-shadow:0 0 0 1px #d7e1dc}.center{text-align:center;font-size:18px;line-height:1.55}.ok{padding:22px;border-radius:16px;background:#e9f8ef;color:#11653d;font-size:18px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:24px}.m{border:1px solid #e1e8e4;border-radius:14px;padding:14px}.m b{display:block;font-size:12px;color:#738079;text-transform:uppercase;margin-bottom:5px}.err{margin-top:18px;padding:12px;background:#fff0f0;color:#922;border-radius:12px}.foot{text-align:center;color:#78867f;font-size:12px;margin-top:16px}@media(max-width:620px){.grid{grid-template-columns:1fr}.card{padding:20px}}
 </style></head><body><div class="wrap"><div class="brand">Alliance Property Intelligence</div><div class="card"><h1>WhatsApp Live Connection</h1><div class="sub">Persistent group ingestion worker</div><div class="status">${connected?"● WHATSAPP LIVE":"● "+esc(stateView.status)}</div>
 ${qr?`<img class="qr" src="${stateView.qrDataUrl}" alt="WhatsApp QR"><div class="center"><b>Scan now</b><br>WhatsApp → Linked devices → Link a device<br><small>QR generated ${esc(fmt(stateView.qrGeneratedAt))}</small></div>`:connected?`<div class="ok"><b>✓ Connected.</b><br>New approved group messages are flowing to the Alliance app.</div>`:`<div class="center">Preparing QR / reconnecting…</div>`}
 <div class="grid"><div class="m"><b>Account</b>${maskPhone(ACCOUNT_PHONE)}</div><div class="m"><b>Session</b>${esc(AUTH_DIR)}</div><div class="m"><b>Connected Since</b>${esc(fmt(stateView.connectedAt))}</div><div class="m"><b>Last Message</b>${esc(fmt(stateView.lastMessageAt))}</div><div class="m"><b>Last Group</b>${esc(stateView.lastGroupName||"—")}</div><div class="m"><b>Delivered</b>${stateView.deliveredCount}</div></div>
 ${stateView.lastError?`<div class="err"><b>Latest error:</b> ${esc(stateView.lastError)}</div>`:""}</div><div class="foot">Protected by QR_PAGE_TOKEN. Do not share this URL.</div></div></body></html>`
}

http.createServer((req,res)=>{
 const u=new URL(req.url||"/",`http://${req.headers.host||"localhost"}`);
 if(u.pathname==="/health"){res.writeHead(200,{"content-type":"application/json"});return res.end(JSON.stringify({ok:true,status:stateView.status,connected:stateView.status==="CONNECTED"}))}
 const token=u.searchParams.get("token")||req.headers["x-qr-page-token"]||"";
 if(!safeEqual(token,QR_PAGE_TOKEN)){res.writeHead(401,{"content-type":"text/html","cache-control":"no-store"});return res.end("<h2>Alliance WhatsApp Live</h2><p>Protected page. Add <code>?token=YOUR_QR_PAGE_TOKEN</code>.</p>")}
 if(u.pathname==="/"||u.pathname==="/connect"){res.writeHead(200,{"content-type":"text/html","cache-control":"no-store"});return res.end(page())}
 if(u.pathname==="/api/status"){res.writeHead(200,{"content-type":"application/json","cache-control":"no-store"});return res.end(JSON.stringify({...stateView,qrDataUrl:undefined,connected:stateView.status==="CONNECTED"}))}
 res.writeHead(404);res.end("Not found")
}).listen(PORT,"0.0.0.0",()=>logger.info({port:PORT},"QR/status web page ready"));

async function postWithRetry(payload){
 let last=null;
 for(let a=1;a<=5;a++){try{const r=await fetch(`${BACKEND_URL}/whatsapp-live/api/ingest`,{method:"POST",headers:{"content-type":"application/json","x-bridge-token":WA_BRIDGE_TOKEN},body:JSON.stringify(payload)});const b=await r.text();if(r.ok){stateView.deliveredCount++;stateView.lastError=null;logger.info({group:payload.group_name,messageId:payload.external_message_id},"Message delivered to Alliance app");return}if(r.status===403){logger.warn({group:payload.group_name,response:b},"Group inactive; skipped");return}throw new Error(`Backend ${r.status}: ${b}`)}catch(e){last=e;stateView.lastError=String(e);await new Promise(r=>setTimeout(r,Math.min(30000,1000*2**a)))}}
 logger.error({err:String(last)},"Message delivery permanently failed")
}

let starting=false;
async function start(){
 if(starting)return;starting=true;
 try{
  stateView.status="CONNECTING";
  const{state,saveCreds}=await useMultiFileAuthState(AUTH_DIR);
  const{version}=await fetchLatestBaileysVersion();
  const sock=makeWASocket({version,auth:state,logger,browser:["Alliance Property Intelligence","Chrome","2.0.0"],syncFullHistory:SEND_OLD_MESSAGES,markOnlineOnConnect:false,generateHighQualityLinkPreview:false});
  sock.ev.on("creds.update",saveCreds);
  sock.ev.on("connection.update",async u=>{
   const{connection,lastDisconnect,qr}=u;
   if(qr){stateView.status="WAITING_FOR_QR";stateView.qrDataUrl=await QRCode.toDataURL(qr,{errorCorrectionLevel:"M",width:620,margin:3});stateView.qrGeneratedAt=new Date().toISOString();stateView.lastError=null;logger.info("New protected browser QR generated")}
   if(connection==="open"){stateView.status="CONNECTED";stateView.connectedAt=new Date().toISOString();stateView.qrDataUrl=null;stateView.qrGeneratedAt=null;stateView.lastError=null;logger.info("WhatsApp linked. Live group capture is ON.")}
   if(connection==="close"){const code=lastDisconnect?.error?.output?.statusCode||lastDisconnect?.error?.statusCode||0;const loggedOut=code===DisconnectReason.loggedOut;stateView.status=loggedOut?"LOGGED_OUT":"RECONNECTING";stateView.lastError=lastDisconnect?.error?String(lastDisconnect.error):`Connection closed (${code||"unknown"})`;if(!loggedOut)setTimeout(()=>{starting=false;start().catch(e=>logger.error({err:String(e)},"Reconnect failed"))},3000)}
  });
  sock.ev.on("messages.upsert",async({messages,type})=>{
   if(type!=="notify"&&!SEND_OLD_MESSAGES)return;
   for(const msg of messages)try{
    const jid=msg?.key?.remoteJid||"";if(!jid.endsWith("@g.us")||msg.key.fromMe)continue;
    const text=getText(msg.message).trim();if(!text)continue;
    const id=msg.key.id||"";if(!id||alreadySeen(id))continue;remember(id);
    let meta=null;try{meta=await sock.groupMetadata(jid)}catch{}
    const groupName=(meta?.subject||jid).trim();stateView.lastMessageAt=new Date().toISOString();stateView.lastGroupName=groupName;
    if(GROUP_ALLOWLIST.length&&!GROUP_ALLOWLIST.includes(groupName))continue;
    const pj=msg.key.participant||msg.participant||msg.message?.extendedTextMessage?.contextInfo?.participant||"";
    const senderPhone=normalizePhone(pj),senderName=msg.pushName||senderPhone||"Unknown";
    const epoch=Number(msg.messageTimestamp||0),timestamp=epoch?new Date(epoch*1000).toISOString():new Date().toISOString();
    await postWithRetry({account_phone:ACCOUNT_PHONE,group_name:groupName,external_message_id:id,sender_name:senderName,sender_phone:senderPhone,timestamp,text})
   }catch(e){stateView.lastError=String(e);logger.error({err:String(e)},"Could not process incoming group message")}
  });
  starting=false;
 }catch(e){starting=false;stateView.status="ERROR";stateView.lastError=String(e);throw e}
}
start().catch(e=>{stateView.status="ERROR";stateView.lastError=String(e);logger.fatal({err:String(e)},"Worker failed to start")});
