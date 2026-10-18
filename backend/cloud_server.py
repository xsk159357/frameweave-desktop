#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse,base64,hashlib,hmac,json,os,random,re,sqlite3,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse

CARD_SECRET=os.environ.get('FRAMEWEAVE_CARD_SECRET',''); ADMIN_KEY=os.environ.get('FRAMEWEAVE_ADMIN_KEY','')
VERIFY_SECRET=os.environ.get('FRAMEWEAVE_VERIFY_SECRET','fw-verify-secret-2026')  # P3/t3 ④：auth 响应 HMAC 签名密钥
ADMIN_ALLOW_IPS=[x.strip() for x in os.environ.get('FRAMEWEAVE_ADMIN_ALLOW_IPS','').split(',') if x.strip()]
TRIAL_DAYS=1
# 2026-10-19 源码树卫生：仓库开发树存在 backend/data/（已 gitignore）时 DB 落 data/，
# 避免从仓库内启动把 cloud.db 写回源码树（曾使 test_health_governance 门禁红）。
# 生产部署目录无 data/ 兄弟目录 → 行为不变（仍在脚本同目录）；CLOUD_DB 优先级最高。
_BASE_DIR=os.path.dirname(os.path.abspath(__file__))
_DEV_DATA=os.path.join(_BASE_DIR,'data')
DB_PATH=os.environ.get('CLOUD_DB', os.path.join(_DEV_DATA,'cloud.db') if os.path.isdir(_DEV_DATA) else os.path.join(_BASE_DIR,'cloud.db'))
AUTH_REVISION='email-code-v2'; PROTOCOL_VERSION=3; VERSION='0.3.12'; AUTHOR_SHARE=0.8
MARKET_FILE=os.path.join(os.path.dirname(os.path.abspath(DB_PATH)),'market_items.json')
_MARKET_LOCK=threading.Lock()  # t1：商城 JSON 读写互斥，防并发下载/更新丢计数
EMAIL_RE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@qq\.com$',re.I)
ADMIN_TOKEN_TTL=28800.0
MAX_BODY=1048576  # t3：单请求体上限（1MB，防内存耗尽 DoS；所有既有接口载荷远小于此）
TRUST_XFF=os.environ.get('FRAMEWEAVE_TRUST_XFF','0').lower() in ('1','true','yes')  # t3：反代 X-Forwarded-For 可信开关（默认关，保持原行为）

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
 c.execute('''CREATE TABLE IF NOT EXISTS market_downloads(id INTEGER PRIMARY KEY AUTOINCREMENT,item_id TEXT NOT NULL,email TEXT NOT NULL DEFAULT '',kind TEXT NOT NULL DEFAULT '',price INTEGER NOT NULL DEFAULT 0,paid INTEGER NOT NULL DEFAULT 0,client_version TEXT NOT NULL DEFAULT '',device_id TEXT NOT NULL DEFAULT '',ip TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL)''')
 c.execute('''CREATE TABLE IF NOT EXISTS market_installs(id INTEGER PRIMARY KEY AUTOINCREMENT,item_id TEXT NOT NULL,email TEXT NOT NULL DEFAULT '',client_version TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL)''')
 c.execute('''CREATE TABLE IF NOT EXISTS admin_settings(k TEXT PRIMARY KEY,v TEXT NOT NULL DEFAULT '')''')
 c.execute('''CREATE TABLE IF NOT EXISTS devices(email TEXT NOT NULL,device_id TEXT NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL DEFAULT 0,kicked INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(email,device_id))''')
 try: c.execute('ALTER TABLE sessions ADD COLUMN status INTEGER NOT NULL DEFAULT 1')
 except Exception: pass
 try: c.execute('ALTER TABLE sessions ADD COLUMN created_ip TEXT NOT NULL DEFAULT \'\'')
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
 c=db();c.execute('''INSERT INTO sessions(email,password_hash,salt,user_id,token,plan,expires_at,device_id,credits,cards_used,last_verified,created_at,status,created_ip) VALUES (:email,:password_hash,:salt,:user_id,:token,:plan,:expires_at,:device_id,:credits,:cards_used,:last_verified,:created_at,:status,:created_ip) ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash,salt=excluded.salt,user_id=excluded.user_id,token=excluded.token,plan=excluded.plan,expires_at=excluded.expires_at,device_id=excluded.device_id,credits=excluded.credits,cards_used=excluded.cards_used,last_verified=excluded.last_verified,status=excluded.status,created_ip=excluded.created_ip''',s);c.commit();c.close()
def new(e,d,p='',ip=''):
 n=time.time();s=uuid.uuid4().hex[:8];return {'email':norm(e),'password_hash':pw_hash(p,s) if p else '','salt':s,'user_id':uuid.uuid4().hex[:12],'token':uuid.uuid4().hex[:32],'plan':'trial','expires_at':n+TRIAL_DAYS*86400,'device_id':d,'credits':0,'cards_used':'[]','last_verified':n,'created_at':n,'status':1,'created_ip':ip}
def track_device(email,device_id):
 if not device_id: return
 e=norm(email); n=time.time(); c=db()
 c.execute('INSERT INTO devices(email,device_id,first_seen,last_seen,kicked) VALUES(?,?,?,?,0) ON CONFLICT(email,device_id) DO UPDATE SET last_seen=excluded.last_seen',(e,str(device_id),n,n))
 c.commit(); c.close()
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
   # t3：验证码日志脱敏——默认只显后两位（防 dev 模式误入生产 journal 泄露）；
   # 本地 E2E 需要完整码时显式设置 FRAMEWEAVE_DEV_MAIL_VERBOSE=1。
   if os.environ.get('FRAMEWEAVE_DEV_MAIL_VERBOSE','0').lower() in ('1','true','yes'): print('[dev-mail]',e,scene,code)
   else: print('[dev-mail]',e,scene,'****'+code[-2:])
   audit(e,scene,ip,device,'sent','dev-mode')
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

# ---------- P3/t3 ④：auth 响应 HMAC 签名（防 fiddler 伪造 member 状态） ----------
# 规范化：排除 signature/sig/nonce 字段，键升序拼接 k=v；HMAC-SHA256 截 48 hex。
# 客户端 cloud_gateway.check_response_signature 用同一规则复算比对。
_SKIP_SIGN_FIELDS={'signature','sig'}
def _sign_norm(v):
 if isinstance(v,bool):return 'true' if v else 'false'
 if v is None:return 'null'
 if isinstance(v,(int,float)):return str(v)
 if isinstance(v,str):return v
 return json.dumps(v,separators=(',',':'),ensure_ascii=False,sort_keys=True)
def sign_auth_resp(o):
 """对 /api/auth/* 响应加 signature 字段（就地或拷贝返回）。"""
 if not isinstance(o,dict):return o
 items=[]
 for k,v in o.items():
  if k in _SKIP_SIGN_FIELDS or v is None:continue
  items.append((str(k),_sign_norm(v)))
 items.sort(key=lambda kv:kv[0])
 canon=''.join(f'{k}={v}' for k,v in items).encode('utf-8')
 o['signature']=hmac.new(VERIFY_SECRET.encode('utf-8'),canon,hashlib.sha256).hexdigest()[:48]
 return o

