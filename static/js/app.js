(function () {
  "use strict";

  let selectedUseCase = "sales";
  let countdownTimer = null;

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".toggle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".toggle-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedUseCase = btn.dataset.useCase;
      });
    });

    document.getElementById("enrich-form").addEventListener("submit", handleSubmit);

    document.getElementById("try-example").addEventListener("click", (e) => {
      e.preventDefault();
      document.getElementById("input-name").value = "Guillermo Rauch";
      document.getElementById("input-company").value = "Vercel";
      document.getElementById("input-name").focus();
    });

    document.getElementById("btn-copy-email").addEventListener("click", () => {
      const t = document.getElementById("email-value").textContent;
      if (t) copy(t, "btn-copy-email");
    });

    document.getElementById("btn-copy-points").addEventListener("click", () => {
      const pts = Array.from(document.querySelectorAll("#points-list li"))
        .map((li, i) => `${i + 1}. ${li.textContent}`).join("\n");
      if (pts) copy(pts, "btn-copy-points");
    });

    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach((a) => {
      a.addEventListener("click", (e) => {
        const target = document.querySelector(a.getAttribute("href"));
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });
  });

  // ── Submit ─────────────────────────────────────────────────────

  async function handleSubmit(e) {
    e.preventDefault();
    const name = document.getElementById("input-name").value.trim();
    const company = document.getElementById("input-company").value.trim();
    const apiKey = document.getElementById("input-api-key").value.trim();
    if (!name) return;

    resetCard();
    show("results"); show("loading"); hide("error-box"); hide("profile-card");
    document.getElementById("loading-text").textContent = `Researching ${name}...`;
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }

    const btn = document.getElementById("btn-submit");
    btn.disabled = true;
    btn.querySelector(".btn-text").textContent = "Enriching...";

    const headers = { "Content-Type": "application/json" };
    if (apiKey) headers["X-API-Key"] = apiKey;

    try {
      const resp = await fetch("/enrich", {
        method: "POST", headers,
        body: JSON.stringify({ name, company, use_case: selectedUseCase }),
      });

      if (resp.status === 429) { const d = await resp.json().catch(() => ({})); showRateLimit(d.retry_after || 60); return; }
      if (resp.status === 401 || resp.status === 403) { showError("Invalid or missing API key."); return; }
      if (!resp.ok) { const t = await resp.text().catch(() => ""); showError(`Error ${resp.status}: ${t}`.slice(0, 300)); return; }
      renderProfile(await resp.json());
    } catch (err) {
      showError(`Connection failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.querySelector(".btn-text").textContent = "Enrich";
      hide("loading");
    }
  }

  function show(id) { document.getElementById(id).classList.remove("hidden"); }
  function hide(id) { document.getElementById(id).classList.add("hidden"); }

  function resetCard() {
    document.querySelectorAll(".badge-cached").forEach((el) => el.remove());
    ["profile-links","section-about","section-email","section-github",
     "section-skills","section-news","section-points",
     "link-github","link-linkedin","link-website","link-twitter",
     "detail-education","detail-previous"].forEach((id) => {
      const el = document.getElementById(id); if (el) el.classList.add("hidden");
    });
    ["gh-languages","skills-list","news-list","points-list","confidence-scores"]
      .forEach((id) => { const el = document.getElementById(id); if (el) el.innerHTML = ""; });
  }

  function renderProfile(data) {
    show("profile-card");
    const p = data.profile || {};

    document.getElementById("profile-name").textContent = p.name || "Unknown";
    document.getElementById("profile-role").textContent =
      p.role ? `${p.role}${p.company ? " @ " + p.company : ""}` : p.company || "";
    document.getElementById("profile-location").textContent = p.location || "";

    const lb = document.getElementById("badge-latency");
    lb.textContent = `${(data.latency_ms / 1000).toFixed(1)}s`;
    if (data.latency_ms < 500) lb.insertAdjacentHTML("afterend", ' <span class="badge badge-cached">Cached</span>');
    document.getElementById("badge-sources").textContent =
      `${data.sources_searched ? data.sources_searched.length : 0} sources`;

    let hasLinks = false;
    [["link-github", p.github?.url],
     ["link-linkedin", p.linkedin_url],
     ["link-website", p.website],
     ["link-twitter", p.twitter_handle ? `https://twitter.com/${p.twitter_handle.replace("@","")}` : null]
    ].forEach(([id, url]) => {
      if (url) { const el = document.getElementById(id); el.href = url; el.classList.remove("hidden"); hasLinks = true; }
    });
    if (hasLinks) show("profile-links");

    if (p.bio || p.education?.length || p.previous_companies?.length) {
      show("section-about");
      document.getElementById("about-bio").textContent = p.bio || "";
      if (p.education?.length) { show("detail-education"); document.getElementById("education-value").textContent = p.education.join(", "); }
      if (p.previous_companies?.length) { show("detail-previous"); document.getElementById("previous-value").textContent = p.previous_companies.join(", "); }
    }

    if (p.email) {
      show("section-email");
      document.getElementById("email-value").textContent = p.email;
      const c = p.confidence?.email || 0;
      const cb = document.getElementById("email-confidence");
      cb.textContent = `${Math.round(c * 100)}%`;
      cb.className = `badge ${c >= 0.8 ? "badge-high" : c >= 0.6 ? "badge-medium" : "badge-low"}`;
    }

    if (p.github?.username) {
      show("section-github");
      document.getElementById("gh-repos").textContent = p.github.public_repos || 0;
      document.getElementById("gh-followers").textContent = p.github.followers || 0;
      document.getElementById("gh-activity").textContent = (p.github.activity_level || "-").replace(/_/g, " ");
      const lc = document.getElementById("gh-languages");
      (p.github.top_languages || []).slice(0, 6).forEach((l) => {
        const s = document.createElement("span"); s.className = "tag"; s.textContent = l; lc.appendChild(s);
      });
      if (p.github.url) document.getElementById("gh-link").href = p.github.url;
    }

    if (p.skills?.length) {
      show("section-skills");
      const sl = document.getElementById("skills-list");
      p.skills.forEach((s) => { const sp = document.createElement("span"); sp.className = "tag"; sp.textContent = s; sl.appendChild(sp); });
    }

    if (p.recent_news?.length) {
      show("section-news");
      const nl = document.getElementById("news-list");
      p.recent_news.slice(0, 5).forEach((item) => {
        const li = document.createElement("li");
        const txt = typeof item === "string" ? item : JSON.stringify(item);
        const m = txt.match(/(https?:\/\/\S+)/);
        if (m) { const a = document.createElement("a"); a.href = m[1]; a.target = "_blank"; a.rel = "noopener"; a.textContent = txt.replace(m[1], "").trim() || m[1]; li.appendChild(a); }
        else li.textContent = txt;
        nl.appendChild(li);
      });
    }

    if (data.talking_points?.length) {
      show("section-points");
      const pl = document.getElementById("points-list");
      data.talking_points.forEach((pt) => { const li = document.createElement("li"); li.textContent = pt; pl.appendChild(li); });
    }

    if (p.confidence) {
      const cb = document.getElementById("confidence-scores");
      ["name","company","role","email","bio","github"].forEach((f) => {
        const v = p.confidence[f];
        if (v > 0) { const s = document.createElement("span"); s.className = "conf-item"; s.innerHTML = `${f}: <span class="conf-score">${Math.round(v*100)}%</span>`; cb.appendChild(s); }
      });
    }

    if (data.sources_searched) {
      const urls = p.sources ? p.sources.length : 0;
      document.getElementById("sources-summary").textContent = `${data.sources_searched.length} tools, ${urls} URLs`;
    }

    document.getElementById("profile-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showError(msg) { hide("loading"); show("error-box"); document.getElementById("error-message").textContent = msg; }

  function showRateLimit(sec) {
    hide("loading"); show("error-box");
    document.getElementById("error-message").textContent = "Rate limit exceeded.";
    const cd = document.getElementById("error-countdown"); cd.classList.remove("hidden");
    let r = sec; cd.textContent = `Try again in ${r}s`;
    countdownTimer = setInterval(() => {
      if (--r <= 0) { clearInterval(countdownTimer); countdownTimer = null; hide("error-box"); cd.classList.add("hidden"); }
      else cd.textContent = `Try again in ${r}s`;
    }, 1000);
  }

  async function copy(text, btnId) {
    try {
      await navigator.clipboard.writeText(text);
      const b = document.getElementById(btnId), orig = b.innerHTML;
      b.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
      setTimeout(() => b.innerHTML = orig, 1500);
    } catch (_) {}
  }
})();
