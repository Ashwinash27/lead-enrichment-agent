(function () {
  "use strict";

  let selectedUseCase = "sales";
  let countdownTimer = null;

  // ── Init ─────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", () => {
    // Use case toggles
    document.querySelectorAll(".toggle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".toggle-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedUseCase = btn.dataset.useCase;
      });
    });

    // Form submit
    document.getElementById("enrich-form").addEventListener("submit", handleSubmit);

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
  });

  // ── Submit ───────────────────────────────────────────────────

  async function handleSubmit(e) {
    e.preventDefault();

    const name = document.getElementById("input-name").value.trim();
    const company = document.getElementById("input-company").value.trim();
    if (!name) return;

    // Reset UI
    const results = document.getElementById("results");
    results.classList.remove("hidden");
    document.getElementById("loading").classList.remove("hidden");
    document.getElementById("loading-text").textContent = `Researching ${name}...`;
    document.getElementById("error-box").classList.add("hidden");
    document.getElementById("profile-card").classList.add("hidden");
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }

    const btn = document.getElementById("btn-submit");
    btn.disabled = true;
    btn.textContent = "Enriching...";

    try {
      const resp = await fetch("/enrich", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, company, use_case: selectedUseCase }),
      });

      if (resp.status === 429) {
        const data = await resp.json().catch(() => ({}));
        showRateLimit(data.retry_after || 60);
        return;
      }

      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        showError(`Server error (${resp.status}): ${text}`.slice(0, 300));
        return;
      }

      const data = await resp.json();
      renderProfile(data);
    } catch (err) {
      showError(`Connection failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Enrich";
      document.getElementById("loading").classList.add("hidden");
    }
  }

  // ── Render Profile ───────────────────────────────────────────

  function renderProfile(data) {
    const card = document.getElementById("profile-card");
    card.classList.remove("hidden");

    const profile = data.profile || {};

    // Header
    document.getElementById("profile-name").textContent = profile.name || "Unknown";
    document.getElementById("profile-role").textContent =
      profile.role ? `${profile.role}${profile.company ? " @ " + profile.company : ""}` : profile.company || "";
    document.getElementById("profile-location").textContent = profile.location || "";

    // Badges
    document.getElementById("badge-latency").textContent = `${(data.latency_ms / 1000).toFixed(1)}s`;
    document.getElementById("badge-sources").textContent =
      `${data.sources_searched ? data.sources_searched.length : 0} sources`;

    // Links
    const linksEl = document.getElementById("profile-links");
    let hasLinks = false;

    if (profile.github && profile.github.url) {
      const el = document.getElementById("link-github");
      el.href = profile.github.url;
      el.classList.remove("hidden");
      hasLinks = true;
    }
    if (profile.linkedin_url) {
      const el = document.getElementById("link-linkedin");
      el.href = profile.linkedin_url;
      el.classList.remove("hidden");
      hasLinks = true;
    }
    if (profile.website) {
      const el = document.getElementById("link-website");
      el.href = profile.website;
      el.classList.remove("hidden");
      hasLinks = true;
    }
    if (profile.twitter_handle) {
      const el = document.getElementById("link-twitter");
      el.href = `https://twitter.com/${profile.twitter_handle.replace("@", "")}`;
      el.classList.remove("hidden");
      hasLinks = true;
    }
    if (hasLinks) linksEl.classList.remove("hidden");

    // About
    if (profile.bio || profile.education?.length || profile.previous_companies?.length) {
      document.getElementById("section-about").classList.remove("hidden");
      document.getElementById("about-bio").textContent = profile.bio || "";

      if (profile.education && profile.education.length) {
        document.getElementById("detail-education").classList.remove("hidden");
        document.getElementById("education-value").textContent = profile.education.join(", ");
      }
      if (profile.previous_companies && profile.previous_companies.length) {
        document.getElementById("detail-previous").classList.remove("hidden");
        document.getElementById("previous-value").textContent = profile.previous_companies.join(", ");
      }
    }

    // Email
    if (profile.email) {
      document.getElementById("section-email").classList.remove("hidden");
      document.getElementById("email-value").textContent = profile.email;

      const conf = profile.confidence ? profile.confidence.email || 0 : 0;
      const confBadge = document.getElementById("email-confidence");
      confBadge.textContent = `${Math.round(conf * 100)}%`;
      confBadge.className = `badge ${conf >= 0.8 ? "badge-high" : conf >= 0.6 ? "badge-medium" : "badge-low"}`;
    }

    // GitHub
    if (profile.github && profile.github.username) {
      document.getElementById("section-github").classList.remove("hidden");
      document.getElementById("gh-repos").textContent = profile.github.public_repos || 0;
      document.getElementById("gh-followers").textContent = profile.github.followers || 0;
      document.getElementById("gh-activity").textContent =
        (profile.github.activity_level || "-").replace(/_/g, " ");

      const langContainer = document.getElementById("gh-languages");
      langContainer.innerHTML = "";
      (profile.github.top_languages || []).slice(0, 6).forEach((lang) => {
        const span = document.createElement("span");
        span.className = "tag";
        span.textContent = lang;
        langContainer.appendChild(span);
      });

      if (profile.github.url) {
        document.getElementById("gh-link").href = profile.github.url;
      }
    }

    // Skills
    if (profile.skills && profile.skills.length) {
      document.getElementById("section-skills").classList.remove("hidden");
      const skillsList = document.getElementById("skills-list");
      skillsList.innerHTML = "";
      profile.skills.forEach((skill) => {
        const span = document.createElement("span");
        span.className = "tag";
        span.textContent = skill;
        skillsList.appendChild(span);
      });
    }

    // News
    if (profile.recent_news && profile.recent_news.length) {
      document.getElementById("section-news").classList.remove("hidden");
      const newsList = document.getElementById("news-list");
      newsList.innerHTML = "";
      profile.recent_news.slice(0, 5).forEach((item) => {
        const li = document.createElement("li");
        const text = typeof item === "string" ? item : JSON.stringify(item);
        // Try to extract URL
        const urlMatch = text.match(/(https?:\/\/\S+)/);
        if (urlMatch) {
          const a = document.createElement("a");
          a.href = urlMatch[1];
          a.target = "_blank";
          a.rel = "noopener";
          a.textContent = text.replace(urlMatch[1], "").trim() || urlMatch[1];
          li.appendChild(a);
        } else {
          li.textContent = text;
        }
        newsList.appendChild(li);
      });
    }

    // Talking Points
    if (data.talking_points && data.talking_points.length) {
      document.getElementById("section-points").classList.remove("hidden");
      const pointsList = document.getElementById("points-list");
      pointsList.innerHTML = "";
      data.talking_points.forEach((point) => {
        const li = document.createElement("li");
        li.textContent = point;
        pointsList.appendChild(li);
      });
    }

    // Confidence scores
    if (profile.confidence) {
      const confBar = document.getElementById("confidence-scores");
      confBar.innerHTML = "";
      const fields = ["name", "company", "role", "email", "bio", "github"];
      fields.forEach((field) => {
        const val = profile.confidence[field];
        if (val > 0) {
          const span = document.createElement("span");
          span.className = "conf-item";
          span.innerHTML = `${field}: <span class="conf-score">${Math.round(val * 100)}%</span>`;
          confBar.appendChild(span);
        }
      });
    }

    // Sources summary
    if (data.sources_searched) {
      const urls = profile.sources ? profile.sources.length : 0;
      document.getElementById("sources-summary").textContent =
        `${data.sources_searched.length} tools searched, ${urls} source URLs`;
    }
  }

  // ── Error States ─────────────────────────────────────────────

  function showError(message) {
    document.getElementById("loading").classList.add("hidden");
    const box = document.getElementById("error-box");
    box.classList.remove("hidden");
    document.getElementById("error-message").textContent = message;
  }

  function showRateLimit(retryAfter) {
    document.getElementById("loading").classList.add("hidden");
    const box = document.getElementById("error-box");
    box.classList.remove("hidden");
    document.getElementById("error-message").textContent = "Rate limit exceeded.";

    const countdown = document.getElementById("error-countdown");
    countdown.classList.remove("hidden");
    let remaining = retryAfter;
    countdown.textContent = `Try again in ${remaining}s`;

    countdownTimer = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        clearInterval(countdownTimer);
        countdownTimer = null;
        box.classList.add("hidden");
        countdown.classList.add("hidden");
      } else {
        countdown.textContent = `Try again in ${remaining}s`;
      }
    }, 1000);
  }

  // ── Utilities ────────────────────────────────────────────────

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
