"""Synthetic two-origin request/approval apps. No auth, real data or external network."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
import threading
import time
import uuid


@contextmanager
def request_apps():
    state = {"record": "REQ-" + uuid.uuid4().hex, "created": False, "approved_at": None,
             "propagate": True, "wrong": False, "fields": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def reply(self, body, status=200, mime="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.end_headers()
            try:
                self.wfile.write(body.encode())
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass  # Browser teardown may cancel an outstanding poll.

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 4096: raise ValueError()
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict): raise ValueError()
            except (ValueError, TypeError):
                return self.reply('{"error":"Invalid request"}', 400)
            if self.path == "/create" and not self.server.consumer:
                if not str(data.get("title", "")).strip():
                    return self.reply('{"error":"Name is required"}', 400)
                try: valid = 0 < float(data.get("amount", 0)) < 1000000
                except (ValueError, TypeError): valid = False
                if not valid: return self.reply('{"error":"Amount must be positive"}', 400)
                state.update(created=True, record="REQ-" + uuid.uuid4().hex, approved_at=None,
                             fields={k: str(data.get(k, "")) for k in ("title", "amount", "note")})
                return self.reply(json.dumps({"id": state["record"]}))
            if self.path == "/approve" and self.server.consumer:
                if not state["created"] or data.get("id") != state["record"]:
                    return self.reply('{"error":"Record mismatch"}', 409)
                state["approved_at"] = time.monotonic()
                return self.reply('{"ok":true}')
            return self.reply('{"error":"Wrong application"}', 403)

        def do_GET(self):
            ready = state["propagate"] and state["approved_at"] is not None and time.monotonic() - state["approved_at"] > .4
            if self.path == "/state":
                return self.reply(json.dumps({"status": "Approved" if ready else "Pending", **state["fields"]}))
            record = "WRONG-RECORD" if self.server.consumer and state["wrong"] else state["record"]
            fields = state["fields"] if state["created"] else {}
            form = '''<label>Request name<input data-testid="title" id="title"></label>
                <label>Amount<input type="number" data-testid="amount" id="amount"></label>
                <label>Notes<textarea data-testid="note" id="note"></textarea></label>
                <button data-testid="create" onclick="createRequest()">Create request</button>
                <p data-testid="error" id="error"></p><output data-testid="record" id="record"></output>'''
            approve = '''<button data-testid="approve" onclick="approveRequest(this.parentElement.dataset.recordId)">Approve</button>'''
            row = '<section data-record-id="%s"><span data-testid="request-title">%s</span> <span data-testid="request-amount">%s</span> <span data-testid="request-note">%s</span> <span data-testid="status">Pending</span>%s</section>' % (
                html.escape(record), *(html.escape(fields.get(k, "")) for k in ("title", "amount", "note")), approve if self.server.consumer else "")
            decoy = '<section data-record-id="DECOY"><span data-testid="request-title">Unrelated request</span><span data-testid="status">Pending</span>' + approve + '</section>'
            body = '<h1>' + ("Approval console" if self.server.consumer else "Record creator") + '</h1>'
            body += (decoy if self.server.consumer else form) + row
            body += '''<script>
            async function createRequest(){let r=await fetch('/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:document.getElementById('title').value,amount:document.getElementById('amount').value,note:document.getElementById('note').value})});let d=await r.json();document.getElementById('error').textContent=d.error||'';if(d.id){document.getElementById('record').textContent=d.id;document.querySelector('section:not([data-record-id="DECOY"])').dataset.recordId=d.id;}await refresh();}
            async function approveRequest(id){await fetch('/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});}
            async function refresh(){let d=await (await fetch('/state')).json();let row=document.querySelector('section:not([data-record-id="DECOY"])');row.querySelector('[data-testid=status]').textContent=d.status;for(let key of ['title','amount','note']){row.querySelector('[data-testid=request-'+key+']').textContent=d[key]||'';}}
            setInterval(refresh,200);refresh();</script>'''
            return self.reply(body, mime="text/html; charset=utf-8")

    servers, threads = [], []
    try:
        for consumer in (False, True):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.consumer = consumer
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)
        yield state, [f"http://127.0.0.1:{s.server_port}/" for s in servers]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads: thread.join()
