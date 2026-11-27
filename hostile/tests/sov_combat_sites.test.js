const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { combatSites, SOURCE_URL } = require('../static/hostile/js/sov_combat_sites.js');

const expected = {
    'non-drone': [
        ['2 Refuge, 2 Den, 1 Hidden Den', '1 Refuge, 3 Den, 1 Hidden Den, 1 Forsaken Den, 1 Forlorn Den, 2 Rally Point, 1 Hidden Rally Point', '2 Refuge, 4 Den, 3 Hidden Den, 2 Forsaken Den, 1 Forlorn Den, 2 Rally Point, 1 Hidden Rally Point', '2 Hub, 3 Hidden Hub, 2 Forsaken Hub', '3 Hub, 3 Hidden Hub, 2 Forsaken Hub, 2 Forlorn Hub, 1 Haven', '4 Hub, 3 Hidden Hub, 3 Forsaken Hub, 3 Forlorn Hub, 2 Haven'],
        ['1 Refuge, 2 Den, 1 Hidden Den, 1 Forsaken Den', '1 Refuge, 3 Den, 2 Hidden Den, 1 Forsaken Den, 1 Forlorn Den, 2 Rally Point, 1 Hidden Rally Point', '2 Refuge, 4 Den, 2 Hidden Den, 2 Forsaken Den, 1 Forlorn Den, 2 Rally Point, 2 Hidden Rally Point, 1 Forsaken Rally Point', '2 Hub, 2 Hidden Hub, 2 Forsaken Hub, 1 Forlorn Hub', '2 Hub, 3 Hidden Hub, 2 Forsaken Hub, 2 Forlorn Hub, 2 Haven', '2 Hub, 3 Hidden Hub, 3 Forsaken Hub, 3 Forlorn Hub, 4 Haven, 1 Sanctum'],
        ['1 Refuge, 2 Den, 2 Hidden Den, 1 Forsaken Den', '1 Refuge, 2 Den, 2 Hidden Den, 2 Forsaken Den, 1 Forlorn Den, 2 Rally Point, 1 Hidden Rally Point', '1 Refuge, 4 Den, 2 Hidden Den, 2 Forsaken Den, 2 Forlorn Den, 2 Rally Point, 1 Hidden Rally Point, 2 Forsaken Rally Point, 1 Forlorn Rally Point', '2 Hidden Hub, 2 Forsaken Hub, 2 Forlorn Hub, 1 Haven', '3 Hidden Hub, 3 Forsaken Hub, 3 Forlorn Hub, 3 Haven', '3 Hidden Hub, 3 Forsaken Hub, 3 Forlorn Hub, 6 Haven, 2 Sanctum'],
        ['2 Den, 1 Hidden Den, 2 Forsaken Den, 1 Forlorn Den', '2 Den, 2 Hidden Den, 2 Forsaken Den, 2 Forlorn Den, 3 Rally Point, 1 Hidden Rally Point', '2 Den, 2 Hidden Den, 2 Forsaken Den, 2 Forlorn Den, 4 Rally Point, 2 Hidden Rally Point, 2 Forsaken Rally Point, 2 Forlorn Rally Point', '2 Hidden Hub, 2 Forsaken Hub, 2 Forlorn Hub, 2 Haven', '2 Hidden Hub, 2 Forsaken Hub, 2 Forlorn Hub, 4 Haven, 2 Sanctum', '2 Hidden Hub, 3 Forsaken Hub, 2 Forlorn Hub, 7 Haven, 3 Sanctum, 1 Forsaken Sanctum'],
        ['2 Den, 1 Hidden Den, 2 Forsaken Den, 2 Forlorn Den', '2 Den, 2 Hidden Den, 2 Forsaken Den, 2 Forlorn Den, 2 Rally Point, 1 Hidden Rally Point, 2 Forsaken Rally Point', '2 Den, 2 Hidden Den, 2 Forsaken Den, 2 Forlorn Den, 2 Rally Point, 3 Hidden Rally Point, 3 Forsaken Rally Point, 3 Forlorn Rally Point', '3 Forsaken Hub, 2 Forlorn Hub, 2 Haven, 1 Sanctum', '3 Forsaken Hub, 3 Forlorn Hub, 5 Haven, 2 Sanctum', '2 Forsaken Hub, 2 Forlorn Hub, 8 Haven, 4 Sanctum, 3 Forsaken Sanctum'],
    ],
    drone: [
        ['3 Drone Assembly, 2 Drone Gathering', '2 Drone Assembly, 3 Drone Gathering, 3 Drone Surveillance, 2 Drone Menagerie', '2 Drone Assembly, 4 Drone Gathering, 5 Drone Surveillance, 3 Drone Menagerie, 1 Drone Herd', '4 Drone Herd, 3 Drone Squad', '5 Drone Herd, 5 Drone Squad, 1 Drone Patrol', '4 Drone Herd, 6 Drone Squad, 3 Drone Patrol, 2 Drone Horde'],
        ['2 Drone Assembly, 2 Drone Gathering, 1 Drone Surveillance', '2 Drone Assembly, 3 Drone Gathering, 3 Drone Surveillance, 3 Drone Menagerie', '2 Drone Assembly, 4 Drone Gathering, 4 Drone Surveillance, 3 Drone Menagerie, 3 Drone Herd', '3 Drone Herd, 4 Drone Squad', '4 Drone Herd, 4 Drone Squad, 3 Drone Patrol', '2 Drone Herd, 4 Drone Squad, 5 Drone Patrol, 5 Drone Horde'],
        ['2 Drone Assembly, 3 Drone Gathering, 1 Drone Surveillance', '2 Drone Assembly, 3 Drone Gathering, 3 Drone Surveillance, 2 Drone Menagerie, 1 Drone Herd', '2 Drone Assembly, 3 Drone Gathering, 4 Drone Surveillance, 4 Drone Menagerie, 3 Drone Herd, 1 Drone Squad', '5 Drone Squad, 2 Drone Patrol', '5 Drone Squad, 6 Drone Patrol, 1 Drone Horde', '2 Drone Squad, 8 Drone Patrol, 7 Drone Horde'],
        ['1 Drone Assembly, 2 Drone Gathering, 2 Drone Surveillance, 1 Drone Menagerie', '1 Drone Assembly, 3 Drone Gathering, 3 Drone Surveillance, 3 Drone Menagerie, 2 Drone Herd', '1 Drone Assembly, 3 Drone Gathering, 4 Drone Surveillance, 4 Drone Menagerie, 4 Drone Herd, 2 Drone Squad', '4 Drone Squad, 4 Drone Patrol', '2 Drone Squad, 6 Drone Patrol, 4 Drone Horde', '8 Drone Patrol, 9 Drone Horde, 1 Teeming Drone Horde'],
        ['2 Drone Gathering, 2 Drone Surveillance, 3 Drone Menagerie', '3 Drone Gathering, 3 Drone Surveillance, 3 Drone Menagerie, 3 Drone Herd, 1 Drone Squad', '3 Drone Gathering, 4 Drone Surveillance, 5 Drone Menagerie, 4 Drone Herd, 3 Drone Squad', '3 Drone Squad, 4 Drone Patrol, 1 Drone Horde', '1 Drone Squad, 7 Drone Patrol, 5 Drone Horde', '6 Drone Patrol, 10 Drone Horde, 3 Teeming Drone Horde'],
    ],
};

