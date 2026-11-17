import argparse
import ctypes
from ctypes import wintypes
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parent
STORE = ROOT / 'vault.dpapi'
PORT = 18791
ADDRESS = f'http://127.0.0.1:{PORT}'
CC_SWITCH_DB = Path.home() / '.cc-switch' / 'cc-switch.db'
CLAUDE_SETTINGS = Path.home() / '.claude' / 'settings.json'
CODEX_STATE = Path.home() / '.codex' / 'state_5.sqlite'
CODEX_AUTH = Path.home() / '.codex' / 'auth.json'
LOCK = threading.RLock()
EVENTS = []
CSRF = secrets.token_urlsafe(32)
APPS = {'codex': 'Codex', 'claude': 'Claude Code', 'workbuddy': 'WorkBuddy'}
WORKBUDDY_MODELS = Path.home() / '.workbuddy' / 'models.json'

# Claude 桌面端/CLI 会发送自家模型名（如 claude-opus-4-8、claude-haiku-4-5），
# 上游站点只认自己的模型名，这里统一映射，避免切换站点后出现 model_not_found。
# 映射目标可通过环境变量覆盖：KS_MAP_HAIKU / KS_MAP_OPUS / KS_MAP_SONNET / KS_MAP_FALLBACK
CLAUDE_MODEL_RULES = (
    ('haiku', os.environ.get('KS_MAP_HAIKU', 'gpt-4o-mini')),
    ('opus', os.environ.get('KS_MAP_OPUS', 'gpt-4o')),
    ('sonnet', os.environ.get('KS_MAP_SONNET', 'gpt-4o')),
)
CLAUDE_MODEL_FALLBACK = os.environ.get('KS_MAP_FALLBACK', 'gpt-4o')

def remap_model(name):
    low = name.lower()
    for keyword, target in CLAUDE_MODEL_RULES:
        if keyword in low:
            return target
    if low.startswith('claude'):
        return CLAUDE_MODEL_FALLBACK
    return name

def remap_claude_body(body):
    try:
        payload = json.loads(body)
    except Exception:
        return body
    if not isinstance(payload, dict):
        return body
    model = payload.get('model')
    if not isinstance(model, str) or not model:
        return body
    target = remap_model(model)
    if target == model:
        return body
    payload['model'] = target
    return json.dumps(payload, ensure_ascii=False).encode()

class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

def crypt(data, decrypt=False):
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    dll = ctypes.windll.crypt32
    if decrypt:
        ok = dll.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        ok = dll.CryptProtectData(ctypes.byref(source), 'Local key switcher', None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)

def save(data):
    encrypted = crypt(json.dumps(data).encode())
    temp = STORE.with_suffix('.tmp')
    temp.write_bytes(encrypted)
    temp.replace(STORE)

def load():
    return json.loads(crypt(STORE.read_bytes(), True))

def import_claude_sites():
    sites = []
    if CC_SWITCH_DB.exists():
        try:
            con = sqlite3.connect(f'file:{CC_SWITCH_DB.as_posix()}?mode=ro', uri=True)
            rows = con.execute("select id, name, settings_config from providers where app_type='claude' and category='third_party' order by sort_index").fetchall()
            con.close()
            for pid, name, cfg in rows:
                env = json.loads(cfg).get('env', {})
                key = env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY') or ''
                base = urlsplit(env.get('ANTHROPIC_BASE_URL', ''))
                if key and base.hostname:
                    sites.append({'id': 'claude-' + re.sub(r'[^a-z0-9]+', '-', pid.lower()).strip('-'),
                                  'name': name, 'baseUrl': f'http://{base.hostname}:{base.port or 8080}', 'key': key, 'status': None})
        except Exception:
            pass
    if not sites:
        sites.append({'id': 'claude-a', 'name': 'Claude 站点（待配置）', 'baseUrl': 'http://上游地址:8080', 'key': '', 'status': None})
    return sites[:2]

