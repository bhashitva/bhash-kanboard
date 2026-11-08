"use strict";

const login = document.querySelector("#login");
const logout = document.querySelector("#logout");
const board = document.querySelector("#board");
const teamGuide = document.querySelector("#team-guide");
const studioGuide = document.querySelector("#studio-guide");
const status = document.querySelector("#status");
const move = document.querySelector("#move");
const cardForm = document.querySelector("#card-form");
const cardConflict = document.querySelector("#card-conflict");
const archive = document.querySelector("#archive");
const archiveSummary = document.querySelector("#archive-summary");
const archiveList = document.querySelector("#archive-list");
const cardSubmit = cardForm.querySelector('button[type="submit"]');
let session;
let snapshot;
let socket;
let retry;
let selection;
let draft;

async function request(path, method = "GET", body) {
    const headers = { Accept: "application/json" };
    if (method !== "GET") headers["X-CSRF-Token"] = session.csrf;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, { method, headers, body: JSON.stringify(body), cache: "no-store" });
    if (!response.ok) throw Object.assign(new Error("Request failed"), { status: response.status });
    return response.status === 204 ? null : response.json();
}

function element(name, content, className) {
    const node = document.createElement(name);
    if (content !== undefined) node.textContent = content;
    if (className) node.className = className;
    return node;
}

function action(label, name, handler, title) {
    const button = element("button", label);
    button.type = "button";
    button.dataset.action = name;
    if (title) button.setAttribute("aria-label", label + " " + title);
    button.addEventListener("click", handler);
    return button;
}

function path() {
    return "/api/boards/" + encodeURIComponent(session.board);
}

function render() {
    const lanes = snapshot.lanes.map(lane => {
        const section = element("section", undefined, "lane");
        section.dataset.lane = lane.id;
        const columns = element("div", undefined, "columns");
        section.append(element("h2", lane.name), columns);
        for (const column of lane.columns) {
            const target = element("section", undefined, "column");
            target.dataset.column = column.id;
            target.append(element("h3", column.title),
                action("कार्ड जोड़ें / Add card", "create", () => openCard(null, lane, column), column.title));
            target.addEventListener("dragover", event => event.preventDefault());
            target.addEventListener("drop", event => {
                event.preventDefault();
                const id = Number(event.dataTransfer.getData("text/plain"));
                if (!id) return;
                const remaining = column.tasks.filter(task => task.id !== id);
                const before = Number(event.target.closest(".card")?.dataset.task);
                if (before === id) return;
                const index = remaining.findIndex(task => task.id === before);
                void saveMove(id, lane.id, column.id, index < 0 ? remaining.length + 1 : index + 1, snapshot.revision);
            });
            for (const task of column.tasks) {
                const card = element("article", undefined, "card");
                card.dataset.task = task.id;
                card.draggable = true;
                card.addEventListener("dragstart", event => event.dataTransfer.setData("text/plain", task.id));
                const actions = element("div", undefined, "card-actions");
                actions.append(
                    action("स्थान बदलें / Move", "move", () => chooseMove(task, lane, column), task.title),
                    action("संपादित करें / Edit", "edit", () => openCard(task, lane, column), task.title),
                    action("संग्रहित करें / Archive", "archive", () => changeArchive(task, "archive"), task.title)
                );
                card.append(element("p", task.title));
                if (task.description) card.append(element("p", task.description));
                card.append(actions);
                target.append(card);
            }
            columns.append(target);
        }
        return section;
    });
    board.replaceChildren(...lanes);
    if (!lanes.length) board.append(element("p", "कोई आयातित परियोजना नहीं है। / No imported project."));
    board.dataset.revision = snapshot.revision;
    archive.hidden = !lanes.length;
    archiveSummary.textContent = "संग्रह / Archive (" + snapshot.archived.length + ")";
    archiveList.replaceChildren(...snapshot.archived.map(({ card, lane, column }) => {
        const item = element("article", undefined, "card");
        item.dataset.archivedTask = card.id;
        item.append(element("p", card.title));
        if (card.description) item.append(element("p", card.description));
        const source = snapshot.lanes.find(row => row.id === lane);
        const destination = source?.columns.find(target => target.id === column);
        if (destination) item.append(element("p", source.name + " · " + destination.title));
        item.append(action("वापस लाएँ / Restore", "restore", () => changeArchive(card, "restore"), card.title));
        return item;
    }));
    if (!snapshot.archived.length) archiveList.append(element("p", "संग्रह खाली है। / The archive is empty."));
}

async function refresh() {
    const account = session;
    const next = await request(path());
    if (session !== account || (snapshot && next.revision <= snapshot.revision)) return;
    snapshot = next;
    render();
}

function refreshInBackground() {
    refresh().catch(error => {
        if (error.status === 401 || error.status === 403) void start();
        else status.textContent = "कार्यपट फिर से पढ़ा जाएगा। / Retrying board refresh.";
    });
}

function connect() {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const current = new WebSocket(scheme + "//" + location.host + "/live/" + encodeURIComponent(session.board), "bhash.board.v1");
    socket = current;
    current.onmessage = event => {
        if (socket !== current) return;
        const update = JSON.parse(event.data);
        if (update.type === "ready" ||
            (update.type === "changed" && update.board === session.board && update.revision > (snapshot?.revision ?? -1))) {
            refreshInBackground();
        }
    };
    current.onclose = () => {
        if (socket !== current) return;
        retry = setTimeout(connect, 1500);
    };
}

function closeBoard() {
    clearTimeout(retry);
    const previous = socket;
    socket = null;
    previous?.close();
    snapshot = null;
    selection = null;
    board.replaceChildren();
    delete board.dataset.revision;
    move.hidden = true;
    closeCard();
    archive.hidden = true;
    archive.open = false;
    archiveList.replaceChildren();
}

