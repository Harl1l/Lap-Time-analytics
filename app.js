(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  $$(".flash-close").forEach(button => button.addEventListener("click", () => button.closest(".flash")?.remove()));
  $$("form[data-confirm]").forEach(form => form.addEventListener("submit", event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }));

  const fileInput = $("#file-input");
  if (fileInput) {
    fileInput.addEventListener("change", () => {
      const label = fileInput.closest(".file-drop");
      const name = fileInput.files?.[0]?.name;
      if (name && label) {
        const title = $("b", label);
        if (title) title.textContent = name;
      }
    });
  }
  const createSession = $("#create-session");
  const newFields = $("#new-session-fields");
  const existingField = $("#existing-session-field");
  if (createSession && newFields && existingField) {
    const toggle = () => {
      newFields.hidden = !createSession.checked;
      existingField.hidden = createSession.checked;
    };
    createSession.addEventListener("change", toggle);
    toggle();
  }

  const trendCanvas = $("#overviewTrend");
  if (trendCanvas) {
    const source = $("#trend-data");
    const rows = source ? JSON.parse(source.textContent || "[]") : [];
    if (rows.length) {
      new Chart(trendCanvas, {
        type: "line",
        data: {
          labels: rows.map((row, i) => `RUN ${String(i + 1).padStart(2, "0")}`),
          datasets: [{
            label: "Session best (seconds)",
            data: rows.map(row => row.ms / 1000),
            borderColor: "#ff5b35",
            backgroundColor: "rgba(255,91,53,.12)",
            fill: true, tension: .32, pointRadius: 4, pointHoverRadius: 6,
            pointBackgroundColor: "#ff5b35", borderWidth: 2
          }]
        },
        options: chartOptions("Lap time (seconds)", true)
      });
    }
  }

  if ($("#lapProgression") && window.APEX_ANALYTICS_URL) {
    initAnalytics();
  }

  function chartOptions(yTitle, reverse = false) {
    return {
      responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: "#171c23", titleColor: "#f3f5f7", bodyColor: "#aeb7c2", borderColor: "#303944", borderWidth: 1, padding: 10 }
      },
      scales: {
        x: { grid: { color: "rgba(151,164,178,.08)" }, ticks: { color: "#7f8a98", maxTicksLimit: 8 }, border: { color: "#303944" } },
        y: { reverse, title: { display: true, text: yTitle, color: "#7f8a98", font: { size: 10 } }, grid: { color: "rgba(151,164,178,.10)" }, ticks: { color: "#7f8a98" }, border: { color: "#303944" } }
      }
    };
  }

  async function initAnalytics() {
    const progressionOptions = chartOptions("Lap time (seconds)");
    progressionOptions.plugins.legend = {
      display: true,
      position: "top",
      labels: { color: "#aeb7c2", usePointStyle: true, pointStyle: "line", boxWidth: 24, padding: 16 }
    };
    const progression = new Chart($("#lapProgression"), {
      type: "line",
      data: { labels: [], datasets: [] },
      options: progressionOptions
    });
    const sector = new Chart($("#sectorChart"), {
      type: "bar",
      data: { labels: ["Sector 1", "Sector 2", "Sector 3"], datasets: [{
        label: "Best sector (s)", data: [null, null, null], backgroundColor: ["#ff5b35", "#ff875f", "#ffb08e"], borderRadius: 4, maxBarThickness: 40
      }] },
      options: { ...chartOptions("Sector time (seconds)"), indexAxis: "y" }
    });

    const refresh = async () => {
      const params = new URLSearchParams();
      const driver = $("#filter-driver")?.value || "";
      const track = $("#filter-track")?.value || "";
      if (driver) params.set("driver", driver);
      if (track) params.set("track", track);
      try {
        const response = await fetch(`${window.APEX_ANALYTICS_URL}?${params.toString()}`);
        if (!response.ok) throw new Error("Could not load analytics.");
        const data = await response.json();
        updateAnalytics(data, progression, sector);
      } catch (error) {
        const count = $("#analysis-count");
        if (count) count.textContent = "Unable to load analytics. Refresh to try again.";
        console.error(error);
      }
    };
    $("#filter-driver")?.addEventListener("change", refresh);
    $("#filter-track")?.addEventListener("change", refresh);
    await refresh();
  }

  function seconds(ms) {
    return ms == null ? null : (ms / 1000);
  }
  function formatDelta(ms) {
    if (ms == null) return "—";
    return `${(ms / 1000).toFixed(3)} s`;
  }
  function safe(text) {
    return String(text ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }[char]));
  }

  function updateAnalytics(data, progression, sector) {
    $("#metric-best").textContent = data.best_time || "—";
    $("#metric-avg").textContent = data.avg_time || "—";
    $("#metric-theoretical").textContent = data.theoretical_time || "—";
    $("#metric-gap").textContent = data.best_ms != null && data.avg_ms != null ? formatDelta(data.avg_ms - data.best_ms) : "—";
    $("#analysis-count").textContent = `${data.valid_laps} valid lap${data.valid_laps === 1 ? "" : "s"} in current view`;
    $("#lap-table-count").textContent = `${data.laps.length} lap${data.laps.length === 1 ? "" : "s"}`;

    const valid = data.laps.filter(lap => lap.valid);
    // Keep one dataset per driver so an all-driver view can be read at a glance.
    // The API returns laps chronologically; each driver's line follows their valid laps
    // in that order, with the x-axis showing their sequential lap within the filtered view.
    const driverColours = [
      "#ff5b35", "#36c5f0", "#a78bfa", "#facc15", "#34d399",
      "#fb7185", "#60a5fa", "#f97316", "#e879f9", "#a3e635",
      "#2dd4bf", "#f472b6", "#c084fc", "#fbbf24", "#818cf8"
    ];
    const lapsByDriver = new Map();
    valid.forEach(lap => {
      if (!lapsByDriver.has(lap.driver)) lapsByDriver.set(lap.driver, []);
      lapsByDriver.get(lap.driver).push(seconds(lap.lap_ms));
    });
    const longestRun = Math.max(0, ...[...lapsByDriver.values()].map(laps => laps.length));
    progression.data.labels = Array.from({ length: longestRun }, (_, index) => `Lap ${index + 1}`);
    progression.data.datasets = [...lapsByDriver.entries()].map(([driver, lapTimes], index) => {
      const colour = driverColours[index % driverColours.length];
      return {
        label: driver,
        data: lapTimes,
        borderColor: colour,
        backgroundColor: colour,
        fill: false,
        tension: .25,
        pointRadius: 3,
        pointHoverRadius: 5,
        pointBackgroundColor: colour,
        pointBorderColor: colour,
        borderWidth: 2,
        spanGaps: false
      };
    });
    progression.update();
    $("#progression-empty").hidden = valid.length > 0;

    const sectorBest = [1, 2, 3].map(n => {
      const values = valid.map(lap => lap[`s${n}_ms`]).filter(value => value != null);
      return values.length ? Math.min(...values) / 1000 : null;
    });
    sector.data.datasets[0].data = sectorBest;
    sector.update();
    $("#sector-empty").hidden = sectorBest.some(value => value != null);

    const comparison = $("#driver-comparison");
    comparison.innerHTML = data.comparison.length ? data.comparison.map((driver, index) => `
      <tr><td><span class="rank-inline">${String(index + 1).padStart(2, "0")}</span><span class="table-primary">${safe(driver.driver)}</span></td>
      <td>${driver.laps}</td><td class="mono best-cell">${driver.best_time}</td><td class="mono">${driver.avg_time}</td><td class="mono">${driver.consistency_time}</td></tr>`).join("") : '<tr><td colspan="5" class="muted">No valid laps to compare.</td></tr>';

    const pbs = $("#personal-bests");
    pbs.innerHTML = data.personal_bests.length ? data.personal_bests.map((driver, index) => `
      <tr><td><span class="rank-inline">${String(index + 1).padStart(2, "0")}</span><span class="table-primary">${safe(driver.driver)}</span></td><td class="mono best-cell">${driver.best_time}</td><td>${driver.laps}</td></tr>`).join("") : '<tr><td colspan="3" class="muted">No personal bests recorded yet.</td></tr>';

    const body = $("#lap-table-body");
    body.innerHTML = data.laps.length ? [...data.laps].reverse().map(lap => `
      <tr><td>${safe(lap.session_date)}</td><td>${safe(lap.session_name)}</td><td class="table-primary">${safe(lap.driver)}</td>
      <td>${lap.lap_number}</td><td class="mono ${lap.valid ? "best-cell" : ""}">${safe(lap.lap_time)}</td>
      <td class="mono">${safe(lap.s1_time)}</td><td class="mono">${safe(lap.s2_time)}</td><td class="mono">${safe(lap.s3_time)}</td>
      <td><span class="badge ${lap.valid ? "badge-green" : "badge-red"}">${lap.valid ? "VALID" : "INVALID"}</span></td></tr>`).join("") : '<tr><td colspan="9" class="muted">No laps match these filters.</td></tr>';
  }
})();
