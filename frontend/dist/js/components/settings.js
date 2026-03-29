/**
 * components/settings.js
 * Backward-compatible facade. New implementation lives under ./settings/.
 */
export {
    initSettings,
    openSettings,
    closeSettings,
} from './settings/index.js';

export { initAppearanceOnStartup } from './settings/appearanceSettings.js';
