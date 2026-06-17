import json
import tkinter as tk
import os
import subprocess
import sys
import uuid
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import END, StringVar, Text, Tk, filedialog, messagebox, ttk

import lionscliapp as app
import machineroot


STATUS_VALUES = [
    "conceived",
    "repo created",
    "formalized",
]

DB_ROOT_KEYS = [
    "db-root-2026",
    "db-root2026",
]

REFRESH_MS = 10000


def main():
    app.declare_app("spark", "0.1.0")
    app.describe_app("Capture small project sparks in delightfully primitive JSON files.")
    app.declare_projectdir(".spark-capture")

    app.declare_key("uuid", "")
    app.declare_key("n", "20")
    app.declare_key("tag", "")

    app.describe_key("uuid", "UUID or UUID prefix of the spark to open or delete.")
    app.describe_key("n", "Number of recent sparks to list.")
    app.describe_key("tag", "Tag used by the tagged command.")

    app.declare_cmd("", cmd_show_browser)
    app.declare_cmd("new", cmd_open_editor)
    app.declare_cmd("setup", cmd_setup)
    app.declare_cmd("delete", cmd_delete)
    app.declare_cmd("list", cmd_list_recent)
    app.declare_cmd("tagged", cmd_tagged)

    app.describe_cmd("", "Open the spark browser window.")
    app.describe_cmd("new", "Open the spark capture form.")
    app.describe_cmd("setup", "Create the sparks folder inside the configured database root.")
    app.describe_cmd("delete", "Delete a spark by UUID or UUID prefix.")
    app.describe_cmd("list", "List the most recently modified sparks.")
    app.describe_cmd("tagged", "List sparks containing a tag.")

    app.main()


def cmd_setup():
    sparks_dir = get_sparks_dir()
    sparks_dir.mkdir(parents=True, exist_ok=True)
    print(f"created: {sparks_dir}")


def cmd_delete():
    sparks_dir = require_sparks_dir()
    spark_uuid = require_uuid_arg()
    spark_path = resolve_spark_path(sparks_dir, spark_uuid)
    spark_path.unlink()
    print(f"deleted: {spark_path.name}")


