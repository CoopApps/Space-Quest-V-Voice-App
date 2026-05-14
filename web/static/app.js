// SQ5 Voice Studio — community web edition.
// Single-page vanilla JS app. No build step.

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
    tab:           "characters",   // "characters" | "rooms"
    selectedCharacter: null,
    selectedRoom:      null,
    statusFilter:  "all",
    search:        "",
    activeLineKey: null,
    detail:        null,
    contributorName: localStorage.getItem("sq5_contributor") || "",
    adminToken:    localStorage.getItem("sq5_admin_token")   || "",
    isRecording:   false,
    mediaRecorder: null,
    recordedBlob:  null,
    recordedFilename: null,
};

$("#admin-token").value = state.adminToken;

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

const api = {
    async get(path) {
        const r = await fetch(path);
        if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
        return r.json();
    },
    async post(path, body, isForm = false, admin = false) {
        const headers = {};
        if (admin) headers["X-Admin-Token"] = state.adminToken;
        const opts = { method: "POST", headers };
        if (body !== undefined) {
            if (isForm)   opts.body = body;
            else        { opts.body = JSON.stringify(body);
                          headers["Content-Type"] = "application/json"; }
        }
        const r = await fetch(path, opts);
        if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
        return r.json();
    },
    async del(path, admin = false) {
        const headers = {};
        if (admin) headers["X-Admin-Token"] = state.adminToken;
        const r = await fetch(path, { method: "DELETE", headers });
        if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
        return r.json();
    },
};

function setStatus(msg) { $("#status-bar").textContent = msg; }

// ---------------------------------------------------------------------------
// Sidebar (characters / rooms)
// ---------------------------------------------------------------------------

async function loadCharacters() {
    const chars = await api.get("/api/characters");
    const ul    = $("#characters");
    const filter = $("#sidebar-filter").value.trim().toLowerCase();
    ul.innerHTML = "";
    let totalLines = 0, totalSelected = 0;
    for (const c of chars) {
        totalLines    += c.total;
        totalSelected += c.selected_count;
        if (filter && !c.name.toLowerCase().includes(filter)) continue;
        const li = document.createElement("li");
        const pct = c.total ? c.selected_count / c.total : 0;
        if (pct === 1)     li.classList.add("complete");
        else if (pct > 0)  li.classList.add("partial");
        if (state.selectedCharacter === c.talker_id) li.classList.add("active");
        li.innerHTML = `
            <span class="name">${escapeHtml(c.name)}</span>
            <span class="counts">${c.selected_count}/${c.total}</span>
        `;
        li.onclick = () => {
            state.selectedCharacter = c.talker_id;
            state.selectedRoom      = null;
            state.search            = "";
            $("#search").value      = "";
            loadCharacters();
            loadLines();
        };
        ul.appendChild(li);
    }
    updateOverallProgress(totalSelected, totalLines);
}

async function loadRooms() {
    const rooms = await api.get("/api/rooms");
    const ul    = $("#rooms");
    const filter = $("#sidebar-filter").value.trim().toLowerCase();
    ul.innerHTML = "";
    for (const r of rooms) {
        if (filter && !String(r.module).includes(filter)) continue;
        const li = document.createElement("li");
        const pct = r.total ? r.selected_count / r.total : 0;
        if (pct === 1)     li.classList.add("complete");
        else if (pct > 0)  li.classList.add("partial");
        if (state.selectedRoom === r.module) li.classList.add("active");
        li.innerHTML = `
            <span class="name">Room ${r.module}</span>
            <span class="counts">${r.selected_count}/${r.total}</span>
        `;
        li.onclick = () => {
            state.selectedRoom      = r.module;
            state.selectedCharacter = null;
            state.search            = "";
            $("#search").value      = "";
            loadRooms();
            loadLines();
        };
        ul.appendChild(li);
    }
}

