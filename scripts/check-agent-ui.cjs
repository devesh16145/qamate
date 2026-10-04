// Compile both inline Babel blocks and shared agent UI; no Electron launch needed.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = path.resolve(__dirname, '..');
const babel = require(path.join(root, 'src/vendor/babel.min.js'));
const html = fs.readFileSync(path.join(root, 'src/index.html'), 'utf8');
const blocks = [...html.matchAll(/<script[^>]*type=["']text\/babel["'][^>]*>([\s\S]*?)<\/script>/g)].filter(block => block[1].trim());
assert.equal(blocks.length, 2, 'Expected both inline JSX blocks');
for (const block of blocks) babel.transform(block[1], {presets: ['react']});
babel.transform(fs.readFileSync(path.join(root, 'src/agent_ui.js'), 'utf8'), {presets: ['react']});
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(root, 'src/llm_providers.js'), 'utf8'), context);
const helpers = context.window.LlmProviders;
const cfg = {llm: {default_provider: 'chat', providers: {
  chat: {protocol: 'openai', api_key_env: 'CHAT_KEY'},
  decision: {protocol: 'typesafe', api_key_env: 'JEV_KEY'},
  gateway: {protocol: 'openrouter_decisions', api_key_env: 'OPENROUTER_API_KEY'},
  local: {protocol: 'ollama'}
}}};
assert.equal(helpers.listFromConfig(cfg).find(p => p.id === 'decision').canPlan, false);
assert.equal(helpers.listFromConfig(cfg).find(p => p.id === 'gateway').canPlan, false);
assert.equal(helpers.isConfigured('local', cfg, {}), true);
assert.equal(helpers.isConfigured('chat', cfg, {}), false);
assert.equal(helpers.isConfigured('chat', cfg, {CHAT_KEY: true}), true);
assert.equal(helpers.keyFieldsFromConfig(cfg).length, 3);

// Catalog-driven presets, defaults and migration (mirrors engine/model_profiles.py).
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'engine/provider_catalog.json'), 'utf8'));
helpers.setCatalog(catalog);
assert.ok(!helpers.presetIds().includes('mock'));
assert.equal(helpers.presetIds().slice(-1)[0], 'custom');
const mimo = {llm: {providers: {mimo: {preset: 'mimo', endpoint: 'tp-sgp'}}}};
const r = helpers.resolve('mimo', mimo);
assert.equal(r.base_url, 'https://token-plan-sgp.xiaomimimo.com/v1');
assert.equal(r.api_key_env, 'MIMO_API_KEY');
assert.equal(r.model, 'mimo-v2.6-flash');
assert.equal(r.thinking, 'on');
assert.equal(helpers.defaultFromConfig(mimo), 'mimo');
assert.equal(helpers.defaultFromConfig({llm: {providers: {}}}), '');
const second = helpers.newProfile('mimo', mimo);
assert.equal(second.id, 'mimo-2');
assert.equal(second.profile.api_key_env, 'QAMATE_MIMO_2_KEY');
const legacy = {llm: {default_provider: 'mock', providers: {
  mock: {},
  anthropic: {model: 'claude-sonnet-4-6', api_key_env: 'ATS_ANTHROPIC_KEY', max_tokens: 4096},
  mimo: {model: 'mimo-v2.5-pro', base_url: 'https://token-plan-sgp.xiaomimimo.com/v1', api_key_env: 'MIMO_API_KEY', token_parameter: 'max_completion_tokens'},
}}};
assert.equal(helpers.migrateConfig(legacy), true);
assert.deepEqual(Object.keys(legacy.llm.providers), ['mimo']);
assert.deepEqual(JSON.parse(JSON.stringify(legacy.llm.providers.mimo)), {api_key_env: 'MIMO_API_KEY', preset: 'mimo', endpoint: 'tp-sgp'});
assert.equal(legacy.llm.default_provider, 'mimo');
assert.equal(helpers.migrateConfig(legacy), false);
console.log('PASS: two inline JSX blocks, shared agent UI, provider helper contracts, catalog presets + migration');
