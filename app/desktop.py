from __future__ import annotations

import mimetypes
import threading
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.client_page import build_data_upload_url
from app.config import AppConfig, AuthMode, ConfigError, DEFAULT_ENDPOINT, DEFAULT_REGION, REGION_ENDPOINTS, auth_mode_label, endpoint_for_region
from app.diagnostics import get_logger, log_path, setup_logging
from app.local_server import LocalServerRunner, LocalServerState
from app.object_key import build_object_key, normalize_prefix
from app.operator_config import delete_operator_config, load_operator_config, operator_config_path, save_operator_config
from app.storage import StorageError, YandexStorageClient


logger = get_logger(__name__)


class DesktopApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Yandex Object Storage Manager")
        self.geometry("1220x780")
        self.minsize(980, 640)
        self.config = AppConfig()
        self.objects = []
        self.last_download_url = ""
        self.last_direct_key = ""
        self.local_state = LocalServerState(lambda: self.config)
        self.local_server: LocalServerRunner | None = None
        self._configure_style()
        self.load_initial_config()
        self.restart_local_server()
        self.show_main()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(background="#ffffff")
        style.configure("TFrame", background="#ffffff")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Toolbar.TFrame", background="#ffffff")
        style.configure("TLabel", background="#ffffff", foreground="#1f2937")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#1f2937")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#667085")
        style.configure("TButton", padding=(12, 7), borderwidth=0)
        style.map("TButton", background=[("active", "#e8eef8")])
        style.configure("Primary.TButton", padding=(14, 8), foreground="#ffffff", background="#2563eb")
        style.map("Primary.TButton", background=[("active", "#1d4ed8"), ("disabled", "#93a4c7")])
        style.configure("Ghost.TButton", padding=(12, 7), foreground="#344054", background="#ffffff")
        style.configure("Danger.TButton", foreground="#b42318", background="#ffffff")
        style.configure("TNotebook", background="#ffffff", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 9), background="#edf1f7", foreground="#475467")
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#111827")])
        style.configure("Treeview", rowheight=30, background="#ffffff", fieldbackground="#ffffff", borderwidth=0)
        style.configure("Treeview.Heading", font=("TkDefaultFont", 9, "bold"), background="#f2f4f7", foreground="#344054")

    def clear_root(self) -> None:
        for child in self.winfo_children():
            child.destroy()

    def load_initial_config(self) -> None:
        try:
            self.config = load_operator_config()
        except ConfigError as exc:
            messagebox.showerror("Конфиг", str(exc))
            self.config = AppConfig()
        setup_logging(self.config.debug)
        logger.info("operator config loaded mode=%s", self.config.auth_mode)

    def restart_local_server(self) -> None:
        if self.local_server:
            self.local_server.stop()
        self.local_server = LocalServerRunner(self.local_state, self.config.upload_server_bind_host, self.config.upload_server_port)
        self.local_server.start()

    def show_main(self) -> None:
        self.clear_root()
        MainFrame(self).pack(fill="both", expand=True)

    def storage(self) -> YandexStorageClient:
        return YandexStorageClient(self.config)

    def update_config(self, update: AppConfig, preserve_blank_secret: bool = True) -> AppConfig:
        self.config = self.config.merged_with(update, preserve_blank_secret=preserve_blank_secret)
        return self.config

    def run_task(self, status_var: tk.StringVar, message: str, func, on_success) -> None:
        status_var.set(message)

        def worker() -> None:
            try:
                result = func()
            except (ConfigError, StorageError, OSError, ValueError) as exc:
                self.after(0, lambda: self._task_error(status_var, exc))
            except Exception as exc:  # pragma: no cover - GUI safety net
                self.after(0, lambda: self._task_error(status_var, exc))
            else:
                self.after(0, lambda: on_success(result))

        threading.Thread(target=worker, daemon=True).start()

    def _task_error(self, status_var: tk.StringVar, exc: Exception) -> None:
        message = _error_text(exc)
        status_var.set(message)
        messagebox.showerror("Ошибка", message)


