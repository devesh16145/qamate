"""Two authenticated disposable HTTP apps sharing a real in-memory service.

Used only as a controlled integration target. No external systems or accounts.
Each actor has a different login session and each app enforces its own role.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread, Lock
import html
import json
import time
import uuid
from urllib.request import Request, urlopen


@contextmanager
def integrated_apps(mode='request'):
    if mode not in {'request', 'return'}:
        raise ValueError('Unknown business workflow')
    fields = [('title', 'Request name'), ('amount', 'Amount'), ('note', 'Notes')] if mode == 'request' else [
        ('sku', 'SKU'), ('quantity', 'Quantity'), ('reason', 'Return reason')]
    final_status = 'Approved' if mode == 'request' else 'Return accepted'
    action_label = 'Approve request' if mode == 'request' else 'Accept return'
    state = {'records': [], 'events': [], 'sessions': {}, 'propagate': True}
    state['records'].append({'id': 'OTHER-' + uuid.uuid4().hex,
                            'fields': {key: 'Unrelated record' for key, _ in fields}, 'updated_at': None})
    lock = Lock()
    credentials = {app: uuid.uuid4().hex for app in ('producer', 'consumer')}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def respond(self, data, status=200, mime='application/json', cookie=None):
            raw = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            try: self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        def authenticated(self):
            token = next((p.strip().split('=', 1)[1] for p in self.headers.get('Cookie', '').split(';')
                          if p.strip().startswith('qamate_session=')), '')
            return state['sessions'].get(token) == self.server.app
        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096: raise ValueError()
                data = json.loads(self.rfile.read(length))
            except (ValueError, TypeError):
                return self.respond({'error': 'Invalid request'}, 400)
            app = self.server.app
            if self.path == '/login':
                if data != {'actor': app, 'password': credentials[app]}:
                    return self.respond({'error': 'Invalid credentials'}, 401)
                token = uuid.uuid4().hex
                state['sessions'][token] = app
                return self.respond({'ok': True}, cookie=f'qamate_session={token}; HttpOnly; SameSite=Lax; Path=/')
            if not self.authenticated():
                return self.respond({'error': 'Actor authentication required'}, 401)
            if self.path == '/create' and app == 'producer':
                if any(not str(data.get(key, '')).strip() for key, _ in fields):
                    return self.respond({'error': 'All fields are required'}, 400)
                number = 'amount' if mode == 'request' else 'quantity'
                try: valid = 0 < float(data[number]) <= 50000
                except (ValueError, TypeError): valid = False
                if not valid:
                    return self.respond({'error': 'Positive amount or quantity is required'}, 400)
                record = {'id': ('REQ-' if mode == 'request' else 'RET-') + uuid.uuid4().hex,
                          'fields': {key: str(data[key]) for key, _ in fields}, 'updated_at': None}
                with lock:
                    state['records'].append(record)
                    state['events'].append({'op': 'create', 'id': record['id'], 'app': app})
                return self.respond({'id': record['id']})
            if self.path == '/approve' and app == 'consumer':
                with lock:
                    record = next((r for r in state['records'] if r['id'] == data.get('id')), None)
                    if record is None:
                        return self.respond({'error': 'Unknown record'}, 404)
                    if record['updated_at'] is not None:
                        return self.respond({'error': 'Already processed'}, 409)
                    record['updated_at'] = time.monotonic()
                    state['events'].append({'op': 'approve', 'id': record['id'], 'app': app})
                return self.respond({'ok': True})
            return self.respond({'error': 'Role cannot perform this operation'}, 403)
        def do_GET(self):
            if not self.authenticated():
                return self.respond('Actor sign-in required', 401, 'text/plain')
            if self.path == '/state':
                with lock:
                    records = [{'id': r['id'], 'fields': r['fields'], 'status': final_status if
                        r['updated_at'] is not None and (self.server.app == 'consumer' or
                        state['propagate'] and time.monotonic() - r['updated_at'] >= 1.5) else 'Pending'}
                        for r in state['records']]
                return self.respond({'records': records})
            if self.path != '/':
                return self.respond({'error': 'Not found'}, 404)
            producer = self.server.app == 'producer'
            form = ('<form id="form">' + ''.join(f'<label>{label}<input data-testid="{key}" name="{key}"></label>'
                for key, label in fields) + '<button data-testid="create">Create '+mode+'</button></form>') if producer else ''
            template = '''<!doctype html><html><head><meta charset="utf-8"><title>Integrated QA app</title>
            <style>body{font:18px system-ui;max-width:900px;margin:30px auto}label{display:block;margin:15px}input,button{font:inherit;margin:8px;padding:8px}section{border:1px solid #aaa;padding:15px;margin:15px}span{display:block}</style></head><body>
            <h1>__TITLE__</h1><p>Signed in as __ROLE__</p>__FORM__
            <output data-testid="record" aria-label="Generated record ID" id="record"></output>
            <p data-testid="creation-status" id="creation-status"></p><p data-testid="error" id="error"></p><div id="rows"></div>
            <script>
            const producer=__PRODUCER__, labels=__LABELS__, finalStatus=__FINAL__;
            const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
            let lastState='';
            async function refresh(){const data=await (await fetch('/state')).json();const signature=JSON.stringify(data);if(signature===lastState)return;lastState=signature;
              document.querySelector('#rows').innerHTML=data.records.map(r=>'<section data-record-id="'+esc(r.id)+'">'+
                Object.keys(labels).map(k=>'<span data-testid="request-'+k+'" aria-label="'+labels[k]+'">'+esc(r.fields[k])+'</span>').join('')+
                '<span data-testid="status">'+r.status+'</span>'+(!producer&&r.status!==finalStatus?'<button data-testid="approve" data-id="'+esc(r.id)+'">__ACTION__</button>':'')+'</section>').join('');
              document.querySelectorAll('[data-testid=approve]').forEach(b=>b.onclick=async()=>{b.disabled=true;const response=await fetch('/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:b.dataset.id})});if(!response.ok)document.querySelector('#error').textContent=(await response.json()).error;await refresh();});}
            if(producer)document.querySelector('#form').onsubmit=async e=>{e.preventDefault();const form=e.target;const data=Object.fromEntries(new FormData(form));const response=await fetch('/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok){document.querySelector('#error').textContent=result.error;return;}form.hidden=true;document.querySelector('#record').textContent=result.id;document.querySelector('#creation-status').textContent='Submitted';document.querySelector('#error').textContent='';await refresh();};
            refresh();setInterval(refresh,500);
            </script></body></html>'''
            body = (template.replace('__TITLE__', 'Request portal' if producer else 'Review console')
                    .replace('__ROLE__', self.server.app).replace('__FORM__', form)
                    .replace('__PRODUCER__', json.dumps(producer)).replace('__LABELS__', json.dumps(dict(fields)))
                    .replace('__FINAL__', json.dumps(final_status)).replace('__ACTION__', html.escape(action_label)))
            return self.respond(body, mime='text/html; charset=utf-8')

    servers = [ThreadingHTTPServer(('127.0.0.1', 0), Handler) for _ in range(2)]
    for server, app in zip(servers, ('producer', 'consumer')):
        server.app = app
    threads = [Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads: thread.start()
    urls = {s.app: f'http://127.0.0.1:{s.server_port}/' for s in servers}
    def provision(directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        states = {}
        for app, url in urls.items():
            request = Request(url + 'login', data=json.dumps({'actor': app, 'password': credentials[app]}).encode(),
                              headers={'Content-Type': 'application/json'})
            with urlopen(request) as response:
                token = response.headers['Set-Cookie'].split(';')[0].split('=', 1)[1]
            path = directory / (app + '.json')
            path.write_text(json.dumps({'cookies': [{'name': 'qamate_session', 'value': token, 'domain': '127.0.0.1',
                'path': '/', 'httpOnly': True, 'secure': False, 'sameSite': 'Lax', 'expires': -1}], 'origins': []}), encoding='utf-8')
            states[app] = str(path.resolve())
        return states
    try:
        yield state, urls, provision
    finally:
        for server in servers: server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=2)
