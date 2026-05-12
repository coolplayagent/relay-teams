/**
 * components/navbar.js
 * Wires UI toggle controls for the header navigation and sidebar overlays.
 */
import { els } from '../utils/dom.js';

const RAIL_RESIZING_CLASS = 'is-resizing-rails';

export function setupNavbarBindings() {
    _initSidebarResize();
    _initRightRailResize();

    if (els.sidebarToggleBtn) {
        els.sidebarToggleBtn.onclick = () => {
            const collapsed = els.sidebar.classList.toggle('collapsed');
            if (collapsed) {
                localStorage.setItem('agent_teams_sidebar_collapsed', '1');
            } else {
                localStorage.setItem('agent_teams_sidebar_collapsed', '0');
            }
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

    if (els.toggleInspectorBtn && els.railInspector) {
        els.toggleInspectorBtn.onclick = () => {
            els.railInspector.classList.toggle('expanded');
        };
    }
}

function _initSidebarResize() {
    if (!els.sidebar) return;

    const savedWidth = localStorage.getItem('agent_teams_sidebar_width');
    let initialWidth = 280;
    if (savedWidth && /^\d+$/.test(savedWidth)) {
        const px = Number(savedWidth);
        if (px >= 180) {
            els.sidebar.style.width = `${px}px`;
            els.sidebar.style.setProperty('--sidebar-width', `${px}px`);
            initialWidth = px;
        }
    }
    document.documentElement.style.setProperty('--sidebar-width', `${initialWidth}px`);

    const collapsed = localStorage.getItem('agent_teams_sidebar_collapsed');
    if (collapsed === '1') {
        els.sidebar.classList.add('collapsed');
    }

    if (!els.sidebarResizer) return;
    let dragging = false;
    let currentWidth = initialWidth;
    let pendingClientX = null;
    let frameHandle = 0;

    const flushWidth = () => {
        frameHandle = 0;
        if (!dragging || pendingClientX === null || !els.sidebar || els.sidebar.classList.contains('collapsed')) {
            return;
        }
        const maxWidth = Math.max(180, window.innerWidth - getVisibleRightRailWidth() - 100);
        const next = Math.max(180, Math.min(maxWidth, pendingClientX));
        currentWidth = next;
        applySidebarWidth(next);
    };

    const onMove = (e) => {
        if (!dragging || !els.sidebar || els.sidebar.classList.contains('collapsed')) return;
        pendingClientX = e.clientX;
        if (!frameHandle) {
            frameHandle = window.requestAnimationFrame(flushWidth);
        }
    };

    const onUp = () => {
        if (!dragging) return;
        if (frameHandle) {
            window.cancelAnimationFrame(frameHandle);
            frameHandle = 0;
            flushWidth();
        }
        dragging = false;
        pendingClientX = null;
        persistSidebarWidth(currentWidth);
        els.sidebarResizer.classList.remove('dragging');
        setRailResizeDragging(false);
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
    };

    els.sidebarResizer.addEventListener('mousedown', (e) => {
        if (els.sidebar.classList.contains('collapsed')) return;
        dragging = true;
        els.sidebarResizer.classList.add('dragging');
        setRailResizeDragging(true);
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
        e.preventDefault();
    });
}

function _initRightRailResize() {
    if (!els.rightRail || !els.rightRailResizer) return;

    const savedWidth = localStorage.getItem('agent_teams_right_rail_width');
    let initialWidth = 280;
    if (savedWidth && /^\d+$/.test(savedWidth)) {
        const px = Number(savedWidth);
        if (px >= 220) {
            applyRightRailWidth(px);
            initialWidth = px;
        }
    }
    document.documentElement.style.setProperty('--right-rail-width', `${initialWidth}px`);

    let dragging = false;
    let currentWidth = initialWidth;
    let pendingClientX = null;
    let frameHandle = 0;

    const flushWidth = () => {
        frameHandle = 0;
        if (!dragging || pendingClientX === null || !els.rightRail || els.rightRail.classList.contains('collapsed')) {
            return;
        }
        const maxWidth = Math.max(220, window.innerWidth - getVisibleSidebarWidth() - 220);
        const next = Math.max(220, Math.min(maxWidth, window.innerWidth - pendingClientX));
        currentWidth = next;
        applyRightRailWidth(next);
    };

    const onMove = (e) => {
        if (!dragging || !els.rightRail || els.rightRail.classList.contains('collapsed')) return;
        pendingClientX = e.clientX;
        if (!frameHandle) {
            frameHandle = window.requestAnimationFrame(flushWidth);
        }
    };

    const onUp = () => {
        if (!dragging) return;
        if (frameHandle) {
            window.cancelAnimationFrame(frameHandle);
            frameHandle = 0;
            flushWidth();
        }
        dragging = false;
        pendingClientX = null;
        persistRightRailWidth(currentWidth);
        els.rightRailResizer.classList.remove('dragging');
        setRailResizeDragging(false);
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
    };

    els.rightRailResizer.addEventListener('mousedown', (e) => {
        if (els.rightRail.classList.contains('collapsed')) return;
        dragging = true;
        els.rightRailResizer.classList.add('dragging');
        setRailResizeDragging(true);
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
        e.preventDefault();
    });
}

function applySidebarWidth(width) {
    if (!els.sidebar) return;
    const px = `${width}px`;
    els.sidebar.style.width = px;
    els.sidebar.style.setProperty('--sidebar-width', px);
    document.documentElement.style.setProperty('--sidebar-width', px);
}

function persistSidebarWidth(width) {
    localStorage.setItem('agent_teams_sidebar_width', String(width));
}

function applyRightRailWidth(width) {
    if (!els.rightRail) return;
    const px = `${width}px`;
    els.rightRail.style.width = px;
    els.rightRail.style.setProperty('--right-rail-width', px);
    document.documentElement.style.setProperty('--right-rail-width', px);
}

function persistRightRailWidth(width) {
    localStorage.setItem('agent_teams_right_rail_width', String(width));
}

function getVisibleRightRailWidth() {
    if (!els.rightRail || els.rightRail.classList.contains('collapsed')) {
        return 0;
    }
    const width = els.rightRail.getBoundingClientRect().width;
    return Number.isFinite(width) ? Math.max(0, width) : 0;
}

function getVisibleSidebarWidth() {
    if (!els.sidebar || els.sidebar.classList.contains('collapsed')) {
        return 0;
    }
    const width = els.sidebar.getBoundingClientRect().width;
    return Number.isFinite(width) ? Math.max(0, width) : 0;
}

function setRailResizeDragging(isDragging) {
    document.body.style.userSelect = isDragging ? 'none' : '';
    document.body.classList.toggle(RAIL_RESIZING_CLASS, isDragging);
}
