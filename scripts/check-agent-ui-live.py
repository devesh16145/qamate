"""Render the actual shared agent UI with a synthetic IPC boundary; no live account."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/ui-verification-20260929'
OUT.mkdir(exist_ok=True)
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass

server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1400, 'height': 960})
        failures = []
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.add_init_script("""(() => {
          const project = {id:'fixture', name:'Disposable integration'};
          const session = {id:'visual-session', title:'Integrated approval verification', status:'running', message_count:0};
          window.ats = new Proxy({}, {get(_, name) {
            if (name === 'onAgentEvent') return cb => {window.emitFixtureEvent = cb; return () => {};};
            return async () => ({
              listProjects: {projects:[project], active:'fixture'},
              agentListSessions: {sessions:[session]},
              getConfig: {llm:{schema:2, default_provider:'local', providers:{local:{preset:'ollama', model:'fixture'}}}},
              getProviderCatalog: {presets:{ollama:{label:'Ollama (local)', protocol:'ollama', needs_key:false, endpoints:[{id:'default', base_url:'http://localhost:11434'}], models:[]}}},
              getSecretStatus: {keys:{}}, agentContextList:{files:[],folders:[]},
              agentSessionTranscript:{bubbles:[]}
            }[name] || {});
          }});
        })();""")
        page.goto(f'http://127.0.0.1:{server.server_port}/src/agent.html')
        try:
            page.get_by_text('Integrated approval verification', exact=True).click()
        except Exception:
            page.screenshot(path=str(OUT / 'render-failure.png'), full_page=True)
            print('Render errors:', failures, 'Page text:', page.locator('body').inner_text()[:1500])
            raise
        def event(value):
            page.evaluate("value => window.emitFixtureEvent({sessionId:'visual-session', ...value})", value)
        event({'event':'ready', 'provider':'Fixture IPC', 'model':'UI verification',
               'project':'Disposable integration', 'auth':True, 'auth_via':'synthetic actors'})
        event({'event':'decision_workflow_contract', 'contract':{'milestones':[{}, {}, {}, {}]}})
        event({'event':'decision_browser_step', 'scope':'multi_app', 'step':5, 'kind':'capture', 'ok':True,
               'milestone':1, 'app':'producer', 'actor':'producer'})
        event({'event':'model_usage', 'role':'decision', 'input':1400, 'output':180})
        event({'event':'decision_workflow_confirmation', 'status':'requested'})
        expect(page.get_by_text('Rechecking uncertain decision', exact=True)).to_be_visible()
        expect(page.get_by_text('App: producer · Actor: producer · Captured records: 1', exact=True)).to_be_visible()
        page.screenshot(path=str(OUT / 'confirmation.png'), full_page=True)
        event({'event':'decision_browser_complete','scope':'multi_app',
               'result':{'ok':True,'status':'outcomes_observed','milestones_completed':4}})
        expect(page.get_by_text('Live outcomes observed', exact=True)).to_be_visible()
        expect(page.get_by_text('Independent replay pending. Live outcomes alone do not verify the exported test.', exact=True)).to_be_visible()
        page.screenshot(path=str(OUT / 'completed.png'), full_page=True)
        event({'event':'decision_browser_complete','scope':'multi_app',
               'result':{'ok':False,'status':'decision_handoff','milestones_completed':2}})
        expect(page.get_by_text('Stopped: decision_handoff', exact=True)).to_be_visible()
        page.set_viewport_size({'width':900,'height':760})
        page.screenshot(path=str(OUT / 'stopped.png'), full_page=True)
        assert not failures, failures
        browser.close()
        print('PASS: actual agent UI, synthetic IPC, confirmation/completion/stop states; screenshots:', OUT)
finally:
    server.shutdown()
    server.server_close()
