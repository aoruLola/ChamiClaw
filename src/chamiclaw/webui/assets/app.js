const endpoints = {
  health: "/health",
  state: "/ops/state",
  preflight: "/ops/preflight",
  batch: "/ops/weather/batch/last",
  notifications: "/ops/notifications/health",
};

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return response.json();
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) {
    node.textContent = value;
  }
}

function setBadge(id, label, tone) {
  const node = document.getElementById(id);
  if (!node) {
    return;
  }
  node.textContent = label;
  node.className = `badge ${tone}`;
}

function renderDefinitionList(id, items) {
  const node = document.getElementById(id);
  if (!node) {
    return;
  }
  node.innerHTML = items.map(([term, value]) => `<dt>${term}</dt><dd>${value ?? "n/a"}</dd>`).join("");
}

function renderPreflight(checks) {
  const node = document.getElementById("preflight-list");
  if (!node) {
    return;
  }
  node.innerHTML = checks.map((check) => {
    const tone = check.ok ? "status-ok" : "status-fail";
    return `<li class="${tone}"><strong>${check.name}</strong><div>${check.message}</div></li>`;
  }).join("");
}

function rejectionSummary(rejections) {
  const entries = Object.entries(rejections || {});
  if (!entries.length) {
    return "none";
  }
  return entries.map(([reason, count]) => `${reason}: ${count}`).join(", ");
}

function healthTone(ok, fallbackWarn = false) {
  if (ok === true) {
    return "good";
  }
  if (fallbackWarn) {
    return "warn";
  }
  return "bad";
}

async function refreshDashboard() {
  const refreshButton = document.getElementById("refresh-button");
  refreshButton.disabled = true;
  try {
    const [health, state, preflight, batch, notifications] = await Promise.all([
      fetchJson(endpoints.health),
      fetchJson(endpoints.state),
      fetchJson(endpoints.preflight),
      fetchJson(endpoints.batch),
      fetchJson(endpoints.notifications),
    ]);

    setText("service-status", health.status || "unknown");
    setText("service-detail", `version ${health.version} • weather mode ${health.weather_enabled ? "on" : "off"}`);
    setText("weather-market-count", String(health.market_pool?.weather_markets_total ?? 0));
    setText("weather-market-detail", `${health.market_pool?.gamma_fetched_total ?? 0} fetched, ${health.market_pool?.active_markets_total ?? 0} active`);
    setText("info-signal-count", String(health.weather_info_refresh?.info_signals ?? 0));
    setText("info-signal-detail", `${health.weather_info_refresh?.weather_markets ?? 0} weather markets reached info refresh`);
    setText("batch-executed-count", String(batch.executed ?? 0));
    setText("batch-detail", `${batch.reviewed ?? 0} reviewed from ${batch.candidates ?? 0} candidates`);

    setBadge("health-badge", health.status === "ok" ? "Healthy" : "Degraded", healthTone(health.status === "ok"));
    setBadge(
      "market-badge",
      (health.market_pool?.weather_markets_total ?? 0) > 0 ? "Ready" : "Empty",
      (health.market_pool?.weather_markets_total ?? 0) > 0 ? "good" : "warn",
    );
    setBadge("preflight-badge", preflight.ok ? "Pass" : "Fail", healthTone(preflight.ok));
    setBadge(
      "notification-badge",
      (notifications.webhook_failures_total ?? 0) === 0 ? "Clean" : "Watch",
      (notifications.webhook_failures_total ?? 0) === 0 ? "good" : "warn",
    );

    renderDefinitionList("system-health-list", [
      ["Repository", health.repository_backend],
      ["Execution", health.execution_dry_run ? "dry-run" : "live"],
      ["Price Stream", health.price_stream_running ? "running" : "stopped"],
      ["Webhook", notifications.webhook_enabled ? "enabled" : "disabled"],
      ["Last Event", notifications.webhook_last_event_type || "n/a"],
    ]);

    renderDefinitionList("market-pool-list", [
      ["Fetched", health.market_pool?.gamma_fetched_total],
      ["Active", health.market_pool?.active_markets_total],
      ["Weather Eligible", health.market_pool?.weather_markets_total],
      ["Selected", health.market_pool?.selected_markets_total],
      ["Rejected", rejectionSummary(health.market_pool?.weather_markets_rejected_by_reason)],
    ]);

    renderPreflight(preflight.checks || []);

    renderDefinitionList("runtime-signal-list", [
      ["Webhook Failures", notifications.webhook_failures_total],
      ["Last Success", notifications.webhook_last_success_ts || "n/a"],
      ["Last Failure", notifications.webhook_last_failure_ts || "n/a"],
      ["Info Signals", state.info_signals],
      ["Price Signals", state.price_signals],
    ]);

    renderDefinitionList("weather-batch-list", [
      ["Candidates", batch.candidates],
      ["Reviewed", batch.reviewed],
      ["Executed", batch.executed],
      ["Rejected", batch.rejected],
    ]);

    renderDefinitionList("ops-state-list", [
      ["Markets", state.markets],
      ["Mode States", state.mode_states],
      ["Phase", state.phase_gate?.phase || "n/a"],
      ["Batch Weather Markets", state.weather_info_refresh?.weather_markets ?? 0],
      ["Daily Halt", state.risk_controls?.daily_halt ? "true" : "false"],
    ]);

    setText("last-updated", `Last updated: ${new Date().toLocaleString()}`);
  } catch (error) {
    setText("service-status", "error");
    setText("service-detail", error instanceof Error ? error.message : String(error));
    setBadge("health-badge", "Error", "bad");
    setText("last-updated", `Last updated: ${new Date().toLocaleString()} (refresh failed)`);
  } finally {
    refreshButton.disabled = false;
  }
}

document.getElementById("refresh-button")?.addEventListener("click", refreshDashboard);
refreshDashboard();
window.setInterval(refreshDashboard, 20000);