test('all 60 published table cells match CCP', () => {
    const names = ['Minor', 'Major'].flatMap((kind) => [1, 2, 3].map((tier) => `${kind} Threat Detection Array ${tier}`));
    for (const [region, bands] of Object.entries(expected)) {
        bands.forEach((columns, band) => columns.forEach((cell, column) => {
            const result = combatSites(names[column], [-0.1, -0.3, -0.5, -0.7, -0.9][band], region);
            assert.equal(result.status, 'ok');
            assert.equal(result.rows.map(({ count, name }) => `${count} ${name}`).join(', '), cell, `${region} band ${band} ${names[column]}`);
        }));
    }
});

test('handles boundaries, unknowns, invalid values and noncombat upgrades explicitly', () => {
    const name = 'Minor Threat Detection Array 1';
    for (const boundary of [-0.25, -0.45, -0.65, -0.85]) {
        assert.equal(combatSites(name, boundary, 'drone').reason, 'security-boundary');
        assert.equal(combatSites(name, boundary + 0.000001, 'drone').status, 'ok');
        assert.equal(combatSites(name, boundary - 0.000001, 'drone').status, 'ok');
    }
    for (const value of [null, undefined, NaN, Infinity, '0', 0.1, -1.1]) {
        assert.equal(combatSites(name, value, 'drone').reason, 'unknown-security');
    }
    for (const region of [null, undefined, 'unknown', 'Drone', '__proto__']) {
        assert.equal(combatSites(name, -0.1, region).reason, 'unknown-region');
    }
    for (const upgrade of [null, '', 'Metenox Moon Drill', 'Minor Threat Detection Array 4']) {
        assert.equal(combatSites(upgrade, -0.1, 'drone').reason, 'noncombat-or-unknown-upgrade');
    }
    assert.equal(combatSites(name, 0, 'drone').status, 'ok');
    assert.equal(combatSites(name, -1, 'drone').status, 'ok');
    assert.equal(combatSites(' minor threat detection array 1 ', -0.1, 'drone').status, 'ok');
});

test('browser global is available and returns independent rows', () => {
    const context = {};
    vm.runInNewContext(fs.readFileSync(require.resolve('../static/hostile/js/sov_combat_sites.js'), 'utf8'), context);
    assert.equal(context.SovCombatSites.SOURCE_URL, SOURCE_URL);
    const first = context.SovCombatSites.combatSites('Major Threat Detection Array 3', -0.9, 'drone');
    first.rows[0].count = 0;
    assert.equal(context.SovCombatSites.combatSites('Major Threat Detection Array 3', -0.9, 'drone').rows[0].count, 6);
});