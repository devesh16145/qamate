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
console.log('PASS: two inline JSX blocks, shared agent UI, provider helper contracts');
