const shortDate = new Intl.DateTimeFormat("pl-PL", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const state = { data: null, selected: null, activePoint: -1 };

function formatDate(value) {
  return value ? shortDate.format(new Date(`${value.slice(0, 10)}T00:00:00Z`)) : "brak";
}

function formatKg(value, digits = 1) {
  return value == null
    ? "brak"
    : `${value.toLocaleString("pl-PL", { maximumFractionDigits: digits })} kg`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function chartMarkup(exercise) {
  const points = exercise.workouts.filter((item) => item.maxWeightKg != null);
  if (!points.length) return '<div class="empty">Brak punktów do pokazania.</div>';

  const weights = points.map((item) => item.maxWeightKg);
  const rawMin = Math.min(...weights);
  const rawMax = Math.max(...weights);
  const padding = Math.max(5, (rawMax - rawMin) * 0.18);
  const min = Math.max(0, Math.floor((rawMin - padding) / 5) * 5);
  const max = Math.ceil((rawMax + padding) / 5) * 5 || min + 5;
  const x = (index) => points.length === 1 ? 500 : 56 + (index / (points.length - 1)) * 900;
  const y = (weight) => 38 + ((max - weight) / (max - min)) * 244;
  const path = points.map((point, index) =>
    `${index === 0 ? "M" : "L"} ${x(index)} ${y(point.maxWeightKg)}`,
  ).join(" ");
  const grid = Array.from({ length: 5 }, (_, index) => ({
    y: 38 + (244 * index) / 4,
    value: max - ((max - min) * index) / 4,
  }));
  state.activePoint = Math.min(
    state.activePoint < 0 ? points.length - 1 : state.activePoint,
    points.length - 1,
  );
  const active = points[state.activePoint];

  return `
    <section class="chart-section" aria-labelledby="chart-title">
      <div class="section-heading">
        <div><p class="eyebrow">Progres ciężaru</p><h2 id="chart-title">Maksymalny ciężar w treningu</h2></div>
        <div class="point-readout" id="point-readout">
          <strong>${formatKg(active.maxWeightKg)}</strong>
          <span>${formatDate(active.date)} · ${escapeHtml(active.sets)}</span>
        </div>
      </div>
      <div class="chart-wrap">
        <svg viewBox="0 0 1000 330" role="img" aria-label="Wykres progresu ${escapeHtml(exercise.label)}">
          ${grid.map((line) => `
            <g><line class="grid-line" x1="56" x2="956" y1="${line.y}" y2="${line.y}"></line>
            <text class="axis-label" x="44" y="${line.y + 4}" text-anchor="end">${Math.round(line.value)}</text></g>
          `).join("")}
          <path class="trend-line" d="${path}" style="stroke:${exercise.color}"></path>
          ${points.map((point, index) => `
            <g><circle class="data-hit" cx="${x(index)}" cy="${y(point.maxWeightKg)}" r="16"
              tabindex="0" data-point="${index}" aria-label="${formatDate(point.date)}, ${formatKg(point.maxWeightKg)}, ${escapeHtml(point.sets)}"></circle>
            <circle class="data-point" cx="${x(index)}" cy="${y(point.maxWeightKg)}" r="${index === state.activePoint ? 7 : 5}"
              style="fill:${exercise.color}"></circle></g>
          `).join("")}
          <text class="date-label" x="56" y="318">${formatDate(points[0].date)}</text>
          <text class="date-label" x="956" y="318" text-anchor="end">${formatDate(points.at(-1).date)}</text>
        </svg>
      </div>
    </section>`;
}

function render() {
  const exercise = state.data.exercises.find((item) => item.slug === state.selected);
  document.documentElement.style.setProperty("--accent", exercise.color);
  document.querySelector("#updated").textContent =
    `Dane Garmin Connect · aktualizacja ${formatDate(state.data.generatedAt)}`;
  document.querySelector("#tabs").innerHTML = state.data.exercises.map((item) => `
    <button class="tab ${item.slug === exercise.slug ? "active" : ""}" data-exercise="${item.slug}"
      aria-pressed="${item.slug === exercise.slug}">${escapeHtml(item.label.replace("Barbell ", ""))}</button>
  `).join("");

  document.querySelector("#dashboard").className = "";
  document.querySelector("#dashboard").innerHTML = `
    <section class="overview">
      <div class="title-block">
        <p class="eyebrow">Aktualny widok</p>
        <h1>${escapeHtml(exercise.label)}</h1>
        <p>${exercise.workoutCount} treningów · ${exercise.setCount} serii · od ${formatDate(exercise.firstWorkout)}</p>
      </div>
      <dl class="metrics">
        <div><dt>Rekord ciężaru</dt><dd>${formatKg(exercise.recordWeightKg)}</dd><span>${formatDate(exercise.recordWeightDate)}</span></div>
        <div><dt>Zmiana</dt><dd class="${(exercise.maxWeightChangeKg ?? 0) >= 0 ? "positive" : "negative"}">${(exercise.maxWeightChangeKg ?? 0) > 0 ? "+" : ""}${formatKg(exercise.maxWeightChangeKg)}</dd><span>pierwszy → ostatni trening</span></div>
        <div><dt>Ostatni trening</dt><dd>${formatKg(exercise.workouts.at(-1)?.maxWeightKg ?? null)}</dd><span>${formatDate(exercise.lastWorkout)}</span></div>
      </dl>
    </section>
    ${chartMarkup(exercise)}
    <section class="history" aria-labelledby="history-title">
      <div class="section-heading"><div><p class="eyebrow">Dziennik</p><h2 id="history-title">Ostatnie treningi</h2></div><span class="count">${exercise.workoutCount} łącznie</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Data</th><th>Serie</th><th>Max</th><th>Tonaż</th></tr></thead>
        <tbody>${[...exercise.workouts].reverse().map((workout) => `
          <tr><td>${formatDate(workout.date)}</td><td class="sets">${escapeHtml(workout.sets)}</td>
          <td>${formatKg(workout.maxWeightKg)}</td><td>${formatKg(workout.tonnageKg, 0)}</td></tr>
        `).join("")}</tbody>
      </table></div>
    </section>`;

  document.querySelectorAll("[data-exercise]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selected = button.dataset.exercise;
      state.activePoint = -1;
      render();
    });
  });
  const chartPoints = exercise.workouts.filter((item) => item.maxWeightKg != null);
  document.querySelectorAll("[data-point]").forEach((point) => {
    const activate = () => {
      state.activePoint = Number(point.dataset.point);
      const workout = chartPoints[state.activePoint];
      const readout = document.querySelector("#point-readout");
      if (readout && workout) {
        readout.querySelector("strong").textContent = formatKg(workout.maxWeightKg);
        readout.querySelector("span").textContent = `${formatDate(workout.date)} · ${workout.sets}`;
      }
      document.querySelectorAll("[data-point]").forEach((item) => {
        item.nextElementSibling.setAttribute(
          "r",
          item === point ? "7" : "5",
        );
      });
    };
    point.addEventListener("mouseenter", activate);
    point.addEventListener("focus", activate);
  });
}

fetch("data/strength-progress.json")
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then((data) => {
    state.data = data;
    state.selected = data.exercises[0]?.slug;
    render();
  })
  .catch(() => {
    document.querySelector("#dashboard").textContent = "Nie udało się wczytać danych.";
  });
