// background.js — Service worker for SSE management and message relay

let activePort = null;
let activeController = null;

// Listen for popup connections via port
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "enrichment") return;

  activePort = port;

  port.onMessage.addListener(async (msg) => {
    if (msg.type === "START_ENRICHMENT") {
      await startEnrichment(msg.data, port);
    } else if (msg.type === "CANCEL_ENRICHMENT") {
      cancelEnrichment();
    }
  });

  port.onDisconnect.addListener(() => {
    activePort = null;
    cancelEnrichment();
  });
});

// Listen for content script messages (LinkedIn profile data)
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "LINKEDIN_PROFILE_DATA") {
    // Store latest profile data for popup to read
    chrome.storage.session.set({ linkedinProfile: msg.data }).catch(() => {
      // Fallback: chrome.storage.session may not be available
      chrome.storage.local.set({ _linkedinProfile: msg.data });
    });
    sendResponse({ ok: true });
  }
  return false;
});

/**
 * Open SSE connection to backend and relay events to popup.
 */
async function startEnrichment(params, port) {
  cancelEnrichment();

  const settings = await chrome.storage.local.get(["apiKey", "serverUrl"]);
  const serverUrl = (settings.serverUrl || "http://localhost:8000").replace(
    /\/+$/,
    ""
  );
  const apiKey = settings.apiKey || "";

  const url = new URL(`${serverUrl}/enrich/stream`);
  url.searchParams.set("name", params.name);
  url.searchParams.set("company", params.company || "");
  url.searchParams.set("use_case", params.use_case || "sales");
  if (params.location) {
    url.searchParams.set("location", params.location);
  }

  activeController = new AbortController();

  try {
    const response = await fetch(url.toString(), {
      method: "GET",
      headers: {
        "X-API-Key": apiKey,
        Accept: "text/event-stream",
      },
      signal: activeController.signal,
    });

    if (!response.ok) {
      if (response.status === 429) {
        const retryAfter = response.headers.get("Retry-After") || "60";
        port.postMessage({
          type: "error",
          data: {
            message: "Rate limit exceeded",
            retryAfter: parseInt(retryAfter, 10),
          },
        });
        return;
      }

      let detail = "";
      try {
        detail = await response.text();
      } catch (_) {}

      port.postMessage({
        type: "error",
        data: {
          message:
            response.status === 401
              ? "Invalid API key. Check extension settings."
              : `Server error (${response.status}): ${detail}`.slice(0, 200),
        },
      });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let inactivityTimer = null;
    const INACTIVITY_TIMEOUT = 45000; // 45s

    const resetTimer = () => {
      if (inactivityTimer) clearTimeout(inactivityTimer);
      inactivityTimer = setTimeout(() => {
        cancelEnrichment();
        if (activePort) {
          activePort.postMessage({
            type: "error",
            data: { message: "Connection timed out — no data received for 45s" },
          });
        }
      }, INACTIVITY_TIMEOUT);
    };

    try {
      resetTimer();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        resetTimer();
        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events from buffer
        const result = parseSSEBuffer(buffer);
        buffer = result.remaining;

        for (const event of result.parsed) {
          try {
            if (activePort) {
              activePort.postMessage(event);
            }
          } catch (_) {
            // Port disconnected
            cancelEnrichment();
            return;
          }
        }
      }
    } finally {
      if (inactivityTimer) clearTimeout(inactivityTimer);
    }
  } catch (e) {
    if (e.name === "AbortError") return;
    if (activePort) {
      activePort.postMessage({
        type: "error",
        data: { message: `Connection failed: ${e.message}` },
      });
    }
  }
}

/**
 * Parse SSE events from a text buffer.
 * Returns { parsed: [{type, data}], remaining: string }
 */
function parseSSEBuffer(buffer) {
  const parsed = [];
  const blocks = buffer.split("\n\n");

  // Last block may be incomplete — keep it in remaining
  const remaining = blocks.pop() || "";

  for (const block of blocks) {
    if (!block.trim()) continue;

    let eventType = "message";
    const dataLines = [];

    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        dataLines.push(line.slice(6));
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5));
      }
    }

    if (dataLines.length > 0) {
      const dataStr = dataLines.join("\n");
      try {
        const data = JSON.parse(dataStr);
        parsed.push({ type: eventType, data });
      } catch (_) {
        console.warn(`SSE parse error [${eventType}]:`, dataStr.slice(0, 200));
      }
    }
  }

  return { parsed, remaining };
}

/**
 * Cancel the active SSE connection.
 */
function cancelEnrichment() {
  if (activeController) {
    activeController.abort();
    activeController = null;
  }
}
