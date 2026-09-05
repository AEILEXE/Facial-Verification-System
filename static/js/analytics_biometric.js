/* BPA-4 — Biometric Performance Analytics charts (score distribution + ROC).
   Reads the JSON payload the server embeds in #bpa-chart-data. All rates/
   counts/points are pre-computed by verification.biometric_analytics — this
   file only plots them with the locally-vendored Chart.js build. */
document.addEventListener('DOMContentLoaded', function () {
  var dataEl = document.getElementById('bpa-chart-data');
  if (!dataEl || typeof Chart === 'undefined') return;

  var data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (e) {
    return;
  }
  if (!data) return;

  function showEmptyState(canvas, message) {
    if (!canvas) return;
    canvas.style.display = 'none';
    var msg = document.createElement('div');
    msg.className = 'text-muted small d-flex align-items-center justify-content-center h-100';
    msg.textContent = message || 'No data available.';
    canvas.parentElement.appendChild(msg);
  }

  function binLabels(edges) {
    var labels = [];
    for (var i = 0; i < edges.length - 1; i++) {
      labels.push(edges[i].toFixed(2) + ' to ' + edges[i + 1].toFixed(2));
    }
    return labels;
  }

  // ── Score distribution: overlaid genuine vs. impostor histogram ──────────
  var scoreCanvas = document.getElementById('bpaScoreDistributionChart');
  var genuineHist = data.genuine_histogram;
  var impostorHist = data.impostor_histogram;
  if (scoreCanvas && (genuineHist || impostorHist)) {
    var edges = (genuineHist || impostorHist).bin_edges;
    var datasets = [];
    if (genuineHist) {
      datasets.push({
        label: 'Genuine',
        data: genuineHist.counts,
        backgroundColor: 'rgba(25,135,84,0.55)',
        borderColor: '#198754',
        borderWidth: 1,
      });
    }
    if (impostorHist) {
      datasets.push({
        label: 'Impostor',
        data: impostorHist.counts,
        backgroundColor: 'rgba(220,53,69,0.55)',
        borderColor: '#dc3545',
        borderWidth: 1,
      });
    }
    new Chart(scoreCanvas, {
      type: 'bar',
      data: { labels: binLabels(edges), datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
        scales: {
          x: { title: { display: true, text: 'Cosine similarity bin' }, ticks: { maxRotation: 60, minRotation: 60, font: { size: 9 } } },
          y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: 'Trial count' } },
        },
      },
    });
  } else if (scoreCanvas) {
    showEmptyState(scoreCanvas, 'Insufficient eligible data for a score distribution.');
  }

  // ── ROC curve ──────────────────────────────────────────────────────────
  var rocCanvas = document.getElementById('bpaRocChart');
  if (rocCanvas && data.roc_points && data.roc_points.length) {
    var points = data.roc_points.map(function (p) { return { x: p.fpr, y: p.tpr }; });
    new Chart(rocCanvas, {
      type: 'scatter',
      data: {
        datasets: [
          {
            label: 'ROC (matcher score)',
            data: points,
            showLine: true,
            borderColor: '#0d6efd',
            backgroundColor: 'rgba(13,110,253,0.15)',
            pointRadius: 2,
            tension: 0,
          },
          {
            label: 'Chance line',
            data: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
            showLine: true,
            borderColor: '#adb5bd',
            borderDash: [6, 4],
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
        scales: {
          x: { min: 0, max: 1, title: { display: true, text: 'False Positive Rate' } },
          y: { min: 0, max: 1, title: { display: true, text: 'True Positive Rate' } },
        },
      },
    });
  } else if (rocCanvas) {
    showEmptyState(rocCanvas, 'ROC unavailable — both genuine and impostor score samples are required.');
  }
});
