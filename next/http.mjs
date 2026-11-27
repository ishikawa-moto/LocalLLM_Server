import { timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';
import { createServer as createSecureServer } from 'node:https';
import { protocol } from './coordinator.mjs';

const conflicts = new Set(['task_exists', 'worker_still_owns_task', 'retired_worker', 'fenced',
    'target_unregistered', 'release_unverified', 'receive_unverified', 'completion_unverified',
    'not_pc73_goal', 'pc73_not_restarted', 'unregistered_worker']);
const invalid = new Set(['invalid_task', 'invalid_worker', 'invalid_request', 'invalid_checkpoint',
    'invalid_goal', 'unknown_action', 'invalid_reason', 'invalid_json', 'body_too_large']);

function send(response, status, payload) {
    response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify(payload));
}

async function readBody(request) {
    const chunks = [];
    let bytes = 0;
    for await (const chunk of request.iterator({ destroyOnReturn: false })) {
        bytes += chunk.length;
        if (bytes > 65536) throw new Error('body_too_large');
        chunks.push(chunk);
    }
    try {
        return JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch {
        throw new Error('invalid_json');
    }
}

export function coordinatorServer(coordinator, { tokens, tls, onError = () => {} }) {
    const credentials = ['PC70', 'PC73'].map((host) => [host, Buffer.from(tokens[host] ?? '')]);
    const [, pc70] = credentials[0];
    const [, pc73] = credentials[1];
    if (pc70.length < 32 || pc73.length < 32 || pc70.equals(pc73)) throw new Error('distinct_worker_tokens_required');

    const handle = async (request, response) => {
        const token = Buffer.from(request.headers.authorization?.replace(/^Bearer /, '') ?? '');
        const host = credentials.find(([, secret]) => token.length === secret.length && timingSafeEqual(token, secret))?.[0];
        if (!host || !request.headers.authorization?.startsWith('Bearer ')) return send(response, 401, { error: 'unauthorized' });
        const actor = { host, instance: request.headers['x-localbrain-worker'] };
        try {
            if (request.method === 'GET' && request.url === '/handoff/v1/health') return send(response, 200, { protocol });
            if (request.method === 'POST' && request.url === '/handoff/v1/command') {
                return send(response, 200, await coordinator.run(actor, await readBody(request)));
            }
            if (request.method === 'GET' && request.url === '/handoff/v1/tasks') {
                return send(response, 200, await coordinator.pending(actor));
            }
            const taskPath = /^\/handoff\/v1\/tasks\/([a-f0-9-]+)$/.exec(request.url);
            if (request.method === 'GET' && taskPath) {
                const task = await coordinator.read(taskPath[1], actor);
                return task ? send(response, 200, task) : send(response, 404, { error: 'task_not_found' });
            }
            return send(response, 404, { error: 'route_not_found' });
        } catch (error) {
            if (conflicts.has(error.message)) return send(response, 409, { error: error.message });
            if (invalid.has(error.message)) return send(response, 400, { error: error.message });
            if (error.message === 'task_not_found') return send(response, 404, { error: error.message });
            onError(error);
            return send(response, 500, { error: 'coordinator_error' });
        }
    };
    const server = tls ? createSecureServer({ ...tls, minVersion: 'TLSv1.2' }, handle) : createServer(handle);
    server.requestTimeout = 15000;
    return {
        async listen({ port = 18443, host = '127.0.0.1' } = {}) {
            if (!tls && host !== '127.0.0.1') throw new Error('tls_required_for_lan');
            await new Promise((resolve, reject) => {
                server.once('error', reject);
                server.listen(port, host, resolve);
            });
            return server.address();
        },
        async close() {
            if (server.listening) await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
        },
    };
}
