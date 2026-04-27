const root = document.querySelector("main[data-job-id]");
const jobId = root?.dataset.jobId;
const isMissing = root?.dataset.missing === "true";
const progressBar = document.querySelector("#progressBar");
const progressText = document.querySelector("#progressText");
const statusMessage = document.querySelector("#statusMessage");
const resultsList = document.querySelector("#resultsList");
const contextBox = document.querySelector("#contextBox");
const errorBox = document.querySelector("#errorBox");
const jobBadges = document.querySelector("#jobBadges");
const leadSummary = document.querySelector("#leadSummary");
const activityPanel = document.querySelector("#activityPanel");
const resultSourceBanner = document.querySelector("#resultSourceBanner");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setProgress(progress, message) {
  progressBar.style.width = `${progress}%`;
  progressText.textContent = `${progress}%`;
  statusMessage.textContent = message;
}

function renderContext(params) {
  contextBox.innerHTML = Object.entries(params)
    .map(([key, value]) => {
      const label = key.replace("_", " ");
      return `
        <div class="rounded-md border border-neutral-800 bg-neutral-900 p-3">
          <div class="text-xs uppercase tracking-wide text-neutral-500">${escapeHtml(label)}</div>
          <div class="mt-1 text-neutral-100">${escapeHtml(value)}</div>
        </div>
      `;
    })
    .join("");
}

function renderBadges(badges) {
  jobBadges.innerHTML = (badges || [])
    .map((badge) => `<span class="rounded-md border border-amber-300/30 bg-amber-300/10 px-2 py-1 text-xs font-semibold text-amber-200">${escapeHtml(badge)}</span>`)
    .join("");
}

function resultSourceCopy(data) {
  const requestedMode = data.requested_mode || data.params?.mode || "seed_demo";
  const resultMode = data.result_mode || "";
  if (requestedMode === "live_required" && data.status === "failed") {
    return {
      title: "Live search could not complete",
      message: data.error || "Live search only failed before producing usable live results.",
      tone: "failed",
    };
  }
  if (requestedMode === "live_required" && resultMode === "live") {
    return {
      title: "Live search results",
      message: "Live results generated from the live sourcing/research/drafting pipeline.",
      tone: "live",
    };
  }
  if (requestedMode === "live_if_available" && resultMode === "live") {
    return {
      title: "Live results",
      message: "Live results generated from the live sourcing/research/drafting pipeline.",
      tone: "live",
    };
  }
  if (requestedMode === "live_if_available" && resultMode === "seed") {
    return {
      title: "Seed fallback used",
      message: "Live mode was requested, but the app used seed fallback because live sourcing was unavailable.",
      tone: "fallback",
    };
  }
  if (requestedMode === "cached_live") {
    return {
      title: "Cached live demo",
      message: "These results were loaded from the cached live demo fixture.",
      tone: "cached",
    };
  }
  if (requestedMode === "cached_demo") {
    return {
      title: "Cached demo",
      message: "These results were loaded from the cached demo fixture.",
      tone: "cached",
    };
  }
  return {
    title: "Seed demo",
    message: "These results were generated from local seed demo data.",
    tone: "seed",
  };
}

function renderResultSourceBanner(data) {
  if (!resultSourceBanner) return;
  const requestedMode = data.requested_mode || data.params?.mode || "seed_demo";
  if (data.status !== "complete" && !(requestedMode === "live_required" && data.status === "failed")) {
    resultSourceBanner.classList.add("hidden");
    resultSourceBanner.innerHTML = "";
    return;
  }
  const copy = resultSourceCopy(data);
  const classes =
    copy.tone === "live"
      ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-100"
      : copy.tone === "fallback"
        ? "border-amber-300/40 bg-amber-300/10 text-amber-100"
        : copy.tone === "failed"
          ? "border-red-400/50 bg-red-400/10 text-red-100"
          : "border-neutral-700 bg-neutral-900 text-neutral-200";
  const badges = (data.badges || [])
    .map((badge) => `<span class="rounded-md bg-neutral-950/50 px-2 py-1 text-xs">${escapeHtml(badge)}</span>`)
    .join("");
  resultSourceBanner.className = `mb-6 rounded-md border p-4 ${classes}`;
  resultSourceBanner.innerHTML = `
    <div class="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
      <div>
        <h2 class="font-semibold text-white">${escapeHtml(copy.title)}</h2>
        <p class="mt-1 text-sm">${escapeHtml(copy.message)}</p>
      </div>
      ${badges ? `<div class="flex flex-wrap gap-2">${badges}</div>` : ""}
    </div>
  `;
}

function stepIcon(state) {
  if (state === "complete") return "OK";
  if (state === "failed") return "!";
  if (state === "current") return ">";
  return "";
}

function stepClasses(state) {
  if (state === "complete") return "border-emerald-400/40 bg-emerald-400/10 text-emerald-100";
  if (state === "failed") return "border-red-400/50 bg-red-400/10 text-red-100";
  if (state === "current") return "border-amber-300/50 bg-amber-300/10 text-amber-100";
  return "border-neutral-700 bg-neutral-950 text-neutral-400";
}

