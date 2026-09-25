import * as sdk from '/sdk/internal.js';
import { signalVerdict } from '/shared/verdict.js';

window.facetechConsoleDeps = () => Promise.resolve({ sdk, shared: { signalVerdict } });

if (typeof window.loadSDK === 'function') document.documentElement.dataset.ready = '1';
