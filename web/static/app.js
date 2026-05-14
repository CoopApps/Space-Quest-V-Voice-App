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
    // Per-browser token for claiming a contributor name.  Anyone who
    // re-opens the site in the same browser keeps their identity; if
    // someone else tries to submit under the same name from a different
    // browser, the server rejects them with HTTP 409.
    contributorToken: (() => {
        let t = localStorage.getItem("sq5_contributor_token");
        if (!t) {
            t = (crypto.randomUUID ? crypto.randomUUID()
                                   : String(Math.random()).slice(2) + Date.now());
            localStorage.setItem("sq5_contributor_token", t);
        }
        return t;
    })(),
    // Result of the latest availability check.
    contributorNameStatus: "empty",   // empty | checking | free | yours | taken
};

$("#admin-token").value = state.adminToken;

// Header contributor name input — persistent, validated against the
// server so the user knows whether the name is free / theirs / taken.
const $contrib = $("#global-contributor");
const $nameSt  = $("#contributor-state");
$contrib.value = state.contributorName;

function setNameStatus(status, title = "") {
    state.contributorNameStatus = status;
    if (!$nameSt) return;
    $nameSt.className = "name-state " + status;
    $nameSt.title     = title;
    // Re-evaluate the Submit button if a line is currently open.
    refreshSubmitEnabled();
}

let _nameCheckTimer = null;
async function checkNameAvailability() {
    const name = (state.contributorName || "").trim();
    if (!name) {
        setNameStatus("empty",
            "A name is required before submitting. Type one to claim it.");
        return;
    }
    setNameStatus("checking", "Checking availability…");
    try {
        const r = await fetch("/api/contributors/check?" + new URLSearchParams({
            name, token: state.contributorToken,
        }));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (data.status === "yours") {
            setNameStatus("yours",
                "This name is registered to this browser — submissions go out under it.");
        } else if (data.status === "free") {
            setNameStatus("free",
                "Available — first submission will claim this name for you.");
        } else if (data.status === "taken") {
            setNameStatus("taken",
                "Already in use by someone else.  Pick a different name.");
        } else {
            setNameStatus("empty");
        }
    } catch (e) {
        setNameStatus("empty", "Couldn't reach server: " + e.message);
    }
}

$contrib.addEventListener("input", e => {
    state.contributorName = e.target.value;
    localStorage.setItem("sq5_contributor", state.contributorName);
    clearTimeout(_nameCheckTimer);
    _nameCheckTimer = setTimeout(checkNameAvailability, 350);
});

// Initial check on load (covers returning visitors with a stored name).
checkNameAvailability();

/**
 * Returns true when the user is allowed to submit a recording right
 * now: they have audio staged, an active line, and a valid name
 * (either available or already claimed by them).
 */
function canSubmitNow() {
    if (!state.recordedBlob || !state.activeLineKey) return false;
    return state.contributorNameStatus === "free"
        || state.contributorNameStatus === "yours";
}