def cmd_list_recent():
    sparks_dir = require_sparks_dir()
    try:
        n = int(app.ctx["n"])
    except ValueError:
        raise SystemExit("--n must be an integer")

    spark_paths = sorted(
        sparks_dir.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in spark_paths[:n]:
        data = read_json_file(path)
        payload = data["data"]
        title = payload.get("title", "").strip() or "(untitled)"
        hook = payload.get("hook", "").strip()
        print(f"{title} {short_uuid(payload['uuid'])} -- {hook}")


def cmd_tagged():
    sparks_dir = require_sparks_dir()
    tag = app.ctx["tag"].strip()
    if not tag:
        raise SystemExit("use --tag <tag>")

    matches = []
    for path in sorted(sparks_dir.glob("*.json")):
        data = read_json_file(path)
        payload = data["data"]
        tags = payload.get("tags", [])
        if tag in tags:
            matches.append(payload)

    for payload in matches:
        title = payload.get("title", "").strip() or "(untitled)"
        hook = payload.get("hook", "").strip()
        print(f"{title} {short_uuid(payload['uuid'])} -- {hook}")


def cmd_show_browser():
    sparks_dir = require_sparks_dir()
    run_browser(sparks_dir)


def cmd_open_editor():
    sparks_dir = require_sparks_dir()
    existing_data = None
    requested_uuid = app.ctx["uuid"].strip()
    if requested_uuid:
        spark_path = resolve_spark_path(sparks_dir, requested_uuid)
        existing_data = read_json_file(spark_path)["data"]
    run_editor(existing_data, sparks_dir)


def run_browser(sparks_dir):
    root = Tk()
    root.title("spark")
    root.geometry("920x620")

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    sort_state = {"field": None, "reverse": False}

    tree = ttk.Treeview(frame, columns=("date", "title", "hook"), show="headings")
    tree.column("date", width=140, anchor="w")
    tree.column("title", width=260, anchor="w")
    tree.column("hook", width=500, anchor="w")

    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)

    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    button_row = ttk.Frame(frame)
    button_row.grid(row=1, column=0, sticky="w", pady=(10, 0))
    ttk.Button(button_row, text="New", command=lambda: open_editor_window(root, None, sparks_dir, refresh)).pack(side="left")
    ttk.Button(button_row, text="Refresh", command=lambda: refresh()).pack(side="left", padx=(8, 0))

    def set_sort(field):
        if sort_state["field"] == field:
            sort_state["reverse"] = not sort_state["reverse"]
        else:
            sort_state["field"] = field
            sort_state["reverse"] = field == "date"
        refresh()

    def refresh_headings():
        for field, label in (("date", "Date"), ("title", "Title"), ("hook", "Hook")):
            marker = ""
            if sort_state["field"] == field:
                marker = " v" if sort_state["reverse"] else " ^"
            tree.heading(field, text=f"{label}{marker}", command=lambda item=field: set_sort(item))

    def refresh():
        selection = tree.selection()
        selected_uuid = selection[0] if selection else None
        tree.delete(*tree.get_children())
        payloads = load_spark_payloads(sparks_dir)
        if sort_state["field"]:
            payloads = sorted(
                payloads,
                key=lambda payload: spark_sort_key(payload, sort_state["field"]),
                reverse=sort_state["reverse"],
            )
        refresh_headings()
        for payload in payloads:
            spark_date = resolve_spark_date(payload)
            title = payload.get("title", "").strip() or "(untitled)"
            hook = payload.get("hook", "").strip()
            tree.insert("", "end", iid=payload["uuid"], values=(spark_date, title, hook))
        if selected_uuid and tree.exists(selected_uuid):
            tree.selection_set(selected_uuid)

    def open_selected(event=None):
        selection = tree.selection()
        if not selection:
            return
        spark_path = resolve_spark_path(sparks_dir, selection[0])
        existing_data = read_json_file(spark_path)["data"]
        open_editor_window(root, existing_data, sparks_dir, refresh)

    tree.bind("<Double-1>", open_selected)
    refresh()

    def schedule_refresh():
        if not root.winfo_exists():
            return
        refresh()
        root.after(REFRESH_MS, schedule_refresh)

    root.after(REFRESH_MS, schedule_refresh)
    root.mainloop()


def run_editor(existing_data, sparks_dir):
    window = open_editor_window(None, existing_data, sparks_dir)
    window.mainloop()


def open_editor_window(parent, existing_data, sparks_dir, on_save=None):
    editor_state = build_editor_state(existing_data)
    today = today_string()

    if parent is None:
        window = Tk()
    else:
        window = tk.Toplevel(parent)
    window.title("spark")
    window.geometry("920x980")

    canvas = create_scroll_canvas(window)
    frame = ttk.Frame(canvas, padding=12)
    window_id = canvas.create_window((0, 0), window=frame, anchor="nw")
    frame.columnconfigure(1, weight=1)

    vars_map = build_variables(existing_data, today, editor_state)
    widgets = {}

    row = 0
    add_entry_row(frame, row, "uuid", vars_map["uuid"], widgets, width=46)
    row += 1
    add_entry_row(frame, row, "title", vars_map["title"], widgets, width=70, font=("TkDefaultFont", 14))
    row += 1
    add_entry_row(frame, row, "tags", vars_map["tags"], widgets, width=70)
    row += 1
    add_entry_row(frame, row, "hook", vars_map["hook"], widgets, width=70)
    row += 1
    add_entry_row(frame, row, "date-recorded", vars_map["date-recorded"], widgets, width=20)
    row += 1
    add_entry_row(frame, row, "date-conceived", vars_map["date-conceived"], widgets, width=30)
    row += 1
    add_combo_row(frame, row, "status", vars_map["status"], widgets, STATUS_VALUES, width=24)
    row += 1
    add_entry_row(frame, row, "graduated-to", vars_map["graduated-to"], widgets, width=70)
    row += 1

    row = add_known_records_section(frame, row, vars_map["known-records"], today, editor_state)
    row = add_text_row(frame, row, "synopsis", widgets, existing_data, height=3)
    row = add_text_row(frame, row, "why-it-matters", widgets, existing_data, height=3)
    row = add_text_row(frame, row, "notes", widgets, existing_data, height=4)
    row = add_text_row(frame, row, "related", widgets, existing_data, height=2)
    row = add_repo_path_row(frame, row, "repo-path", vars_map["repo-path"], widgets)
    row += 1
    row = add_folder_path_row(frame, row, "folder-path", vars_map["folder-path"], widgets)
    row += 1
    row = add_conversations_section(frame, row, vars_map["conversations"], editor_state)
    row = add_related_projects_section(frame, row, vars_map["related-projects"], editor_state)

    button_row = ttk.Frame(frame)
    button_row.grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 18))

    ttk.Button(
        button_row,
        text="Save",
        command=lambda: save_current_spark(window, widgets, vars_map, sparks_dir, editor_state, on_save),
    ).pack(side="left")
    ttk.Button(
        button_row,
        text="Copy to Clipboard",
        command=lambda: copy_current_spark_to_clipboard(window, widgets, vars_map, sparks_dir, editor_state),
    ).pack(side="left", padx=(8, 0))

    row += 1
    status_var = StringVar(value="")
    ttk.Label(frame, textvariable=status_var, anchor="w").grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    editor_state["status-var"] = status_var

    frame.bind("<Configure>", lambda event: sync_canvas(canvas, window_id, event))
    window.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"))
    return window


