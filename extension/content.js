// content.js — LinkedIn DOM extraction
// Extracts name + company from LinkedIn profile pages.
// Only reads visible DOM text — no scraping, no API calls.

(function () {
  "use strict";

  if (window.__leadEnrichmentInjected) return;
  window.__leadEnrichmentInjected = true;

  // ── Name extraction ────────────────────────────────────────────────

  function extractName() {
    // Cascade from most stable (semantic/aria) to LinkedIn-specific class selectors
    const selectors = [
      '[data-anonymize="person-name"]',
      '[role="heading"][aria-level="1"]',
      "h1",
      "h1.text-heading-xlarge",
      'h1[class*="text-heading"]',
      "div.ph5 h1",
      "section.pv-top-card h1",
      ".pv-text-details__left-panel h1",
    ];

    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        const text = el.innerText.trim();
        if (text.length >= 2 && text.length <= 80) return text;
      }
    }

    // Ultimate fallback: parse <title> tag
    // LinkedIn title format: "Name - Title - Company | LinkedIn"
    return extractNameFromTitle();
  }

  function extractNameFromTitle() {
    const title = document.title || "";
    // "Guillermo Rauch - CEO - Vercel | LinkedIn"
    const match = title.match(/^(.+?)\s[-–|]/);
    if (match) {
      const name = match[1].trim();
      if (name.length >= 2 && name.length <= 80 && name !== "LinkedIn") {
        return name;
      }
    }
    return "";
  }

  // ── Company extraction ─────────────────────────────────────────────

  function extractCompany() {
    // Strategy 1: Experience section — find current role (has "Present")
    const expSection = document.getElementById("experience");
    if (expSection) {
      const container = expSection.closest("section") || expSection.parentElement;
      if (container) {
        const items = container.querySelectorAll("li");
        for (const item of items) {
          const spans = item.querySelectorAll("span");
          let hasPresent = false;
          for (const span of spans) {
            if ((span.innerText || "").toLowerCase().includes("present")) {
              hasPresent = true;
              break;
            }
          }
          if (hasPresent) {
            // Company name often in a span with specific classes
            const companyEl = item.querySelector(
              'span[class*="t-14"][class*="t-normal"]:not([class*="t-black--light"])'
            );
            if (companyEl) {
              const text = companyEl.innerText.trim().split("\n")[0].trim();
              if (text && text.length >= 2 && text.length <= 100) return text;
            }
          }
        }
      }
    }

    // Strategy 2: Headline text — "Software Engineer at Google"
    const headlineSelectors = [
      "div.text-body-medium.break-words",
      'div[class*="text-body-medium"]',
      ".pv-text-details__left-panel div.text-body-medium",
    ];

    for (const sel of headlineSelectors) {
      const el = document.querySelector(sel);
      if (el) {
        const headline = el.innerText.trim();
        const atMatch = headline.match(/\bat\s+(.+?)(?:\s*[|·\-,]|$)/i);
        if (atMatch) {
          const company = atMatch[1].trim();
          if (company.length >= 2 && company.length <= 100) return company;
        }
      }
    }

    // Strategy 3: Company link in top card
    const companyLink = document.querySelector(
      'a[href*="/company/"] span, button[class*="company"] span'
    );
    if (companyLink) {
      const text = companyLink.innerText.trim();
      if (text.length >= 2 && text.length <= 100) return text;
    }

    // Strategy 4: Parse from <title> tag
    return extractCompanyFromTitle();
  }

  function extractCompanyFromTitle() {
    const title = document.title || "";
    // "Guillermo Rauch - CEO - Vercel | LinkedIn"
    // Company is typically the last segment before "| LinkedIn"
    const pipeIdx = title.lastIndexOf("|");
    if (pipeIdx === -1) return "";
    const beforePipe = title.substring(0, pipeIdx).trim();
    const segments = beforePipe.split(/\s[-–]\s/);
    if (segments.length >= 3) {
      // Last segment is company
      const company = segments[segments.length - 1].trim();
      if (company.length >= 2 && company.length <= 100) return company;
    }
    return "";
  }

  // ── Extract and send ───────────────────────────────────────────────

  function extractAndSend() {
    if (!window.location.pathname.startsWith("/in/")) return;

    const name = extractName();
    const company = extractCompany();

    if (name) {
      chrome.runtime.sendMessage({
        type: "LINKEDIN_PROFILE_DATA",
        data: { name, company, url: window.location.href },
      });
    }
  }

  // Poll for h1 element (handles LinkedIn's SPA hydration)
  let attempts = 0;
  const maxAttempts = 10;
  const pollInterval = 500; // ms

  function pollAndExtract() {
    attempts++;
    const h1 = document.querySelector("h1");
    if (h1 && h1.innerText.trim()) {
      extractAndSend();
    } else if (attempts < maxAttempts) {
      setTimeout(pollAndExtract, pollInterval);
    }
  }

  pollAndExtract();

  // Re-extract on SPA navigation (LinkedIn uses pushState)
  let lastUrl = window.location.href;
  const observer = new MutationObserver(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      attempts = 0;
      pollAndExtract();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();
