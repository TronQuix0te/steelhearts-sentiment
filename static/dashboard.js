// ── State ──
let timelineChart = null;
let currentHours = 72;
let currentChannel = null;
let currentSentiment = null;
let ws = null;

// ── API Helpers ──

async function fetchJSON(url) {
    const res = await fetch(url);
    return res.json();
}

function sentimentParam() {
    return currentSentiment ? `&sentiment=${currentSentiment}` : "";
}

// ── Overview Cards ──

async function loadOverview() {
    const data = await fetchJSON(`/api/overview?_=1${sentimentParam()}`);
    document.getElementById("total-count").textContent = (data.total || 0).toLocaleString();
    document.getElementById("positive-count").textContent = (data.positive || 0).toLocaleString();
    document.getElementById("negative-count").textContent = (data.negative || 0).toLocaleString();
    document.getElementById("neutral-count").textContent = (data.neutral || 0).toLocaleString();
    const avg = data.avg_score != null ? data.avg_score.toFixed(2) : "--";
    document.getElementById("avg-score").textContent = avg;
}

// ── Timeline Chart ──

const MOMENT_COLORS = {
    announcement: "#5865f2",
    mint: "#43b581",
    partnership: "#faa61a",
    incident: "#f04747",
};

async function loadTimeline(hours) {
    currentHours = hours;
    const resp = await fetchJSON(`/api/timeline?hours=${hours}`);
    const data = resp.timeline || [];
    const moments = resp.moments || [];

    const labels = data.map(d => {
        const dt = new Date(d.bucket + "Z");
        return dt.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    });

    const datasets = [
        {
            label: "Positive",
            data: data.map(d => d.positive || 0),
            borderColor: "#43b581",
            backgroundColor: "rgba(67,181,129,0.1)",
            fill: true,
            tension: 0.3,
        },
        {
            label: "Negative",
            data: data.map(d => d.negative || 0),
            borderColor: "#f04747",
            backgroundColor: "rgba(240,71,71,0.1)",
            fill: true,
            tension: 0.3,
        },
        {
            label: "Neutral",
            data: data.map(d => d.neutral || 0),
            borderColor: "#96989d",
            backgroundColor: "rgba(150,152,157,0.05)",
            fill: true,
            tension: 0.3,
        },
        {
            label: "Avg Score",
            data: data.map(d => d.avg_score),
            borderColor: "#5865f2",
            borderDash: [5, 5],
            fill: false,
            tension: 0.3,
            yAxisID: "y1",
        },
    ];

    // Build annotation lines for key moments
    const annotations = {};
    moments.forEach((m, i) => {
        const mTime = new Date(m.timestamp + "Z");
        const mLabel = mTime.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
        // Find closest bucket index
        let closestIdx = 0;
        let closestDiff = Infinity;
        data.forEach((d, idx) => {
            const bTime = new Date(d.bucket + "Z");
            const diff = Math.abs(bTime - mTime);
            if (diff < closestDiff) {
                closestDiff = diff;
                closestIdx = idx;
            }
        });

        const color = MOMENT_COLORS[m.moment_type] || "#5865f2";
        annotations[`moment_${i}`] = {
            type: "line",
            xMin: closestIdx,
            xMax: closestIdx,
            borderColor: color,
            borderWidth: 2,
            borderDash: [6, 3],
            label: {
                display: true,
                content: m.label,
                position: "start",
                backgroundColor: color,
                color: "#fff",
                font: { size: 11, weight: "bold" },
                padding: 4,
                borderRadius: 3,
            },
        };
    });

    const chartConfig = {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
            legend: {
                labels: { color: "#dcddde", usePointStyle: true, pointStyle: "circle" },
            },
            annotation: { annotations },
        },
        scales: {
            x: {
                ticks: { color: "#72767d", maxRotation: 45, maxTicksLimit: 20 },
                grid: { color: "rgba(79,84,92,0.3)" },
            },
            y: {
                position: "left",
                beginAtZero: true,
                ticks: { color: "#72767d", precision: 0 },
                grid: { color: "rgba(79,84,92,0.3)" },
                title: { display: true, text: "Count", color: "#72767d" },
            },
            y1: {
                position: "right",
                min: -1,
                max: 1,
                ticks: { color: "#5865f2" },
                grid: { drawOnChartArea: false },
                title: { display: true, text: "Score", color: "#5865f2" },
            },
        },
    };

    if (timelineChart) {
        timelineChart.data.labels = labels;
        timelineChart.data.datasets = datasets;
        timelineChart.options.plugins.annotation.annotations = annotations;
        timelineChart.update();
    } else {
        const ctx = document.getElementById("timeline-chart").getContext("2d");
        timelineChart = new Chart(ctx, {
            type: "line",
            data: { labels, datasets },
            options: chartConfig,
        });
    }
}

// ── Channels ──

