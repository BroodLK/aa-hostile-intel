((root) => {
    'use strict';

    function findRoute(systems, selected, transfers, from, to) {
        if (!selected.has(from) || !selected.has(to) || from === to) return null;
        const ids = [...selected.keys()];
        const neighbors = new Map(ids.map((id) => [id, []]));
        ids.forEach((id) => {
            const system = systems.get(id);
            if (!system) return;
            (system.neighbors || []).forEach((value) => {
                const other = String(value);
                if (other !== id && neighbors.has(other)) {
                    neighbors.get(id).push(other);
                    neighbors.get(other).push(id);
                }
            });
        });
        neighbors.forEach((values, id) => neighbors.set(id, [...new Set(values)].sort()));
        const outgoing = new Map(ids.map((id) => [id, new Set()]));
        const incoming = new Map(ids.map((id) => [id, new Set()]));
        transfers.forEach((edge) => {
            if (outgoing.has(edge.from) && incoming.has(edge.to)) {
                outgoing.get(edge.from).add(edge.to);
                incoming.get(edge.to).add(edge.from);
            }
        });
        // Adding an edge must not create a cycle in the existing transfer graph.
        function reaches(start, target, path) {
            const seen = new Set(), pending = [start];
            while (pending.length) {
                const id = pending.pop();
                if (id === target) return true;
                if (seen.has(id)) continue;
                seen.add(id);
                outgoing.get(id).forEach((next) => pending.push(next));
                const index = path.indexOf(id);
                if (index >= 0 && index < path.length - 1) pending.push(path[index + 1]);
            }
            return false;
        }
        const queue = [[from]];
        for (let i = 0; i < queue.length; i++) {
            const path = queue[i], current = path[path.length - 1];
            for (const next of neighbors.get(current)) {
                if (path.includes(next) || (outgoing.get(current).size && !outgoing.get(current).has(next)) || (!incoming.get(next).has(current) && incoming.get(next).size >= 3) || reaches(next, current, path)) continue;
                const route = [...path, next];
                if (next === to) return route;
                queue.push(route);
            }
        }
        return null;
    }

    const api = { findRoute };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.SovPlannerRoutes = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