def initialize():
    if STORE.exists():
        data = load()
        if 'codex' not in data:
            data = {'gatewayToken': data['gatewayToken'], 'claudeToken': secrets.token_urlsafe(40),
                    'codex': {'active': data['active'], 'sites': data['sites']},
                    'claude': {'active': None, 'sites': import_claude_sites()}}
            data['claude']['active'] = data['claude']['sites'][0]['id'] if data['claude']['sites'] else None
            save(data)
        if 'workbuddy' not in data:
            data['workbuddy'] = {'active': data['codex']['active'], 'sites': [dict(s) for s in data['codex']['sites']]}
            data['workbuddyToken'] = secrets.token_urlsafe(40)
            save(data)
        return
    def blank(pid, name, base):
        return {'id': pid, 'name': name, 'baseUrl': base, 'key': '', 'status': None}

    save({'gatewayToken': secrets.token_urlsafe(40), 'claudeToken': secrets.token_urlsafe(40),
          'workbuddyToken': secrets.token_urlsafe(40),
          'codex': {'active': None, 'sites': [blank('site-a', '站点 A', 'http://上游地址:8080/v1'),
                                               blank('site-b', '站点 B', 'http://备用地址:8080/v1')]},
          'claude': {'active': None, 'sites': [blank('claude-a', 'Claude 站点 A', 'http://上游地址:8080'),
                                                blank('claude-b', 'Claude 站点 B', 'http://备用地址:8080')]},
          'workbuddy': {'active': None, 'sites': [blank('wb-a', '站点 A', 'http://上游地址:8080/v1'),
                                                   blank('wb-b', '站点 B', 'http://备用地址:8080/v1')]}})

def normalize(url, app):
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请输入完整的 http(s) 服务地址，不要包含用户名、参数或锚点')
    if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('上游地址不能指向本机转发服务')
    if app == 'claude':
        path = parsed.path.rstrip('/')
        if path not in ('', '/v1', '/keys', '/api'):
            raise ValueError('Claude 通道请填写站点根地址，如 http://服务地址:8080')
        return f'{parsed.scheme}://{parsed.netloc}'
    path = parsed.path.rstrip('/')
    if path == '/keys' or not path:
        path = '/v1'
    if path != '/v1':
        raise ValueError('Codex / WorkBuddy 通道请填写 /v1 接口地址；/keys 管理页会自动转换为 /v1')
    return f'{parsed.scheme}://{parsed.netloc}{path}'

def mask(key):
    return key[:7] + '…' + key[-4:] if len(key) > 12 else ('已设置' if key else '未设置')

def snapshot():
    with LOCK:
        d = load()
        apps = {}
        for app in APPS:
            group = d[app]
            apps[app] = {'active': group['active'],
                         'sites': [{k: v for k, v in s.items() if k != 'key'} | {'keyMask': mask(s['key']), 'hasKey': bool(s['key'])} for s in group['sites']],
                         'integrated': integrated(app)}
        return {'apps': apps, 'events': list(EVENTS), 'gateway': ADDRESS + '/v1', 'claudeGateway': ADDRESS}

def record(app, pid, code, ms, kind='request'):
    with LOCK:
        EVENTS.insert(0, {'time': datetime.now().strftime('%H:%M:%S'), 'app': app, 'site': pid, 'code': code, 'ms': ms, 'kind': kind})
        del EVENTS[40:]

def connection(site, timeout=90):
    p = urlsplit(site['baseUrl'])
    cls = http.client.HTTPSConnection if p.scheme == 'https' else http.client.HTTPConnection
    return cls(p.hostname, p.port, timeout=timeout), p.path.rstrip('/')

