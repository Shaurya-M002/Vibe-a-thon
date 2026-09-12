const $ = (id) => document.getElementById(id);
const fmt = (t) =>
  `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
export function createJourneyEvidence(data, collection, seek) {
  let selected = null,
    time = 0;
  function chart(
    id,
    series,
    label,
    { bars = false, min = 0, max = 10, height = 160, events = [] } = {},
  ) {
    const W = Math.max(
        360,
        $(id).clientWidth || $("journeyEvidence").clientWidth - 44,
      ),
      L = 48,
      R = 18,
      T = 30,
      B = 28,
      H = height,
      xx = (t) => L + (t / selected.duration) * (W - L - R),
      yy = (v) => H - B - ((v - min) / (max - min)) * (H - T - B);
    let out = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${label}"><text x="${L}" y="15">${label}</text>`;
    for (let i = 0; i < 4; i++) {
      let v = min + ((max - min) * i) / 3;
      out += `<path d="M${L} ${yy(v)}H${W - R}" stroke="#2b4546"/><text x="2" y="${yy(v) + 4}">${v.toFixed(1)}</text>`;
    }
    for (let i = 0; i <= 5; i++) {
      let v = (selected.duration * i) / 5;
      out += `<text x="${xx(v) - 10}" y="${H - 4}">${fmt(v)}</text>`;
    }
    for (const e of events)
      out += `<rect x="${xx(e.start)}" y="${T}" width="${xx(e.end) - xx(e.start)}" height="${H - T - B}" fill="#b883ee" opacity=".17"/>`;
    for (const [points, color] of series) {
      if (bars) {
        for (const [t, v] of points)
          out += `<rect x="${xx(t)}" y="${yy(v)}" width="${Math.max(1, ((W - L - R) * 4) / selected.duration)}" height="${yy(0) - yy(v)}" fill="${color}"/>`;
      } else
        out += `<polyline points="${points.map((p) => `${xx(p[0])},${yy(p[1])}`).join(" ")}" fill="none" stroke="${color}" stroke-width="2.4"/>`;
    }
    out += `<path class="playhead" d="M${xx(time)} ${T}V${H - B}" stroke="#efb86d" stroke-width="2"/></svg>`;
    $(id).innerHTML = out;
    $(id).onclick = (e) => {
      const r = $(id).getBoundingClientRect();
      seek(
        selected.id,
        Math.max(
          0,
          Math.min(
            selected.duration,
            ((((e.clientX - r.left) / r.width) * W - L) / (W - L - R)) *
              selected.duration,
          ),
        ),
      );
    };
  }
  function render() {
    const s = selected,
      g = s.audit.gps,
      h = s.height.map((p) => p[1]),
      events = s.events[$("eventThreshold").value];
    const title = `Session ${s.id}`,
      description =
        s.story ||
        "Compare movement, height and signal changes along your route.";
    $("journeyOverview").innerHTML =
      `<div class="overview-copy"><p class="eyebrow">SESSION ${s.id} · ${s.start} IST</p><h2>${title}</h2><p>${description}</p></div><div class="overview-numbers"><div><b>${fmt(s.duration)}</b><span>duration</span></div><div><b>${s.steps.length}</b><span>steps</span></div><div><b>${g.median_accuracy_m ?? "—"} m</b><span>reported GPS accuracy</span></div></div>`;
    chart(
      "journeyHeight",
      [[s.height, "#87d5b8"]],
      "Relative pressure height · metres",
      {
        min: Math.min(-1, ...h) - 0.4,
        max: Math.max(2, ...h) + 0.4,
        height: 190,
        events,
      },
    );
    const bins = [];
    for (let t = 0; t < s.duration; t += 5)
      bins.push([t, s.steps.filter((x) => x >= t && x < t + 5).length]);
    chart("journeySteps", [[bins, "#72a9d9"]], "Detected steps / 5 seconds", {
      bars: true,
      max: Math.max(12, ...bins.map((p) => p[1])),
      height: 125,
    });
    let chunks = [],
      chunk = [];
    for (const p of s.gps) {
      if (chunk.length && p[0] - chunk.at(-1)[0] > 4) {
        chunks.push([chunk, "#72a9d9"]);
        chunk = [];
      }
      chunk.push([p[0], p[3]]);
    }
    if (chunk.length) chunks.push([chunk, "#72a9d9"]);
    chart(
      "journeyGPS",
      chunks,
      "GPS reported accuracy · metres (lower is better)",
      { max: Math.max(20, ...s.gps.map((p) => p[3])) },
    );
    $("journeyGPSHelp").textContent =
      g.first_s === undefined
        ? "No valid GPS fixes in this recording."
        : `First fix at ${fmt(g.first_s)}. Largest gap including recording edges: ${g.max_gap_including_edges_s}s. Reported accuracy is not measured error; gaps over four seconds are disconnected.`;
    chart(
      "journeyRadio",
      s.radios.map((r) => [r.bins, r.type === "wifi" ? "#87d5b8" : "#b883ee"]),
      "Distinct Wi-Fi / BLE identifiers per 10 seconds",
      {
        max: Math.max(10, ...s.radios.flatMap((r) => r.bins.map((p) => p[1]))),
      },
    );
    $("journeyEvents").innerHTML = events.length
      ? events
          .map(
            (e) =>
              `<button data-time="${e.start}">${fmt(e.start)}–${fmt(e.end)} · ${e.kind} · ${e.delta > 0 ? "+" : ""}${e.delta.toFixed(1)} m · ${e.steps} steps</button>`,
          )
          .join("")
      : '<p class="empty-evidence">No height event passes this threshold. Small or gradual changes may still be present.</p>';
    $("journeyEvents")
      .querySelectorAll("button")
      .forEach((b) => (b.onclick = () => seek(s.id, Number(b.dataset.time))));
    const sensorDefs = [
      [
        "linearAcceleration",
        "Movement intensity · m/s²",
        "Acceleration with gravity removed. Bursts can reveal footsteps or handling the phone.",
      ],
      [
        "rotation",
        "Turning rate · rad/s",
        "Peaks show phone rotation; they may reflect a turn or simply moving the phone.",
      ],
      [
        "magnetic",
        "Magnetic field · µT",
        "Changes may repeat near metal structures or lifts. Compare repeated visits before treating them as landmarks.",
      ],
      [
        "heading",
        "Phone compass heading · degrees",
        "The direction the phone points, not necessarily your walking direction. North wraps between 360° and 0°.",
      ],
      [
        "light",
        "Ambient light · lux",
        "Changes can reveal brighter or darker spaces, but a pocket or covered sensor also changes this reading.",
      ],
      [
        "pressure",
        "Air pressure · hPa",
        "The measured signal behind relative height. Weather and ventilation can also affect it.",
      ],
      [
        "acceleration",
        "Total acceleration · m/s²",
        "Includes gravity: a resting phone is usually near 9.8 m/s². Peaks show motion or vibration.",
      ],
    ].filter(([key]) => s.sensors?.[key]?.length > 1);
    $("journeySensors").innerHTML = sensorDefs
      .map(
        ([key, , help]) =>
          `<div id="sensor-${key}" class="sensor-chart"></div><p class="hint">${help}</p>`,
      )
      .join("");
    for (const [key, label] of sensorDefs) {
      const points = s.sensors[key],
        values = points.map((p) => p[1]),
        lo = Math.min(...values),
        hi = Math.max(...values),
        pad = Math.max((hi - lo) * 0.1, 0.1);
      let segments = [],
        segment = [];
      for (const p of points) {
        if (
          segment.length &&
          (p[0] - segment.at(-1)[0] > 4 ||
            (key === "heading" && Math.abs(p[1] - segment.at(-1)[1]) > 180))
        ) {
          segments.push([segment, "#87d5b8"]);
          segment = [];
        }
        segment.push(p);
      }
      if (segment.length) segments.push([segment, "#87d5b8"]);
      chart("sensor-" + key, segments, label, {
        min: key === "heading" ? 0 : Math.max(0, lo - pad),
        max: key === "heading" ? 360 : hi + pad,
        height: 150,
      });
    }
  }
  $("eventThreshold").onchange = () => {
    if (selected) render();
  };
  return {
    update(id, t) {
      time = t;
      if (selected?.id !== id) {
        selected = data.sessions.find((s) => s.id === id);
        render();
      } else {
        document.querySelectorAll(".sensor-chart .playhead").forEach((p) => {
          const x =
              48 +
              (t / selected.duration) *
                (p.ownerSVGElement.viewBox.baseVal.width - 48 - 18),
            h = Number(p.ownerSVGElement.viewBox.baseVal.height);
          p.setAttribute("d", `M${x} 30V${h - 28}`);
        });
      }
    },
  };
}
