import { mkdir, realpath } from 'node:fs/promises';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { BACKGROUND_CONTEXT as context } from '@earendil-works/chord/context';
import { createModels } from '@earendil-works/pi-ai/models';
import { createRegistry, defineDoc, Harness } from '@earendil-works/pi-durable';
import { SqliteStorage } from '@earendil-works/pi-durable/storage/sqlite';
import { openNodeSqliteDatabase } from '@earendil-works/pi-durable/storage/sqlite/node';

export const protocol = 'localbrain-handoff/1';
const sha256 = /^[a-f0-9]{64}$/;
const gitSha = /^[a-f0-9]{40}$/;
const id = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const hosts = new Set(['PC70', 'PC73']);

const taskState = defineDoc({
    kind: 'localbrain.task-state',
    version: 1,
    scope: 'conversation',
    history: 'latest',
    fork: 'initial',
    initial: () => ({ workers: {}, retired: { PC70: [], PC73: [] }, tasks: {} }),
    checkpointWhen: () => false,
});

function requireState(condition, code) {
    if (!condition) throw new Error(code);
}

function receivingWorker(state, task, actor) {
    return actor.host === task.target && task.phase === 'offered'
        && state.workers[actor.host] === actor.instance;
}

function holdsGrant(task, actor, epoch) {
    return task.owner?.host === actor.host && task.owner.instance === actor.instance && task.epoch === epoch;
}

function validCheckpoint(value) {
    return value && gitSha.test(value.serverSha) && gitSha.test(value.clientSha)
        && sha256.test(value.environmentSha256) && sha256.test(value.artifactSha256)
        && sha256.test(value.stopReceiptSha256);
}

function checkpointFields(value) {
    return {
        serverSha: value.serverSha,
        clientSha: value.clientSha,
        environmentSha256: value.environmentSha256,
        artifactSha256: value.artifactSha256,
        stopReceiptSha256: value.stopReceiptSha256,
    };
}