def probe(site, app):
    start = time.monotonic()
    conn, prefix = connection(site, 20)
    headers = {'Accept': 'application/json'}
    if app == 'claude':
        headers.update({'Authorization': 'Bearer ' + site['key'], 'anthropic-version': '2023-06-01'})
        path = prefix + '/v1/models'
    else:
        headers['Authorization'] = 'Bearer ' + site['key']
        path = prefix + '/models'
    try:
        conn.request('GET', path, headers=headers)
        res = conn.getresponse()
        body = res.read(1024 * 1024)
        code = res.status
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {}
        raw = str(parsed.get('code', '')) + ' ' + str(parsed.get('message', '')) + ' ' + str(parsed.get('error', ''))
        if code == 200:
            state, detail = 'ok', '鉴权通过；模型列表可访问（不代表剩余额度或生成可用）'
        elif 'DAILY_LIMIT_EXCEEDED' in raw:
            state, detail = 'quota', '每日额度已用完，请换站点或等待服务商重置'
        elif code in (401, 403):
            state, detail = 'auth', '鉴权失败或权限不足，请更新此站点的 key'
        elif code == 429:
            state, detail = 'limited', '服务商返回 429：限流或额度不足'
        else:
            state, detail = 'error', '上游返回 HTTP ' + str(code)
        result = {'state': state, 'detail': detail, 'http': code, 'models': len(parsed.get('data', [])) if isinstance(parsed.get('data'), list) else None}
    except Exception:
        code = 0
        result = {'state': 'offline', 'detail': '连接失败或超时，请检查地址与网络', 'http': 0}
    finally:
        conn.close()
    result['ms'] = round((time.monotonic() - start) * 1000)
    result['checkedAt'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    record(app, site['id'], code, result['ms'], 'probe')
    return result

CONFIG = Path.home() / '.codex/config.toml'

def integrated(app):
    if app == 'codex':
        if not CONFIG.exists():
            return False
        text = CONFIG.read_text(encoding='utf-8-sig')
        match = re.search(r'^\[model_providers\.custom_gpt6\]\s*\n(.*?)(?=^\[|\Z)', text, re.M | re.S)
        return bool(match and ADDRESS + '/v1' in match.group(1))
    if app == 'workbuddy':
        if not WORKBUDDY_MODELS.exists():
            return False
        try:
            entries = json.loads(WORKBUDDY_MODELS.read_text(encoding='utf-8-sig'))
        except Exception:
            return False
        with LOCK:
            data = load()
        hosts = {urlsplit(s['baseUrl']).hostname for s in data['workbuddy']['sites']}
        hits = [x for x in entries if isinstance(x, dict) and (urlsplit(str(x.get('url', ''))).hostname in hosts or x.get('url') == ADDRESS + '/v1')]
        return bool(hits) and all(x.get('url') == ADDRESS + '/v1' and x.get('apiKey') == data['workbuddyToken'] for x in hits)
    if CC_SWITCH_DB.exists():
        with LOCK:
            data = load()
        site = next((s for s in data['claude']['sites'] if s['id'] == data['claude']['active']), None)
        if not site:
            return False
        base = site['baseUrl'].rstrip('/')
        try:
            con = sqlite3.connect(str(CC_SWITCH_DB), timeout=5)
            rows = con.execute("select settings_config from providers where app_type in ('claude','claude-desktop') and is_current=1 and category<>'official'").fetchall()
            con.close()
        except Exception:
            return False
        if not rows:
            return False
        for (raw,) in rows:
            try:
                env = json.loads(raw).get('env', {})
            except Exception:
                return False
            if env.get('ANTHROPIC_AUTH_TOKEN') != site['key']:
                return False
            if not str(env.get('ANTHROPIC_BASE_URL', '')).rstrip('/').startswith(base):
                return False
        return True
    if not CLAUDE_SETTINGS.exists():
        return False
    env = json.loads(CLAUDE_SETTINGS.read_text(encoding='utf-8-sig')).get('env', {})
    return env.get('ANTHROPIC_BASE_URL') == ADDRESS

def write_atomic(path, text):
    tmp = path.with_name(path.name + '.switcher-tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)

def cc_update(app_type, base_url, key):
    """把 cc-switch 里该客户端当前启用的第三方线路改到指定上游，热生效（cc-switch 每次请求读库）。"""
    if not CC_SWITCH_DB.exists():
        return []
    if not base_url or not key:
        return []
    changed = []
    con = sqlite3.connect(str(CC_SWITCH_DB), timeout=8)
    try:
        con.execute('PRAGMA busy_timeout=6000')
        rows = con.execute('select id, settings_config, is_current, category from providers where app_type=?', (app_type,)).fetchall()
        for pid, raw, current, category in rows:
            if current != 1:
                continue
            if category == 'official':
                continue
            try:
                cfg = json.loads(raw)
            except Exception:
                continue
            env = cfg.get('env')
            if not isinstance(env, dict):
                continue
            if env.get('ANTHROPIC_BASE_URL') == base_url and env.get('ANTHROPIC_AUTH_TOKEN') == key:
                continue
            env['ANTHROPIC_BASE_URL'] = base_url
            env['ANTHROPIC_AUTH_TOKEN'] = key
            con.execute('update providers set settings_config=? where id=?', (json.dumps(cfg, ensure_ascii=False), pid))
            changed.append(pid)
        con.commit()
    except Exception:
        return []
    finally:
        con.close()
    return changed

def fix_codex_threads():
    """Codex 会话级 provider 快照优先于全局配置，历史会话必须一起纠正。"""
    if not CODEX_STATE.exists():
        return 0
    try:
        con = sqlite3.connect(str(CODEX_STATE), timeout=8)
        con.execute('PRAGMA busy_timeout=6000')
        n = con.execute("update threads set model_provider='custom_gpt6' where model_provider<>'custom_gpt6'").rowcount
        con.commit()
        con.close()
        return n
    except Exception:
        return 0

def sync_codex(site):
    """Codex 走本机网关；这里只保证兜底线路（auth.json + OpenAI 段）与当前站点一致。"""
    notes = []
    try:
        changed = fix_codex_threads()
        if changed:
            notes.append(f'{changed} 个会话已归位到网关')
    except Exception:
        pass
    try:
        if CODEX_AUTH.exists() and site['key']:
            auth = json.loads(CODEX_AUTH.read_text(encoding='utf-8-sig'))
            if auth.get('OPENAI_API_KEY') != site['key']:
                backup = CODEX_AUTH.with_name('auth.json.bak-switcher-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
                backup.write_bytes(CODEX_AUTH.read_bytes())
                auth['OPENAI_API_KEY'] = site['key']
                auth['auth_mode'] = 'apikey'
                write_atomic(CODEX_AUTH, json.dumps(auth, ensure_ascii=False, indent=2))
                notes.append('Codex 认证 key 已同步')
    except Exception:
        pass
    try:
        if CONFIG.exists() and site['key']:
            text = CONFIG.read_text(encoding='utf-8-sig')
            origin = urlsplit(site['baseUrl'])
            target_base = f'{origin.scheme}://{origin.netloc}/v1'
            match = re.search(r'^\[model_providers\.OpenAI\]\s*\n(.*?)(?=^\[|\Z)', text, re.M | re.S)
            if match:
                block = match.group(0)
                fixed = re.sub(r'^base_url\s*=.*$', 'base_url = "' + target_base + '"', block, count=1, flags=re.M)
                if fixed != block:
                    write_atomic(CONFIG, text.replace(block, fixed, 1))
                    notes.append('Codex 兜底线路已同步')
    except Exception:
        pass
    return notes

def sync_claude(site):
    """Claude CLI 与 Claude 桌面端都归 cc-switch 管，改库即热生效，无需重启客户端。"""
    base = site['baseUrl'].rstrip('/')
    api_base = base if base.endswith('/v1') else base + '/v1'
    notes = []
    if cc_update('claude', base, site['key']):
        notes.append('Claude Code 线路已切换')
    if cc_update('claude-desktop', api_base, site['key']):
        notes.append('Claude 桌面端线路已切换')
    return notes

def apply_site(app, site):
    if app == 'claude':
        return sync_claude(site)
    if app == 'codex':
        return sync_codex(site)
    return []

def integrate(app):
    with LOCK:
        data = load()
        if app == 'codex':
            text = CONFIG.read_text(encoding='utf-8-sig')
            block = '[model_providers.custom_gpt6]\nname = "Local Key Switcher"\nbase_url = "' + ADDRESS + '/v1"\nexperimental_bearer_token = "' + data['gatewayToken'] + '"\nwire_api = "responses"\nrequires_openai_auth = false\nsupports_websockets = false\n\n'
            pattern = r'^\[model_providers\.custom_gpt6\]\s*\n.*?(?=^\[|\Z)'
            if not re.search(pattern, text, re.M | re.S):
                raise ValueError('未找到现有 custom_gpt6 配置，已停止以保护原文件')
            updated = re.sub(pattern, lambda _: block, text, count=1, flags=re.M | re.S)
            updated = re.sub(r'^model_provider\s*=.*$', 'model_provider = "custom_gpt6"', updated, count=1, flags=re.M)
            import tomllib
            tomllib.loads(updated)
            if updated == text:
                return {'message': 'Codex 已接入，无需重复写入'}
            backup = CONFIG.with_name('config.toml.bak-switcher-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            backup.write_bytes(CONFIG.read_bytes())
            write_atomic(CONFIG, updated)
            return {'message': '已备份并接入 Codex。首次需要完全退出并重启 Codex。', 'backup': str(backup)}
        if app == 'workbuddy':
            entries = json.loads(WORKBUDDY_MODELS.read_text(encoding='utf-8-sig'))
            hosts = {urlsplit(s['baseUrl']).hostname for s in data['workbuddy']['sites']}
            hits = [x for x in entries if isinstance(x, dict) and (urlsplit(str(x.get('url', ''))).hostname in hosts or x.get('url') == ADDRESS + '/v1')]
            if not hits:
                raise ValueError('models.json 中未找到指向这两个站点的模型，已停止写入')
            changed = False
            for x in hits:
                if x.get('url') != ADDRESS + '/v1' or x.get('apiKey') != data['workbuddyToken']:
                    x['url'] = ADDRESS + '/v1'
                    x['apiKey'] = data['workbuddyToken']
                    changed = True
            if not changed:
                return {'message': 'WorkBuddy 已接入，无需重复写入'}
            backup = WORKBUDDY_MODELS.with_name('models.json.bak-switcher-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            backup.write_bytes(WORKBUDDY_MODELS.read_bytes())
            write_atomic(WORKBUDDY_MODELS, json.dumps(entries, ensure_ascii=False, indent=2))
            return {'message': f'已备份并接入 WorkBuddy（{len(hits)} 个模型）。新开会话或重新选择模型后生效。', 'backup': str(backup)}
        active = next((s for s in data['claude']['sites'] if s['id'] == data['claude']['active']), None)
        if CC_SWITCH_DB.exists():
            notes = sync_claude(active) if active else []
            return {'message': 'Claude 由 cc-switch 统一托管，已把线路同步到当前站点。' + ('变更：' + '、'.join(notes) if notes else '（本已是当前站点）')}
        obj = json.loads(CLAUDE_SETTINGS.read_text(encoding='utf-8-sig'))
        env = obj.setdefault('env', {})
        if env.get('ANTHROPIC_BASE_URL') == ADDRESS and env.get('ANTHROPIC_AUTH_TOKEN') == data['claudeToken']:
            return {'message': 'Claude Code 已接入，无需重复写入'}
        backup = CLAUDE_SETTINGS.with_name('settings.json.bak-switcher-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.write_bytes(CLAUDE_SETTINGS.read_bytes())
        env['ANTHROPIC_BASE_URL'] = ADDRESS
        env['ANTHROPIC_AUTH_TOKEN'] = data['claudeToken']
        write_atomic(CLAUDE_SETTINGS, json.dumps(obj, ensure_ascii=False, indent=2))
        return {'message': '已备份并接入 Claude Code。重启正在运行的 Claude Code 会话即可。', 'backup': str(backup)}

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass
    def reply(self, code, obj, content='application/json; charset=utf-8'):
        raw = obj.encode() if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        if content.startswith('text/html'):
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(raw)
    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')
    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'error': 'Invalid host'})
        if self.path == '/':
            html = (ROOT / 'index.html').read_text(encoding='utf-8').replace('__CSRF__', CSRF)
            return self.reply(200, html, 'text/html; charset=utf-8')
        if self.path == '/api/state':
            return self.reply(200, snapshot())
        if self.path.startswith('/v1/'):
            return self.forward()
        return self.reply(404, {'error': 'Not found'})
    def do_POST(self):
        if not self.valid_host():
            return self.reply(403, {'error': 'Invalid host'})
        if self.path.startswith('/v1/'):
            return self.forward()
        if self.headers.get('X-CSRF-Token') != CSRF or self.headers.get('Origin') not in (None, ADDRESS, f'http://localhost:{PORT}'):
            return self.reply(403, {'error': '请从本机页面操作'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size > 32768:
                return self.reply(413, {'error': '请求过大'})
            payload = json.loads(self.rfile.read(size) or b'{}')
            if self.path == '/api/switch-all':
                host = str(payload.get('host', '')).strip()
                if not host or len(host) > 253 or '/' in host:
                    raise ValueError('请指定要切换到的站点主机')
                notes, targets = [], {}
                with LOCK:
                    data = load()
                    for name in APPS:
                        target = next((s for s in data[name]['sites'] if urlsplit(s['baseUrl']).hostname == host), None)
                        if target and target['key']:
                            data[name]['active'] = target['id']
                            targets[name] = dict(target)
                    if not targets:
                        raise ValueError('没有任何通道配置了这个站点，请先在该通道添加')
                    save(data)
                for name, target in targets.items():
                    notes.extend(apply_site(name, target))
                    notes.append(f'{APPS[name]} → {target["name"]}（{host}）')
                result = snapshot()
                result['notes'] = notes
                return self.reply(200, result)
            if payload.get('app') not in APPS:
                raise ValueError('未知的应用通道')
            app = payload['app']
            if self.path == '/api/integrate':
                return self.reply(200, integrate(app))
            with LOCK:
                data = load()
                group = data[app]
                if self.path == '/api/add':
                    if len(group['sites']) >= 8:
                        raise ValueError('每个通道最多保留 8 个站点，请先删除不用的站点')
                    baseUrl = normalize(str(payload.get('baseUrl', '')), app)
                    key = str(payload.get('key', '')).strip()
                    if not key:
                        raise ValueError('请填写该站点的 API Key')
                    if len(key) > 512 or re.search(r'\s', key):
                        raise ValueError('Key 格式有误')
                    name = str(payload.get('name', '')).strip()[:40] or f'站点 {len(group["sites"]) + 1}'
                    pid = 'site-' + secrets.token_hex(4)
                    group['sites'].append({'id': pid, 'name': name, 'baseUrl': baseUrl, 'key': key, 'status': None})
                    save(data)
                    return self.reply(200, snapshot())
                site = next(s for s in group['sites'] if s['id'] == payload['id'])
            if self.path == '/api/probe':
                snapshot_site = dict(site)
                result = probe(site, app)
                with LOCK:
                    data = load()
                    current = next(s for s in data[app]['sites'] if s['id'] == site['id'])
                    if current['key'] == snapshot_site['key'] and current['baseUrl'] == snapshot_site['baseUrl']:
                        current['status'] = result
                        save(data)
                return self.reply(200, result)
            notes = []
            with LOCK:
                data = load()
                site = next(s for s in data[app]['sites'] if s['id'] == payload['id'])
                if self.path == '/api/activate':
                    if not site['key']:
                        raise ValueError('请先保存 key')
                    data[app]['active'] = site['id']
                elif self.path == '/api/delete':
                    if data[app]['active'] == site['id']:
                        raise ValueError('该站点正在使用，请先切换到其他站点再删除')
                    data[app]['sites'].remove(site)
                elif self.path == '/api/save':
                    site['name'] = str(payload['name']).strip()[:40] or site['name']
                    site['baseUrl'] = normalize(payload['baseUrl'], app)
                    key = str(payload.get('key', '')).strip()
                    if key:
                        if len(key) > 512 or re.search(r'\s', key):
                            raise ValueError('Key 格式有误')
                        site['key'] = key
                    site['status'] = None
                else:
                    return self.reply(404, {'error': 'Not found'})
                save(data)
                if self.path == '/api/activate':
                    notes = apply_site(app, site)
            result = snapshot()
            result['notes'] = notes
            self.reply(200, result)
        except (KeyError, StopIteration, ValueError) as e:
            self.reply(400, {'error': str(e) or '站点不存在'})
        except Exception:
            self.reply(500, {'error': '本机操作失败，请检查文件权限'})
    def pick_app(self):
        with LOCK:
            data = load()
        auth = self.headers.get('Authorization', '')
        if secrets.compare_digest(auth, 'Bearer ' + data['claudeToken']):
            return 'claude', data
        if secrets.compare_digest(auth, 'Bearer ' + data['gatewayToken']):
            return 'codex', data
        if secrets.compare_digest(auth, 'Bearer ' + data['workbuddyToken']):
            return 'workbuddy', data
        return None, data
    def forward(self):
        app, data = self.pick_app()
        if app is None:
            return self.reply(401, {'error': {'message': '本机代理凭据不匹配，请在切换页面重新接入对应客户端', 'type': 'local_auth_error'}})
        with LOCK:
            site = next(s.copy() for s in data[app]['sites'] if s['id'] == data[app]['active'])
        if self.headers.get('Upgrade'):
            return self.reply(426, {'error': '当前使用 HTTP/SSE，请关闭客户端 WebSocket 传输'})
        conn = None
        started = False
        status = 502
        start = time.monotonic()
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if self.headers.get('Transfer-Encoding') or size > 64 * 1024 * 1024:
                return self.reply(413, {'error': '不支持此请求体大小或编码'})
            body = self.rfile.read(size) if size else None
            if body and app == 'claude':
                body = remap_claude_body(body)
            conn, prefix = connection(site, 300)
            skip = {'host', 'authorization', 'connection', 'content-length', 'transfer-encoding', 'accept-encoding', 'cookie', 'proxy-authorization', 'upgrade'}
            headers = {k: v for k, v in self.headers.items() if k.lower() not in skip}
            headers['Authorization'] = 'Bearer ' + site['key']
            if app == 'claude':
                headers.setdefault('anthropic-version', '2023-06-01')
                path = prefix + self.path
            else:
                path = prefix + self.path[len('/v1'):]
            headers['Accept-Encoding'] = 'identity'
            conn.request(self.command, path, body=body, headers=headers)
            upstream = conn.getresponse()
            status = upstream.status
            self.send_response(status)
            for key, value in upstream.getheaders():
                if key.lower() not in {'transfer-encoding', 'connection', 'content-length', 'keep-alive', 'set-cookie', 'server', 'date'}:
                    self.send_header(key, value)
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Local-App', app)
            self.send_header('X-Local-Upstream', site['id'])
            self.end_headers()
            started = True
            while True:
                chunk = upstream.read1(65536)
                if not chunk:
                    break
                self.wfile.write(f'{len(chunk):X}\r\n'.encode() + chunk + b'\r\n')
                self.wfile.flush()
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            if not started:
                self.reply(502, {'error': {'message': '无法连接选中的服务商，请在切换页面检测网络或选择其他站点', 'type': 'upstream_connection_error'}})
            else:
                self.close_connection = True
        finally:
            if conn:
                conn.close()
            record(app, site['id'], status, round((time.monotonic() - start) * 1000))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    initialize()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    except OSError:
        if args.open:
            import urllib.request
            try:
                with urllib.request.urlopen(ADDRESS + '/api/state', timeout=3) as response:
                    state = json.load(response)
                if 'apps' in state or state.get('gateway') == ADDRESS + '/v1':
                    webbrowser.open(ADDRESS)
                    print('Existing local switcher opened.', flush=True)
                    return
            except Exception:
                pass
        raise
    server.daemon_threads = True
    print('Local Key Switcher: ' + ADDRESS, flush=True)
    if args.open:
        webbrowser.open(ADDRESS)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
