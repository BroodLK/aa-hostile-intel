const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parsePlan } = require('../static/hostile/js/sov_planner_plan.js');

const catalog = {
    constellations: [{ id: 1 }],
    systems: [{ id: 1, neighbors: [2] }, { id: 2, neighbors: [1] }, { id: 3, neighbors: [] }],
    upgrades: [{ id: 10, mutually_exclusive_group: 'x' }, { id: 11, mutually_exclusive_group: 'x' }],
};
const plan = { version: 1, constellation: 1, systems: [{ id: 1, upgrades: [10] }, { id: 2, upgrades: [] }], transfers: [{ from: 1, to: 2, amount: 25 }], hiddenNeighbors: [3] };

test('plans round trip with normalized catalog IDs', () => {
    const result = parsePlan(JSON.stringify(plan), catalog);
    assert.deepEqual(result.systems, [{ id: '1', upgrades: ['10'] }, { id: '2', upgrades: [] }]);
    assert.deepEqual(result.transfers, [{ from: '1', to: '2', amount: 25 }]);
    assert.deepEqual(result.hiddenNeighbors, ['3']);
});

test('rejects invalid or outdated plans without partial application', () => {
    const invalid = [
        { ...plan, version: 2 },
        { ...plan, systems: [{ id: 1, upgrades: [999] }] },
        { ...plan, systems: [{ id: 1, upgrades: [10, 11] }] },
        { ...plan, transfers: [{ from: 1, to: 3, amount: 2 }] },
        { ...plan, transfers: [{ from: 1, to: 2, amount: -1 }] },
        { ...plan, hiddenNeighbors: [1] },
    ];
    invalid.forEach((value) => assert.throws(() => parsePlan(JSON.stringify(value), catalog)));
    assert.throws(() => parsePlan('{', catalog));
});