function updateOverallProgress(done, total) {
    const pct = total ? Math.round((done / total) * 100) : 0;
    $("#overall-bar").max = total;
    $("#overall-bar").value = done;
    $("#overall-counts").textContent =
        `${done} / ${total} lines selected (${pct}%)`;
}

// ---------------------------------------------------------------------------
// Line list
// ---------------------------------------------------------------------------

async function loadLines() {
    const params = new URLSearchParams();
    if (state.search)            params.set("search", state.search);
    else if (state.selectedCharacter !== null) params.set("character", state.selectedCharacter);
    else if (state.selectedRoom !== null)      params.set("room",      state.selectedRoom);

    let lines;
    try {
        lines = await api.get(`/api/lines?${params}`);
    } catch (e) {
        setStatus("Failed to load lines: " + e.message);
        return;
    }

    // Client-side status filter on top of server query.
    const f = state.statusFilter;
    lines = lines.filter(l => {
        if (f === "missing")     return l.contribution_count === 0;
        if (f === "contributed") return l.contribution_count > 0;
        if (f === "selected")    return l.selected_id !== null;
        return true;
    });

    const list = $("#line-list");
    list.innerHTML = "";
    if (lines.length === 0) {
        $("#line-list-empty").classList.remove("hidden");
        return;
    }
    $("#line-list-empty").classList.add("hidden");

    for (const l of lines) {
        const key = lineKey(l);
        const row = document.createElement("div");
        row.className = "line-row";
        if (state.activeLineKey === key) row.classList.add("active");
        let statusText = "—", statusClass = "missing";
        if (l.selected_id) { statusText = "Selected"; statusClass = "selected"; }
        else if (l.contribution_count > 0) {
            statusText  = `${l.contribution_count} contrib`;
            statusClass = "contributed";
        }
        row.innerHTML = `
            <span class="room">${l.module}</span>
            <span class="text">${escapeHtml(l.text)}</span>
            <span class="status ${statusClass}">${statusText}</span>
        `;
        row.onclick = () => openLine(key);
        list.appendChild(row);
    }
}

