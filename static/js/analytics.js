/* Analytics Dashboard charts (Operational tab). Reads the JSON payload embedded
   by the server in #analytics-data and renders it with the locally-vendored
   Chart.js build (no CDN — this app runs on LAN sites with no internet). */

/* KPI count-up — subtle, professional tween from 0 to the server-rendered
   value for elements marked `data-countup`. Independent of the chart code
   below (runs even on pages/branches with no chart data) and a no-op for
   anyone with prefers-reduced-motion set. */
function fanscRunCountUp() {
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var els = document.querySelectorAll('[data-countup]');
  els.forEach(function (el) {
    var target = parseInt(el.getAttribute('data-countup'), 10);
    if (isNaN(target)) return;
    if (reduceMotion || target === 0) {
      el.textContent = target.toLocaleString();
      return;
    }
    var duration = 600;
    var start = null;
    function step(ts) {
      if (start === null) start = ts;
      var progress = Math.min((ts - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      el.textContent = Math.round(target * eased).toLocaleString();
      if (progress < 1) window.requestAnimationFrame(step);
      else el.textContent = target.toLocaleString();
    }
    window.requestAnimationFrame(step);
  });
}

document.addEventListener('DOMContentLoaded', function () {
  fanscRunCountUp();

  // Professional, restrained chart entrance — a moderate-duration ease-out
  // rather than Chart.js's default bouncy easing. Applies to every chart
  // created below since Chart.js reads these defaults at construction time.
  if (typeof Chart !== 'undefined') {
    Chart.defaults.animation = { duration: 600, easing: 'easeOutQuart' };
  }

  // Marks a chart's wrapper ready so the CSS skeleton (main.css
  // .chart-canvas-wrap) is removed the moment the chart has actually been
  // constructed — not on a fixed timer.
  function markChartReady(canvas) {
    if (canvas && canvas.parentElement) canvas.parentElement.classList.add('chart-ready');
  }

  var dataEl = document.getElementById('analytics-data');
  if (!dataEl || typeof Chart === 'undefined') return;

  var data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (e) {
    return;
  }

  // A chart left blank when its data array is empty reads as broken, not
  // "no data yet". Hide the canvas and say so instead.
  function showEmptyState(canvas, message) {
    if (!canvas) return;
    markChartReady(canvas);
    canvas.style.display = 'none';
    var msg = document.createElement('div');
    msg.className = 'text-muted small d-flex align-items-center justify-content-center h-100';
    msg.textContent = message || 'No data available for this period.';
    canvas.parentElement.appendChild(msg);
  }

  var trendCanvas = document.getElementById('verificationTrendChart');
  if (trendCanvas && data.daily_trend && data.daily_trend.length) {
    new Chart(trendCanvas, {
      type: 'line',
      data: {
        labels: data.daily_trend.map(function (r) { return r.day; }),
        datasets: [
          {
            label: 'Total Attempts',
            data: data.daily_trend.map(function (r) { return r.total; }),
            borderColor: '#0d6efd',
            backgroundColor: 'rgba(13,110,253,0.1)',
            tension: 0.2,
          },
          {
            label: 'Verified',
            data: data.daily_trend.map(function (r) { return r.verified; }),
            borderColor: '#198754',
            backgroundColor: 'rgba(25,135,84,0.1)',
            tension: 0.2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
    markChartReady(trendCanvas);
  } else if (trendCanvas) {
    showEmptyState(trendCanvas, 'No verification attempts in this period.');
  }

  var regCanvas = document.getElementById('registrationTrendChart');
  if (regCanvas && data.registration_trend && data.registration_trend.length) {
    new Chart(regCanvas, {
      type: 'bar',
      data: {
        labels: data.registration_trend.map(function (r) { return r.day; }),
        datasets: [{
          label: 'New Registrations',
          data: data.registration_trend.map(function (r) { return r.n; }),
          backgroundColor: '#0dcaf0',
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
    markChartReady(regCanvas);
  } else if (regCanvas) {
    showEmptyState(regCanvas, 'No new registrations in this period.');
  }

  // Executive tab charts (v2.2.0 Phase 6).
  var growthCanvas = document.getElementById('beneficiaryGrowthChart');
  if (growthCanvas && data.beneficiary_growth && data.beneficiary_growth.length) {
    new Chart(growthCanvas, {
      type: 'line',
      data: {
        labels: data.beneficiary_growth.map(function (r) { return r.month; }),
        datasets: [{
          label: 'Cumulative Beneficiaries',
          data: data.beneficiary_growth.map(function (r) { return r.cumulative; }),
          borderColor: '#0d6efd',
          backgroundColor: 'rgba(13,110,253,0.1)',
          tension: 0.2,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
    markChartReady(growthCanvas);
  } else if (growthCanvas) {
    showEmptyState(growthCanvas, 'No beneficiary registrations in this period.');
  }

  var distCanvas = document.getElementById('monthlyDistributionChart');
  if (distCanvas && data.monthly_distribution && data.monthly_distribution.length) {
    new Chart(distCanvas, {
      type: 'bar',
      data: {
        labels: data.monthly_distribution.map(function (r) { return r.month; }),
        datasets: [{
          label: 'Total Released (PHP)',
          data: data.monthly_distribution.map(function (r) { return r.total; }),
          backgroundColor: '#198754',
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
    markChartReady(distCanvas);
  } else if (distCanvas) {
    showEmptyState(distCanvas, 'No stipend amounts released in this period.');
  }

  // v2.2.0 Post-UAT Phase 13 — Claim Progress + Payout Completion
  // (Executive tab), scoped to the current/nearest stipend event.
  var dp = data.distribution_progress;
  var claimProgressCanvas = document.getElementById('claimProgressChart');
  if (claimProgressCanvas && dp) {
    new Chart(claimProgressCanvas, {
      type: 'doughnut',
      data: {
        labels: ['Claimed', 'Unclaimed'],
        datasets: [{
          data: [dp.claimed, dp.remaining],
          backgroundColor: ['#198754', '#e2e8f0'],
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
      },
    });
    markChartReady(claimProgressCanvas);
  }

  var payoutCompletionCanvas = document.getElementById('payoutCompletionChart');
  if (payoutCompletionCanvas && dp) {
    new Chart(payoutCompletionCanvas, {
      type: 'bar',
      data: {
        labels: ['Expected', 'Claimed', 'Remaining'],
        datasets: [{
          data: [dp.expected, dp.claimed, dp.remaining],
          backgroundColor: ['#0d6efd', '#198754', '#fd7e14'],
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
    markChartReady(payoutCompletionCanvas);
  }

  // v2.2.0 Post-UAT Phase 13 — Verification Results (Operational tab).
  var resultsCanvas = document.getElementById('verificationResultsChart');
  if (resultsCanvas && data.decision_breakdown && data.decision_breakdown.length) {
    var decisionColors = {
      verified: '#198754', not_verified: '#dc3545',
      manual_review: '#ffc107', denied: '#6c757d', unknown: '#adb5bd',
    };
    new Chart(resultsCanvas, {
      type: 'doughnut',
      data: {
        labels: data.decision_breakdown.map(function (r) {
          return r.decision.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
        }),
        datasets: [{
          data: data.decision_breakdown.map(function (r) { return r.n; }),
          backgroundColor: data.decision_breakdown.map(function (r) {
            return decisionColors[r.decision] || '#adb5bd';
          }),
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
      },
    });
    markChartReady(resultsCanvas);
  } else if (resultsCanvas) {
    showEmptyState(resultsCanvas, 'No verification results in this period.');
  }

  // v2.2.0 Post-UAT Phase 13 — Review/Security Cases (Security tab).
  var reviewCasesCanvas = document.getElementById('reviewCasesChart');
  if (reviewCasesCanvas && data.review_cases) {
    var rc = data.review_cases;
    new Chart(reviewCasesCanvas, {
      type: 'bar',
      data: {
        labels: ['Duplicate Face', 'Manual Review', 'Representative Review', 'Fraud Alerts'],
        datasets: [{
          data: [rc.duplicate_face, rc.manual_review, rc.representative_review, rc.fraud_alerts],
          backgroundColor: ['#fd7e14', '#ffc107', '#0dcaf0', '#dc3545'],
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
    markChartReady(reviewCasesCanvas);
  }

  // v2.1.19 UX pass — Dashboard "ONE USEFUL TREND": verifications + released
  // claims per day, last 7 days. Deliberately short-range/operational, not a
  // long-term growth chart (that stays under Executive Analytics).
  var dashTrendCanvas = document.getElementById('dashboardTrendChart');
  if (dashTrendCanvas && data.dashboard_trend && data.dashboard_trend.labels.length) {
    var dt = data.dashboard_trend;
    new Chart(dashTrendCanvas, {
      type: 'line',
      data: {
        labels: dt.labels,
        datasets: [
          {
            label: 'Verification Attempts',
            data: dt.verifications,
            borderColor: '#0d6efd',
            backgroundColor: 'rgba(13,110,253,0.1)',
            tension: 0.2,
          },
          {
            label: 'Claims Released',
            data: dt.claims,
            borderColor: '#198754',
            backgroundColor: 'rgba(25,135,84,0.1)',
            tension: 0.2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
    markChartReady(dashTrendCanvas);
  }

  // UX pass — Dashboard beneficiary status snapshot (not a trend; a
  // point-in-time distribution, so it stays a compact chart on the
  // Dashboard rather than duplicating an Analytics trend chart).
  var statusCanvas = document.getElementById('beneficiaryStatusChart');
  if (statusCanvas && data.beneficiary_status) {
    var bs = data.beneficiary_status;
    new Chart(statusCanvas, {
      type: 'doughnut',
      data: {
        labels: ['Active', 'Pending', 'Inactive', 'Deceased'],
        datasets: [{
          data: [bs.active, bs.pending, bs.inactive, bs.deceased],
          backgroundColor: ['#198754', '#fd7e14', '#6c757d', '#343a40'],
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
      },
    });
    markChartReady(statusCanvas);
  }
});