function renderActivityPanel(steps = [], currentStep = "") {
  if (!activityPanel || !steps.length) return;
  const current = steps.find((step) => step.key === currentStep);
  activityPanel.classList.remove("hidden");
  activityPanel.innerHTML = `
    <div class="flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
      <div>
        <h2 class="text-lg font-semibold text-white">Generation activity</h2>
        <p class="text-sm text-neutral-400">${escapeHtml(current?.detail || "Tracking the lead generation workflow.")}</p>
      </div>
      <span class="text-xs uppercase tracking-wide text-neutral-500">${escapeHtml(current?.label || "Working")}</span>
    </div>
    <ol class="mt-4 grid gap-2 lg:grid-cols-7">
      ${steps
        .map(
          (step) => `
            <li class="rounded-md border px-3 py-3 ${stepClasses(step.state)}">
              <div class="flex items-center gap-2">
                <span class="flex h-5 w-5 items-center justify-center rounded-full border border-current text-xs">${escapeHtml(stepIcon(step.state))}</span>
                <span class="text-sm font-semibold">${escapeHtml(step.label)}</span>
              </div>
              <p class="mt-2 text-xs leading-5 opacity-80">${escapeHtml(step.detail)}</p>
            </li>
          `
        )
        .join("")}
    </ol>
  `;
}

function statusCounts(results) {
  const counts = {
    total: results.length,
    approved: 0,
    rejected: 0,
    do_not_contact: 0,
    ready_to_export: 0,
  };
  results.forEach((lead) => {
    const status = lead.status || "generated";
    if (status === "approved") counts.approved += 1;
    if (status === "rejected") counts.rejected += 1;
    if (status === "do_not_contact") counts.do_not_contact += 1;
  });
  counts.ready_to_export = counts.approved;
  return counts;
}

function renderLeadSummary(results) {
  const counts = statusCounts(results);
  const cards = [
    ["Total leads", counts.total],
    ["Approved", counts.approved],
    ["Rejected", counts.rejected],
    ["Do not contact", counts.do_not_contact],
    ["Ready to export", counts.ready_to_export],
  ];
  leadSummary.innerHTML = cards
    .map(([label, value]) => `
      <div class="rounded-md border border-neutral-800 bg-neutral-900 p-3">
        <div class="text-xs uppercase tracking-wide text-neutral-500">${escapeHtml(label)}</div>
        <div class="mt-1 text-2xl font-semibold text-white">${escapeHtml(value)}</div>
      </div>
    `)
    .join("");
}

function renderSources(sources) {
  return sources
    .map((source) => {
      const safe = escapeHtml(source);
      if (String(source).startsWith("http")) {
        return `<li><a class="text-amber-300 hover:text-amber-200" href="${safe}" target="_blank" rel="noreferrer">${safe}</a></li>`;
      }
      return `<li>${safe}</li>`;
    })
    .join("");
}

function confidenceText(confidence) {
  if (typeof confidence === "number") {
    return `${Math.round(confidence * 100)}%`;
  }
  return escapeHtml(confidence || "medium");
}

function providerText(lead) {
  if (lead.provider_used) {
    return lead.provider_used;
  }
  if (lead.result_mode === "seed" || lead.research_mode === "seed") {
    return "Seed";
  }
  return "Fallback";
}

