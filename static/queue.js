const queueList = document.querySelector("#queueList");
const queueCounts = document.querySelector("#queueCounts");
const queueTitle = document.querySelector("#queueTitle");
const queueDescription = document.querySelector("#queueDescription");
const queueEyebrow = document.querySelector("#queueEyebrow");
const exportButton = document.querySelector("#exportButton");
const toast = document.querySelector("#toast");
const outreachPanel = document.querySelector("#outreachPanel");
const actionResults = document.querySelector("#actionResults");
const resetDemoButton = document.querySelector("#resetDemoButton");

const resetConfirmation =
  "This will delete local demo leads, workflow history, exports, and suppression records. It will not delete seed/cached data or API keys.";

const views = {
  review: {
    label: "Review Queue",
    description: "Generated and in-review leads that still need a decision.",
    empty: "No active leads need review.",
    query: "?queue=review",
  },
  approved: {
    label: "Approved",
    description: "Final review queue for leads ready to export.",
    empty: "No approved leads are ready to export.",
    query: "?status=approved",
  },
  rejected: {
    label: "Rejected",
    description: "Leads removed from active review. Restore one if it deserves another look.",
    empty: "No rejected leads.",
    query: "?status=rejected",
  },
  do_not_contact: {
    label: "Do Not Contact",
    description: "Protected suppression list. These leads are excluded from review and export.",
    empty: "No suppressed leads.",
    query: "?status=do_not_contact",
  },
  exported: {
    label: "Exported",
    description: "Approved leads that have already been exported.",
    empty: "No exported leads yet.",
    query: "?status=exported",
  },
};

let currentView = "review";
let leadNameById = {};
let lastActionResults = null;
let queueLoadSequence = 0;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function labelForStatus(status) {
  return String(status || "generated").replaceAll("_", " ");
}