def login(b):
 e=norm(str(b.get('email') or ''));p=str(b.get('password') or '');d=str(b.get('device_id') or '');ip=str(b.get('ip') or '')
 if not valid_email(e):return {'ok':False,'code':'invalid_email','message':'目前仅支持 QQ 邮箱（@qq.com）'}
 if not rate_hit('login:ip',ip or '?',20,300): return {'ok':False,'code':'rate_limited','message':'尝试过于频繁，请稍后再试'}
 if not rate_hit('login:email',e,5,600): return {'ok':False,'code':'rate_limited','message':'失败次数过多，请 10 分钟后再试'}
 s=find(e)
 if not s:return {'ok':False,'code':'not_registered','message':'该邮箱尚未注册，请先注册'}
 if int(s.get('status',1))==0:return {'ok':False,'code':'account_disabled','message':'账号已被禁用，请联系管理员'}
 if not pw_verify(p,s.get('salt',''),s.get('password_hash','')):
  return {'ok':False,'code':'bad_credentials','message':'密码不正确'}
 if pw_needs_upgrade(s.get('password_hash','')):
  s['salt']=uuid.uuid4().hex[:16];s['password_hash']=pw_hash(p,s['salt']);save(s)
 rate_reset('login:email',e)
 kick=None
 if d and s.get('device_id') and s['device_id']!=d:kick={'message':'账号已在其他设备登录，旧设备已下线'}
 if d:s['device_id']=d
 track_device(e,d)
 s['last_verified']=time.time();save(s);r={'ok':True,'code':'ok','session':public(s),'plan':s['plan']}
 if kick:r['device_kick']=kick
 return r

def register(b):
 e=norm(str(b.get('email') or ''));p=str(b.get('password') or '')
 if not valid_email(e):return {'ok':False,'code':'invalid_email','message':'目前仅支持 QQ 邮箱（@qq.com）'}
 if len(p)<8:return {'ok':False,'code':'weak_password','message':'密码至少 8 位'}
 ok,msg=verify_code(e,'register',str(b.get('code') or ''))
 if not ok:return {'ok':False,'code':'bad_code','message':msg}
 if find(e):return {'ok':False,'code':'already_registered','message':'该邮箱已注册，请直接登录'}
 s=new(e,str(b.get('device_id') or ''),p,str(b.get('ip') or ''));save(s);track_device(e,str(b.get('device_id') or ''));return {'ok':True,'code':'ok','session':public(s),'plan':s['plan']}

def reset(b):
 e=norm(str(b.get('email') or ''));p=str(b.get('new_password') or '');ok,msg=verify_code(e,'reset_password',str(b.get('code') or ''))
 if not ok:return {'ok':False,'code':'bad_code','message':msg}
 if len(p)<8:return {'ok':False,'code':'weak_password','message':'密码至少 8 位'}
 s=find(e)
 if s:s['salt']=uuid.uuid4().hex[:8];s['password_hash']=pw_hash(p,s['salt']);s['token']=uuid.uuid4().hex[:32];save(s)
 return {'ok':True,'code':'ok','message':'密码重置成功'}

def verify(b):
 tok=str(b.get('token') or '')
 c=db();r=c.execute('SELECT * FROM sessions WHERE token=?',(tok,)).fetchone();c.close()
 out={'req_nonce':str(b.get('nonce') or '')}  # P3/t3 ④：回显客户端 nonce 并纳入签名
 if not r:return {'ok':False,'code':'invalid_token','reason':'token 无效',**out}
 s=dict(r)
 if int(s.get('status',1))==0:return {'ok':False,'code':'account_disabled','reason':'账号已被禁用',**out}
 if time.time()>float(s.get('expires_at') or 0):return {'ok':False,'code':'subscription_expired','reason':'订阅已过期，请续费',**out}
 if b.get('device_id') and s.get('device_id')!=str(b['device_id']):return {'ok':False,'code':'device_kick','reason':'账号已在其他设备登录','device_kick':True,**out}
 track_device(s['email'],b.get('device_id',''))
 c=db();c.execute('UPDATE sessions SET last_verified=? WHERE token=?',(time.time(),tok));c.commit();c.close()
 return {'ok':True,'code':'ok',**public(s),**out}
def activate(b):
 s=find(str(b.get('email') or ''));p=card(str(b.get('card') or ''))
 if not p:return {'ok':False,'code':'card_invalid','message':'卡密无效或签名校验失败'}
 if not s:return {'ok':False,'code':'not_registered','message':'账号不存在'}
 if int(s.get('status',1))==0:return {'ok':False,'code':'account_disabled','message':'账号已被禁用'}
 used=json.loads(s.get('cards_used') or '[]');serial=p.get('serial','')
 if serial in used:return {'ok':False,'code':'card_already_used','message':'该卡密已被使用'}
 c=db(); row=c.execute('SELECT revoked FROM cards WHERE card=?',(str(b.get('card') or '').strip(),)).fetchone()
 revoked=bool(row and int(row['revoked'] or 0)) if row else False
 c.close()
 if revoked:return {'ok':False,'code':'card_revoked','message':'该卡密已作废，请联系管理员'}
 now=time.time();s['expires_at']=max(now,float(s.get('expires_at') or now))+int(p.get('days') or 30)*86400;s['plan']='member';s['cards_used']=json.dumps(used+[serial]);save(s)
 c=db(); c.execute('UPDATE cards SET used_by=?,used_at=? WHERE card=?',(norm(s['email']),now,str(b.get('card') or '').strip())); c.commit(); c.close()
 return {'ok':True,'code':'ok','message':'激活成功','plan':'member','expires_at':s['expires_at']}
# ---------- market (M20: 商城下载授权 + 安装回报) ----------
def _ver_tuple(v):
    parts=[]
    for seg in re.split(r'[._-]',str(v or '').strip()):
        if seg.isdigit(): parts.append(int(seg))
        else: break
    return tuple(parts[:7])
def _clean_key(k):
    # 防御：剔除键中的 BOM / 零宽 / 控制字符 / 空白，避免磁盘文件混入不可见字符导致
    # download-auth item_not_found（历史线上事故：键含不可见字符，json 键匹配失败）。
    return ''.join(ch for ch in (k or '') if not (ch.isspace() or ord(ch) < 32))
def _load_market():
    try:
        if os.path.exists(MARKET_FILE):
            with open(MARKET_FILE,'r',encoding='utf-8') as f: data=json.load(f)
            if isinstance(data,dict):
                cleaned={}; changed=False
                for k,v in data.items():
                    ck=_clean_key(k)
                    if ck != k: changed=True
                    if ck: cleaned[ck]=v
                if changed: _save_market(cleaned)
                return cleaned
    except Exception: pass
    return {}
def _save_market(data):
    with open(MARKET_FILE,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)
def _market_item(iid):
    return _load_market().get(_clean_key(iid))
def find_by_token(tok):
    c=db()
    try:
        r=c.execute('SELECT * FROM sessions WHERE token=?',(tok,)).fetchone(); return dict(r) if r else None
    finally: c.close()