// Only one process may drive a given journal. The OS releases this SQLite lock even on a crash.
export async function openCoordinator(directory, { verifyRelease, verifyReceive, verifyCompletion } = {}) {
    await mkdir(directory, { recursive: true });
    const dataRoot = await realpath(directory);
    const lock = new DatabaseSync(join(dataRoot, 'writer.lock.sqlite'));
    let database;
    let harness;
    try {
        lock.exec('BEGIN IMMEDIATE');
        database = await openNodeSqliteDatabase(join(dataRoot, 'durable.sqlite'));
        await database.exec('PRAGMA synchronous = FULL');
        harness = await Harness.open(await SqliteStorage.open(database), {
            models: createModels(),
            registry: createRegistry(),
        }, context);
        const root = await harness.root(context);
        const read = () => harness.snapshot(taskState, root.id, context);
        return {
            async read(taskId, actor) {
                requireState(id.test(taskId), 'invalid_task');
                const state = await read();
                requireState(actor && state?.workers[actor.host] === actor.instance, 'unregistered_worker');
                const task = state.tasks[taskId];
                if (!task || ![task.origin, task.target, task.owner?.host].includes(actor.host)) return null;
                return task;
            },
            async pending(actor) {
                const state = await read();
                requireState(state?.workers[actor.host] === actor.instance, 'unregistered_worker');
                return Object.values(state.tasks).filter((task) =>
                    (task.phase === 'offered' && task.target === actor.host)
                    || holdsGrant(task, actor, task.epoch)).map((task) => ({
                    ...task,
                    returnPending: actor.host === 'PC70' && task.origin === 'PC73' && task.phase === 'active'
                        && !!state.workers.PC73 && state.workers.PC73 !== task.departedInstance,
                }));
            },
            async run(actor, command) {
                requireState(command?.protocol === protocol && id.test(command.requestId), 'invalid_request');
                requireState(hosts.has(actor?.host) && id.test(actor.instance), 'invalid_worker');
                if (command.action !== 'register') requireState(id.test(command.taskId), 'invalid_task');
                const { requestId, action, taskId, epoch } = command;
                // Proof must come from trusted checkpoint/workspace adapters. Recheck the bound
                // phase, epoch and registration in the commit after any asynchronous verification.
                const previous = ['release', 'receive', 'complete'].includes(action) ? await read() : null;
                const offered = previous?.tasks[taskId];
                let checkpoint;
                let verified = false;
                if (action === 'release' && offered?.phase === 'draining' && holdsGrant(offered, actor, epoch)) {
                    requireState(validCheckpoint(command.checkpoint), 'invalid_checkpoint');
                    checkpoint = checkpointFields(command.checkpoint);
                    verified = await verifyRelease?.({ task: offered, actor, checkpoint }) === true;
                }
                if (action === 'receive' && offered && receivingWorker(previous, offered, actor) && offered.epoch === epoch) {
                    verified = await verifyReceive?.({ task: offered, actor, checkpoint: offered.checkpoint }) === true;
                }
                if (action === 'complete' && offered?.phase === 'active' && holdsGrant(offered, actor, epoch)) {
                    verified = await verifyCompletion?.({ task: offered, actor }) === true;
                }
                return root.commit(async (tx) => {
                    const state = await tx.doc(taskState, root.id);
                    if (action === 'register') {
                        if (state.workers[actor.host] !== actor.instance) {
                            requireState(!state.retired[actor.host].includes(actor.instance), 'retired_worker');
                            requireState(!Object.values(state.tasks).some((task) => task.owner?.host === actor.host), 'worker_still_owns_task');
                            if (state.workers[actor.host]) state.retired[actor.host].push(state.workers[actor.host]);
                            state.workers[actor.host] = actor.instance;
                        }
                        return { registered: true };
                    }
                    requireState(state.workers[actor.host] === actor.instance, 'unregistered_worker');
                    let task = state.tasks[taskId];
                    if (action === 'create' && !task) {
                        requireState(typeof command.goal === 'string' && command.goal.trim().length > 0 && command.goal.length <= 8192, 'invalid_goal');
                        task = { id: taskId, goal: command.goal, origin: actor.host, target: actor.host,
                            phase: 'ready', epoch: 0, owner: null, checkpoint: null, lastOperation: null, departedInstance: null };
                        state.tasks[taskId] = task;
                    }
                    requireState(task, 'task_not_found');
                    if (action === 'check') {
                        requireState(task.phase === 'active' && holdsGrant(task, actor, epoch), 'fenced');
                        return { allowed: true };
                    }
                    const operation = `${actor.host}/${actor.instance}/${action}/${requestId}`;
                    if (task.lastOperation === operation) return JSON.parse(JSON.stringify(task));
                    switch (action) {
                        case 'create':
                            requireState(task.lastOperation === null, 'task_exists');
                            break;
                        case 'start':
                            requireState(task.phase === 'ready' && task.target === actor.host && task.epoch === epoch, 'fenced');
                            task.owner = { ...actor };
                            task.phase = 'active';
                            task.epoch++;
                            break;
                        case 'drain': {
                            requireState(task.phase === 'active' && holdsGrant(task, actor, epoch), 'fenced');
                            requireState(task.origin === 'PC73', 'not_pc73_goal');
                            const target = actor.host === 'PC73' ? 'PC70' : 'PC73';
                            requireState(command.reason === (target === 'PC70' ? 'shutdown' : 'return'), 'invalid_reason');
                            requireState(state.workers[target], 'target_unregistered');
                            if (target === 'PC73') {
                                requireState(state.workers.PC73 !== task.departedInstance, 'pc73_not_restarted');
                            } else {
                                task.departedInstance = actor.instance;
                            }
                            task.target = target;
                            task.phase = 'draining';
                            break;
                        }
                        case 'release':
                            requireState(task.phase === 'draining' && holdsGrant(task, actor, epoch), 'fenced');
                            requireState(verified && task.target === offered.target, 'release_unverified');
                            task.checkpoint = checkpoint;
                            task.owner = null;
                            task.phase = 'offered';
                            break;
                        case 'receive':
                            requireState(receivingWorker(state, task, actor) && task.epoch === epoch, 'fenced');
                            requireState(verified, 'receive_unverified');
                            task.owner = { ...actor };
                            task.phase = 'active';
                            task.epoch++;
                            break;
                        case 'complete':
                            requireState(task.phase === 'active' && holdsGrant(task, actor, epoch), 'fenced');
                            requireState(verified, 'completion_unverified');
                            task.owner = null;
                            task.phase = 'complete';
                            break;
                        default:
                            throw new Error('unknown_action');
                    }
                    task.lastOperation = operation;
                    return JSON.parse(JSON.stringify(task));
                }, context);
            },
            async close() {
                try {
                    await harness.close(context);
                } finally {
                    lock.close();
                }
            },
        };
    } catch (error) {
        try {
            if (harness) await harness.close(context);
            else await database?.close();
        } finally {
            lock.close();
        }
        throw error;
    }
}
