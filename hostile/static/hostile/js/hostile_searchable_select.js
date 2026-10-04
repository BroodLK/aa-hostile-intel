/**
 * AA Hostile Intel - Universal Searchable Select Component
 * Converts standard <select> elements into searchable, dark-themed, keyboard-accessible dropdowns.
 */

(function () {
    'use strict';

    function makeSearchableSelect(selectEl) {
        if (!selectEl || selectEl.dataset.searchableInit === 'true') return;
        if (selectEl.classList.contains('no-searchable')) return;
        if (selectEl.multiple && !selectEl.dataset.searchable) return;
        if (selectEl.closest('.d-none')) return;

        selectEl.dataset.searchableInit = 'true';

        // Container wrapper
        const wrapper = document.createElement('div');
        wrapper.className = 'hostile-searchable-select dropdown position-relative';
        
        // Preserve any custom width/class from select if applicable
        if (selectEl.classList.contains('w-100') || selectEl.style.width === '100%') {
            wrapper.classList.add('w-100');
        }

        // Toggle button resembling a form-select
        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'form-select text-start d-flex justify-content-between align-items-center bg-black text-light border-secondary hostile-searchable-toggle';
        toggleBtn.setAttribute('data-bs-toggle', 'dropdown');
        toggleBtn.setAttribute('data-bs-auto-close', 'outside');
        toggleBtn.setAttribute('aria-expanded', 'false');
        toggleBtn.setAttribute('data-bs-display', 'static');

        const labelSpan = document.createElement('span');
        labelSpan.className = 'hostile-searchable-label text-truncate me-2';
        labelSpan.textContent = selectEl.options[selectEl.selectedIndex]?.text || selectEl.getAttribute('placeholder') || 'Select option...';

        const caretSpan = document.createElement('span');
        caretSpan.className = 'hostile-searchable-caret ms-auto flex-shrink-0';
        caretSpan.innerHTML = '<i class="fas fa-chevron-down text-muted small"></i>';

        toggleBtn.appendChild(labelSpan);
        toggleBtn.appendChild(caretSpan);

        // Dropdown Menu
        const menu = document.createElement('div');
        menu.className = 'dropdown-menu bg-dark text-white border-secondary shadow-lg p-2 hostile-searchable-menu';
        menu.style.minWidth = '100%';
        menu.style.zIndex = '1070';
        menu.addEventListener('click', function (e) {
            e.stopPropagation();
        });

        // Search Box
        const searchBox = document.createElement('div');
        searchBox.className = 'input-group input-group-sm mb-2 hostile-searchable-search-box';
        searchBox.innerHTML = `
            <span class="input-group-text bg-black border-secondary text-muted"><i class="fas fa-search"></i></span>
            <input type="text" class="form-control bg-black text-light border-secondary hostile-searchable-input" placeholder="Type to search..." autocomplete="off">
            <button class="btn btn-outline-secondary btn-sm hostile-searchable-clear-btn d-none" type="button"><i class="fas fa-times"></i></button>
        `;

        const searchInput = searchBox.querySelector('.hostile-searchable-input');
        const clearBtn = searchBox.querySelector('.hostile-searchable-clear-btn');

        // Options List Container
        const listContainer = document.createElement('div');
        listContainer.className = 'hostile-searchable-options overflow-auto';
        listContainer.style.maxHeight = '240px';

        // Empty Result Message
        const emptyMsg = document.createElement('div');
        emptyMsg.className = 'hostile-searchable-empty text-muted small text-center py-2 d-none';
        emptyMsg.innerHTML = '<i class="fas fa-search me-1"></i> No matching options';

        menu.appendChild(searchBox);
        menu.appendChild(listContainer);
        menu.appendChild(emptyMsg);

        // Populate options from native select
        function renderOptions() {
            listContainer.innerHTML = '';
            const query = searchInput.value.toLowerCase().trim();
            let matchCount = 0;

            const children = Array.from(selectEl.children);
            children.forEach(child => {
                if (child.tagName === 'OPTGROUP') {
                    const groupHeader = document.createElement('div');
                    groupHeader.className = 'dropdown-header text-uppercase text-muted fw-bold px-2 py-1 small';
                    groupHeader.textContent = child.label;

                    const groupOptions = Array.from(child.children);
                    let groupMatches = 0;
                    const groupItems = [];

                    groupOptions.forEach(opt => {
                        const optText = opt.text;
                        const optVal = opt.value;
                        const matches = !query || optText.toLowerCase().includes(query) || (opt.dataset && Object.values(opt.dataset).some(d => String(d).toLowerCase().includes(query)));

                        if (matches) {
                            matchCount++;
                            groupMatches++;
                            const itemBtn = createOptionButton(opt);
                            groupItems.push(itemBtn);
                        }
                    });

                    if (groupMatches > 0) {
                        listContainer.appendChild(groupHeader);
                        groupItems.forEach(btn => listContainer.appendChild(btn));
                    }
                } else if (child.tagName === 'OPTION') {
                    const optText = child.text;
                    const matches = !query || optText.toLowerCase().includes(query) || (child.dataset && Object.values(child.dataset).some(d => String(d).toLowerCase().includes(query)));

                    if (matches) {
                        matchCount++;
                        const itemBtn = createOptionButton(child);
                        listContainer.appendChild(itemBtn);
                    }
                }
            });

            if (matchCount === 0) {
                emptyMsg.classList.remove('d-none');
            } else {
                emptyMsg.classList.add('d-none');
            }

            if (query.length > 0) {
                clearBtn.classList.remove('d-none');
            } else {
                clearBtn.classList.add('d-none');
            }
        }

        function createOptionButton(opt) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'dropdown-item hostile-searchable-item text-light rounded px-2 py-1 small mb-1';
            btn.dataset.value = opt.value;
            btn.textContent = opt.text;

            if (opt.selected || opt.value === selectEl.value) {
                btn.classList.add('selected');
            }
            if (opt.disabled) {
                btn.disabled = true;
                btn.classList.add('disabled', 'text-muted');
            }

            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                selectOption(opt.value, opt.text);
            });

            return btn;
        }

        function selectOption(value, text) {
            selectEl.value = value;
            labelSpan.textContent = text || selectEl.options[selectEl.selectedIndex]?.text || '';
            
            // Trigger standard change event on native select
            selectEl.dispatchEvent(new Event('change', { bubbles: true }));
            selectEl.dispatchEvent(new Event('input', { bubbles: true }));

            // Close dropdown
            if (window.bootstrap && bootstrap.Dropdown) {
                const bsDropdown = bootstrap.Dropdown.getInstance(toggleBtn) || new bootstrap.Dropdown(toggleBtn);
                bsDropdown.hide();
            } else {
                menu.classList.remove('show');
                toggleBtn.classList.remove('show');
            }
        }

        function updateLabelFromSelect() {
            const selectedOpt = selectEl.options[selectEl.selectedIndex];
            labelSpan.textContent = selectedOpt ? selectedOpt.text : (selectEl.getAttribute('placeholder') || 'Select option...');
            
            // Update selected class in items
            listContainer.querySelectorAll('.hostile-searchable-item').forEach(item => {
                if (item.dataset.value === selectEl.value) {
                    item.classList.add('selected');
                } else {
                    item.classList.remove('selected');
                }
            });
        }

        // Search input events
        searchInput.addEventListener('input', function () {
            renderOptions();
        });

        clearBtn.addEventListener('click', function () {
            searchInput.value = '';
            searchInput.focus();
            renderOptions();
        });

        // Keyboard navigation
        searchInput.addEventListener('keydown', function (e) {
            const items = Array.from(listContainer.querySelectorAll('.hostile-searchable-item:not(.disabled)'));
            const activeIndex = items.findIndex(it => it.classList.contains('active'));

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const nextIndex = activeIndex < items.length - 1 ? activeIndex + 1 : 0;
                items.forEach((it, idx) => it.classList.toggle('active', idx === nextIndex));
                if (items[nextIndex]) items[nextIndex].scrollIntoView({ block: 'nearest' });
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                const prevIndex = activeIndex > 0 ? activeIndex - 1 : items.length - 1;
                items.forEach((it, idx) => it.classList.toggle('active', idx === prevIndex));
                if (items[prevIndex]) items[prevIndex].scrollIntoView({ block: 'nearest' });
            } else if (e.key === 'Enter') {
                e.preventDefault();
                const target = activeIndex >= 0 ? items[activeIndex] : items[0];
                if (target) {
                    target.click();
                }
            } else if (e.key === 'Escape') {
                if (window.bootstrap && bootstrap.Dropdown) {
                    const bsDropdown = bootstrap.Dropdown.getInstance(toggleBtn);
                    if (bsDropdown) bsDropdown.hide();
                }
            }
        });

        // Focus search input when dropdown is shown
        toggleBtn.addEventListener('shown.bs.dropdown', function () {
            searchInput.value = '';
            renderOptions();
            setTimeout(() => {
                try {
                    searchInput.focus({ preventScroll: true });
                } catch (e) {}
                const selectedItem = listContainer.querySelector('.hostile-searchable-item.selected');
                if (selectedItem) {
                    selectedItem.scrollIntoView({ block: 'nearest' });
                }
            }, 30);
        });

        // Listen for native select changes (e.g. from code)
        selectEl.addEventListener('change', updateLabelFromSelect);

        // Expose sync function on select element for dynamic DOM updates
        selectEl.syncSearchableSelect = function () {
            renderOptions();
            updateLabelFromSelect();
        };

        // MutationObserver to detect dynamic changes to select options
        const observer = new MutationObserver(function () {
            renderOptions();
            updateLabelFromSelect();
        });
        observer.observe(selectEl, { childList: true, subtree: true, attributes: true });

        // Insert wrapper in place of select and hide native select
        selectEl.style.display = 'none';
        selectEl.parentNode.insertBefore(wrapper, selectEl);
        wrapper.appendChild(toggleBtn);
        wrapper.appendChild(menu);
        wrapper.appendChild(selectEl); // Keep inside wrapper for context

        renderOptions();
        updateLabelFromSelect();
    }

    function initAllSearchableSelects(root = document) {
        const selects = root.querySelectorAll('select.form-select, select.form-control, select[data-searchable]');
        selects.forEach(select => {
            makeSearchableSelect(select);
        });
    }

    // Auto-initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => initAllSearchableSelects());
    } else {
        initAllSearchableSelects();
    }

    // Initialize when any Bootstrap modal is shown
    document.addEventListener('shown.bs.modal', function (e) {
        initAllSearchableSelects(e.target);
    });

    // Expose global initializer
    window.HostileSearchableSelect = {
        init: makeSearchableSelect,
        initAll: initAllSearchableSelects
    };
})();