function lineKey(l) {
    return `${l.module}-${l.noun}-${l.verb}-${l.cond}-${l.seq}`;
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

async function openLine(key) {
    state.activeLineKey = key;
    state.recordedBlob  = null;
    // Highlight active row.
    $$(".line-row").forEach(r => r.classList.remove("active"));
    $$(".line-row").forEach(r => {
        const t = r.querySelector(".room").textContent;
        // We compare via key inside onclick; cheap re-render via reload would
        // also work but this keeps the DOM stable.
    });
    let detail;
    try {
        detail = await api.get(`/api/lines/${key}`);
    } catch (e) {
        setStatus("Failed to load line: " + e.message);
        return;
    }
    state.detail = detail;
    renderDetail();
    // Update active highlight after re-render of list.
    loadLines();
}

function renderDetail() {
    const { line, contributions } = state.detail;
    const key = lineKey(line);

    const adminEnabled = !!state.adminToken;
    const html = `
        <div class="context">
            <span><b>${escapeHtml(line.character)}</b> · Room ${line.module} ·
                  noun=${line.noun} verb=${line.verb} cond=${line.cond} seq=${line.seq}</span>
            <button class="copy" id="btn-copy">📋 Copy</button>
        </div>

        <div class="text-box" id="line-text">${escapeHtml(line.text)}</div>

        <div class="contrib-name-row">
            <input id="contributor-name" placeholder="Your name (optional)"
                   value="${escapeHtml(state.contributorName)}">
        </div>

        <div class="dropzone" id="dropzone">
            <label>
                <input type="file" id="file-input" accept=".wav,.mp3,.flac,.ogg,.m4a,.aac,.aiff,.aif">
                <span>📂 Drop an audio file here, or click to pick one
                       (WAV, MP3, FLAC, OGG, M4A, AAC, AIFF)</span>
            </label>
        </div>

        <div class="controls">
            <button id="btn-record" class="record">● Record</button>
            <button id="btn-preview" disabled>▶ Preview</button>
            <button id="btn-submit" class="primary" disabled>✓ Submit</button>
            <button id="btn-cancel"  disabled>✗ Cancel</button>
        </div>

        <h3 class="section">Contributions (${contributions.length})</h3>
        <div class="contributions" id="contributions">
            ${contributions.length === 0
                ? '<p class="empty">No recordings yet for this line.</p>'
                : contributions.map(c => renderContribution(c, adminEnabled)).join("")}
        </div>
    `;
    $("#detail").innerHTML = html;

    $("#btn-copy").onclick = () => {
        navigator.clipboard.writeText(line.text);
        setStatus("Line text copied to clipboard.");
    };
    $("#contributor-name").oninput = e => {
        state.contributorName = e.target.value;
        localStorage.setItem("sq5_contributor", state.contributorName);
    };

    wireDropzone();
    wireRecord();
    wireContributions(adminEnabled);
}

function renderContribution(c, adminEnabled) {
    const cls = "contrib" + (c.selected ? " selected" : "");
    return `
        <div class="${cls}" data-id="${c.id}">
            <div class="meta">
                <span class="by">${escapeHtml(c.contributor || "anonymous")}</span>
                <span class="when">${escapeHtml(c.uploaded_at)}${
                    c.duration_sec
                        ? ` · ${c.duration_sec.toFixed(1)}s`
                        : ""}</span>
            </div>
            <audio controls preload="none" src="/api/contributions/${c.id}/audio"></audio>
            ${adminEnabled
                ? (c.selected
                    ? `<button class="success" data-act="selected" disabled>✓ Canonical</button>`
                    : `<button class="success" data-act="select">Make canonical</button>`)
                : `<span></span>`}
            ${adminEnabled
                ? `<button class="danger" data-act="delete">🗑</button>`
                : `<span></span>`}
        </div>
    `;
}

function wireContributions(adminEnabled) {
    if (!adminEnabled) return;
    $$("#contributions .contrib").forEach(el => {
        const id = parseInt(el.dataset.id, 10);
        el.querySelectorAll("button[data-act]").forEach(btn => {
            btn.onclick = async () => {
                const act = btn.dataset.act;
                try {
                    if (act === "select") {
                        await api.post(`/api/admin/select/${id}`, undefined, false, true);
                        setStatus("Marked as canonical.");
                    } else if (act === "delete") {
                        if (!confirm("Delete this contribution permanently?")) return;
                        await api.del(`/api/admin/contributions/${id}`, true);
                        setStatus("Deleted.");
                    }
                    await refreshAll();
                    if (state.activeLineKey) await openLine(state.activeLineKey);
                } catch (e) { setStatus(e.message); }
            };
        });
    });
}

// ---------------------------------------------------------------------------
// Upload (drag-drop or file picker)
// ---------------------------------------------------------------------------

function wireDropzone() {
    const dz    = $("#dropzone");
    const input = $("#file-input");
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("dragover"); };
    dz.ondragleave = () => dz.classList.remove("dragover");
    dz.ondrop = e => {
        e.preventDefault();
        dz.classList.remove("dragover");
        if (e.dataTransfer.files.length) acceptFile(e.dataTransfer.files[0]);
    };
    input.onchange = () => { if (input.files.length) acceptFile(input.files[0]); };
}

async function acceptFile(file) {
    // Stage the file the same way browser recordings stage: load it
    // into state.recordedBlob, enable Preview/Submit, and let the user
    // confirm before we upload it.  Keeps the UX consistent across
    // upload-from-file and record-in-browser.
    state.recordedBlob = file;
    state.recordedFilename = file.name;
    const rec    = $("#btn-record");
    const prev   = $("#btn-preview");
    const submit = $("#btn-submit");
    const cancel = $("#btn-cancel");
    if (rec) rec.textContent = "● Record";
    if (prev)   prev.disabled   = false;
    if (submit) submit.disabled = false;
    if (cancel) cancel.disabled = false;
    setStatus(`Loaded ${file.name} — click ✓ Submit to upload.`);
}