function renderLead(lead, index) {
  const email = `${lead.email_subject}\n\n${lead.email_body}`;
  const signals = lead.signals.map((signal) => `<li>${escapeHtml(signal)}</li>`).join("");
  const sources = renderSources(lead.source_urls || []);
  const status = lead.status || "generated";
  const badges = ([status.replaceAll("_", " "), ...(lead.badges || [])])
    .filter((badge, badgeIndex, allBadges) => allBadges.indexOf(badge) === badgeIndex)
    .map((badge) => `<span class="rounded-md bg-neutral-800 px-2 py-1 text-xs text-neutral-200">${escapeHtml(badge)}</span>`)
    .join("");
  const subject = lead.email_subject || "";
  const body = lead.email_body || "";
  const actionButtons = lead.id ? `
    <div class="mt-4 flex flex-wrap gap-2">
      <button class="set-status rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-neutral-950" data-status="approved">Approve</button>
      <button class="set-status rounded-md bg-neutral-700 px-3 py-2 text-sm font-semibold text-white" data-status="rejected">Reject</button>
      <button class="set-status rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" data-status="do_not_contact">Do not contact</button>
    </div>
  ` : "";

  return `
    <article class="rounded-lg border border-neutral-800 bg-neutral-900 p-5" data-lead-id="${escapeHtml(lead.id || "")}">
      <div class="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p class="text-sm text-amber-300">#${index + 1} ${escapeHtml(lead.category)}</p>
          <h2 class="mt-1 text-2xl font-semibold text-white">${escapeHtml(lead.business_name)}</h2>
          <div class="mt-2 flex flex-wrap gap-2">${badges}</div>
          <p class="mt-2 text-neutral-300">${escapeHtml(lead.address)}</p>
          <a class="mt-1 inline-block text-sm text-amber-300 hover:text-amber-200" href="${escapeHtml(lead.website)}" target="_blank" rel="noreferrer">${escapeHtml(lead.website)}</a>
          ${actionButtons}
        </div>
        <div class="min-w-28 rounded-md border border-amber-300/30 bg-amber-300/10 p-3 text-center">
          <div class="text-3xl font-semibold text-amber-200">${escapeHtml(lead.fit_score)}</div>
          <div class="text-xs uppercase tracking-wide text-neutral-400">fit score</div>
        </div>
      </div>

      <div class="mt-5 grid gap-4 md:grid-cols-2">
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Reason</h3>
          <p class="mt-2 text-neutral-200">${escapeHtml(lead.reason)}</p>
        </div>
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Suggested contact role</h3>
          <p class="mt-2 text-neutral-200">${escapeHtml(lead.suggested_contact_role)}</p>
        </div>
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Research summary</h3>
          <p class="mt-2 text-neutral-200">${escapeHtml(lead.research_summary)}</p>
        </div>
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Signals</h3>
          <ul class="mt-2 list-inside list-disc text-neutral-200">${signals}</ul>
        </div>
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Lead source</h3>
          <p class="mt-2 text-neutral-200">${escapeHtml(lead.lead_source || lead.source || "Seed data")}</p>
        </div>
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Research mode</h3>
          <p class="mt-2 text-neutral-200">${escapeHtml(lead.research_mode || "seed")}</p>
        </div>
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Provider used</h3>
          <p class="mt-2 text-neutral-200">${escapeHtml(providerText(lead))}</p>
        </div>
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Sources</h3>
          <ul class="mt-2 list-inside list-disc text-neutral-200">${sources}</ul>
        </div>
        <div>
          <h3 class="text-sm font-semibold uppercase tracking-wide text-neutral-400">Confidence</h3>
          <p class="mt-2 text-neutral-200">${confidenceText(lead.confidence)}</p>
        </div>
      </div>

      <div class="mt-5 rounded-md border border-neutral-800 bg-neutral-950 p-4">
        <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <h3 class="font-semibold text-white">${escapeHtml(lead.email_subject)}</h3>
          <button class="copy-email rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300" data-email="${escapeHtml(email)}">Copy email</button>
        </div>
        <pre class="mt-3 whitespace-pre-wrap font-sans text-sm leading-6 text-neutral-300">${escapeHtml(lead.email_body)}</pre>
        ${lead.id ? `
          <div class="mt-4 grid gap-3">
            <input class="email-subject rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-white" value="${escapeHtml(subject)}">
            <textarea class="email-body min-h-36 rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-white">${escapeHtml(body)}</textarea>
            <div class="flex flex-wrap gap-2">
              <button class="save-email rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300">Save edit</button>
            </div>
          </div>
        ` : ""}
      </div>
    </article>
  `;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

function bindLeadActions() {
  document.querySelectorAll("article[data-lead-id]").forEach((card) => {
    const leadId = card.dataset.leadId;
    if (!leadId) return;

    card.querySelectorAll(".set-status").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await postJson(`/api/leads/${leadId}/status`, { status: button.dataset.status });
          button.textContent = "Saved";
        } catch (error) {
          button.textContent = error.message;
        } finally {
          window.setTimeout(() => window.location.reload(), 700);
        }
      });
    });

    const saveButton = card.querySelector(".save-email");
    saveButton?.addEventListener("click", async () => {
      saveButton.disabled = true;
      try {
        await postJson(`/api/leads/${leadId}/email`, {
          email_subject: card.querySelector(".email-subject").value,
          email_body: card.querySelector(".email-body").value,
        });
        saveButton.textContent = "Saved";
      } catch (error) {
        saveButton.textContent = error.message;
      } finally {
        window.setTimeout(() => window.location.reload(), 700);
      }
    });
  });
}

function renderResults(results) {
  renderLeadSummary(results);
  resultsList.innerHTML = results.map(renderLead).join("");
  bindLeadActions();
  document.querySelectorAll(".copy-email").forEach((button) => {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.email);
      button.textContent = "Copied";
      setTimeout(() => {
        button.textContent = "Copy email";
      }, 1400);
    });
  });
}

async function pollStatus() {
  if (isMissing) {
    errorBox.classList.remove("hidden");
    errorBox.textContent = "Job not found. Start a new search from the form.";
    setProgress(100, "Missing job");
    return;
  }

  try {
    const response = await fetch(`/api/status/${jobId}`);
    const data = await response.json();

    setProgress(data.progress, data.message);
    renderContext(data.params);
    renderBadges(data.badges);
    renderResultSourceBanner(data);
    renderActivityPanel(data.steps, data.current_step);
    if (!response.ok || data.status === "failed") {
      throw new Error(data.error || "Unable to load job status");
    }

    if (data.status === "complete") {
      renderResults(data.results);
      return;
    }

    window.setTimeout(pollStatus, 700);
  } catch (error) {
    errorBox.classList.remove("hidden");
    errorBox.textContent = error.message;
  }
}

if (jobId) {
  pollStatus();
}
