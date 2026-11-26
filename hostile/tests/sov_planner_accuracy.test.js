const { test } = require('node:test');
const assert = require('node:assert/strict');
const { transferWarnings } = require('../static/hostile/js/sov_planner_accuracy.js');

test('identifies exports exceeding local and incoming workforce without counting upgrade production', () => {
    assert.deepEqual(transferWarnings(100, 20, 130), ['exports exceed local and incoming workforce; Mecha-Tooling workforce cannot be exported']);
    assert.deepEqual(transferWarnings(100, 20, 120), []);
});

test('reports unknown export capacity only when exporting', () => {
    assert.deepEqual(transferWarnings(null, 0, 1), ['export capacity is unknown']);
    assert.deepEqual(transferWarnings(100, 0, 0), []);
});