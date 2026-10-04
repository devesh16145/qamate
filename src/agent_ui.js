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
  function emptyRuntime() { return { messages: [], status: 'idle', info: null, attachments: [], lastLog: '', input: '', usage: null, mode: 'auto', plan: [] }; }
  function finalizeStreaming(prev) {
    const last = prev[prev.length - 1];
    if (last && (last.role === 'assistant' || last.role === 'thinking') && last.streaming) return [...prev.slice(0, -1), { ...last, streaming: false }];
    return prev;
  }
  function bubbleFromTranscript(b, id) {
    return { id, role: b.role, text: b.text || '', tool: b.tool, args: b.args, attachments: b.attachments };
  }
  const fmtTok = (n) => { n = n || 0; if (n < 1000) return '' + n; if (n < 1e6) return (n / 1000).toFixed(n < 10000 ? 1 : 0) + 'k'; return (n / 1e6).toFixed(2) + 'M'; };

  /* ── Markdown rendering ──────────────────────────────────────────────────── */
  function ALink({ href, children }) {
    return (
      <a href="#" title={href}
        onClick={(e) => { e.preventDefault(); if (window.ats && window.ats.openExternal) window.ats.openExternal(href); }}
        style={{ color: 'var(--accent)', textDecoration: 'underline', textUnderlineOffset: 2, cursor: 'pointer', wordBreak: 'break-all' }}>
        {children}
      </a>
    );
  }

  function Spans({ text }) {
    const re = /\[([^\]]+)\]\((https?:[^)\s]+)\)|(https?:\/\/[^\s<>"')\]]+)|\*\*(.+?)\*\*|__(.+?)__|`([^`\n]+)`|\*(.+?)\*|_(.+?)_|~~(.+?)~~/gs;
    const nodes = []; let last = 0, m, k = 0;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) nodes.push(text.slice(last, m.index));
      if (m[1] != null) nodes.push(<ALink key={k++} href={m[2]}>{m[1]}</ALink>);
      else if (m[3] != null) nodes.push(<ALink key={k++} href={m[3]}>{m[3]}</ALink>);
      else if (m[4] != null) nodes.push(<strong key={k++}>{m[4]}</strong>);
      else if (m[5] != null) nodes.push(<strong key={k++}>{m[5]}</strong>);
      else if (m[6] != null) nodes.push(<code key={k++} style={{ background: 'var(--accent-bg)', padding: '1px 5px', borderRadius: 4, fontSize: '0.87em', fontFamily: 'var(--mono)', color: 'var(--text)' }}>{m[6]}</code>);
      else if (m[7] != null) nodes.push(<em key={k++}>{m[7]}</em>);
      else if (m[8] != null) nodes.push(<em key={k++}>{m[8]}</em>);
      else if (m[9] != null) nodes.push(<s key={k++} style={{ opacity: 0.6 }}>{m[9]}</s>);
      last = m.index + m[0].length;
    }
    if (last < text.length) nodes.push(text.slice(last));
    return nodes.length ? nodes : [text];
  }

  /* Lightweight token highlighting for code cards (keys/strings/numbers/bools/comments). */
  function CodeTokens({ code }) {
    const re = /("(?:[^"\\]|\\.)*")(\s*:)?|('(?:[^'\\]|\\.)*')|\b(true|false|null|True|False|None)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|((?:\/\/|#)[^\n]*)/g;
    const nodes = []; let last = 0, m, k = 0;
    while ((m = re.exec(code)) !== null) {
      if (m.index > last) nodes.push(code.slice(last, m.index));
      if (m[1] != null && m[2] != null) { nodes.push(<span key={k++} style={{ color: 'var(--accent)' }}>{m[1]}</span>); nodes.push(m[2]); }
      else if (m[1] != null) nodes.push(<span key={k++} style={{ color: 'var(--pass, #16a34a)' }}>{m[1]}</span>);
      else if (m[3] != null) nodes.push(<span key={k++} style={{ color: 'var(--pass, #16a34a)' }}>{m[3]}</span>);
      else if (m[4] != null) nodes.push(<span key={k++} style={{ color: '#b07cd8' }}>{m[4]}</span>);
      else if (m[5] != null) nodes.push(<span key={k++} style={{ color: '#5b9dd9' }}>{m[5]}</span>);
      else nodes.push(<span key={k++} style={{ color: 'var(--text-3)', fontStyle: 'italic' }}>{m[6]}</span>);
      last = re.lastIndex;
    }
    if (last < code.length) nodes.push(code.slice(last));
    return nodes.length ? nodes : [code];
  }

  function CodeCard({ lang, code }) {
    return (
      <div style={{ border: '1px solid var(--border)', borderRadius: 10, margin: '10px 0', background: 'var(--bg)', overflow: 'hidden' }}>
        <div style={{ padding: '7px 13px 0', fontSize: 10, color: 'var(--text-3)', fontFamily: 'var(--mono)', userSelect: 'none' }}>{lang || 'code'}</div>
        <pre style={{ margin: 0, padding: '8px 13px 12px', fontSize: 11.5, fontFamily: 'var(--mono)', overflowX: 'auto', whiteSpace: 'pre', lineHeight: 1.6 }}>
          <code><CodeTokens code={code} /></code>
        </pre>
      </div>
    );
  }

  const _mdCell = { padding: '5px 10px', borderBottom: '1px solid var(--accent-bg)', textAlign: 'left', verticalAlign: 'top' };

  /* Block-level prose: headings, lists, quotes, MARKDOWN TABLES, and bare JSON
     blocks the model emits without code fences (rendered as code cards). */
  function ProseBlock({ s }) {
    const lines = s.split('\n');
    const out = []; let i = 0, k = 0;
    const isRow = (l) => /^\|.*\|\s*$/.test(l.trim());
    const splitRow = (l) => l.trim().replace(/^\||\|\s*$/g, '').split('|').map(c => c.trim());
    while (i < lines.length) {
      const line = lines[i];
      const trim = line.trimStart(), ind = line.length - trim.length;

      // ── Markdown table: header row + |---| separator + body rows ──
      if (isRow(line) && i + 1 < lines.length && /^\|[\s:\-|]+\|\s*$/.test(lines[i + 1].trim())) {
        const header = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && isRow(lines[i])) { rows.push(splitRow(lines[i])); i++; }
        out.push(
          <div key={k++} style={{ margin: '10px 0', border: '1px solid var(--border)', borderRadius: 10, overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12.5 }}>
              <thead><tr>{header.map((h, hi) => <th key={hi} style={{ ..._mdCell, fontWeight: 600, color: 'var(--text-2)', borderBottom: '1px solid var(--border)', background: 'var(--bg-2, var(--bg))', whiteSpace: 'nowrap' }}><Spans text={h} /></th>)}</tr></thead>
              <tbody>{rows.map((r, ri) => (
                <tr key={ri}>{header.map((_, ci) => <td key={ci} style={_mdCell}><Spans text={r[ci] || ''} /></td>)}</tr>
              ))}</tbody>
            </table>
          </div>
        );
        continue;
      }

      // ── Bare JSON block (no code fence): consecutive lines that parse as JSON ──
      if (/^[{\[]/.test(trim)) {
        let j = i;
        while (j < lines.length && lines[j].trim()) j++;
        const chunk = lines.slice(i, j).join('\n').trim();
        let parsed = null;
        if (chunk.length > 2) { try { parsed = JSON.parse(chunk); } catch (e) { parsed = null; } }
        if (parsed !== null && typeof parsed === 'object') {
          out.push(<CodeCard key={k++} lang="json" code={JSON.stringify(parsed, null, 2)} />);
          i = j;
          continue;
        }
      }

      if (!trim) out.push(<div key={k++} style={{ height: 6 }} />);
      else if (trim.startsWith('### ')) out.push(<div key={k++} style={{ fontWeight: 600, fontSize: 12.5, marginTop: 10, marginBottom: 2, color: 'var(--text)' }}><Spans text={trim.slice(4)} /></div>);
      else if (trim.startsWith('## '))  out.push(<div key={k++} style={{ fontWeight: 700, fontSize: 13.5, marginTop: 12, marginBottom: 4, color: 'var(--text)' }}><Spans text={trim.slice(3)} /></div>);
      else if (trim.startsWith('# '))   out.push(<div key={k++} style={{ fontWeight: 700, fontSize: 15,   marginTop: 14, marginBottom: 5, color: 'var(--text)' }}><Spans text={trim.slice(2)} /></div>);
      else if (/^[-*] /.test(trim)) out.push(
        <div key={k++} style={{ display: 'flex', gap: 8, paddingLeft: 4 + ind * 4, marginTop: 2 }}>
          <span style={{ color: 'var(--accent)', fontSize: 8, marginTop: 5, flexShrink: 0 }}>◆</span>
          <span><Spans text={trim.slice(2)} /></span>
        </div>);
      else if (/^\d+\. /.test(trim)) out.push(
        <div key={k++} style={{ display: 'flex', gap: 8, paddingLeft: 4 + ind * 4, marginTop: 2 }}>
          <span style={{ color: 'var(--accent)', flexShrink: 0, fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{trim.match(/^(\d+)\. /)[1]}.</span>
          <span><Spans text={trim.replace(/^\d+\. /, '')} /></span>
        </div>);
      else if (/^---+$/.test(trim)) out.push(<hr key={k++} style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '8px 0' }} />);
      else if (trim.startsWith('> ')) out.push(
        <div key={k++} style={{ borderLeft: '3px solid var(--accent)', paddingLeft: 10, margin: '4px 0', color: 'var(--text-2)', fontStyle: 'italic' }}><Spans text={trim.slice(2)} /></div>);
      else out.push(<div key={k++}><Spans text={line} /></div>);
      i++;
    }
    return <React.Fragment>{out}</React.Fragment>;
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
      <div style={{ lineHeight: 1.7, wordBreak: 'break-word' }}>
        {segs.map((seg, si) => seg.t === 'code'
          ? <CodeCard key={si} lang={seg.lang} code={seg.s} />
          : <ProseBlock key={si} s={seg.s} />)}
      </div>
    );
  }

  /* ── Thinking block: the model's reasoning, collapsible (Cursor-style) ─────── */
  function ThinkingBlock({ m }) {
    const [open, setOpen] = useState(false);
    const live = !!m.streaming;
    const tail = (m.text || '').slice(-150).replace(/\s+/g, ' ').trim();
    return (
      <div style={{ alignSelf: 'stretch', minWidth: 0 }}>
        <button onClick={() => setOpen(o => !o)}
          style={{ display: 'flex', alignItems: 'center', gap: 6, maxWidth: '100%', fontSize: 11, color: 'var(--text-3)', background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 0', fontFamily: 'inherit' }}>
          <span style={{ flexShrink: 0 }}>{live ? 'Thinking' : 'Thought process'}</span>
          {live && <span className="live-dot" style={{ flexShrink: 0 }}></span>}
          {!open && tail && <span style={{ fontStyle: 'italic', opacity: 0.7, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>· {tail}</span>}
          <span style={{ flexShrink: 0, transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s', opacity: 0.7 }}>›</span>
        </button>
        {open && (
          <div style={{ marginTop: 3, borderLeft: '2px solid var(--border)', paddingLeft: 10, fontSize: 11.5, lineHeight: 1.55, color: 'var(--text-3)', fontStyle: 'italic', whiteSpace: 'pre-wrap', maxHeight: 280, overflowY: 'auto' }}>{m.text}</div>
        )}
      </div>
    );
  }

  /* ── Plan checklist: the agent's live to-do list (set_plan/update_plan) ────── */
  const PLAN_GLYPH = {
    pending: ['○', 'var(--text-3)'], active: ['▶', 'var(--accent)'],
    done: ['✓', 'var(--pass, #16a34a)'], failed: ['✕', 'var(--fail, #dc2626)'],
    skipped: ['⊘', 'var(--text-3)'],
  };
  function PlanCard({ plan }) {
    const [open, setOpen] = useState(false);   // minimized by default — never covers the chat
    if (!plan || !plan.length) return null;
    const done = plan.filter(p => p.status === 'done').length;
    const active = plan.find(p => p.status === 'active');

    if (!open) return (
      <div style={{ position: 'sticky', top: 0, zIndex: 5, alignSelf: 'flex-end' }}>
        <button onClick={() => setOpen(true)}
          title={active ? `Now: ${active.step} — click to expand the plan` : 'Click to expand the plan'}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, fontFamily: 'inherit', color: 'var(--text-2)', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 20, padding: '3px 11px', cursor: 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,0.10)' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: done === plan.length ? 'var(--pass, #16a34a)' : 'var(--accent)' }}></span>
          <b>Plan</b>
          <span style={{ fontFamily: 'var(--mono)' }}>{done}/{plan.length}</span>
        </button>
      </div>
    );

    return (
      <div style={{ position: 'sticky', top: 0, zIndex: 5, background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 10, padding: '7px 12px', boxShadow: '0 2px 10px rgba(0,0,0,0.12)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11, minWidth: 0 }}>
          <b style={{ color: 'var(--text-2)', flexShrink: 0 }}>Plan</b>
          <span style={{ color: 'var(--text-3)', fontFamily: 'var(--mono)', flexShrink: 0 }}>{done}/{plan.length}</span>
          <span style={{ flex: 1, height: 3, background: 'var(--accent-bg)', borderRadius: 2, overflow: 'hidden', minWidth: 30 }}>
            <span style={{ display: 'block', height: '100%', width: `${Math.round(100 * done / plan.length)}%`, background: 'var(--pass, #16a34a)', transition: 'width 0.3s' }}></span>
          </span>
          <button onClick={() => setOpen(false)} title="Minimize — shows just the progress count"
            style={{ display: 'inline-flex', alignItems: 'center', border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--text-3)', padding: '0 2px', fontSize: 14, lineHeight: 1, fontFamily: 'inherit' }}>—</button>
        </div>
        <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 3, maxHeight: 200, overflowY: 'auto' }}>
          {plan.map((p, i) => {
            const [glyph, color] = PLAN_GLYPH[p.status] || PLAN_GLYPH.pending;
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 7, fontSize: 11.5, color: p.status === 'done' ? 'var(--text-3)' : 'var(--text)', textDecoration: p.status === 'skipped' ? 'line-through' : 'none' }}>
                <span style={{ color, fontWeight: 700, width: 13, textAlign: 'center', flexShrink: 0, fontFamily: 'var(--mono)' }}>{glyph}</span>
                <span style={{ fontWeight: p.status === 'active' ? 600 : 400, minWidth: 0 }}>{p.step}</span>
                {p.note && <span style={{ color: 'var(--text-3)', fontSize: 10.5, fontStyle: 'italic' }}>— {p.note}</span>}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  /* ── JSON syntax highlighting + table rendering for tool cards ──────────────── */
  function JsonView({ value, maxHeight = 180 }) {
    let obj = value;
    if (typeof value === 'string') { try { obj = JSON.parse(value); } catch (e) { obj = null; } }
    if (obj === null || typeof obj !== 'object') return null;
    const text = JSON.stringify(obj, null, 2);
    if (!text || text.length > 20000) return null;
    const re = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
    const nodes = []; let last = 0, m, k = 0;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) nodes.push(text.slice(last, m.index));
      if (m[1] != null && m[2] != null) { nodes.push(<span key={k++} style={{ color: 'var(--accent)' }}>{m[1]}</span>); nodes.push(m[2]); }
      else if (m[1] != null) nodes.push(<span key={k++} style={{ color: 'var(--pass, #16a34a)' }}>{m[1]}</span>);
      else if (m[3] != null) nodes.push(<span key={k++} style={{ color: '#b07cd8' }}>{m[3]}</span>);
      else nodes.push(<span key={k++} style={{ color: '#5b9dd9' }}>{m[4]}</span>);
      last = re.lastIndex;
    }
    if (last < text.length) nodes.push(text.slice(last));
    return (
      <pre style={{ margin: '3px 0 0', padding: '6px 8px', background: 'var(--editor, var(--bg-2, var(--bg)))', border: '1px solid var(--accent-bg)', borderRadius: 6, fontSize: 10.5, fontFamily: 'var(--mono)', lineHeight: 1.5, maxHeight, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{nodes}</pre>
    );
  }

  /* Render an array of flat objects as a table (element lists, run results). */
  function MiniTable({ rows }) {
    if (!Array.isArray(rows) || rows.length < 2 || rows.length > 40) return null;
    if (!rows.every(r => r && typeof r === 'object' && !Array.isArray(r))) return null;
    const cols = [...new Set(rows.flatMap(r => Object.keys(r)))].slice(0, 7);
    if (cols.length < 2) return null;
    const cell = (v) => v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
    return (
      <div style={{ marginTop: 3, maxHeight: 200, overflow: 'auto', border: '1px solid var(--accent-bg)', borderRadius: 6 }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 10.5, fontFamily: 'var(--mono)' }}>
          <thead><tr>{cols.map(c => <th key={c} style={{ position: 'sticky', top: 0, background: 'var(--bg-2, var(--bg))', textAlign: 'left', padding: '3px 8px', borderBottom: '1px solid var(--border)', color: 'var(--text-2)', fontWeight: 600 }}>{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{cols.map(c => <td key={c} style={{ padding: '2px 8px', borderBottom: '1px solid var(--accent-bg)', color: 'var(--text)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={cell(r[c])}>{cell(r[c])}</td>)}</tr>
            ))}
          </tbody>
        </table>
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
    const preview = names.slice(0, 5).join(', ') + (names.length > 5 ? ` +${names.length - 5}` : '');
    return (
      <div style={{ alignSelf: 'stretch', margin: '1px 0', minWidth: 0 }}>
        <button onClick={() => setOpen(o => !o)}
          style={{ display: 'flex', alignItems: 'center', gap: 6, maxWidth: '100%', fontSize: 11, color: 'var(--text-3)', background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 0', fontFamily: 'inherit' }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
            {names.length === 1 && names[0] === 'step'
              ? `Ran ${pairs.length} step${pairs.length !== 1 ? 's' : ''}${pairs.some(p => p.result && /"ok": false/.test(p.result.text || '')) ? ' — one could not run' : ''}`
              : `Worked with ${pairs.length} tool call${pairs.length !== 1 ? 's' : ''} — ${preview}`}
          </span>
          <span style={{ flexShrink: 0, transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s', opacity: 0.7 }}>›</span>
        </button>
        {open && (
          <div style={{ marginTop: 4, border: '1px solid var(--border)', borderRadius: 10, padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 6, background: 'var(--bg)' }}>
            {pairs.map(({ call, result }, pi) => <ToolPair key={pi} call={call} result={result} />)}
          </div>
        )}
      </div>
    );
  }

  /* One tool call+result; click to expand structured detail (JSON / table). */
  function ToolPair({ call, result }) {
    const [detail, setDetail] = useState(false);
    let parsed = null;
    if (result && typeof result.text === 'string') {
      try { parsed = JSON.parse(result.text); } catch (e) { parsed = null; }
    }
    const tableRows = parsed && !Array.isArray(parsed)
      ? Object.values(parsed).find(v => Array.isArray(v) && v.length > 1 && v.every(x => x && typeof x === 'object' && !Array.isArray(x)))
      : (Array.isArray(parsed) ? parsed : null);
    return (
      <div>
        <div onClick={() => setDetail(d => !d)} title="Click for full arguments / result"
          style={{ display: 'flex', alignItems: 'baseline', gap: 6, fontSize: 11, fontFamily: 'var(--mono)', cursor: 'pointer' }}>
          <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{call.tool}</span>
          <span style={{ color: 'var(--text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 320 }}>{argStr(call.args).slice(0, 100)}</span>
        </div>
        {!detail && result && <div style={{ fontSize: 10.5, fontFamily: 'var(--mono)', color: 'var(--text-3)', paddingLeft: 2, marginTop: 1 }}>↳ {String(result.text).slice(0, 140)}</div>}
        {detail && (
          <div style={{ paddingLeft: 2 }}>
            {call.args && Object.keys(call.args || {}).length > 0 && <JsonView value={call.args} maxHeight={140} />}
            {tableRows ? <MiniTable rows={tableRows} /> : (parsed ? <JsonView value={parsed} /> : (
              result && <div style={{ fontSize: 10.5, fontFamily: 'var(--mono)', color: 'var(--text-3)', marginTop: 2, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>↳ {String(result.text)}</div>
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
      return set({ status: 'ready', info, messages: msgs, usage: msg.tokens || rt.usage,
                   mode: msg.mode || rt.mode || 'auto',
                   plan: Array.isArray(msg.plan) && msg.plan.length ? msg.plan : (rt.plan || []) });
    }
    if (ev === 'mode_changed') return set({
      mode: msg.mode,
      messages: [...messages, { id: nextId(), role: 'system',
        text: msg.mode === 'guided'
          ? 'GUIDED mode — the agent will pause for your input on any value you did not provide, and on skips.'
          : 'AUTO mode — the agent proceeds freely; values it makes up are reported as assumptions.' }],
    });
    if (ev === 'usage') return set({ usage: { input: msg.input || 0, output: msg.output || 0, total: msg.total || 0, estimated: !!msg.estimated, usage_incomplete: !!msg.usage_incomplete } });
    if (ev === 'decision_browser_contract' || ev === 'decision_workflow_contract') return set({ browsing: {
      total: (msg.contract?.milestones || []).length, completed: 0, steps: 0,
      scope: ev === 'decision_workflow_contract' ? 'multi_app' : 'single_app', captures: 0,
      state: 'Browsing', verification: 'Independent replay pending', decisionTokens: 0, usageMissing: false,
    } });
    if (ev === 'model_usage' && msg.role === 'decision' && rt.browsing) return set({ browsing: {
      ...rt.browsing, decisionTokens: rt.browsing.decisionTokens + (msg.input || 0) + (msg.output || 0),
      usageMissing: rt.browsing.usageMissing || msg.input == null || msg.output == null,
    } });
    if (ev === 'decision_browser_step') return set({ browsing: {
      ...rt.browsing, completed: msg.milestone, steps: msg.step,
      app: msg.app || rt.browsing?.app, actor: msg.actor || rt.browsing?.actor,
      captures: (rt.browsing?.captures || 0) + (msg.kind === 'capture' && msg.ok ? 1 : 0),
      state: msg.ok ? 'Browsing' : 'Action failed', action: msg.kind,
    } });
    if (ev === 'decision_browser_confirmation' || ev === 'decision_workflow_confirmation') return set({ browsing: {
      ...rt.browsing, state: msg.status === 'requested' ? 'Rechecking uncertain decision' : 'Decision confirmed',
    } });
    if (ev === 'decision_browser_recovery') return set({ browsing: {
      ...rt.browsing, state: `Refreshing stale target (${msg.attempt}/${msg.limit})`,
    } });
    if (ev === 'decision_browser_complete') return set({ browsing: {
      ...rt.browsing, completed: msg.result?.milestones_completed || 0,
      state: msg.result?.ok ? 'Live outcomes observed' : `Stopped: ${msg.result?.status || 'unknown'}`,
      blocked: !msg.result?.ok, verification: 'Independent replay pending',
    } });
    if (ev === 'text') {
      const delta = msg.delta || '';
      const last = messages[messages.length - 1];
      if (last && last.role === 'assistant' && last.streaming)
        return set({ messages: [...messages.slice(0, -1), { ...last, text: (last.text || '') + delta }] });
      return set({ messages: [...finalizeStreaming(messages), { id: nextId(), role: 'assistant', text: delta, streaming: true }] });
    }
    if (ev === 'thinking') {
      const delta = msg.delta || '';
      if (!delta) return set({ status: 'busy' });
      const last = messages[messages.length - 1];
      if (last && last.role === 'thinking' && last.streaming)
        return set({ status: 'busy', messages: [...messages.slice(0, -1), { ...last, text: (last.text || '') + delta }] });
      return set({ status: 'busy', messages: [...finalizeStreaming(messages), { id: nextId(), role: 'thinking', text: delta, streaming: true }] });
    }
    if (ev === 'plan') return set({ plan: Array.isArray(msg.steps) ? msg.steps : [] });
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

  /* ── Shared chrome: status, menus, header and the one composer used everywhere ── */
  const STATUS_LABEL = {
    idle: 'Paused', starting: 'Starting…', ready: 'Ready',
    busy: 'Working…', awaiting_input: 'Needs your answer', error: 'Error',
  };
  function StatusDot({ rt }) {
    const st = (rt && rt.status) || 'idle';
    const pulse = st === 'busy' || st === 'starting';
    return (
      <span className="ag-status" title={STATUS_LABEL[st] || st} style={{ color: statusColor(rt, false) }}>
        {pulse ? <span className="live-dot"></span> : <span className="ag-dot" style={{ background: statusColor(rt, false) }}></span>}
        {STATUS_LABEL[st] || st}
      </span>
    );
  }

  /* Close a floating panel on outside click / Escape. */
  function useDismiss(open, setOpen) {
    const ref = useRef(null);
    useEffect(() => {
      if (!open) return;
      const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
      const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
      document.addEventListener('mousedown', onDown);
      document.addEventListener('keydown', onKey);
      return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
    }, [open]);
    return ref;
  }
  /* Button + floating panel. `up` opens above the trigger (composer), else below (header). */
  function Pop({ button, children, up, align, width, className }) {
    const [open, setOpen] = useState(false);
    const ref = useDismiss(open, setOpen);
    return (
      <div className={'ag-pop ' + (className || '')} ref={ref}>
        {button({ open, toggle: () => setOpen((o) => !o) })}
        {open && (
          <div className={'ag-pop-panel' + (up ? ' up' : '') + (align === 'right' ? ' right' : '')} style={width ? { width } : undefined}>
            {typeof children === 'function' ? children(() => setOpen(false)) : children}
          </div>
        )}
      </div>
    );
  }
  function MenuItem({ icon, label, hint, onClick, danger, disabled, checked }) {
    const Icon = icon && Ic[icon];
    return (
      <button className={'ag-menu-item' + (danger ? ' danger' : '')} onClick={onClick} disabled={disabled}>
        <span className="ag-menu-ic">{checked ? <Ic.Check size={13} /> : (Icon ? <Icon size={13} /> : null)}</span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span className="ag-menu-label">{label}</span>
          {hint && <span className="ag-menu-hint">{hint}</span>}
        </span>
      </button>
    );
  }

  /* Model picker pill. Locked while a session runs (the model is fixed at start). */
  function ModelMenu({ provider, setProvider, providerList, secretKeys, llmConfig, lockedModel }) {
    const LP = window.LlmProviders;
    const list = (providerList || []).filter((p) => p.canPlan !== false);
    if (lockedModel) {
      return <span className="ag-pill static" title="The model is fixed while this session runs. Start a new chat to switch."><Ic.Sparkle size={12} />{lockedModel}</span>;
    }
    if (llmConfig && !list.length) {
      return <span className="ag-pill warn" title="Open Preferences → AI models in the main window, add a provider and save."><Ic.AlertCircle size={12} />No model — add one in Preferences</span>;
    }
    const cur = list.find((p) => p.id === provider);
    const configured = !LP || !llmConfig || LP.isConfigured(provider, llmConfig, secretKeys || {});
    return (
      <Pop up width={300} className="shrink" button={({ toggle }) => (
        <button className={'ag-pill' + (configured ? '' : ' warn')} onClick={toggle} title={configured ? 'Choose the model for new sessions' : LP.missingKeyMessage(provider, llmConfig)}>
          {configured ? <Ic.Sparkle size={12} /> : <Ic.AlertCircle size={12} />}
          <span className="ag-pill-text">{cur ? (cur.model || cur.label) : 'Choose model'}</span><Ic.ChevronDown size={11} />
        </button>
      )}>
        {(close) => (
          <React.Fragment>
            <div className="ag-menu-head">Model</div>
            {list.map((p) => {
              const ok = !p.needsKey || !secretKeys || LP.isConfigured(p.id, llmConfig, secretKeys);
              return <MenuItem key={p.id} checked={p.id === provider} label={p.model || p.label} hint={p.label + (ok ? '' : ' · key needed')} onClick={() => { setProvider(p.id); close(); }} />;
            })}
            <div className="ag-menu-foot">Add or change models in Preferences → AI models.</div>
          </React.Fragment>
        )}
      </Pop>
    );
  }

  const MODES = {
    auto: { label: 'Auto', icon: 'Play', hint: 'Works without stopping. Values it had to invent are reported as assumptions for review.' },
    guided: { label: 'Guided', icon: 'Pause', hint: 'Pauses to ask you for any value you did not provide, and before skipping a step.' },
  };
  function ModeMenu({ mode, onChange, disabled }) {
    const m = MODES[mode] || MODES.auto;
    const Icon = Ic[m.icon];
    return (
      <Pop up width={280} button={({ toggle }) => (
        <button className={'ag-pill' + (mode === 'guided' ? ' guided' : '')} onClick={toggle} disabled={disabled} title={m.hint}>
          <Icon size={10} />{m.label}<Ic.ChevronDown size={11} />
        </button>
      )}>
        {(close) => (
          <React.Fragment>
            <div className="ag-menu-head">When the agent needs information</div>
            {Object.keys(MODES).map((k) => (
              <MenuItem key={k} checked={k === mode} label={MODES[k].label} hint={MODES[k].hint} onClick={() => { onChange(k); close(); }} />
            ))}
          </React.Fragment>
        )}
      </Pop>
    );
  }
  function OptionsMenu({ headed, setHeaded, toolBudget, setToolBudget, engine, setEngine }) {
    return (
      <Pop up width={260} button={({ toggle, open }) => (
        <button className={'ag-icon-btn' + (open ? ' on' : '')} onClick={toggle} aria-label="Session options" title="Session options"><Ic.Sliders size={14} /></button>
      )}>
        <div className="ag-menu-head">Session options</div>
        {setEngine && (
          <div className="ag-opt" style={{ cursor: 'default' }}>
            <Ic.Zap size={13} style={{ marginTop: 2, color: 'var(--text-3)' }} />
            <span style={{ flex: 1 }}>
              <span className="ag-menu-label">Engine</span>
              <span className="ag-menu-hint">{engine === 'classic'
                ? 'Classic: the original tool-calling agent.'
                : 'Fast: plans once, acts without a model on clear steps, replays every test before saving.'}</span>
              <select value={engine} onChange={(e) => setEngine(e.target.value)} style={{ marginTop: 5, fontSize: 11.5, padding: '3px 6px', width: '100%' }}>
                <option value="fast">Fast (new)</option><option value="classic">Classic</option>
              </select>
            </span>
          </div>
        )}
        <label className="ag-opt">
          <input type="checkbox" checked={headed} onChange={(e) => setHeaded(e.target.checked)} />
          <span><span className="ag-menu-label">Watch live</span><span className="ag-menu-hint">Show the browser window while the agent works.</span></span>
        </label>
        <div className="ag-opt">
          <Ic.Zap size={13} style={{ marginTop: 2, color: 'var(--text-3)' }} />
          <span style={{ flex: 1 }}>
            <span className="ag-menu-label">Check in after</span>
            <span className="ag-menu-hint">Steps per turn before the agent pauses and summarises.</span>
            <select value={toolBudget} onChange={(e) => setToolBudget(Number(e.target.value))} style={{ marginTop: 5, fontSize: 11.5, padding: '3px 6px', width: '100%' }}>
              <option value={30}>30 steps</option><option value={50}>50 steps</option>
              <option value={100}>100 steps</option><option value={0}>Never — run until done</option>
            </select>
          </span>
        </div>
        <div className="ag-menu-foot">Applies when a session starts or resumes.</div>
      </Pop>
    );
  }

  /* The one composer: attachments, auto-growing textarea, toolbar inside the box. */
  function Composer({ value, onChange, onKeyDown, taRef, onBlur, placeholder, attachments, onRemoveAttachment, onAttach,
                      canSend, onSend, busy, onStop, highlight, overlay, left, right, note }) {
    const autoGrow = (e) => { try { e.target.style.height = 'auto'; e.target.style.height = Math.min(180, e.target.scrollHeight) + 'px'; } catch (err) { /* ignore */ } };
    return (
      <div className="ag-composer-wrap">
        {note}
        <div className={'ag-composer' + (highlight ? ' highlight' : '')}>
          {overlay}
          {attachments && attachments.length > 0 && (
            <div className="ag-atts">
              {attachments.map((a) => (
                <span key={a.path || a.name} className="ag-att">
                  {isImg(a.ext) ? <Ic.File size={10} /> : <Ic.FileText size={10} />}
                  <span className="nm" title={a.name}>{a.name}</span>
                  <button onClick={() => onRemoveAttachment(a)} title="Remove"><Ic.X size={11} /></button>
                </span>
              ))}
            </div>
          )}
          <textarea ref={taRef} value={value} rows={1} placeholder={placeholder}
            onChange={(e) => { autoGrow(e); onChange(e); }} onKeyDown={onKeyDown} onBlur={onBlur} />
          <div className="ag-toolbar">
            <button className="ag-icon-btn" onClick={onAttach} aria-label="Attach documents or images (or drop files)" title="Attach documents or images (or drop files)"><Ic.Paperclip size={14} /></button>
            {left}
            <span style={{ flex: 1 }}></span>
            {right}
            {busy
              ? <button className="ag-send stop" onClick={onStop} aria-label="Stop the agent (you can resume the session later)" title="Stop the agent (you can resume the session later)"><Ic.Stop size={12} fill="currentColor" /></button>
              : <button className="ag-send" onClick={onSend} disabled={!canSend} aria-label="Send (Enter)" title="Send (Enter)"><Ic.ArrowUp size={15} stroke={2.2} /></button>}
          </div>
        </div>
      </div>
    );
  }

  /* Header: title + status on the left; new chat, history (docked), more, pop-out, close. */
  function AgentHeader({ title, rt, extra, menu, chrome, onRename }) {
    const [editing, setEditing] = useState(false);
    const [text, setText] = useState(title || '');
    return (
      <div className="ag-head">
        {editing && onRename ? (
          <input className="ag-title-edit" autoFocus value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { onRename(text); setEditing(false); } if (e.key === 'Escape') setEditing(false); }}
            onBlur={() => { onRename(text); setEditing(false); }} />
        ) : (
          <div className="ag-title" title={onRename ? 'Double-click to rename' : title} onDoubleClick={() => { if (onRename) { setText(title || ''); setEditing(true); } }}>{title}</div>
        )}
        {rt && <StatusDot rt={rt} />}
        {extra}
        <span style={{ flex: 1 }}></span>
        {chrome.onNew && <button className="ag-icon-btn" onClick={chrome.onNew} aria-label="New chat" title="New chat"><Ic.PenSquare size={15} /></button>}
        {chrome.history}
        {menu && (
          <Pop align="right" width={240} button={({ toggle, open }) => (
            <button className={'ag-icon-btn' + (open ? ' on' : '')} onClick={toggle} aria-label="More" title="More"><Ic.MoreHorizontal size={16} /></button>
          )}>{menu}</Pop>
        )}
        {chrome.onUndock && <button className="ag-icon-btn" onClick={chrome.onUndock} aria-label="Open in its own window" title="Open in its own window"><Ic.ExternalLink size={14} /></button>}
        {chrome.onClose && <button className="ag-icon-btn" onClick={chrome.onClose} aria-label="Close the agent panel" title="Close the agent panel"><Ic.X size={15} /></button>}
      </div>
    );
  }

  /* ── Session history: sidebar in window mode, popover from the header when docked ── */
  function SessionList({ projects, projectId, setProjectId, sessions, activeId, runtimeMap, onSelect, onNew, onRename, onDelete, compact }) {
    const [search, setSearch] = useState('');
    const [editing, setEditing] = useState(null);
    const [editText, setEditText] = useState('');
    const [confirmDel, setConfirmDel] = useState(null);
    const filtered = useMemo(() => {
      const q = search.trim().toLowerCase();
      return q ? sessions.filter((s) => (s.title || '').toLowerCase().includes(q)) : sessions;
    }, [sessions, search]);
    return (
      <div className={'ag-history' + (compact ? ' compact' : '')}>
        <div className="ag-history-top">
          <label className="ag-proj" title="Project — sessions, memory and context folder are per project">
            <Ic.Folder size={12} />
            <select value={projectId || ''} onChange={(e) => setProjectId(e.target.value || null)}>
              <option value="">No project</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
            </select>
            <Ic.ChevronDown size={11} />
          </label>
          {onNew && <button className="ag-new" onClick={onNew}><Ic.PenSquare size={13} /> New chat</button>}
          <div className="ag-search"><Ic.Search size={12} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search chats" /></div>
        </div>
        <div className="ag-history-list">
          {filtered.length === 0 && <div className="ag-empty-note">{search ? 'No chats match.' : 'No chats yet in this project.'}</div>}
          {filtered.map((s) => {
            const rt = runtimeMap[s.id];
            const live = !!s.live || (rt && (rt.status === 'busy' || rt.status === 'ready' || rt.status === 'starting'));
            return (
              <div key={s.id} className={'ag-hrow' + (s.id === activeId ? ' active' : '')} onClick={() => onSelect(s.id)}>
                <span className="ag-dot" style={{ background: statusColor(rt, live) }}></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  {editing === s.id ? (
                    <input autoFocus value={editText} onChange={(e) => setEditText(e.target.value)} onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => { if (e.key === 'Enter') { onRename(s.id, editText); setEditing(null); } if (e.key === 'Escape') setEditing(null); }}
                      onBlur={() => { onRename(s.id, editText); setEditing(null); }} className="ag-title-edit" />
                  ) : (
                    <div className="ag-hrow-title" title={s.title} onDoubleClick={(e) => { e.stopPropagation(); setEditing(s.id); setEditText(s.title || ''); }}>{s.title || 'Untitled'}</div>
                  )}
                  <div className="ag-hrow-meta">
                    {live ? 'running' : (s.message_count ? s.message_count + ' msgs' : 'empty')}{s.updated_at ? ' · ' + relTime(s.updated_at) : ''}{(s.tokens && s.tokens.total) ? ' · ' + fmtTok(s.tokens.total) + ' tok' : ''}
                  </div>
                </div>
                {confirmDel === s.id ? (
                  <span style={{ display: 'inline-flex', gap: 2 }} onClick={(e) => e.stopPropagation()}>
                    <button className="ag-row-btn" style={{ color: 'var(--fail)', opacity: 1 }} title="Confirm delete" onClick={() => { setConfirmDel(null); onDelete(s.id); }}><Ic.Check size={13} /></button>
                    <button className="ag-row-btn" style={{ opacity: 1 }} title="Cancel" onClick={() => setConfirmDel(null)}><Ic.X size={13} /></button>
                  </span>
                ) : (
                  <button className="ag-row-btn" title="Delete chat" onClick={(e) => { e.stopPropagation(); setConfirmDel(s.id); }}><Ic.Trash size={13} /></button>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  /* ── Welcome / empty state with starter prompts ──────────────────────────── */
  const STARTERS = [
    { icon: 'Globe', title: 'Explore the app', text: 'Explore the app and list its main user flows, with the pages each flow touches.' },
    { icon: 'ListChecks', title: 'Write a test', text: 'Log in and write a test for the login flow, including a wrong-password check.' },
    { icon: 'Search', title: 'Check a feature', text: 'Open the orders area and write a test that verifies the order list, filters and order details.' },
    { icon: 'Zap', title: 'Autonomous run', text: null, hint: 'Map the app and author a test for every flow, unsupervised.' },
  ];
  function Welcome({ projectName, onPick, onAuto }) {
    return (
      <div className="ag-welcome">
        <div className="ag-welcome-mark"><Ic.Sparkle size={22} /></div>
        <h2>What should we test?</h2>
        <p>The agent drives its own browser, records what it does and turns it into a replayable test{projectName ? <> for <b>{projectName}</b></> : null}.</p>
        <div className="ag-starters">
          {STARTERS.map((s) => {
            const Icon = Ic[s.icon];
            return (
              <button key={s.title} className="ag-starter" onClick={() => (s.text ? onPick(s.text) : onAuto())}>
                <Icon size={14} />
                <span><b>{s.title}</b><span>{s.text || s.hint}</span></span>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  /* ── Active session (ALWAYS rendered with a real session — no conditional hooks) ── */
  function SessionChat({ session, rt, provider, setProvider, providerList, secretKeys, llmConfig, headed, setHeaded, toolBudget, setToolBudget, engine, setEngine,
                         startMode, setStartMode, onStart, onStartAuto, onSend, onResumeWithMessage, onStop, onReset, onOpenBrowser,
                         onToggleMode, setInput, setAttachments, projectId, projectName, toast, chrome, onRename }) {
    const scrollRef = useRef(null);
    const taRef = useRef(null);
    const [confirmReset, setConfirmReset] = useState(false);
    const [autoOpen, setAutoOpen] = useState(false);
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
    const openCtx = () => { setCtxOpen(true); loadCtx(ctxSubdir); };
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
    const removeAttachment = (a) => setAttachments(attachments.filter((x) => x.path !== a.path));
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
    // Sending while paused resumes the session and delivers the message once it is ready.
    const canSend = !!((rt.input || '').trim() || attachments.length) && (connected ? (status === 'ready' || awaitingInput) : status !== 'starting');
    const send = () => {
      if (!canSend) return;
      if (connected) onSend();
      else onResumeWithMessage((rt.input || '').trim(), attachments);
    };
    const onKey = (e) => {
      if (atOpen && atMatches.length) {
        if (e.key === 'ArrowDown') { e.preventDefault(); setAtIndex((i) => (i + 1) % atMatches.length); return; }
        if (e.key === 'ArrowUp') { e.preventDefault(); setAtIndex((i) => (i - 1 + atMatches.length) % atMatches.length); return; }
        if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); insertAtMatch(atMatches[atIndex] || atMatches[0]); return; }
        if (e.key === 'Escape') { e.preventDefault(); setAtOpen(false); return; }
      }
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    };

    /* Document-style chat: no avatars. User = right-aligned soft pill;
       assistant = plain text in the reading column (modern agent look). */
    const bubble = (m) => {
      if (m.role === 'user') return (
        <div key={m.id} style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <div className="ag-user-msg">
            {m.text}
            {m.attachments && m.attachments.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                {m.attachments.map((n, i) => (
                  <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 10.5, fontFamily: 'var(--mono)', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 6, padding: '1px 6px' }}><Ic.File size={10} /> {n}</span>
                ))}
              </div>
            )}
          </div>
        </div>
      );

      if (m.role === 'thinking') return <ThinkingBlock key={m.id} m={m} />;

      if (m.role === 'assistant') return (
        <div key={m.id} style={{ minWidth: 0, fontSize: 13.5, color: 'var(--text)' }}>
          <Markdown text={m.text} />
          {m.streaming && <span className="live-dot" style={{ marginLeft: 4 }}></span>}
        </div>
      );

      // tool/tool_result are batched by groupMessages; these are fallback singles
      if (m.role === 'tool') return (
        <div key={m.id} style={{ display: 'flex', alignItems: 'baseline', gap: 6, fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--text-3)' }}>
          <b style={{ color: 'var(--text-2)' }}>{m.tool}</b><span>{argStr(m.args).slice(0, 100)}</span>
        </div>
      );
      if (m.role === 'tool_result') return (
        <div key={m.id} style={{ fontSize: 10.5, fontFamily: 'var(--mono)', color: 'var(--text-3)', paddingLeft: 14 }}>↳ {String(m.text).slice(0, 120)}</div>
      );

      if (m.role === 'error') return (
        <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 8, alignSelf: 'center', fontSize: 11.5, color: 'var(--fail)', background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.25)', borderRadius: 10, padding: '7px 12px', maxWidth: '90%' }}>
          <Ic.AlertCircle size={14} style={{ flexShrink: 0 }} /> {m.text}
        </div>
      );

      if (m.role === 'input_request') return (
        <div key={m.id} style={{ minWidth: 0, padding: '10px 14px', background: 'rgba(245,158,11,0.07)', border: '1.5px solid #f59e0b', borderRadius: 12 }}>
          <div style={{ fontSize: 10.5, fontWeight: 700, color: '#f59e0b', marginBottom: 5, display: 'flex', alignItems: 'center', gap: 5 }}><Ic.Pause size={12} /> Needs your input</div>
          <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.6 }}>{m.text}</div>
          <div style={{ marginTop: 6, fontSize: 10.5, color: 'var(--text-3)' }}>Type your answer in the box below ↓</div>
        </div>
      );

      // system messages — centered pill
      return (
        <div key={m.id} style={{ display: 'flex', justifyContent: 'center' }}>
          <div style={{ fontSize: 10.5, color: 'var(--text-3)', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 20, padding: '3px 12px', maxWidth: '80%', textAlign: 'center', lineHeight: 1.5 }}>{m.text}</div>
        </div>
      );
    };

    const usageTitle = `Primary-model tokens this session${rt.usage?.usage_incomplete ? ' (usage missing for some requests; not a complete total)' : ((rt.usage && rt.usage.estimated) ? ' (legacy estimate)' : '')} — in ${(rt.usage && rt.usage.input) || 0}, out ${(rt.usage && rt.usage.output) || 0}, reported total ${(rt.usage && rt.usage.total) || 0}. Decision-model usage is recorded separately in the event log.`;
    const usageText = rt.usage?.usage_incomplete ? (rt.usage.total ? fmtTok(rt.usage.total) + '+?' : '?') : ((rt.usage?.estimated ? '~' : '') + fmtTok(rt.usage?.total || 0));

    const menu = (close) => (
      <React.Fragment>
        <MenuItem icon="Folder" label="Context folder" hint="Docs the agent can read and you can @-mention" onClick={() => { close(); openCtx(); }} />
        <MenuItem icon="FileText" label="Agent memory" hint="What the agent remembers about this project" onClick={() => { close(); window.ats.agentMemoryOpen({ projectId: ctxPid() }); }} />
        <MenuItem icon="Globe" label={connected ? "Show the agent's browser" : 'Open the app in your browser'} onClick={() => { close(); onOpenBrowser(); }} />
        <MenuItem icon="Zap" label="Autonomous run…" hint="Map the app and author tests for every flow" onClick={() => { close(); setAutoOpen(true); }} />
        <div className="ag-menu-sep"></div>
        {messages.length > 0 && (confirmReset
          ? <MenuItem danger icon="Trash" label="Click again to clear" hint="Removes this chat's conversation" onClick={() => { setConfirmReset(false); close(); onReset(); }} />
          : <MenuItem icon="RefreshCw" label="Clear conversation" disabled={busy} onClick={() => setConfirmReset(true)} />)}
        {connected && <MenuItem danger icon="Stop" label="Stop session" hint="Closes its browser; you can resume later" onClick={() => { close(); onStop(); }} />}
      </React.Fragment>
    );

    return (
      <div className="agent-pane">
        <div style={{ position: 'relative' }}>
          <AgentHeader title={session.title || 'New chat'} rt={rt} chrome={chrome} onRename={onRename}
            menu={(close) => menu(close)}
            extra={info && !info.auth && <span className="ag-badge warn" title="Not logged in — set app credentials in Project Settings or capture a login for this project">not logged in</span>} />
          {ctxOpen && (
            <div className="ag-ctx-panel">
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                <b style={{ fontSize: 11.5 }}>Context folder</b><span style={{ flex: 1 }}></span>
                <button className="rv-cta" onClick={useFolder} title="Point this project at an existing folder on disk"><Ic.FolderOpen size={11} /> Use folder…</button>
                <button className="rv-cta" onClick={addCtx} title="Copy individual files into the managed context folder"><Ic.Plus size={11} /> Add files</button>
                <button className="rv-cta" onClick={() => window.ats.agentContextOpen({ projectId: ctxPid() })} title="Open the folder in Finder / Explorer"><Ic.ExternalLink size={11} /></button>
                <button className="rv-cta" onClick={() => setCtxOpen(false)} title="Close"><Ic.X size={11} /></button>
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

        <div ref={scrollRef} className="ag-scroll"
          onDragOver={(e) => { e.preventDefault(); if (!dragOver) setDragOver(true); }}
          onDragLeave={(e) => { if (e.currentTarget === e.target) setDragOver(false); }}
          onDrop={onDrop}
          style={{ outline: dragOver ? '2px dashed var(--accent)' : 'none', outlineOffset: -6 }}>
          <div className="ag-column">
            <PlanCard plan={rt.plan || []} />
            {rt.browsing && <div role="status" style={{ border: '1px solid var(--border)', padding: 12, borderRadius: 8, fontSize: 12 }}>
              <strong>{rt.browsing.state}</strong>
              <div>{rt.browsing.completed || 0}/{rt.browsing.total || '?'} outcome milestones · {rt.browsing.steps || 0} decisions</div>
              {rt.browsing.scope === 'multi_app' && <div>App: {rt.browsing.app || 'Starting'} · Actor: {rt.browsing.actor || 'Pending'} · Captured records: {rt.browsing.captures || 0}</div>}
              <div>{rt.browsing.verification}. Live outcomes alone do not verify the exported test.</div>
              <div style={{ color: 'var(--text-2)' }}>Decision tokens: {fmtTok(rt.browsing.decisionTokens || 0)}{rt.browsing.usageMissing ? ' + unknown usage' : ''}</div>
            </div>}
            {messages.length === 0 && !connected && (
              <Welcome projectName={projectName} onPick={(t) => { setInput(t); setTimeout(() => taRef.current && taRef.current.focus(), 0); }} onAuto={() => setAutoOpen(true)} />
            )}
            {groupMessages(messages).map(g =>
              g.type === 'batch'
                ? <ToolBatch key={g.key} items={g.items} />
                : bubble(g.msg)
            )}
            {dragOver && <div style={{ alignSelf: 'center', margin: 'auto', color: 'var(--accent)', fontSize: 12, fontFamily: 'var(--mono)' }}>Drop files to attach</div>}
          </div>
        </div>

        {autoOpen && (
          <RunAutoModal projectId={projectId} onClose={() => setAutoOpen(false)}
            onConfirm={(msg) => { setAutoOpen(false); onStartAuto(msg, []); }} />
        )}
        <Composer taRef={taRef} value={rt.input || ''} onChange={onComposerChange} onKeyDown={onKey}
          onBlur={() => setTimeout(() => setAtOpen(false), 120)}
          placeholder={awaitingInput ? 'The agent is waiting — type your answer…'
            : busy ? 'The agent is working… you can type your next message'
            : connected ? 'Ask a follow-up…  (@ to reference a file)'
            : canResume ? 'Message to resume this chat…' : 'Describe what to test…'}
          attachments={attachments} onRemoveAttachment={removeAttachment} onAttach={addAttachments}
          canSend={canSend} onSend={send} busy={busy} onStop={onStop} highlight={awaitingInput}
          note={busy ? (
            <div className="ag-working"><span className="live-dot"></span><span className="ag-working-text">{rt.lastLog || 'Working…'}</span></div>
          ) : (!connected && canResume) ? (
            <div className="ag-working paused">Paused — your next message resumes this chat with its memory. <a href="#" onClick={(e) => { e.preventDefault(); onStart(); }}>Resume without a message</a></div>
          ) : null}
          overlay={atOpen && atMatches.length > 0 && (
            <div className="ag-at-list">
              <div className="ag-menu-head">Reference a file — ↑↓ then Enter</div>
              {atMatches.map((p, i) => (
                <div key={p} onMouseDown={(e) => { e.preventDefault(); insertAtMatch(p); }} onMouseEnter={() => setAtIndex(i)}
                  className={'ag-at-item' + (i === atIndex ? ' hi' : '')} title={p}>
                  <Ic.FileText size={11} /><span>{p}</span>
                </div>
              ))}
            </div>
          )}
          left={<React.Fragment>
            <ModelMenu provider={provider} setProvider={setProvider} providerList={providerList} secretKeys={secretKeys} llmConfig={llmConfig} lockedModel={connected && info ? info.model : null} />
            <ModeMenu mode={connected ? (rt.mode || 'auto') : startMode} disabled={connected && busy}
              onChange={(m) => { if (connected) { if (m !== (rt.mode || 'auto')) onToggleMode(); } else setStartMode(m); }} />
            {!connected && <OptionsMenu headed={headed} setHeaded={setHeaded} toolBudget={toolBudget} setToolBudget={setToolBudget} engine={engine} setEngine={setEngine} />}
          </React.Fragment>}
          right={connected && <span className="ag-tokens" title={usageTitle}><Ic.Activity size={10} />{usageText}</span>} />
      </div>
    );
  }

  /* ── New chat (no session yet): welcome + composer that creates the session on send ── */
  function NewChat({ onStart, provider, setProvider, providerList, secretKeys, llmConfig, headed, setHeaded, toolBudget, setToolBudget, engine, setEngine,
                     startMode, setStartMode, onOpenBrowser, projectId, projectName, chrome }) {
    const [text, setText] = useState('');
    const [atts, setAtts] = useState([]);
    const [autoOpen, setAutoOpen] = useState(false);
    const taRef = useRef(null);
    const canSend = !!(text.trim() || atts.length);
    const go = () => { if (!canSend) return; onStart(text.trim(), atts); setText(''); setAtts([]); };
    const addFiles = async () => { const r = await window.ats.agentPickFiles(); const files = (r && r.files) || []; if (files.length) setAtts((a) => [...a, ...files]); };
    const menu = (close) => (
      <React.Fragment>
        <MenuItem icon="Globe" label="Open the app in your browser" onClick={() => { close(); onOpenBrowser(); }} />
        <MenuItem icon="Zap" label="Autonomous run…" hint="Map the app and author tests for every flow" onClick={() => { close(); setAutoOpen(true); }} />
      </React.Fragment>
    );
    return (
      <div className="agent-pane">
        <AgentHeader title="New chat" chrome={{ ...chrome, onNew: null }} menu={menu} />
        <div className="ag-scroll">
          <div className="ag-column">
            <Welcome projectName={projectName} onPick={(t) => { setText(t); setTimeout(() => taRef.current && taRef.current.focus(), 0); }} onAuto={() => setAutoOpen(true)} />
          </div>
        </div>
        {autoOpen && (
          <RunAutoModal projectId={projectId} onClose={() => setAutoOpen(false)}
            onConfirm={(msg) => { setAutoOpen(false); onStart(msg, []); }} />
        )}
        <Composer taRef={taRef} value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); } }}
          placeholder='Describe what to test — e.g. "log in, open Orders and write a test for the order lifecycle"'
          attachments={atts} onRemoveAttachment={(a) => setAtts((x) => x.filter((y) => y !== a))} onAttach={addFiles}
          canSend={canSend} onSend={go}
          left={<React.Fragment>
            <ModelMenu provider={provider} setProvider={setProvider} providerList={providerList} secretKeys={secretKeys} llmConfig={llmConfig} />
            <ModeMenu mode={startMode} onChange={setStartMode} />
            <OptionsMenu headed={headed} setHeaded={setHeaded} toolBudget={toolBudget} setToolBudget={setToolBudget} engine={engine} setEngine={setEngine} />
          </React.Fragment>} />
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
    const [provider, setProvider] = useState('');
    const [llmConfig, setLlmConfig] = useState(null);
    const [secretKeys, setSecretKeys] = useState({});
    const providerList = useMemo(() => {
      const LP = window.LlmProviders;
      return LP && llmConfig ? LP.listFromConfig(llmConfig) : [];
    }, [llmConfig]);
    const [startMode, setStartMode] = useState('auto');   // initial GUIDED/AUTO for new sessions
    const [headed, setHeaded] = useState(false);
    const [toolBudget, setToolBudget] = useState(30);
    const [engine, setEngineState] = useState(() => { try { return localStorage.getItem('qamate.engine') === 'classic' ? 'classic' : 'fast'; } catch (e) { return 'fast'; } });
    const setEngine = (v) => { setEngineState(v); try { localStorage.setItem('qamate.engine', v); } catch (e) { /* ignore */ } };
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
      loadLlm(true);
      // Providers are added in the main window's Settings; pick changes up on focus.
      const onFocus = () => loadLlm(false);
      window.addEventListener('focus', onFocus);
      return () => window.removeEventListener('focus', onFocus);
    }, []);

    const loadLlm = useCallback((initial) => {
      Promise.all([window.ats.getProviderCatalog(), window.ats.getConfig(), window.ats.getSecretStatus()]).then(([catalog, cfg, st]) => {
        const LP = window.LlmProviders;
        if (LP) LP.setCatalog(catalog);
        const c = cfg || {};
        setLlmConfig(c);
        setSecretKeys((st && st.keys) || {});
        if (!LP) return;
        const ids = LP.listFromConfig(c).filter((p) => p.canPlan).map((p) => p.id);
        setProvider((cur) => (!initial && ids.includes(cur)) ? cur : LP.defaultFromConfig(c));
      }).catch(() => {});
    }, []);

    const ensureProviderKey = useCallback(async () => {
      const LP = window.LlmProviders;
      if (!LP || !llmConfig) return true;
      const st = await window.ats.getSecretStatus().catch(() => null);
      const keys = (st && st.keys) || secretKeys;
      setSecretKeys(keys);
      if (LP.isConfigured(provider, llmConfig, keys)) return true;
      toast(LP.missingKeyMessage(provider, llmConfig));
      return false;
    }, [provider, llmConfig, secretKeys]);

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

    const onStart = async () => {
      const sid = activeId; if (!sid) return;
      if (!(await ensureProviderKey())) return;
      const sess = sessions.find((s) => s.id === sid) || {};
      patchRt(sid, (cur) => ({ status: 'starting', messages: [...cur.messages, { id: nextId(), role: 'system', text: 'Starting session (launching browser)…' }] }));
      const res = await window.ats.agentStart({ sessionId: sid, projectId, provider, headed, toolBudget, agentMode: startMode, engine, title: sess.title || '' });
      if (res && res.status === 'error') patchRt(sid, (cur) => ({ status: 'idle', messages: [...cur.messages, { id: nextId(), role: 'error', text: res.message }] }));
    };
    // Compose-to-start: create a session, start it, and queue the first message (sent on 'ready').
    const startWithMessage = async (text, atts) => {
      if (!(await ensureProviderKey())) return;
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
      const res = await window.ats.agentStart({ sessionId: sid, projectId, provider, headed, toolBudget, agentMode: startMode, engine, title: '' });
      if (res && res.status === 'error') patchRt(sid, (cur) => ({ status: 'idle', pendingSend: null, messages: [...cur.messages, { id: nextId(), role: 'error', text: res.message }] }));
    };
    // Paused/never-started session: resume it and deliver the message once it is ready.
    const resumeWithMessage = async (text, atts) => {
      const sid = activeId; if (!sid) return;
      if (!(await ensureProviderKey())) return;
      const sess = sessions.find((s) => s.id === sid) || {};
      patchRt(sid, (cur) => ({ status: 'starting', input: '', attachments: [], pendingSend: { text, atts: atts || [] },
        messages: [...cur.messages,
          ...(text ? [{ id: nextId(), role: 'user', text, attachments: (atts || []).map((a) => a.name) }] : []),
          { id: nextId(), role: 'system', text: 'Resuming session (launching browser)…' }] }));
      const res = await window.ats.agentStart({ sessionId: sid, projectId, provider, headed, toolBudget, agentMode: startMode, engine, title: sess.title || '' });
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
    const onToggleMode = async () => {
      const sid = activeId; if (!sid) return;
      const cur = runtime[sid] || {};
      const next = (cur.mode === 'guided') ? 'auto' : 'guided';
      patchRt(sid, { mode: next });   // optimistic; mode_changed confirms
      const res = await window.ats.agentSetMode({ sessionId: sid, mode: next });
      if (res && res.status === 'error') { patchRt(sid, { mode: cur.mode || 'auto' }); toast(res.message || 'Could not switch mode'); }
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
    const projectName = (projects.find((p) => p.id === projectId) || {}).name || '';
    const onNewChat = () => setActiveId(null);   // a session is only created when the first message is sent

    const historyProps = { projects, projectId, setProjectId, sessions, activeId, runtimeMap: runtime, onRename, onDelete };
    // Docked: history lives in a header popover. Window: a permanent left sidebar.
    const historyPopover = embedded ? (
      <Pop align="right" width={300} className="ag-history-pop" button={({ toggle, open }) => (
        <button className={'ag-icon-btn' + (open ? ' on' : '')} onClick={toggle} aria-label="Chat history" title="Chat history"><Ic.History size={15} /></button>
      )}>
        {(close) => <SessionList {...historyProps} compact onSelect={(id) => { setActiveId(id); close(); }} />}
      </Pop>
    ) : null;
    const chrome = { onNew: onNewChat, history: historyPopover, onUndock: embedded ? onUndock : null, onClose: embedded ? onClose : null };
    const shared = {
      provider, setProvider, providerList, secretKeys, llmConfig, headed, setHeaded, toolBudget, setToolBudget, engine, setEngine,
      startMode, setStartMode, onOpenBrowser: openBrowser, projectId, projectName, chrome,
    };
    const main = activeSession ? (
      <SessionChat {...shared} session={activeSession} rt={activeRt}
        onStart={onStart} onStartAuto={startWithMessage} onSend={onSend} onResumeWithMessage={resumeWithMessage}
        onStop={onStop} onReset={onReset} onToggleMode={onToggleMode}
        onRename={(title) => onRename(activeId, title)}
        setInput={(v) => patchRt(activeId, { input: v })}
        setAttachments={(v) => patchRt(activeId, { attachments: v })}
        toast={toast} />
    ) : (
      <NewChat {...shared} onStart={startWithMessage} />
    );

    return (
      <div className={'agent-shell' + (embedded ? ' embedded' : '')}>
        {!embedded && (
          <aside className="ag-sidebar">
            <div className="ag-sidebar-brand"><Ic.MessageSquare size={15} /> <b>AI Agent</b></div>
            <SessionList {...historyProps} onSelect={setActiveId} onNew={onNewChat} />
          </aside>
        )}
        {main}
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