def market_list(b=None):
    b = b or {}
    q = str(b.get('q') or '').strip().lower()
    kind = str(b.get('kind') or '').strip().lower()
    official = str(b.get('official') or '').strip().lower()
    data=_load_market()
    items=[]
    for raw in data.values():
        it=dict(raw)
        if kind and str(it.get('kind') or '').lower() != kind:
            continue
        if official in ('1','true','yes') and not bool(it.get('official')):
            continue
        hay=' '.join(str(it.get(k) or '') for k in ('id','title','description','author','type_id','kind','tags')).lower()
        if q and q not in hay:
            continue
        items.append(it)
    items.sort(key=lambda x:(not bool(x.get('official')),-int(x.get('downloads') or 0),-float(x.get('created_at') or 0)))
    return {'ok':True,'items':items,'count':len(items)}
def market_download_auth(b):
    iid=str(b.get('item_id','')).strip(); ck=_clean_key(iid)
    it=_market_item(iid)
    if it is None: return {'ok':False,'code':'item_not_found','message':'条目不存在'}
    if it.get('status','active')!='active': return {'ok':False,'code':'item_disabled','message':'条目已下架'}
    minv=it.get('min_client_version',''); cv=str(b.get('client_version',''))
    if minv and (not cv or _ver_tuple(cv)<_ver_tuple(minv)):
        return {'ok':False,'code':'client_version_too_old','message':'客户端版本过低，需要 '+minv+' 及以上','min_client_version':minv}
    price=int(it.get('price') or 0); paid=price>0; email=''
    c=db()
    if paid:
        s=find_by_token(str(b.get('token','')))
        if not s: c.close(); return {'ok':False,'code':'login_required','message':'请先登录后再下载付费节点'}
        # t1：原子扣积分（防并发双花：UPDATE ... WHERE credits>=price，rowcount 判定成败）
        cur=c.execute('UPDATE sessions SET credits=credits-? WHERE email=? AND credits>=?',(price,s['email'],price))
        if cur.rowcount==0: c.close(); return {'ok':False,'code':'insufficient_credits','message':'积分不足'}
        ae=norm(it.get('author',''))
        if ae:
            c.execute('UPDATE sessions SET credits=credits+? WHERE email=?',(int(price*AUTHOR_SHARE),ae))
        c.commit(); c.close()
        email=s['email']
    else:
        ss=find_by_token(str(b.get('token','')))
        if ss: email=ss['email']
        c.close()
    with _MARKET_LOCK:
        data=_load_market(); d=data.get(ck) if ck in data else data.get(iid)
        if d is None: return {'ok':False,'code':'item_not_found','message':'条目不存在'}
        d['downloads']=int(d.get('downloads') or 0)+1; _save_market(data)
    c=db(); c.execute('INSERT INTO market_downloads(item_id,email,kind,price,paid,client_version,device_id,ip,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(ck,email,it.get('kind',''),price,1 if paid else 0,cv,str(b.get('device_id','')),str(b.get('ip','')),time.time())); c.commit(); c.close()
    fn=(it.get('type_id') or ck).replace('/','_') or 'plugin'
    return {'ok':True,'code':'ok','status':'authorized','item_id':ck,'title':it.get('title',''),'version':it.get('version','1.0.0'),'kind':it.get('kind',''),'type_id':it.get('type_id',''),'price':price,
            'download':{'filename':fn+'.zip','size':int(it.get('size') or 0),'sha256':it.get('sha256','') or '','url':it.get('download_url',''),'direct':True},
            'direct':True,'min_client_version':minv,'client_version_ok':True}
def market_install_report(b):
    iid=str(b.get('item_id','')).strip(); ck=_clean_key(iid)
    it=_market_item(iid)
    if it is None: return {'ok':False,'code':'item_not_found','message':'条目不存在'}
    result=str(b.get('result','ok')); ok=result.lower() in ('ok','success','installed')
    ss=find_by_token(str(b.get('token',''))); email=ss['email'] if ss else ''
    with _MARKET_LOCK:
        data=_load_market()
        if ok and ck in data:
            data[ck]['installs']=int(data[ck].get('installs') or 0)+1; _save_market(data)
    c=db(); c.execute('INSERT INTO market_installs(item_id,email,client_version,result,error,created_at) VALUES(?,?,?,?,?,?)',(ck,email,str(b.get('client_version','')),result or 'ok',str(b.get('error',''))[:500],time.time())); c.commit(); c.close()
    return {'ok':True,'code':'ok','item_id':ck,'recorded':True,'install_count':int(data[ck].get('installs') or 0) if ck in data else 0}
def admin_market_list(b):
    if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
    return market_list(b)
def admin_market_upsert(b):
    if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
    it=b.get('item',{})
    if not isinstance(it,dict) or not (it.get('title') or it.get('id')): return {'ok':False,'code':'invalid_param','message':'item 需含 title/id'}
    with _MARKET_LOCK:
        data=_load_market(); iid=str(it.get('id','')).strip() or ('it_'+uuid.uuid4().hex[:10])
        base=data.get(iid,{})
        for k,v in it.items():
            if k=='id': continue
            base[k]=v
        base['id']=iid
        base.setdefault('kind',it.get('kind','node')); base.setdefault('price',0)
        base.setdefault('version',it.get('version','1.0.0') or '1.0.0')
        base.setdefault('official',False); base.setdefault('status',it.get('status','active') or 'active')
        base.setdefault('downloads',0); base.setdefault('installs',0); base.setdefault('created_at',time.time())
        data[iid]=base; _save_market(data)
    _adm_write(b,'market_upsert','root',b.get('ip',''),'ok',f'item={iid}')
    return {'ok':True,'code':'ok','item':base}

# ---------- admin ----------
def _admin_ip_ok(b):
    if not ADMIN_ALLOW_IPS: return True
    return str(b.get('ip','')).split(':')[0] in ADMIN_ALLOW_IPS
def admin_auth(b):
    # 先 IP 白名单，后令牌校验：两者都通过才返回令牌行（防 IP 白名单旁路）
    if not _admin_ip_ok(b): return None
    t=str(b.get('token','')); c=db(); r=c.execute('SELECT * FROM admin_tokens WHERE token=?',(t,)).fetchone(); c.close()
    if not r or time.time()>float(r['expires_at']): return None
    return dict(r)
def admin_login(b):
    ip=str(b.get('ip','?') or '?')
    if not _admin_ip_ok(b): return {'ok':False,'code':'ip_forbidden','message':'IP 不在允许列表'}
    if not rate_hit('adminlogin',ip,5,900): return {'ok':False,'code':'rate_limited','message':'尝试过于频繁，请 15 分钟后再试'}
    ek=_admin_key_effective()
    # t3：未配置管理密钥时拒绝空串登录（compare_digest('','') 恒真 → 空密钥可绕过）
    if not ek: return {'ok':False,'code':'not_configured','message':'管理密钥未配置'}
    if not hmac.compare_digest(str(b.get('admin_key','')),ek): return {'ok':False,'code':'bad_credentials','message':'管理密钥错误'}
    rate_reset('adminlogin',ip)
    t=uuid.uuid4().hex[:32]; now=time.time(); c=db()
    c.execute('INSERT INTO admin_tokens(token,created_at,expires_at) VALUES(?,?,?)',(t,now,now+ADMIN_TOKEN_TTL)); c.commit(); c.close()
    _adm_write(b,'login','root',ip,'ok','管理后台登录')
    return {'ok':True,'code':'ok','token':t,'expires_at':now+ADMIN_TOKEN_TTL}
def admin_logout(b):
    if not _admin_ip_ok(b): return {'ok':False,'code':'ip_forbidden','message':'IP 不在允许列表'}
    t=str(b.get('token','')); c=db(); c.execute('DELETE FROM admin_tokens WHERE token=?',(t,)); c.commit(); c.close()
    return {'ok':True,'code':'ok'}
def admin_users(b):
 if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 c=db(); rows=c.execute('SELECT email,user_id,plan,expires_at,credits,device_id,status,created_at,last_verified,password_hash FROM sessions ORDER BY created_at DESC LIMIT 500').fetchall(); c.close()
 now=time.time(); out=[]
 for r in rows:
  d=dict(r); d['hash_type']='scrypt' if str(d.get('password_hash') or '').startswith('scrypt$') else ('sha256' if d.get('password_hash') else 'none'); d.pop('password_hash',None)
  d['expiring_soon']=bool(d.get('expires_at') and d.get('plan')=='member' and float(d['expires_at'])>now and float(d['expires_at'])-now<7*86400)
  out.append(d)
 return {'ok':True,'users':out}
def admin_extend_subscription(b):
 if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 e=norm(b.get('email',''))
 try: days=int(b.get('days',0))
 except Exception: days=0
 if not valid_email(e): return {'ok':False,'message':'邮箱格式不正确'}
 if days < 1 or days > 3650: return {'ok':False,'message':'加时时长需为 1-3650 天'}
 s=find(e)
 if not s: return {'ok':False,'message':'用户不存在'}
 now=time.time(); old=float(s.get('expires_at') or 0); base=max(now,old); new_exp=base+days*86400
 s['expires_at']=new_exp; s['plan']='member'; save(s)
 _adm_write(b,'extend',e,b.get('ip',''),'ok',f'days={days},expires_at={new_exp}')
 return {'ok':True,'code':'ok','email':e,'days':days,'expires_at':new_exp,'plan':'member'}
def admin_toggle(b):
 if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 e=norm(b.get('email','')); s=find(e)
 if not s: return {'ok':False,'code':'not_found','message':'用户不存在'}
 ns=0 if int(s.get('status',1))==1 else 1
 c=db(); c.execute('UPDATE sessions SET status=? WHERE email=?',(ns,e)); c.commit(); c.close()
 _adm_write(b,'toggle',e,b.get('ip',''),'ok',f'status={ns}')
 return {'ok':True,'code':'ok','email':e,'status':ns}
def admin_audit(b):
    if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
    q=str(b.get('q') or '').strip().lower()
    try: page=max(1,int(b.get('page') or 1))
    except Exception: page=1
    try: per=max(1,min(500,int(b.get('per_page') or 100)))
    except Exception: per=100
    scene=str(b.get('scene') or '').strip()
    result=str(b.get('result') or '').strip().lower()
    where=[]; args=[]
    if q: where.append('(email LIKE ? OR ip LIKE ?)'); args += [f'%{q}%', f'%{q}%']
    if scene: where.append('scene LIKE ?'); args.append(scene+'%')
    if result: where.append('LOWER(result)=?'); args.append(result)
    wsql=(' WHERE '+ ' AND '.join(where)) if where else ''
    c=db()
    total=c.execute('SELECT COUNT(*) n FROM mail_audit'+wsql,args).fetchone()['n']
    rows=c.execute('SELECT id,email,scene,ip,device_id,result,reason,created_at FROM mail_audit'+wsql+' ORDER BY id DESC LIMIT ? OFFSET ?',args+[per,(page-1)*per]).fetchall(); c.close()
    return {'ok':True,'code':'ok','logs':[dict(r) for r in rows],'total':total,'page':page,'per_page':per,'pages':max(1,(total+per-1)//per)}
def admin_issue(b):
    if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
    plan=str(b.get('plan') or 'month') if str(b.get('plan') or '') in ('month','quarter','year') else 'month'
    try: days=int(b.get('days') or 30)
    except Exception: days=30
    try: count=min(max(int(b.get('count') or 1),1),200)
    except Exception: count=1
    if days<1 or days>3650: return {'ok':False,'code':'invalid_param','message':'签发天数需为 1-3650 天'}
    prefix=str(get_setting('card_prefix','') or '').strip().upper()
    cards=[]; c=db(); now=time.time()
    for _ in range(count):
        p={'plan':plan,'days':days,'serial':uuid.uuid4().hex[:10]}; x=base64.urlsafe_b64encode(json.dumps(p,separators=(',',':')).encode()).rstrip(b'=').decode()
        card=f'{prefix or "FW"}-{x}-{hmac.new(CARD_SECRET.encode(),x.encode(),hashlib.sha256).hexdigest()[:12]}'
        cards.append(card); c.execute('INSERT INTO cards(card,plan,days,created_at) VALUES(?,?,?,?)',(card,plan,days,now))
    c.commit(); c.close()
    _adm_write(b,'issue_cards','root',b.get('ip',''),'ok',f'plan={plan},days={days},count={count}')
    return {'ok':True,'code':'ok','cards':cards,'count':count}
def admin_cards(b):
    if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
    st=str(b.get('status') or '').strip().lower()
    q=str(b.get('q') or '').strip()
    try: page=max(1,int(b.get('page') or 1))
    except Exception: page=1
    try: per=max(1,min(500,int(b.get('per_page') or 100)))
    except Exception: per=100
    where=[]; args=[]
    if st in ('unused','used','revoked'):
        if st=='unused': where.append("used_by='' AND revoked=0")
        elif st=='used': where.append("used_by<>''")
        else: where.append('revoked=1')
    if q: where.append('(card LIKE ? OR used_by LIKE ?)'); args += [f'%{q}%', f'%{q}%']
    wsql=(' WHERE '+ ' AND '.join(where)) if where else ''
    c=db()
    total=c.execute('SELECT COUNT(*) n FROM cards'+wsql,args).fetchone()['n']
    rows=c.execute('SELECT id,card,plan,days,created_at,used_by,used_at,revoked FROM cards'+wsql+' ORDER BY id DESC LIMIT ? OFFSET ?',args+[per,(page-1)*per]).fetchall(); c.close()
    return {'ok':True,'code':'ok','cards':[dict(r) for r in rows],'total':total,'page':page,'per_page':per,'pages':max(1,(total+per-1)//per)}
def admin_card_revoke(b):
    if not admin_auth(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
    try: cid=int(b.get('id',0) or 0)
    except Exception: cid=0
    if cid<1: return {'ok':False,'code':'invalid_param','message':'卡密 ID 无效'}
    c=db()
    r=c.execute('SELECT card,used_by FROM cards WHERE id=?',(cid,)).fetchone()
    if not r: c.close(); return {'ok':False,'code':'not_found','message':'卡密不存在'}
    c.execute('UPDATE cards SET revoked=1 WHERE id=?',(cid,)); c.commit(); c.close()
    _adm_write(b,'card_revoke','root',b.get('ip',''),'ok',f'card_id={cid}')
    return {'ok':True,'code':'ok','id':cid,'revoked':1}

# ---------- 后台控制台 P0-P3（captain 2026-10） ----------
def get_setting(k, default=''):
 c=db()
 try:
  r=c.execute('SELECT v FROM admin_settings WHERE k=?',(k,)).fetchone(); return r['v'] if r else default
 finally: c.close()
def set_setting(k, v):
 c=db(); c.execute('INSERT OR REPLACE INTO admin_settings(k,v) VALUES(?,?)',(k,str(v))); c.commit(); c.close()
def _admin_key_effective():
 k=get_setting('admin_key','')
 return k or ADMIN_KEY
def _adm_write(b, scene, target, ip, result='ok', reason=''):
 try: audit(norm(target), 'admin_'+scene, ip, '', result, reason)
 except Exception: pass
def _adm(s):
 if not admin_auth(s): return None
 return s

def admin_stats(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 now=time.time(); c=db()
 total=c.execute('SELECT COUNT(*) n FROM sessions').fetchone()['n']
 members=c.execute("SELECT COUNT(*) n FROM sessions WHERE plan='member'").fetchone()['n']
 today_regs=c.execute('SELECT COUNT(*) n FROM sessions WHERE created_at>?',(now-86400,)).fetchone()['n']
 weekly_regs=c.execute('SELECT COUNT(*) n FROM sessions WHERE created_at>?',(now-7*86400,)).fetchone()['n']
 online15=c.execute('SELECT COUNT(*) n FROM sessions WHERE last_verified>?',(now-900,)).fetchone()['n']
 online60=c.execute('SELECT COUNT(*) n FROM sessions WHERE last_verified>?',(now-3600,)).fetchone()['n']
 disabled=c.execute('SELECT COUNT(*) n FROM sessions WHERE status=0').fetchone()['n']
 expiring=c.execute("SELECT COUNT(*) n FROM sessions WHERE plan='member' AND expires_at>? AND expires_at<?",(now,now+7*86400)).fetchone()['n']
 expired=c.execute("SELECT COUNT(*) n FROM sessions WHERE plan='member' AND expires_at<=?",(now,)).fetchone()['n']
 card_total=c.execute('SELECT COUNT(*) n FROM cards').fetchone()['n']
 card_unused=c.execute('SELECT COUNT(*) n FROM cards WHERE used_by=\'\' AND revoked=0').fetchone()['n']
 card_today=c.execute('SELECT COUNT(*) n FROM cards WHERE created_at>?',(now-86400,)).fetchone()['n']
 mail_ok=c.execute("SELECT COUNT(*) n FROM mail_audit WHERE result='sent' AND scene NOT LIKE 'admin_%'").fetchone()['n']
 mail_fail=c.execute("SELECT COUNT(*) n FROM mail_audit WHERE result='failed'").fetchone()['n']
 verifies=c.execute('SELECT COUNT(*) n FROM mail_audit WHERE scene IN (\'verify\',\'login\',\'register\',\'activate\')').fetchone()['n']
 dl=c.execute("SELECT COUNT(*) n FROM market_downloads WHERE paid=1").fetchone()['n']
 inst=c.execute('SELECT COUNT(*) n FROM market_installs WHERE result IN (\'ok\',\'success\',\'installed\')').fetchone()['n']
 admins=c.execute('SELECT COUNT(*) n FROM admin_tokens').fetchone()['n']; c.close()
 market=_load_market(); m_items=len(market); m_installs=sum(int(i.get('installs') or 0) for i in market.values())
 return {'ok':True,'stats':{'users':total,'members':members,'today_regs':today_regs,'weekly_regs':weekly_regs,
  'online_15m':online15,'online_60m':online60,'disabled':disabled,'expiring_7d':expiring,'expired':expired,
  'cards_total':card_total,'cards_unused':card_unused,'cards_today':card_today,
  'mail_ok':mail_ok,'mail_fail':mail_fail,'auth_events':verifies,
  'paid_downloads':dl,'install_records':inst,'admin_sessions':admins,'market_items':m_items,'market_installs':m_installs,
  'now':now,'author_share':AUTHOR_SHARE}}

def admin_users_v2(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 q=str(b.get('q') or '').strip().lower()
 try: page=max(1,int(b.get('page') or 1))
 except Exception: page=1
 try: per=max(1,min(200,int(b.get('per_page') or 50)))
 except Exception: per=50
 try: status=int(b.get('status') or -1)
 except Exception: status=-1
 c=db(); where=[]
 if q: where.append('(email LIKE ? OR user_id LIKE ? OR device_id LIKE ?)')
 if status>=0: where.append('status=?')
 wsql=(' WHERE '+ ' AND '.join(where)) if where else ''
 args=[]
 if q: args += [f'%{q}%', f'%{q}%', f'%{q}%']
 if status>=0: args.append(status)
 total=c.execute('SELECT COUNT(*) n FROM sessions'+wsql,args).fetchone()['n']
 rows=c.execute('SELECT email,user_id,plan,expires_at,credits,device_id,status,created_at,created_ip,last_verified,password_hash FROM sessions'+wsql+' ORDER BY created_at DESC LIMIT ? OFFSET ?',args+[per,(page-1)*per]).fetchall(); c.close()
 now=time.time(); out=[]
 for r in rows:
  d=dict(r); d['hash_type']='scrypt' if str(d.get('password_hash') or '').startswith('scrypt$') else ('sha256' if d.get('password_hash') else 'none'); d.pop('password_hash',None)
  d['expiring_soon']=bool(d.get('expires_at') and d.get('plan')=='member' and float(d['expires_at'])>now and float(d['expires_at'])-now<7*86400)
  out.append(d)
 return {'ok':True,'users':out,'total':total,'page':page,'per_page':per,'pages':max(1,(total+per-1)//per)}

def admin_user_get(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 e=norm(str(b.get('email') or '')); s=find(e)
 if not s: return {'ok':False,'code':'not_found','message':'用户不存在'}
 c=db()
 devices=[dict(r) for r in c.execute('SELECT * FROM devices WHERE email=? ORDER BY last_seen DESC LIMIT 50',(e,))]
 trail=[dict(r) for r in c.execute("SELECT * FROM mail_audit WHERE email=? ORDER BY id DESC LIMIT 50",(e,))]
 used_cards=json.loads(s.get('cards_used') or '[]')
 cards=[dict(r) for r in c.execute('SELECT * FROM cards WHERE used_by=? ORDER BY used_at DESC LIMIT 50',(e,))] or []
 if used_cards and not cards:
  for u in used_cards:
   cards += [dict(r) for r in c.execute('SELECT * FROM cards WHERE card LIKE ?',('%'+u+'%',))]
 dls=[dict(r) for r in c.execute('SELECT * FROM market_downloads WHERE email=? ORDER BY id DESC LIMIT 20',(e,))]
 c.close()
 d=dict(s); d['hash_type']='scrypt' if str(d.get('password_hash') or '').startswith('scrypt$') else ('sha256' if d.get('password_hash') else 'none'); d.pop('password_hash',None)
 return {'ok':True,'code':'ok','user':d,'devices':devices,'trail':trail,'cards':cards,'downloads':dls}

def admin_user_credits(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 e=norm(b.get('email','')); s=find(e)
 if not s: return {'ok':False,'message':'用户不存在'}
 try: delta=int(b.get('delta',0))
 except Exception: delta=0
 if delta==0 or abs(delta)>100000: return {'ok':False,'message':'积分调整需在 ±100000 内且非零'}
 if not rate_hit('admcredits',e,30,3600): return {'ok':False,'message':'操作过于频繁，请稍后再试'}
 s['credits']=max(0,int(s.get('credits') or 0)+delta); save(s)
 _adm_write(b,'credits',e,b.get('ip',''),'ok',f'delta={delta:+d}')
 return {'ok':True,'email':e,'credits':s['credits'],'delta':delta}

def admin_user_kick(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 e=norm(str(b.get('email') or '')); s=find(e)
 if not s: return {'ok':False,'code':'not_found','message':'用户不存在'}
 c=db(); c.execute('UPDATE sessions SET device_id=\'\' WHERE email=?',(e,)); c.commit()
 c.execute('UPDATE devices SET kicked=1 WHERE email=? AND kicked=0',(e,)); c.commit(); c.close()
 _adm_write(b,'kick',e,b.get('ip',''),'ok','强制全部设备下线')
 return {'ok':True,'code':'ok','email':e,'message':'已强制该账号全部设备下线（下次 verify 将触发 device_kick）'}

def admin_devices(b):
    """设备管理：跨账号设备列表（分页/筛选 kicked），补齐管理后台设备视图。"""
    if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
    q=str(b.get('q') or '').strip().lower()
    try: page=max(1,int(b.get('page') or 1))
    except Exception: page=1
    try: per=max(1,min(500,int(b.get('per_page') or 100)))
    except Exception: per=100
    where=[]; args=[]
    if q: where.append('(email LIKE ? OR device_id LIKE ?)'); args += [f'%{q}%', f'%{q}%']
    wsql=(' WHERE '+ ' AND '.join(where)) if where else ''
    c=db()
    total=c.execute('SELECT COUNT(*) n FROM devices'+wsql,args).fetchone()['n']
    rows=c.execute('SELECT email,device_id,first_seen,last_seen,kicked FROM devices'+wsql+' ORDER BY last_seen DESC LIMIT ? OFFSET ?',args+[per,(page-1)*per]).fetchall(); c.close()
    return {'ok':True,'code':'ok','devices':[dict(r) for r in rows],'total':total,'page':page,'per_page':per,'pages':max(1,(total+per-1)//per)}

def admin_device_reset(b):
    """设备管理：解踢/重置指定设备记录（email+device_id 精准定位）。"""
    if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
    e=norm(str(b.get('email') or '')); did=str(b.get('device_id') or '')
    if not valid_email(e) or not did: return {'ok':False,'code':'invalid_param','message':'需提供 email 与 device_id'}
    c=db()
    r=c.execute('SELECT 1 FROM devices WHERE email=? AND device_id=?',(e,did)).fetchone()
    if not r: c.close(); return {'ok':False,'code':'not_found','message':'设备记录不存在'}
    c.execute('UPDATE devices SET kicked=0,last_seen=? WHERE email=? AND device_id=?',(time.time(),e,did)); c.commit(); c.close()
    _adm_write(b,'device_reset',e,b.get('ip',''),'ok',f'device={did}')
    return {'ok':True,'code':'ok','email':e,'device_id':did,'kicked':0}

def admin_settings_get(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 keys=('smtp','plans','card_prefix','totp_enabled','readonly_admin_key','admin_key_set','ops_base','ops_token_set','admin_allow_ips')
 out={}
 for k in keys:
  if k.endswith('_set') or k=='admin_allow_ips': continue
  out[k]=get_setting(k,'')
 out['admin_key_set']=bool(get_setting('admin_key',''))
 out['ops_base']=get_setting('ops_base','')
 out['ops_token_set']=bool(get_setting('ops_token',''))
 out['admin_allow_ips']=','.join(ADMIN_ALLOW_IPS)
 return {'ok':True,'settings':out}

def admin_settings_put(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 upd=b.get('settings',{})
 if not isinstance(upd,dict): return {'ok':False,'message':'settings 需为对象'}
 allow={'smtp','plans','card_prefix','totp_enabled','ops_base','ops_token','admin_allow_ips'}
 for k,v in upd.items():
  if k not in allow: continue
  set_setting(k,str(v))
 if 'ops_token' in upd and upd['ops_token']:
  set_setting('ops_token',str(upd['ops_token']))
 _adm_write(b,'settings',get_setting('ops_base','admin'),b.get('ip',''),'ok',','.join(sorted(upd.keys())))
 if 'admin_allow_ips' in upd:
  os.environ['FRAMEWEAVE_ADMIN_ALLOW_IPS']=str(upd['admin_allow_ips'])
 return admin_settings_get(b)

def admin_key_rotate(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 nk=str(b.get('new_key',''))
 if len(nk)<8: return {'ok':False,'message':'新管理密钥至少 8 位'}
 import hashlib as _h
 set_setting('admin_key',nk)
 _adm_write(b,'key_rotate','root',b.get('ip',''),'ok','管理密钥已轮换')
 return {'ok':True,'message':'管理密钥已轮换（旧会话失效，请用新密钥重新登录）'}

def admin_tokens_list(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 c=db(); rows=[dict(r) for r in c.execute('SELECT token,created_at,expires_at FROM admin_tokens ORDER BY created_at DESC LIMIT 100')]; c.close()
 now=time.time()
 for r in rows: r['active']=now<float(r['expires_at'])
 return {'ok':True,'tokens':rows}

def admin_token_revoke(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 t=str(b.get('token','')); c=db()
 # 始终拒绝吊销"本次鉴权所用令牌"本身（auth_token 由 ROUTES 包装注入）
 if t==str(b.get('auth_token','')): c.close(); return {'ok':False,'message':'不能吊销当前会话'}
 c.execute('DELETE FROM admin_tokens WHERE token=?',(t,)); c.commit(); c.close()
 _adm_write(b,'token_revoke','root',b.get('ip',''),'ok','吊销管理会话')
 return {'ok':True}

def admin_backup(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 c=db()
 # t3：补齐 password_hash/salt/cards_used —— 否则恢复后全部用户密码丢失、无法登录（恢复功能性缺陷）
 # 会话 token 有意不入备份：恢复时统一重置，避免备份泄露导致会话令牌被复用。
 users=[dict(r) for r in c.execute('SELECT email,user_id,plan,expires_at,credits,device_id,status,created_at,created_ip,last_verified,password_hash,salt,cards_used FROM sessions')]
 cards=[dict(r) for r in c.execute('SELECT * FROM cards')]
 audit_rows=[dict(r) for r in c.execute('SELECT * FROM mail_audit')]
 settings=[dict(r) for r in c.execute('SELECT * FROM admin_settings')]
 devices=[dict(r) for r in c.execute('SELECT * FROM devices')]
 c.close()
 data={'meta':{'service':'frameweave-cloud','version':VERSION,'exported_at':time.time()},'users':users,'cards':cards,'audit':audit_rows,'settings':settings,'devices':devices,'market':_load_market()}
 return {'ok':True,'data':data}

def admin_restore(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 if str(b.get('confirm',''))!='yes-i-understand': return {'ok':False,'message':'需 confirm=yes-i-understand 二次确认'}
 d=b.get('data',{})
 if not isinstance(d,dict) or not isinstance(d.get('users'),list) or not isinstance(d.get('cards'),list) or not isinstance(d.get('audit'),list):
  return {'ok':False,'message':'备份数据结构不完整'}
 # t3：恢复前自动生成同库目录快照（backups/pre_restore_*.db，保留最近 10 份），防误恢复无法回退
 import shutil,glob as _glob
 try:
  bdir=os.path.join(os.path.dirname(os.path.abspath(DB_PATH)),'backups')
  os.makedirs(bdir,exist_ok=True)
  if os.path.exists(DB_PATH): shutil.copy2(DB_PATH,os.path.join(bdir,'pre_restore_'+time.strftime('%Y%m%d%H%M%S')+'.db'))
  snaps=sorted(_glob.glob(os.path.join(bdir,'pre_restore_*.db')))
  for old in snaps[:-10]:
   try: os.remove(old)
   except Exception: pass
 except Exception: pass
 c=db()
 try:
  c.execute('BEGIN IMMEDIATE')  # t3：事务化恢复，任一步失败整体回滚，不留下半覆盖数据
  for t in ('devices','cards','mail_audit','sessions'): c.execute(f'DELETE FROM {t}')
  for u in d['users']:
   if not isinstance(u,dict): continue
   row=dict(u); row['password_hash']=str(u.get('password_hash') or ''); row['salt']=str(u.get('salt') or '')
   row['token']=uuid.uuid4().hex[:32]  # t3：恢复后重置会话令牌（备份不携带 token，防泄露复用），用户需重新登录
   row['cards_used']=json.dumps(u.get('cards_used') or []) if not isinstance(u.get('cards_used'),str) else u.get('cards_used')
   c.execute('INSERT OR IGNORE INTO sessions(email,password_hash,salt,user_id,token,plan,expires_at,device_id,credits,cards_used,last_verified,created_at,status,created_ip) VALUES(:email,:password_hash,:salt,:user_id,:token,:plan,:expires_at,:device_id,:credits,:cards_used,:last_verified,:created_at,:status,:created_ip)',row)
  for card in d['cards']:
   if isinstance(card,dict): c.execute('INSERT OR IGNORE INTO cards(id,card,plan,days,created_at,used_by,used_at,revoked) VALUES(:id,:card,:plan,:days,:created_at,:used_by,:used_at,:revoked)',card)
  for a in d['audit']:
   if isinstance(a,dict): c.execute('INSERT INTO mail_audit(email,scene,ip,device_id,result,reason,created_at) VALUES(:email,:scene,:ip,:device_id,:result,:reason,:created_at)',a)
  for dv in d.get('devices') or []:
   if isinstance(dv,dict): c.execute('INSERT OR IGNORE INTO devices(email,device_id,first_seen,last_seen,kicked) VALUES(:email,:device_id,:first_seen,:last_seen,:kicked)',dv)
  for k,v in (d.get('settings') or []):
   c.execute('INSERT OR REPLACE INTO admin_settings(k,v) VALUES(?,?)',(k,v))
  c.commit(); c.close()
 except Exception:
  try: c.rollback()
  except Exception: pass
  try: c.close()
  except Exception: pass
  return {'ok':False,'message':'恢复失败，已回滚，未改动数据'}
 if 'market' in d and isinstance(d['market'],dict): _save_market(d['market'])
 _adm_write(b,'restore','root',b.get('ip',''),'ok','数据恢复')
 return {'ok':True,'message':'备份数据已恢复（会话令牌已重置，用户需重新登录；恢复前快照见 backups/ 目录）'}

def admin_ops_proxy(b):
 """运维视图：只读代理本地服务端点（GET）。ops_base+ops_token 需在设置页配置。"""
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 op=str(b.get('path','')).strip()
 if not op.startswith('/api/'): return {'ok':False,'message':'仅允许 /api/* 路径'}
 base=str(get_setting('ops_base','') or '').rstrip('/')
 if not base: return {'ok':False,'message':'未配置本地服务地址（设置页 ops_base）'}
 # t3：仅允许 http(s)://127.0.0.1|localhost 回环目标（阻断 file:// 本地文件读取与外部 SSRF）
 import re as _re
 if not _re.match(r'^https?://(127\.0\.0\.1|localhost)(:\d+)?(/.*)?$',base,re.I):
  return {'ok':False,'message':'ops_base 仅允许回环地址（http(s)://127.0.0.1|localhost）'}
 tok=get_setting('ops_token','')
 import urllib.request
 import urllib.error
 req=urllib.request.Request(base+op,headers={'X-FW-Local-Token':tok,'Accept':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=15) as r:
   body=r.read().decode('utf-8','replace')
  try: data=json.loads(body)
  except Exception: data={'raw':body[:4000]}
  return {'ok':True,'status':200,'path':op,'data':data}
 except urllib.error.HTTPError as e:
  return {'ok':False,'message':f'本地服务 HTTP {e.code}'}
 except Exception:
  return {'ok':False,'message':'本地服务请求失败（HTTP 连接失败）'}  # t3：不透出异常类型/内部细节

def admin_market_stats(b):
 if not _adm(b): return {'ok':False,'code':'unauthorized','message':'未授权或登录已过期'}
 m=_load_market()
 c=db(); dl=c.execute('SELECT COUNT(*) n FROM market_downloads').fetchone()['n']; ins=c.execute('SELECT COUNT(*) n FROM market_installs').fetchone()['n']; c.close()
 return {'ok':True,'stats':{'items':len(m),'downloads':dl,'installs':ins,
  'by_kind':{k:sum(1 for i in m.values() if i.get('kind')==k) for k in ('node','workflow')}}}

ROUTES={'/api/auth/email/send-code':lambda b: issue_code(b.get('email',''),b.get('scene','register'),b.get('ip',''),b.get('device_id','')) if valid_email(b.get('email','')) else (False,'目前仅支持 QQ 邮箱（@qq.com）'),'/api/auth/register':register,'/api/auth/login':login,'/api/auth/password/reset':reset,'/api/auth/verify':verify,'/api/auth/activate':activate,'/api/market/download-auth':market_download_auth,'/api/market/install-report':market_install_report,'/api/admin/login':admin_login,'/api/admin/logout':admin_logout,'/api/admin/users':admin_users_v2,'/api/admin/user/get':admin_user_get,'/api/admin/user/toggle':admin_toggle,'/api/admin/user/extend':admin_extend_subscription,'/api/admin/user/credits':admin_user_credits,'/api/admin/user/kick':admin_user_kick,'/api/admin/devices':admin_devices,'/api/admin/device/reset':admin_device_reset,'/api/admin/cards':admin_cards,'/api/admin/card/revoke':admin_card_revoke,'/api/admin/audit':admin_audit,'/api/admin/issue-card':admin_issue,'/api/admin/stats':admin_stats,'/api/admin/settings':admin_settings_put,'/api/admin/settings/get':admin_settings_get,'/api/admin/key/rotate':admin_key_rotate,'/api/admin/tokens':admin_tokens_list,'/api/admin/token/revoke':admin_token_revoke,'/api/admin/backup':admin_backup,'/api/admin/restore':admin_restore,'/api/admin/market/list':admin_market_list,'/api/admin/market/upsert':admin_market_upsert,'/api/admin/market/stats':admin_market_stats,'/api/admin/ops':admin_ops_proxy}
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
 const usr=u.users.map(x=>'<tr><td>'+esc(x.email)+(x.expiring_soon?'<br><span class="badge warn">即将到期</span>':'')+'</td><td>'+esc(x.plan)+'</td><td>'+fmt(x.expires_at)+'</td><td>'+(x.hash_type=='scrypt'?'<span class="badge ok">scrypt</span>':'<span class="badge off">sha256</span>')+'</td><td>'+esc(x.credits)+'</td><td>'+(x.status==1?'<span class="badge ok">启用</span>':'<span class="badge off">禁用</span>')+'</td><td><input class="extendDays" data-email="'+esc(x.email)+'" type="number" min="1" max="3650" value="30" style="width:55px"><button onclick="extendUser(\''+esc(x.email)+'\',this.previousElementSibling.value)">加时</button> <button onclick="toggleUser(\''+esc(x.email)+'\')">'+(x.status==1?'禁用':'启用')+'</button></td></tr>').join('');
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
async function extendUser(email,days){const r=await call('/user/extend',{email,days});if(r.ok){toast('已增加 '+r.days+' 天');render()}else{toast(r.message||'加时失败')}}
async function toggleUser(email){const r=await call('/user/toggle',{email});if(r.ok){toast(r.status==1?'已启用':'已禁用');render()}else{toast(r.message||'操作失败')}}
async function revokeCard(id){if(!confirm('确认作废该卡密？作废后不可恢复'))return;const r=await call('/card/revoke',{id});if(r.ok){toast('已作废');render()}else{toast(r.message||'操作失败')}}
async function issue(){const plan=document.getElementById('plan').value;const days=parseInt(document.getElementById('days').value)||30;const count=parseInt(document.getElementById('count').value)||1;const r=await call('/issue-card',{plan,days,count});if(r.ok){const el=document.getElementById('cards');el.style.display='block';el.textContent=r.cards.join('\n');toast('已签发 '+count+' 张')}else{toast(r.message||'签发失败')}}
render();
</script><div class="toast"></div>
</body></html>'''

class H(BaseHTTPRequestHandler):
 def out(self,o,st=200):
  x=json.dumps(o,ensure_ascii=False).encode();self.send_response(st);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(x)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(x)
 def out_html(self):
  bury=None
  try:
   _p=os.path.join(os.path.dirname(os.path.abspath(__file__)),'admin.html')
   if os.path.exists(_p):
    with open(_p,'r',encoding='utf-8') as _f: bury=_f.read()
  except Exception: bury=None
  x=(bury or ADMIN_HTML).encode('utf-8');self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(x)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(x)
 def do_GET(self):
  parsed=urlparse(self.path); path=parsed.path
  if path=='/api/health':self.out({'ok':True,'service':'frameweave-cloud','version':VERSION,'protocol_version':PROTOCOL_VERSION,'auth_revision':AUTH_REVISION,'market':'v1'})
  elif path=='/api/market/items' or path=='/api/market/list':
   from urllib.parse import parse_qs
   qs=parse_qs(parsed.query)
   self.out(market_list({k:(v[0] if v else '') for k,v in qs.items()}))
  elif path=='/admin' or path=='/admin/' or path=='/' or path=='/index.html':self.out_html()
  else:self.out({'ok':False,'code':'not_found','message':'Not Found'},404)
 def _peer_ip(self):
  # t3：反代 IP 还原。仅当 TRUST_XFF=1 且对端为回环（OpenResty 本地反代）时取 X-Forwarded-For 最右项；
  # 默认关闭保持原行为（直接取 socket 对端）。使 IP 限流/管理后台 IP 白名单在反代后真实生效。
  ip=self.client_address[0]
  if TRUST_XFF and ip in ('127.0.0.1','::1','localhost'):
   xff=self.headers.get('X-Forwarded-For','')
   if xff:
    parts=[p.strip() for p in xff.split(',') if p.strip()]
    if parts: ip=parts[-1]
  return ip
 def do_POST(self):
  f=ROUTES.get(urlparse(self.path).path)
  if not f:return self.out({'ok':False,'code':'not_found','message':'Not Found'},404)  # t1 统一错误码
  try:
   raw=self.headers.get('Content-Length','0')
   try: n=int(raw)
   except Exception: n=0
   if n>MAX_BODY: return self.out({'ok':False,'code':'body_too_large','message':'请求体过大'},413)  # t3：请求体上限
   if n<0: n=0
   try:
    b=json.loads(self.rfile.read(n) or b'{}')
   except Exception:
    return self.out({'ok':False,'code':'bad_json','message':'请求体不是合法 JSON'},400)  # t1 统一错误码
   if not isinstance(b,dict): return self.out({'ok':False,'code':'bad_json','message':'请求体需为 JSON 对象'},400)  # t1 参数校验
   b['ip']=self._peer_ip()
   if urlparse(self.path).path.startswith('/api/admin'):
    b['auth_token']=str(b.get('token',''))
   r=f(b)
   if isinstance(r,tuple):r={'ok':r[0],'message':r[1]}
   if urlparse(self.path).path.startswith('/api/auth') and isinstance(r,dict):sign_auth_resp(r)
   self.out(r)
  except Exception as e:print('[cloud-error]',type(e).__name__);self.out({'ok':False,'code':'internal_error','message':'服务器内部错误'},500)  # t1 统一错误码
 def log_message(self,*a):pass
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=int(os.environ.get('CLOUD_PORT','8789')));ap.add_argument('--bind',default='127.0.0.1');a=ap.parse_args();db().close()
 # t3：启动自检（仅告警不阻断，保持既有部署契约；生产必须配置相关环境变量）
 if not CARD_SECRET: print('[cloud] WARN FRAMEWEAVE_CARD_SECRET 未设置：卡密 HMAC 使用空密钥可被伪造，生产必须在 cloud.env 配置')
 if not _admin_key_effective(): print('[cloud] WARN 管理密钥未配置（env FRAMEWEAVE_ADMIN_KEY 或设置页 admin_key）：/api/admin/login 将拒绝登录')
 if not ADMIN_ALLOW_IPS: print('[cloud] INFO 未配置 FRAMEWEAVE_ADMIN_ALLOW_IPS：管理后台未启用 IP 白名单')
 if TRUST_XFF: print('[cloud] TRUST_XFF=1：接受反代 X-Forwarded-For（需 OpenResty 配置 proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for）')
 print('[cloud] start',a.bind,a.port,DB_PATH,'v'+VERSION)
 class _S(ThreadingHTTPServer): daemon_threads=True
 _S((a.bind,a.port),H).serve_forever()
