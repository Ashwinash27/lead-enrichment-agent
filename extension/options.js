document.addEventListener("DOMContentLoaded", async () => {
  const settings = await chrome.storage.local.get(["serverUrl", "apiKey"]);
  document.getElementById("server-url").value = settings.serverUrl || "http://localhost:8000";
  document.getElementById("api-key").value = settings.apiKey || "";

  const statusEl = document.getElementById("status-msg");

  function showStatus(message, type) {
    statusEl.textContent = message;
    statusEl.className = "status " + type;
    statusEl.style.display = "inline";
    if (type !== "info") {
      setTimeout(() => { statusEl.style.display = "none"; }, 3000);
    }
  }

  function validateUrl(url) {
    try {
      const parsed = new URL(url);
      return /^https?:$/.test(parsed.protocol);
    } catch (_) {
      return false;
    }
  }

  document.getElementById("save").addEventListener("click", async () => {
    const url = document.getElementById("server-url").value.trim();
    if (!validateUrl(url)) {
      showStatus("Invalid URL — must start with http:// or https://", "error");
      return;
    }

    await chrome.storage.local.set({
      serverUrl: url.replace(/\/+$/, ""),
      apiKey: document.getElementById("api-key").value,
    });
    showStatus("Saved!", "success");
  });

  document.getElementById("test-connection").addEventListener("click", async () => {
    const url = document.getElementById("server-url").value.trim();
    if (!validateUrl(url)) {
      showStatus("Invalid URL — must start with http:// or https://", "error");
      return;
    }

    showStatus("Testing...", "info");

    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);

      const resp = await fetch(`${url.replace(/\/+$/, "")}/health`, {
        signal: controller.signal,
      });
      clearTimeout(timeout);

      if (resp.ok) {
        showStatus("Connected!", "success");
      } else {
        showStatus(`Server returned ${resp.status}`, "error");
      }
    } catch (e) {
      if (e.name === "AbortError") {
        showStatus("Connection timed out (5s)", "error");
      } else {
        showStatus("Connection failed — is the server running?", "error");
      }
    }
  });
});