function titleCaseStatus(status) {
  return labelForStatus(status)
    .split(" ")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function showToast(message, type = "success") {
  toast.textContent = message;
  toast.className =
    type === "error"
      ? "mt-4 rounded-md border border-red-500/40 bg-red-950/40 px-4 py-3 text-sm text-red-100"
      : "mt-4 rounded-md border border-emerald-500/40 bg-emerald-950/40 px-4 py-3 text-sm text-emerald-100";
  window.setTimeout(() => toast.classList.add("hidden"), 2200);
}

function renderQueueMessage(message) {
  queueList.innerHTML = `<div class="rounded-md border border-neutral-800 bg-neutral-900 p-5 text-neutral-300">${escapeHtml(message)}</div>`;
}

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const contentType = response.headers.get("Content-Type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : { error: `Server returned a non-JSON response for ${url}. Restart Flask and refresh the page.` };
  if (!response.ok) {
    const error = new Error(data.error || data.message || `Request failed with status ${response.status}`);
    error.data = data;
    throw error;
  }
  return data;
}

function renderCounts(counts = {}) {
  const cards = [
    ["Review", counts.review || 0],
    ["Approved", counts.approved || 0],
    ["Rejected", counts.rejected || 0],
    ["Do Not Contact", counts.do_not_contact || 0],
    ["Export Ready", counts.export_ready || 0],
  ];
  queueCounts.innerHTML = cards
    .map(
      ([label, value]) => `
        <div class="rounded-md border border-neutral-800 bg-neutral-900 p-3">
          <div class="text-xs uppercase text-neutral-500">${escapeHtml(label)}</div>
          <div class="mt-1 text-2xl font-semibold text-white">${escapeHtml(value)}</div>
        </div>
      `
    )
    .join("");
  exportButton.classList.toggle("hidden", currentView !== "approved");
  exportButton.classList.toggle("pointer-events-none", (counts.approved || 0) === 0);
  exportButton.classList.toggle("opacity-50", (counts.approved || 0) === 0);
}

function renderOutreachPanel(outreach, view = currentView) {
  if (view !== "approved") {
    outreachPanel.classList.add("hidden");
    outreachPanel.innerHTML = "";
    return;
  }
  const counts = outreach?.counts || {};
  const warnings = outreach?.warnings || [];
  const ready = counts.ready_to_push || 0;
  outreachPanel.classList.remove("hidden");
  outreachPanel.innerHTML = `
    <div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
      <div>
        <h3 class="text-lg font-semibold text-white">Outreach readiness</h3>
        <p class="mt-1 max-w-3xl text-sm text-neutral-400">Work left to right: find contacts, verify emails, then push ready leads and their draft copy into Instantly. Instantly handles the actual campaign sending.</p>
        <div class="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
          ${[
            ["Emails found", counts.emails_found || 0],
            ["Verified valid", counts.verified_valid || 0],
            ["Missing emails", counts.missing_emails || 0],
            ["Invalid emails", counts.invalid_emails || 0],
            ["Ready to push", ready],
            ["Already pushed", counts.already_pushed || 0],
          ]
            .map(
              ([label, value]) => `
                <div class="rounded-md border border-neutral-800 bg-neutral-950 p-3">
                  <div class="text-xs uppercase text-neutral-500">${escapeHtml(label)}</div>
                  <div class="mt-1 text-xl font-semibold">${escapeHtml(value)}</div>
                </div>
              `
            )
            .join("")}
        </div>
        ${
          warnings.length
            ? `<div class="mt-3 rounded-md border border-amber-300/30 bg-amber-300/10 p-3 text-sm text-amber-100">${warnings.map(escapeHtml).join("<br>")}</div>`
            : ""
        }
      </div>
      <div class="flex flex-wrap gap-2 xl:max-w-xs">
        <button class="bulk-action rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300" data-action="discover">1. Discover missing contacts</button>
        <button class="bulk-action rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300" data-action="verify">2. Verify found emails</button>
        <button class="bulk-action rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-neutral-950 disabled:cursor-not-allowed disabled:opacity-50" data-action="push" ${ready ? "" : "disabled"}>3. Push ready to Instantly</button>
        <a class="rounded-md bg-amber-300 px-3 py-2 text-sm font-semibold text-neutral-950 hover:bg-amber-200" href="/export/approved">Export CSV</a>
      </div>
    </div>
  `;
  outreachPanel.querySelectorAll(".bulk-action").forEach((button) => {
    button.addEventListener("click", async () => runBulkAction(button.dataset.action));
  });
}

function stageForLead(lead) {
  const verification = lead.email_verification_status || "unverified";
  const contact = lead.contact_status || "not_started";
  const instantly = lead.instantly_status || "not_ready";

  if (instantly === "pushed") return "pushed";
  if (instantly === "failed" || verification === "invalid" || verification === "failed" || contact === "failed") return "blocked";
  if (!lead.recipient_email) return "needs_contact";
  if (verification === "valid") return "ready";
  return "needs_verification";
}

function nextStepForLead(lead) {
  const verification = lead.email_verification_status || "unverified";
  const contact = lead.contact_status || "not_started";
  const instantly = lead.instantly_status || "not_ready";

  if (instantly === "pushed") return "Already pushed to Instantly. Sending now depends on the Instantly campaign/list setup.";
  if (instantly === "failed") return "Instantly rejected this lead. Check the error below, then retry after fixing setup or lead data.";
  if (verification === "invalid" || verification === "failed") return "Blocked from Instantly because the email is invalid or verification failed.";
  if (!lead.recipient_email) return contact === "not_found" ? "No email was found. Enter one manually or try discovery again after checking the website." : "Find a recipient email before verification or Instantly push.";
  if (verification === "valid") return "Ready to push. This lead has an email Hunter accepted as valid.";
  if (lead.contact_source === "manual") return "Manual email saved. Verify it if possible, or push with manual confirmation if you trust it.";
  return "Email found. Verify it before pushing to Instantly.";
}

function renderWorkflowStatus(lead) {
  const items = [
    ["Contact", lead.contact_status || "not_started"],
    ["Source", lead.contact_source || "none"],
    ["Verification", lead.email_verification_status || "unverified"],
    ["Instantly", lead.instantly_status || "not_ready"],
  ];
  return items
    .map(
      ([label, value]) => `
        <span class="inline-flex items-center gap-1 rounded-md bg-neutral-800 px-2 py-1 text-xs text-neutral-300">
          <span class="text-neutral-500">${escapeHtml(label)}:</span>
          <span>${escapeHtml(titleCaseStatus(value))}</span>
        </span>
      `
    )
    .join("");
}

function renderActionResults(view = currentView) {
  if (!actionResults || view !== "approved" || !lastActionResults) {
    actionResults?.classList.add("hidden");
    if (actionResults) actionResults.innerHTML = "";
    return;
  }
  const rows = lastActionResults.items || [];
  actionResults.classList.remove("hidden");
  actionResults.innerHTML = `
    <div class="flex flex-col gap-3 rounded-md border border-sky-400/30 bg-sky-400/10 p-4 text-sm text-sky-100">
      <div class="flex items-start justify-between gap-4">
        <div>
          <h3 class="font-semibold text-white">${escapeHtml(lastActionResults.title)}</h3>
          <p class="mt-1 text-sky-100/80">These are the leads affected by the last action.</p>
        </div>
        <button class="clear-action-results rounded-md border border-sky-200/30 px-3 py-1 text-xs text-sky-50 hover:bg-sky-200/10">Clear</button>
      </div>
      ${
        rows.length
          ? `<div class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              ${rows
                .map(
                  (item) => `
                    <div class="rounded-md border border-sky-200/20 bg-neutral-950/60 p-3">
                      <div class="font-semibold text-white">${escapeHtml(item.name || leadNameById[item.id] || `Lead ${item.id || ""}`)}</div>
                      <div class="mt-1 text-xs uppercase tracking-wide text-sky-200">${escapeHtml(titleCaseStatus(item.status || "updated"))}</div>
                      <div class="mt-1 text-neutral-200">${escapeHtml(item.message || "")}</div>
                    </div>
                  `
                )
                .join("")}
            </div>`
          : `<p>No leads were changed.</p>`
      }
    </div>
  `;
  actionResults.querySelector(".clear-action-results")?.addEventListener("click", () => {
    lastActionResults = null;
    renderActionResults();
  });
}

function setActionResults(title, items) {
  lastActionResults = { title, items: items || [] };
}

function normalizeBulkResults(results = []) {
  return results.map((item) => ({
    id: item.id || item.lead_id,
    name: item.business_name || item.name,
    status: item.status || (item.ok ? "updated" : "blocked"),
    message: item.message || item.reason || item.error || "",
  }));
}

const approvedSections = [
  {
    key: "needs_contact",
    label: "Needs contact",
    description: "Approved leads without a recipient email yet.",
  },
  {
    key: "needs_verification",
    label: "Needs verification",
    description: "Emails exist, but they still need verification or manual confirmation.",
  },
  {
    key: "ready",
    label: "Ready to push",
    description: "Verified leads that can be sent into Instantly.",
  },
  {
    key: "pushed",
    label: "Pushed to Instantly",
    description: "Already handed to Instantly. Campaign sending happens there.",
  },
  {
    key: "blocked",
    label: "Blocked",
    description: "Invalid emails or failed pushes that need attention before retrying.",
  },
];

function renderApprovedPipeline(leads, view = "approved") {
  return approvedSections
    .map((section) => {
      const items = leads.filter((lead) => stageForLead(lead) === section.key);
      return `
        <section class="border-t border-neutral-800 pt-5">
          <div class="mb-3 flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
            <div>
              <h3 class="text-xl font-semibold text-white">${escapeHtml(section.label)}</h3>
              <p class="text-sm text-neutral-400">${escapeHtml(section.description)}</p>
            </div>
            <span class="text-sm font-semibold text-amber-200">${escapeHtml(items.length)} lead${items.length === 1 ? "" : "s"}</span>
          </div>
          <div class="grid gap-4">
            ${
              items.length
                ? items.map((lead) => renderLead(lead, view)).join("")
                : `<div class="rounded-md border border-neutral-800 bg-neutral-900/50 p-4 text-sm text-neutral-400">Nothing here right now.</div>`
            }
          </div>
        </section>
      `;
    })
    .join("");
}

function actionButtonsFor(lead, view = currentView) {
  if (view === "rejected" || view === "do_not_contact") {
    return `<button class="lead-action rounded-md border border-amber-300 px-3 py-2 text-sm font-semibold text-amber-100 hover:bg-amber-300/10" data-action="restore">Restore to review</button>`;
  }
  if (view === "exported") {
    return "";
  }
  if (view === "approved") {
    return `
      <button class="lead-action rounded-md bg-neutral-700 px-3 py-2 text-sm font-semibold text-white" data-action="reject">Reject</button>
      <button class="lead-action rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" data-action="do-not-contact">Do not contact</button>
    `;
  }
  return `
    <button class="lead-action rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-neutral-950" data-action="approve">Approve</button>
    <button class="lead-action rounded-md bg-neutral-700 px-3 py-2 text-sm font-semibold text-white" data-action="reject">Reject</button>
    <button class="lead-action rounded-md bg-red-500 px-3 py-2 text-sm font-semibold text-white" data-action="do-not-contact">Do not contact</button>
  `;
}

function renderLead(lead, view = currentView) {
  const effectiveEmail = `${lead.email_subject || ""}\n\n${lead.email_body || ""}`;
  const contactPanel =
    view === "approved"
      ? `
        <div class="mt-4 rounded-md border border-neutral-800 bg-neutral-950 p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="font-semibold text-white">Recipient</h4>
            <div class="flex flex-wrap gap-2 text-xs text-neutral-300">
              ${renderWorkflowStatus(lead)}
            </div>
          </div>
          <div class="mt-3 rounded-md border border-neutral-800 bg-neutral-900 p-3 text-sm text-neutral-200">
            <span class="font-semibold text-amber-200">Next step:</span>
            ${escapeHtml(nextStepForLead(lead))}
          </div>
          <div class="mt-3 grid gap-3 md:grid-cols-3">
            <input class="recipient-email rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-white" placeholder="recipient@email.com" value="${escapeHtml(lead.recipient_email || "")}">
            <input class="recipient-name rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-white" placeholder="Recipient name" value="${escapeHtml(lead.recipient_name || "")}">
            <input class="recipient-role rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-white" placeholder="Recipient role" value="${escapeHtml(lead.recipient_role || lead.suggested_contact_role || "")}">
          </div>
          ${
            lead.email_verification_reason || lead.instantly_error
              ? `<p class="mt-3 text-sm text-neutral-400">${escapeHtml(lead.email_verification_reason || lead.instantly_error)}</p>`
              : ""
          }
          <div class="mt-3 flex flex-wrap gap-2">
            <button class="contact-action rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300" data-action="discover-contact">Discover contact</button>
            <button class="save-contact rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300">Save contact</button>
            <button class="contact-action rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300" data-action="verify-email">Verify email</button>
            <button class="push-single rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-neutral-950">Push to Instantly</button>
          </div>
        </div>
      `
      : "";
  return `
    <article class="rounded-lg border border-neutral-800 bg-neutral-900 p-5" data-lead-id="${escapeHtml(lead.id)}">
      <div class="grid gap-5 xl:grid-cols-[1fr_420px]">
        <div>
          <div class="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <p class="text-sm text-amber-300">${escapeHtml(lead.category || "Lead")}</p>
              <h3 class="mt-1 text-2xl font-semibold">${escapeHtml(lead.business_name)}</h3>
              <div class="mt-2 flex flex-wrap gap-2">
                <span class="rounded-md bg-neutral-800 px-2 py-1 text-xs text-neutral-200">${escapeHtml(labelForStatus(lead.status))}</span>
                <span class="rounded-md bg-neutral-800 px-2 py-1 text-xs text-neutral-200">${escapeHtml(lead.provider_used || lead.result_mode || "source")}</span>
                <span class="rounded-md bg-neutral-800 px-2 py-1 text-xs text-neutral-200">${escapeHtml(lead.confidence || "confidence")}</span>
              </div>
              <p class="mt-3 text-neutral-300">${escapeHtml(lead.website || lead.address || "")}</p>
              <p class="mt-2 text-sm text-neutral-400">Contact role: ${escapeHtml(lead.suggested_contact_role || "")}</p>
            </div>
            <div class="rounded-md border border-amber-300/30 bg-amber-300/10 px-4 py-3 text-center text-amber-200">
              <div class="text-3xl font-semibold">${escapeHtml(lead.fit_score || "")}</div>
              <div class="text-xs uppercase text-neutral-400">fit</div>
            </div>
          </div>
          <p class="mt-4 text-neutral-200">${escapeHtml(lead.reason || "")}</p>
          <div class="mt-4 flex flex-wrap gap-2">${actionButtonsFor(lead, view)}</div>
          ${contactPanel}
        </div>

        <div class="rounded-md border border-neutral-800 bg-neutral-950 p-4">
          <div class="flex items-center justify-between gap-3">
            <h4 class="font-semibold text-white">Email draft</h4>
            <button class="copy-email rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300" data-email="${escapeHtml(effectiveEmail)}">Copy</button>
          </div>
          <div class="mt-4 grid gap-3">
            <input class="email-subject rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-white" value="${escapeHtml(lead.email_subject || "")}">
            <textarea class="email-body min-h-36 rounded-md border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-white">${escapeHtml(lead.email_body || "")}</textarea>
            <button class="save-email rounded-md border border-neutral-700 px-3 py-2 text-sm text-neutral-100 hover:border-amber-300">Save edit</button>
          </div>
        </div>
      </div>
    </article>
  `;
}

function setActiveButton() {
  document.querySelectorAll(".queue-filter").forEach((button) => {
    const isActive = (button.dataset.queue || button.dataset.status) === currentView;
    button.classList.toggle("border-amber-300", isActive);
    button.classList.toggle("bg-amber-300/10", isActive);
    button.classList.toggle("text-amber-100", isActive);
  });
}

function endpointForAction(leadId, action) {
  return `/api/leads/${leadId}/${action}`;
}

async function runBulkAction(action) {
  try {
    if (action === "discover") {
      const data = await postJson("/api/leads/discover-approved");
      setActionResults("Contact discovery results", normalizeBulkResults(data.results || []));
      showToast("Contact discovery finished. Results are shown below.");
    }
    if (action === "verify") {
      const data = await postJson("/api/leads/verify-approved");
      setActionResults("Email verification results", normalizeBulkResults(data.results || []));
      showToast("Email verification finished. Results are shown below.");
    }
    if (action === "push") {
      const confirmUnverified = window.confirm("Push valid emails and manually entered unverified emails to Instantly?");
      const data = await postJson("/api/instantly/push-approved", { confirm_unverified: confirmUnverified });
      const pushed = (data.pushed || []).map((id) => ({ id, status: "pushed", message: "Pushed to Instantly." }));
      const blocked = (data.blocked || []).map((item) => ({
        id: item.id || item.lead_id,
        status: "blocked",
        message: item.reason || item.error || "Not pushed.",
      }));
      setActionResults("Instantly push results", [...pushed, ...blocked]);
      showToast(`${pushed.length} lead${pushed.length === 1 ? "" : "s"} pushed to Instantly.`);
    }
    await loadQueue("approved");
  } catch (error) {
    if (action === "push" && error.data?.blocked) {
      setActionResults(
        "Instantly push blocked",
        (error.data.blocked || []).map((item) => ({
          id: item.id || item.lead_id,
          name: item.business_name,
          status: "blocked",
          message: item.reason || item.error || error.message,
        }))
      );
      await loadQueue("approved");
    }
    showToast(error.message, "error");
  }
}

async function resetDemoData() {
  if (!window.confirm(resetConfirmation)) {
    return;
  }
  const original = resetDemoButton.textContent;
  resetDemoButton.disabled = true;
  resetDemoButton.textContent = "Resetting...";
  try {
    const data = await postJson("/api/demo/reset");
    lastActionResults = null;
    await loadQueue("review");
    showToast(data.message || "Demo data reset.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    resetDemoButton.disabled = false;
    resetDemoButton.textContent = original;
  }
}

function bindActions() {
  document.querySelectorAll("article[data-lead-id]").forEach((card) => {
    const leadId = card.dataset.leadId;

    card.querySelectorAll(".lead-action").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        const original = button.textContent;
        button.textContent = "Saving...";
        try {
          await postJson(endpointForAction(leadId, button.dataset.action));
          showToast("Lead moved.");
          lastActionResults = null;
          await loadQueue(currentView);
        } catch (error) {
          button.disabled = false;
          button.textContent = original;
          showToast(error.message, "error");
        }
      });
    });

    card.querySelector(".save-email")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      const original = button.textContent;
      button.textContent = "Saving...";
      try {
        await postJson(`/api/leads/${leadId}/edit-email`, {
          email_subject: card.querySelector(".email-subject").value,
          email_body: card.querySelector(".email-body").value,
        });
        showToast("Email saved.");
        await loadQueue(currentView);
      } catch (error) {
        button.disabled = false;
        button.textContent = original;
        showToast(error.message, "error");
      }
    });

    card.querySelectorAll(".contact-action").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        const original = button.textContent;
        button.textContent = "Working...";
        try {
          const data = await postJson(`/api/leads/${leadId}/${button.dataset.action}`);
          const updatedLead = data.lead || {};
          setActionResults(button.dataset.action === "verify-email" ? "Email verification result" : "Contact discovery result", [
            {
              id: leadId,
              name: updatedLead.business_name,
              status: updatedLead.email_verification_status || updatedLead.contact_status || "updated",
              message: data.message || updatedLead.email_verification_reason || "Lead updated.",
            },
          ]);
          showToast("Lead updated. Result is shown below.");
          await loadQueue(currentView);
        } catch (error) {
          button.disabled = false;
          button.textContent = original;
          showToast(error.message, "error");
        }
      });
    });

    card.querySelector(".save-contact")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      const original = button.textContent;
      button.textContent = "Saving...";
      try {
        const data = await postJson(`/api/leads/${leadId}/save-contact`, {
          recipient_email: card.querySelector(".recipient-email").value,
          recipient_name: card.querySelector(".recipient-name").value,
          recipient_role: card.querySelector(".recipient-role").value,
        });
        setActionResults("Manual contact saved", [
          {
            id: leadId,
            name: data.lead?.business_name,
            status: "manual",
            message: "Saved manual recipient details. Verification is now unverified until checked.",
          },
        ]);
        showToast("Contact saved.");
        await loadQueue(currentView);
      } catch (error) {
        button.disabled = false;
        button.textContent = original;
        showToast(error.message, "error");
      }
    });

    card.querySelector(".push-single")?.addEventListener("click", async () => {
      try {
        const confirmUnverified = window.confirm("Push this lead if the email is manually entered but unverified?");
        const data = await postJson("/api/instantly/push-approved", { lead_ids: [leadId], confirm_unverified: confirmUnverified });
        const pushed = (data.pushed || []).map((id) => ({ id, status: "pushed", message: "Pushed to Instantly." }));
        const blocked = (data.blocked || []).map((item) => ({
          id: item.id || item.lead_id,
          status: "blocked",
          message: item.reason || item.error || "Not pushed.",
        }));
        setActionResults("Single lead push result", [...pushed, ...blocked]);
        showToast(pushed.length ? "Lead pushed to Instantly." : "Lead was not pushed. See result below.", pushed.length ? "success" : "error");
        await loadQueue(currentView);
      } catch (error) {
        if (error.data?.blocked) {
          setActionResults(
            "Single lead push blocked",
            (error.data.blocked || []).map((item) => ({
              id: item.id || item.lead_id,
              name: item.business_name,
              status: "blocked",
              message: item.reason || item.error || error.message,
            }))
          );
          await loadQueue(currentView);
        }
        showToast(error.message, "error");
      }
    });

    card.querySelector(".copy-email")?.addEventListener("click", async (event) => {
      await navigator.clipboard.writeText(event.currentTarget.dataset.email);
      event.currentTarget.textContent = "Copied";
      window.setTimeout(() => {
        event.currentTarget.textContent = "Copy";
      }, 1200);
    });
  });
}

