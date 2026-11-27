const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Reuse the interaction suite's DOM harness without changing that suite.
let harness = fs.readFileSync(path.join(__dirname, 'sov_planner_interaction.test.js'), 'utf8');
harness = harness.slice(0, harness.indexOf("test('selecting a constellation"));
harness = harness.replace("'sov-controls'", "'sov-controls', 'sov-zoom-in', 'sov-zoom-out', 'sov-zoom-reset', 'sov-zoom-level'");
harness = harness.replace('async function setup()', 'async function setup(extraSystems = [])');
harness = harness.replace('    const document =', '    catalog.systems.push(...extraSystems);\n    catalog.systems[0].neighbors.push(...extraSystems.map((system) => system.id));\n    const document =');
harness += '\nmodule.exports = { setup, descendants };';
const harnessModule = {exports: {}};
vm.runInThisContext(`(function(require, module, __dirname) { ${harness}\n})`)(require, harnessModule, __dirname);
const {setup, descendants} = harnessModule.exports;

test('zoom remains bounded and preserves map interactions and viewport on rerender', async () => {
    const {get, findButton, mapNode} = await setup([{id: 3, name: 'Gamma', constellation_id: 2, neighbors: [1], x_2d: 2, y_2d: 0}]);
    const map = get('sov-map');
    map.clientWidth = 400;
    map.clientHeight = 200;
    map.scrollLeft = 100;
    map.scrollTop = 50;
    get('sov-zoom-in').click();
    assert.equal(get('sov-zoom-level').textContent, '125%');
    assert.equal(map.children[0].children[0].style.transform, 'scale(1.25)');
    const left = map.scrollLeft;
    findButton(map, 'Add Gamma').click();
    assert.equal(map.scrollLeft, left);
    assert.ok(mapNode('3'));
    for (let i = 0; i < 20; i++) get('sov-zoom-out').click();
    assert.equal(get('sov-zoom-level').textContent, '50%');
    assert.equal(get('sov-zoom-out').disabled, true);
    get('sov-zoom-reset').click();
    assert.equal(get('sov-zoom-level').textContent, '100%');
});

test('close systems expand uniformly and coincident systems use the separate row', async () => {
    const extra = [
        {id: 3, name: 'Close', x_2d: 0.02, y_2d: 0, neighbors: [1]},
        {id: 4, name: 'Coincident', x_2d: 0, y_2d: 0, neighbors: [1]},
    ];
    const {get, findButton} = await setup(extra);
    const map = get('sov-map');
    const wrappers = descendants(map).filter((item) => item.style.left);
    const alpha = wrappers.find((item) => item.textContent.includes('Alpha'));
    const close = wrappers.find((item) => item.textContent.includes('Close'));
    const duplicate = wrappers.find((item) => item.textContent.includes('Coincident'));
    assert.ok(parseFloat(close.style.left) - parseFloat(alpha.style.left) >= 320);
    assert.equal(close.style.top, alpha.style.top);
    assert.ok(parseFloat(duplicate.style.top) > parseFloat(alpha.style.top));
    findButton(map, 'Add Close').click();
    assert.ok(descendants(map).some((item) => item.dataset.systemId === '3'));
});

test('background pointer dragging pans but buttons do not start a drag', async () => {
    const {get} = await setup();
    const map = get('sov-map');
    map.scrollLeft = 200;
    map.scrollTop = 100;
    map.setPointerCapture = () => {
    };
    const fire = (type, event) => map.listeners[type].forEach((fn) => fn(event));
    fire('pointerdown', {button: 0, pointerId: 1, clientX: 50, clientY: 50, target: {closest: () => null}});
    fire('pointermove', {pointerId: 1, clientX: 30, clientY: 20});
    assert.equal(map.scrollLeft, 220);
    assert.equal(map.scrollTop, 130);
    fire('pointerup', {pointerId: 1});
    fire('pointerdown', {button: 0, pointerId: 2, clientX: 0, clientY: 0, target: {closest: () => ({})}});
    fire('pointermove', {pointerId: 2, clientX: 30, clientY: 20});
    assert.equal(map.scrollLeft, 220);
});
