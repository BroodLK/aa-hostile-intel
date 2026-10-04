/**
 * Live tick-down countdown timer engine for Hostile Intel & Sovereignty Campaigns
 */
(function() {
    'use strict';

    function pad(n) {
        return String(n).padStart(2, '0');
    }

    function formatTimeRemaining(diffMs) {
        if (diffMs > 0) {
            const totalSeconds = Math.floor(diffMs / 1000);
            const days = Math.floor(totalSeconds / 86400);
            const hours = Math.floor((totalSeconds % 86400) / 3600);
            const minutes = Math.floor((totalSeconds % 3600) / 60);
            const seconds = totalSeconds % 60;

            if (days > 0) {
                return `${days}d ${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
            } else {
                return `${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
            }
        } else {
            const elapsedSeconds = Math.floor(Math.abs(diffMs) / 1000);
            const days = Math.floor(elapsedSeconds / 86400);
            const hours = Math.floor((elapsedSeconds % 86400) / 3600);
            const minutes = Math.floor((elapsedSeconds % 3600) / 60);
            const seconds = elapsedSeconds % 60;

            if (days > 0) {
                return `+${days}d ${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
            } else {
                return `+${pad(hours)}h ${pad(minutes)}m ${pad(seconds)}s`;
            }
        }
    }

    function updateCountdowns() {
        const timerElements = document.querySelectorAll('.countdown-timer[data-expiry]');
        if (!timerElements.length) return;

        const now = Date.now();
        const threeHoursMs = 3 * 3600 * 1000;
        const oneHourMs = 1 * 3600 * 1000;

        timerElements.forEach(function(el) {
            const expiryStr = el.getAttribute('data-expiry');
            if (!expiryStr) return;

            const expiryDate = new Date(expiryStr);
            if (isNaN(expiryDate.getTime())) return;

            const diffMs = expiryDate.getTime() - now;

            if (diffMs > 0) {
                const formatted = formatTimeRemaining(diffMs);
                el.textContent = formatted;

                if (el.classList.contains('badge')) {
                    el.classList.remove('bg-danger', 'bg-warning', 'bg-secondary', 'text-dark', 'pulse-glow-danger', 'pulse-glow-warning');
                    if (diffMs <= oneHourMs) {
                        el.classList.add('bg-danger', 'pulse-glow-danger');
                    } else if (diffMs <= threeHoursMs) {
                        el.classList.add('bg-warning', 'text-dark');
                    } else {
                        el.classList.add('bg-secondary');
                    }
                } else {
                    el.classList.remove('text-light', 'text-white', 'text-warning', 'text-danger', 'pulse-glow-danger', 'pulse-glow-warning');
                    if (diffMs <= oneHourMs) {
                        el.classList.add('text-danger', 'pulse-glow-danger');
                    } else if (diffMs <= threeHoursMs) {
                        el.classList.add('text-warning');
                    } else {
                        el.classList.add('text-light');
                    }
                }
            } else {
                const elapsedFormatted = formatTimeRemaining(diffMs);
                if (el.classList.contains('badge')) {
                    el.textContent = `ON-GOING (${elapsedFormatted})`;
                    el.classList.remove('bg-danger', 'bg-secondary', 'pulse-glow-danger');
                    el.classList.add('bg-warning', 'text-dark', 'pulse-glow-warning');
                } else {
                    el.classList.remove('text-light', 'text-white', 'text-warning', 'text-danger');
                    el.innerHTML = `<span class="text-success fw-bold"><i class="fas fa-play-circle me-1"></i>ON-GOING</span> <span class="text-warning small font-mono">(${elapsedFormatted})</span>`;
                }

                const tr = el.closest('tr[data-sov-row]');
                if (tr) {
                    const scoreCell = tr.children[4];
                    if (scoreCell && (scoreCell.textContent.includes('Not Started') || scoreCell.textContent.includes('N/A'))) {
                        scoreCell.innerHTML = `
                            <div class="sov-score-wrapper">
                                <div class="sov-score-container">
                                    <div class="sov-score-donut" style="background: conic-gradient(#10b981 0% 60%, #ef4444 60% 100%);">
                                        <div class="sov-score-donut-inner">
                                            <span>60%</span>
                                        </div>
                                    </div>
                                    <div class="sov-score-breakdown font-mono">
                                        <span class="text-success"><i class="fas fa-shield-alt me-1"></i>Def: 60%</span>
                                        <span class="text-danger"><i class="fas fa-crosshairs me-1"></i>Atk: 40%</span>
                                    </div>
                                </div>
                                <div class="small text-muted font-mono mt-1">No progress yet</div>
                            </div>`;
                    }
                }
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            updateCountdowns();
            setInterval(updateCountdowns, 1000);
        });
    } else {
        updateCountdowns();
        setInterval(updateCountdowns, 1000);
    }
})();