async function loadChannels() {
    const data = await fetchJSON("/api/channels");
    const container = document.getElementById("channel-list");

    let html = `<div class="channel-item ${currentChannel === null ? "active" : ""}" data-channel="">
        # all <span class="count">${data.reduce((s, c) => s + c.message_count, 0)}</span>
    </div>`;

    for (const ch of data) {
        const active = currentChannel === ch.channel_name ? "active" : "";
        html += `<div class="channel-item ${active}" data-channel="${ch.channel_name}">
            # ${ch.channel_name} <span class="count">${ch.message_count}</span>
        </div>`;
    }

    container.innerHTML = html;

    container.querySelectorAll(".channel-item").forEach(el => {
        el.addEventListener("click", () => {
            currentChannel = el.dataset.channel || null;
            loadChannels();
            loadMessages();
        });
    });
}

// ── Users Table ──

async function loadUsers() {
    const data = await fetchJSON(`/api/users?limit=20${sentimentParam()}`);
    const tbody = document.querySelector("#users-table tbody");

    tbody.innerHTML = data.map(u => {
        const avg = u.avg_score != null ? u.avg_score.toFixed(2) : "--";
        const avgColor = u.avg_score > 0.2 ? "#43b581" : u.avg_score < -0.2 ? "#f04747" : "#96989d";
        return `<tr>
            <td>${escapeHtml(u.author_name)}</td>
            <td>${u.message_count}</td>
            <td style="color:${avgColor}">${avg}</td>
            <td><span style="color:#43b581">+${u.positive || 0}</span> / <span style="color:#f04747">-${u.negative || 0}</span></td>
        </tr>`;
    }).join("");
}

// ── Message Feed ──

async function loadMessages() {
    const channelParam = currentChannel ? `&channel=${encodeURIComponent(currentChannel)}` : "";
    const data = await fetchJSON(`/api/recent?limit=50${channelParam}${sentimentParam()}`);
    renderMessages(data);
}

const SENTIMENT_CYCLE = ["positive", "neutral", "negative"];
const SENTIMENT_SCORES = { positive: 0.5, neutral: 0.0, negative: -0.5 };

function buildMsgHtml(m) {
    const msgId = m.discord_message_id || m.id || "";
    const sentClass = m.sentiment || "";
    const score = m.score != null ? m.score.toFixed(2) : "";
    let keywords = "";
    if (m.keywords) {
        try {
            const parsed = typeof m.keywords === "string" ? JSON.parse(m.keywords) : m.keywords;
            if (Array.isArray(parsed)) keywords = parsed.join(", ");
        } catch {}
    }
    const time = m.created_at ? new Date(m.created_at + "Z").toLocaleString([], {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
    }) : "";

    const sentBadge = sentClass
        ? `<span class="msg-sentiment ${sentClass}" data-msgid="${escapeHtml(msgId)}" data-sent="${sentClass}" title="Click to reclassify">${sentClass}</span>`
        : "";

    return `<div class="msg-item" data-msgid="${escapeHtml(msgId)}">
        <div class="msg-header">
            <span class="msg-author">${escapeHtml(m.author_name || "")}</span>
            <span class="msg-channel">#${escapeHtml(m.channel_name || "")}</span>
            ${sentBadge}
            ${score ? `<span class="score-bar">${score}</span>` : ""}
            <span class="msg-time">${time}</span>
        </div>
        <div class="msg-content">${escapeHtml(m.content || "")}</div>
        ${keywords ? `<div style="font-size:11px;color:#72767d;margin-top:2px">${escapeHtml(keywords)}</div>` : ""}
    </div>`;
}

function renderMessages(data) {
    const feed = document.getElementById("message-feed");
    feed.innerHTML = data.map(buildMsgHtml).join("");
    attachReclassifyHandlers(feed);
}

function prependMessage(m) {
    const feed = document.getElementById("message-feed");
    const div = document.createElement("div");
    div.innerHTML = buildMsgHtml(m);
    const msgEl = div.firstElementChild;
    feed.prepend(msgEl);
    attachReclassifyHandlers(feed);
    while (feed.children.length > 100) {
        feed.removeChild(feed.lastChild);
    }
}

async function reclassifySentiment(msgId, currentSent) {
    const idx = SENTIMENT_CYCLE.indexOf(currentSent);
    const next = SENTIMENT_CYCLE[(idx + 1) % SENTIMENT_CYCLE.length];
    const score = SENTIMENT_SCORES[next];

    await fetch(`/api/messages/${msgId}/sentiment`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sentiment: next, score }),
    });

    // Update the badge in-place
    const badge = document.querySelector(`.msg-sentiment[data-msgid="${msgId}"]`);
    if (badge) {
        badge.className = `msg-sentiment ${next}`;
        badge.dataset.sent = next;
        badge.textContent = next;
    }

    // Update the score display
    const msgItem = document.querySelector(`.msg-item[data-msgid="${msgId}"]`);
    if (msgItem) {
        const scoreEl = msgItem.querySelector(".score-bar");
        if (scoreEl) scoreEl.textContent = score.toFixed(2);
    }

    loadOverview();
}

function attachReclassifyHandlers(container) {
    container.querySelectorAll(".msg-sentiment[data-msgid]").forEach(badge => {
        badge.style.cursor = "pointer";
        badge.onclick = (e) => {
            e.stopPropagation();
            reclassifySentiment(badge.dataset.msgid, badge.dataset.sent);
        };
    });
}