def create_scroll_canvas(root):
    container = ttk.Frame(root)
    container.pack(fill="both", expand=True)

    canvas = tk.Canvas(container, highlightthickness=0)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    return canvas


def sync_canvas(canvas, window_id, event):
    canvas.configure(scrollregion=canvas.bbox("all"))
    canvas.itemconfigure(window_id, width=event.width)


def build_editor_state(existing_data):
    generated_uuid = str(uuid.uuid4())
    active_uuid = existing_data["uuid"] if existing_data else generated_uuid
    return {
        "active-uuid": active_uuid,
        "generated-uuid": generated_uuid,
        "known-record-rows": [],
        "known-record-button": None,
        "conversation-rows": [],
        "related-project-rows": [],
        "status-var": None,
    }


def build_variables(existing_data, today, editor_state):
    data = existing_data or {}
    record_date = data.get("date-recorded", today)

    uuid_value = data.get("uuid", editor_state["active-uuid"])
    uuid_display = uuid_value

    return {
        "uuid": StringVar(value=uuid_display),
        "title": StringVar(value=data.get("title", "")),
        "tags": StringVar(value=" ".join(data.get("tags", []))),
        "hook": StringVar(value=data.get("hook", "")),
        "date-recorded": StringVar(value=record_date),
        "date-conceived": StringVar(value=data.get("date-conceived", "")),
        "status": StringVar(value=data.get("status", STATUS_VALUES[0])),
        "graduated-to": StringVar(value=data.get("graduated-to", "")),
        "repo-path": StringVar(value=data.get("repo-path", "")),
        "folder-path": StringVar(value=data.get("folder-path", "")),
        "known-records": data.get("known-records", [{"date-recorded": today, "location": "", "hook": ""}]),
        "conversations": data.get("conversations", [{"url": "", "hook": ""}, {"url": "", "hook": ""}]),
        "related-projects": data.get("related-projects", [{"project": ""}, {"project": ""}, {"project": ""}]),
    }


def add_entry_row(frame, row, label_text, variable, widgets, width=40, font=None):
    ttk.Label(frame, text=label_text).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
    entry = ttk.Entry(frame, textvariable=variable, width=width)
    if font:
        entry.configure(font=font)
    entry.grid(row=row, column=1, sticky="ew", pady=4)
    widgets[label_text] = entry


def add_combo_row(frame, row, label_text, variable, widgets, values, width=24):
    ttk.Label(frame, text=label_text).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
    combo = ttk.Combobox(frame, textvariable=variable, values=values, width=width)
    combo.grid(row=row, column=1, sticky="w", pady=4)
    widgets[label_text] = combo


def add_text_row(frame, row, label_text, widgets, existing_data, height):
    ttk.Label(frame, text=label_text).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(8, 4))
    text = Text(frame, width=72, height=height)
    text.grid(row=row, column=1, sticky="ew", pady=(8, 4))
    text.insert("1.0", (existing_data or {}).get(label_text, ""))
    widgets[label_text] = text
    return row + 1


