(() => {
    'use strict';

    const root = document.getElementById('sov-planner');
    if (!root) return;
    const el = (id) => document.getElementById(id);
    const error = el('sov-error');
    const loading = el('sov-loading');
    const retry = el('sov-retry');
    function showLoadError() {
        error.textContent = 'Could not load planner data. Please try again or check your connection and sign-in.';
        error.hidden = false;
        retry.hidden = false;
    }
    async function load() {
        loading.hidden = false;
        error.hidden = true;
        retry.hidden = true;
        root.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(root.dataset.catalogUrl, { credentials: 'same-origin' });
            if (!response.ok || response.redirected) {
                showLoadError();
                return;
            }
            const data = await response.json();
            initialize(data);
        } catch (e) {
            showLoadError();
        } finally {
            loading.hidden = true;
            root.setAttribute('aria-busy', 'false');
        }
    }
    function initialize(data) {
    if (!data || !Array.isArray(data.constellations) || !Array.isArray(data.systems) || !Array.isArray(data.upgrades) ||
        ![...data.constellations, ...data.systems, ...data.upgrades].every((item) => item && item.id != null && typeof item.name === 'string')) {
        throw new Error('Invalid planner catalog');
    }

    const key = (value) => String(value);
    const number = (value) => (typeof value !== 'number' && typeof value !== 'string') || (typeof value === 'string' && !value.trim()) || !Number.isFinite(Number(value)) || Number(value) < 0 ? null : Number(value);
    const systems = new Map(data.systems.map((system) => [key(system.id), system]));
    const selected = new Map();
    const hiddenNeighbors = new Set();
    let activeSystem = null;
    let transfers = [];
    const constellation = el('sov-constellation');
    const systemSelect = el('sov-system');
    const from = el('sov-from');
    const to = el('sov-to');
    const message = el('sov-transfer-message');
    const dialog = el('sov-upgrade-dialog');
    const search = el('sov-upgrade-search');
    let dialogSystem = null;

    function node(tag, text, className) {
        const result = document.createElement(tag);
        if (text != null) result.textContent = text;
        if (className) result.className = className;
        return result;
    }
    function options(select, items, placeholder) {
        const previous = select.value;
        select.replaceChildren(new Option(placeholder, ''));
        items.forEach((item) => select.add(new Option(item.name, key(item.id))));
        if (items.some((item) => key(item.id) === previous)) select.value = previous;
        if (select.syncSearchableSelect) select.syncSearchableSelect();
    }
    function linked(a, b) {
        const first = systems.get(a);
        const second = systems.get(b);
        return !!first && !!second && ((Array.isArray(first.neighbors) && first.neighbors.some((id) => key(id) === b)) || (Array.isArray(second.neighbors) && second.neighbors.some((id) => key(id) === a)));
    }
    function button(label, action) {
        const result = node('button', label, 'btn btn-outline-secondary btn-sm');
        result.type = 'button';
        result.addEventListener('click', action);
        return result;
    }
    function balance(capacity, costs, production, adjustment) {
        if (number(capacity) === null || [...costs, ...production].some((value) => number(value) === null)) return null;
        return Number(capacity) - costs.reduce((sum, cost) => sum + Number(cost), 0) + production.reduce((sum, value) => sum + Number(value), 0) + adjustment;
    }
    const display = (value) => value === null ? 'Unknown' : `${value}${value < 0 ? ' (deficit)' : ''}`;
    function balances(id, plan) {
        const incoming = transfers.filter((transfer) => transfer.to === id).reduce((sum, transfer) => sum + transfer.amount, 0);
        const outgoing = transfers.filter((transfer) => transfer.from === id).reduce((sum, transfer) => sum + transfer.amount, 0);
        return {
            incoming, outgoing,
            power: balance(plan.power, plan.upgrades.map((upgrade) => upgrade.power), plan.upgrades.map((upgrade) => upgrade.power_production), 0),
            workforce: balance(plan.workforce, plan.upgrades.map((upgrade) => upgrade.workforce), plan.upgrades.map((upgrade) => upgrade.workforce_production), incoming - outgoing),
        };
    }
    function upgradeBlurb(upgrade) {
        const description = upgrade.description ? new DOMParser().parseFromString(upgrade.description, 'text/html').body.textContent.trim() : '';
        return `${upgrade.name}: ${description || 'No SDE description available.'}`;
    }
    function addSystem(id) {
        const system = systems.get(id);
        if (!system || selected.has(id)) return;
        hiddenNeighbors.delete(id);
        selected.set(id, { power: number(system.power), workforce: number(system.workforce), upgrades: [] });
        activeSystem = id;
        render();
    }
    function renderDialog() {
        const id = dialogSystem;
        const plan = selected.get(id);
        if (!plan) return;
        el('sov-dialog-title').textContent = `Manage upgrades: ${systems.get(id).name}`;
        const chosen = el('sov-dialog-selected');
        chosen.replaceChildren(node('h6', 'Selected upgrades'));
        if (!plan.upgrades.length) chosen.append(node('p', 'No upgrades selected.', 'small text-secondary'));
        plan.upgrades.forEach((upgrade, index) => {
            const row = node('div', null, 'd-flex justify-content-between gap-2 mb-2');
            row.append(node('span', upgrade.name), button(`Remove ${upgrade.name}`, () => {
                plan.upgrades.splice(index, 1);
                render();
                renderDialog();
                search.focus();
            }));
            chosen.append(row);
        });
        const available = el('sov-dialog-available');
        available.replaceChildren();
        const eligible = data.upgrades.filter((upgrade) => !plan.upgrades.some((chosenUpgrade) => key(chosenUpgrade.id) === key(upgrade.id) || (upgrade.mutually_exclusive_group != null && upgrade.mutually_exclusive_group !== '' && key(chosenUpgrade.mutually_exclusive_group) === key(upgrade.mutually_exclusive_group))) && upgrade.name.toLowerCase().includes(search.value.trim().toLowerCase()));
        if (!eligible.length) available.append(node('p', 'No available upgrades match.', 'small text-secondary'));
        eligible.forEach((upgrade) => {
            const add = button(`Add ${upgrade.name}`, () => {
                plan.upgrades.push(upgrade);
                render();
                renderDialog();
                search.focus();
            });
            add.className += ' d-block mb-1';
            available.append(add);
        });
        const actions = el('sov-dialog-actions');
        actions.replaceChildren(button('Remove system from plan', () => {
            selected.delete(id);
            if (activeSystem === id) activeSystem = selected.keys().next().value || null;
            transfers = transfers.filter((transfer) => transfer.from !== id && transfer.to !== id);
            dialog.close();
            render();
            el('sov-add-system').focus();
        }));
    }
    function openDialog(id) {
        activeSystem = id;
        dialogSystem = id;
        search.value = '';
        renderDialog();
        dialog.showModal();
        search.focus();
    }
    search.addEventListener('input', renderDialog);
    el('sov-dialog-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        const mapButton = el('sov-map').querySelector(`[data-system-id="${CSS.escape(dialogSystem)}"]`);
        if (mapButton) mapButton.focus();
    });
    function renderMap(available) {
        const container = el('sov-map');
        const details = el('sov-map-details');
        container.replaceChildren();
        details.replaceChildren();
        if (!selected.size) {
            container.append(node('p', 'Select a starting system to view its neighbors.', 'text-secondary mb-0'));
            return;
        }
        const visible = [...selected.keys(), ...available.map((system) => key(system.id)).filter((id) => !hiddenNeighbors.has(id))];
        const positioned = [], missing = [], seenPositions = new Set();
        visible.forEach((id) => {
            const system = systems.get(id);
            const coordinate = `${system.x_2d},${system.y_2d}`;
            if (Number.isFinite(system.x_2d) && Number.isFinite(system.y_2d) && !seenPositions.has(coordinate)) {
                positioned.push(id);
                seenPositions.add(coordinate);
            } else {
                missing.push(id);
            }
        });
        const positions = new Map();
        const marginX = 120, marginY = 45;
        let width = 460, height = 180;
        if (positioned.length) {
            const xs = positioned.map((id) => systems.get(id).x_2d);
            const ys = positioned.map((id) => systems.get(id).y_2d);
            const minX = Math.min(...xs), maxX = Math.max(...xs);
            const minY = Math.min(...ys), maxY = Math.max(...ys);
            const spanX = maxX - minX, spanY = maxY - minY;
            const span = Math.max(spanX, spanY);
            const scale = span > 0 && Number.isFinite(span) ? 600 / span : 0;
            width = Math.max(width, spanX * scale + marginX * 2);
            height = Math.max(height, spanY * scale + marginY * 2);
            positioned.forEach((id) => {
                const system = systems.get(id);
                positions.set(id, { x: marginX + (system.x_2d - minX) * scale, y: marginY + (system.y_2d - minY) * scale });
            });
        }
        if (missing.length) {
            const startY = positioned.length ? height + 45 : marginY;
            missing.forEach((id, index) => positions.set(id, { x: marginX + (index % 4) * 220, y: startY + Math.floor(index / 4) * 90 }));
            width = Math.max(width, Math.min(missing.length, 4) * 220 + 20);
            height = startY + Math.ceil(missing.length / 4) * 90;
        }
        const map = node('div', null, 'position-relative');
        map.style.width = `${width}px`;
        map.style.height = `${height}px`;
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('width', width);
        svg.setAttribute('height', height);
        svg.setAttribute('aria-hidden', 'true');
        svg.style.position = 'absolute';
        visible.forEach((id, index) => visible.slice(index + 1).forEach((other) => {
            if (!linked(id, other)) return;
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            const a = positions.get(id), b = positions.get(other);
            [['x1', a.x], ['y1', a.y], ['x2', b.x], ['y2', b.y], ['stroke', '#6c757d'], ['stroke-width', '2']].forEach(([name, value]) => line.setAttribute(name, value));
            svg.append(line);
        }));
        map.append(svg);
        visible.forEach((id) => {
            const isSelected = selected.has(id);
            const item = isSelected ? button(systems.get(id).name, () => openDialog(id)) : button(`Add ${systems.get(id).name}`, () => addSystem(id));
            if (isSelected) {
                const remaining = balances(id, selected.get(id));
                const state = remaining.power < 0 || remaining.workforce < 0 ? 'danger' : remaining.power === null || remaining.workforce === null ? 'secondary' : 'success';
                item.className = `btn btn-sm btn-outline-${state}`;
                item.dataset.systemId = id;
                item.append(node('span', `Power: ${display(remaining.power)} | Workforce: ${display(remaining.workforce)}`, 'd-block'));
                const blurbs = selected.get(id).upgrades.map(upgradeBlurb);
                item.title = blurbs.length ? blurbs.join('\n') : 'No upgrades selected';
                item.setAttribute('aria-label', `${systems.get(id).name}, power remaining ${display(remaining.power)}, workforce remaining ${display(remaining.workforce)}. Manage upgrades`);
            }
            const position = positions.get(id);
            const wrapper = node('div', null, 'd-flex align-items-center gap-1');
            wrapper.style.position = 'absolute';
            wrapper.style.left = `${position.x}px`;
            wrapper.style.top = `${position.y}px`;
            wrapper.style.transform = 'translate(-50%, -50%)';
            wrapper.append(item);
            if (!isSelected) {
                const hide = button(`Hide ${systems.get(id).name}`, () => {
                    hiddenNeighbors.add(id);
                    render();
                    systemSelect.focus();
                });
                wrapper.append(hide);
            }
            map.append(wrapper);
        });
        container.append(map);
        if (selected.has(activeSystem)) {
            const plan = selected.get(activeSystem);
            details.append(node('strong', `${systems.get(activeSystem).name} upgrade benefits`));
            if (!plan.upgrades.length) details.append(node('p', 'No upgrades selected.', 'small text-secondary mb-0'));
            plan.upgrades.forEach((upgrade) => {
                details.append(node('p', upgradeBlurb(upgrade), 'small text-secondary mb-1'));
            });
        }
    }
    function render() {
        const available = data.systems.filter((system) => !selected.has(key(system.id)) && (selected.size === 0 ? constellation.value !== '' && key(system.constellation_id) === constellation.value : [...selected.keys()].some((id) => linked(id, key(system.id)))));
        options(systemSelect, available, 'Select a system');
        el('sov-add-system').disabled = !available.length;
        const chosen = [...selected.keys()].map((id) => systems.get(id));
        renderMap(available);
        options(from, chosen, 'Select source');
        options(to, chosen, 'Select destination');
        const cards = el('sov-systems');
        cards.replaceChildren();
        selected.forEach((plan, id) => {
            const system = systems.get(id);
            const col = node('div', null, 'col-lg-6');
            const card = node('div', null, 'card hostile-card h-100');
            card.id = `sov-system-card-${id}`;
            const body = node('div', null, 'card-body');
            body.append(node('h5', system.name));
            body.append(node('p', `Security: ${system.security == null ? 'Unknown' : system.security}`, 'small text-secondary'));
            body.append(node('p', `SDE power capacity: ${display(plan.power)} | SDE workforce capacity: ${display(plan.workforce)}`, 'small'));
            const { incoming, outgoing, power, workforce } = balances(id, plan);
            body.append(node('p', `Estimated power remaining: ${display(power)} | Estimated workforce remaining (assuming transfers succeed): ${display(workforce)}`, 'small fw-bold'));
            body.append(node('p', `Workforce in: ${incoming} / out: ${outgoing}`, 'small text-secondary'));
            const sources = new Set(transfers.filter((transfer) => transfer.to === id).map((transfer) => transfer.from));
            const destinations = new Set(transfers.filter((transfer) => transfer.from === id).map((transfer) => transfer.to));
            const warnings = window.SovPlannerAccuracy.transferWarnings(plan.workforce, incoming, outgoing);
            if (sources.size > 3) warnings.push('more than 3 source neighbors');
            if (destinations.size > 1) warnings.push('more than one export neighbor');
            if (warnings.length) body.append(node('p', `Transfer warning: ${warnings.join('; ')}. Verify in game.`, 'small text-warning'));
            if (!plan.upgrades.length) body.append(node('p', 'No upgrades selected.', 'small text-secondary'));
            plan.upgrades.forEach((upgrade) => {
                const box = node('div', null, 'border border-secondary rounded p-2 mb-2');
                box.append(node('strong', upgrade.name));
                box.append(node('p', upgradeBlurb(upgrade), 'small text-secondary mb-1'));
                [['Power cost', 'power'], ['Workforce cost', 'workforce'], ['Power production', 'power_production'], ['Workforce production', 'workforce_production']].forEach(([label, prop]) => {
                    box.append(node('div', `${label}: ${display(number(upgrade[prop]))}`, 'small'));
                });
                body.append(box);
            });
            card.append(body);
            col.append(card);
            cards.append(col);
        });
        const list = el('sov-transfers');
        list.replaceChildren();
        transfers.forEach((transfer, index) => {
            const row = node('div', null, 'd-flex align-items-center gap-2 mb-1');
            row.append(node('span', `${systems.get(transfer.from).name} → ${systems.get(transfer.to).name}: ${transfer.amount}`));
            row.append(button('Remove', () => { transfers.splice(index, 1); render(); }));
            list.append(row);
        });
        if (selected.size > 1) {
            const reached = new Set([selected.keys().next().value]);
            let size;
            do {
                size = reached.size;
                selected.forEach((_, id) => { if ([...reached].some((other) => linked(id, other))) reached.add(id); });
            } while (reached.size !== size);
            if (reached.size !== selected.size) cards.prepend(node('div', 'Warning: selected systems are no longer connected.', 'col-12 text-warning'));
        }
    }
    const emptyCatalogs = ['constellations', 'systems', 'upgrades'].filter((name) => !data[name].length);
    if (emptyCatalogs.length) el('sov-catalog-warning').textContent = `Empty catalogs: ${emptyCatalogs.join(', ')}. SDE data may need to be loaded.`;
    options(constellation, data.constellations, 'Select a constellation');
    constellation.addEventListener('change', () => { selected.clear(); hiddenNeighbors.clear(); activeSystem = null; transfers = []; message.textContent = ''; render(); });
    el('sov-add-system').addEventListener('click', () => {
        addSystem(systemSelect.value);
    });
    el('sov-add-transfer').addEventListener('click', () => {
        const amount = number(el('sov-amount').value);
        if (!selected.has(from.value) || !selected.has(to.value) || from.value === to.value || !linked(from.value, to.value) || amount === null || amount <= 0) {
            message.textContent = 'Choose two different linked systems and a positive workforce amount.';
            return;
        }
        transfers.push({ from: from.value, to: to.value, amount });
        el('sov-amount').value = '';
        message.textContent = '';
        render();
    });
    el('sov-controls').hidden = false;
    render();
    }
    retry.addEventListener('click', load);
    load();
})();
