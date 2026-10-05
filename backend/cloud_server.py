#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse,base64,hashlib,hmac,json,os,random,re,sqlite3,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse

CARD_SECRET=os.environ.get('FRAMEWEAVE_CARD_SECRET',''); ADMIN_KEY=os.environ.get('FRAMEWEAVE_ADMIN_KEY','')
ADMIN_ALLOW_IPS=[x.strip() for x in os.environ.get('FRAMEWEAVE_ADMIN_ALLOW_IPS','').split(',') if x.strip()]
TRIAL_DAYS=1; DB_PATH=os.environ.get('CLOUD_DB',os.path.join(os.path.dirname(os.path.abspath(__file__)),'cloud.db'))
AUTH_REVISION='email-code-v2'; PROTOCOL_VERSION=3; VERSION='0.2.12'
EMAIL_RE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@qq\.com$',re.I)
ADMIN_TOKEN_TTL=28800.0

def norm(e): return (e or '').strip().lower()
def valid_email(e): return bool(EMAIL_RE.fullmatch(norm(e)))
def pw_hash(p,s):
 dk=hashlib.scrypt(p.encode('utf-8'),salt=s.encode('utf-8'),n=16384,r=8,p=1,dklen=32)
 return 'scrypt$16384$8$1$'+dk.hex()
def pw_verify(p,s,stored):
 stored=stored or ''
 if stored.startswith('scrypt$'):
  try:
   _,n,r,pp,h=stored.split('$',4)
   dk=hashlib.scrypt(p.encode('utf-8'),salt=s.encode('utf-8'),n=int(n),r=int(r),p=int(pp),dklen=32)
   return hmac.compare_digest(dk.hex(),h)
  except Exception: return False
 return bool(stored) and hmac.compare_digest(hashlib.sha256((s+p).encode()).hexdigest(),stored)
def pw_needs_upgrade(stored): return bool(stored) and not stored.startswith('scrypt$')
def code_hash(c): return hashlib.sha256((os.environ.get('FRAMEWEAVE_VERIFY_SECRET','')+c).encode()).hexdigest()

def db():
 c=sqlite3.connect(DB_PATH,timeout=10); c.row_factory=sqlite3.Row
 c.execute('''CREATE TABLE IF NOT EXISTS sessions(email TEXT PRIMARY KEY,password_hash TEXT NOT NULL DEFAULT '',salt TEXT NOT NULL DEFAULT '',user_id TEXT NOT NULL,token TEXT NOT NULL DEFAULT '',plan TEXT NOT NULL DEFAULT 'trial',expires_at REAL NOT NULL DEFAULT 0,device_id TEXT NOT NULL DEFAULT '',credits INTEGER NOT NULL DEFAULT 0,cards_used TEXT NOT NULL DEFAULT '[]',last_verified REAL NOT NULL DEFAULT 0,created_at REAL NOT NULL DEFAULT 0)''')
 c.execute('''CREATE TABLE IF NOT EXISTS email_codes(email TEXT NOT NULL,scene TEXT NOT NULL,code_hash TEXT NOT NULL,expires_at REAL NOT NULL,sent_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(email,scene))''')
 c.execute('''CREATE TABLE IF NOT EXISTS mail_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,scene TEXT NOT NULL,ip TEXT NOT NULL DEFAULT '',device_id TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL)''')
 c.execute('''CREATE TABLE IF NOT EXISTS send_log(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,scene TEXT NOT NULL,sent_at REAL NOT NULL,ip TEXT NOT NULL DEFAULT '',device_id TEXT NOT NULL DEFAULT '')''')
 c.execute('''CREATE INDEX IF NOT EXISTS idx_send_log_email ON send_log(email,sent_at)''')
 c.execute('''CREATE TABLE IF NOT EXISTS rate_limits(scope TEXT NOT NULL,key TEXT NOT NULL,window_start INTEGER NOT NULL,count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(scope,key,window_start))''')
 c.execute('''CREATE TABLE IF NOT EXISTS admin_tokens(token TEXT PRIMARY KEY,created_at REAL NOT NULL,expires_at REAL NOT NULL)''')
 c.execute('''CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY AUTOINCREMENT,card TEXT NOT NULL UNIQUE,plan TEXT NOT NULL,days INTEGER NOT NULL,created_at REAL NOT NULL,used_by TEXT NOT NULL DEFAULT '',used_at REAL NOT NULL DEFAULT 0,revoked INTEGER NOT NULL DEFAULT 0)''')
 try: c.execute('ALTER TABLE sessions ADD COLUMN status INTEGER NOT NULL DEFAULT 1')
 except Exception: pass
 try: c.execute('''CREATE INDEX IF NOT EXISTS idx_audit_created ON mail_audit(created_at)''')
 except Exception: pass
 now=int(time.time())
 c.execute('DELETE FROM rate_limits WHERE window_start < ?',(now-2*86400,))
 c.execute('DELETE FROM admin_tokens WHERE expires_at < ?',(now,))
 c.commit()
 return c

