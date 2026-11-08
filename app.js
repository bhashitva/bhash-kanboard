"use strict";

const login = document.querySelector("#login");
const logout = document.querySelector("#logout");
const registration = document.querySelector("#register");
const membership = document.querySelector("#membership");
const boardChoice = document.querySelector("#board-choice");
const inviteOwner = document.querySelector("#invite-owner");
const inviteResult = document.querySelector("#invite-result");
const inviteCode = document.querySelector("#invite-code");
const board = document.querySelector("#board");
const boardTitle = document.querySelector("#board-title");
const projectSettings = document.querySelector("#project-settings");
const layoutSettings = document.querySelector("#layout-settings");
const memberSettings = document.querySelector("#member-settings");
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
let currentBoard;
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

function canEdit() {
    return currentBoard?.role === "owner" || currentBoard?.role === "editor";
}

function path() {
    return "/api/boards/" + encodeURIComponent(currentBoard.id);
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
            target.append(element("h3", column.title));
            if (canEdit()) {
                target.append(action("कार्ड जोड़ें / Add card", "create", () => openCard(null, lane, column), column.title));
                target.addEventListener("dragover", event => event.preventDefault());
            }
            target.addEventListener("drop", event => {
                event.preventDefault();
                const id = Number(event.dataTransfer.getData("text/plain"));
                if (!canEdit() || !id) return;
                const remaining = column.tasks.filter(task => task.id !== id);
                const before = Number(event.target.closest(".card")?.dataset.task);
                if (before === id) return;
                const index = remaining.findIndex(task => task.id === before);
                void saveMove(id, lane.id, column.id, index < 0 ? remaining.length + 1 : index + 1, snapshot.revision);
            });
            for (const task of column.tasks) {
                const card = element("article", undefined, "card");
                card.dataset.task = task.id;
                card.append(element("p", task.title));
                if (task.description) card.append(element("p", task.description));
                if (canEdit()) {
                    card.draggable = true;
                    card.addEventListener("dragstart", event => event.dataTransfer.setData("text/plain", task.id));
                    const actions = element("div", undefined, "card-actions");
                    actions.append(
                        action("स्थान बदलें / Move", "move", () => chooseMove(task, lane, column), task.title),
                        action("संपादित करें / Edit", "edit", () => openCard(task, lane, column), task.title),
                        action("संग्रहित करें / Archive", "archive", () => changeArchive(task, "archive"), task.title)
                    );
                    card.append(actions);
                }
                target.append(card);
            }
            columns.append(target);
        }
        return section;
    });
    board.replaceChildren(...lanes);
    if (!lanes.length) board.append(element("p", "कोई आयातित परियोजना नहीं है। / No imported project."));
    board.dataset.revision = snapshot.revision;
    boardTitle.textContent = snapshot.name;
    studioGuide.hidden = currentBoard.id !== "studio" || lanes.length > 0;
    if (boardChoice.selectedOptions.length) boardChoice.selectedOptions[0].textContent = snapshot.name + " · " + currentBoard.role;
    if (!projectSettings.hidden) renderLayout();
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
        if (canEdit()) item.append(action("वापस लाएँ / Restore", "restore", () => changeArchive(card, "restore"), card.title));
        return item;
    }));
    if (!snapshot.archived.length) archiveList.append(element("p", "संग्रह खाली है। / The archive is empty."));
}

async function refresh() {
    const selected = currentBoard;
    if (!selected) return;
    const next = await request(path());
    if (currentBoard !== selected || (snapshot && next.revision <= snapshot.revision)) return;
    snapshot = next;
    render();
}

function refreshInBackground() {
    const selected = currentBoard;
    refresh().catch(error => {
        if (currentBoard !== selected) return;
        if (error.status === 401 || error.status === 403) void start(selected?.id);
        else status.textContent = "कार्यपट फिर से पढ़ा जाएगा। / Retrying board refresh.";
    });
}