class MainFrame(ttk.Frame):
    def __init__(self, app: DesktopApp) -> None:
        super().__init__(app, padding=14)
        self.app = app
        self.status_connection = tk.StringVar()
        self.status_files = tk.StringVar()
        self.status_upload = tk.StringVar()
        self.status_download = tk.StringVar()
        self.status_direct = tk.StringVar()
        self.file_path = tk.StringVar()
        self._build()
        self.fill_config()

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 14))
        title_block = ttk.Frame(header)
        title_block.pack(side="left", fill="x", expand=True)
        ttk.Label(title_block, text="Yandex Object Storage Manager", font=("TkDefaultFont", 18, "bold")).pack(anchor="w")
        ttk.Label(
            title_block,
            text=f"Локальное desktop-приложение без входа. Конфиг: {operator_config_path()}",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        ttk.Button(header, text="Открыть логи", command=self.open_logs, style="Ghost.TButton").pack(side="right")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self._connection_tab()
        self._files_tab()
        self._upload_tab()
        self._download_tab()
        self._direct_tab()

    def _connection_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=20, style="Panel.TFrame")
        self.notebook.add(tab, text="Подключение")
        form = ttk.Frame(tab, style="Panel.TFrame")
        form.pack(fill="x")
        self.auth_mode = tk.StringVar(value=AuthMode.YC_CLI.value)
        self.access_key_id = tk.StringVar()
        self.secret_key = tk.StringVar()
        self.bucket = tk.StringVar()
        self.prefix = tk.StringVar()
        self.endpoint = tk.StringVar(value=DEFAULT_ENDPOINT)
        self.region = tk.StringVar(value=DEFAULT_REGION)
        self.yc_profile = tk.StringVar()
        self.service_account_key_path = tk.StringVar()
        self.upload_server_bind_host = tk.StringVar(value="127.0.0.1")
        self.upload_server_port = tk.StringVar(value="8765")
        self.public_base_url = tk.StringVar(value="http://127.0.0.1:8765")
        self.debug = tk.BooleanVar(value=False)

        auth_group = ttk.Frame(form, style="Panel.TFrame")
        auth_group.grid(row=0, column=0, sticky="ew", padx=8, pady=7)
        ttk.Label(auth_group, text="Способ аутентификации").pack(anchor="w")
        ttk.Combobox(
            auth_group,
            textvariable=self.auth_mode,
            state="readonly",
            values=[AuthMode.YC_CLI.value, AuthMode.SERVICE_ACCOUNT_JSON.value, AuthMode.LEGACY_STATIC.value],
        ).pack(fill="x")
        self.auth_mode.trace_add("write", lambda *_: self.update_auth_hint())

        region_group = ttk.Frame(form, style="Panel.TFrame")
        region_group.grid(row=0, column=1, sticky="ew", padx=8, pady=7)
        ttk.Label(region_group, text="Region").pack(anchor="w")
        ttk.Combobox(region_group, textvariable=self.region, values=list(REGION_ENDPOINTS), state="readonly").pack(fill="x")
        self.region.trace_add("write", lambda *_: self.apply_region_endpoint())

        _grid_entry(form, "Bucket", self.bucket, 1, 0)
        _grid_entry(form, "Prefix", self.prefix, 1, 1)
        _grid_entry(form, "Endpoint", self.endpoint, 2, 0)
        _grid_entry(form, "Yandex CLI profile", self.yc_profile, 2, 1)
        _grid_entry(form, "Service account JSON path", self.service_account_key_path, 3, 0)
        ttk.Button(form, text="Выбрать JSON", command=self.choose_service_account_json).grid(row=3, column=1, sticky="w", padx=8, pady=(25, 7))
        _grid_entry(form, "Legacy Access Key ID", self.access_key_id, 4, 0)
        _grid_entry(form, "Legacy Secret Key", self.secret_key, 4, 1, show="*")
        _grid_entry(form, "Upload server bind host", self.upload_server_bind_host, 5, 0)
        _grid_entry(form, "Upload server port", self.upload_server_port, 5, 1)
        _grid_entry(form, "Public base URL для client links", self.public_base_url, 6, 0)
        ttk.Checkbutton(form, text="Debug logs", variable=self.debug).grid(row=6, column=1, sticky="w", padx=8, pady=(25, 7))

        actions = ttk.Frame(tab, style="Panel.TFrame")
        actions.pack(fill="x", pady=14)
        ttk.Button(actions, text="Проверить подключение", command=self.test_connection).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Применить", command=self.apply_config).pack(side="left", padx=8)
        ttk.Button(actions, text="Сохранить локально", command=self.save_config).pack(side="left", padx=8)
        ttk.Button(actions, text="Очистить", command=self.clear_config, style="Danger.TButton").pack(side="left", padx=8)
        ttk.Label(tab, text=f"Логи: {log_path()}", foreground="#657286").pack(anchor="w")
        self.auth_hint = tk.StringVar()
        ttk.Label(tab, textvariable=self.auth_hint, foreground="#5f6b7a", wraplength=900).pack(anchor="w", pady=(8, 0))
        ttk.Label(tab, textvariable=self.status_connection, foreground="#5f6b7a").pack(anchor="w")

    def _files_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=20, style="Panel.TFrame")
        self.notebook.add(tab, text="Файлы")
        toolbar = ttk.Frame(tab, style="Panel.TFrame")
        toolbar.pack(fill="x", pady=(0, 10))
        self.files_prefix = tk.StringVar()
        self.files_search = tk.StringVar()
        self.files_sort = tk.StringVar(value="date-desc")
        _inline_entry(toolbar, "Prefix", self.files_prefix, width=28).pack(side="left", padx=(0, 10))
        search_box = _inline_entry(toolbar, "Поиск", self.files_search, width=28)
        search_box.pack(side="left", padx=10)
        search_box.entry.bind("<KeyRelease>", lambda _: self.render_objects())
        sort_box = ttk.Frame(toolbar, style="Panel.TFrame")
        sort_box.pack(side="left", padx=10)
        ttk.Label(sort_box, text="Сортировка").pack(anchor="w")
        ttk.Combobox(
            sort_box,
            textvariable=self.files_sort,
            state="readonly",
            values=["date-desc", "date-asc", "name-asc", "name-desc", "size-desc", "size-asc"],
            width=18,
        ).pack()
        ttk.Button(toolbar, text="Обновить", command=self.refresh_files).pack(side="left", padx=10, pady=(17, 0))
        ttk.Button(toolbar, text="Копировать key", command=self.copy_selected_key).pack(side="left", padx=4, pady=(17, 0))
        ttk.Button(toolbar, text="Download URL", command=self.download_selected_key).pack(side="left", padx=4, pady=(17, 0))

        columns = ("key", "size", "modified", "storage_class", "etag")
        self.files_tree = ttk.Treeview(tab, columns=columns, show="headings", height=17)
        for key, title, width in [
            ("key", "Object key", 420),
            ("size", "Размер", 110),
            ("modified", "Дата изменения", 170),
            ("storage_class", "Storage class", 130),
            ("etag", "ETag", 220),
        ]:
            self.files_tree.heading(key, text=title)
            self.files_tree.column(key, width=width, anchor="w")
        self.files_tree.pack(fill="both", expand=True)
        self.files_sort.trace_add("write", lambda *_: self.render_objects())
        ttk.Label(tab, textvariable=self.status_files, foreground="#5f6b7a").pack(anchor="w", pady=(8, 0))

    def _upload_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=20, style="Panel.TFrame")
        self.notebook.add(tab, text="Ссылка на загрузку")
        form = ttk.Frame(tab, style="Panel.TFrame")
        form.pack(fill="x")
        self.upload_name = tk.StringVar()
        self.upload_prefix = tk.StringVar()
        self.upload_expires = tk.StringVar(value="3600")
        self.upload_content_type = tk.StringVar()
        self.upload_expected_type = tk.StringVar()
        self.upload_max_size_mb = tk.StringVar(value="100")
        self.upload_guid = tk.BooleanVar(value=True)
        self.upload_sanitize = tk.BooleanVar(value=True)
        _grid_entry(form, "Имя файла / object key", self.upload_name, 0, 0)
        _grid_entry(form, "Prefix", self.upload_prefix, 0, 1)
        _grid_entry(form, "Срок жизни, секунд", self.upload_expires, 1, 0)
        _grid_entry(form, "Content-Type", self.upload_content_type, 1, 1)
        _grid_entry(form, "Ожидаемый тип файла", self.upload_expected_type, 2, 0)
        _grid_entry(form, "Максимальный размер, МБ", self.upload_max_size_mb, 2, 1)
        checks = ttk.Frame(tab, style="Panel.TFrame")
        checks.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(checks, text="Добавить GUID к имени", variable=self.upload_guid).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(checks, text="Санитизировать имя", variable=self.upload_sanitize).pack(side="left")
        ttk.Button(tab, text="Сгенерировать", command=self.generate_upload_link).pack(anchor="w", pady=12)
        ttk.Label(tab, textvariable=self.status_upload, foreground="#5f6b7a").pack(anchor="w")

        self.upload_object_key = tk.StringVar()
        _inline_entry(tab, "Итоговый object key", self.upload_object_key, width=90).pack(fill="x", pady=(12, 6))
        ttk.Button(tab, text="Копировать object key", command=lambda: self.copy_value(self.upload_object_key.get())).pack(anchor="w")
        ttk.Label(tab, text="Client upload link").pack(anchor="w", pady=(12, 2))
        self.upload_client_url = ScrolledText(tab, height=3, wrap="word")
        self.upload_client_url.pack(fill="x")
        ttk.Button(tab, text="Копировать client link", command=lambda: self.copy_text_widget(self.upload_client_url)).pack(anchor="w", pady=(5, 8))
        ttk.Label(tab, text="Legacy raw pre-signed PUT URL").pack(anchor="w", pady=(4, 2))
        self.upload_url = ScrolledText(tab, height=5, wrap="word")
        self.upload_url.pack(fill="x")
        ttk.Button(tab, text="Копировать legacy raw URL", command=lambda: self.copy_text_widget(self.upload_url)).pack(anchor="w", pady=5)

    def _download_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=20, style="Panel.TFrame")
        self.notebook.add(tab, text="Ссылка на скачивание")
        form = ttk.Frame(tab, style="Panel.TFrame")
        form.pack(fill="x")
        self.download_key = tk.StringVar()
        self.download_expires = tk.StringVar(value="3600")
        _grid_entry(form, "Object key", self.download_key, 0, 0)
        _grid_entry(form, "Срок жизни, секунд", self.download_expires, 0, 1)
        actions = ttk.Frame(tab, style="Panel.TFrame")
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Сгенерировать download-ссылку", command=self.generate_download_link).pack(side="left")
        ttk.Button(actions, text="Открыть", command=self.open_download_url).pack(side="left", padx=10)
        ttk.Label(tab, textvariable=self.status_download, foreground="#5f6b7a").pack(anchor="w")
        self.download_url = ScrolledText(tab, height=7, wrap="word")
        self.download_url.pack(fill="x", pady=(12, 6))
        ttk.Button(tab, text="Копировать ссылку", command=lambda: self.copy_text_widget(self.download_url)).pack(anchor="w")

    def _direct_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=20, style="Panel.TFrame")
        self.notebook.add(tab, text="Прямая загрузка")
        file_row = ttk.Frame(tab, style="Panel.TFrame")
        file_row.pack(fill="x")
        _inline_entry(file_row, "Файл", self.file_path, width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(file_row, text="Выбрать файл", command=self.choose_file).pack(side="left", padx=10, pady=(17, 0))
        form = ttk.Frame(tab, style="Panel.TFrame")
        form.pack(fill="x", pady=10)
        self.direct_prefix = tk.StringVar()
        self.direct_name = tk.StringVar()
        self.direct_guid = tk.BooleanVar(value=False)
        self.direct_sanitize = tk.BooleanVar(value=True)
        _grid_entry(form, "Prefix", self.direct_prefix, 0, 0)
        _grid_entry(form, "Итоговое имя объекта", self.direct_name, 0, 1)
        checks = ttk.Frame(tab, style="Panel.TFrame")
        checks.pack(fill="x")
        ttk.Checkbutton(checks, text="Добавить GUID к имени", variable=self.direct_guid).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(checks, text="Санитизировать имя", variable=self.direct_sanitize).pack(side="left")
        actions = ttk.Frame(tab, style="Panel.TFrame")
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Загрузить", command=self.direct_upload).pack(side="left")
        ttk.Button(actions, text="Показать в списке файлов", command=self.show_direct_in_files).pack(side="left", padx=10)
        ttk.Label(tab, textvariable=self.status_direct, foreground="#5f6b7a").pack(anchor="w")
        self.direct_progress = ttk.Progressbar(tab, maximum=100, mode="determinate")
        self.direct_progress.pack(fill="x", pady=(8, 0))
        self.direct_object_key = tk.StringVar()
        _inline_entry(tab, "Object key", self.direct_object_key, width=90).pack(fill="x", pady=(12, 6))
        ttk.Button(tab, text="Копировать object key", command=lambda: self.copy_value(self.direct_object_key.get())).pack(anchor="w")

    def fill_config(self) -> None:
        cfg = self.app.config
        self.auth_mode.set(cfg.auth_mode)
        self.access_key_id.set(cfg.access_key_id)
        self.secret_key.set(cfg.secret_key)
        self.bucket.set(cfg.bucket)
        self.prefix.set(cfg.prefix)
        self.region.set(cfg.region or DEFAULT_REGION)
        self.endpoint.set(cfg.endpoint or DEFAULT_ENDPOINT)
        self.yc_profile.set(cfg.yc_profile)
        self.service_account_key_path.set(cfg.service_account_key_path)
        self.upload_server_bind_host.set(cfg.upload_server_bind_host)
        self.upload_server_port.set(str(cfg.upload_server_port))
        self.public_base_url.set(cfg.public_base_url)
        self.debug.set(cfg.debug)
        self.files_prefix.set(cfg.prefix)
        self.upload_prefix.set(cfg.prefix)
        self.direct_prefix.set(cfg.prefix)
        self.update_auth_hint()

    def read_config_form(self) -> AppConfig:
        port = _read_port(self.upload_server_port.get())
        return AppConfig(
            access_key_id=self.access_key_id.get().strip(),
            secret_key=self.secret_key.get(),
            bucket=self.bucket.get().strip(),
            prefix=self.prefix.get().strip(),
            endpoint=self.endpoint.get().strip() or DEFAULT_ENDPOINT,
            region=self.region.get().strip() or DEFAULT_REGION,
            auth_mode=self.auth_mode.get().strip() or AuthMode.YC_CLI.value,
            yc_profile=self.yc_profile.get().strip(),
            service_account_key_path=self.service_account_key_path.get().strip(),
            upload_server_bind_host=self.upload_server_bind_host.get().strip() or "127.0.0.1",
            upload_server_port=port,
            public_base_url=self.public_base_url.get().strip() or f"http://127.0.0.1:{port}",
            debug=self.debug.get(),
        )

    def choose_service_account_json(self) -> None:
        path = filedialog.askopenfilename(title="Service account authorized key JSON", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.service_account_key_path.set(path)

    def apply_region_endpoint(self) -> None:
        self.endpoint.set(endpoint_for_region(self.region.get()))

    def update_auth_hint(self) -> None:
        mode = self.auth_mode.get()
        if mode == AuthMode.YC_CLI.value:
            text = "Основной режим: приложение получает IAM token через локальный Yandex Cloud CLI profile. Static S3 keys не нужны."
        elif mode == AuthMode.SERVICE_ACCOUNT_JSON.value:
            text = "Service account JSON используется только для получения IAM token через JWT exchange. Клиенту JSON и token не передаются."
        else:
            text = "Legacy static access key mode: оставлен только для совместимости. Он использует S3 signing/presigned URLs и менее надёжен."
        self.auth_hint.set(f"{auth_mode_label(mode)}. {text}")

    def apply_config(self) -> None:
        try:
            config = self.read_config_form()
        except ValueError as exc:
            self.status_connection.set(str(exc))
            return
        self.app.update_config(config, preserve_blank_secret=True)
        setup_logging(self.app.config.debug)
        self.app.restart_local_server()
        self.fill_config()
        self.status_connection.set("Настройки применены")

    def save_config(self) -> None:
        try:
            config = self.app.update_config(self.read_config_form(), preserve_blank_secret=True)
        except ValueError as exc:
            self.status_connection.set(str(exc))
            return
        path = save_operator_config(config)
        setup_logging(self.app.config.debug)
        self.app.restart_local_server()
        note = " Legacy Secret Key не сохраняется." if config.auth_mode == AuthMode.LEGACY_STATIC.value else ""
        self.status_connection.set(f"Настройки сохранены: {path}.{note}")

    def clear_config(self) -> None:
        if not messagebox.askyesno("Очистить", "Очистить настройки подключения и удалить desktop-конфиг?"):
            return
        self.app.config = AppConfig()
        delete_operator_config()
        setup_logging(self.app.config.debug)
        self.app.restart_local_server()
        self.fill_config()
        self.status_connection.set("Настройки очищены")

    def test_connection(self) -> None:
        try:
            config = self.read_config_form()
        except ValueError as exc:
            self.status_connection.set(str(exc))
            return

        def work() -> str:
            YandexStorageClient(self.app.config.merged_with(config, preserve_blank_secret=True)).test_connection()
            return "Подключение к bucket успешно проверено"

        def done(message: str) -> None:
            self.app.update_config(config, preserve_blank_secret=True)
            self.status_connection.set(message)

        self.app.run_task(self.status_connection, "Проверка подключения...", work, done)

    def refresh_files(self) -> None:
        prefix = normalize_prefix(self.files_prefix.get() or self.app.config.prefix)

        def work():
            return self.app.storage().list_objects(prefix)

        def done(objects) -> None:
            self.app.objects = objects
            self.render_objects()
            self.status_files.set(f"Объектов: {len(objects)}")

        self.app.run_task(self.status_files, "Загрузка списка объектов...", work, done)

    def render_objects(self) -> None:
        for item in self.files_tree.get_children():
            self.files_tree.delete(item)
        objects = self._filtered_sorted_objects()
        for index, obj in enumerate(objects):
            modified = obj.last_modified.isoformat(sep=" ", timespec="seconds") if obj.last_modified else ""
            self.files_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(obj.key, _format_size(obj.size), modified, obj.storage_class, obj.etag),
            )

    def _filtered_sorted_objects(self):
        search = self.files_search.get().strip().lower()
        objects = [obj for obj in self.app.objects if not search or search in obj.key.lower()]
        sort = self.files_sort.get()
        if sort == "date-asc":
            objects.sort(key=lambda obj: _sort_datetime(obj.last_modified))
        elif sort == "date-desc":
            objects.sort(key=lambda obj: _sort_datetime(obj.last_modified), reverse=True)
        elif sort == "name-desc":
            objects.sort(key=lambda obj: obj.key, reverse=True)
        elif sort == "size-asc":
            objects.sort(key=lambda obj: obj.size)
        elif sort == "size-desc":
            objects.sort(key=lambda obj: obj.size, reverse=True)
        else:
            objects.sort(key=lambda obj: obj.key)
        return objects

    def selected_key(self) -> str:
        selected = self.files_tree.selection()
        if not selected:
            raise ConfigError("Выберите объект в списке")
        values = self.files_tree.item(selected[0], "values")
        return str(values[0])

    def copy_selected_key(self) -> None:
        try:
            self.copy_value(self.selected_key())
        except ConfigError as exc:
            self.status_files.set(str(exc))

    def download_selected_key(self) -> None:
        try:
            self.download_key.set(self.selected_key())
            self.notebook.select(3)
        except ConfigError as exc:
            self.status_files.set(str(exc))

    def generate_upload_link(self) -> None:
        name = self.upload_name.get().strip()
        if not name:
            self.status_upload.set("Введите имя файла или object key")
            return
        try:
            expires = _read_int(self.upload_expires.get(), "Срок жизни")
            max_size_bytes = _read_size_mb(self.upload_max_size_mb.get())
        except ValueError as exc:
            self.status_upload.set(str(exc))
            return
        content_type = (self.upload_content_type.get().strip() or self.upload_expected_type.get().strip())
        expected_type = self.upload_expected_type.get().strip()
        object_key = build_object_key(
            name,
            self.upload_prefix.get().strip() or self.app.config.prefix,
            add_guid=self.upload_guid.get(),
            sanitize=self.upload_sanitize.get(),
        )

        def work():
            legacy_url = ""
            legacy_html = ""
            if self.app.config.uses_legacy_static_keys:
                legacy_url = self.app.storage().presign_upload(object_key, expires, content_type=content_type)
                legacy_html = build_data_upload_url(legacy_url, content_type=content_type, expected_file_type=expected_type)
            return legacy_url, legacy_html

        def done(result) -> None:
            raw_url, html_url = result
            token = self.app.local_state.uploads.create(
                object_key,
                expires,
                content_type=content_type,
                expected_file_type=expected_type,
                max_size_bytes=max_size_bytes,
            )
            logger.info("upload token generation key=%s ttl=%s max_size=%s", object_key, expires, max_size_bytes)
            client_url = f"{self.app.config.public_base_url.rstrip('/')}/upload/{token.token}"
            self.upload_object_key.set(object_key)
            _set_text(self.upload_client_url, client_url)
            legacy_text = raw_url or "Недоступно в IAM mode. Основной сценарий: backend-mediated upload link выше."
            if html_url:
                legacy_text += "\n\nLegacy HTML data URL:\n" + html_url
            _set_text(self.upload_url, legacy_text)
            expires_at = datetime.now(UTC) + timedelta(seconds=expires)
            self.status_upload.set(f"Ссылка действует до {expires_at:%Y-%m-%d %H:%M:%S UTC}")

        self.app.run_task(self.status_upload, "Генерация upload-ссылки...", work, done)

    def generate_download_link(self) -> None:
        object_key = self.download_key.get().strip()
        if not object_key:
            self.status_download.set("Введите object key")
            return
        try:
            expires = _read_int(self.download_expires.get(), "Срок жизни")
        except ValueError as exc:
            self.status_download.set(str(exc))
            return

        def work():
            legacy_url = ""
            if self.app.config.uses_legacy_static_keys:
                legacy_url = self.app.storage().presign_download(object_key, expires)
            return legacy_url

        def done(legacy_url: str) -> None:
            token = self.app.local_state.downloads.create(object_key, expires)
            logger.info("download link generation key=%s ttl=%s", object_key, expires)
            url = f"{self.app.config.public_base_url.rstrip('/')}/download/{token.token}"
            self.app.last_download_url = url
            text = url
            if legacy_url:
                text += "\n\nLegacy presigned GET URL:\n" + legacy_url
            _set_text(self.download_url, text)
            expires_at = datetime.now(UTC) + timedelta(seconds=expires)
            self.status_download.set(f"Ссылка действует до {expires_at:%Y-%m-%d %H:%M:%S UTC}")

        self.app.run_task(self.status_download, "Генерация download-ссылки...", work, done)

    def open_download_url(self) -> None:
        if self.app.last_download_url:
            webbrowser.open(self.app.last_download_url)

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(title="Выберите файл")
        if path:
            self.file_path.set(path)
            if not self.direct_name.get():
                self.direct_name.set(Path(path).name)

    def direct_upload(self) -> None:
        path = Path(self.file_path.get())
        if not path.exists() or not path.is_file():
            self.status_direct.set("Выберите файл")
            return
        object_name = self.direct_name.get().strip() or path.name
        object_key = build_object_key(
            object_name,
            self.direct_prefix.get().strip() or self.app.config.prefix,
            add_guid=self.direct_guid.get(),
            sanitize=self.direct_sanitize.get(),
        )
        content_type = mimetypes.guess_type(path.name)[0] or ""
        total_size = path.stat().st_size
        self.direct_progress.configure(value=0)

        def progress(done: int, total: int | None) -> None:
            total = total or total_size

            def update() -> None:
                percent = min(100, int((done / total) * 100)) if total else 0
                self.direct_progress.configure(value=percent)
                self.status_direct.set(f"Загрузка в Object Storage: {percent}% ({_format_size(done)} / {_format_size(total)})")

            self.app.after(0, update)

        def work():
            return self.app.storage().upload_file(path, object_key, content_type=content_type, progress_callback=progress)

        def done(result) -> None:
            self.direct_progress.configure(value=100)
            self.app.last_direct_key = result.object_key
            self.direct_object_key.set(result.object_key)
            self.status_direct.set(f"Файл загружен, размер {_format_size(result.size)}")

        self.app.run_task(self.status_direct, "Загрузка файла...", work, done)

    def show_direct_in_files(self) -> None:
        self.notebook.select(1)
        self.refresh_files()

    def open_logs(self) -> None:
        webbrowser.open(str(log_path()))

    def copy_value(self, value: str) -> None:
        if not value:
            return
        self.clipboard_clear()
        self.clipboard_append(value)

    def copy_text_widget(self, widget: ScrolledText) -> None:
        self.copy_value(widget.get("1.0", "end").strip())


class _EntryGroup(ttk.Frame):
    def __init__(self, parent, label: str, variable: tk.StringVar, show: str | None = None, width: int | None = None) -> None:
        super().__init__(parent, style="Panel.TFrame")
        ttk.Label(self, text=label, style="Panel.TLabel").pack(anchor="w")
        self.entry = ttk.Entry(self, textvariable=variable, show=show or "", width=width)
        self.entry.pack(fill="x")


def _entry(parent, label: str, variable: tk.StringVar, show: str | None = None, width: int | None = None) -> _EntryGroup:
    return _EntryGroup(parent, label, variable, show=show, width=width)


def _inline_entry(parent, label: str, variable: tk.StringVar, width: int | None = None) -> _EntryGroup:
    return _EntryGroup(parent, label, variable, width=width)


def _grid_entry(parent, label: str, variable: tk.StringVar, row: int, column: int, show: str | None = None) -> _EntryGroup:
    group = _EntryGroup(parent, label, variable, show=show)
    group.grid(row=row, column=column, sticky="ew", padx=8, pady=7)
    parent.columnconfigure(column, weight=1)
    return group


def _set_text(widget: ScrolledText, value: str) -> None:
    widget.delete("1.0", "end")
    widget.insert("1.0", value)


def _read_int(value: str, label: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{label}: введите целое число") from exc
    if number < 60 or number > 604800:
        raise ValueError(f"{label}: допустимо от 60 до 604800 секунд")
    return number


def _read_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("Upload server port: введите целое число") from exc
    if port < 1 or port > 65535:
        raise ValueError("Upload server port: допустимо от 1 до 65535")
    return port


def _read_size_mb(value: str) -> int:
    try:
        mb = float((value or "0").replace(",", "."))
    except ValueError as exc:
        raise ValueError("Максимальный размер: введите число в МБ") from exc
    if mb < 0:
        raise ValueError("Максимальный размер не может быть отрицательным")
    return int(mb * 1024 * 1024)


def _format_size(size: int) -> str:
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    value = float(size)
    unit = 0
    while value >= 1024 and unit < len(units) - 1:
        value /= 1024
        unit += 1
    return f"{value:.0f} {units[unit]}" if unit == 0 else f"{value:.1f} {units[unit]}"


def _sort_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _error_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or f"{exc.__class__.__name__}: ошибка без подробного сообщения"


def run() -> None:
    DesktopApp().mainloop()


if __name__ == "__main__":
    run()
