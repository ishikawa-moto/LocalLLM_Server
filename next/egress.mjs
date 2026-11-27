const fallback = Object.freeze({
    LOW: Object.freeze(['Terra', 'Haiku']),
    NORMAL: Object.freeze(['Terra', 'Haiku', 'Sol', 'Opus']),
    HIGH: Object.freeze(['Sol', 'Opus']),
});

export function delegationCandidates({ difficulty, bonsaiFailed, lowHaikuRevalidated }) {
    if (bonsaiFailed !== true || !Object.hasOwn(fallback, difficulty)) return [];
    return fallback[difficulty].filter((model) => difficulty !== 'LOW' || model !== 'Haiku' || lowHaikuRevalidated === true);
}

// Call from the trusted CLI broker immediately before launch. Its input must come
// from the classified packet, configured CLI and subscription, never model output.
export function authorizeDelegation(packet, approvals = []) {
    if (!packet || !['synthetic', 'public-source', 'approved-source-excerpt'].includes(packet.dataClass)
        || packet.containsRestrictedContent !== false || packet.auth !== 'subscription-cli'
        || packet.additionalApiCharges !== false) return false;

    return approvals.some((entry) => entry.decision === 'allow'
        && entry.model === packet.model && entry.cli === packet.cli && entry.destination === packet.destination
        && entry.auth === packet.auth && entry.additionalApiCharges === false
        && Array.isArray(entry.dataClasses) && entry.dataClasses.includes(packet.dataClass));
}
