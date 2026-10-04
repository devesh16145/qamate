/* QAmate — BYOK LLM provider helpers (plain JS, shared by Settings, the Agent UI
 * and main.js). Presets come from engine/provider_catalog.json (handed in via
 * setCatalog); a config.json profile names a preset and overrides its fields.
 * Mirrors engine/model_profiles.py resolve_profile(). */
(function (root) {
  const DECISION_PROTOCOLS = ['typesafe', 'openrouter_decisions'];
  const NO_KEY_PROTOCOLS = ['ollama'];
  let catalog = { presets: {} };

  function setCatalog(c) { catalog = (c && c.presets) ? c : { presets: {} }; }
  function presets() { return catalog.presets || {}; }
  function preset(id) { return presets()[id] || null; }

  /** Preset ids in display order (custom last). */
  function presetIds() {
    const ids = Object.keys(presets()).filter(id => id !== 'custom');
    if (presets().custom) ids.push('custom');
    return ids;
  }

  function providersMap(cfg) {
    return (cfg && cfg.llm && cfg.llm.providers) || {};
  }

  function presetIdFor(id, p) {
    if (p && p.preset) return p.preset;
    return (id !== 'custom' && presets()[id]) ? id : null;
  }

  /** Effective profile: preset defaults overlaid with the profile's own fields. */
  function resolve(id, cfg) {
    const own = providersMap(cfg)[id];
    if (!own) return null;
    const pid = presetIdFor(id, own);
    const pre = pid ? preset(pid) : null;
    const eps = (pre && pre.endpoints) || [];
    const ep = eps.find(e => e.id === own.endpoint) || eps[0] || null;
    const out = {
      protocol: (pre && pre.protocol) || 'openai',
      api_key_env: (pre && pre.api_key_env) || '',
      base_url: ep ? ep.base_url : '',
      model: (pre && pre.default_model) || '',
      max_tokens: (pre && pre.max_tokens) || 0,
      supports_temperature: pre ? pre.supports_temperature !== false : true,
      headers: (pre && pre.headers) || null,
      thinking: (pre && pre.thinking) ? (own.thinking || pre.thinking.default || 'on') : null,
    };
    for (const k of Object.keys(own)) if (own[k] !== '' && own[k] != null) out[k] = own[k];
    if (pre && pre.thinking) out.thinking = own.thinking || pre.thinking.default || 'on';
    out.id = id;
    out.preset = pid;
    out.endpoint = ep ? ep.id : (own.endpoint || null);
    return out;
  }

  function needsKeyFor(r, pre) {
    if (!r) return false;
    if (NO_KEY_PROTOCOLS.includes(r.protocol)) return false;
    return !(pre && pre.needs_key === false);
  }

  function label(id, cfg) {
    const own = providersMap(cfg)[id] || {};
    if (own.label) return own.label;
    const pre = preset(presetIdFor(id, own));
    return pre ? pre.label : id;
  }

  function listFromConfig(cfg) {
    return Object.keys(providersMap(cfg)).map((id) => {
      const r = resolve(id, cfg);
      const pre = preset(r.preset);
      return {
        id,
        preset: r.preset,
        protocol: r.protocol,
        canPlan: !DECISION_PROTOCOLS.includes(r.protocol) && !(r.capabilities && r.capabilities.tools === false),
        label: label(id, cfg),
        api_key_env: r.api_key_env || '',
        needsKey: needsKeyFor(r, pre),
        model: r.model || '',
        base_url: r.base_url || '',
        endpoint: r.endpoint,
        thinking: r.thinking,
      };
    });
  }

  /** The planner/default provider id, or '' when nothing usable is configured. */
  function defaultFromConfig(cfg) {
    const d = cfg && cfg.llm && (cfg.llm.roles?.planner || cfg.llm.roles?.primary || cfg.llm.default_provider);
    const ids = listFromConfig(cfg).filter(p => p.canPlan).map((p) => p.id);
    if (d && ids.includes(d)) return d;
    return ids[0] || '';
  }

  function keyEnv(id, cfg) {
    const r = resolve(id, cfg);
    return (r && r.api_key_env) || '';
  }

  function isConfigured(id, cfg, secretKeys) {
    const profile = listFromConfig(cfg).find(p => p.id === id);
    if (!profile) return false;
    if (!profile.needsKey) return true;
    const env = keyEnv(id, cfg);
    return !!(env && secretKeys && secretKeys[env]);
  }

  function missingKeyMessage(id, cfg) {
    if (!id) return 'No AI provider configured. Add one in Settings → AI / LLM.';
    const env = keyEnv(id, cfg);
    const name = label(id, cfg);
    if (!env) return `${name} has no API key variable configured.`;
    return `Add your ${name} API key in Settings → AI / LLM (or set ${env} in your environment).`;
  }

  /** One key field per env var (several profiles may share a key). */
  function keyFieldsFromConfig(cfg) {
    const seen = new Set();
    const out = [];
    const list = listFromConfig(cfg);
    for (const p of list) {
      if (!p.needsKey || !p.api_key_env || seen.has(p.api_key_env)) continue;
      seen.add(p.api_key_env);
      out.push({ env: p.api_key_env, usedBy: list.filter((x) => x.api_key_env === p.api_key_env).map((x) => x.label) });
    }
    return out;
  }

  function envSafe(s) { return String(s).toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, ''); }

  /** A new profile for `presetId` with an id that doesn't collide. */
  function newProfile(presetId, cfg) {
    const existing = providersMap(cfg);
    const base = presetId === 'custom' ? 'custom' : presetId;
    let id = base, n = 2;
    while (existing[id]) id = `${base}-${n++}`;
    const profile = { preset: presetId };
    const pre = preset(presetId) || {};
    // A second profile on the same preset gets its own key slot.
    if (id !== base || !pre.api_key_env) profile.api_key_env = `QAMATE_${envSafe(id)}_KEY`;
    if (presetId === 'custom') profile.label = 'Custom endpoint';
    return { id, profile };
  }

  // Untouched pre-catalog example profiles: dropped on migration so stale
  // defaults (gpt-4o, claude-sonnet-4-6, llama3.1) don't masquerade as choices.
  const LEGACY_EXAMPLES = {
    anthropic: { model: 'claude-sonnet-4-6', api_key_env: 'ATS_ANTHROPIC_KEY', max_tokens: 4096 },
    openai: { model: 'gpt-4o', api_key_env: 'ATS_OPENAI_KEY', max_tokens: 4096 },
    ollama: { model: 'llama3.1', base_url: 'http://localhost:11434', max_tokens: 4096 },
  };
  const LEGACY_INFERRED = { anthropic: 'anthropic', ollama: 'ollama' };

  function sameShape(a, b) {
    const ka = Object.keys(a || {}), kb = Object.keys(b || {});
    return ka.length === kb.length && ka.every(k => a[k] === b[k]);
  }

  /** Upgrade a pre-catalog llm config in place. Returns true if it changed. */
  function migrateConfig(cfg) {
    if (!cfg || !cfg.llm || cfg.llm.schema >= 2) return false;
    const llm = cfg.llm;
    const provs = { ...(llm.providers || {}) };
    delete provs.mock;
    for (const id of Object.keys(LEGACY_EXAMPLES)) {
      if (provs[id] && sameShape(provs[id], LEGACY_EXAMPLES[id])) delete provs[id];
    }
    for (const id of Object.keys(provs)) {
      const p = { ...provs[id] };
      if (!p.preset && presets()[id] && id !== 'custom') {
        p.preset = id;
        // Legacy name-inferred protocol; the preset now owns it.
        if (!p.protocol || p.protocol === (LEGACY_INFERRED[id] || 'openai')) delete p.protocol;
      }
      if (!p.preset && id === 'mimo-ultraspeed') {
        p.preset = 'mimo';
        delete p.protocol;
      }
      if (p.preset === 'mimo') {
        // Name-based quirks now come from the preset; the 2.5 models retire 2026-10-21.
        delete p.token_parameter; delete p.supports_temperature;
        if (/^mimo-v2\.5/.test(p.model || '')) delete p.model;
        const ep = (preset('mimo').endpoints || []).find(e => e.base_url === (p.base_url || '').replace(/\/+$/, ''));
        if (ep) { p.endpoint = ep.id; delete p.base_url; }
      }
      provs[id] = p;
    }
    llm.providers = provs;
    for (const role of Object.keys(llm.roles || {})) if (llm.roles[role] && !provs[llm.roles[role]]) delete llm.roles[role];
    if (!llm.default_provider || !provs[llm.default_provider]) {
      llm.default_provider = listFromConfig(cfg).filter(p => p.canPlan).map(p => p.id)[0] || '';
    }
    llm.schema = 2;
    return true;
  }

  const api = {
    setCatalog, preset, presetIds, resolve, label, listFromConfig, defaultFromConfig,
    keyEnv, isConfigured, missingKeyMessage, keyFieldsFromConfig, newProfile, migrateConfig,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LlmProviders = api;
})(typeof window !== 'undefined' ? window : null);