function showSession() {
    closeBoard();
    login.hidden = Boolean(session.user);
    board.hidden = !session.user;
    teamGuide.hidden = !session.user || session.board !== "team";
    studioGuide.hidden = !session.user || session.board !== "studio";
    logout.hidden = !session.user;
    if (session.user) {
        refreshInBackground();
        connect();
    }
}

function chooseMove(task, lane, column) {
    closeCard();
    selection = { id: task.id, revision: snapshot.revision, lanes: snapshot.lanes };
    document.querySelector("#move-title").textContent = task.title;
    const options = snapshot.lanes.flatMap(row => row.columns.map(target =>
        new Option(row.name + " · " + target.title, row.id + "/" + target.id)
    ));
    move.elements.destination.replaceChildren(...options);
    move.elements.destination.value = lane.id + "/" + column.id;
    updatePositions();
    move.elements.position.value = column.tasks.indexOf(task) + 1;
    move.hidden = false;
    move.elements.destination.focus();
}

function updatePositions() {
    const [lane, column] = move.elements.destination.value.split("/").map(Number);
    const cards = selection.lanes.find(row => row.id === lane).columns.find(target => target.id === column).tasks;
    move.elements.position.max = cards.filter(task => task.id !== selection.id).length + 1;
    move.elements.position.value = move.elements.position.max;
}

async function saveMove(id, lane, column, position, revision) {
    try {
        await request(path() + "/tasks/" + id, "PATCH", { lane, column, position, revision });
        status.textContent = "स्थान सहेजा गया। / Move saved.";
    } catch (error) {
        status.textContent = error.status === 409
            ? "कार्यपट बदल गया है। फिर से कोशिश करें। / Board changed. Try again."
            : "स्थान सहेजा नहीं गया। / Move failed.";
    }
    move.hidden = true;
    refreshInBackground();
}

function closeCard() {
    draft = null;
    cardForm.hidden = true;
    cardConflict.hidden = true;
    cardSubmit.disabled = false;
    cardForm.reset();
}

function openCard(task, lane, column) {
    closeCard();
    move.hidden = true;
    draft = { id: task?.id, lane: lane.id, column: column.id, revision: snapshot.revision };
    document.querySelector("#card-form-title").textContent = task
        ? "कार्ड संपादित करें / Edit card"
        : "कार्ड जोड़ें / Add card · " + lane.name + " · " + column.title;
    cardForm.elements.title.value = task?.title ?? "";
    cardForm.elements.description.value = task?.description ?? "";
    cardForm.hidden = false;
    cardForm.elements.title.focus();
}

cardForm.addEventListener("submit", async event => {
    event.preventDefault();
    const editing = draft;
    if (!editing || cardSubmit.disabled) return;
    cardSubmit.disabled = true;
    const values = Object.fromEntries(new FormData(cardForm));
    try {
        if (editing.id === undefined) {
            await request(path() + "/tasks", "POST", {
                ...values, lane: editing.lane, column: editing.column, revision: editing.revision
            });
        } else {
            await request(path() + "/tasks/" + editing.id + "/details", "PATCH", {
                ...values, revision: editing.revision
            });
        }
        if (draft !== editing) return;
        closeCard();
        status.textContent = "कार्ड सहेजा गया। / Card saved.";
    } catch (error) {
        if (draft !== editing) return;
        if (error.status === 409) {
            cardConflict.hidden = false;
            status.textContent = "कार्यपट बदल गया है। / Board changed while you were editing.";
        } else {
            cardSubmit.disabled = false;
            status.textContent = error.status === 422
                ? "शीर्षक, विवरण और कार्यपट की सीमा जाँचें। / Check the title, description, and board limits."
                : "कार्ड सहेजा नहीं गया। / Card could not be saved.";
        }
    }
    refreshInBackground();
});
document.querySelector("#cancel-card").addEventListener("click", closeCard);

async function changeArchive(task, operation) {
    const account = session;
    try {
        await request(path() + "/tasks/" + task.id + "/" + operation, "POST", { revision: snapshot.revision });
        if (session !== account) return;
        status.textContent = operation === "archive" ? "कार्ड संग्रहित है। / Card archived." : "कार्ड लौट आया। / Card restored.";
    } catch (error) {
        if (session !== account) return;
        status.textContent = error.status === 409
            ? "कार्यपट बदल गया है। फिर से कोशिश करें। / Board changed. Try again."
            : "कार्ड बदला नहीं गया। / Card could not be changed.";
    }
    refreshInBackground();
}

move.elements.destination.addEventListener("change", updatePositions);
move.addEventListener("submit", event => {
    event.preventDefault();
    const [lane, column] = move.elements.destination.value.split("/").map(Number);
    void saveMove(selection.id, lane, column, move.elements.position.valueAsNumber, selection.revision);
});
document.querySelector("#cancel-move").addEventListener("click", () => { move.hidden = true; });

login.addEventListener("submit", async event => {
    event.preventDefault();
    try {
        session = await request("/login", "POST", Object.fromEntries(new FormData(login)));
        login.reset();
        status.textContent = "";
        showSession();
    } catch {
        status.textContent = "प्रवेश नहीं मिला। / Sign-in failed.";
    }
});

logout.addEventListener("click", async () => {
    try {
        await request("/logout", "POST");
        await start();
    } catch {
        status.textContent = "बाहर नहीं जा सके। / Sign-out failed.";
    }
});

async function start() {
    try {
        session = await request("/session");
        showSession();
    } catch {
        status.textContent = "सर्वर उपलब्ध नहीं है। / Server unavailable.";
    }
}

// Reconcile after reconnects and if a request is cancelled immediately after a commit.
setInterval(() => { if (session?.user) refreshInBackground(); }, 15000);
void start();
