import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { openCoordinator, protocol } from './coordinator.mjs';
import { coordinatorServer } from './http.mjs';
import { authorizeDelegation, delegationCandidates } from './egress.mjs';

const thisFile = fileURLToPath(import.meta.url);
const checkpoint = {
    serverSha: '1'.repeat(40), clientSha: '2'.repeat(40), environmentSha256: '3'.repeat(64),
    artifactSha256: '4'.repeat(64), stopReceiptSha256: '5'.repeat(64),
};

if (process.argv[2] === '--fixture-server') {
    const tokens = JSON.parse(process.env.LOCALBRAIN_FIXTURE_TOKENS);
    const proofs = {
        verifyRelease: async ({ checkpoint: proposed }) => JSON.stringify(proposed) === JSON.stringify(checkpoint),
        verifyReceive: async ({ checkpoint: received }) => JSON.stringify(received) === JSON.stringify(checkpoint),
        verifyCompletion: async () => true,
    };
    const coordinator = await openCoordinator(process.argv[3], process.argv[4] === 'verified' ? proofs : {});
    const server = coordinatorServer(coordinator, { tokens, onError: console.error });
    const { port } = await server.listen({ port: 0 });
    process.send(port);
    process.on('message', async (message) => {
        if (message === 'close') {
            await server.close();
            await coordinator.close();
            process.disconnect();
        }
    });
} else {
    const clientFile = process.argv[2] ? pathToFileURL(process.argv[2]) : new URL('../../LocalLLM_Client/next/worker-client.mjs', import.meta.url);
    const { workerClient } = await import(clientFile);
    const { handoffWorker } = await import(new URL('./worker.mjs', clientFile));
    const directory = await mkdtemp(join(tmpdir(), 'localbrain-next-'));
    const tokens = { PC70: randomBytes(32).toString('hex'), PC73: randomBytes(32).toString('hex') };
    const instances = { PC70: randomUUID(), PC73: randomUUID() };
    let child;
    let endpoint;
    async function start(mode) {
        child = spawn(process.execPath, [thisFile, '--fixture-server', directory, mode], {
            stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
            env: { ...process.env, LOCALBRAIN_FIXTURE_TOKENS: JSON.stringify(tokens) },
        });
        const [port] = await Promise.race([once(child, 'message'), once(child, 'exit').then(([code]) => {
            throw new Error(`fixture server exited with ${code}`);
        })]);
        endpoint = `http://127.0.0.1:${port}`;
    }
    async function stop(crash) {
        const exiting = once(child, 'exit');
        if (crash) child.kill('SIGKILL');
        else child.send('close');
        await exiting;
        child = undefined;
    }
    const worker = (host, instance = instances[host]) => workerClient({ endpoint, token: tokens[host], instance });
    try {
        await start('unverified');
        const pc73 = worker('PC73');
        const pc70 = worker('PC70');
        const taskId = randomUUID();
        const pc70Task = randomUUID();
        assert.equal((await pc73.health()).protocol, protocol);
        await assert.rejects(workerClient({ endpoint, token: randomBytes(32).toString('hex'), instance: randomUUID() }).health(), /unauthorized/);
        await pc73.command('register');
        await pc70.command('register');
        await pc70.command('create', { taskId: pc70Task, goal: 'Validate a PC70-only OCR fixture' });
        await pc70.command('start', { taskId: pc70Task, epoch: 0 });
        await assert.rejects(pc70.command('drain', { taskId: pc70Task, epoch: 1, reason: 'return' }), /not_pc73_goal/);
        await pc73.command('create', { taskId, goal: 'Add a non-main fixture command and verify its output' });
        const startId = randomUUID();
        assert.equal((await pc73.command('start', { taskId, epoch: 0 }, startId)).epoch, 1);
        assert.equal((await pc73.command('start', { taskId, epoch: 0 }, startId)).epoch, 1);
        await assert.rejects(openCoordinator(directory), /locked/);
        await assert.rejects(pc70.command('receive', { taskId, epoch: 1 }), /fenced/);
        assert.equal((await pc73.command('check', { taskId, epoch: 1 })).allowed, true);
        await pc73.command('drain', { taskId, epoch: 1, reason: 'shutdown' });
        await assert.rejects(pc73.command('check', { taskId, epoch: 1 }), /fenced/);
        await assert.rejects(pc73.command('release', { taskId, epoch: 1, checkpoint }), /release_unverified/);
        assert.equal((await pc73.task(taskId)).phase, 'draining');
        await stop(true);
        await assert.rejects(pc73.command('check', { taskId, epoch: 1 }));

        await start('verified');
        const source = worker('PC73');
        const fallback = worker('PC70');
        assert.equal((await source.task(taskId)).owner.instance, instances.PC73);
        const releaseId = randomUUID();
        const released = await source.command('release', { taskId, epoch: 1, checkpoint }, releaseId);
        assert.equal(released.phase, 'offered');
        assert.equal(released.owner, null);
        assert.deepEqual(await source.command('release', { taskId, epoch: 1, checkpoint }, releaseId), released);
        assert.ok((await fallback.pending()).some((task) => task.id === taskId));
        const receives = await Promise.allSettled([
            fallback.command('receive', { taskId, epoch: 1 }),
            fallback.command('receive', { taskId, epoch: 1 }),
        ]);
        assert.deepEqual(receives.map((result) => result.status).sort(), ['fulfilled', 'rejected']);
        assert.equal((await fallback.task(taskId)).epoch, 2);
        await assert.rejects(source.command('check', { taskId, epoch: 1 }), /fenced/);
        await assert.rejects(fallback.command('drain', { taskId, epoch: 2, reason: 'return' }), /pc73_not_restarted/);

        const restarted = worker('PC73', randomUUID());
        const steps = [];
        const pc70Worker = handoffWorker({ host: 'PC70', client: fallback, supervisor: {
            startOrResume: async (task) => assert.equal(task.id, pc70Task),
            stopAndCheckpoint: async (task) => {
                assert.equal(task.phase, 'draining');
                steps.push('PC70 stopped');
                return checkpoint;
            },
        } });
        const pc73Worker = handoffWorker({ host: 'PC73', client: restarted, supervisor: {
            prepareCheckout: async (task) => {
                assert.equal(task.phase, 'offered');
                assert.deepEqual(task.checkpoint, checkpoint);
                steps.push('PC73 verified checkout');
            },
            startOrResume: async (task) => {
                assert.equal(task.epoch, 3);
                steps.push('PC73 resumed');
            },
        } });
        await pc73Worker.register();
        await assert.rejects(source.command('register'), /retired_worker/);
        await pc70Worker.tick();
        await pc73Worker.tick();
        assert.deepEqual(steps, ['PC70 stopped', 'PC73 verified checkout', 'PC73 resumed']);
        await assert.rejects(fallback.command('check', { taskId, epoch: 2 }), /fenced/);
        assert.equal((await fallback.task(pc70Task)).owner.host, 'PC70');
        assert.equal((await restarted.command('complete', { taskId, epoch: 3 })).phase, 'complete');
        assert.deepEqual(delegationCandidates({ difficulty: 'LOW', bonsaiFailed: true }), ['Terra']);
        assert.deepEqual(delegationCandidates({ difficulty: 'NORMAL', bonsaiFailed: true }), ['Terra', 'Haiku', 'Sol', 'Opus']);
        assert.deepEqual(delegationCandidates({ difficulty: 'HIGH', bonsaiFailed: true }), ['Sol', 'Opus']);
        assert.deepEqual(delegationCandidates({ difficulty: 'NORMAL', bonsaiFailed: false }), []);
        const packet = { dataClass: 'synthetic', model: 'Sol', cli: 'codex', destination: 'OpenAI',
            containsRestrictedContent: false, auth: 'subscription-cli', additionalApiCharges: false };
        const approvals = [{ ...packet, decision: 'allow', dataClasses: ['synthetic'] }];
        assert.equal(authorizeDelegation(packet), false);
        assert.equal(authorizeDelegation(packet, approvals), true);
        assert.equal(authorizeDelegation({ ...packet, destination: 'proxy' }, approvals), false);
        assert.equal(authorizeDelegation({ ...packet, containsRestrictedContent: true }, approvals), false);
        assert.equal(authorizeDelegation({ ...packet, auth: 'api-key' }, approvals), false);
        console.log('PASS HTTP authentication, crash/reopen, exclusive writer, proof gate, automatic return, stale-worker fencing and egress decisions');
    } finally {
        if (child && child.exitCode === null) await stop(false);
        await rm(directory, { recursive: true });
    }
}