function refreshSubmitEnabled() {
    const submit = document.getElementById("btn-submit");
    if (!submit) return;
    submit.disabled = !canSubmitNow();
    submit.title = canSubmitNow()
        ? "Submit this recording"
        : (!state.recordedBlob
            ? "Stage a recording or file first"
            : "Enter a valid name above before submitting");
}

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
    if (state.statusFilter === "needs-review") params.set("needs_review", "1");

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
        if (f === "missing")      return l.contribution_count === 0;
        if (f === "contributed")  return l.contribution_count > 0;
        if (f === "selected")     return l.selected_id !== null;
        if (f === "needs-review") return l.contribution_count >= 2 && !l.selected_id;
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

        <div class="dropzone" id="dropzone">
            <label>
                <input type="file" id="file-input" accept=".wav,.mp3,.flac,.ogg,.m4a,.aac,.aiff,.aif">
                <span id="dropzone-text">📂 Drop an audio file here, or click to pick one
                       (WAV, MP3, FLAC, OGG, M4A, AAC, AIFF)</span>
            </label>
        </div>

        <canvas id="pending-waveform" class="pending-waveform hidden"
                width="600" height="60"></canvas>
        <div id="pending-info" class="pending-info hidden"></div>

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
                : contributions.map((c, i) => renderContribution(c, adminEnabled, i + 1)).join("")}
        </div>
    `;
    $("#detail").innerHTML = html;

    $("#btn-copy").onclick = () => {
        navigator.clipboard.writeText(line.text);
        setStatus("Line text copied to clipboard.");
    };

    wireDropzone();
    wireRecord();
    wireContributions(adminEnabled);
}

function renderContribution(c, adminEnabled, index = 0) {
    const cls = "contrib" + (c.selected ? " selected" : "");
    const badge = adminEnabled && index ? `<span class="index-badge">[${index}]</span>` : "";
    return `
        <div class="${cls}" data-id="${c.id}">
            <div class="meta">
                <span class="by">${badge}${escapeHtml(c.contributor || "anonymous")}</span>
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
                        const r = await api.post(`/api/admin/select/${id}`, undefined, false, true);
                        const n = (r && r.cascaded_lines) || 1;
                        setStatus(n > 1
                            ? `Picked as canonical. Cascaded to ${n} of this contributor's lines for this character.`
                            : "Picked as canonical.");
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
    const cancel = $("#btn-cancel");
    if (rec) rec.textContent = "● Record";
    if (prev)   prev.disabled   = false;
    if (cancel) cancel.disabled = false;
    refreshSubmitEnabled();   // submit enables only if name is OK
    showPendingAudio(file, file.name);
    if (!canSubmitNow() && state.contributorNameStatus !== "yours" &&
        state.contributorNameStatus !== "free") {
        setStatus(`Loaded ${file.name} — set a name in the header to submit.`);
    } else {
        setStatus(`Loaded ${file.name} — click ✓ Submit to upload.`);
    }
}

// ---------------------------------------------------------------------------
// Pending-audio visualizer
// ---------------------------------------------------------------------------

async function showPendingAudio(blob, displayName) {
    const dz       = $("#dropzone");
    const dzText   = $("#dropzone-text");
    const canvas   = $("#pending-waveform");
    const info     = $("#pending-info");
    if (!dz) return;

    // Update the dropzone so it visibly confirms a file is staged.
    dz.classList.add("loaded");
    const sizeKb = (blob.size / 1024).toFixed(0);
    if (dzText) {
        dzText.innerHTML = `✓ <b>${escapeHtml(displayName)}</b> &nbsp; ${sizeKb} KB &nbsp;
                            <span style="opacity:.6">— click ✓ Submit to upload</span>`;
    }

    // Decode and draw a waveform.  Uses the same OfflineAudio path
    // blobToWav uses, but here we keep it visual only.
    try {
        const buf = await blob.arrayBuffer();
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const dec = await ctx.decodeAudioData(buf.slice(0));   // slice = copy
        ctx.close();

        // Match canvas internal pixel buffer to its on-screen size so the
        // waveform is crisp at any panel width.
        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.getBoundingClientRect();
        canvas.width  = Math.max(200, Math.round(rect.width  * dpr));
        canvas.height = Math.round(60 * dpr);

        // Build a peak-envelope array sized to canvas width.
        const cw = canvas.width, ch = canvas.height;
        const samples = dec.getChannelData(0);
        const bucket  = Math.max(1, Math.floor(samples.length / cw));
        const peaks   = new Float32Array(cw);
        for (let x = 0; x < cw; x++) {
            let max = 0;
            const start = x * bucket;
            const end   = Math.min(samples.length, start + bucket);
            for (let i = start; i < end; i++) {
                const v = Math.abs(samples[i]);
                if (v > max) max = v;
            }
            peaks[x] = max;
        }

        const c = canvas.getContext("2d");
        c.fillStyle   = "#181825";
        c.fillRect(0, 0, cw, ch);
        c.strokeStyle = "#89b4fa";
        c.lineWidth   = 1;
        c.beginPath();
        const mid = ch / 2;
        for (let x = 0; x < cw; x++) {
            const h = peaks[x] * (mid - 2);
            c.moveTo(x + 0.5, mid - h);
            c.lineTo(x + 0.5, mid + h);
        }
        c.stroke();
        canvas.classList.remove("hidden");

        if (info) {
            info.textContent = `${dec.duration.toFixed(2)}s · ${dec.sampleRate} Hz · ` +
                               `${dec.numberOfChannels === 1 ? "mono" : "stereo"}`;
            info.classList.remove("hidden");
        }
    } catch (e) {
        // Some uploads (e.g. raw .wav that the browser can't decode for
        // preview) will hit this — that's fine, the upload still works.
        canvas.classList.add("hidden");
        if (info) {
            info.textContent = `Cannot preview in browser (${e.message}). Submit will still work.`;
            info.classList.remove("hidden");
        }
    }
}