def add_repo_path_row(frame, row, label_text, variable, widgets):
    ttk.Label(frame, text=label_text).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
    inner = ttk.Frame(frame)
    inner.grid(row=row, column=1, sticky="ew", pady=4)
    entry = ttk.Entry(inner, textvariable=variable, width=60)
    entry.grid(row=0, column=0, sticky="ew")
    ttk.Button(inner, text="Open", command=lambda: open_target(variable.get().strip())).grid(row=0, column=1, padx=(8, 0))
    inner.columnconfigure(0, weight=1)
    widgets[label_text] = entry
    return row


def add_folder_path_row(frame, row, label_text, variable, widgets):
    ttk.Label(frame, text=label_text).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
    inner = ttk.Frame(frame)
    inner.grid(row=row, column=1, sticky="ew", pady=4)
    entry = ttk.Entry(inner, textvariable=variable, width=50)
    entry.grid(row=0, column=0, sticky="ew")
    ttk.Button(inner, text="Browse", command=lambda: browse_for_folder(variable)).grid(row=0, column=1, padx=(8, 0))
    ttk.Button(inner, text="Open", command=lambda: open_target(variable.get().strip())).grid(row=0, column=2, padx=(8, 0))
    inner.columnconfigure(0, weight=1)
    widgets[label_text] = entry
    return row


def add_known_records_section(frame, row, records, today, editor_state):
    ttk.Label(frame, text="known-records").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(10, 4))
    section = ttk.Frame(frame)
    section.grid(row=row, column=1, sticky="ew")
    section.columnconfigure(0, weight=1)
    editor_state["known-record-rows"] = []
    for record in records:
        add_known_record_row(section, record, today, editor_state)
    editor_state["known-record-button"] = ttk.Button(
        section,
        text="Add known record",
        command=lambda: add_known_record_row(section, None, today, editor_state),
    )
    refresh_known_record_button(editor_state)
    return row + 1


def add_known_record_row(section, record, today, editor_state):
    index = len(editor_state["known-record-rows"])
    record = record or {"date-recorded": today, "location": "", "hook": ""}

    row_frame = ttk.LabelFrame(section, text=f"record {index + 1}", padding=8)
    row_frame.grid(row=index, column=0, sticky="ew", pady=(0, 8))

    date_var = StringVar(value=record.get("date-recorded", today))
    location_var = StringVar(value=record.get("location", ""))
    hook_var = StringVar(value=record.get("hook", ""))

    ttk.Label(row_frame, text="date-recorded").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
    ttk.Entry(row_frame, textvariable=date_var, width=18).grid(row=0, column=1, sticky="w", pady=2)
    ttk.Button(row_frame, text="set to today", command=lambda: date_var.set(today_string())).grid(row=0, column=2, padx=(8, 0), pady=2)

    ttk.Label(row_frame, text="location").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
    ttk.Entry(row_frame, textvariable=location_var, width=64).grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)

    ttk.Label(row_frame, text="hook").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=2)
    ttk.Entry(row_frame, textvariable=hook_var, width=64).grid(row=2, column=1, columnspan=2, sticky="ew", pady=2)
    row_frame.columnconfigure(1, weight=1)

    editor_state["known-record-rows"].append(
        {
            "date-recorded": date_var,
            "location": location_var,
            "hook": hook_var,
        }
    )
    refresh_known_record_button(editor_state)


def refresh_known_record_button(editor_state):
    if editor_state["known-record-button"] is None:
        return
    editor_state["known-record-button"].grid(
        row=len(editor_state["known-record-rows"]),
        column=0,
        sticky="w",
        pady=(6, 0),
    )


