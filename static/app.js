const form = document.getElementById("search-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const subjectCard = document.getElementById("subject-card");
const statsCard = document.getElementById("stats-card");
const compsBody = document.querySelector("#comps-table tbody");
const submitBtn = form.querySelector("button[type=submit]");

let map = null;
let markers = [];

function fmtMoney(v) {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
}

function fmtDistance(km) {
  if (km === null || km === undefined) return "—";
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(2)} km`;
}

function ensureMap() {
  if (map) return map;
  map = L.map("map");
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(map);
  return map;
}

function clearMarkers() {
  markers.forEach((m) => m.remove());
  markers = [];
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const address = document.getElementById("address").value.trim();
  const radiusKm = parseFloat(document.getElementById("radius").value) || 1;
  if (!address) return;

  submitBtn.disabled = true;
  statusEl.textContent = "Searching…";
  statusEl.className = "";
  resultsEl.hidden = true;

  try {
    const res = await fetch(`/api/search?address=${encodeURIComponent(address)}&radius_km=${radiusKm}`);
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "Something went wrong.");
    }
    render(data);
    statusEl.textContent = "";
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.className = "error";
  } finally {
    submitBtn.disabled = false;
  }
});

function render(data) {
  resultsEl.hidden = false;

  subjectCard.innerHTML = `
    <h2>Subject property</h2>
    <div>${data.subject.matched_address}</div>
    ${data.subject.assessed_value !== null ? `<div>Assessed value: <strong>${fmtMoney(data.subject.assessed_value)}</strong></div>` : `<div class="pill">No matching assessment record found for this exact address — comps below are still based on location</div>`}
    ${data.subject.property_class ? `<div>Property class: ${data.subject.property_class}</div>` : ""}
  `;

  if (!data.precise_geo) {
    subjectCard.innerHTML += `<div class="pill">Coordinates not found in the dataset — comps are matched by community name instead of exact radius</div>`;
  }

  if (data.stats) {
    statsCard.innerHTML = `
      <h2>Comparable value summary (${data.stats.count} properties)</h2>
      <div class="stat-grid">
        <div><div class="value">${fmtMoney(data.stats.average)}</div><div class="label">Average</div></div>
        <div><div class="value">${fmtMoney(data.stats.median)}</div><div class="label">Median</div></div>
        <div><div class="value">${fmtMoney(data.stats.min)}</div><div class="label">Min</div></div>
        <div><div class="value">${fmtMoney(data.stats.max)}</div><div class="label">Max</div></div>
      </div>
    `;
  } else {
    statsCard.innerHTML = `<h2>Comparable value summary</h2><div>No assessed values found nearby to summarize.</div>`;
  }

  compsBody.innerHTML = "";
  data.comparables.forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${c.address || "—"}</td>
      <td>${c.property_class || "—"}</td>
      <td>${fmtDistance(c.distance_km)}</td>
      <td>${fmtMoney(c.assessed_value)}</td>
    `;
    compsBody.appendChild(tr);
  });

  const m = ensureMap();
  clearMarkers();
  const subjectMarker = L.marker([data.subject.lat, data.subject.lon], { title: "Subject" })
    .addTo(m)
    .bindPopup(`<strong>Subject</strong><br>${data.subject.matched_address}`);
  markers.push(subjectMarker);

  const bounds = [[data.subject.lat, data.subject.lon]];
  data.comparables.forEach((c) => {
    if (c.lat === null || c.lon === null || c.lat === undefined || c.lon === undefined) return;
    const marker = L.circleMarker([c.lat, c.lon], {
      radius: 6,
      color: "#1f6f50",
      fillColor: "#1f6f50",
      fillOpacity: 0.6,
    })
      .addTo(m)
      .bindPopup(`${c.address || "Nearby property"}<br>${fmtMoney(c.assessed_value)}`);
    markers.push(marker);
    bounds.push([c.lat, c.lon]);
  });

  if (bounds.length > 1) {
    m.fitBounds(bounds, { padding: [30, 30] });
  } else {
    m.setView([data.subject.lat, data.subject.lon], 14);
  }
}
