/**
 * Hostile Intel navigation & action loading overlay.
 *
 * Pages and actions that require significant server-side processing (e.g., loading
 * structures registry, running threat scans, calculating timers, filtering intel, or syncing zKillboard)
 * can take several seconds. Any element marked with data-hostile-loading, data-indy-loading,
 * data-me-loading, or data-loading-overlay shows the shared overlay as soon as it is activated:
 *
 *   <a href="..." data-hostile-loading="Loading Structures" data-hostile-loading-detail="Fetching hostile structures registry...">
 *   <button type="submit" data-hostile-loading="Running Threat Scan" data-hostile-loading-detail="Analyzing character killboards and drop risks...">
 *   <form ... data-hostile-loading="Calculating Reinforcement Window">
 *
 * The overlay markup lives in hostile/includes/page_loading_overlay.html.
 */

(function() {
    const OVERLAY_ID = 'hostilePageLoadingOverlay';
    const FALLBACK_OVERLAY_ID = 'mePageLoadingOverlay';
    const TRIGGER_SELECTOR = '[data-hostile-loading], [data-indy-loading], [data-me-loading], [data-loading-overlay]';

    function getOverlay() {
        return document.getElementById(OVERLAY_ID) || document.getElementById(FALLBACK_OVERLAY_ID);
    }

    function setText(overlay, selector, text) {
        const node = overlay.querySelector(selector);
        if (node && text) {
            node.textContent = text;
        }
    }

    function showOverlay(title, detail) {
        const overlay = getOverlay();
        if (!overlay) {
            return;
        }
        if (title) {
            setText(overlay, '[data-hostile-loading-title]', title);
            setText(overlay, '[data-me-loading-title]', title);
        }
        if (detail) {
            setText(overlay, '[data-hostile-loading-detail]', detail);
            setText(overlay, '[data-me-loading-detail]', detail);
        }
        overlay.classList.remove('d-none');
        overlay.setAttribute('aria-hidden', 'false');
    }

    function hideOverlay() {
        const overlay = getOverlay();
        if (!overlay) {
            return;
        }
        overlay.classList.add('d-none');
        overlay.setAttribute('aria-hidden', 'true');
    }

    function showFromTrigger(trigger) {
        const title = trigger.getAttribute('data-hostile-loading') ||
                      trigger.getAttribute('data-loading-overlay') ||
                      trigger.getAttribute('data-indy-loading') ||
                      trigger.getAttribute('data-me-loading') ||
                      '';
        const detail = trigger.getAttribute('data-hostile-loading-detail') ||
                       trigger.getAttribute('data-loading-detail') ||
                       trigger.getAttribute('data-indy-loading-detail') ||
                       trigger.getAttribute('data-me-loading-detail') ||
                       '';
        showOverlay(title, detail);
    }

    /** True for clicks the browser handles itself (new tab/window, download, ...). */
    function isPassthroughClick(event, trigger) {
        if (event.defaultPrevented || event.button !== 0) {
            return true;
        }
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return true;
        }
        const target = trigger.getAttribute('target');
        if (target && target !== '_self') {
            return true;
        }
        if (trigger.hasAttribute('download')) {
            return true;
        }
        const href = trigger.getAttribute('href');
        if (href !== null && (href === '' || href === '#' || href.startsWith('javascript:'))) {
            return true;
        }
        return false;
    }

    document.addEventListener('click', function(event) {
        const trigger = event.target.closest(TRIGGER_SELECTOR);
        if (!trigger || trigger.tagName === 'FORM') {
            return;
        }
        if (trigger.disabled || trigger.getAttribute('aria-disabled') === 'true') {
            return;
        }
        if (isPassthroughClick(event, trigger)) {
            return;
        }
        showFromTrigger(trigger);
    });

    document.addEventListener('submit', function(event) {
        if (event.defaultPrevented) {
            return;
        }
        const form = event.target;
        if (!form || form.tagName !== 'FORM') {
            return;
        }

        // Look for custom loading attributes on either the submitter or the form itself
        const submitter = event.submitter;
        let title = '';
        let detail = '';

        if (submitter) {
            title = submitter.getAttribute('data-hostile-loading') ||
                    submitter.getAttribute('data-loading-overlay') ||
                    submitter.getAttribute('data-indy-loading') ||
                    submitter.getAttribute('data-me-loading') ||
                    '';
            detail = submitter.getAttribute('data-hostile-loading-detail') ||
                     submitter.getAttribute('data-loading-detail') ||
                     submitter.getAttribute('data-indy-loading-detail') ||
                     submitter.getAttribute('data-me-loading-detail') ||
                     '';
        }
        if (!title) {
            title = form.getAttribute('data-hostile-loading') ||
                    form.getAttribute('data-loading-overlay') ||
                    form.getAttribute('data-indy-loading') ||
                    form.getAttribute('data-me-loading') ||
                    '';
        }
        if (!detail) {
            detail = form.getAttribute('data-hostile-loading-detail') ||
                     form.getAttribute('data-loading-detail') ||
                     form.getAttribute('data-indy-loading-detail') ||
                     form.getAttribute('data-me-loading-detail') ||
                     '';
        }

        // Fallback default message for submitting forms
        if (!title) {
            const method = (form.getAttribute('method') || 'GET').toUpperCase();
            if (method === 'POST') {
                title = 'Submitting & Processing Intel...';
                detail = 'Saving records and updating intelligence state...';
            } else {
                title = 'Loading Data...';
                detail = 'Fetching matching intelligence records...';
            }
        }

        showOverlay(title, detail);
    });

    // Returning through history (including the back/forward cache) must never restore the
    // page with the overlay still covering it.
    window.addEventListener('pageshow', hideOverlay);

    // Escape hatch: if a request dies without navigating, let the user dismiss the overlay.
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            hideOverlay();
        }
    });

    window.hostilePageLoading = {
        show: showOverlay,
        hide: hideOverlay
    };

    window.indyHubPageLoading = window.hostilePageLoading;
})();
