/* ──────────────────────────────────────────────────────────────────────────
   Bug & Story Manager — its own BrowserWindow (src/bugs.html).
   Local store (jira_items.json via window.ats.jiraItems*) of bugs/stories, each
   Draft or Raised (to Jira). Raise creates the issue + attaches artifacts; Add
   comment posts a comment + attachments; live Jira status is fetched per item.
   Wrapped in an IIFE; exposes window.BugManagerApp.
   ────────────────────────────────────────────────────────────────────────── */
(function () {
  const { useState, useEffect, useCallback } = React;
  const Ic = window.I || {};
  const ats = window.ats || {};

  function relTime(iso) {
    if (!iso) return '';
    const t = new Date(iso.length <= 19 ? iso + 'Z' : iso).getTime();
    if (isNaN(t)) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  // Attach picker: a test's latest-run screenshots/videos + arbitrary files.
  function Artifacts({ tcId, selected, onChange }) {
    const [arts, setArts] = useState({ videos: [], screenshots: [] });
    const [extra, setExtra] = useState([]);
    useEffect(() => {
      if (!tcId) { setArts({ videos: [], screenshots: [] }); return; }
      let live = true;
      ats.getTcArtifacts({ tcId }).then(r => { if (live) setArts(r || { videos: [], screenshots: [] }); });
      return () => { live = false; };
    }, [tcId]);
    const items = [
      ...(arts.screenshots || []).map(a => ({ ...a, kind: 'img' })),
      ...(arts.videos || []).map(a => ({ ...a, kind: 'vid' })),
      ...extra.map(a => ({ ...a, kind: 'file' })),
    ];
    const sel = selected || [];
    const toggle = (p) => { const s = new Set(sel); s.has(p) ? s.delete(p) : s.add(p); onChange([...s]); };
    const addFiles = async () => {
      const r = await ats.pickFiles();
      const metas = (r && r.files) ? r.files : [];
      const added = metas.map(f => (typeof f === 'string' ? f : f.path)).filter(Boolean);
      if (!added.length) return;
      setExtra(e => [...e, ...added.map(p => ({ name: p.split(/[\\/]/).pop(), path: p }))]);
      onChange([...sel, ...added]);
    };
    return (
      <div className="art-picker">
        <div className="art-head">
          <span>Attach{tcId ? ` (${tcId})` : ''}{sel.length ? ` · ${sel.length} selected` : ''}</span>
          <button className="art-add" onClick={addFiles}><Ic.Upload size={12} /> Add file…</button>
        </div>
        {items.length === 0 ? (
          <div className="art-empty">{tcId ? 'No screenshots/videos for this test yet — run it, or add a file.' : 'Set a test case ID to list its run artifacts, or add a file.'}</div>
        ) : (
          <div className="art-grid">
            {items.map(a => {
              const on = sel.includes(a.path);
              const I2 = a.kind === 'vid' ? Ic.Video : a.kind === 'file' ? Ic.File : Ic.FileText;
              return (
                <button key={a.path} className={`art-chip${on ? ' on' : ''}`} onClick={() => toggle(a.path)} title={a.name}>
                  <I2 size={12} /><span className="nm">{a.name}</span>{on && <Ic.Check size={11} />}
                </button>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  function Editor({ item, onClose, onSave }) {
    const [f, setF] = useState({ type: 'bug', status: 'draft', summary: '', description: '', tcId: '', flowId: '', attachments: [], ...item, labels: (item.labels || []) });
    const set = (k, v) => setF(s => ({ ...s, [k]: v }));
    return (
      <div className="bm-overlay" onMouseDown={onClose}>
        <div className="bm-modal" onMouseDown={e => e.stopPropagation()}>
          <div className="bm-modal-head"><span>{f.id ? 'Edit' : 'New'} {f.type === 'story' ? 'Story' : 'Bug'}</span><button className="bm-ic" onClick={onClose}><Ic.X size={15} /></button></div>
          <div className="bm-modal-body">
            <label>Type</label>
            <select value={f.type} onChange={e => set('type', e.target.value)}><option value="bug">Bug</option><option value="story">Story</option></select>
            <label>Test case ID</label>
            <input value={f.tcId} onChange={e => set('tcId', e.target.value)} placeholder="TC-CATALOG-001" />
            <label>Summary</label>
            <input value={f.summary} onChange={e => set('summary', e.target.value)} placeholder="Short summary" />
            <label>Description</label>
            <textarea value={f.description} onChange={e => set('description', e.target.value)} style={{ height: 120 }} />
            <label>Labels (comma-separated)</label>
            <input value={(f.labels || []).join(', ')} onChange={e => set('labels', e.target.value.split(',').map(s => s.trim()).filter(Boolean))} />
            <Artifacts tcId={f.tcId} selected={f.attachments || []} onChange={v => set('attachments', v)} />
          </div>
          <div className="bm-modal-acts">
            <button className="rv-cta" onClick={onClose}>Cancel</button>
            <button className="rv-cta primary" onClick={() => onSave(f)} disabled={!f.summary.trim()}>Save</button>
          </div>
        </div>
      </div>
    );
  }

  function CommentBox({ item, onClose, toast }) {
    const [body, setBody] = useState('');
    const [attach, setAttach] = useState([]);
    const [busy, setBusy] = useState(false);
    const send = async () => {
      setBusy(true);
      const res = await ats.jiraAddComment({ key: item.jiraKey, body, attachments: attach.length ? attach : null, tcId: attach.length ? null : item.tcId });
      setBusy(false);
      if (res && res.success) { toast(`Comment added${res.attachments ? ' · ' + res.attachments + ' file(s)' : ''}`); onClose(); }
      else toast('Failed: ' + ((res && res.error) || 'unknown'));
    };
    return (
      <div className="bm-overlay" onMouseDown={onClose}>
        <div className="bm-modal" onMouseDown={e => e.stopPropagation()}>
          <div className="bm-modal-head"><span>Comment on {item.jiraKey}</span><button className="bm-ic" onClick={onClose}><Ic.X size={15} /></button></div>
          <div className="bm-modal-body">
            <textarea value={body} onChange={e => setBody(e.target.value)} placeholder="Comment (optional if you only attach files)…" style={{ height: 100 }} />
            <Artifacts tcId={item.tcId} selected={attach} onChange={setAttach} />
          </div>
          <div className="bm-modal-acts">
            <button className="rv-cta" onClick={onClose} disabled={busy}>Cancel</button>
            <button className="rv-cta primary" onClick={send} disabled={busy || (!body.trim() && !attach.length)}><Ic.MessageSquare size={13} /> Add to Jira</button>
          </div>
        </div>
      </div>
    );
  }

  function BugManagerApp() {
    const [items, setItems] = useState([]);
    const [statuses, setStatuses] = useState({});
    const [filter, setFilter] = useState('all');
    const [search, setSearch] = useState('');
    const [editing, setEditing] = useState(null);
    const [commenting, setCommenting] = useState(null);
    const [conn, setConn] = useState(null);
    const [toastMsg, setToastMsg] = useState(null);
    const toast = (m) => { setToastMsg(m); setTimeout(() => setToastMsg(null), 2800); };

    const load = useCallback(() => { ats.jiraItemsList().then(r => setItems((r && r.items) || [])); }, []);
    useEffect(() => {
      load();
      if (ats.testJiraConnection) ats.testJiraConnection().then(setConn);
      const h = () => load();
      window.addEventListener('jira-items-changed', h);
      return () => window.removeEventListener('jira-items-changed', h);
    }, [load]);
    useEffect(() => {
      items.filter(i => i.status === 'raised' && i.jiraKey).forEach(i => {
        ats.jiraIssueStatus(i.jiraKey).then(r => { if (r && r.success && r.status) setStatuses(s => ({ ...s, [i.jiraKey]: r.status })); });
      });
    }, [items]);

    const counts = {
      all: items.length,
      draft: items.filter(i => i.status === 'draft').length,
      raised: items.filter(i => i.status === 'raised').length,
      bug: items.filter(i => i.type === 'bug').length,
      story: items.filter(i => i.type === 'story').length,
    };
    const filtered = items.filter(i => {
      if ((filter === 'draft' || filter === 'raised') && i.status !== filter) return false;
      if ((filter === 'bug' || filter === 'story') && i.type !== filter) return false;
      if (search && !((i.summary || '') + ' ' + (i.tcId || '') + ' ' + (i.jiraKey || '')).toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });

    const raise = async (it) => { toast('Raising to Jira…'); const r = await ats.jiraItemRaise(it.id); if (r && r.success) { toast('Raised ' + r.key); window.dispatchEvent(new Event('jira-items-changed')); load(); } else toast('Failed: ' + ((r && r.error) || 'unknown')); };
    const del = async (it) => { if (!window.confirm('Delete this item from the manager? (It does not delete a raised Jira issue.)')) return; await ats.jiraItemDelete(it.id); window.dispatchEvent(new Event('jira-items-changed')); load(); };
    const saveItem = async (it) => { const r = await ats.jiraItemSave(it); if (r && r.success) { setEditing(null); window.dispatchEvent(new Event('jira-items-changed')); load(); toast('Saved'); } else toast('Save failed'); };
    const openJira = (it) => { if (it.jiraUrl) ats.openExternal(it.jiraUrl); };

    const FILTERS = ['all', 'draft', 'raised', 'bug', 'story'];

    return (
      <div className="bm">
        <div className="bm-top">
          <div className="bm-title"><Ic.Bug size={16} /> Bug &amp; Story Manager</div>
          <div className="bm-conn">{conn ? (conn.success ? <span className="ok">● {conn.user || 'connected'}</span> : <span className="warn">Jira not connected — set it in Settings</span>) : null}</div>
          <div className="bm-spacer"></div>
          <button className="rv-cta primary" onClick={() => setEditing({ type: 'bug', status: 'draft', summary: '', description: '', tcId: '', labels: [], attachments: [] })}><Ic.Plus size={13} /> New</button>
        </div>
        <div className="bm-bar">
          {FILTERS.map(f => <button key={f} className={`bm-fl${filter === f ? ' on' : ''}`} onClick={() => setFilter(f)}>{f}{counts[f] != null ? ` ${counts[f]}` : ''}</button>)}
          <div className="bm-spacer"></div>
          <input className="bm-search" placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)} />
          <button className="bm-ic" title="Refresh" onClick={load}><Ic.RefreshCw size={14} /></button>
        </div>
        <div className="bm-list">
          {filtered.length === 0 ? (
            <div className="bm-empty">No items yet. Click <b>New</b>, or create a bug/story from a test case in the main window.</div>
          ) : filtered.map(it => (
            <div className="bm-row" key={it.id}>
              <span className={`bm-type ${it.type}`}>{it.type === 'story' ? <Ic.FileText size={14} /> : <Ic.Bug size={14} />}</span>
              <div className="bm-main">
                <div className="bm-sum">{it.summary || '(no summary)'}</div>
                <div className="bm-meta">
                  {it.tcId && <span className="bm-tc">{it.tcId}</span>}
                  {it.status === 'raised'
                    ? <a className="bm-key" onClick={() => openJira(it)} title="Open in Jira">{it.jiraKey}{(statuses[it.jiraKey] || it.jiraStatus) ? ' · ' + (statuses[it.jiraKey] || it.jiraStatus) : ''}</a>
                    : <span className="bm-draft">Draft</span>}
                  {(it.updatedAt || it.createdAt) && <span className="bm-time">{relTime(it.updatedAt || it.createdAt)}</span>}
                  {(it.attachments && it.attachments.length) ? <span className="bm-att"><Ic.Upload size={10} /> {it.attachments.length}</span> : null}
                </div>
              </div>
              <div className="bm-acts">
                {it.status === 'draft' && <button className="rv-cta sm" onClick={() => raise(it)}><Ic.ExternalLink size={12} /> Raise</button>}
                {it.status === 'raised' && <button className="bm-ic" title="Add comment + attach" onClick={() => setCommenting(it)}><Ic.MessageSquare size={14} /></button>}
                {it.status === 'raised' && <button className="bm-ic" title="Open in Jira" onClick={() => openJira(it)}><Ic.ExternalLink size={14} /></button>}
                <button className="bm-ic" title="Edit" onClick={() => setEditing(it)}><Ic.Edit size={14} /></button>
                <button className="bm-ic danger" title="Delete" onClick={() => del(it)}><Ic.Trash size={14} /></button>
              </div>
            </div>
          ))}
        </div>
        {editing && <Editor item={editing} onClose={() => setEditing(null)} onSave={saveItem} />}
        {commenting && <CommentBox item={commenting} onClose={() => setCommenting(null)} toast={toast} />}
        {toastMsg && <div className="bm-toast">{toastMsg}</div>}
      </div>
    );
  }

  window.BugManagerApp = BugManagerApp;
})();
