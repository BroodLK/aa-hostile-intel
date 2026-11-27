const { test } = require('node:test');
const assert = require('node:assert/strict');
const { transferWarnings, assessTransfers } = require('../static/hostile/js/sov_planner_accuracy.js');

test('identifies exports exceeding local and incoming workforce without counting upgrade production', () => {
    assert.deepEqual(transferWarnings(100, 20, 130), ['exports exceed local and incoming workforce; Mecha-Tooling workforce cannot be exported']);
    assert.deepEqual(transferWarnings(100, 20, 120), []);
});

test('reports unknown export capacity only when exporting', () => {
    assert.deepEqual(transferWarnings(null, 0, 1), ['export capacity is unknown']);
    assert.deepEqual(transferWarnings(100, 0, 0), []);
});

test('propagates impossible and unknown transfers through chains without trusting cycles', () => {
    const plans = new Map([['a', {workforce: 10}], ['b', {workforce: 0}], ['c', {workforce: 0}]]);
    const edges = [{from: 'a', to: 'b', amount: 20}, {from: 'b', to: 'c', amount: 20}];
    let result = assessTransfers(plans, edges);
    assert.equal(result.get('a').status, 'invalid');
    assert.equal(result.get('b').incomingStatus, 'unknown');
    assert.equal(result.get('c').incomingStatus, 'unknown');
    plans.get('a').workforce = null;
    result = assessTransfers(plans, edges);
    assert.equal(result.get('a').status, 'unknown');
    assert.equal(result.get('c').incomingStatus, 'unknown');
    result = assessTransfers(plans, [{from: 'a', to: 'b', amount: 1}, {from: 'b', to: 'a', amount: 1}]);
    assert.equal(result.get('a').incomingStatus, 'unknown');
    assert.equal(result.get('b').incomingStatus, 'unknown');
});

test('treats transit modes and routing limits conservatively', () => {
    const plans = new Map(['a', 'b', 'c', 'd', 'e'].map((id) => [id, {workforce: 100}]));
    const result = assessTransfers(plans, [{from: 'a', to: 'b', amount: 10}, {from: 'b', to: 'c', amount: 10}]);
    assert.equal(result.get('a').status, 'valid');
    assert.equal(result.get('b').status, 'unknown');
    assert.equal(result.get('c').incomingStatus, 'unknown');
    const limited = assessTransfers(plans, ['a', 'b', 'c', 'd'].map((from) => ({from, to: 'e', amount: 1})));
    assert.equal(limited.get('e').incomingStatus, 'unknown');
});
