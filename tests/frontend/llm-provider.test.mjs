import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const appSource = readFileSync(new URL('../../web/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../web/static/index.html', import.meta.url), 'utf8');

function setup() {
  const controls = [{ hidden: false }];
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      const value = { value: '', placeholder: '' };
      if (id === 'llmReasoningEffort') {
        value.options = ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'ultra', 'max'].map(value => ({ value, hidden: false, disabled: false }));
      }
      elements.set(id, value);
    }
    return elements.get(id);
  }
  const context = vm.createContext({
    URLSearchParams,
    window: { location: { search: '' }, addEventListener() {} },
    document: {
      getElementById: element,
      querySelectorAll: () => controls,
    },
  });
  vm.runInContext(appSource, context);
  return { element, controls, context };
}

test('Google preset selects native Gemini endpoint and sends Google provider', () => {
  const { element, controls, context } = setup();
  vm.runInContext('applyLlmPreset("google")', context);
  assert.equal(element('llmProvider').value, 'google');
  assert.equal(element('llmModel').value, 'gemini-2.5-flash');
  assert.equal(element('llmBaseUrl').value, 'https://generativelanguage.googleapis.com/v1beta');
  assert.equal(element('llmModel').placeholder, 'GOOGLE_MODEL');
  assert.ok(controls.every(control => control.hidden));
  element('llmApiKey').value = 'test-key';
  element('llmReasoningEffort').value = 'high';
  const payload = vm.runInContext('llmPayload()', context);
  assert.equal(payload.reasoning_effort, 'high');
  assert.ok(!('thinking_effort' in payload));
  assert.equal(payload.provider, 'google');
  assert.equal(payload.api_key, 'test-key');
  assert.equal(payload.base_url, element('llmBaseUrl').value);
  assert.equal(payload.model, element('llmModel').value);
  assert.match(html, /<select id="llmProvider">[^]*?<option value="google">/);
  assert.match(html, /<select id="llmPresetSelect">[^]*?<option value="google">/);
});

test('switching away from Google restores provider-specific hints and controls', () => {
  const { element, controls, context } = setup();
  for (const [provider, modelHint, baseHint, hidden] of [
    ['google', 'GOOGLE_MODEL', 'https://generativelanguage.googleapis.com/v1beta', true],
    ['anthropic', 'ANTHROPIC_MODEL', 'https://api.anthropic.com', false],
    ['openai-responses', 'OPENAI_MODEL', 'https://api.openai.com/v1', true],
  ]) {
    element('llmProvider').value = provider;
    vm.runInContext('syncLlmProviderFields()', context);
    assert.equal(element('llmModel').placeholder, modelHint);
    assert.equal(element('llmBaseUrl').placeholder, baseHint);
    assert.ok(controls.every(control => control.hidden === hidden));
  }
});

test('reasoning choices follow the provider and invalid selections are cleared', () => {
  const { element, context } = setup();
  const effort = element('llmReasoningEffort');
  for (const [provider, expected] of [
    ['openai', ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'ultra', 'max']],
    ['openai-responses', ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'ultra', 'max']],
    ['anthropic', ['low', 'medium', 'high', 'max']],
    ['google', ['minimal', 'low', 'medium', 'high']],
  ]) {
    element('llmProvider').value = provider;
    effort.value = 'high';
    vm.runInContext('syncLlmProviderFields()', context);
    assert.deepEqual(effort.options.filter(option => option.value && !option.hidden && !option.disabled).map(option => option.value), expected);
    assert.equal(effort.value, 'high');
    assert.equal(vm.runInContext('llmPayload()', context).reasoning_effort, 'high');
  }
  effort.value = 'max';
  vm.runInContext('syncLlmProviderFields()', context);
  assert.equal(effort.value, '');
  assert.ok(!('reasoning_effort' in vm.runInContext('llmPayload()', context)));
  assert.match(html, /<select id="llmReasoningEffort">/);
  assert.ok(!html.includes('llmThinkingEffort'));
});
