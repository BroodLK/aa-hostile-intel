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

    const api = { transferWarnings };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.SovPlannerAccuracy = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
