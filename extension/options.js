document.addEventListener("DOMContentLoaded", async () => {
  const settings = await chrome.storage.local.get(["serverUrl", "apiKey"]);
  document.getElementById("server-url").value = settings.serverUrl || "http://localhost:8000";
  document.getElementById("api-key").value = settings.apiKey || "";

  document.getElementById("save").addEventListener("click", async () => {
    await chrome.storage.local.set({
      serverUrl: document.getElementById("server-url").value.replace(/\/+$/, ""),
      apiKey: document.getElementById("api-key").value,
    });
    const msg = document.getElementById("saved-msg");
    msg.style.display = "inline";
    setTimeout(() => { msg.style.display = "none"; }, 2000);
  });
});