def rate_hit(scope,key,limit,window):
 now=time.time(); ws=int(now//window)*window
 c=db()
 row=c.execute('SELECT count FROM rate_limits WHERE scope=? AND key=? AND window_start=?',(scope,key,ws)).fetchone()
 if row is None:
  c.execute('INSERT OR REPLACE INTO rate_limits(scope,key,window_start,count) VALUES(?,?,?,?)',(scope,key,ws,1))
  c.commit(); c.close(); return True
 cur=int(row['count'] or 0)
 if cur>=limit:
  c.close(); return False
 c.execute('UPDATE rate_limits SET count=? WHERE scope=? AND key=? AND window_start=?',(cur+1,scope,key,ws))
 c.commit(); c.close(); return True

def rate_reset(scope,key):
 c=db(); c.execute('DELETE FROM rate_limits WHERE scope=? AND key=?',(scope,key)); c.commit(); c.close()

def find(e):
 c=db()
 try:
  r=c.execute('SELECT * FROM sessions WHERE email=?',(norm(e),)).fetchone(); return dict(r) if r else None
 finally:c.close()
def save(s):
 c=db();c.execute('''INSERT INTO sessions(email,password_hash,salt,user_id,token,plan,expires_at,device_id,credits,cards_used,last_verified,created_at,status) VALUES (:email,:password_hash,:salt,:user_id,:token,:plan,:expires_at,:device_id,:credits,:cards_used,:last_verified,:created_at,:status) ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash,salt=excluded.salt,user_id=excluded.user_id,token=excluded.token,plan=excluded.plan,expires_at=excluded.expires_at,device_id=excluded.device_id,credits=excluded.credits,cards_used=excluded.cards_used,last_verified=excluded.last_verified,status=excluded.status''',s);c.commit();c.close()
def new(e,d,p=''):
 n=time.time();s=uuid.uuid4().hex[:8];return {'email':norm(e),'password_hash':pw_hash(p,s) if p else '','salt':s,'user_id':uuid.uuid4().hex[:12],'token':uuid.uuid4().hex[:32],'plan':'trial','expires_at':n+TRIAL_DAYS*86400,'device_id':d,'credits':0,'cards_used':'[]','last_verified':n,'created_at':n,'status':1}
def public(s):return {'user_id':s['user_id'],'email':s['email'],'token':s['token'],'plan':s.get('plan','trial'),'expires_at':s.get('expires_at'),'credits':s.get('credits',0),'device_id':s.get('device_id',''),'status':s.get('status',1)}

def audit(email,scene,ip,device,result,reason=''):
 c=db(); c.execute('INSERT INTO mail_audit(email,scene,ip,device_id,result,reason,created_at) VALUES(?,?,?,?,?,?,?)',(norm(email),scene,ip,device,result,reason,time.time())); c.commit(); c.close()

def send_mail(to,scene,code):
 from alibabacloud_dm20151123.client import Client
 from alibabacloud_dm20151123.models import SingleSendMailRequest
 from alibabacloud_tea_openapi.models import Config
 c=Client(Config(access_key_id=os.environ['ALIYUN_DM_ACCESS_KEY_ID'],access_key_secret=os.environ['ALIYUN_DM_ACCESS_KEY_SECRET'],region_id=os.environ.get('ALIYUN_DM_REGION_ID','cn-hangzhou'),endpoint=os.environ.get('ALIYUN_DM_ENDPOINT','dm.aliyuncs.com')))
 sub='【拾帧 FrameWeave】'+('注册验证码' if scene=='register' else '密码重置验证码')
 body=f'你好：<br><br>你的验证码是：<b>{code}</b><br><br>验证码 5 分钟内有效。如非本人操作，请忽略此邮件。<br><br>拾帧 FrameWeave'
 c.single_send_mail(SingleSendMailRequest(account_name=os.environ['ALIYUN_DM_ACCOUNT_NAME'],address_type=1,to_address=to,subject=sub,html_body=body,reply_to_address=False))

def issue_code(e,scene,ip='',device=''):
 e=norm(e); now=time.time()
 c=db(); old=c.execute('SELECT * FROM email_codes WHERE email=? AND scene=?',(e,scene)).fetchone(); c.close()
 if old and now-old['sent_at']<60: return False,'验证码发送过于频繁，请稍后再试'
 c=db(); n=c.execute('SELECT COUNT(*) AS n FROM send_log WHERE email=? AND sent_at>?',(e,now-86400)).fetchone()['n']; c.close()
 if n>=10: return False,'今日验证码发送次数已达上限'
 if not rate_hit('send:ip',ip or '?',20,3600): return False,'当前 IP 发送过于频繁，请稍后再试'
 if device and not rate_hit('send:device',device,10,3600): return False,'当前设备发送过于频繁，请稍后再试'
 if not rate_hit('send:global','global',30,60): return False,'系统繁忙，请稍后再试'
 code=f'{random.SystemRandom().randrange(100000,1000000):06d}'
 try:
  if os.environ.get('ALIYUN_DM_ENABLED','false').lower()=='true': send_mail(e,scene,code); audit(e,scene,ip,device,'sent')
  else:
   print('[dev-mail]',e,scene,code); audit(e,scene,ip,device,'sent','dev-mode')
  c=db(); c.execute('INSERT OR REPLACE INTO email_codes(email,scene,code_hash,expires_at,sent_at,attempts) VALUES(?,?,?,?,?,0)',(e,scene,code_hash(code),now+300,now)); c.execute('INSERT INTO send_log(email,scene,sent_at,ip,device_id) VALUES(?,?,?,?,?)',(e,scene,now,ip,device)); c.commit(); c.close()
  return True,'验证码已发送到 QQ 邮箱'
 except Exception as exc:
  print('[mail-error]',type(exc).__name__); audit(e,scene,ip,device,'failed',type(exc).__name__)
  return False,'邮件发送失败，请稍后重试'

def verify_code(e,scene,code):
 e=norm(e)
 c=db(); x=c.execute('SELECT * FROM email_codes WHERE email=? AND scene=?',(e,scene)).fetchone()
 if not x: c.close(); return False,'验证码已过期，请重新获取'
 if time.time()>x['expires_at']:
  c.execute('DELETE FROM email_codes WHERE email=? AND scene=?',(e,scene)); c.commit(); c.close(); return False,'验证码已过期，请重新获取'
 c.execute('UPDATE email_codes SET attempts=attempts+1 WHERE email=? AND scene=?',(e,scene))
 if x['attempts']+1>5:
  c.execute('DELETE FROM email_codes WHERE email=? AND scene=?',(e,scene)); c.commit(); c.close(); return False,'验证码错误次数过多，请重新获取'
 if not code or code_hash(code.strip())!=x['code_hash']:
  c.commit(); c.close(); return False,'验证码不正确'
 c.execute('DELETE FROM email_codes WHERE email=? AND scene=?',(e,scene)); c.commit(); c.close()
 return True,''

def card(card):
 try:
  _,b,s=card.strip().split('-',2);calc=hmac.new(CARD_SECRET.encode(),b.encode(),hashlib.sha256).hexdigest()[:12]
  if not hmac.compare_digest(calc,s):return None
  return json.loads(base64.urlsafe_b64decode(b+'='*(-len(b)%4)).decode())
 except Exception:return None

def login(b):
 e=norm(b.get('email'));p=b.get('password','');d=b.get('device_id','');ip=b.get('ip','')
 if not valid_email(e):return {'ok':False,'message':'目前仅支持 QQ 邮箱（@qq.com）'}
 if not rate_hit('login:ip',ip or '?',20,300): return {'ok':False,'message':'尝试过于频繁，请稍后再试'}
 if not rate_hit('login:email',e,5,600): return {'ok':False,'message':'失败次数过多，请 10 分钟后再试'}
 s=find(e)
 if not s:return {'ok':False,'message':'该邮箱尚未注册，请先注册'}
 if int(s.get('status',1))==0:return {'ok':False,'message':'账号已被禁用，请联系管理员'}
 if not pw_verify(p,s.get('salt',''),s.get('password_hash','')):
  return {'ok':False,'message':'密码不正确'}
 if pw_needs_upgrade(s.get('password_hash','')):
  s['salt']=uuid.uuid4().hex[:16];s['password_hash']=pw_hash(p,s['salt']);save(s)
 rate_reset('login:email',e)
 kick=None
 if d and s.get('device_id') and s['device_id']!=d:kick={'message':'账号已在其他设备登录，旧设备已下线'}
 if d:s['device_id']=d
 s['last_verified']=time.time();save(s);r={'ok':True,'session':public(s),'plan':s['plan']}
 if kick:r['device_kick']=kick
 return r

def register(b):
 e=norm(b.get('email'));p=b.get('password','')
 if not valid_email(e):return {'ok':False,'message':'目前仅支持 QQ 邮箱（@qq.com）'}
 if len(p)<8:return {'ok':False,'message':'密码至少 8 位'}
 ok,msg=verify_code(e,'register',b.get('code',''))
 if not ok:return {'ok':False,'message':msg}
 if find(e):return {'ok':False,'message':'该邮箱已注册，请直接登录'}
 s=new(e,b.get('device_id',''),p);save(s);return {'ok':True,'session':public(s),'plan':s['plan']}

def reset(b):
 e=norm(b.get('email'));p=b.get('new_password','');ok,msg=verify_code(e,'reset_password',b.get('code',''))
 if not ok:return {'ok':False,'message':msg}
 if len(p)<8:return {'ok':False,'message':'密码至少 8 位'}
 s=find(e)
 if s:s['salt']=uuid.uuid4().hex[:8];s['password_hash']=pw_hash(p,s['salt']);s['token']=uuid.uuid4().hex[:32];save(s)
 return {'ok':True,'message':'密码重置成功'}

def verify(b):
 c=db();r=c.execute('SELECT * FROM sessions WHERE token=?',(b.get('token',''),)).fetchone();c.close()
 if not r:return {'ok':False,'reason':'token 无效'}
 s=dict(r)
 if int(s.get('status',1))==0:return {'ok':False,'reason':'账号已被禁用'}
 if time.time()>float(s.get('expires_at') or 0):return {'ok':False,'reason':'订阅已过期，请续费'}
 if b.get('device_id') and s.get('device_id')!=b['device_id']:return {'ok':False,'reason':'账号已在其他设备登录','device_kick':True}
 return {'ok':True,**public(s)}
def activate(b):
 s=find(b.get('email'));p=card(b.get('card',''))
 if not p:return {'ok':False,'message':'卡密无效或签名校验失败'}
 if not s:return {'ok':False,'message':'账号不存在'}
 if int(s.get('status',1))==0:return {'ok':False,'message':'账号已被禁用'}
 used=json.loads(s.get('cards_used') or '[]');serial=p.get('serial','')
 if serial in used:return {'ok':False,'message':'该卡密已被使用'}
 now=time.time();s['expires_at']=max(now,float(s.get('expires_at') or now))+int(p.get('days') or 30)*86400;s['plan']='member';s['cards_used']=json.dumps(used+[serial]);save(s)
 c=db(); c.execute('UPDATE cards SET used_by=?,used_at=? WHERE card=?',(norm(s['email']),now,b.get('card','').strip())); c.commit(); c.close()
 return {'ok':True,'message':'激活成功','plan':'member','expires_at':s['expires_at']}

# ---------- admin ----------
def admin_auth(b):
 if ADMIN_ALLOW_IPS and str(b.get('ip','')).split(':')[0] not in ADMIN_ALLOW_IPS: return 'ip'
 t=str(b.get('token','')); c=db(); r=c.execute('SELECT * FROM admin_tokens WHERE token=?',(t,)).fetchone(); c.close()
 if not r or time.time()>float(r['expires_at']): return None
 return dict(r)
def admin_login(b):
 ip=b.get('ip','?') or '?'
 if ADMIN_ALLOW_IPS and ip.split(':')[0] not in ADMIN_ALLOW_IPS: return {'ok':False,'message':'IP 不在允许列表'}
 if not rate_hit('adminlogin',ip,5,900): return {'ok':False,'message':'尝试过于频繁，请 15 分钟后再试'}
 if not hmac.compare_digest(str(b.get('admin_key','')),ADMIN_KEY): return {'ok':False,'message':'管理密钥错误'}
 rate_reset('adminlogin',ip)
 t=uuid.uuid4().hex[:32]; now=time.time(); c=db()
 c.execute('INSERT INTO admin_tokens(token,created_at,expires_at) VALUES(?,?,?)',(t,now,now+ADMIN_TOKEN_TTL)); c.commit(); c.close()
 return {'ok':True,'token':t,'expires_at':now+ADMIN_TOKEN_TTL}
def admin_logout(b):
 if ADMIN_ALLOW_IPS and str(b.get('ip','')).split(':')[0] not in ADMIN_ALLOW_IPS: return {'ok':False,'message':'IP 不在允许列表'}
 t=str(b.get('token','')); c=db(); c.execute('DELETE FROM admin_tokens WHERE token=?',(t,)); c.commit(); c.close()
 return {'ok':True}
def admin_users(b):
 if not admin_auth(b): return {'ok':False,'message':'未授权或登录已过期'}
 c=db(); rows=c.execute('SELECT email,user_id,plan,expires_at,credits,device_id,status,created_at,last_verified,password_hash FROM sessions ORDER BY created_at DESC LIMIT 500').fetchall(); c.close()
 now=time.time(); out=[]
 for r in rows:
  d=dict(r); d['hash_type']='scrypt' if str(d.get('password_hash') or '').startswith('scrypt$') else ('sha256' if d.get('password_hash') else 'none'); d.pop('password_hash',None)
  d['expiring_soon']=bool(d.get('expires_at') and d.get('plan')=='member' and float(d['expires_at'])>now and float(d['expires_at'])-now<7*86400)
  out.append(d)
 return {'ok':True,'users':out}
def admin_toggle(b):
 if not admin_auth(b): return {'ok':False,'message':'未授权或登录已过期'}
 e=norm(b.get('email','')); s=find(e)
 if not s: return {'ok':False,'message':'用户不存在'}
 ns=0 if int(s.get('status',1))==1 else 1
 c=db(); c.execute('UPDATE sessions SET status=? WHERE email=?',(ns,e)); c.commit(); c.close()
 return {'ok':True,'email':e,'status':ns}
def admin_audit(b):
 if not admin_auth(b): return {'ok':False,'message':'未授权或登录已过期'}
 c=db(); rows=c.execute('SELECT id,email,scene,ip,device_id,result,reason,created_at FROM mail_audit ORDER BY id DESC LIMIT 300').fetchall(); c.close()
 return {'ok':True,'logs':[dict(r) for r in rows]}
def admin_issue(b):
 if not admin_auth(b): return {'ok':False,'message':'未授权或登录已过期'}
 plan=b.get('plan','month') if b.get('plan') in ('month','quarter','year') else 'month'
 days=int(b.get('days',30)); count=min(int(b.get('count',1)),50); cards=[]
 c=db(); now=time.time()
 for _ in range(count):
  p={'plan':plan,'days':days,'serial':uuid.uuid4().hex[:10]}; x=base64.urlsafe_b64encode(json.dumps(p,separators=(',',':')).encode()).rstrip(b'=').decode()
  card=f'FW-{x}-{hmac.new(CARD_SECRET.encode(),x.encode(),hashlib.sha256).hexdigest()[:12]}'
  cards.append(card); c.execute('INSERT INTO cards(card,plan,days,created_at) VALUES(?,?,?,?)',(card,plan,days,now))
 c.commit(); c.close()
 return {'ok':True,'cards':cards}
def admin_cards(b):
 if not admin_auth(b): return {'ok':False,'message':'未授权或登录已过期'}
 c=db(); rows=c.execute('SELECT id,card,plan,days,created_at,used_by,used_at,revoked FROM cards ORDER BY id DESC LIMIT 200').fetchall(); c.close()
 return {'ok':True,'cards':[dict(r) for r in rows]}
def admin_card_revoke(b):
 if not admin_auth(b): return {'ok':False,'message':'未授权或登录已过期'}
 cid=int(b.get('id',0) or 0); c=db()
 r=c.execute('SELECT card FROM cards WHERE id=?',(cid,)).fetchone()
 if not r: c.close(); return {'ok':False,'message':'卡密不存在'}
 c.execute('UPDATE cards SET revoked=1 WHERE id=?',(cid,)); c.commit(); c.close()
 return {'ok':True,'id':cid}

ROUTES={'/api/auth/email/send-code':lambda b: issue_code(b.get('email',''),b.get('scene','register'),b.get('ip',''),b.get('device_id','')) if valid_email(b.get('email','')) else (False,'目前仅支持 QQ 邮箱（@qq.com）'),'/api/auth/register':register,'/api/auth/login':login,'/api/auth/password/reset':reset,'/api/auth/verify':verify,'/api/auth/activate':activate,'/api/admin/login':admin_login,'/api/admin/logout':admin_logout,'/api/admin/users':admin_users,'/api/admin/user/toggle':admin_toggle,'/api/admin/cards':admin_cards,'/api/admin/card/revoke':admin_card_revoke,'/api/admin/audit':admin_audit,'/api/admin/issue-card':admin_issue}
ADMIN_HTML=r'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>拾帧 FrameWeave 授权后台</title>
<style>
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#f2f4f8;color:#1c2333}
header{background:#101828;color:#fff;padding:14px 22px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:16px;margin:0;font-weight:600}
main{max-width:1100px;margin:22px auto;padding:0 16px}
.card{background:#fff;border:1px solid #e4e8ef;border-radius:10px;padding:16px 18px;margin-bottom:18px}
.card h2{font-size:14px;margin:0 0 12px;color:#101828}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #eef1f6;white-space:nowrap}
th{color:#667085;font-weight:600;background:#fafbfc}
input,select,button{font-size:13px;padding:6px 10px;border:1px solid #d0d5dd;border-radius:6px;background:#fff}
button{background:#101828;color:#fff;border:none;cursor:pointer}
button.sec{background:#fff;color:#101828;border:1px solid #d0d5dd}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px}
.badge.ok{background:#ecfdf3;color:#027a48}.badge.off{background:#fef3f2;color:#b42318}
.badge.warn{background:#fff3cd;color:#856404}
.muted{color:#667085;font-size:12px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#loginBox{max-width:420px;margin:80px auto}
#cards{white-space:pre-wrap;font-family:ui-monospace,Consolas,monospace;font-size:12px;background:#f8f9fc;border:1px solid #e4e8ef;border-radius:6px;padding:10px}
.toast{position:fixed;right:16px;bottom:16px;background:#101828;color:#fff;padding:10px 16px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .2s}
.toast.show{opacity:1}
</style></head><body>
<div id="app"></div>
<script>
const API='/api/admin';
let TOKEN=localStorage.getItem('fw_admin_token')||'';
function toast(m){const t=document.querySelector('.toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200)}
async function call(path,body){const r=await fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:TOKEN,...body})});return r.json()}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}
function fmt(ts){if(!ts)return '-';const d=new Date(ts*1000);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')}
async function login(){const key=document.getElementById('key').value.trim();const r=await call('/login',{admin_key:key});if(r.ok){TOKEN=r.token;localStorage.setItem('fw_admin_token',TOKEN);render()}else{toast(r.message||'登录失败')}}
function logout(){TOKEN='';localStorage.removeItem('fw_admin_token');render()}
async function render(){
 if(!TOKEN){document.getElementById('app').innerHTML='<div id="loginBox" class="card"><h2>拾帧 FrameWeave 授权后台</h2><div class="muted" style="margin-bottom:12px">请输入管理密钥登录</div><div class="row"><input id="key" type="password" style="flex:1" placeholder="管理密钥"><button onclick="login()">登录</button></div></div>';return}
 const u=await call('/users'); if(!u.ok){logout();return}
 const a=await call('/audit');
 const c=await call('/cards');
 const expiring=u.users.filter(x=>x.expiring_soon);
 const usr=u.users.map(x=>'<tr><td>'+esc(x.email)+(x.expiring_soon?'<br><span class="badge warn">即将到期</span>':'')+'</td><td>'+esc(x.plan)+'</td><td>'+fmt(x.expires_at)+'</td><td>'+(x.hash_type=='scrypt'?'<span class="badge ok">scrypt</span>':'<span class="badge off">sha256</span>')+'</td><td>'+esc(x.credits)+'</td><td>'+(x.status==1?'<span class="badge ok">启用</span>':'<span class="badge off">禁用</span>')+'</td><td><button onclick="toggleUser(\''+esc(x.email)+'\')">'+(x.status==1?'禁用':'启用')+'</button></td></tr>').join('');
 const logs=(a.logs||[]).map(l=>'<tr><td>'+fmt(l.created_at)+'</td><td>'+esc(l.email)+'</td><td>'+esc(l.scene)+'</td><td>'+esc(l.ip)+'</td><td>'+(l.result=='sent'?'<span class="badge ok">成功</span>':'<span class="badge off">失败</span>')+'</td><td>'+esc(l.reason)+'</td></tr>').join('');
 const cards=(c&&c.cards||[]).map(x=>'<tr><td>'+x.id+'</td><td style="font-family:monospace;font-size:11px">'+esc(x.card)+'</td><td>'+esc(x.plan)+'</td><td>'+x.days+'</td><td>'+fmt(x.created_at)+'</td><td>'+esc(x.used_by||'-')+'</td><td>'+(x.revoked==1?'<span class="badge off">已作废</span>':(x.used_by?'<span class="badge ok">已使用</span>':'<span class="badge">未使用</span>'))+'</td><td>'+(x.revoked==1?'-':'<button onclick="revokeCard('+x.id+')">作废</button>')+'</td></tr>').join('');
 document.getElementById('app').innerHTML='<header><h1>拾帧 FrameWeave 授权后台</h1><button class="sec" onclick="logout()">退出登录</button></header><main>'
 +(expiring.length?'<div class="card" style="background:#fff8e6"><h2 style="color:#856404">⚠ 即将到期（'+expiring.length+' 个会员 7 天内到期）</h2>'+expiring.map(x=>'<span style="margin-right:12px;font-size:13px">'+esc(x.email)+' → '+fmt(x.expires_at)+'</span>').join('')+'</div>':'')
 +'<div class="card"><h2>签发卡密</h2><div class="row"><select id="plan"><option value="month">月卡</option><option value="quarter">季卡</option><option value="year">年卡</option></select>'
 +'<input id="days" type="number" value="30" min="1" style="width:80px">天 <input id="count" type="number" value="1" min="1" max="50" style="width:70px">张 <button onclick="issue()">签发</button></div><div id="cards" style="display:none;margin-top:10px"></div></div>'
 +'<div class="card"><h2>用户管理（'+u.users.length+'）</h2><div style="overflow-x:auto"><table><tr><th>邮箱</th><th>套餐</th><th>到期时间</th><th>哈希</th><th>积分</th><th>状态</th><th>操作</th></tr>'+usr+'</table></div></div>'
 +'<div class="card"><h2>卡密管理（'+(c&&c.cards||[]).length+'）</h2><div style="overflow-x:auto"><table><tr><th>ID</th><th>卡密</th><th>套餐</th><th>天数</th><th>签发时间</th><th>使用者</th><th>状态</th><th>操作</th></tr>'+cards+'</table></div></div>'
 +'<div class="card"><h2>邮件发送审计</h2><div style="overflow-x:auto"><table><tr><th>时间</th><th>邮箱</th><th>场景</th><th>IP</th><th>结果</th><th>原因</th></tr>'+logs+'</table></div></div>'
 +'</main>';
}
async function toggleUser(email){const r=await call('/user/toggle',{email});if(r.ok){toast(r.status==1?'已启用':'已禁用');render()}else{toast(r.message||'操作失败')}}
async function revokeCard(id){if(!confirm('确认作废该卡密？作废后不可恢复'))return;const r=await call('/card/revoke',{id});if(r.ok){toast('已作废');render()}else{toast(r.message||'操作失败')}}
async function issue(){const plan=document.getElementById('plan').value;const days=parseInt(document.getElementById('days').value)||30;const count=parseInt(document.getElementById('count').value)||1;const r=await call('/issue-card',{plan,days,count});if(r.ok){const el=document.getElementById('cards');el.style.display='block';el.textContent=r.cards.join('\n');toast('已签发 '+count+' 张')}else{toast(r.message||'签发失败')}}
render();
</script><div class="toast"></div>
</body></html>'''

class H(BaseHTTPRequestHandler):
 def out(self,o,st=200):
  x=json.dumps(o,ensure_ascii=False).encode();self.send_response(st);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(x)));self.end_headers();self.wfile.write(x)
 def out_html(self):
  x=ADMIN_HTML.encode('utf-8');self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(x)));self.end_headers();self.wfile.write(x)
 def do_GET(self):
  path=urlparse(self.path).path
  if path=='/api/health':self.out({'ok':True,'service':'frameweave-cloud','version':VERSION,'protocol_version':PROTOCOL_VERSION,'auth_revision':AUTH_REVISION})
  elif path=='/admin' or path=='/admin/':self.out_html()
  else:self.out({'ok':False,'message':'Not Found'},404)
 def do_POST(self):
  f=ROUTES.get(urlparse(self.path).path)
  if not f:return self.out({'ok':False,'message':'Not Found'},404)
  try:n=int(self.headers.get('Content-Length','0'));b=json.loads(self.rfile.read(n) or b'{}');b['ip']=self.client_address[0];r=f(b);self.out({'ok':r[0],'message':r[1]} if isinstance(r,tuple) else r)
  except Exception as e:print('[cloud-error]',type(e).__name__);self.out({'ok':False,'message':'服务器内部错误'},500)
 def log_message(self,*a):pass
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=int(os.environ.get('CLOUD_PORT','8789')));ap.add_argument('--bind',default='127.0.0.1');a=ap.parse_args();db().close();print('[cloud] start',a.bind,a.port,DB_PATH,'v'+VERSION);ThreadingHTTPServer((a.bind,a.port),H).serve_forever()
