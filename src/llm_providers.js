/* QAmate — BYOK LLM provider helpers (plain JS, shared by Settings + Agent UI). */
(function () {
  const LABELS = {
    mock: 'Mock (offline, no key)',
    anthropic: 'Claude (Anthropic)',
    openai: 'OpenAI',
    ollama: 'Local (Ollama)',
    mimo: 'Xiaomi MiMo',
    'mimo-ultraspeed': 'MiMo Ultraspeed',
    openrouter: 'OpenRouter',
  };

  function label(id) {
    return LABELS[id] || id;
  }

  function providersMap(cfg) {
    return (cfg && cfg.llm && cfg.llm.providers) || {};
  }

  function listFromConfig(cfg) {
    const map = providersMap(cfg);
    return Object.keys(map).map((id) => {
      const p = map[id] || {};
      const protocol = p.protocol || ({anthropic:'anthropic', ollama:'ollama', mock:'mock'}[id] || 'openai');
      const needsKey = protocol !== 'mock' && protocol !== 'ollama';
      return {
        id,
        protocol,
        canPlan: !['typesafe', 'openrouter_decisions'].includes(protocol) && p.capabilities?.tools !== false,
        label: label(id),
        api_key_env: p.api_key_env || '',
        needsKey,
        model: p.model || '',
        base_url: p.base_url || '',
      };
    });
  }

  function defaultFromConfig(cfg) {
    const d = cfg && cfg.llm && (cfg.llm.roles?.planner || cfg.llm.roles?.primary || cfg.llm.default_provider);
    const ids = listFromConfig(cfg).filter(p => p.canPlan).map((p) => p.id);
    if (d && ids.includes(d)) return d;
    if (ids.includes('mock')) return 'mock';
    return ids[0] || 'mock';
  }

  function keyEnv(id, cfg) {
    const p = providersMap(cfg)[id];
    return (p && p.api_key_env) || '';
  }

  function isConfigured(id, cfg, secretKeys) {
    const profile = listFromConfig(cfg).find(p => p.id === id);
    if (!profile) return false;
    if (!profile.needsKey) return true;
    const env = keyEnv(id, cfg);
    if (!env) return false;
    return !!(secretKeys && secretKeys[env]);
  }

  function missingKeyMessage(id, cfg) {
    const env = keyEnv(id, cfg);
    const name = label(id);
    if (!env) return `${name} is not configured in config.json (missing api_key_env).`;
    return `Add your ${name} API key in Settings → AI / LLM (${env}), or set the ${env} environment variable.`;
  }

  /** One key field per env var (MiMo + Ultraspeed may differ). */
  function keyFieldsFromConfig(cfg) {
    const seen = new Set();
    const out = [];
    for (const p of listFromConfig(cfg)) {
      if (!p.needsKey || !p.api_key_env || seen.has(p.api_key_env)) continue;
      seen.add(p.api_key_env);
      const usedBy = listFromConfig(cfg).filter((x) => x.api_key_env === p.api_key_env).map((x) => x.label);
      out.push({ env: p.api_key_env, usedBy });
    }
    return out;
  }

  window.LlmProviders = {
    label,
    listFromConfig,
    defaultFromConfig,
    keyEnv,
    isConfigured,
    missingKeyMessage,
    keyFieldsFromConfig,
  };
})();
