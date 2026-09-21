// Automatic ENTER-password login for id.tsinghua.edu.cn.
//
// Chrome refuses to expose the password it autofilled until a *real* user event
// reaches the page, and there is no extension API that can forge one: that is
// why the previous implementation attached chrome.debugger and used CDP, which
// raised the "started debugging this browser" banner.
//
// This version gets the trusted event from the operating system instead:
//   content.js (has focus?)  ->  this service worker  ->  native host #1
//     ->  key agent app #2  ->  Quartz CGEvent into the Chrome process
//
// No debugger, no banner.

const NATIVE_HOST = 'com.thu.autologin.host';
const NATIVE_TIMEOUT_MS = 4000;

// Edge's password fill only commits after a SECOND Tab, so the tab rung is sent
// twice there. The pair is kept close together on purpose — two fast taps, not
// two separate attempts — and the gap is explicit rather than left to IPC
// jitter. Tune here if Edge needs a different spacing.
const UNLOCK_REPEAT_GAP_MS = 60;

// Escalation ladder. Every entry but the last is a side-effect-free key: it
// only proves "a real user typed something" so Chrome reveals the value it has
// already autofilled into the DOM. `enter` additionally triggers the page's own
// keyLogin() handler, so once it is used we no longer call doLogin() ourselves.
//
// `shift` is deliberately absent: measured on this machine, a bare modifier
// keydown does not make Chrome expose the autofilled value. `tab` and `f15`
// move focus / are inert but still count as real typing.
const UNLOCK_LADDER = [
  { strategy: 'tab', pollMs: 1000 },
  { strategy: 'f15', pollMs: 1000 },
  { strategy: 'enter', pollMs: 4000, submitsPage: true },
];

/* ------------------------------------------------------------------ *
 * Browser detection                                                   *
 * ------------------------------------------------------------------ */

// Both are Chromium, so both expose the same APIs; only the autofill timing
// differs. userAgentData.brands is consulted first because it survives user
// agent reduction, with the UA string as a fallback for older builds.
const detectBrowser = (nav) => {
  const source = nav || (typeof navigator !== 'undefined' ? navigator : {});

  try {
    const brands = source.userAgentData && source.userAgentData.brands;
    if (Array.isArray(brands)) {
      if (brands.some((b) => b && /Microsoft Edge/i.test(b.brand))) return 'edge';
      if (brands.some((b) => b && /Google Chrome/i.test(b.brand))) return 'chrome';
    }
  } catch (e) {
    // fall through to the UA string
  }

  const ua = source.userAgent || '';
  // "Edg/" is Chromium Edge ("Edge/" was the legacy engine, which cannot run
  // Chrome extensions at all — matched only so this stays honest).
  if (/\bEdge?\//.test(ua)) return 'edge';
  return 'chrome';
};

const BROWSER = detectBrowser();

// Number of times a rung's key must be pressed.
const pressesFor = (step) => (BROWSER === 'edge' && step.strategy === 'tab' ? 2 : 1);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* ------------------------------------------------------------------ *
 * Native messaging                                                    *
 * ------------------------------------------------------------------ */

let port = null;
const pending = new Map();

function dropPending(reason) {
  for (const [, entry] of pending) {
    clearTimeout(entry.timer);
    entry.resolve({ ok: false, error: reason });
  }
  pending.clear();
}

function ensurePort() {
  if (port) return port;

  port = chrome.runtime.connectNative(NATIVE_HOST);

  port.onMessage.addListener((msg) => {
    const entry = msg && msg.requestId ? pending.get(msg.requestId) : null;
    if (!entry) return;
    pending.delete(msg.requestId);
    clearTimeout(entry.timer);
    entry.resolve(msg);
  });

  port.onDisconnect.addListener(() => {
    // Reading lastError is required to avoid an unchecked-runtime-error warning.
    const err = chrome.runtime.lastError;
    port = null;
    dropPending((err && err.message) || 'native host disconnected');
  });

  return port;
}

function sendNative(payload, timeoutMs = NATIVE_TIMEOUT_MS) {
  return new Promise((resolve) => {
    let p;
    try {
      p = ensurePort();
    } catch (e) {
      resolve({ ok: false, error: String((e && e.message) || e) });
      return;
    }

    const requestId = crypto.randomUUID();
    const timer = setTimeout(() => {
      pending.delete(requestId);
      resolve({ ok: false, error: 'native timeout' });
    }, timeoutMs);

    pending.set(requestId, { resolve, timer });

    try {
      p.postMessage({ ...payload, requestId });
    } catch (e) {
      clearTimeout(timer);
      pending.delete(requestId);
      resolve({ ok: false, error: String((e && e.message) || e) });
    }
  });
}

/* ------------------------------------------------------------------ *
 * MAIN-world page helpers                                             *
 * ------------------------------------------------------------------ */

// These are injected with world: 'MAIN' so they can read the real input values
// and call the page's own doLogin(). Each one must be fully self-contained.

const pageState = () => {
  const user = document.getElementById('i_user');
  const pass = document.getElementById('i_pass');
  return {
    focused: document.hasFocus() && document.visibilityState === 'visible',
    hasFields: Boolean(user && pass),
    filled: Boolean(user && user.value && pass && pass.value),
    hasDoLogin: typeof doLogin === 'function',
  };
};

const focusPassword = () => {
  const pass = document.getElementById('i_pass');
  if (!pass) return false;
  pass.focus();
  return document.activeElement === pass;
};

const callDoLogin = () => {
  if (typeof doLogin === 'function') {
    doLogin();
    return true;
  }
  const btn = document.getElementById('loginButtonId');
  if (btn && !btn.disabled) {
    btn.click();
    return true;
  }
  return false;
};

const waitForCredentials = (timeoutMs) =>
  new Promise((resolve) => {
    const startedAt = Date.now();
    const step = () => {
      const user = document.getElementById('i_user');
      const pass = document.getElementById('i_pass');
      if (user && user.value && pass && pass.value) {
        resolve(true);
        return;
      }
      if (Date.now() - startedAt >= timeoutMs) {
        resolve(false);
        return;
      }
      setTimeout(step, 200);
    };
    step();
  });

const runInPage = async (tabId, frameId, func, args = []) => {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId, frameIds: [frameId] },
      world: 'MAIN',
      func,
      args,
    });
    return results && results[0] ? results[0].result : undefined;
  } catch (e) {
    return undefined;
  }
};