def add_conversations_section(frame, row, conversations, editor_state):
    ttk.Label(frame, text="conversations").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(10, 4))
    section = ttk.Frame(frame)
    section.grid(row=row, column=1, sticky="ew")
    editor_state["conversation-rows"] = []
    for index, conversation in enumerate(conversations, start=1):
        url_var = StringVar(value=conversation.get("url", ""))
        hook_var = StringVar(value=conversation.get("hook", ""))
        ttk.Label(section, text=f"url {index}").grid(row=(index - 1) * 2, column=0, sticky="w", pady=2)
        ttk.Entry(section, textvariable=url_var, width=58).grid(row=(index - 1) * 2, column=1, sticky="ew", pady=2)
        ttk.Button(section, text="Open", command=lambda var=url_var: open_target(var.get().strip())).grid(
            row=(index - 1) * 2,
            column=2,
            padx=(8, 0),
            pady=2,
        )
        ttk.Label(section, text=f"hook {index}").grid(row=(index - 1) * 2 + 1, column=0, sticky="w", pady=2)
        ttk.Entry(section, textvariable=hook_var, width=58).grid(row=(index - 1) * 2 + 1, column=1, columnspan=2, sticky="ew", pady=2)
        editor_state["conversation-rows"].append({"url": url_var, "hook": hook_var})
    section.columnconfigure(1, weight=1)
    return row + 1


def add_related_projects_section(frame, row, related_projects, editor_state):
    ttk.Label(frame, text="related-projects").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(10, 4))
    section = ttk.Frame(frame)
    section.grid(row=row, column=1, sticky="ew")
    section.columnconfigure(1, weight=1)
    editor_state["related-project-rows"] = []
    for index, related_project in enumerate(related_projects, start=1):
        project_var = StringVar(value=related_project.get("project", ""))
        ttk.Label(section, text=f"project {index}").grid(row=index - 1, column=0, sticky="w", pady=2)
        ttk.Entry(section, textvariable=project_var, width=36).grid(row=index - 1, column=1, sticky="ew", pady=2)
        ttk.Button(section, text="Open", command=lambda var=project_var: open_related_project(var.get().strip())).grid(
            row=index - 1,
            column=2,
            padx=(8, 0),
            pady=2,
        )
        editor_state["related-project-rows"].append({"project": project_var})
    return row + 1


def save_current_spark(window, widgets, vars_map, sparks_dir, editor_state, on_save=None):
    try:
        payload = collect_form_data(widgets, vars_map, sparks_dir, editor_state)
    except ValueError as exc:
        messagebox.showerror("spark", str(exc))
        return

    document = {
        "type": "spark-capture",
        "version": "1.0",
        "data": payload,
    }
    spark_path = sparks_dir / f"{payload['uuid']}.json"
    spark_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    if on_save:
        on_save()
    messagebox.showinfo("spark", f"saved:\n{spark_path}")
    window.destroy()


def copy_current_spark_to_clipboard(window, widgets, vars_map, sparks_dir, editor_state):
    try:
        payload = collect_form_data(widgets, vars_map, sparks_dir, editor_state)
    except ValueError as exc:
        messagebox.showerror("spark", str(exc))
        return

    document = {
        "type": "spark-capture",
        "version": "1.0",
        "data": payload,
    }
    text = json.dumps(document, indent=2) + "\n"
    window.clipboard_clear()
    window.clipboard_append(text)
    window.update()
    set_status(editor_state, "spark JSON copied to clipboard")


def set_status(editor_state, message):
    if editor_state["status-var"] is not None:
        editor_state["status-var"].set(message)


def collect_form_data(widgets, vars_map, sparks_dir, editor_state):
    spark_uuid = resolve_form_uuid(vars_map["uuid"].get().strip(), sparks_dir, editor_state)
    data = {
        "uuid": spark_uuid,
        "title": vars_map["title"].get().strip(),
        "tags": split_tags(vars_map["tags"].get()),
        "hook": vars_map["hook"].get().strip(),
        "date-recorded": vars_map["date-recorded"].get().strip(),
        "date-conceived": vars_map["date-conceived"].get().strip(),
        "status": vars_map["status"].get().strip(),
        "graduated-to": vars_map["graduated-to"].get().strip(),
        "known-records": collect_known_records(editor_state),
        "synopsis": read_text_widget(widgets["synopsis"]),
        "why-it-matters": read_text_widget(widgets["why-it-matters"]),
        "notes": read_text_widget(widgets["notes"]),
        "related": read_text_widget(widgets["related"]),
        "repo-path": vars_map["repo-path"].get().strip(),
        "folder-path": vars_map["folder-path"].get().strip(),
        "conversations": collect_conversations(editor_state),
        "related-projects": collect_related_projects(editor_state),
    }
    return data


