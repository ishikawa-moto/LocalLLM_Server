import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { BACKGROUND_CONTEXT as context } from '@earendil-works/chord/context';
import { createModels, createProvider } from '@earendil-works/pi-ai/models';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { AssistantEntry, createRegistry, defineExtension, defineTool, Harness } from '@earendil-works/pi-durable';
import { openNodeSqliteStorage } from '@earendil-works/pi-durable/storage/sqlite/node';

const base = 'http://127.0.0.1:18080';
const modelId = 'bonsai-2.5-27b';
const expected = Number(process.argv[2]);
const output = process.argv[3];
assert.ok([65536, 131072].includes(expected) && output, 'Usage: node bonsai-smoke.mjs 131072 <new evidence directory> [--long]');

async function api(path, body, timeout = 15000) {
    const response = await fetch(`${base}${path}`, {
        redirect: 'error',
        signal: AbortSignal.timeout(timeout),
        ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
    assert.ok(response.ok, `${path} responded with ${response.status}`);
    return response.json();
}

const props = await api('/props');
const modelsList = await api('/v1/models');
assert.ok(modelsList.data.some(({ id }) => id === modelId), 'Only probe the isolated Bonsai runtime');
assert.equal(props.default_generation_settings.n_ctx, expected, 'Unexpected effective per-slot context');
assert.equal(props.total_slots, 1, 'Use a single test slot');
assert.ok(props.chat_template, 'No active chat template');
const text = 'LocalBrain 日本語 tokenizer check';
const { tokens } = await api('/tokenize', { content: text });
assert.ok(tokens.length > 0);
assert.equal((await api('/detokenize', { tokens })).content, text);

const model = {
    id: modelId, name: 'Bonsai 2.5 27B', api: 'openai-completions', provider: 'localbrain-bonsai',
    baseUrl: `${base}/v1`, reasoning: false, input: ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: expected, maxTokens: 256, compat: { supportsDeveloperRole: false, supportsStore: false },
};
const models = createModels();
models.setProvider(createProvider({
    id: model.provider, name: model.name, baseUrl: model.baseUrl,
    auth: { apiKey: { name: model.name, resolve: async () => ({ auth: { apiKey: 'local-loopback' } }) } },
    models: [model], api: openAICompletionsApi(),
}));
const calls = [];
const registry = createRegistry();
registry.install(defineExtension({ name: 'localbrain-probe', tools: [defineTool({
    name: 'read_fixture_code', description: 'Read a deterministic fixture code. Pass the key code.',
    parameters: { type: 'object', properties: { key: { type: 'string', enum: ['code'] } }, required: ['key'], additionalProperties: false },
    replay: 'safe',
    execute: async ({ key }) => {
        calls.push(key);
        return { output: [{ type: 'text', text: 'LB-CHECK-9537' }] };
    },
})] }));

await mkdir(output);
const harness = await Harness.open(await openNodeSqliteStorage(join(output, 'inference.sqlite')), { models, registry }, context);
try {
    const root = await harness.root(context, { agent: { model: { provider: model.provider, modelId } } });
    const submission = await root.submit({ type: 'input', requestId: 'model-probe-v1',
        content: 'Call read_fixture_code with key code. After seeing its result, reply with that exact code.' }, context);
    const settled = await submission.wait(context);
    assert.equal(settled.status, 'done', JSON.stringify(settled));
    assert.deepEqual(calls, ['code']);
    const answer = await root.commit((tx) => tx.entry(AssistantEntry, settled.answer), context);
    assert.ok(answer.model.some((message) => message.content?.some((part) => part.type === 'text' && part.text.includes('LB-CHECK-9537'))));
    console.log(JSON.stringify({ model: modelId, effectiveContext: expected, slots: props.total_slots,
        chatTemplate: true, tokenizerRoundTrip: true, piDurableToolCall: true }));
} finally {
    await harness.close(context);
}

if (process.argv.includes('--long')) {
    const start = 'The first retrieval key is ALPHA-7581. Keep it for the final question.\n';
    const end = '\nThe final retrieval key is OMEGA-1946. Return only the two retrieval keys in order.';
    const fill = ' The blue bird carried a small stone across an empty field.\n';
    const perLine = (await api('/tokenize', { content: fill.repeat(100) })).tokens.length / 100;
    const content = start + fill.repeat(Math.floor((expected - 4096) / perLine)) + end;
    const result = await api('/v1/chat/completions', { model: modelId, messages: [{ role: 'user', content }],
        temperature: 0, max_tokens: 96 }, 1200000);
    const answer = result.choices[0].message.content;
    assert.ok(result.usage.prompt_tokens >= expected * 0.94 && result.usage.prompt_tokens < expected - 96);
    assert.ok(typeof answer === 'string' && /ALPHA-7581[\s\S]*OMEGA-1946/.test(answer));
    console.log(JSON.stringify({ longPromptTokens: result.usage.prompt_tokens, retrieval: true }));
}
