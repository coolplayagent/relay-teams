/**
 * app/index.js
 * Root app orchestration.
 */
import { initApp } from './bootstrap.js';
import { handleSend } from './prompt.js';
import { setSelectSessionHandler } from '../components/sidebar.js';
import { selectSession, selectSubagentSession } from './session.js';

export { selectSession, selectSubagentSession } from './session.js';

export async function startApp() {
    setSelectSessionHandler(selectSession);
    await initApp(selectSession, selectSubagentSession, handleSend);
}
