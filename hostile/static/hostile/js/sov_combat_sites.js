((root) => {
    'use strict';

    const SOURCE_URL = 'https://support.eveonline.com/hc/en-us/articles/14996978906908-Sovereignty-Hub-Combat-Anomalies';
    const tables = {
        'non-drone': [
            [
                '2 Refuge;2 Den;1 Hidden Den',
                '1 Refuge;3 Den;1 Hidden Den;1 Forsaken Den;1 Forlorn Den;2 Rally Point;1 Hidden Rally Point',
                '2 Refuge;4 Den;3 Hidden Den;2 Forsaken Den;1 Forlorn Den;2 Rally Point;1 Hidden Rally Point',
                '2 Hub;3 Hidden Hub;2 Forsaken Hub',
                '3 Hub;3 Hidden Hub;2 Forsaken Hub;2 Forlorn Hub;1 Haven',
                '4 Hub;3 Hidden Hub;3 Forsaken Hub;3 Forlorn Hub;2 Haven',
            ],
            [
                '1 Refuge;2 Den;1 Hidden Den;1 Forsaken Den',
                '1 Refuge;3 Den;2 Hidden Den;1 Forsaken Den;1 Forlorn Den;2 Rally Point;1 Hidden Rally Point',
                '2 Refuge;4 Den;2 Hidden Den;2 Forsaken Den;1 Forlorn Den;2 Rally Point;2 Hidden Rally Point;1 Forsaken Rally Point',
                '2 Hub;2 Hidden Hub;2 Forsaken Hub;1 Forlorn Hub',
                '2 Hub;3 Hidden Hub;2 Forsaken Hub;2 Forlorn Hub;2 Haven',
                '2 Hub;3 Hidden Hub;3 Forsaken Hub;3 Forlorn Hub;4 Haven;1 Sanctum',
            ],
            [
                '1 Refuge;2 Den;2 Hidden Den;1 Forsaken Den',
                '1 Refuge;2 Den;2 Hidden Den;2 Forsaken Den;1 Forlorn Den;2 Rally Point;1 Hidden Rally Point',
                '1 Refuge;4 Den;2 Hidden Den;2 Forsaken Den;2 Forlorn Den;2 Rally Point;1 Hidden Rally Point;2 Forsaken Rally Point;1 Forlorn Rally Point',
                '2 Hidden Hub;2 Forsaken Hub;2 Forlorn Hub;1 Haven',
                '3 Hidden Hub;3 Forsaken Hub;3 Forlorn Hub;3 Haven',
                '3 Hidden Hub;3 Forsaken Hub;3 Forlorn Hub;6 Haven;2 Sanctum',
            ],
            [
                '2 Den;1 Hidden Den;2 Forsaken Den;1 Forlorn Den',
                '2 Den;2 Hidden Den;2 Forsaken Den;2 Forlorn Den;3 Rally Point;1 Hidden Rally Point',
                '2 Den;2 Hidden Den;2 Forsaken Den;2 Forlorn Den;4 Rally Point;2 Hidden Rally Point;2 Forsaken Rally Point;2 Forlorn Rally Point',
                '2 Hidden Hub;2 Forsaken Hub;2 Forlorn Hub;2 Haven',
                '2 Hidden Hub;2 Forsaken Hub;2 Forlorn Hub;4 Haven;2 Sanctum',
                '2 Hidden Hub;3 Forsaken Hub;2 Forlorn Hub;7 Haven;3 Sanctum;1 Forsaken Sanctum',
            ],
            [
                '2 Den;1 Hidden Den;2 Forsaken Den;2 Forlorn Den',
                '2 Den;2 Hidden Den;2 Forsaken Den;2 Forlorn Den;2 Rally Point;1 Hidden Rally Point;2 Forsaken Rally Point',
                '2 Den;2 Hidden Den;2 Forsaken Den;2 Forlorn Den;2 Rally Point;3 Hidden Rally Point;3 Forsaken Rally Point;3 Forlorn Rally Point',
                '3 Forsaken Hub;2 Forlorn Hub;2 Haven;1 Sanctum',
                '3 Forsaken Hub;3 Forlorn Hub;5 Haven;2 Sanctum',
                '2 Forsaken Hub;2 Forlorn Hub;8 Haven;4 Sanctum;3 Forsaken Sanctum',
            ],
        ],
        drone: [
            [
                '3 Drone Assembly;2 Drone Gathering',
                '2 Drone Assembly;3 Drone Gathering;3 Drone Surveillance;2 Drone Menagerie',
                '2 Drone Assembly;4 Drone Gathering;5 Drone Surveillance;3 Drone Menagerie;1 Drone Herd',
                '4 Drone Herd;3 Drone Squad',
                '5 Drone Herd;5 Drone Squad;1 Drone Patrol',
                '4 Drone Herd;6 Drone Squad;3 Drone Patrol;2 Drone Horde',
            ],
            [
                '2 Drone Assembly;2 Drone Gathering;1 Drone Surveillance',
                '2 Drone Assembly;3 Drone Gathering;3 Drone Surveillance;3 Drone Menagerie',
                '2 Drone Assembly;4 Drone Gathering;4 Drone Surveillance;3 Drone Menagerie;3 Drone Herd',
                '3 Drone Herd;4 Drone Squad',
                '4 Drone Herd;4 Drone Squad;3 Drone Patrol',
                '2 Drone Herd;4 Drone Squad;5 Drone Patrol;5 Drone Horde',
            ],
            [
                '2 Drone Assembly;3 Drone Gathering;1 Drone Surveillance',
                '2 Drone Assembly;3 Drone Gathering;3 Drone Surveillance;2 Drone Menagerie;1 Drone Herd',
                '2 Drone Assembly;3 Drone Gathering;4 Drone Surveillance;4 Drone Menagerie;3 Drone Herd;1 Drone Squad',
                '5 Drone Squad;2 Drone Patrol',
                '5 Drone Squad;6 Drone Patrol;1 Drone Horde',
                '2 Drone Squad;8 Drone Patrol;7 Drone Horde',
            ],
            [
                '1 Drone Assembly;2 Drone Gathering;2 Drone Surveillance;1 Drone Menagerie',
                '1 Drone Assembly;3 Drone Gathering;3 Drone Surveillance;3 Drone Menagerie;2 Drone Herd',
                '1 Drone Assembly;3 Drone Gathering;4 Drone Surveillance;4 Drone Menagerie;4 Drone Herd;2 Drone Squad',
                '4 Drone Squad;4 Drone Patrol',
                '2 Drone Squad;6 Drone Patrol;4 Drone Horde',
                '8 Drone Patrol;9 Drone Horde;1 Teeming Drone Horde',
            ],
            [
                '2 Drone Gathering;2 Drone Surveillance;3 Drone Menagerie',
                '3 Drone Gathering;3 Drone Surveillance;3 Drone Menagerie;3 Drone Herd;1 Drone Squad',
                '3 Drone Gathering;4 Drone Surveillance;5 Drone Menagerie;4 Drone Herd;3 Drone Squad',
                '3 Drone Squad;4 Drone Patrol;1 Drone Horde',
                '1 Drone Squad;7 Drone Patrol;5 Drone Horde',
                '6 Drone Patrol;10 Drone Horde;3 Teeming Drone Horde',
            ],
        ],
    };

    function combatSites(upgradeName, trueSecurity, region) {
        const match = typeof upgradeName === 'string' && /^(Minor|Major) Threat Detection Array ([123])$/i.exec(upgradeName.trim());
        if (!match) return { status: 'uncertain', reason: 'noncombat-or-unknown-upgrade' };
        if (region !== 'drone' && region !== 'non-drone') return { status: 'uncertain', reason: 'unknown-region' };
        if (typeof trueSecurity !== 'number' || !Number.isFinite(trueSecurity) || trueSecurity < -1 || trueSecurity > 0) {
            return { status: 'uncertain', reason: 'unknown-security' };
        }
        if ([-0.25, -0.45, -0.65, -0.85].includes(trueSecurity)) {
            return { status: 'uncertain', reason: 'security-boundary' };
        }
        const band = [-0.25, -0.45, -0.65, -0.85].filter((boundary) => trueSecurity < boundary).length;
        const column = (match[1].toLowerCase() === 'major' ? 3 : 0) + Number(match[2]) - 1;
        const rows = tables[region][band][column].split(';').map((entry) => {
            const space = entry.indexOf(' ');
            return { name: entry.slice(space + 1), count: Number(entry.slice(0, space)) };
        });
        return { status: 'ok', rows };
    }

    const api = { combatSites, SOURCE_URL };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.SovCombatSites = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);