const statusBadge = document.getElementById('connection-status');
const overallBar = document.getElementById('overall-bar');
const overallPercentage = document.getElementById('overall-percentage');
const overallStatus = document.getElementById('overall-status');
const overallCompleted = document.getElementById('overall-completed');
const overallTotal = document.getElementById('overall-total');
const downloadsContainer = document.getElementById('downloads-container');
const queueContainer = document.getElementById('queue-container');

let socket;

function connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    socket = new WebSocket(`${protocol}//${host}/ws/progress`);

    socket.onopen = () => {
        statusBadge.textContent = 'Connected';
        statusBadge.className = 'status-badge connected';
    };

    socket.onclose = () => {
        statusBadge.textContent = 'Disconnected';
        statusBadge.className = 'status-badge disconnected';
        setTimeout(connect, 2000);
    };

    socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updateUI(data);
    };
}

function updateUI(data) {
    // Overall
    const { overall, chats, active_downloads } = data;
    const percentage = overall.total > 0 ? Math.round((overall.completed / overall.total) * 100) : 0;

    overallBar.style.width = `${percentage}%`;
    overallPercentage.textContent = `${percentage}%`;
    overallStatus.textContent = overall.status;
    overallCompleted.textContent = overall.completed;
    overallTotal.textContent = overall.total;

    // Active Downloads
    const downloadIds = Object.keys(active_downloads);
    if (downloadIds.length === 0) {
        downloadsContainer.innerHTML = '<div class="empty-state">No active downloads</div>';
    } else {
        downloadsContainer.innerHTML = downloadIds.map(id => {
            const dl = active_downloads[id];
            const dlPerc = dl.total > 0 ? Math.round((dl.completed / dl.total) * 100) : 0;
            return `
                <div class="download-item">
                    <h3>${escapeHtml(dl.description)}</h3>
                    <div class="progress-info">
                        <span>${formatSize(dl.completed)} / ${formatSize(dl.total)}</span>
                        <span>${dlPerc}%</span>
                    </div>
                    <div class="mini-bar-container">
                        <div class="mini-bar" style="width: ${dlPerc}%"></div>
                    </div>
                </div>
            `;
        }).join('');
    }

    // Queue
    const chatIds = Object.keys(chats);
    if (chatIds.length === 0) {
        queueContainer.innerHTML = '<div class="empty-state">Queue is empty</div>';
    } else {
        queueContainer.innerHTML = chatIds.map(id => {
            const chat = chats[id];
            return `
                <div class="queue-item">
                    <h3>${escapeHtml(chat.title || id)}</h3>
                    <div class="status-badge ${chat.status.toLowerCase()}">${chat.status}</div>
                </div>
            `;
        }).join('');
    }
}

function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

connect();
