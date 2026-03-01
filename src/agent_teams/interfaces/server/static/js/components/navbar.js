/**
 * components/navbar.js
 * Wires UI toggle controls for the header navigation and sidebar overlays.
 */
import { els } from '../utils/dom.js';

export function setupNavbarBindings() {
    if (els.sidebarToggleBtn) {
        els.sidebarToggleBtn.onclick = () => {
            els.sidebar.classList.toggle('collapsed');
        };
    }

    if (els.inspectorToggleBtn) {
        els.inspectorToggleBtn.onclick = () => {
            els.inspectorPanel.classList.toggle('collapsed');
        };
    }

    if (els.themeToggleBtn) {
        els.themeToggleBtn.onclick = () => {
            document.body.classList.toggle('light-theme');
            const isLight = document.body.classList.contains('light-theme');
            localStorage.setItem('agent_teams_theme', isLight ? 'light' : 'dark');
        };

        // Load theme from localStorage on start
        const savedTheme = localStorage.getItem('agent_teams_theme');
        if (savedTheme === 'light') {
            document.body.classList.add('light-theme');
        }
    }
}
