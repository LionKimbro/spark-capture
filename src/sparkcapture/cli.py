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

g = {
    "active-uuid": None,
    "generated-uuid": None,
    "known-record-rows": [],
    "known-record-section": None,
    "known-record-button": None,
    "conversation-rows": [],
    "related-project-rows": [],
}


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

    app.declare_cmd("", cmd_open_editor)
    app.declare_cmd("setup", cmd_setup)
    app.declare_cmd("delete", cmd_delete)
    app.declare_cmd("list", cmd_list_recent)
    app.declare_cmd("tagged", cmd_tagged)

    app.describe_cmd("", "Open the spark capture form.")
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


def cmd_open_editor():
    sparks_dir = require_sparks_dir()
    existing_data = None
    requested_uuid = app.ctx["uuid"].strip()
    if requested_uuid:
        spark_path = resolve_spark_path(sparks_dir, requested_uuid)
        existing_data = read_json_file(spark_path)["data"]
    run_editor(existing_data, sparks_dir)


def run_editor(existing_data, sparks_dir):
    g["generated-uuid"] = str(uuid.uuid4())
    g["active-uuid"] = existing_data["uuid"] if existing_data else g["generated-uuid"]
    today = today_string()

    root = Tk()
    root.title("spark")
    root.geometry("920x980")

    canvas = create_scroll_canvas(root)
    frame = ttk.Frame(canvas, padding=12)
    window_id = canvas.create_window((0, 0), window=frame, anchor="nw")
    frame.columnconfigure(1, weight=1)

    vars_map = build_variables(existing_data, today)
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

    row = add_known_records_section(frame, row, vars_map["known-records"], today)
    row = add_text_row(frame, row, "synopsis", widgets, existing_data, height=3)
    row = add_text_row(frame, row, "why-it-matters", widgets, existing_data, height=3)
    row = add_text_row(frame, row, "notes", widgets, existing_data, height=4)
    row = add_text_row(frame, row, "related", widgets, existing_data, height=2)
    row = add_repo_path_row(frame, row, "repo-path", vars_map["repo-path"], widgets)
    row += 1
    row = add_folder_path_row(frame, row, "folder-path", vars_map["folder-path"], widgets)
    row += 1
    row = add_conversations_section(frame, row, vars_map["conversations"])
    row = add_related_projects_section(frame, row, vars_map["related-projects"])

    save_button = ttk.Button(
        frame,
        text="Save",
        command=lambda: save_current_spark(root, widgets, vars_map, sparks_dir),
    )
    save_button.grid(row=row, column=0, sticky="w", pady=(14, 18))

    frame.bind("<Configure>", lambda event: sync_canvas(canvas, window_id, event))
    root.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"))
    root.mainloop()


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


def build_variables(existing_data, today):
    data = existing_data or {}
    record_date = data.get("date-recorded", today)

    uuid_value = data.get("uuid", g["active-uuid"])
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


def add_known_records_section(frame, row, records, today):
    ttk.Label(frame, text="known-records").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(10, 4))
    section = ttk.Frame(frame)
    section.grid(row=row, column=1, sticky="ew")
    section.columnconfigure(0, weight=1)
    g["known-record-rows"] = []
    g["known-record-section"] = section
    for record in records:
        add_known_record_row(section, record, today)
    g["known-record-button"] = ttk.Button(
        section,
        text="Add known record",
        command=lambda: add_known_record_row(section, None, today),
    )
    refresh_known_record_button()
    return row + 1


def add_known_record_row(section, record, today):
    index = len(g["known-record-rows"])
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

    g["known-record-rows"].append(
        {
            "date-recorded": date_var,
            "location": location_var,
            "hook": hook_var,
        }
    )
    refresh_known_record_button()


def refresh_known_record_button():
    if g["known-record-button"] is None:
        return
    g["known-record-button"].grid(
        row=len(g["known-record-rows"]),
        column=0,
        sticky="w",
        pady=(6, 0),
    )


def add_conversations_section(frame, row, conversations):
    ttk.Label(frame, text="conversations").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(10, 4))
    section = ttk.Frame(frame)
    section.grid(row=row, column=1, sticky="ew")
    g["conversation-rows"] = []
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
        g["conversation-rows"].append({"url": url_var, "hook": hook_var})
    section.columnconfigure(1, weight=1)
    return row + 1


def add_related_projects_section(frame, row, related_projects):
    ttk.Label(frame, text="related-projects").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=(10, 4))
    section = ttk.Frame(frame)
    section.grid(row=row, column=1, sticky="ew")
    section.columnconfigure(1, weight=1)
    g["related-project-rows"] = []
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
        g["related-project-rows"].append({"project": project_var})
    return row + 1


def save_current_spark(root, widgets, vars_map, sparks_dir):
    try:
        payload = collect_form_data(widgets, vars_map, sparks_dir)
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
    messagebox.showinfo("spark", f"saved:\n{spark_path}")
    root.destroy()


def collect_form_data(widgets, vars_map, sparks_dir):
    spark_uuid = resolve_form_uuid(vars_map["uuid"].get().strip(), sparks_dir)
    data = {
        "uuid": spark_uuid,
        "title": vars_map["title"].get().strip(),
        "tags": split_tags(vars_map["tags"].get()),
        "hook": vars_map["hook"].get().strip(),
        "date-recorded": vars_map["date-recorded"].get().strip(),
        "date-conceived": vars_map["date-conceived"].get().strip(),
        "status": vars_map["status"].get().strip(),
        "graduated-to": vars_map["graduated-to"].get().strip(),
        "known-records": collect_known_records(),
        "synopsis": read_text_widget(widgets["synopsis"]),
        "why-it-matters": read_text_widget(widgets["why-it-matters"]),
        "notes": read_text_widget(widgets["notes"]),
        "related": read_text_widget(widgets["related"]),
        "repo-path": vars_map["repo-path"].get().strip(),
        "folder-path": vars_map["folder-path"].get().strip(),
        "conversations": collect_conversations(),
        "related-projects": collect_related_projects(),
    }
    return data


def collect_known_records():
    records = []
    for record in g["known-record-rows"]:
        item = {
            "date-recorded": record["date-recorded"].get().strip(),
            "location": record["location"].get().strip(),
            "hook": record["hook"].get().strip(),
        }
        if item["date-recorded"] or item["location"] or item["hook"]:
            records.append(item)
    return records


def collect_conversations():
    conversations = []
    for conversation in g["conversation-rows"]:
        item = {
            "url": conversation["url"].get().strip(),
            "hook": conversation["hook"].get().strip(),
        }
        if item["url"] or item["hook"]:
            conversations.append(item)
    return conversations


def collect_related_projects():
    related_projects = []
    for related_project in g["related-project-rows"]:
        project = related_project["project"].get().strip()
        if project:
            related_projects.append({"project": project})
    return related_projects


def resolve_form_uuid(value, sparks_dir):
    if not value:
        raise ValueError("uuid is required")
    if value == g["active-uuid"]:
        return g["active-uuid"]
    if g["active-uuid"] and value != short_uuid(g["active-uuid"]) and g["active-uuid"].startswith(value):
        return g["active-uuid"]
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
        subprocess.Popen(["spark", "--uuid", project_uuid])
    except FileNotFoundError:
        subprocess.Popen([sys.executable, "-m", "sparkcapture", "--uuid", project_uuid])


if __name__ == "__main__":
    main()
