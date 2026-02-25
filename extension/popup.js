// popup.js — Progressive rendering from SSE events

(function () {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────

  let port = null;
  let selectedUseCase = "sales";
  let profileData = null;
  let enrichmentStartTime = 0;
  let isEnriching = false;
  let countdownTimer = null;

  const PHASES = {
    planner: { weight: 10, done: false },
    deterministic_tools: { weight: 25, done: false },
    planner_dependent: { weight: 25, done: false },
    email: { weight: 10, done: false },
    extraction: { weight: 30, done: false },
  };

  // ── Init ───────────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", async () => {
    // Use case toggles
    document.querySelectorAll(".toggle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".toggle-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedUseCase = btn.dataset.useCase;
      });
    });

    // Enrich button
    document.getElementById("btn-enrich").addEventListener("click", startEnrichment);

    // Copy buttons
    document.getElementById("btn-copy-email").addEventListener("click", () => {
      const email = document.getElementById("email-value").textContent;
      if (email) copyToClipboard(email, "btn-copy-email");
    });

    document.getElementById("btn-copy-points").addEventListener("click", () => {
      const points = Array.from(document.querySelectorAll("#points-list li"))
        .map((li, i) => `${i + 1}. ${li.textContent}`)
        .join("\n");
      if (points) copyToClipboard(points, "btn-copy-points");
    });

    await checkLinkedInProfile();
    await checkBackendHealth();
  });

  // ── Backend Health Check ───────────────────────────────────────────

  async function checkBackendHealth() {
    try {
      const settings = await chrome.storage.local.get(["serverUrl"]);
      const serverUrl = (settings.serverUrl || "http://localhost:8000").replace(/\/+$/, "");
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3000);

      const resp = await fetch(`${serverUrl}/health`, { signal: controller.signal });
      clearTimeout(timeout);

      if (!resp.ok) throw new Error("unhealthy");
    } catch (_) {
      showError("Backend unreachable. Check server URL in settings.");
      document.getElementById("btn-enrich").disabled = true;
    }
  }

  // ── LinkedIn Profile Detection ─────────────────────────────────────

  async function checkLinkedInProfile() {
    // Check session storage first (set by content.js)
    try {
      const stored = await chrome.storage.session.get("linkedinProfile");
      if (stored.linkedinProfile && stored.linkedinProfile.name) {
        profileData = stored.linkedinProfile;
        showMainContent();
        return;
      }
    } catch (_) {
      // session storage not available, try local fallback
      try {
        const stored = await chrome.storage.local.get("_linkedinProfile");
        if (stored._linkedinProfile && stored._linkedinProfile.name) {
          profileData = stored._linkedinProfile;
          showMainContent();
          return;
        }
      } catch (_) {}
    }

    // Fallback: inject script into active tab to extract directly
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && tab.url && tab.url.includes("linkedin.com/in/")) {
        const results = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => {
            // Quick extraction from DOM
            const h1 = document.querySelector("h1");
            const name = h1 ? h1.innerText.trim() : "";

            let company = "";
            const headline = document.querySelector("div.text-body-medium");
            if (headline) {
              const match = headline.innerText.match(/\bat\s+(.+?)(?:\s*[|·\-,]|$)/i);
              if (match) company = match[1].trim();
            }

            // Title fallback
            if (!name || !company) {
              const title = document.title || "";
              const titleMatch = title.match(/^(.+?)\s[-–|]/);
              if (!name && titleMatch) {
                const n = titleMatch[1].trim();
                if (n !== "LinkedIn") return { name: n, company, url: location.href };
              }
              if (!company) {
                const pipeIdx = title.lastIndexOf("|");
                if (pipeIdx > 0) {
                  const segments = title.substring(0, pipeIdx).trim().split(/\s[-–]\s/);
                  if (segments.length >= 3) company = segments[segments.length - 1].trim();
                }
              }
            }

            return { name: name || "", company: company || "", url: location.href };
          },
        });

        if (results && results[0] && results[0].result && results[0].result.name) {
          profileData = results[0].result;
          showMainContent();
          return;
        }
      }
    } catch (_) {}

    showNotLinkedIn();
  }

  function showNotLinkedIn() {
    document.getElementById("not-linkedin").classList.remove("hidden");
    document.getElementById("main-content").classList.add("hidden");
  }

  function showMainContent() {
    document.getElementById("not-linkedin").classList.add("hidden");
    document.getElementById("main-content").classList.remove("hidden");

    const nameEl = document.getElementById("person-name");
    const companyEl = document.getElementById("person-company");
    nameEl.textContent = profileData.name;
    nameEl.classList.remove("skeleton-text");
    companyEl.textContent = profileData.company || "Unknown company";
    companyEl.classList.remove("skeleton-text");
  }

  // ── Enrichment Flow ────────────────────────────────────────────────

  function startEnrichment() {
    if (isEnriching || !profileData) return;
    isEnriching = true;

    resetSections();
    document.getElementById("btn-enrich").disabled = true;
    document.getElementById("btn-enrich").textContent = "Enriching...";
    showProgress("Connecting...");
    enrichmentStartTime = Date.now();

    // Connect to background service worker
    port = chrome.runtime.connect({ name: "enrichment" });
    port.onMessage.addListener(handleEvent);

    port.onDisconnect.addListener(() => {
      if (isEnriching) {
        showError("Connection lost");
        finishEnrichment();
      }
    });

    port.postMessage({
      type: "START_ENRICHMENT",
      data: {
        name: profileData.name,
        company: profileData.company,
        use_case: selectedUseCase,
      },
    });
  }

  function finishEnrichment() {
    isEnriching = false;
    document.getElementById("btn-enrich").disabled = false;
    document.getElementById("btn-enrich").textContent = "Enrich";
  }

  // ── Event Handling ─────────────────────────────────────────────────

  function handleEvent(event) {
    switch (event.type) {
      case "status":
        handleStatus(event.data);
        break;
      case "tool_result":
        handleToolResult(event.data);
        break;
      case "email_found":
        handleEmailFound(event.data);
        break;
      case "profile":
        handleProfile(event.data);
        break;
      case "talking_points":
        handleTalkingPoints(event.data);
        break;
      case "cache_hit":
        handleCacheHit(event.data);
        break;
      case "complete":
        handleComplete(event.data);
        break;
      case "error":
        if (event.data.retryAfter) {
          showRateLimitCountdown(event.data.retryAfter);
        } else {
          showError(event.data.message || "Unknown error");
        }
        finishEnrichment();
        break;
      case "heartbeat":
        break; // ignore keepalive
    }
  }

  function handleStatus(data) {
    const { phase, status } = data;

    if (status === "started") {
      const labels = {
        planner: "Planning research strategy...",
        deterministic_tools: "Searching GitHub, news, communities...",
        planner_dependent: "Running web search & browser...",
        email: "Finding email address...",
        extraction: "Extracting profile with AI...",
      };
      updateProgress(labels[phase] || `Running ${phase}...`);
    }

    if (status === "completed" && PHASES[phase]) {
      PHASES[phase].done = true;
      updateProgressBar();
    }
  }

  function handleToolResult(data) {
    if (data.tool === "github" && data.success && data.preview) {
      showGitHubPreview(data.preview);
    }
    if (data.tool === "news" && data.success && data.preview) {
      showNewsPreview(data.preview);
    }
  }

  function showGitHubPreview(preview) {
    const section = document.getElementById("section-github");
    section.classList.remove("hidden");

    const reposMatch = preview.match(/Public Repos:\s*(\d+)/);
    const followersMatch = preview.match(/Followers:\s*(\d+)/);
    const activityMatch = preview.match(/Activity Level:\s*(\w+)/);
    const langsMatch = preview.match(/Top Languages:\s*(.+)/);
    const urlMatch = preview.match(/URL:\s*(https:\/\/github\.com\/\S+)/);

    if (reposMatch) document.getElementById("gh-repos").textContent = reposMatch[1];
    if (followersMatch) document.getElementById("gh-followers").textContent = followersMatch[1];
    if (activityMatch) document.getElementById("gh-activity").textContent = activityMatch[1].replace(/_/g, " ");

    if (langsMatch) {
      const container = document.getElementById("gh-languages");
      container.innerHTML = "";
      langsMatch[1].split(",").slice(0, 6).forEach((lang) => {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = lang.trim();
        container.appendChild(tag);
      });
    }

    if (urlMatch) {
      document.getElementById("gh-link").href = urlMatch[1];
    }
  }

  function showNewsPreview(preview) {
    const section = document.getElementById("section-news");
    section.classList.remove("hidden");

    const list = document.getElementById("news-list");
    list.innerHTML = "";

    // Parse news lines — look for title/URL pairs
    const lines = preview.split("\n").filter((l) => l.trim()).slice(0, 5);
    for (const line of lines) {
      const li = document.createElement("li");
      const urlMatch = line.match(/(https?:\/\/\S+)/);
      if (urlMatch) {
        const a = document.createElement("a");
        a.href = urlMatch[1];
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = line.replace(urlMatch[1], "").replace(/[-–|]\s*$/, "").trim() || urlMatch[1];
        li.appendChild(a);
      } else {
        li.textContent = line;
      }
      list.appendChild(li);
    }
  }

  function handleEmailFound(data) {
    if (!data.email) return;

    const section = document.getElementById("section-email");
    section.classList.remove("hidden");
    document.getElementById("email-value").textContent = data.email;

    // Confidence badge
    const confBadge = document.getElementById("email-confidence");
    const conf = data.confidence || 0;
    confBadge.textContent = `${Math.round(conf * 100)}%`;
    confBadge.className = conf >= 0.8 ? "badge badge-high" : conf >= 0.6 ? "badge badge-medium" : "badge badge-low";

    // Source badge
    const sourceBadge = document.getElementById("email-source");
    const sourceLabels = {
      github_public: "GitHub",
      hunter: "Hunter.io",
      smtp_verified: "SMTP",
      regex_scan: "Regex",
      cached: "Cached",
    };
    const sourceKey = (data.source || "").split(":")[0];
    sourceBadge.textContent = sourceLabels[sourceKey] || data.source || "";
  }

  function handleProfile(data) {
    if (!data || !data.name) return;

    // About section
    const bioSection = document.getElementById("section-bio");
    bioSection.classList.remove("hidden");

    if (data.role) {
      document.getElementById("profile-role").textContent = data.role;
    }
    if (data.bio) {
      document.getElementById("profile-bio").textContent = data.bio;
    }
    if (data.location) {
      document.getElementById("profile-location").classList.remove("hidden");
      document.getElementById("location-value").textContent = data.location;
    }

    // GitHub from full profile
    if (data.github && data.github.username) {
      const section = document.getElementById("section-github");
      section.classList.remove("hidden");
      document.getElementById("gh-repos").textContent = data.github.public_repos || 0;
      document.getElementById("gh-followers").textContent = data.github.followers || 0;
      document.getElementById("gh-activity").textContent =
        (data.github.activity_level || "unknown").replace(/_/g, " ");

      if (data.github.top_languages && data.github.top_languages.length) {
        const container = document.getElementById("gh-languages");
        container.innerHTML = "";
        data.github.top_languages.slice(0, 6).forEach((lang) => {
          const tag = document.createElement("span");
          tag.className = "tag";
          tag.textContent = lang;
          container.appendChild(tag);
        });
      }

      if (data.github.url) {
        document.getElementById("gh-link").href = data.github.url;
      }
    }

    // News from profile extraction
    if (data.recent_news && data.recent_news.length) {
      const section = document.getElementById("section-news");
      section.classList.remove("hidden");
      const list = document.getElementById("news-list");
      list.innerHTML = "";
      data.recent_news.slice(0, 5).forEach((item) => {
        const li = document.createElement("li");
        li.textContent = typeof item === "string" ? item : JSON.stringify(item);
        list.appendChild(li);
      });
    }

    // Email from profile (if not already shown by email_found event)
    if (data.email && !document.getElementById("email-value").textContent) {
      handleEmailFound({
        email: data.email,
        confidence: data.confidence && data.confidence.email ? data.confidence.email : 0,
        source: "extraction",
      });
    }
  }

  function handleTalkingPoints(data) {
    const points = data.points || [];
    if (!points.length) return;

    const section = document.getElementById("section-talking-points");
    section.classList.remove("hidden");

    const list = document.getElementById("points-list");
    list.innerHTML = "";
    points.forEach((point) => {
      const li = document.createElement("li");
      li.textContent = point;
      list.appendChild(li);
    });
  }

  function handleCacheHit(data) {
    document.getElementById("badge-cached").classList.remove("hidden");

    if (data.profile) handleProfile(data.profile);
    if (data.talking_points) handleTalkingPoints({ points: data.talking_points });

    if (data.profile && data.profile.email) {
      handleEmailFound({
        email: data.profile.email,
        confidence: data.profile.confidence ? data.profile.confidence.email || 0 : 0,
        source: "cached",
      });
    }
  }

  function handleComplete(data) {
    // Latency badge
    const latencyBadge = document.getElementById("badge-latency");
    const latencyMs = data.latency_ms || (Date.now() - enrichmentStartTime);
    latencyBadge.textContent = `${(latencyMs / 1000).toFixed(1)}s`;
    latencyBadge.classList.remove("hidden");

    // Fill progress
    document.getElementById("progress-fill").style.width = "100%";
    document.getElementById("progress-text").textContent = "Complete";

    // Fill any gaps from earlier events
    if (data.profile) handleProfile(data.profile);
    if (data.talking_points) handleTalkingPoints({ points: data.talking_points });

    // Hide progress after a moment
    setTimeout(() => {
      document.getElementById("progress-section").classList.add("hidden");
    }, 1500);

    finishEnrichment();
  }

  // ── Progress Bar ───────────────────────────────────────────────────

  function showProgress(text) {
    document.getElementById("progress-section").classList.remove("hidden");
    document.getElementById("progress-text").textContent = text;
  }

  function updateProgress(text) {
    document.getElementById("progress-text").textContent = text;
  }

  function updateProgressBar() {
    let completed = 0;
    let total = 0;
    for (const p of Object.values(PHASES)) {
      total += p.weight;
      if (p.done) completed += p.weight;
    }
    document.getElementById("progress-fill").style.width = `${Math.round((completed / total) * 100)}%`;
  }

  function resetSections() {
    for (const p of Object.values(PHASES)) p.done = false;
    document.getElementById("progress-fill").style.width = "0%";

    ["section-bio", "section-email", "section-github", "section-news",
      "section-talking-points", "section-errors"].forEach((id) => {
      document.getElementById(id).classList.add("hidden");
    });

    document.getElementById("email-value").textContent = "";
    document.getElementById("badge-cached").classList.add("hidden");
    document.getElementById("badge-latency").classList.add("hidden");
  }

  // ── Utilities ──────────────────────────────────────────────────────

  function showError(message) {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    document.getElementById("section-errors").classList.remove("hidden");
    document.getElementById("error-message").textContent = message;
  }

  function showRateLimitCountdown(seconds) {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    const section = document.getElementById("section-errors");
    const msgEl = document.getElementById("error-message");
    section.classList.remove("hidden");

    let remaining = seconds;
    msgEl.textContent = `Rate limit exceeded. Try again in ${remaining}s`;

    countdownTimer = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        clearInterval(countdownTimer);
        countdownTimer = null;
        section.classList.add("hidden");
      } else {
        msgEl.textContent = `Rate limit exceeded. Try again in ${remaining}s`;
      }
    }, 1000);
  }

  async function copyToClipboard(text, buttonId) {
    try {
      await navigator.clipboard.writeText(text);
      const btn = document.getElementById(buttonId);
      const original = btn.innerHTML;
      btn.innerHTML = "&#10003;";
      setTimeout(() => { btn.innerHTML = original; }, 1500);
    } catch (_) {}
  }
})();