// ---------------------------------------------------------------------------
// In-browser recording (MediaRecorder)
// ---------------------------------------------------------------------------

function wireRecord() {
    const rec     = $("#btn-record");
    const prev    = $("#btn-preview");
    const submit  = $("#btn-submit");
    const cancel  = $("#btn-cancel");

    rec.onclick = async () => {
        if (state.isRecording) {
            state.mediaRecorder.stop();
            return;
        }
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e) {
            setStatus("Microphone access denied: " + e.message);
            return;
        }
        const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
            ? "audio/webm;codecs=opus" : "";
        const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        const chunks = [];
        mr.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
        mr.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            state.recordedBlob = new Blob(chunks, { type: mr.mimeType });
            state.isRecording = false;
            rec.classList.remove("recording");
            rec.textContent = "● Record again";
            prev.disabled = false; submit.disabled = false; cancel.disabled = false;
            setStatus(`Recorded ${(state.recordedBlob.size/1024).toFixed(0)} KB.`);
        };
        state.mediaRecorder = mr;
        state.isRecording   = true;
        rec.classList.add("recording");
        rec.textContent     = "⏹ Stop";
        prev.disabled = submit.disabled = cancel.disabled = true;
        mr.start();
        setStatus("Recording… click ⏹ Stop when done.");
    };

    prev.onclick = () => {
        if (!state.recordedBlob) return;
        const url = URL.createObjectURL(state.recordedBlob);
        const a = new Audio(url);
        a.onended = a.onerror = () => URL.revokeObjectURL(url);
        a.play().catch(e => setStatus("Preview failed: " + e.message));
    };

    cancel.onclick = () => {
        state.recordedBlob = null;
        state.recordedFilename = null;
        rec.textContent = "● Record";
        prev.disabled = submit.disabled = cancel.disabled = true;
        setStatus("Discarded.");
    };

    submit.onclick = async () => {
        if (!state.recordedBlob) return;

        // If the staged blob came from a real File (drag-drop / picker)
        // we have its original filename and can upload it as-is — the
        // server's loader handles WAV/MP3/FLAC/OGG/M4A/AAC/AIFF.
        // If it's a MediaRecorder blob (no File), it'll be webm/opus
        // which the backend can't decode, so transcode to WAV first.
        let blob     = state.recordedBlob;
        let filename = state.recordedFilename;

        const isUploadedFile = (blob instanceof File);
        if (!isUploadedFile) {
            const isWebmOrOgg = blob.type.includes("webm") || blob.type.includes("ogg");
            if (isWebmOrOgg) {
                try {
                    blob = await blobToWav(state.recordedBlob);
                } catch (e) {
                    setStatus("In-browser WAV conversion failed: " + e.message);
                    return;
                }
            }
            filename = "recording.wav";
        }

        await submitBlob(blob, filename);
        state.recordedBlob = null;
        state.recordedFilename = null;
        rec.textContent = "● Record";
        prev.disabled = submit.disabled = cancel.disabled = true;
    };
}