/* ------------------------------------------------------------------ *
 * Login flow                                                          *
 * ------------------------------------------------------------------ */

const pageHasFocus = () => document.hasFocus();

const isPageFocused = async (tabId, frameId) =>
  (await runInPage(tabId, frameId, pageHasFocus)) === true;

const handleLoginPage = async (tabId, frameId) => {
  const state = await runInPage(tabId, frameId, pageState);
  if (!state || !state.hasFields) return { ok: false, reason: 'no-login-form', browser: BROWSER };

  if (state.filled) {
    const submitted = await runInPage(tabId, frameId, callDoLogin);
    return { ok: Boolean(submitted), reason: 'already-filled', browser: BROWSER };
  }

  await runInPage(tabId, frameId, focusPassword);

  let usedStrategy = null;

  for (const step of UNLOCK_LADDER) {
    const presses = pressesFor(step);
    let delivered = 0;
    let failure = null;

    for (let i = 0; i < presses; i += 1) {
      // Re-check focus before every keystroke: focus may have moved since the
      // content script asked, or since the previous key of this pair.
      if (!(await isPageFocused(tabId, frameId))) {
        return { ok: false, reason: 'unfocused', strategy: usedStrategy, browser: BROWSER };
      }

      const res = await sendNative({ type: 'unlock', strategy: step.strategy });
      if (!res || res.ok !== true) {
        failure = res;
        break;
      }
      delivered += 1;

      if (i + 1 < presses) await sleep(UNLOCK_REPEAT_GAP_MS);
    }

    if (failure) {
      // Nothing has been sent yet, so the host itself is unreachable and
      // climbing further cannot help. If a later rung failed we may still have
      // unlocked the fields, so fall through and let the caller retry.
      if (!usedStrategy && delivered === 0) {
        return {
          ok: false,
          reason: (failure && failure.error) || 'key-agent-unavailable',
          browser: BROWSER,
        };
      }
      break;
    }

    usedStrategy = step.strategy;

    const visible = await runInPage(tabId, frameId, waitForCredentials, [step.pollMs]);
    if (visible) {
      if (step.submitsPage) {
        // Enter already fired the page's keyLogin(); do not submit twice.
        return { ok: true, strategy: step.strategy, submittedBy: 'page', browser: BROWSER };
      }
      const submitted = await runInPage(tabId, frameId, callDoLogin);
      return {
        ok: Boolean(submitted),
        strategy: step.strategy,
        submittedBy: 'doLogin',
        browser: BROWSER,
      };
    }
  }

  return { ok: false, reason: 'credentials-not-revealed', strategy: usedStrategy, browser: BROWSER };
};

/* ------------------------------------------------------------------ *
 * Wiring                                                              *
 * ------------------------------------------------------------------ */

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== 'loginForm' || !sender.tab) return false;

  handleLoginPage(sender.tab.id, sender.frameId ?? 0)
    .then(sendResponse)
    .catch((e) => sendResponse({ ok: false, reason: String((e && e.message) || e) }));

  return true; // keep the message channel open for the async response
});
