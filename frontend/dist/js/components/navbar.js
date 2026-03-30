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

    const railInspector = document.getElementById('rail-inspector');
    const toggleInspectorBtn = document.getElementById('toggle-inspector');
    if (toggleInspectorBtn && railInspector) {
        const header = railInspector.querySelector('.inspector-header');
        const iconPath = toggleInspectorBtn.querySelector('path');
        
        const toggle = () => {
            const isExpanded = railInspector.classList.toggle('expanded');
            if (iconPath) {
                if (isExpanded) {
                    iconPath.setAttribute('d', 'M19 9l-7 7-7-7');
                } else {
                    iconPath.setAttribute('d', 'M7 13l5 5 5-5M7 6l5 5 5-5');
                }
            }
        };
        
        if (header) header.onclick = toggle;
        toggleInspectorBtn.onclick = (e) => {
            e.stopPropagation();
            toggle();
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
    let dragRightRailWidth = 280;
    let currentWidth = initialWidth;
    let pendingClientX = null;
    let frameHandle = 0;

    const flushWidth = () => {
        frameHandle = 0;
        if (!dragging || pendingClientX === null || !els.sidebar || els.sidebar.classList.contains('collapsed')) {
            return;
        }
        const maxWidth = window.innerWidth - dragRightRailWidth - 100;
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
        dragRightRailWidth = getRailWidth(document.getElementById('right-rail'), 280);
        els.sidebarResizer.classList.add('dragging');
        setRailResizeDragging(true);
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
        e.preventDefault();
    });
}

function _initRightRailResize() {
    const rightRail = document.getElementById('right-rail');
    const rightRailResizer = document.getElementById('right-rail-resizer');
    if (!rightRail || !rightRailResizer) return;

    const savedWidth = localStorage.getItem('agent_teams_right_rail_width');
    let initialWidth = 280;
    if (savedWidth && /^\d+$/.test(savedWidth)) {
        const px = Number(savedWidth);
        if (px >= 180) {
            initialWidth = px;
        }
    }
    rightRail.style.width = `${initialWidth}px`;
    rightRail.style.setProperty('--right-rail-width', `${initialWidth}px`);
    document.documentElement.style.setProperty('--right-rail-width', `${initialWidth}px`);

    let dragging = false;
    let dragSidebarWidth = 280;
    let currentWidth = initialWidth;
    let pendingClientX = null;
    let frameHandle = 0;

    const flushWidth = () => {
        frameHandle = 0;
        if (!dragging || pendingClientX === null) {
            return;
        }
        const windowWidth = window.innerWidth;
        const minWidth = 180;
        const maxWidth = windowWidth - dragSidebarWidth - 100;
        const next = Math.max(minWidth, Math.min(maxWidth, windowWidth - pendingClientX));
        currentWidth = next;
        applyRightRailWidth(rightRail, next);
    };

    const onMove = (e) => {
        if (!dragging) return;
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
        rightRailResizer.classList.remove('dragging');
        setRailResizeDragging(false);
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
    };

    rightRailResizer.addEventListener('mousedown', (e) => {
        dragging = true;
        dragSidebarWidth = getRailWidth(document.querySelector('.sidebar'), 280);
        rightRailResizer.classList.add('dragging');
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

function applyRightRailWidth(rightRail, width) {
    const px = `${width}px`;
    rightRail.style.width = px;
    rightRail.style.setProperty('--right-rail-width', px);
    document.documentElement.style.setProperty('--right-rail-width', px);
}

function persistRightRailWidth(width) {
    localStorage.setItem('agent_teams_right_rail_width', String(width));
}

function getRailWidth(element, fallback) {
    if (!element) return fallback;
    const inlineWidth = Number.parseInt(String(element.style.width || ''), 10);
    if (Number.isFinite(inlineWidth) && inlineWidth >= 0) {
        return inlineWidth;
    }
    const cssWidth = Number.parseInt(getComputedStyle(element).width, 10);
    if (Number.isFinite(cssWidth) && cssWidth >= 0) {
        return cssWidth;
    }
    return fallback;
}

function setRailResizeDragging(isDragging) {
    document.body.style.userSelect = isDragging ? 'none' : '';
    document.body.classList.toggle(RAIL_RESIZING_CLASS, isDragging);
}