function connect() {
    if (!currentBoard) return;
    const selected = currentBoard.id;
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const current = new WebSocket(scheme + "//" + location.host + "/live/" + encodeURIComponent(selected), "bhash.board.v1");
    socket = current;
    current.onmessage = event => {
        if (socket !== current) return;
        const update = JSON.parse(event.data);
        if (update.type === "ready" ||
            (update.type === "changed" && update.board === selected && update.revision > (snapshot?.revision ?? -1))) {
            refreshInBackground();
        }
    };
    current.onclose = event => {
        if (socket !== current) return;
        if (event.code === 1008) {
            void start(selected);
            return;
        }
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

function selectBoard(id) {
    closeBoard();
    currentBoard = session.boards.find(project => project.id === id);
    boardChoice.value = currentBoard?.id ?? "";
    board.hidden = !currentBoard;
    boardTitle.hidden = !currentBoard;
    boardTitle.textContent = currentBoard?.name ?? "";
    teamGuide.hidden = currentBoard?.id === "studio" || !canEdit();
    projectSettings.hidden = currentBoard?.role !== "owner";
    projectSettings.open = false;
    layoutSettings.replaceChildren();
    memberSettings.replaceChildren();
    studioGuide.hidden = currentBoard?.id !== "studio";
    inviteOwner.hidden = currentBoard?.role !== "owner";
    inviteOwner.open = false;
    inviteResult.hidden = true;
    inviteCode.value = "";
    if (currentBoard) {
        refreshInBackground();
        connect();
    }
}

function showSession(preferred) {
    const signedIn = Boolean(session.user);
    login.hidden = signedIn;
    registration.hidden = signedIn;
    membership.hidden = !signedIn;
    logout.hidden = !signedIn;
    boardChoice.replaceChildren(...session.boards.map(project =>
        new Option(project.name + " · " + project.role, project.id)
    ));
    document.querySelector("#board-selector").hidden = !session.boards.length;
    document.querySelector("#no-membership").hidden = Boolean(session.boards.length);
    const selected = session.boards.some(project => project.id === preferred) ? preferred : session.board;
    selectBoard(selected);
}

boardChoice.addEventListener("change", () => selectBoard(boardChoice.value));

document.querySelector("#issue-invite").addEventListener("submit", async event => {
    event.preventDefault();
    const selected = currentBoard;
    inviteResult.hidden = true;
    inviteCode.value = "";
    try {
        const result = await request(path() + "/invitations", "POST", Object.fromEntries(new FormData(event.target)));
        if (currentBoard !== selected) return;
        inviteCode.value = result.code;
        inviteResult.hidden = false;
        inviteCode.select();
        if (projectSettings.open) void renderMembers();
    } catch {
        if (currentBoard === selected) status.textContent = "निमंत्रण नहीं बन सका। / Could not invite that account.";
    }
});

document.querySelector("#accept-invite").addEventListener("submit", async event => {
    event.preventDefault();
    const account = session;
    const [boardId, token, extra] = event.target.elements.code.value.trim().split(".");
    if (!boardId || !token || extra !== undefined) {
        status.textContent = "पूरा निमंत्रण कोड दें। / Enter the full invitation code.";
        return;
    }
    try {
        await request("/api/invitations/accept", "POST", { board: boardId, token });
        const updated = await request("/session");
        if (session !== account) return;
        session = updated;
        event.target.reset();
        showSession(boardId);
        status.textContent = "निमंत्रण स्वीकार हुआ। / Invitation accepted.";
    } catch {
        if (session === account) status.textContent = "निमंत्रण स्वीकार नहीं हुआ। / Invitation could not be accepted.";
    }
});

document.querySelector("#create-board").addEventListener("submit", async event => {
    event.preventDefault();
    const account = session;
    const form = event.target;
    const submit = form.querySelector("button");
    submit.disabled = true;
    try {
        const created = await request("/api/boards", "POST", Object.fromEntries(new FormData(form)));
        if (session !== account) return;
        form.reset();
        document.querySelector("#create-project").open = false;
        await start(created.id);
    } catch {
        if (session === account) status.textContent = "कार्यपट नहीं बना। / Could not create the board.";
    } finally {
        submit.disabled = false;
    }
});

async function changeLayout(change, revision) {
    const selected = currentBoard;
    try {
        await request(path() + "/settings", "POST", { ...change, revision });
        if (currentBoard !== selected) return;
        status.textContent = "कार्यपट सहेजा गया। / Board settings saved.";
    } catch (error) {
        if (currentBoard !== selected) return;
        status.textContent = error.status === 409
            ? "बदलाव अस्वीकार है। क्रम और कार्डों की जगह जाँचें। / Check for newer edits or cards in that location."
            : "बदलाव नहीं सहेजा गया। / Could not save board settings.";
    }
    refreshInBackground();
}

function nameForm(label, current, change, revision) {
    const form = element("form");
    const field = element("label", label);
    const input = element("input");
    input.value = current;
    input.maxLength = 80;
    input.required = true;
    field.append(input);
    form.append(field, element("button", current ? "नाम सहेजें / Save name" : "जोड़ें / Add"));
    form.addEventListener("submit", event => {
        event.preventDefault();
        void changeLayout({ ...change, name: input.value }, revision);
    });
    return form;
}

function layoutRow(kind, item, index, siblings, lane, revision) {
    const keys = { kind, id: item.id, lane };
    const name = item.name ?? item.title;
    const label = kind === "lane" ? "पंक्ति / Swimlane" : "स्तंभ / Column";
    const form = nameForm(label, name, { ...keys, action: "rename" }, revision);
    const up = action("ऊपर / Earlier", "earlier", () => changeLayout({ ...keys, action: "move", position: index }, revision), name);
    const down = action("नीचे / Later", "later", () => changeLayout({ ...keys, action: "move", position: index + 2 }, revision), name);
    const remove = action("हटाएँ / Delete", "delete", () => changeLayout({ ...keys, action: "delete" }, revision), name);
    up.disabled = index === 0;
    down.disabled = index === siblings.length - 1;
    remove.disabled = siblings.length <= 1;
    form.append(up, down, remove);
    return form;
}

function renderLayout() {
    const { revision, lanes, name } = snapshot;
    const title = nameForm("कार्यपट का नाम / Board name", name, { kind: "board", action: "rename" }, revision);
    const addLane = nameForm("नई पंक्ति / New swimlane", "", { kind: "lane", action: "add" }, revision);
    const groups = lanes.map((lane, index) => {
        const group = element("section", undefined, "layout-lane");
        group.append(element("h4", lane.name), layoutRow("lane", lane, index, lanes, lane.id, revision));
        for (const [columnIndex, column] of lane.columns.entries()) {
            group.append(layoutRow("column", column, columnIndex, lane.columns, lane.id, revision));
        }
        group.append(nameForm("नया स्तंभ / New column", "", { kind: "column", action: "add", lane: lane.id }, revision));
        return group;
    });
    layoutSettings.replaceChildren(title, ...groups, addLane);
}

async function renderMembers() {
    const selected = currentBoard;
    if (selected?.role !== "owner") return;
    try {
        const members = await request(path() + "/members");
        if (currentBoard !== selected) return;
        memberSettings.replaceChildren(...members.map(member => {
            const row = element("form");
            row.append(element("p", member.username + (member.pending ? " · निमंत्रण / Invitation" : "")));
            if (!member.pending) {
                const field = element("label", "भूमिका / Role");
                const roles = element("select");
                roles.append(...["owner", "editor", "viewer"].map(role => new Option(role, role)));
                roles.value = member.role;
                field.append(roles);
                row.append(field, element("button", "भूमिका सहेजें / Save role"));
                row.addEventListener("submit", event => {
                    event.preventDefault();
                    void changeMember(member.username, "role", roles.value);
                });
            }
            row.append(action(member.pending ? "निमंत्रण रद्द करें / Revoke invite" : "सदस्य हटाएँ / Remove member",
                "remove-member", () => {
                    if (confirm("सदस्यता या निमंत्रण हटाएँ? / Remove this member or invitation?")) {
                        void changeMember(member.username, "remove");
                    }
                }, member.username));
            return row;
        }));
    } catch {
        if (currentBoard === selected) status.textContent = "सदस्य सूची नहीं मिली। / Could not load the member list.";
    }
}

async function changeMember(username, operation, role) {
    const selected = currentBoard;
    try {
        await request(path() + "/members", "POST", { username, action: operation, role });
        if (currentBoard !== selected) return;
        if (username === session.user) await start(selected.id);
        else void renderMembers();
        status.textContent = "सदस्यता सहेजी गई। / Membership saved.";
    } catch (error) {
        if (currentBoard !== selected) return;
        status.textContent = error.status === 409
            ? "एक मालिक आवश्यक है। / A board must retain an owner."
            : "सदस्यता नहीं बदली। / Could not change membership.";
    }
}

projectSettings.addEventListener("toggle", () => {
    if (projectSettings.open) void renderMembers();
});

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
    const selected = currentBoard;
    try {
        await request(path() + "/tasks/" + id, "PATCH", { lane, column, position, revision });
        if (currentBoard !== selected) return;
        status.textContent = "स्थान सहेजा गया। / Move saved.";
    } catch (error) {
        if (currentBoard !== selected) return;
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
    const selected = currentBoard;
    try {
        await request(path() + "/tasks/" + task.id + "/" + operation, "POST", { revision: snapshot.revision });
        if (currentBoard !== selected) return;
        status.textContent = operation === "archive" ? "कार्ड संग्रहित है। / Card archived." : "कार्ड लौट आया। / Card restored.";
    } catch (error) {
        if (currentBoard !== selected) return;
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

function accountForm(form, endpoint, failure) {
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!session) return;
        const submit = form.querySelector("button");
        submit.disabled = true;
        try {
            session = await request(endpoint, "POST", Object.fromEntries(new FormData(form)));
            login.reset();
            registration.reset();
            status.textContent = "";
            showSession();
        } catch {
            status.textContent = failure;
        } finally {
            submit.disabled = false;
        }
    });
}

accountForm(login, "/login", "प्रवेश नहीं मिला। / Sign-in failed.");
accountForm(registration, "/register", "खाता नहीं बना। नाम और कूटशब्द जाँचें। / Registration failed. Check the username and password.");

logout.addEventListener("click", async () => {
    try {
        await request("/logout", "POST");
        await start();
    } catch {
        status.textContent = "बाहर नहीं जा सके। / Sign-out failed.";
    }
});

async function start(preferred) {
    const previous = session;
    try {
        const next = await request("/session");
        if (session !== previous) return;
        session = next;
        showSession(preferred);
    } catch {
        status.textContent = "सर्वर उपलब्ध नहीं है। / Server unavailable.";
    }
}

// Reconcile after reconnects and if a request is cancelled immediately after a commit.
setInterval(() => { if (session?.user) refreshInBackground(); }, 15000);
void start();
