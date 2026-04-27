const liveReadinessPanel = document.querySelector("#liveReadinessPanel");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function readinessLabel(ready) {
  return ready ? "Available" : "Fallback may be used";
}

function renderLiveReadiness(data) {
  if (!liveReadinessPanel) return;
  const rows = [
    ["Live search only", data.live_sourcing_ready ? "SerpAPI available" : "Requires SerpAPI"],
    ["Research", data.live_research_ready ? "Live research available" : "Fallback research may be used"],
    ["Drafting", data.live_drafting_ready ? "LLM drafting available" : "Deterministic drafting fallback may be used"],
  ];
  liveReadinessPanel.innerHTML = `
    <div class="flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
      <div>
        <h2 class="font-semibold text-white">Live readiness</h2>
        <p class="mt-1 text-amber-100/80">${escapeHtml(data.likely_behavior)}</p>
        <p class="mt-1 text-amber-100/70">Live search only requires SerpAPI. If live APIs fail, it fails clearly instead of showing demo results.</p>
      </div>
      <span class="rounded-md border border-amber-200/30 px-2 py-1 text-xs font-semibold text-amber-100">Optional APIs</span>
    </div>
    <div class="mt-3 grid gap-2 md:grid-cols-3">
      ${rows
        .map(
          ([label, value]) => `
            <div class="rounded-md border border-amber-200/20 bg-neutral-950/40 p-3">
              <div class="text-xs uppercase tracking-wide text-amber-100/60">${escapeHtml(label)}</div>
              <div class="mt-1 text-neutral-100">${escapeHtml(value)}</div>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

async function loadLiveReadiness() {
  if (!liveReadinessPanel) return;
  try {
    const response = await fetch("/api/live-readiness");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Unable to check live readiness");
    renderLiveReadiness(data);
  } catch (_error) {
    liveReadinessPanel.innerHTML = `
      <div class="font-semibold text-white">Live readiness</div>
      <p class="mt-1 text-amber-100/80">Live readiness is temporarily unavailable. Seed and cached demo modes still work.</p>
    `;
  }
}

loadLiveReadiness();