// ── WebSocket ──

function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById("ws-status").textContent = "Live";
        document.getElementById("ws-status").className = "ws-badge connected";
    };

    ws.onclose = () => {
        document.getElementById("ws-status").textContent = "Disconnected";
        document.getElementById("ws-status").className = "ws-badge disconnected";
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = () => { ws.close(); };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "sentiment_update") {
            for (const item of msg.data) {
                prependMessage(item);
            }
            loadOverview();
            loadTimeline(currentHours);
            loadChannels();
            loadUsers();
        }
    };

    setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send("ping");
        }
    }, 30000);
}

// ── Insights ──

async function loadInsights() {
    const loading = document.getElementById("insights-loading");
    loading.classList.remove("hidden");

    try {
        const data = await fetchJSON("/api/insights");

        // Summary
        document.getElementById("insights-summary").textContent = data.summary || "No insights available.";

        // Action Items
        const actionsEl = document.getElementById("insights-actions");
        if (data.action_items && data.action_items.length) {
            actionsEl.innerHTML = data.action_items.map(a => {
                const pClass = `priority-${a.priority || "low"}`;
                return `<div class="insight-item">
                    <div class="insight-item-title">
                        <span class="priority-badge ${pClass}">${a.priority || "low"}</span>
                        ${escapeHtml(a.title)}
                    </div>
                    <div class="insight-item-detail">${escapeHtml(a.detail)}</div>
                </div>`;
            }).join("");
        } else {
            actionsEl.innerHTML = '<div class="insight-item"><div class="insight-item-detail">No action items.</div></div>';
        }

        // Risks
        const risksEl = document.getElementById("insights-risks");
        if (data.risks && data.risks.length) {
            risksEl.innerHTML = data.risks.map(r =>
                `<div class="insight-item risk-item">
                    <div class="insight-item-title">${escapeHtml(r.title)}</div>
                    <div class="insight-item-detail">${escapeHtml(r.detail)}</div>
                </div>`
            ).join("");
        } else {
            risksEl.innerHTML = '<div class="insight-item"><div class="insight-item-detail">No risks detected.</div></div>';
        }

        // Opportunities
        const oppEl = document.getElementById("insights-opportunities");
        if (data.opportunities && data.opportunities.length) {
            oppEl.innerHTML = data.opportunities.map(o =>
                `<div class="insight-item opp-item">
                    <div class="insight-item-title">${escapeHtml(o.title)}</div>
                    <div class="insight-item-detail">${escapeHtml(o.detail)}</div>
                </div>`
            ).join("");
        } else {
            oppEl.innerHTML = '<div class="insight-item"><div class="insight-item-detail">No opportunities detected.</div></div>';
        }
    } catch (err) {
        document.getElementById("insights-summary").textContent = "Failed to load insights.";
    } finally {
        loading.classList.add("hidden");
    }
}

document.getElementById("refresh-insights").addEventListener("click", () => {
    // Force cache bust by adding timestamp
    loadInsights();
});

// ── Key Moments Modal ──

function setupMomentModal() {
    const modal = document.getElementById("moment-modal");
    const btn = document.getElementById("add-moment-btn");
    const cancelBtn = document.getElementById("moment-cancel");
    const saveBtn = document.getElementById("moment-save");
    const tsInput = document.getElementById("moment-timestamp");

    btn.addEventListener("click", () => {
        // Default to now
        const now = new Date();
        tsInput.value = now.toISOString().slice(0, 16);
        modal.classList.remove("hidden");
    });

    cancelBtn.addEventListener("click", () => {
        modal.classList.add("hidden");
    });

    modal.addEventListener("click", (e) => {
        if (e.target === modal) modal.classList.add("hidden");
    });

    saveBtn.addEventListener("click", async () => {
        const label = document.getElementById("moment-label").value.trim();
        if (!label) return;

        const ts = tsInput.value;
        const desc = document.getElementById("moment-desc").value.trim();
        const type = document.getElementById("moment-type").value;

        // Convert local datetime to UTC string
        const utcDate = new Date(ts);
        const utcStr = utcDate.toISOString().replace("T", " ").slice(0, 19);

        await fetch("/api/moments", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                timestamp: utcStr,
                label: label,
                description: desc,
                moment_type: type,
            }),
        });

        modal.classList.add("hidden");
        document.getElementById("moment-label").value = "";
        document.getElementById("moment-desc").value = "";
        loadTimeline(currentHours);
    });
}

// ── Utilities ──

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ── Sentiment Filter ──

document.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentSentiment = btn.dataset.sentiment || null;
        loadOverview();
        loadUsers();
        loadMessages();
    });
});

// ── Chart Controls ──

document.querySelectorAll(".chart-controls .btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".chart-controls .btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        loadTimeline(parseInt(btn.dataset.hours, 10));
    });
});

// ── Init ──

async function init() {
    await Promise.all([
        loadOverview(),
        loadTimeline(currentHours),
        loadChannels(),
        loadUsers(),
        loadMessages(),
    ]);
    setupMomentModal();
    connectWebSocket();
    loadInsights();
}

init();
