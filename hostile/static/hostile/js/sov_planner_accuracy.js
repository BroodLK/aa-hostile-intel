((root) => {
    'use strict';

    function transferWarnings(capacity, incoming, outgoing) {
        if (!outgoing) return [];
        if (capacity === null) return ['export capacity is unknown'];
        if (outgoing > capacity + incoming) {
            return ['exports exceed local and incoming workforce; Mecha-Tooling workforce cannot be exported'];
        }
        return [];
    }

    function assessTransfers(plans, transfers) {
        const result = new Map();
        const visiting = new Set();
        function assess(id) {
            if (result.has(id)) return result.get(id);
            if (visiting.has(id)) return {status: 'unknown'};
            visiting.add(id);
            const incoming = transfers.filter((edge) => edge.to === id);
            const outgoing = transfers.filter((edge) => edge.from === id);
            const sources = new Set(incoming.map((edge) => edge.from));
            const destinations = new Set(outgoing.map((edge) => edge.to));
            const incomingStatus = sources.size > 3 || incoming.some((edge) => assess(edge.from).status !== 'valid') ? 'unknown' : 'valid';
            const inTotal = incoming.reduce((sum, edge) => sum + edge.amount, 0);
            const outTotal = outgoing.reduce((sum, edge) => sum + edge.amount, 0);
            const capacity = plans.get(id).workforce;
            const warnings = transferWarnings(capacity, inTotal, outTotal);
            if (incomingStatus === 'unknown') warnings.push('incoming transfers are uncertain');
            if (sources.size > 3) warnings.push('more than 3 source neighbors');
            if (destinations.size > 1) warnings.push('more than one export neighbor');
            if (incoming.length && outgoing.length) warnings.push('transit mode and local use require verification');
            const invalid = outTotal > 0 && capacity !== null && outTotal > capacity + inTotal;
            const status = invalid ? 'invalid' : warnings.length ? 'unknown' : 'valid';
            const assessment = {status, incomingStatus, warnings};
            visiting.delete(id);
            result.set(id, assessment);
            return assessment;
        }
        plans.forEach((_, id) => assess(id));
        return result;
    }

    const api = { transferWarnings, assessTransfers };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.SovPlannerAccuracy = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
