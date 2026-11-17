"""Key Switch 结构性自检（不依赖外部上游，随时可跑）。

用法：先启动 server.py，再运行  python verify.py
覆盖：页面渲染、三通道状态、密钥脱敏、CSRF/Host/网关凭据拒绝路径、本地鉴权路由。
"""
import json
import re
import urllib.request
import urllib.error
import server

BASE = server.ADDRESS


def request(path, data=None, token=None, csrf=None, host=None):
    headers = {}
    if csrf:
        headers['X-CSRF-Token'] = csrf
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if host:
        headers['Host'] = host
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        res = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as err:
        res = err
    return res.code, res.read(), res.headers


def main():
    code, raw, _ = request('/')
    assert code == 200
    html = raw.decode()
    csrf = re.search("const csrf='([^']+)'", html).group(1)
    data = server.load()
    groups = [data[app] for app in server.APPS]
    all_keys = [s['key'] for group in groups for s in group['sites'] if s['key']]
    assert all(k not in html for k in all_keys)
    code, raw, _ = request('/api/state')
    state = json.loads(raw)
    assert set(state['apps']) == set(server.APPS)
    assert all(s['key'].encode() not in raw for g in groups for s in g['sites'] if s['key'])
    for name in ('gatewayToken', 'claudeToken', 'workbuddyToken'):
        assert data[name].encode() not in raw
    print('PASS: page + state, key masking, no local tokens leaked')
    assert request('/api/activate', {'app': 'codex', 'id': 'x'})[0] == 403
    assert request('/api/activate', {'app': 'bad', 'id': 'x'})[0] in (400, 403)
    assert request('/api/state', host='evil.example')[0] == 403
    assert request('/v1/models', token='invalid')[0] == 401
    print('PASS: CSRF / Host / gateway credential rejection enforced')
    original_active = data['codex']['active']
    code, raw, _ = request('/api/add', {'app': 'codex', 'baseUrl': 'http://上游地址:8080/v1', 'key': 'sk-verify-add-test'}, csrf=csrf)
    assert code == 200, raw.decode()
    added = next(s for s in json.loads(raw)['apps']['codex']['sites'] if s['keyMask'].startswith('sk-ver'))
    print('PASS: /api/add creates site with masked key')
    code, raw, _ = request('/api/add', {'app': 'codex', 'baseUrl': 'http://上游地址:8080/v1'}, csrf=csrf)
    assert code == 400 and 'API Key' in raw.decode()
    code, raw, _ = request('/api/add', {'app': 'codex', 'baseUrl': 'http://127.0.0.1:18791/v1', 'key': 'sk-x'}, csrf=csrf)
    assert code == 400 and '本机' in raw.decode()
    print('PASS: /api/add validates empty key and loopback address')
    if original_active:
        code, raw, _ = request('/api/activate', {'app': 'codex', 'id': added['id']}, csrf=csrf)
        assert code == 200
        code, raw, _ = request('/api/delete', {'app': 'codex', 'id': added['id']}, csrf=csrf)
        assert code == 400 and '正在使用' in raw.decode()
        print('PASS: /api/delete refuses the active site')
        code, raw, _ = request('/api/activate', {'app': 'codex', 'id': original_active}, csrf=csrf)
        assert code == 200
    else:
        print('SKIP: active-site deletion guard（该通道尚未启用任何站点）')
    code, raw, _ = request('/api/delete', {'app': 'codex', 'id': added['id']}, csrf=csrf)
    assert code == 200
    assert all(s['id'] != added['id'] for s in json.loads(raw)['apps']['codex']['sites'])
    assert json.loads(raw)['apps']['codex']['active'] == original_active
    print('PASS: /api/delete removes site and keeps active intact')
    configured = 0
    for app in server.APPS:
        group = data[app]
        active = next((s for s in group['sites'] if s['id'] == group['active']), None)
        if active and active['key'] and '上游地址' not in active['baseUrl'] and '备用地址' not in active['baseUrl']:
            token = {'codex': 'gatewayToken', 'claude': 'claudeToken', 'workbuddy': 'workbuddyToken'}[app]
            code, raw, headers = request('/v1/models', token=data[token])
            assert headers.get('X-Local-App') == app
            assert headers.get('X-Local-Upstream') == active['id']
            print('PASS:', app, 'routes to', active['id'], 'HTTP', code)
            configured += 1
        else:
            print('SKIP:', app, '未配置有效站点（在页面填写地址与 key 后此项才会实测）')
    print(f'ALL CHECKS PASSED（{configured} 个通道完成上游实测）')


if __name__ == '__main__':
    main()