def collect_known_records(editor_state):
    records = []
    for record in editor_state["known-record-rows"]:
        item = {
            "date-recorded": record["date-recorded"].get().strip(),
            "location": record["location"].get().strip(),
            "hook": record["hook"].get().strip(),
        }
        if item["date-recorded"] or item["location"] or item["hook"]:
            records.append(item)
    return records


def collect_conversations(editor_state):
    conversations = []
    for conversation in editor_state["conversation-rows"]:
        item = {
            "url": conversation["url"].get().strip(),
            "hook": conversation["hook"].get().strip(),
        }
        if item["url"] or item["hook"]:
            conversations.append(item)
    return conversations


def collect_related_projects(editor_state):
    related_projects = []
    for related_project in editor_state["related-project-rows"]:
        project = related_project["project"].get().strip()
        if project:
            related_projects.append({"project": project})
    return related_projects


def resolve_form_uuid(value, sparks_dir, editor_state):
    if not value:
        raise ValueError("uuid is required")
    if value == editor_state["active-uuid"]:
        return editor_state["active-uuid"]
    if editor_state["active-uuid"] and value != short_uuid(editor_state["active-uuid"]) and editor_state["active-uuid"].startswith(value):
        return editor_state["active-uuid"]
    try:
        return str(uuid.UUID(value))
    except ValueError:
        pass

    matches = sorted(sparks_dir.glob(f"{value}*.json"))
    if len(matches) == 1:
        return matches[0].stem
    if len(matches) > 1:
        raise ValueError(f"uuid prefix is ambiguous: {value}")
    raise ValueError("uuid must be a full UUID, or a unique short prefix")


def require_uuid_arg():
    spark_uuid = app.ctx["uuid"].strip()
    if not spark_uuid:
        raise SystemExit("use --uuid <uuid>")
    return spark_uuid


def get_sparks_dir():
    db_root = find_db_root()
    if db_root is None:
        raise SystemExit("unable to locate a machine-root database root")
    return db_root / "sparks"


def require_sparks_dir():
    sparks_dir = get_sparks_dir()
    if not sparks_dir.exists():
        raise SystemExit("run: 'spark setup' before using the 'spark' command.")
    return sparks_dir


def find_db_root():
    for key in DB_ROOT_KEYS:
        path = machine_root_get(key)
        if path:
            return Path(path)
    return None


def machine_root_get(key):
    try:
        return machineroot.get(key)
    except Exception:
        return None


def resolve_spark_path(sparks_dir, prefix):
    matches = sorted(sparks_dir.glob(f"{prefix}*.json"))
    if not matches:
        raise SystemExit(f"no spark matches uuid prefix: {prefix}")
    if len(matches) > 1:
        names = ", ".join(path.stem for path in matches[:5])
        raise SystemExit(f"uuid prefix is ambiguous: {prefix} -> {names}")
    return matches[0]


def read_json_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_spark_payloads(sparks_dir):
    payloads = []
    for path in sorted(sparks_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payloads.append(read_json_file(path)["data"])
    return payloads


def resolve_spark_date(payload):
    return payload.get("date-conceived", "").strip() or payload.get("date-recorded", "").strip()


def spark_sort_key(payload, field):
    if field == "date":
        return resolve_spark_date(payload).lower()
    return payload.get(field, "").strip().lower()


def split_tags(value):
    return [tag for tag in value.split() if tag]


def read_text_widget(widget):
    return widget.get("1.0", END).strip()


def short_uuid(value):
    return value[:8]


def today_string():
    return date.today().isoformat()


def browse_for_folder(variable):
    chosen = filedialog.askdirectory()
    if chosen:
        variable.set(chosen)


def open_target(target):
    if not target:
        return
    if target.startswith("http://") or target.startswith("https://"):
        webbrowser.open(target)
        return

    path = Path(target).expanduser()
    if path.exists():
        os.startfile(path)
        return

    messagebox.showerror("spark", f"cannot open:\n{target}")


def open_related_project(project_uuid):
    if not project_uuid:
        return
    try:
        subprocess.Popen(["spark", "new", "--uuid", project_uuid])
    except FileNotFoundError:
        subprocess.Popen([sys.executable, "-m", "sparkcapture", "new", "--uuid", project_uuid])


if __name__ == "__main__":
    main()
