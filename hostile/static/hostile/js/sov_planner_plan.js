((root) => {
    'use strict';

    function parsePlan(text, catalog) {
        const plan = JSON.parse(text);
        if (!plan || plan.version !== 1 || !Array.isArray(plan.systems) || !Array.isArray(plan.transfers) || !Array.isArray(plan.hiddenNeighbors)) throw new Error('Invalid or unsupported plan format.');
        const id = (value) => String(value);
        const constellations = new Set(catalog.constellations.map((item) => id(item.id)));
        const systems = new Map(catalog.systems.map((item) => [id(item.id), item]));
        const upgrades = new Map(catalog.upgrades.map((item) => [id(item.id), item]));
        const constellation = id(plan.constellation);
        if (!constellations.has(constellation)) throw new Error('The saved constellation is not in the current catalog.');
        const selected = new Set();
        const normalizedSystems = plan.systems.map((entry) => {
            if (!entry || !Array.isArray(entry.upgrades)) throw new Error('Invalid saved system.');
            const systemId = id(entry.id);
            if (!systems.has(systemId) || selected.has(systemId)) throw new Error('A saved system is missing or duplicated.');
            selected.add(systemId);
            const chosen = new Set();
            const groups = new Set();
            const upgradeIds = entry.upgrades.map((value) => {
                const upgradeId = id(value), upgrade = upgrades.get(upgradeId);
                if (!upgrade || chosen.has(upgradeId)) throw new Error('A saved upgrade is unavailable or duplicated.');
                const group = upgrade.mutually_exclusive_group;
                if (group != null && group !== '' && groups.has(id(group))) throw new Error('Saved upgrades conflict.');
                chosen.add(upgradeId);
                if (group != null && group !== '') groups.add(id(group));
                return upgradeId;
            });
            return { id: systemId, upgrades: upgradeIds };
        });
        const linked = (a, b) => (systems.get(a).neighbors || []).some((neighbor) => id(neighbor) === b) || (systems.get(b).neighbors || []).some((neighbor) => id(neighbor) === a);
        const transfers = plan.transfers.map((entry) => {
            if (!entry) throw new Error('Invalid saved transfer.');
            const from = id(entry.from), to = id(entry.to), amount = entry.amount;
            if (!selected.has(from) || !selected.has(to) || from === to || !linked(from, to) || typeof amount !== 'number' || !Number.isFinite(amount) || amount <= 0) throw new Error('A saved transfer is invalid or no longer linked.');
            return { from, to, amount };
        });
        const hiddenNeighbors = plan.hiddenNeighbors.map(id);
        if (hiddenNeighbors.some((value) => !systems.has(value) || selected.has(value)) || new Set(hiddenNeighbors).size !== hiddenNeighbors.length) throw new Error('A hidden system is missing or duplicated.');
        return { version: 1, constellation, systems: normalizedSystems, transfers, hiddenNeighbors };
    }

    const api = { parsePlan };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.SovPlannerPlan = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);