function clearPendingAudio() {
    const dz     = $("#dropzone");
    const dzText = $("#dropzone-text");
    const canvas = $("#pending-waveform");
    const info   = $("#pending-info");
    if (dz)     dz.classList.remove("loaded");
    if (dzText) dzText.textContent = "📂 Drop an audio file here, or click to pick one " +
                                     "(WAV, MP3, FLAC, OGG, M4A, AAC, AIFF)";
    if (canvas) canvas.classList.add("hidden");
    if (info)   info.classList.add("hidden");
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
            prev.disabled = cancel.disabled = false;
            refreshSubmitEnabled();
            showPendingAudio(state.recordedBlob, "(browser recording)");
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
        clearPendingAudio();
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
        clearPendingAudio();
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
    if (!canSubmitNow()) {
        setStatus("Set a valid name before submitting (see the header field).");
        return;
    }
    setStatus(`Uploading ${filename} (${(blob.size/1024).toFixed(0)} KB)…`);
    const fd = new FormData();
    fd.append("audio", blob, filename);
    fd.append("contributor",       state.contributorName.trim());
    fd.append("contributor_token", state.contributorToken);
    try {
        const result = await api.post(
            `/api/lines/${state.activeLineKey}/upload`, fd, true);
        setStatus(
            `✓ Uploaded ${filename}` +
            (result.duration_sec ? ` (${result.duration_sec.toFixed(1)}s)` : "") +
            ` as "${state.contributorName || "anonymous"}".`
        );
        // Re-render the detail panel with the new contribution and flash
        // the row for it so the user can see what just landed.
        await openLine(state.activeLineKey);
        const el = document.querySelector(
            `#contributions .contrib[data-id="${result.id}"]`);
        if (el) {
            el.classList.add("just-added");
            el.scrollIntoView({ block: "nearest", behavior: "smooth" });
            setTimeout(() => el.classList.remove("just-added"), 1700);
        }
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
// Admin-token validation: ping /api/admin/stats whenever the token
// changes (debounced ~300ms).  Updates the ✓ / ✗ pill next to the
// input and enables the compile buttons only when the server confirms.
let _tokenValidateTimer = null;

async function validateAdminToken() {
    const indicator = $("#admin-token-state");
    const token = state.adminToken;
    if (!token) {
        indicator.className = "token-state empty";
        indicator.title = "";
        $("#btn-compile").disabled     = true;
        $("#btn-compile-aud").disabled = true;
        refreshAdminStats();   // hides the stats strip
        return false;
    }
    indicator.className = "token-state checking";
    indicator.title = "Checking…";
    try {
        const r = await fetch("/api/admin/stats", {
            headers: { "X-Admin-Token": token },
        });
        if (r.ok) {
            indicator.className = "token-state valid";
            indicator.title = "Token accepted — admin actions enabled";
            $("#btn-compile").disabled     = false;
            $("#btn-compile-aud").disabled = false;
            setStatus("Admin token accepted.");
            refreshAdminStats();
            if (state.detail) renderDetail();
            return true;
        }
        indicator.className = "token-state invalid";
        indicator.title = `Server rejected token (HTTP ${r.status})`;
        $("#btn-compile").disabled     = true;
        $("#btn-compile-aud").disabled = true;
        setStatus(r.status === 401
            ? "Admin token rejected. Check the value in Railway's variables."
            : `Admin check failed (HTTP ${r.status}).`);
        return false;
    } catch (e) {
        indicator.className = "token-state invalid";
        indicator.title = "Network error during admin check";
        $("#btn-compile").disabled     = true;
        $("#btn-compile-aud").disabled = true;
        setStatus("Admin check failed: " + e.message);
        return false;
    }
}

$("#admin-token").oninput = e => {
    state.adminToken = e.target.value.trim();
    localStorage.setItem("sq5_admin_token", state.adminToken);
    clearTimeout(_tokenValidateTimer);
    _tokenValidateTimer = setTimeout(validateAdminToken, 300);
};

// Also validate on initial load if a token was already stored.
if (state.adminToken) {
    validateAdminToken();
}
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

// ---------------------------------------------------------------------------
// Admin stats strip (header) — shown only when an admin token is set.
// ---------------------------------------------------------------------------

async function refreshAdminStats() {
    const el = $("#admin-stats");
    if (!state.adminToken) { el.classList.add("hidden"); el.innerHTML = ""; return; }
    try {
        const r = await fetch("/api/admin/stats", {
            headers: { "X-Admin-Token": state.adminToken },
        });
        if (!r.ok) { el.classList.add("hidden"); el.innerHTML = ""; return; }
        const s = await r.json();
        const reviewClass = s.needs_review > 0 ? "warn" : "good";
        el.innerHTML = `
            <span><span class="stat-label">Contribs:</span>
                  <span class="stat-value">${s.total_contribs}</span></span>
            <span><span class="stat-label">Picks:</span>
                  <span class="stat-value good">${s.canonical_picks}</span></span>
            <span><span class="stat-label">Needs review:</span>
                  <span class="stat-value ${reviewClass}">${s.needs_review}</span></span>
        `;
        el.classList.remove("hidden");
    } catch (e) {
        el.classList.add("hidden");
    }
}

// refreshAll also pulls admin stats so the header strip stays in sync
// after deploys / picks / deletes.  (The admin-token input has its own
// validation/refresh path above — no separate wrapper needed.)
const _origRefreshAll = refreshAll;
refreshAll = function () {
    return Promise.all([_origRefreshAll(), refreshAdminStats()]);
};

// ---------------------------------------------------------------------------
// Keyboard shortcuts (active when an admin is logged in).
//   J / ↓ : next line                 K / ↑ : previous line
//   Enter : play first contribution   1..9 : pick that contribution canonical
//   ⇧+D   : delete focused contribution (with confirm)
// Shortcuts are ignored while typing in an input/textarea/select.
// ---------------------------------------------------------------------------

let _focusedContribIdx = 0;

document.addEventListener("keydown", async (e) => {
    const t = e.target;
    if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
    if (!state.adminToken) return;   // shortcuts are admin-only

    const rows = $$(".line-row");
    const activeRow = rows.findIndex(r => r.classList.contains("active"));

    const moveTo = (idx) => {
        if (idx < 0 || idx >= rows.length) return;
        rows[idx].scrollIntoView({ block: "nearest" });
        rows[idx].click();
    };

    if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        moveTo(activeRow < 0 ? 0 : activeRow + 1);
        return;
    }
    if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        moveTo(activeRow < 0 ? 0 : activeRow - 1);
        return;
    }

    // Contribution-level shortcuts require the detail panel be open.
    const contribs = $$("#contributions .contrib");
    if (contribs.length === 0) return;

    if (e.key >= "1" && e.key <= "9") {
        e.preventDefault();
        const idx = parseInt(e.key, 10) - 1;
        if (idx >= contribs.length) return;
        const id = parseInt(contribs[idx].dataset.id, 10);
        try {
            const r = await api.post(`/api/admin/select/${id}`, undefined, false, true);
            const n = (r && r.cascaded_lines) || 1;
            setStatus(n > 1
                ? `Picked contribution #${idx + 1} as canonical. Cascaded to ${n} lines.`
                : `Picked contribution #${idx + 1} as canonical.`);
            if (state.activeLineKey) await openLine(state.activeLineKey);
            refreshAll();
        } catch (err) { setStatus(err.message); }
        return;
    }

    if (e.key === "Enter") {
        e.preventDefault();
        const idx = _focusedContribIdx < contribs.length ? _focusedContribIdx : 0;
        const audio = contribs[idx].querySelector("audio");
        if (audio) {
            audio.currentTime = 0;
            audio.play();
        }
        return;
    }

    if (e.key === "D" && e.shiftKey) {
        e.preventDefault();
        if (contribs.length === 0) return;
        if (!confirm("Delete the first contribution for this line?")) return;
        const id = parseInt(contribs[0].dataset.id, 10);
        try {
            await api.del(`/api/admin/contributions/${id}`, true);
            setStatus("Deleted.");
            if (state.activeLineKey) await openLine(state.activeLineKey);
            refreshAll();
        } catch (err) { setStatus(err.message); }
    }
});

// Initial load
refreshAll().then(() => setStatus("Ready. Tip: enter admin token, then J/K to navigate, 1–9 to pick canonical."));