async function blobToWav(blob) {
    // Decode any codec the browser supports, then re-encode to a 16-bit PCM WAV.
    const arrayBuf = await blob.arrayBuffer();
    const audioCtx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(
        1, 1, 22050,  // dummy — we read real params after decode
    );
    // Use a live AudioContext to decode; offline ones can't decodeAudioData reliably.
    const decodeCtx = new (window.AudioContext || window.webkitAudioContext)();
    const decoded = await decodeCtx.decodeAudioData(arrayBuf);
    decodeCtx.close();

    // Mix to mono.
    const length = decoded.length;
    const sr     = decoded.sampleRate;
    const mono   = new Float32Array(length);
    for (let c = 0; c < decoded.numberOfChannels; c++) {
        const ch = decoded.getChannelData(c);
        for (let i = 0; i < length; i++) mono[i] += ch[i];
    }
    if (decoded.numberOfChannels > 1) {
        for (let i = 0; i < length; i++) mono[i] /= decoded.numberOfChannels;
    }

    // Encode as 16-bit PCM WAV.
    const buffer = new ArrayBuffer(44 + length * 2);
    const view   = new DataView(buffer);
    const writeStr = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off+i, s.charCodeAt(i)); };
    writeStr(0,  "RIFF");
    view.setUint32(4,  36 + length * 2, true);
    writeStr(8,  "WAVE");
    writeStr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1,  true);
    view.setUint16(22, 1,  true);
    view.setUint32(24, sr, true);
    view.setUint32(28, sr * 2, true);
    view.setUint16(32, 2,  true);
    view.setUint16(34, 16, true);
    writeStr(36, "data");
    view.setUint32(40, length * 2, true);
    for (let i = 0; i < length; i++) {
        const s = Math.max(-1, Math.min(1, mono[i]));
        view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
}

async function submitBlob(blob, filename) {
    if (!state.activeLineKey) return;
    const fd = new FormData();
    fd.append("audio", blob, filename);
    fd.append("contributor", state.contributorName || "anonymous");
    try {
        await api.post(`/api/lines/${state.activeLineKey}/upload`, fd, true);
        setStatus(`Uploaded ${filename}.`);
        await openLine(state.activeLineKey);
        refreshAll();
    } catch (e) {
        setStatus("Upload failed: " + e.message);
    }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function refreshAll() {
    return Promise.all([loadCharacters(), loadRooms()]);
}

function escapeHtml(s) {
    return String(s ?? "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

// Tabs
$$(".tabs .tab").forEach(t => {
    t.onclick = () => {
        $$(".tabs .tab").forEach(x => x.classList.remove("active"));
        t.classList.add("active");
        state.tab = t.dataset.tab;
        $("#characters").classList.toggle("hidden", state.tab !== "characters");
        $("#rooms"     ).classList.toggle("hidden", state.tab !== "rooms");
    };
});

// Sidebar filter
$("#sidebar-filter").oninput = () => { loadCharacters(); loadRooms(); };

// Search + status filter
let searchTimer;
$("#search").oninput = e => {
    clearTimeout(searchTimer);
    state.search = e.target.value.trim();
    searchTimer = setTimeout(loadLines, 200);
};
$("#status-filter").onchange = e => {
    state.statusFilter = e.target.value;
    loadLines();
};

// Admin token
$("#admin-token").oninput = e => {
    state.adminToken = e.target.value.trim();
    localStorage.setItem("sq5_admin_token", state.adminToken);
    $("#btn-compile").disabled     = !state.adminToken;
    $("#btn-compile-aud").disabled = !state.adminToken;
    if (state.detail) renderDetail();
};
$("#btn-compile").disabled     = !state.adminToken;
$("#btn-compile-aud").disabled = !state.adminToken;

async function downloadCompile(path, defaultFilename, label) {
    setStatus(`Compiling ${label}…`);
    try {
        const r = await fetch(path, {
            headers: { "X-Admin-Token": state.adminToken },
        });
        if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
        // Honor server-provided filename if any.
        const cd = r.headers.get("Content-Disposition") || "";
        const m  = cd.match(/filename="([^"]+)"/);
        const filename = (m ? m[1] : defaultFilename);
        const blob = await r.blob();
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement("a");
        a.href     = url; a.download = filename; a.click();
        URL.revokeObjectURL(url);
        setStatus(`${label} downloaded.`);
    } catch (e) {
        setStatus("Compile failed: " + e.message);
    }
}

$("#btn-compile").onclick = () =>
    downloadCompile("/api/admin/compile",     "sq5_voice_patches.zip", "patches");
$("#btn-compile-aud").onclick = () =>
    downloadCompile("/api/admin/compile/aud", "sq5_audio.zip",         "RESOURCE.AUD bundle");

// Initial load
refreshAll().then(() => setStatus("Ready."));
