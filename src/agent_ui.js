/* ──────────────────────────────────────────────────────────────────────────
   Shared AI Agent UI — loaded by BOTH the dedicated window (src/agent.html) and
   the main IDE (src/index.html, docked into the right panel) via
       <script type="text/babel" src="agent_ui.js"></script>
   Wrapped in an IIFE so its top-level consts (useState, helpers, component names)
   never collide with index.html's own inline-babel globals. Exposes window.AgentApp.

   AgentApp({ embedded, onUndock, onClose }):
     embedded=false → window mode: session sidebar + chat (its own BrowserWindow)
     embedded=true  → docked mode: a compact session switcher bar + chat, sized to
                      the main window's right panel. onUndock pops out to a window;
                      onClose removes the dock.
   Backend is identical in both: window.ats IPC + onAgentEvent (events are tagged
   with sessionId and broadcast to every window, so a docked panel and a popped-out
   window both stay live).
   ────────────────────────────────────────────────────────────────────────── */
(function () {
  const { useState, useEffect, useRef, useCallback, useMemo } = React;
  const Ic = window.I || {};

  /* ── helpers ─────────────────────────────────────────────────────────────── */
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
  const isImg = (ext) => /^(png|jpe?g|webp|gif|bmp)$/i.test(ext || '');
  const argStr = (a) => {
    if (a == null) return '';
    if (typeof a === 'string') return a;
    try { return Object.entries(a).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', '); }
    catch (e) { return String(a); }
  };
  function emptyRuntime() { return { messages: [], status: 'idle', info: null, attachments: [], lastLog: '', input: '', usage: null }; }
  function finalizeStreaming(prev) {
    const last = prev[prev.length - 1];
    if (last && last.role === 'assistant' && last.streaming) return [...prev.slice(0, -1), { ...last, streaming: false }];
    return prev;
  }
  function bubbleFromTranscript(b, id) {
    return { id, role: b.role, text: b.text || '', tool: b.tool, args: b.args, attachments: b.attachments };
  }
  const fmtTok = (n) => { n = n || 0; if (n < 1000) return '' + n; if (n < 1e6) return (n / 1000).toFixed(n < 10000 ? 1 : 0) + 'k'; return (n / 1e6).toFixed(2) + 'M'; };

  /* ── Markdown rendering ──────────────────────────────────────────────────── */
  function Spans({ text }) {
    const re = /\*\*(.+?)\*\*|__(.+?)__|`([^`\n]+)`|\*(.+?)\*|_(.+?)_|~~(.+?)~~/gs;
    const nodes = []; let last = 0, m, k = 0;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) nodes.push(text.slice(last, m.index));
      if (m[1] != null) nodes.push(<strong key={k++}>{m[1]}</strong>);
      else if (m[2] != null) nodes.push(<strong key={k++}>{m[2]}</strong>);
      else if (m[3] != null) nodes.push(<code key={k++} style={{ background: 'var(--accent-bg)', padding: '1px 5px', borderRadius: 4, fontSize: '0.87em', fontFamily: 'var(--mono)', color: 'var(--text)' }}>{m[3]}</code>);
      else if (m[4] != null) nodes.push(<em key={k++}>{m[4]}</em>);
      else if (m[5] != null) nodes.push(<em key={k++}>{m[5]}</em>);
      else if (m[6] != null) nodes.push(<s key={k++} style={{ opacity: 0.6 }}>{m[6]}</s>);
      last = m.index + m[0].length;
    }
    if (last < text.length) nodes.push(text.slice(last));
    return nodes.length ? nodes : [text];
  }

  function Markdown({ text }) {
    if (!text) return null;
    // Split code fences out first
    const segs = []; const fence = /```(\w*)\n?([\s\S]*?)```/g;
    let last = 0, m;
    while ((m = fence.exec(text)) !== null) {
      if (m.index > last) segs.push({ t: 'prose', s: text.slice(last, m.index) });
      segs.push({ t: 'code', lang: m[1], s: m[2].trimEnd() });
      last = m.index + m[0].length;
    }
    if (last < text.length) segs.push({ t: 'prose', s: text.slice(last) });
    return (
      <div style={{ lineHeight: 1.65, wordBreak: 'break-word' }}>
        {segs.map((seg, si) => {
          if (seg.t === 'code') return (
            <pre key={si} style={{ background: 'var(--editor)', border: '1px solid var(--border)', borderRadius: 6, padding: '10px 12px', margin: '8px 0', fontSize: 11.5, fontFamily: 'var(--mono)', overflowX: 'auto', whiteSpace: 'pre' }}>
              {seg.lang && <span style={{ display: 'block', fontSize: 10, color: 'var(--text-3)', marginBottom: 6, fontFamily: 'var(--mono)' }}>{seg.lang}</span>}
              <code>{seg.s}</code>
            </pre>
          );
          return (
            <React.Fragment key={si}>
              {seg.s.split('\n').map((line, li) => {
                const trim = line.trimStart(), ind = line.length - trim.length;
                if (!trim) return <div key={li} style={{ height: 6 }} />;
                if (trim.startsWith('### ')) return <div key={li} style={{ fontWeight: 600, fontSize: 12.5, marginTop: 10, marginBottom: 2, color: 'var(--text)' }}><Spans text={trim.slice(4)} /></div>;
                if (trim.startsWith('## '))  return <div key={li} style={{ fontWeight: 700, fontSize: 13.5, marginTop: 12, marginBottom: 4, color: 'var(--text)' }}><Spans text={trim.slice(3)} /></div>;
                if (trim.startsWith('# '))   return <div key={li} style={{ fontWeight: 700, fontSize: 15,   marginTop: 14, marginBottom: 5, color: 'var(--text)' }}><Spans text={trim.slice(2)} /></div>;
                if (/^[-*] /.test(trim)) return (
                  <div key={li} style={{ display: 'flex', gap: 8, paddingLeft: 4 + ind * 4, marginTop: 2 }}>
                    <span style={{ color: 'var(--accent)', fontSize: 8, marginTop: 5, flexShrink: 0 }}>◆</span>
                    <span><Spans text={trim.slice(2)} /></span>
                  </div>
                );
                const num = trim.match(/^(\d+)\. /);
                if (num) return (
                  <div key={li} style={{ display: 'flex', gap: 8, paddingLeft: 4 + ind * 4, marginTop: 2 }}>
                    <span style={{ color: 'var(--accent)', flexShrink: 0, fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{num[1]}.</span>
                    <span><Spans text={trim.replace(/^\d+\. /, '')} /></span>
                  </div>
                );
                if (/^---+$/.test(trim)) return <hr key={li} style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '8px 0' }} />;
                if (trim.startsWith('> ')) return (
                  <div key={li} style={{ borderLeft: '3px solid var(--accent)', paddingLeft: 10, margin: '4px 0', color: 'var(--text-2)', fontStyle: 'italic' }}><Spans text={trim.slice(2)} /></div>
                );
                return <div key={li}><Spans text={line} /></div>;
              })}
            </React.Fragment>
          );
        })}
      </div>
    );
  }

  /* ── Tool activity batch (collapsible) ───────────────────────────────────── */
  function ToolBatch({ items }) {
    const [open, setOpen] = useState(false);
    // Pair tool + tool_result
    const pairs = [];
    let i = 0;
    while (i < items.length) {
      if (items[i].role === 'tool') {
        const res = items[i + 1] && items[i + 1].role === 'tool_result' ? items[i + 1] : null;
        pairs.push({ call: items[i], result: res });
        i += res ? 2 : 1;
      } else { i++; }
    }
    const names = [...new Set(pairs.map(p => p.call.tool))];
    const preview = names.slice(0, 4).join(' · ') + (names.length > 4 ? ` +${names.length - 4}` : '');
    return (
      <div style={{ alignSelf: 'flex-start', margin: '1px 0', paddingLeft: 36 }}>
        <button onClick={() => setOpen(o => !o)}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5, color: 'var(--text-3)', background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 0', fontFamily: 'inherit' }}>
          <span style={{ fontSize: 8, color: 'var(--accent)' }}>{open ? '▾' : '▸'}</span>
          <Ic.Zap size={10} style={{ color: 'var(--accent)', flexShrink: 0 }} />
          <span style={{ fontWeight: 600, color: 'var(--text-2)' }}>{pairs.length} tool call{pairs.length !== 1 ? 's' : ''}</span>
          {!open && <span style={{ color: 'var(--text-3)' }}>· {preview}</span>}
        </button>
        {open && (
          <div style={{ marginTop: 4, borderLeft: '1px solid var(--border)', paddingLeft: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {pairs.map(({ call, result }, pi) => (
              <div key={pi}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, fontSize: 11, fontFamily: 'var(--mono)' }}>
                  <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{call.tool}</span>
                  <span style={{ color: 'var(--text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 320 }}>{argStr(call.args).slice(0, 100)}</span>
                </div>
                {result && <div style={{ fontSize: 10.5, fontFamily: 'var(--mono)', color: 'var(--text-3)', paddingLeft: 2, marginTop: 1 }}>↳ {String(result.text).slice(0, 140)}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  /* Group consecutive tool/tool_result messages into batches for rendering */
  function groupMessages(msgs) {
    const out = []; let i = 0;
    while (i < msgs.length) {
      const m = msgs[i];
      if (m.role === 'tool' || m.role === 'tool_result') {
        const startKey = 'tb-' + m.id; const batch = [];
        while (i < msgs.length && (msgs[i].role === 'tool' || msgs[i].role === 'tool_result')) batch.push(msgs[i++]);
        out.push({ type: 'batch', key: startKey, items: batch });
      } else {
        out.push({ type: 'msg', key: String(m.id), msg: m });
        i++;
      }
    }
    return out;
  }

  /* Reduce one streamed agent-event into a session's runtime state. */
  function applyEvent(rt, msg, nextId) {
    const ev = msg.event;
    const messages = rt.messages;
    const set = (patch) => ({ ...rt, ...patch });
    if (ev === 'ready') {
      const info = { provider: msg.provider, model: msg.model, url: msg.url, auth: msg.auth,
        auth_via: msg.auth_via, project: msg.project, project_id: msg.project_id, session_id: msg.session_id };
      let msgs = messages;
      if (msg.resumed && Array.isArray(msg.transcript)) {
        msgs = msg.transcript.map((b) => bubbleFromTranscript(b, nextId()));
      }
      const authTxt = msg.auth ? ` · authenticated (${msg.auth_via})` : ' · NOT logged in — set credentials in settings';
      msgs = [...msgs, { id: nextId(), role: 'system',
        text: `Connected to ${msg.provider} (${msg.model})${msg.project ? ' · ' + msg.project : ''}${authTxt}. Browser open${msg.url ? ' at ' + msg.url : ''}.${msg.resumed ? ' Resumed from saved memory.' : ''}` }];
      return set({ status: 'ready', info, messages: msgs, usage: msg.tokens || rt.usage });
    }
    if (ev === 'usage') return set({ usage: { input: msg.input || 0, output: msg.output || 0, total: msg.total || 0, estimated: !!msg.estimated } });
    if (ev === 'text') {
      const delta = msg.delta || '';
      const last = messages[messages.length - 1];
      if (last && last.role === 'assistant' && last.streaming)
        return set({ messages: [...messages.slice(0, -1), { ...last, text: (last.text || '') + delta }] });
      return set({ messages: [...messages, { id: nextId(), role: 'assistant', text: delta, streaming: true }] });
    }
    if (ev === 'thinking') return set({ status: 'busy' });
    if (ev === 'tool_call') return set({ status: 'busy', messages: [...finalizeStreaming(messages), { id: nextId(), role: 'tool', tool: msg.tool, args: msg.args }] });
    if (ev === 'tool_result') {
      const t = typeof msg.summary === 'string' ? msg.summary : JSON.stringify(msg.summary || '');
      return set({ messages: [...messages, { id: nextId(), role: 'tool_result', tool: msg.tool, text: t }] });
    }
    if (ev === 'turn_complete') {
      let p = finalizeStreaming(messages);
      const last = p[p.length - 1];
      if ((!last || last.role !== 'assistant') && msg.text) p = [...p, { id: nextId(), role: 'assistant', text: msg.text }];
      return set({ status: 'ready', lastLog: '', messages: p });
    }
    if (ev === 'reset_ok') return set({ messages: [], status: 'ready' });
    if (ev === 'error') return set({ status: 'ready', messages: [...messages, { id: nextId(), role: 'error', text: msg.message || 'error' }] });
    if (ev === 'exited') return set({ status: 'idle', info: null, messages: [...messages, { id: nextId(), role: 'system', text: 'Session stopped.' }] });
    if (ev === 'log') return set({ lastLog: String(msg.message || '') });
    if (ev === 'input_required') return set({
      status: 'awaiting_input',
      messages: [...finalizeStreaming(messages), { id: nextId(), role: 'input_request', text: msg.question || '' }],
    });
    return rt;
  }

  function statusColor(rt, live) {
    const st = rt && rt.status;
    if (st === 'busy' || st === 'starting') return 'var(--accent)';
    if (st === 'awaiting_input') return '#f59e0b';
    if (live || st === 'ready') return 'var(--pass, #16a34a)';
    if (st === 'error') return 'var(--fail, #dc2626)';
    return 'var(--text-3)';
  }

  /* ── Session list (left sidebar in window mode; full-width "page 1" when docked) ── */
  function SessionSidebar({ projects, projectId, setProjectId, sessions, activeId, runtimeMap,
                            onSelect, onNew, onRename, onDelete, full, onUndock, onClose }) {
    const [search, setSearch] = useState('');
    const [editing, setEditing] = useState(null);
    const [editText, setEditText] = useState('');
    const [confirmDel, setConfirmDel] = useState(null);
    const filtered = useMemo(() => {
      const q = search.trim().toLowerCase();
      if (!q) return sessions;
      return sessions.filter((s) => (s.title || '').toLowerCase().includes(q));
    }, [sessions, search]);

    return (
      <aside className={'ses-sidebar' + (full ? ' full' : '')}>
        <div className="ses-head">
          <Ic.MessageSquare size={15} style={{ color: 'var(--accent)' }} />
          <b>AI Agent</b>
          <span style={{ flex: 1 }}></span>
          {onUndock && <button className="ses-hbtn" onClick={onUndock} title="Pop out to its own window"><Ic.ExternalLink size={14} /></button>}
          {onClose && <button className="ses-hbtn" onClick={onClose} title="Close the agent panel"><Ic.X size={15} /></button>}
        </div>
        <div className="ses-controls">
          <select className="ses-proj" value={projectId || ''} onChange={(e) => setProjectId(e.target.value || null)} title="Project">
            <option value="">Default (no project)</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
          </select>
          <div className="ses-row2">
            <input className="ses-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search sessions…" />
            <button className="rv-cta primary ses-new" onClick={onNew} title="New session"><Ic.Plus size={14} /> New</button>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: '0 8px 10px', display: 'flex', flexDirection: 'column', gap: 3 }}>
          {filtered.length === 0 && (
            <div style={{ color: 'var(--text-3)', fontSize: 11.5, padding: '10px 6px', lineHeight: 1.5 }}>
              No sessions yet. Click <b>New session</b> to start the agent on this project — each session
              keeps its own browser, memory, and transcript, and you can resume it later.
            </div>
          )}
          {filtered.map((s) => {
            const rt = runtimeMap[s.id];
            const live = !!s.live || (rt && (rt.status === 'busy' || rt.status === 'ready' || rt.status === 'starting'));
            return (
              <div key={s.id} className={'ses-row' + (s.id === activeId ? ' active' : '')} onClick={() => onSelect(s.id)}>
                <span className="ses-dot" style={{ background: statusColor(rt, live) }}></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  {editing === s.id ? (
                    <input autoFocus value={editText} onChange={(e) => setEditText(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => { if (e.key === 'Enter') { onRename(s.id, editText); setEditing(null); } if (e.key === 'Escape') setEditing(null); }}
                      onBlur={() => { onRename(s.id, editText); setEditing(null); }}
                      style={{ width: '100%', fontSize: 12, padding: '1px 4px' }} />
                  ) : (
                    <div onDoubleClick={(e) => { e.stopPropagation(); setEditing(s.id); setEditText(s.title || ''); }}
                      style={{ fontSize: 12, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      title={s.title}>{s.title || 'Untitled'}</div>
                  )}
                  <div style={{ fontSize: 10, color: 'var(--text-3)', fontFamily: 'var(--mono)' }}>
                    {live ? 'running' : (s.message_count ? s.message_count + ' msgs' : 'new')}{s.updated_at ? ' · ' + relTime(s.updated_at) : ''}{(s.tokens && s.tokens.total) ? ' · ' + fmtTok(s.tokens.total) + ' tok' : ''}
                  </div>
                </div>
                {confirmDel === s.id ? (
                  <span style={{ display: 'inline-flex', gap: 3 }} onClick={(e) => e.stopPropagation()}>
                    <button className="ses-x" style={{ opacity: 1, color: 'var(--fail)' }} title="Confirm delete"
                      onClick={() => { setConfirmDel(null); onDelete(s.id); }}><Ic.Check size={13} /></button>
                    <button className="ses-x" style={{ opacity: 1 }} title="Cancel" onClick={() => setConfirmDel(null)}><Ic.X size={13} /></button>
                  </span>
                ) : (
                  <button className="ses-x" title="Delete session" onClick={(e) => { e.stopPropagation(); setConfirmDel(s.id); }}><Ic.Trash size={13} /></button>
                )}
              </div>
            );
          })}
        </div>
      </aside>
    );
  }

  /* ── "No session selected" placeholder (window mode, chat column) ─────────── */
  function NoSession() {
    return (
      <div className="agent-pane" style={{ alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center', color: 'var(--text-3)', maxWidth: 420, padding: 16 }}>
          <Ic.MessageSquare size={28} style={{ color: 'var(--accent)', marginBottom: 10 }} />
          <h2 style={{ fontSize: 15, margin: '0 0 6px', color: 'var(--text-1)' }}>No session selected</h2>
          <p style={{ fontSize: 12, lineHeight: 1.5 }}>Create a <b>New session</b> or pick one from the list.
          Each session drives its own browser, keeps memory, and can run alongside others.</p>
        </div>
      </div>
    );
  }

  /* ── Active session chat (ALWAYS rendered with a real session — no conditional
     hooks; the "no session" case is handled by AgentApp, never here) ────────── */
  function SessionChat({ session, rt, provider, setProvider, headed, setHeaded, toolBudget, setToolBudget,
                         onStart, onSend, onStop, onReset, onOpenBrowser, setInput, setAttachments, projectId, toast, onBack }) {
    const scrollRef = useRef(null);
    const taRef = useRef(null);
    const caretRef = useRef(null);
    const [ctxOpen, setCtxOpen] = useState(false);
    const [ctxDir, setCtxDir] = useState('');
    const [ctxSubdir, setCtxSubdir] = useState('');
    const [ctxLevel, setCtxLevel] = useState(null);   // {dirs, files} | null = not loaded
    const [dragOver, setDragOver] = useState(false);
    // @-mention picker (fuzzy file references into the scoped context folder)
    const [atFiles, setAtFiles] = useState(null);     // flat path list, cached per folder
    const [atOpen, setAtOpen] = useState(false);
    const [atQuery, setAtQuery] = useState('');
    const [atIndex, setAtIndex] = useState(0);
    useEffect(() => { const el = scrollRef.current; if (el) el.scrollTop = el.scrollHeight; }, [rt && rt.messages, rt && rt.status]);

    const status = rt.status;
    const info = rt.info;
    const messages = rt.messages || [];
    const attachments = rt.attachments || [];
    const connected = status === 'ready' || status === 'busy' || status === 'starting' || status === 'awaiting_input' || !!info;
    const busy = status === 'busy' || status === 'starting';
    const awaitingInput = status === 'awaiting_input';
    const canResume = (session.message_count > 0) || messages.length > 0;

    const ctxPid = () => projectId || (info && info.project_id) || null;
    const loadCtx = async (subdir) => {
      try {
        const r = await window.ats.agentContextList({ projectId: ctxPid(), subdir: subdir || '' });
        setCtxDir((r && r.dir) || '');
        setCtxSubdir(subdir || '');
        setCtxLevel({ dirs: (r && r.dirs) || [], files: (r && r.files) || [] });
      } catch (e) { setCtxLevel({ dirs: [], files: [] }); }
    };
    const toggleCtx = () => { const n = !ctxOpen; setCtxOpen(n); if (n) loadCtx(ctxSubdir); };
    const applyCtxResult = (r) => { if (!r) return; setCtxDir(r.dir || ''); setCtxSubdir(''); setCtxLevel({ dirs: r.dirs || [], files: r.files || [] }); setAtFiles(null); };
    const addCtx = async () => {
      try { const r = await window.ats.agentContextAdd({ projectId: ctxPid() }); applyCtxResult(r); if (r && r.added) toast(`Added ${r.added} file(s) to context`); }
      catch (e) { toast('Could not add context files'); }
    };
    const useFolder = async () => {
      try {
        const r = await window.ats.agentContextSetFolder({ projectId: ctxPid() });
        if (!r || r.status === 'cancelled') return;
        if (r.status === 'error') { toast(r.message || 'Could not set folder'); return; }
        applyCtxResult(r);
        toast('Context folder attached to project');
      } catch (e) { toast('Could not attach folder'); }
    };

    // @-mention: lazily fetch the flat file list (cached), fuzzy-match the typed token.
    const ensureAtFiles = async () => {
      if (atFiles) return atFiles;
      try { const r = await window.ats.agentContextFiles({ projectId: ctxPid() }); const f = (r && r.files) || []; setAtFiles(f); return f; }
      catch (e) { setAtFiles([]); return []; }
    };
    const atMatches = useMemo(() => {
      if (!atOpen || !atFiles) return [];
      const q = atQuery.toLowerCase();
      return atFiles
        .filter((p) => p.toLowerCase().includes(q))
        .sort((a, b) => {
          const ab = a.split('/').pop().toLowerCase().startsWith(q) ? 0 : 1;
          const bb = b.split('/').pop().toLowerCase().startsWith(q) ? 0 : 1;
          return ab - bb || a.length - b.length;
        })
        .slice(0, 8);
    }, [atOpen, atFiles, atQuery]);
    const onComposerChange = (e) => {
      const v = e.target.value;
      setInput(v);
      const caret = e.target.selectionStart != null ? e.target.selectionStart : v.length;
      const m = v.slice(0, caret).match(/(?:^|\s)@([^\s@]*)$/);
      if (m) { setAtQuery(m[1]); setAtIndex(0); setAtOpen(true); ensureAtFiles(); }
      else if (atOpen) setAtOpen(false);
    };
    const insertAtMatch = (pathStr) => {
      const ta = taRef.current; if (!ta) { setAtOpen(false); return; }
      const v = rt.input || '';
      const caret = ta.selectionStart != null ? ta.selectionStart : v.length;
      const m = v.slice(0, caret).match(/(?:^|\s)@([^\s@]*)$/);
      if (!m) { setAtOpen(false); return; }
      const start = caret - m[1].length - 1;          // position of '@'
      const after = v.slice(caret);
      const sep = after.startsWith(' ') ? '' : ' ';   // one space, never a double
      setInput(v.slice(0, start) + '@' + pathStr + sep + after);
      setAtOpen(false);
      caretRef.current = start + 1 + pathStr.length + sep.length;  // caret after "@path "
    };
    useEffect(() => {
      if (caretRef.current != null && taRef.current) {
        const pos = caretRef.current; caretRef.current = null;
        try { taRef.current.focus(); taRef.current.setSelectionRange(pos, pos); } catch (e) {}
      }
    });

    const mergeAttachments = (metas) => {
      const seen = new Set(attachments.map((a) => a.path));
      setAttachments([...attachments, ...metas.filter((m) => m.path && !seen.has(m.path))]);
    };
    const addAttachments = async () => {
      try { const r = await window.ats.agentPickFiles(); if (r && r.status === 'success' && r.files && r.files.length) mergeAttachments(r.files); }
      catch (e) { toast('Could not open file picker'); }
    };
    const removeAttachment = (p) => setAttachments(attachments.filter((a) => a.path !== p));
    const onDrop = (e) => {
      e.preventDefault(); setDragOver(false);
      const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      const metas = files.map((f) => {
        const p = (window.ats.getPathForFile && window.ats.getPathForFile(f)) || '';
        const name = f.name || (p ? p.split(/[\\/]/).pop() : 'file');
        return { path: p, name, ext: (name.split('.').pop() || '').toLowerCase(), size: f.size || 0 };
      }).filter((m) => m.path);
      if (!metas.length) { toast('Could not read dropped file path'); return; }
      mergeAttachments(metas);
    };
    const onKey = (e) => {
      if (atOpen && atMatches.length) {
        if (e.key === 'ArrowDown') { e.preventDefault(); setAtIndex((i) => (i + 1) % atMatches.length); return; }
        if (e.key === 'ArrowUp') { e.preventDefault(); setAtIndex((i) => (i - 1 + atMatches.length) % atMatches.length); return; }
        if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); insertAtMatch(atMatches[atIndex] || atMatches[0]); return; }
        if (e.key === 'Escape') { e.preventDefault(); setAtOpen(false); return; }
      }
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
    };

    const MimoAvatar = () => (
      <div style={{ width: 28, height: 28, borderRadius: 7, background: 'linear-gradient(135deg,#1d4ed8 0%,#7c3aed 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 800, color: '#fff', flexShrink: 0, letterSpacing: '-0.5px', userSelect: 'none' }}>M</div>
    );
    const YouAvatar = () => (
      <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><Ic.User size={13} style={{ color: '#fff' }} /></div>
    );

    const bubble = (m) => {
      if (m.role === 'user') return (
        <div key={m.id} style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'flex-end', gap: 8 }}>
          <div style={{ maxWidth: '78%', background: 'var(--editor)', color: 'var(--text)', border: '1px solid var(--border)', padding: '9px 13px', borderRadius: '12px 12px 3px 12px', fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {m.text}
            {m.attachments && m.attachments.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                {m.attachments.map((n, i) => (
                  <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 10.5, fontFamily: 'var(--mono)', background: 'var(--bg)', border: '1px solid var(--accent)', borderRadius: 6, padding: '1px 6px' }}><Ic.File size={10} /> {n}</span>
                ))}
              </div>
            )}
          </div>
          <YouAvatar />
        </div>
      );

      if (m.role === 'assistant') return (
        <div key={m.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <MimoAvatar />
          <div style={{ flex: 1, minWidth: 0, paddingTop: 3, fontSize: 13, color: 'var(--text)' }}>
            <Markdown text={m.text} />
            {m.streaming && <span className="live-dot" style={{ marginLeft: 4 }}></span>}
          </div>
        </div>
      );

      // tool/tool_result are batched by groupMessages; these are fallback singles
      if (m.role === 'tool') return (
        <div key={m.id} style={{ display: 'flex', alignItems: 'baseline', gap: 6, fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--accent)', paddingLeft: 38 }}>
          <Ic.Zap size={10} /><b>{m.tool}</b><span style={{ color: 'var(--text-3)' }}>{argStr(m.args).slice(0, 100)}</span>
        </div>
      );
      if (m.role === 'tool_result') return (
        <div key={m.id} style={{ fontSize: 10.5, fontFamily: 'var(--mono)', color: 'var(--text-3)', paddingLeft: 54 }}>↳ {String(m.text).slice(0, 120)}</div>
      );

      if (m.role === 'error') return (
        <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 8, alignSelf: 'center', fontSize: 11.5, color: 'var(--fail)', background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.25)', borderRadius: 8, padding: '7px 12px', maxWidth: '90%' }}>
          <Ic.AlertCircle size={14} style={{ flexShrink: 0 }} /> {m.text}
        </div>
      );

      if (m.role === 'input_request') return (
        <div key={m.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <MimoAvatar />
          <div style={{ flex: 1, minWidth: 0, padding: '10px 14px', background: 'rgba(245,158,11,0.08)', border: '1.5px solid #f59e0b', borderRadius: '12px 12px 12px 3px' }}>
            <div style={{ fontSize: 10.5, fontWeight: 700, color: '#f59e0b', marginBottom: 5, display: 'flex', alignItems: 'center', gap: 5 }}><Ic.Pause size={12} /> Needs your input</div>
            <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.6 }}>{m.text}</div>
            <div style={{ marginTop: 6, fontSize: 10.5, color: 'var(--text-3)' }}>Type your answer in the box below ↓</div>
          </div>
        </div>
      );

      // system messages — centered pill
      return (
        <div key={m.id} style={{ display: 'flex', justifyContent: 'center' }}>
          <div style={{ fontSize: 10.5, color: 'var(--text-3)', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 20, padding: '3px 12px', maxWidth: '80%', textAlign: 'center', lineHeight: 1.5 }}>{m.text}</div>
        </div>
      );
    };

    return (
      <div className="agent-pane">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: '1px solid var(--accent-bg)', position: 'relative', flexWrap: 'wrap' }}>
          {onBack && <button className="rv-cta" onClick={onBack} title="Back to sessions"><Ic.ChevronLeft size={13} /></button>}
          <b style={{ fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 200 }} title={session.title}>{session.title || 'Untitled session'}</b>
          {info && <span style={{ fontSize: 10.5, color: 'var(--text-3)', fontFamily: 'var(--mono)' }}>{info.model}{info.auth ? ' · ' + (info.auth_via || 'auth') : ' · no auth'}</span>}
          {connected && (
            <span title={`Tokens this session${(rt.usage && rt.usage.estimated) ? ' (estimated — the model provider did not report usage)' : ''} — in ${(rt.usage && rt.usage.input) || 0}, out ${(rt.usage && rt.usage.output) || 0}, total ${(rt.usage && rt.usage.total) || 0}`}
              style={{ fontSize: 10.5, color: 'var(--accent)', fontFamily: 'var(--mono)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Ic.Activity size={10} />{(rt.usage && rt.usage.estimated) ? '~' : ''}{fmtTok((rt.usage && rt.usage.total) || 0)} tok
            </span>
          )}
          <span style={{ flex: 1 }}></span>
          {busy && <span style={{ fontSize: 10.5, color: 'var(--accent)' }}><span className="live-dot"></span> {status === 'starting' ? 'starting' : 'working'}…</span>}
          {awaitingInput && <span style={{ fontSize: 10.5, color: '#f59e0b', fontWeight: 600 }}>⏸ Needs your answer ↓</span>}
          <button className="rv-cta" onClick={toggleCtx} title="Scoped project folder the agent explores"><Ic.Folder size={12} /> Context</button>
          <button className="rv-cta" onClick={() => window.ats.agentMemoryOpen({ projectId: ctxPid() })} title="Open the agent's memory file (AGENT_MEMORY.md)"><Ic.FileText size={12} /> Memory</button>
          {connected && <button className="rv-cta" onClick={onReset} disabled={busy} title="Clear this session's conversation"><Ic.RefreshCw size={12} /></button>}
          {connected && <button className="rv-cta" onClick={onStop} title="Stop this session + close its browser">Stop</button>}
          {ctxOpen && (
            <div style={{ position: 'absolute', top: '100%', right: 12, zIndex: 30, width: 340, background: 'var(--bg)', border: '1px solid var(--accent)', borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.22)', padding: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                <b style={{ fontSize: 11 }}>Project folder</b><span style={{ flex: 1 }}></span>
                <button className="rv-cta" onClick={useFolder} title="Point this project at an existing folder on disk"><Ic.FolderOpen size={11} /> Use folder…</button>
                <button className="rv-cta" onClick={addCtx} title="Copy individual files into the managed context folder"><Ic.Plus size={11} /> Add files</button>
                <button className="rv-cta" onClick={() => window.ats.agentContextOpen({ projectId: ctxPid() })} title="Open the folder in your file explorer"><Ic.FolderOpen size={11} /></button>
                <button className="rv-cta" onClick={() => setCtxOpen(false)}><Ic.X size={11} /></button>
              </div>
              {ctxDir
                ? <div style={{ fontSize: 10, color: 'var(--text-3)', fontFamily: 'var(--mono)', marginBottom: 4, wordBreak: 'break-all' }} title={ctxDir}>{ctxDir}</div>
                : <div style={{ fontSize: 11, color: 'var(--text-3)', padding: '8px 2px', lineHeight: 1.5 }}>No folder yet. <b>Use folder…</b> to give this project a folder, or <b>Add files</b> to copy docs in.</div>}
              <div style={{ fontSize: 10, color: 'var(--text-3)', marginBottom: 6, lineHeight: 1.45 }}>The agent explores this folder on demand (lists, searches, reads) and you can <b>@</b>-mention files in chat — nothing is imported.</div>
              {ctxLevel && (
                <React.Fragment>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, fontFamily: 'var(--mono)', marginBottom: 4, flexWrap: 'wrap' }}>
                    <span style={{ cursor: 'pointer', color: ctxSubdir ? 'var(--accent)' : 'var(--text-2)' }} onClick={() => loadCtx('')}>root</span>
                    {ctxSubdir && ctxSubdir.split('/').map((seg, i, arr) => (
                      <span key={i}><span style={{ color: 'var(--text-3)' }}>/</span><span style={{ cursor: 'pointer', color: i === arr.length - 1 ? 'var(--text-2)' : 'var(--accent)' }} onClick={() => loadCtx(arr.slice(0, i + 1).join('/'))}>{seg}</span></span>
                    ))}
                  </div>
                  <div style={{ maxHeight: 200, overflowY: 'auto', fontSize: 11 }}>
                    {!ctxLevel.dirs.length && !ctxLevel.files.length && <div style={{ color: 'var(--text-3)', padding: '8px 2px' }}>(empty)</div>}
                    {ctxLevel.dirs.map((d) => (
                      <div key={'d:' + d.name} onClick={() => loadCtx(d.name)} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 2px', fontFamily: 'var(--mono)', cursor: 'pointer' }} title={d.name}>
                        <Ic.Folder size={11} style={{ color: 'var(--accent)' }} />
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name.split('/').pop()}/</span>
                      </div>
                    ))}
                    {ctxLevel.files.map((f) => (
                      <div key={'f:' + f.name} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 2px', fontFamily: 'var(--mono)' }} title={f.name}>
                        {isImg(f.ext) ? <Ic.File size={11} style={{ color: 'var(--text-3)' }} /> : <Ic.FileText size={11} style={{ color: 'var(--text-3)' }} />}
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name.split('/').pop()}</span>
                        <span style={{ color: 'var(--text-3)' }}>{f.size ? Math.max(1, Math.round(f.size / 1024)) + 'k' : ''}</span>
                      </div>
                    ))}
                  </div>
                </React.Fragment>
              )}
            </div>
          )}
        </div>

        <div ref={scrollRef}
          onDragOver={(e) => { e.preventDefault(); if (!dragOver) setDragOver(true); }}
          onDragLeave={(e) => { if (e.currentTarget === e.target) setDragOver(false); }}
          onDrop={onDrop}
          style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8, outline: dragOver ? '2px dashed var(--accent)' : 'none', outlineOffset: -6 }}>
          {messages.length === 0 && !connected && (
            <div style={{ margin: 'auto', textAlign: 'center', color: 'var(--text-3)', maxWidth: 420, fontSize: 12.5, lineHeight: 1.6 }}>
              {canResume ? 'This session is paused. Resume it to continue from where you left off — its memory is restored.'
                         : 'New session. Start the agent below, then tell it what to do — e.g. "explore the orders area and write a test for the full order lifecycle."'}
            </div>
          )}
          {groupMessages(messages).map(g =>
            g.type === 'batch'
              ? <ToolBatch key={g.key} items={g.items} />
              : bubble(g.msg)
          )}
          {dragOver && <div style={{ alignSelf: 'center', margin: 'auto', color: 'var(--accent)', fontSize: 12, fontFamily: 'var(--mono)' }}>Drop files to attach</div>}
        </div>

        {busy && rt.lastLog && (
          <div style={{ padding: '2px 16px', fontSize: 10, color: 'var(--text-3)', fontFamily: 'var(--mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{rt.lastLog}</div>
        )}

        {!connected ? (
          <div style={{ padding: '12px 16px', borderTop: '1px solid var(--accent-bg)', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <select value={provider} onChange={(e) => setProvider(e.target.value)} title="LLM provider" style={{ fontSize: 11, padding: '3px 6px' }}>
              <option value="mimo">Xiaomi MiMo</option><option value="openai">OpenAI</option><option value="ollama">Local (Ollama)</option>
            </select>
            <label style={{ fontSize: 11.5, color: 'var(--text-2)', cursor: 'pointer', userSelect: 'none' }}>
              <input type="checkbox" checked={headed} onChange={(e) => setHeaded(e.target.checked)} style={{ marginRight: 5, verticalAlign: 'middle' }} />
              Watch live
            </label>
            <label style={{ fontSize: 11, color: 'var(--text-2)', display: 'flex', alignItems: 'center', gap: 5 }} title="Max tool calls per turn before the agent pauses and summarises. 'Never' runs until it finishes.">
              <Ic.Zap size={11} />
              <select value={toolBudget} onChange={(e) => setToolBudget(Number(e.target.value))} style={{ fontSize: 11, padding: '3px 6px' }}>
                <option value={30}>30 steps</option>
                <option value={50}>50 steps</option>
                <option value={100}>100 steps</option>
                <option value={0}>Never pause</option>
              </select>
            </label>
            <span style={{ flex: 1 }}></span>
            <button className="rv-cta primary" onClick={onStart} disabled={status === 'starting'}>
              <Ic.Play size={13} /> {status === 'starting' ? 'Starting…' : (canResume ? 'Resume' : 'Start session')}
            </button>
          </div>
        ) : (
          <div style={{ padding: '8px 16px 10px', borderTop: '1px solid var(--accent-bg)' }}>
            {attachments.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 6 }}>
                {attachments.map((a) => (
                  <span key={a.path} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10.5, fontFamily: 'var(--mono)', background: 'var(--bg-2, var(--bg))', border: '1px solid var(--accent-bg)', borderRadius: 6, padding: '2px 4px 2px 7px' }}>
                    {isImg(a.ext) ? <Ic.File size={10} /> : <Ic.FileText size={10} />}
                    <span style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={a.name}>{a.name}</span>
                    <button onClick={() => removeAttachment(a.path)} title="Remove" style={{ display: 'inline-flex', border: 'none', background: 'transparent', cursor: 'pointer', padding: 0, color: 'var(--text-3)' }}><Ic.X size={11} /></button>
                  </span>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, position: 'relative' }}>
              {atOpen && atMatches.length > 0 && (
                <div style={{ position: 'absolute', bottom: '100%', left: 40, right: 0, marginBottom: 6, maxHeight: 210, overflowY: 'auto', background: 'var(--bg)', border: '1px solid var(--accent)', borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.22)', zIndex: 40, fontSize: 11.5, fontFamily: 'var(--mono)' }}>
                  <div style={{ padding: '4px 8px', fontSize: 10, color: 'var(--text-3)', borderBottom: '1px solid var(--accent-bg)' }}>Reference a file — ↑↓ then Enter</div>
                  {atMatches.map((p, i) => (
                    <div key={p} onMouseDown={(e) => { e.preventDefault(); insertAtMatch(p); }} onMouseEnter={() => setAtIndex(i)}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px', cursor: 'pointer', background: i === atIndex ? 'var(--accent-bg)' : 'transparent' }}>
                      <Ic.FileText size={11} style={{ color: 'var(--text-3)' }} />
                      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p}>{p}</span>
                    </div>
                  ))}
                </div>
              )}
              <button className="rv-cta" onClick={addAttachments} disabled={status !== 'ready'} title="Attach documents or images" style={{ alignSelf: 'flex-end' }}><Ic.Upload size={14} /></button>
              <button className="rv-cta" onClick={onOpenBrowser} title="Open the app under test in your browser" style={{ alignSelf: 'flex-end' }}><Ic.ExternalLink size={14} /></button>
              <textarea ref={taRef} value={rt.input} onChange={onComposerChange} onKeyDown={onKey}
                onBlur={() => setTimeout(() => setAtOpen(false), 120)}
                placeholder={awaitingInput ? 'Type your answer and press Enter…' : status === 'ready' ? 'Message MIMO… (Enter to send · @ to reference a file · drop files to attach)' : 'MIMO is working…'}
                disabled={status !== 'ready' && status !== 'awaiting_input'}
                style={{ flex: 1, minHeight: 38, maxHeight: 120, resize: 'vertical', fontSize: 13, padding: '9px 10px', fontFamily: 'inherit', background: awaitingInput ? 'rgba(245,158,11,0.06)' : 'var(--editor)', color: 'var(--text)', border: awaitingInput ? '1.5px solid #f59e0b' : '1px solid var(--border)', borderRadius: 'var(--radius-lg)', outline: 'none', transition: 'border-color 0.15s, background 0.15s' }} />
              <button className="rv-cta primary" onClick={onSend} disabled={(status !== 'ready' && status !== 'awaiting_input') || (!(rt.input || '').trim() && !attachments.length)} style={{ alignSelf: 'flex-end' }}><Ic.Play size={13} /></button>
            </div>
          </div>
        )}
      </div>
    );
  }

  /* ── Root ────────────────────────────────────────────────────────────────── */
  /* ── Chat initiator: compose a first message to start a brand-new session ──── */
  function StartComposer({ onStart, headed, setHeaded, toolBudget, setToolBudget, onOpenBrowser, projectId }) {
    const [text, setText] = useState('');
    const [atts, setAtts] = useState([]);
    const [autoOpen, setAutoOpen] = useState(false);
    const go = () => { const t = text.trim(); if (!t && !atts.length) return; onStart(t, atts); setText(''); setAtts([]); };
    const onKey = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); } };
    const addFiles = async () => { const r = await window.ats.agentPickFiles(); const files = (r && r.files) || []; if (files.length) setAtts((a) => [...a, ...files]); };
    return (
      <div className="ses-initiator">
        {autoOpen && (
          <RunAutoModal
            projectId={projectId}
            onClose={() => setAutoOpen(false)}
            onConfirm={(msg) => { setAutoOpen(false); onStart(msg, []); }}
          />
        )}
        {atts.length > 0 && (
          <div className="ses-init-atts">
            {atts.map((a, i) => (
              <span key={i} className="att-chip"><Ic.File size={10} /><span className="nm">{a.name}</span>
                <button onClick={() => setAtts((x) => x.filter((_, j) => j !== i))}><Ic.X size={10} /></button></span>
            ))}
          </div>
        )}
        <textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
          className="ses-init-ta"
          placeholder={'Message the agent to start a new session… e.g. "log in, open Orders, and write a test for the order lifecycle." (Enter to send)'} />
        <div className="ses-init-bar">
          <button className="rv-cta sm" onClick={addFiles} title="Attach documents or images"><Ic.Upload size={13} /></button>
          <button className="rv-cta sm" onClick={onOpenBrowser} title="Open the app under test in your browser"><Ic.ExternalLink size={13} /></button>
          <label className="ses-headed" title="Show the automation browser window while the agent works">
            <input type="checkbox" checked={headed} onChange={(e) => setHeaded(e.target.checked)} /> Watch live
          </label>
          <label style={{ fontSize: 11, color: 'var(--text-2)', display: 'flex', alignItems: 'center', gap: 5 }} title="Max tool calls per turn before the agent pauses. 'Never' runs until done.">
            <Ic.Zap size={11} />
            <select value={toolBudget} onChange={(e) => setToolBudget(Number(e.target.value))} style={{ fontSize: 11, padding: '2px 5px' }}>
              <option value={30}>30 steps</option>
              <option value={50}>50 steps</option>
              <option value={100}>100 steps</option>
              <option value={0}>Never pause</option>
            </select>
          </label>
          <span style={{ flex: 1 }}></span>
          <button className="rv-cta sm" onClick={() => setAutoOpen(true)} title="Autonomous mode: map the app, extract flows from a spec, and author all tests unsupervised">
            <Ic.Zap size={12} /> Auto
          </button>
          <button className="rv-cta primary sm" onClick={go} disabled={!text.trim() && !atts.length}><Ic.Play size={13} /> Start</button>
        </div>
      </div>
    );
  }

  /* ── Autonomous Run Modal ────────────────────────────────────────────────── */
  let _autoAppSeq = 0;
  const mkApp = (label='', url='', email='', password='') =>
    ({ id: ++_autoAppSeq, label, url, email, password, showPw: false });

  function AppCard({ app, index, total, onChange, onRemove }) {
    const inp = (field) => (e) => onChange(app.id, field, e.target.value);
    const iStyle = { width: '100%', fontSize: 12, padding: '5px 8px', background: 'var(--editor)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--text)', outline: 'none', boxSizing: 'border-box' };
    return (
      <div style={{ border: '1px solid var(--border)', borderRadius: 8, marginBottom: 8, overflow: 'hidden' }}>
        {/* card header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: 'var(--tool)', borderBottom: '1px solid var(--border)' }}>
          <Ic.ExternalLink size={11} style={{ color: 'var(--accent)', flexShrink: 0 }} />
          <span style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--text-2)' }}>App {index + 1}</span>
          <input value={app.label} onChange={inp('label')} placeholder="Label (e.g. Admin Panel)"
            style={{ flex: 1, fontSize: 11.5, padding: '2px 7px', background: 'var(--editor)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--text)', outline: 'none', minWidth: 0 }} />
          {total > 1 && (
            <button onClick={() => onRemove(app.id)} title="Remove this app"
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-3)', display: 'flex', padding: 2 }}>
              <Ic.X size={13} />
            </button>
          )}
        </div>
        {/* card body */}
        <div style={{ padding: '10px 10px 8px', display: 'flex', flexDirection: 'column', gap: 7 }}>
          <div>
            <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginBottom: 3 }}>URL <span style={{ color: 'var(--fail)' }}>*</span></div>
            <input value={app.url} onChange={inp('url')} placeholder="https://your-app.com/" style={iStyle} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div>
              <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginBottom: 3 }}>Email / Username</div>
              <input value={app.email} onChange={inp('email')} placeholder="user@example.com" autoComplete="off" style={iStyle} />
            </div>
            <div>
              <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginBottom: 3 }}>Password</div>
              <div style={{ position: 'relative' }}>
                <input value={app.password} onChange={inp('password')} type={app.showPw ? 'text' : 'password'}
                  placeholder="••••••••" autoComplete="new-password"
                  style={{ ...iStyle, paddingRight: 28 }} />
                <button onClick={() => onChange(app.id, 'showPw', !app.showPw)}
                  style={{ position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)', background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-3)', display: 'flex', padding: 0 }}>
                  {app.showPw ? <Ic.EyeOff size={13} /> : <Ic.Eye size={13} />}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  function RunAutoModal({ onConfirm, onClose, projectId }) {
    const [apps, setApps] = useState(() => [mkApp()]);
    const [files, setFiles] = useState(null);
    const [contextFile, setContextFile] = useState('');
    const [focus, setFocus] = useState('');
    const [busy, setBusy] = useState(false);

    useEffect(() => {
      window.ats.getConfig().then((cfg) => {
        const envs = (cfg && cfg.environments) || {};
        const env = Object.keys(envs).find((k) => envs[k] && envs[k].is_default) || 'dev';
        const pl = (cfg && cfg.platforms) || {};
        const selUrls = pl.seller && pl.seller.urls;
        const selUrl = (selUrls && (selUrls[env] || selUrls.dev)) || '';
        const selUsers = pl.seller && pl.seller.users;
        const selUser = (selUsers && selUsers[0]) || {};
        const admUrl = (pl.admin && pl.admin.url) || (pl.admin && pl.admin.urls && (pl.admin.urls[env] || pl.admin.urls.dev)) || '';
        const admUsers = pl.admin && pl.admin.users;
        const admUser = (admUsers && admUsers[0]) || {};
        const initial = [];
        if (selUrl) initial.push(mkApp('Seller App', selUrl, selUser.email || '', selUser.password || ''));
        if (admUrl) initial.push(mkApp('Admin Panel', admUrl, admUser.email || '', admUser.password || ''));
        if (initial.length) setApps(initial);
        else setApps([mkApp()]);
      }).catch(() => {});
      window.ats.agentContextList({ projectId: projectId || '', subdir: '' })
        .then((r) => setFiles((r && r.files) || []))
        .catch(() => setFiles([]));
    }, [projectId]);

    const updateApp = (id, field, value) =>
      setApps(prev => prev.map(a => a.id === id ? { ...a, [field]: value } : a));
    const removeApp = (id) => setApps(prev => prev.filter(a => a.id !== id));
    const addApp = () => setApps(prev => [...prev, mkApp()]);

    const run = () => {
      const valid = apps.filter(a => a.url.trim());
      if (!valid.length) return;
      const appLines = valid.map((a, i) => {
        let line = `${i + 1}. ${a.label || ('App ' + (i + 1))}: ${a.url.trim()}`;
        if (a.email) line += ` — login: email="${a.email}"${a.password ? ` password="${a.password}"` : ''}`;
        return line;
      });
      let msg = valid.length === 1
        ? `Map the app at ${valid[0].url.trim()}${valid[0].email ? ` (login: email="${valid[0].email}" password="${valid[0].password}")` : ''}`
        : `Map these apps in order:\n${appLines.join('\n')}`;
      if (contextFile) {
        msg += `\n\nExtract all testable flows from @${contextFile}`;
        if (focus.trim()) msg += ` (focus: ${focus.trim()})`;
      } else {
        msg += '\n\nDiscover all screens and identify the main user flows.';
      }
      msg += '\n\nFor each flow: navigate to its entry URL, drive the complete flow step by step, add_checkpoint at each meaningful outcome, create_test_case with the suggested tc_id and flow_id, run_test_case — fix and re-run until it passes. Work through ALL flows autonomously without stopping for confirmation between them. End with a summary: N flows authored, M passed, K need attention.';
      setBusy(true);
      onConfirm(msg);
    };

    const hasUrl = apps.some(a => a.url.trim());
    const divStyle = { fontSize: 11, color: 'var(--text-2)', marginBottom: 4, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase' };

    return (
      <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 60, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
           onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        <div style={{ background: 'var(--bg)', border: '1px solid var(--accent)', borderRadius: 10, width: 500, maxWidth: '94vw', maxHeight: '88vh', display: 'flex', flexDirection: 'column', boxShadow: '0 16px 48px rgba(0,0,0,0.4)' }}>

          {/* header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '14px 16px 12px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
            <Ic.Zap size={15} style={{ color: 'var(--accent)' }} />
            <b style={{ fontSize: 13 }}>Run Autonomous</b>
            <span style={{ flex: 1 }} />
            <button className="rv-cta" onClick={onClose}><Ic.X size={13} /></button>
          </div>

          {/* scrollable body */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '14px 16px', minHeight: 0 }}>
            <p style={{ fontSize: 11.5, color: 'var(--text-3)', lineHeight: 1.55, margin: '0 0 16px' }}>
              The agent logs into each app, maps every screen, extracts flows from your spec, then authors and verifies a test for each — unsupervised.
            </p>

            {/* apps section */}
            <div style={divStyle}>Apps to test</div>
            {apps.map((app, i) => (
              <AppCard key={app.id} app={app} index={i} total={apps.length}
                onChange={updateApp} onRemove={removeApp} />
            ))}
            <button onClick={addApp}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--accent)', background: 'transparent', border: '1px dashed var(--accent)', borderRadius: 6, padding: '5px 12px', cursor: 'pointer', marginBottom: 16 }}>
              <Ic.Plus size={12} /> Add another app
            </button>

            {/* spec section */}
            <div style={divStyle}>Spec / PRD <span style={{ fontWeight: 400, textTransform: 'none', color: 'var(--text-3)', fontSize: 10.5 }}>(optional)</span></div>
            <div style={{ marginBottom: 10 }}>
              {files === null
                ? <div style={{ fontSize: 11, color: 'var(--text-3)' }}>Loading context files…</div>
                : files.length === 0
                  ? <div style={{ fontSize: 11, color: 'var(--text-3)', lineHeight: 1.55, padding: '6px 0' }}>
                      No context folder attached. Add one via <b>Context</b> in the agent header, or leave blank — the agent will explore the app freely.
                    </div>
                  : <select value={contextFile} onChange={(e) => setContextFile(e.target.value)}
                      style={{ width: '100%', fontSize: 12, padding: '6px 8px', background: 'var(--editor)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--text)' }}>
                      <option value="">— explore freely, no spec —</option>
                      {files.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
                    </select>}
            </div>
            {contextFile && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginBottom: 3 }}>Focus <span style={{ fontStyle: 'italic' }}>(optional — e.g. "cart flow" or "checkout")</span></div>
                <input value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="leave blank to extract all flows"
                  style={{ width: '100%', fontSize: 12, padding: '6px 8px', background: 'var(--editor)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--text)', outline: 'none', boxSizing: 'border-box' }} />
              </div>
            )}
          </div>

          {/* footer */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 16px', borderTop: '1px solid var(--border)', flexShrink: 0 }}>
            <button className="rv-cta" onClick={onClose}>Cancel</button>
            <button className="rv-cta primary" onClick={run} disabled={!hasUrl || busy}>
              <Ic.Zap size={12} /> {busy ? 'Starting…' : 'Run Autonomous'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  function AgentApp({ embedded, onUndock, onClose }) {
    const [projects, setProjects] = useState([]);
    const [projectId, setProjectId] = useState(null);
    const [sessions, setSessions] = useState([]);
    const [activeId, setActiveId] = useState(null);
    const [runtime, setRuntime] = useState({});
    const [provider, setProvider] = useState('mimo');
    const [headed, setHeaded] = useState(false);
    const [toolBudget, setToolBudget] = useState(30);
    const [toasts, setToasts] = useState([]);
    const idRef = useRef(0);
    const nextId = () => (idRef.current += 1);
    const toast = (msg) => { const id = Date.now() + Math.random(); setToasts((t) => [...t, { id, msg }]); setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2400); };
    const pidRef = useRef(projectId);
    useEffect(() => { pidRef.current = projectId; }, [projectId]);
    const runtimeRef = useRef(runtime);
    useEffect(() => { runtimeRef.current = runtime; }, [runtime]);

    const refreshSessions = useCallback(async (pid) => {
      try {
        const r = await window.ats.agentListSessions({ projectId: pid });
        const disk = (r && r.sessions) || [];
        const diskIds = new Set(disk.map((s) => s.id));
        setSessions((prev) => {
          const placeholders = prev.filter((s) => s._placeholder && !diskIds.has(s.id));
          return [...placeholders, ...disk];
        });
      } catch (e) { /* ignore */ }
    }, []);

    useEffect(() => {
      window.ats.listProjects().then((r) => {
        setProjects((r && r.projects) || []);
        if (r && r.active) setProjectId(r.active);
      }).catch(() => {});
    }, []);

    useEffect(() => { refreshSessions(projectId); }, [projectId, refreshSessions]);

    useEffect(() => {
      const off = window.ats.onAgentEvent((msg) => {
        const sid = msg && msg.sessionId;
        if (!sid) return;
        setRuntime((prev) => {
          const cur = prev[sid] || emptyRuntime();
          return { ...prev, [sid]: applyEvent(cur, msg, nextId) };
        });
        if (msg.event === 'ready' || msg.event === 'turn_complete' || msg.event === 'exited' || msg.event === 'reset_ok') {
          refreshSessions(pidRef.current);
        }
        // "Compose to start": once a freshly-created session is ready, fire its first queued message.
        if (msg.event === 'ready') {
          const rt0 = runtimeRef.current[sid];
          if (rt0 && rt0.pendingSend) {
            const ps = rt0.pendingSend;
            patchRt(sid, { pendingSend: null, status: 'busy' });
            window.ats.agentSend({ sessionId: sid, message: ps.text, attachments: (ps.atts || []).map((a) => a.path) }).then((res) => {
              if (res && res.status === 'error') patchRt(sid, (c) => ({ status: 'ready', messages: [...c.messages, { id: nextId(), role: 'error', text: res.message }] }));
            });
          }
        }
      });
      return () => { if (off) off(); };
    }, [refreshSessions]);

    useEffect(() => {
      if (!activeId || runtime[activeId]) return;
      const sess = sessions.find((s) => s.id === activeId);
      if (!sess) return;
      if (sess._placeholder || !(sess.message_count > 0)) { setRuntime((prev) => (prev[activeId] ? prev : { ...prev, [activeId]: emptyRuntime() })); return; }
      window.ats.agentSessionTranscript({ projectId, sessionId: activeId }).then((r) => {
        const msgs = ((r && r.bubbles) || []).map((b) => bubbleFromTranscript(b, nextId()));
        setRuntime((prev) => (prev[activeId] ? prev : { ...prev, [activeId]: { ...emptyRuntime(), messages: msgs } }));
      }).catch(() => { setRuntime((prev) => (prev[activeId] ? prev : { ...prev, [activeId]: emptyRuntime() })); });
    }, [activeId, sessions, projectId]);

    const patchRt = (sid, patch) => setRuntime((prev) => ({ ...prev, [sid]: { ...(prev[sid] || emptyRuntime()), ...(typeof patch === 'function' ? patch(prev[sid] || emptyRuntime()) : patch) } }));

    const onNew = async () => {
      const r = await window.ats.agentNewSession({ projectId, title: '' });
      if (!r || !r.session) return;
      const s = { ...r.session, _placeholder: true };
      setSessions((prev) => [s, ...prev]);
      setActiveId(s.id);
      setRuntime((prev) => ({ ...prev, [s.id]: emptyRuntime() }));
    };
    const onStart = async () => {
      const sid = activeId; if (!sid) return;
      const sess = sessions.find((s) => s.id === sid) || {};
      patchRt(sid, (cur) => ({ status: 'starting', messages: [...cur.messages, { id: nextId(), role: 'system', text: 'Starting session (launching browser)…' }] }));
      const res = await window.ats.agentStart({ sessionId: sid, projectId, provider, headed, toolBudget, title: sess.title || '' });
      if (res && res.status === 'error') patchRt(sid, (cur) => ({ status: 'idle', messages: [...cur.messages, { id: nextId(), role: 'error', text: res.message }] }));
    };
    // Compose-to-start: create a session, start it, and queue the first message (sent on 'ready').
    const startWithMessage = async (text, atts) => {
      const r = await window.ats.agentNewSession({ projectId, title: '' });
      if (!r || !r.session) { toast('Could not start a session'); return; }
      const sid = r.session.id;
      setSessions((prev) => [{ ...r.session, _placeholder: true }, ...prev]);
      setActiveId(sid);
      setRuntime((prev) => ({ ...prev, [sid]: { ...emptyRuntime(), status: 'starting', pendingSend: { text, atts: atts || [] },
        messages: [
          ...(text ? [{ id: nextId(), role: 'user', text, attachments: (atts || []).map((a) => a.name) }] : []),
          { id: nextId(), role: 'system', text: 'Starting session (launching browser)…' },
        ] } }));
      const res = await window.ats.agentStart({ sessionId: sid, projectId, provider, headed, toolBudget, title: '' });
      if (res && res.status === 'error') patchRt(sid, (cur) => ({ status: 'idle', pendingSend: null, messages: [...cur.messages, { id: nextId(), role: 'error', text: res.message }] }));
    };
    // Open the app under test (the agent's current page, else the seller URL from config) in the system browser.
    const openBrowser = async () => {
      const sid = activeId;
      const rt = sid ? runtime[sid] : null;
      const sessionRunning = rt && (rt.status === 'ready' || rt.status === 'busy');
      // If a session is active, show the agent's OWN browser (switch to headed mode).
      // If no session is running, fall back to opening the app URL in the system browser.
      if (sessionRunning && sid) {
        const res = await window.ats.agentShowBrowser({ sessionId: sid });
        if (res && res.status === 'error') toast(res.message || 'Could not show browser');
        else toast('Browser window opened — switching to headed mode');
        return;
      }
      let url = rt && rt.info && rt.info.url;
      if (!url) {
        try {
          const cfg = await window.ats.getConfig();
          const envs = (cfg && cfg.environments) || {};
          const env = Object.keys(envs).find((k) => envs[k] && envs[k].is_default) || 'dev';
          const su = cfg && cfg.platforms && cfg.platforms.seller && cfg.platforms.seller.urls;
          url = su && (su[env] || su.dev);
        } catch (e) { /* ignore */ }
      }
      if (url && window.ats.openExternal) window.ats.openExternal(url);
      else toast('No app URL configured');
    };
    const onSend = async () => {
      const sid = activeId; if (!sid) return;
      const cur = runtime[sid]; if (!cur) return;
      const text = (cur.input || '').trim();
      const atts = cur.attachments || [];
      if ((!text && !atts.length) || (cur.status !== 'ready' && cur.status !== 'awaiting_input')) return;
      patchRt(sid, (c) => ({ messages: [...c.messages, { id: nextId(), role: 'user', text, attachments: atts.map((a) => a.name) }], input: '', attachments: [], status: 'busy' }));
      const res = await window.ats.agentSend({ sessionId: sid, message: text, attachments: atts.map((a) => a.path) });
      if (res && res.status === 'error') patchRt(sid, (c) => ({ status: 'ready', messages: [...c.messages, { id: nextId(), role: 'error', text: res.message }] }));
    };
    const onStop = async () => { const sid = activeId; if (!sid) return; await window.ats.agentStop({ sessionId: sid }); patchRt(sid, { status: 'idle', info: null }); refreshSessions(projectId); };
    const onReset = async () => { const sid = activeId; if (!sid) return; await window.ats.agentReset({ sessionId: sid }); patchRt(sid, { messages: [] }); toast('Conversation cleared'); };
    const onRename = async (sid, title) => { setSessions((prev) => prev.map((s) => (s.id === sid ? { ...s, title } : s))); await window.ats.agentRenameSession({ projectId, sessionId: sid, title }); };
    const onDelete = async (sid) => {
      await window.ats.agentDeleteSession({ projectId, sessionId: sid });
      setSessions((prev) => prev.filter((s) => s.id !== sid));
      setRuntime((prev) => { const n = { ...prev }; delete n[sid]; return n; });
      if (activeId === sid) setActiveId(null);
      toast('Session deleted');
    };

    const activeSession = sessions.find((s) => s.id === activeId) || null;
    const activeRt = activeId ? (runtime[activeId] || emptyRuntime()) : null;
    const renderChat = (onBack) => (
      <SessionChat session={activeSession} rt={activeRt}
        provider={provider} setProvider={setProvider} headed={headed} setHeaded={setHeaded}
        toolBudget={toolBudget} setToolBudget={setToolBudget}
        onStart={onStart} onSend={onSend} onStop={onStop} onReset={onReset} onOpenBrowser={openBrowser}
        setInput={(v) => patchRt(activeId, { input: v })}
        setAttachments={(v) => patchRt(activeId, { attachments: v })}
        projectId={projectId} toast={toast} onBack={onBack} />
    );
    const initiator = <StartComposer onStart={startWithMessage} headed={headed} setHeaded={setHeaded} toolBudget={toolBudget} setToolBudget={setToolBudget} onOpenBrowser={openBrowser} projectId={projectId} />;
    const noSessionPane = (
      <div className="agent-pane no-ses">
        <div className="no-ses-msg">
          <Ic.MessageSquare size={28} style={{ color: 'var(--accent)', marginBottom: 10 }} />
          <h2>Start the agent</h2>
          <p>Type a message below to start a new session — or pick one from the list. Each session drives its own browser and keeps its own memory.</p>
        </div>
        {initiator}
      </div>
    );
    const list = (
      <SessionSidebar projects={projects} projectId={projectId} setProjectId={setProjectId}
        sessions={sessions} activeId={activeId} runtimeMap={runtime}
        onSelect={setActiveId} onNew={onNew} onRename={onRename} onDelete={onDelete}
        full={embedded} onUndock={embedded ? onUndock : undefined} onClose={embedded ? onClose : undefined} />
    );

    return (
      <div className={'agent-shell' + (embedded ? ' embedded' : '')}>
        {embedded
          /* Docked = 2 pages: page 1 is the session list + a chat initiator at the bottom, page 2 is the opened session (with Back). */
          ? (activeSession ? renderChat(() => setActiveId(null)) : <React.Fragment>{list}{initiator}</React.Fragment>)
          /* Window = list sidebar + chat (or the start pane + initiator when nothing is selected). */
          : (<React.Fragment>{list}{activeSession ? renderChat(null) : noSessionPane}</React.Fragment>)}
        <div style={{ position: 'fixed', bottom: 16, right: 16, display: 'flex', flexDirection: 'column', gap: 6, zIndex: 80 }}>
          {toasts.map((t) => (
            <div key={t.id} style={{ background: 'var(--bg-2, var(--bg))', border: '1px solid var(--accent)', borderRadius: 8, padding: '7px 12px', fontSize: 12, boxShadow: '0 6px 18px rgba(0,0,0,0.2)' }}>{t.msg}</div>
          ))}
        </div>
      </div>
    );
  }

  window.AgentApp = AgentApp;
})();