async function loadQueue(view = "review") {
  const requestedView = views[view] ? view : "review";
  const requestSequence = ++queueLoadSequence;
  currentView = requestedView;
  const config = views[requestedView];
  queueEyebrow.textContent = "Active workflow";
  queueTitle.textContent = config.label;
  queueDescription.textContent = config.description;
  setActiveButton();
  renderQueueMessage(`Loading ${config.label.toLowerCase()}...`);

  try {
    const response = await fetch(`/api/leads${config.query}`);
    const contentType = response.headers.get("Content-Type") || "";
    const data = contentType.includes("application/json")
      ? await response.json()
      : { error: `Server returned a non-JSON response for ${config.label}. Restart Flask and refresh the page.` };
    if (requestSequence !== queueLoadSequence) {
      return;
    }
    if (!response.ok) {
      throw new Error(data.error || "Unable to load leads");
    }
    currentView = requestedView;
    leadNameById = Object.fromEntries((data.leads || []).map((lead) => [String(lead.id), lead.business_name]));
    renderCounts(data.counts || {});
    let outreach = data.outreach;
    if (requestedView === "approved" && !outreach) {
      const outreachResponse = await fetch("/api/outreach/readiness");
      outreach = await outreachResponse.json();
      if (requestSequence !== queueLoadSequence) {
        return;
      }
    }
    renderOutreachPanel(outreach, requestedView);
    renderActionResults(requestedView);
    queueList.innerHTML = data.leads.length
      ? requestedView === "approved"
        ? renderApprovedPipeline(data.leads, requestedView)
        : data.leads.map((lead) => renderLead(lead, requestedView)).join("")
      : `<div class="rounded-md border border-neutral-800 bg-neutral-900 p-5 text-neutral-300">${escapeHtml(config.empty)}</div>`;
    bindActions();
  } catch (error) {
    if (requestSequence !== queueLoadSequence) {
      return;
    }
    renderQueueMessage(`Unable to load ${config.label.toLowerCase()}.`);
    showToast(error.message, "error");
  }
}

document.querySelectorAll(".queue-filter").forEach((button) => {
  button.addEventListener("click", () => loadQueue(button.dataset.queue || button.dataset.status));
});

resetDemoButton?.addEventListener("click", resetDemoData);

loadQueue("review");
