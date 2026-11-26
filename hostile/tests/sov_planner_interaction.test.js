const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const accuracy = require('../static/hostile/js/sov_planner_accuracy.js');

class Element {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.dataset = {};
        this.style = {};
        this.attributes = {};
        this.listeners = {};
        this.value = '';
        this.text = '';
        this.className = '';
    }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map((child) => child.textContent).join(''); }
    append(...children) { this.children.push(...children); }
    prepend(child) { this.children.unshift(child); }
    replaceChildren(...children) { this.text = ''; this.children = children; if (this.tagName === 'SELECT') this.value = children[0]?.value || ''; }
    add(child) { this.append(child); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    dispatch(name) { for (const callback of this.listeners[name] || []) callback({ target: this }); }
    click() { this.dispatch('click'); }
    focus() {}
    showModal() { this.open = true; }
    close() { this.open = false; this.dispatch('close'); }
    querySelector(selector) {
        const match = selector.match(/^\[data-system-id="(.*)"]$/);
        return match ? descendants(this).find((child) => child.dataset.systemId === match[1]) || null : null;
    }
}

function descendants(element) {
    return element.children.flatMap((child) => [child, ...descendants(child)]);
}

async function setup() {
    const ids = ['sov-planner', 'sov-error', 'sov-loading', 'sov-retry', 'sov-constellation', 'sov-system', 'sov-from', 'sov-to', 'sov-transfer-message', 'sov-upgrade-dialog', 'sov-upgrade-search', 'sov-dialog-title', 'sov-dialog-selected', 'sov-dialog-available', 'sov-dialog-actions', 'sov-dialog-close', 'sov-map', 'sov-map-details', 'sov-systems', 'sov-add-system', 'sov-catalog-warning', 'sov-transfers', 'sov-add-transfer', 'sov-amount', 'sov-controls'];
    const elements = Object.fromEntries(ids.map((id) => [id, new Element(['sov-constellation', 'sov-system', 'sov-from', 'sov-to'].includes(id) ? 'select' : 'div')]));
    elements['sov-planner'].dataset.catalogUrl = '/catalog';
    const catalog = {
        constellations: [{ id: 1, name: 'Constellation' }],
        systems: [
            { id: 1, name: 'Alpha', constellation_id: 1, neighbors: [2], power: 100, workforce: 100, x_2d: 0, y_2d: 0 },
            { id: 2, name: 'Beta', constellation_id: 1, neighbors: [1], power: null, workforce: 40, x_2d: 1, y_2d: 0 },
        ],
        upgrades: [
            { id: 1, name: 'Factory', description: 'Makes things', power: 80, workforce: 30, power_production: 0, workforce_production: 0 },
            { id: 2, name: 'Generator', description: 'Produces power', power: 0, workforce: 0, power_production: 30, workforce_production: 0 },
            { id: 3, name: 'Unknown cost', description: '', power: null, workforce: 0, power_production: 0, workforce_production: 0 },
        ],
    };
    const document = { getElementById: (id) => elements[id], createElement: (tag) => new Element(tag), createElementNS: (_, tag) => new Element(tag) };
    class Option extends Element { constructor(label, value) { super('option'); this.textContent = label; this.value = value; } }
    const source = fs.readFileSync(path.join(__dirname, '../static/hostile/js/sov_planner.js'), 'utf8');
    vm.runInNewContext(source, { document, Option, CSS: { escape: (value) => value }, DOMParser: class { parseFromString(text) { return { body: { textContent: text } }; } }, window: { SovPlannerAccuracy: accuracy }, fetch: async () => ({ ok: true, redirected: false, json: async () => catalog }) });
    await new Promise((resolve) => setImmediate(resolve));
    const get = (id) => elements[id];
    const findButton = (parent, text) => {
        const result = descendants(parent).find((item) => item.tagName === 'BUTTON' && item.textContent === text);
        assert.ok(result, `Missing button: ${text}`);
        return result;
    };
    const mapNode = (id) => descendants(get('sov-map')).find((item) => item.dataset.systemId === id);
    get('sov-constellation').value = '1';
    get('sov-constellation').dispatch('change');
    get('sov-system').value = '1';
    get('sov-add-system').click();
    return { get, findButton, mapNode };
}

test('dialog searches, adds and removes upgrades with read-only detailed cards', async () => {
    const { get, findButton, mapNode } = await setup();
    mapNode('1').click();
    assert.equal(get('sov-upgrade-dialog').open, true);
    get('sov-upgrade-search').value = 'fact';
    get('sov-upgrade-search').dispatch('input');
    assert.equal(descendants(get('sov-dialog-available')).filter((item) => item.tagName === 'BUTTON').length, 1);
    findButton(get('sov-dialog-available'), 'Add Factory').click();
    assert.match(get('sov-systems').textContent, /Makes things/);
    assert.match(get('sov-systems').textContent, /Power cost: 80/);
    assert.match(mapNode('1').textContent, /Power: 20 \| Workforce: 70/);
    assert.match(mapNode('1').className, /btn-outline-success/);
    assert.equal(descendants(get('sov-systems')).filter((item) => ['BUTTON', 'INPUT', 'SELECT'].includes(item.tagName)).length, 0);
    findButton(get('sov-dialog-selected'), 'Remove Factory').click();
    assert.match(get('sov-systems').textContent, /No upgrades selected/);
    assert.match(mapNode('1').textContent, /Power: 100 \| Workforce: 100/);
});

test('map colors track production, transfers, unknown values and known deficits', async () => {
    const { get, findButton, mapNode } = await setup();
    findButton(get('sov-map'), 'Add Beta').click();
    assert.match(mapNode('2').className, /btn-outline-secondary/);
    mapNode('1').click();
    findButton(get('sov-dialog-available'), 'Add Factory').click();
    findButton(get('sov-dialog-available'), 'Add Generator').click();
    assert.match(mapNode('1').textContent, /Power: 50 \| Workforce: 70/);
    get('sov-dialog-close').click();
    get('sov-from').value = '2';
    get('sov-to').value = '1';
    get('sov-amount').value = '50';
    get('sov-add-transfer').click();
    assert.match(mapNode('1').textContent, /Workforce: 120/);
    assert.match(mapNode('2').textContent, /Power: Unknown \| Workforce: -10 \(deficit\)/);
    assert.match(mapNode('2').className, /btn-outline-danger/);
    findButton(get('sov-transfers'), 'Remove').click();
    assert.match(mapNode('2').className, /btn-outline-secondary/);
    mapNode('1').click();
    findButton(get('sov-dialog-available'), 'Add Unknown cost').click();
    assert.match(mapNode('1').className, /btn-outline-secondary/);
});

test('adjacent systems can be hidden without preventing selection', async () => {
    const { get, findButton, mapNode } = await setup();
    findButton(get('sov-map'), 'Hide Beta').click();
    assert.equal(descendants(get('sov-map')).some((item) => item.textContent === 'Add Beta'), false);
    assert.ok(descendants(get('sov-system')).some((item) => item.value === '2'));
    get('sov-system').value = '2';
    get('sov-add-system').click();
    assert.ok(mapNode('2'));
    mapNode('2').click();
    findButton(get('sov-dialog-actions'), 'Remove system from plan').click();
    assert.ok(findButton(get('sov-map'), 'Add Beta'));
});