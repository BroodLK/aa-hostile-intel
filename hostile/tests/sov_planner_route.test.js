const { test } = require('node:test');
const assert = require('node:assert/strict');
const { findRoute } = require('../static/hostile/js/sov_planner_routes.js');

function graph(adjacency, selectedIds = Object.keys(adjacency)) {
    return {
        systems: new Map(Object.entries(adjacency).map(([id, neighbors]) => [id, {id, neighbors}])),
        selected: new Map(selectedIds.map((id) => [id, {}])),
    };
}

test('finds direct and shortest multihop routes through selected systems', () => {
    const { systems, selected } = graph({a: ['b', 'c'], b: ['a', 'd'], c: ['a', 'e'], d: ['b', 'e'], e: ['c', 'd']});
    assert.deepEqual(findRoute(systems, selected, [], 'a', 'b'), ['a', 'b']);
    assert.deepEqual(findRoute(systems, selected, [], 'a', 'd'), ['a', 'b', 'd']);
});

test('returns null for disconnected or unselected endpoints and intermediates', () => {
    const { systems, selected } = graph({a: ['b'], b: ['a', 'c'], c: ['b'], d: []}, ['a', 'c', 'd']);
    assert.equal(findRoute(systems, selected, [], 'a', 'c'), null);
    assert.equal(findRoute(systems, selected, [], 'a', 'd'), null);
    assert.equal(findRoute(systems, selected, [], 'a', 'b'), null);
    assert.equal(findRoute(systems, selected, [], 'b', 'a'), null);
});

test('reuses compatible directed transfers without treating them as conflicts', () => {
    const { systems, selected } = graph({a: ['b'], b: ['a', 'c'], c: ['b']});
    const transfers = [{from: 'a', to: 'b', amount: 10}, {from: 'b', to: 'c', amount: 10}];
    assert.deepEqual(findRoute(systems, selected, transfers, 'a', 'c'), ['a', 'b', 'c']);
});

test('avoids an intermediate with an incompatible existing outbound transfer', () => {
    const { systems, selected } = graph({a: ['b'], b: ['a', 'c', 'x'], c: ['b'], x: ['b']});
    assert.equal(findRoute(systems, selected, [{from: 'b', to: 'x', amount: 1}], 'a', 'c'), null);
});

test('enforces the limit of three distinct incoming neighbors, including the new edge', () => {
    const { systems, selected } = graph({a: ['b'], b: ['a', 'c', 'x', 'y', 'z'], c: ['b'], x: ['b'], y: ['b'], z: ['b']});
    const transfers = ['x', 'y', 'z'].map((from) => ({from, to: 'b', amount: 1}));
    assert.equal(findRoute(systems, selected, transfers, 'a', 'c'), null);
    assert.deepEqual(findRoute(systems, selected, transfers.slice(0, 2), 'a', 'c'), ['a', 'b', 'c']);
    assert.deepEqual(findRoute(systems, selected, [...transfers, {from: 'a', to: 'b', amount: 1}].slice(1), 'a', 'c'), ['a', 'b', 'c']);
});

test('selects a deterministic shortest alternative when the other branch conflicts', () => {
    const { systems, selected } = graph({a: ['b', 'c'], b: ['a', 'd', 'x'], c: ['a', 'd'], d: ['b', 'c'], x: ['b']});
    const transfers = [{from: 'b', to: 'x', amount: 1}];
    for (let i = 0; i < 3; i += 1) {
        assert.deepEqual(findRoute(systems, selected, transfers, 'a', 'd'), ['a', 'c', 'd']);
    }
});

test('does not create a directed cycle through existing transfers', () => {
    const { systems, selected } = graph({a: ['b'], b: ['a', 'c'], c: ['b']});
    assert.equal(findRoute(systems, selected, [{from: 'c', to: 'b', amount: 1}, {from: 'b', to: 'a', amount: 1}], 'a', 'c'), null);
});

test('detects cycles involving multiple newly planned hops', () => {
    const { systems, selected } = graph({a: ['b', 'c'], b: ['a', 'c'], c: ['a', 'b']});
    assert.equal(findRoute(systems, selected, [{from: 'c', to: 'a', amount: 1}], 'a', 'c'), null);
});

test('terminates on cyclic topology and returns a simple shortest route', () => {
    const { systems, selected } = graph({a: ['b', 'c'], b: ['a', 'c'], c: ['a', 'b', 'd'], d: ['c']});
    assert.deepEqual(findRoute(systems, selected, [], 'a', 'd'), ['a', 'c', 'd']);
});
