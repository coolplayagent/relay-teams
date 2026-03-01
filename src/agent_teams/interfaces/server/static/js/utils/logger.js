/**
 * utils/logger.js
 * Handles system log appends.
 */
import { els } from './dom.js';

export function sysLog(msg, type = "info") {
    if (!els.systemLogs) return;

    const entry = document.createElement("div");
    entry.className = `log-entry ${type}`;

    const time = new Date().toLocaleTimeString();
    entry.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-msg">${msg}</span>`;

    els.systemLogs.appendChild(entry);
    els.systemLogs.scrollTop = els.systemLogs.scrollHeight;
}
