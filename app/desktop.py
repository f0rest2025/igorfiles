from __future__ import annotations

import mimetypes
import threading
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from app.auth import AuthManager, AuthSession
from app.client_page import build_data_upload_url
from app.config import AppConfig, ConfigError, DEFAULT_ENDPOINT, DEFAULT_REGION
from app.object_key import build_object_key, normalize_prefix
from app.secure_config import delete_secure_config, load_secure_config, save_secure_config, secure_config_path
from app.storage import StorageError, YandexStorageClient


class DesktopApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Yandex Object Storage Manager")
        self.geometry("1220x780")
        self.minsize(980, 640)
        self.auth = AuthManager()
        self.session: AuthSession | None = None
        self.config = AppConfig()
        self.objects = []
        self.last_download_url = ""
        self.last_direct_key = ""
        self._configure_style()
        self.show_auth()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#f4f6f8")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f4f6f8", foreground="#172033")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#172033")
        style.configure("TButton", padding=(10, 6))
        style.configure("Primary.TButton", padding=(12, 7))
        style.configure("Danger.TButton", foreground="#b42318")
        style.configure("Treeview", rowheight=28)
        style.configure("Treeview.Heading", font=("TkDefaultFont", 9, "bold"))

    def clear_root(self) -> None:
        for child in self.winfo_children():
            child.destroy()

    def show_auth(self) -> None:
        self.session = None
        self.clear_root()
        if self.auth.has_user():
            LoginFrame(self, self.auth, self.on_authenticated).pack(fill="both", expand=True)
        else:
            SetupFrame(self, self.auth, self.on_authenticated).pack(fill="both", expand=True)

    def on_authenticated(self, session: AuthSession) -> None:
        self.session = session
        try:
            self.config = load_secure_config(session)
        except ConfigError as exc:
            messagebox.showerror("Конфиг", str(exc))
            self.config = AppConfig()
        self.show_main()

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
        status_var.set(str(exc))
        messagebox.showerror("Ошибка", str(exc))


class SetupFrame(ttk.Frame):
    def __init__(self, app: DesktopApp, auth: AuthManager, on_done) -> None:
        super().__init__(app, padding=28)
        self.auth = auth
        self.on_done = on_done
        self.username = tk.StringVar(value="operator")
        self.password = tk.StringVar()
        self.password_repeat = tk.StringVar()
        self.status = tk.StringVar(value="Первый запуск: создайте локального администратора.")
        self._build()

    def _build(self) -> None:
        card = ttk.Frame(self, padding=28, style="Panel.TFrame")
        card.place(relx=0.5, rely=0.5, anchor="center", width=440)
        ttk.Label(card, text="Создание администратора", style="Panel.TLabel", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(card, text="Пароль хранится как PBKDF2-хеш и используется для шифрования Secret Key.", style="Panel.TLabel", wraplength=380).pack(anchor="w", pady=(6, 18))
        _entry(card, "Логин", self.username).pack(fill="x", pady=6)
        _entry(card, "Пароль", self.password, show="*").pack(fill="x", pady=6)
        _entry(card, "Повтор пароля", self.password_repeat, show="*").pack(fill="x", pady=6)
        ttk.Button(card, text="Создать и войти", command=self.create_user, style="Primary.TButton").pack(fill="x", pady=(16, 8))
        ttk.Label(card, textvariable=self.status, style="Panel.TLabel", foreground="#5f6b7a", wraplength=380).pack(anchor="w")

    def create_user(self) -> None:
        if self.password.get() != self.password_repeat.get():
            self.status.set("Пароли не совпадают")
            return
        try:
            session = self.auth.create_user(self.username.get(), self.password.get())
        except ConfigError as exc:
            self.status.set(str(exc))
            return
        self.on_done(session)


class LoginFrame(ttk.Frame):
    def __init__(self, app: DesktopApp, auth: AuthManager, on_done) -> None:
        super().__init__(app, padding=28)
        self.auth = auth
        self.on_done = on_done
        self.username = tk.StringVar(value="operator")
        self.password = tk.StringVar()
        self.status = tk.StringVar(value="Введите пароль оператора.")
        self.failed_attempts = 0
        self._build()

    def _build(self) -> None:
        card = ttk.Frame(self, padding=28, style="Panel.TFrame")
        card.place(relx=0.5, rely=0.5, anchor="center", width=400)
        ttk.Label(card, text="Вход", style="Panel.TLabel", font=("TkDefaultFont", 17, "bold")).pack(anchor="w")
        ttk.Label(card, text="Доступ к настройкам и ключам открыт только после входа.", style="Panel.TLabel", wraplength=340).pack(anchor="w", pady=(6, 18))
        _entry(card, "Логин", self.username).pack(fill="x", pady=6)
        password_box = _entry(card, "Пароль", self.password, show="*")
        password_box.pack(fill="x", pady=6)
        password_box.entry.bind("<Return>", lambda _: self.login())
        self.login_button = ttk.Button(card, text="Войти", command=self.login, style="Primary.TButton")
        self.login_button.pack(fill="x", pady=(16, 8))
        ttk.Label(card, textvariable=self.status, style="Panel.TLabel", foreground="#5f6b7a", wraplength=340).pack(anchor="w")

    def login(self) -> None:
        try:
            session = self.auth.verify(self.username.get(), self.password.get())
        except ConfigError as exc:
            self.failed_attempts += 1
            self.status.set(str(exc))
            if self.failed_attempts >= 5:
                self.login_button.state(["disabled"])
                self.status.set("Слишком много попыток. Повторите через 10 секунд.")
                self.after(10_000, self._unlock)
            return
        self.failed_attempts = 0
        self.on_done(session)

    def _unlock(self) -> None:
        self.failed_attempts = 0
        self.login_button.state(["!disabled"])
        self.status.set("Введите пароль оператора.")


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
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Yandex Object Storage Manager", font=("TkDefaultFont", 17, "bold")).pack(side="left")
        ttk.Label(header, text=f"Конфиг: {secure_config_path()}", foreground="#657286").pack(side="left", padx=18)
        ttk.Button(header, text="Сменить пароль", command=self.change_password).pack(side="right", padx=(8, 0))
        ttk.Button(header, text="Заблокировать", command=self.app.show_auth).pack(side="right")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self._connection_tab()
        self._files_tab()
        self._upload_tab()
        self._download_tab()
        self._direct_tab()

    def _connection_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(tab, text="Подключение")
        form = ttk.Frame(tab)
        form.pack(fill="x")
        self.access_key_id = tk.StringVar()
        self.secret_key = tk.StringVar()
        self.bucket = tk.StringVar()
        self.prefix = tk.StringVar()
        self.endpoint = tk.StringVar(value=DEFAULT_ENDPOINT)
        self.region = tk.StringVar(value=DEFAULT_REGION)

        _grid_entry(form, "Access Key ID", self.access_key_id, 0, 0)
        _grid_entry(form, "Secret Key", self.secret_key, 0, 1, show="*")
        _grid_entry(form, "Bucket", self.bucket, 1, 0)
        _grid_entry(form, "Prefix", self.prefix, 1, 1)
        _grid_entry(form, "Endpoint", self.endpoint, 2, 0)
        _grid_entry(form, "Region", self.region, 2, 1)

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=14)
        ttk.Button(actions, text="Проверить подключение", command=self.test_connection).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Применить", command=self.apply_config).pack(side="left", padx=8)
        ttk.Button(actions, text="Сохранить локально", command=self.save_config).pack(side="left", padx=8)
        ttk.Button(actions, text="Очистить", command=self.clear_config, style="Danger.TButton").pack(side="left", padx=8)
        ttk.Label(tab, textvariable=self.status_connection, foreground="#5f6b7a").pack(anchor="w")

    def _files_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(tab, text="Файлы")
        toolbar = ttk.Frame(tab)
        toolbar.pack(fill="x", pady=(0, 10))
        self.files_prefix = tk.StringVar()
        self.files_search = tk.StringVar()
        self.files_sort = tk.StringVar(value="date-desc")
        _inline_entry(toolbar, "Prefix", self.files_prefix, width=28).pack(side="left", padx=(0, 10))
        search_box = _inline_entry(toolbar, "Поиск", self.files_search, width=28)
        search_box.pack(side="left", padx=10)
        search_box.entry.bind("<KeyRelease>", lambda _: self.render_objects())
        sort_box = ttk.Frame(toolbar)
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
        tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(tab, text="Ссылка на загрузку")
        form = ttk.Frame(tab)
        form.pack(fill="x")
        self.upload_name = tk.StringVar()
        self.upload_prefix = tk.StringVar()
        self.upload_expires = tk.StringVar(value="3600")
        self.upload_content_type = tk.StringVar()
        self.upload_expected_type = tk.StringVar()
        self.upload_guid = tk.BooleanVar(value=True)
        self.upload_sanitize = tk.BooleanVar(value=True)
        _grid_entry(form, "Имя файла / object key", self.upload_name, 0, 0)
        _grid_entry(form, "Prefix", self.upload_prefix, 0, 1)
        _grid_entry(form, "Срок жизни, секунд", self.upload_expires, 1, 0)
        _grid_entry(form, "Content-Type", self.upload_content_type, 1, 1)
        _grid_entry(form, "Ожидаемый тип файла", self.upload_expected_type, 2, 0)
        checks = ttk.Frame(tab)
        checks.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(checks, text="Добавить GUID к имени", variable=self.upload_guid).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(checks, text="Санитизировать имя", variable=self.upload_sanitize).pack(side="left")
        ttk.Button(tab, text="Сгенерировать", command=self.generate_upload_link).pack(anchor="w", pady=12)
        ttk.Label(tab, textvariable=self.status_upload, foreground="#5f6b7a").pack(anchor="w")

        self.upload_object_key = tk.StringVar()
        _inline_entry(tab, "Итоговый object key", self.upload_object_key, width=90).pack(fill="x", pady=(12, 6))
        ttk.Button(tab, text="Копировать object key", command=lambda: self.copy_value(self.upload_object_key.get())).pack(anchor="w")
        ttk.Label(tab, text="Клиентская HTML-ссылка для браузера").pack(anchor="w", pady=(12, 2))
        self.upload_data_url = ScrolledText(tab, height=5, wrap="word")
        self.upload_data_url.pack(fill="x")
        ttk.Button(tab, text="Копировать HTML-ссылку", command=lambda: self.copy_text_widget(self.upload_data_url)).pack(anchor="w", pady=(5, 8))
        ttk.Label(tab, text="Raw pre-signed PUT URL").pack(anchor="w", pady=(4, 2))
        self.upload_url = ScrolledText(tab, height=5, wrap="word")
        self.upload_url.pack(fill="x")
        ttk.Button(tab, text="Копировать raw URL", command=lambda: self.copy_text_widget(self.upload_url)).pack(anchor="w", pady=5)

    def _download_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(tab, text="Ссылка на скачивание")
        form = ttk.Frame(tab)
        form.pack(fill="x")
        self.download_key = tk.StringVar()
        self.download_expires = tk.StringVar(value="3600")
        _grid_entry(form, "Object key", self.download_key, 0, 0)
        _grid_entry(form, "Срок жизни, секунд", self.download_expires, 0, 1)
        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Сгенерировать download-ссылку", command=self.generate_download_link).pack(side="left")
        ttk.Button(actions, text="Открыть", command=self.open_download_url).pack(side="left", padx=10)
        ttk.Label(tab, textvariable=self.status_download, foreground="#5f6b7a").pack(anchor="w")
        self.download_url = ScrolledText(tab, height=7, wrap="word")
        self.download_url.pack(fill="x", pady=(12, 6))
        ttk.Button(tab, text="Копировать ссылку", command=lambda: self.copy_text_widget(self.download_url)).pack(anchor="w")

    def _direct_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(tab, text="Прямая загрузка")
        file_row = ttk.Frame(tab)
        file_row.pack(fill="x")
        _inline_entry(file_row, "Файл", self.file_path, width=70).pack(side="left", fill="x", expand=True)
        ttk.Button(file_row, text="Выбрать файл", command=self.choose_file).pack(side="left", padx=10, pady=(17, 0))
        form = ttk.Frame(tab)
        form.pack(fill="x", pady=10)
        self.direct_prefix = tk.StringVar()
        self.direct_name = tk.StringVar()
        self.direct_guid = tk.BooleanVar(value=False)
        self.direct_sanitize = tk.BooleanVar(value=True)
        _grid_entry(form, "Prefix", self.direct_prefix, 0, 0)
        _grid_entry(form, "Итоговое имя объекта", self.direct_name, 0, 1)
        checks = ttk.Frame(tab)
        checks.pack(fill="x")
        ttk.Checkbutton(checks, text="Добавить GUID к имени", variable=self.direct_guid).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(checks, text="Санитизировать имя", variable=self.direct_sanitize).pack(side="left")
        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Загрузить", command=self.direct_upload).pack(side="left")
        ttk.Button(actions, text="Показать в списке файлов", command=self.show_direct_in_files).pack(side="left", padx=10)
        ttk.Label(tab, textvariable=self.status_direct, foreground="#5f6b7a").pack(anchor="w")
        self.direct_object_key = tk.StringVar()
        _inline_entry(tab, "Object key", self.direct_object_key, width=90).pack(fill="x", pady=(12, 6))
        ttk.Button(tab, text="Копировать object key", command=lambda: self.copy_value(self.direct_object_key.get())).pack(anchor="w")

    def fill_config(self) -> None:
        cfg = self.app.config
        self.access_key_id.set(cfg.access_key_id)
        self.secret_key.set(cfg.secret_key)
        self.bucket.set(cfg.bucket)
        self.prefix.set(cfg.prefix)
        self.endpoint.set(cfg.endpoint or DEFAULT_ENDPOINT)
        self.region.set(cfg.region or DEFAULT_REGION)
        self.files_prefix.set(cfg.prefix)
        self.upload_prefix.set(cfg.prefix)
        self.direct_prefix.set(cfg.prefix)

    def read_config_form(self) -> AppConfig:
        return AppConfig(
            access_key_id=self.access_key_id.get().strip(),
            secret_key=self.secret_key.get(),
            bucket=self.bucket.get().strip(),
            prefix=self.prefix.get().strip(),
            endpoint=self.endpoint.get().strip() or DEFAULT_ENDPOINT,
            region=self.region.get().strip() or DEFAULT_REGION,
        )

    def apply_config(self) -> None:
        self.app.update_config(self.read_config_form(), preserve_blank_secret=True)
        self.fill_config()
        self.status_connection.set("Настройки применены")

    def save_config(self) -> None:
        if self.app.session is None:
            self.status_connection.set("Сессия заблокирована")
            return
        config = self.app.update_config(self.read_config_form(), preserve_blank_secret=True)
        path = save_secure_config(config, self.app.session)
        self.status_connection.set(f"Настройки сохранены: {path}")

    def clear_config(self) -> None:
        if not messagebox.askyesno("Очистить", "Очистить настройки подключения и удалить защищённый desktop-конфиг?"):
            return
        self.app.config = AppConfig()
        delete_secure_config()
        self.fill_config()
        self.status_connection.set("Настройки очищены")

    def test_connection(self) -> None:
        config = self.read_config_form()

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
            raw_url = self.app.storage().presign_upload(object_key, expires, content_type=content_type)
            html_url = build_data_upload_url(raw_url, content_type=content_type, expected_file_type=expected_type)
            return raw_url, html_url

        def done(result) -> None:
            raw_url, html_url = result
            self.upload_object_key.set(object_key)
            _set_text(self.upload_url, raw_url)
            _set_text(self.upload_data_url, html_url)
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
            return self.app.storage().presign_download(object_key, expires)

        def done(url: str) -> None:
            self.app.last_download_url = url
            _set_text(self.download_url, url)
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

        def work():
            with path.open("rb") as fh:
                return self.app.storage().upload_direct(fh, object_key, content_type=content_type)

        def done(result) -> None:
            self.app.last_direct_key = result.object_key
            self.direct_object_key.set(result.object_key)
            self.status_direct.set(f"Файл загружен, размер {_format_size(result.size)}")

        self.app.run_task(self.status_direct, "Загрузка файла...", work, done)

    def show_direct_in_files(self) -> None:
        self.notebook.select(1)
        self.refresh_files()

    def change_password(self) -> None:
        if self.app.session is None:
            return
        password = simpledialog.askstring("Смена пароля", "Новый пароль", show="*", parent=self)
        if not password:
            return
        repeat = simpledialog.askstring("Смена пароля", "Повторите новый пароль", show="*", parent=self)
        if password != repeat:
            messagebox.showerror("Смена пароля", "Пароли не совпадают")
            return
        try:
            new_session = self.app.auth.change_password(self.app.session, password)
            save_secure_config(self.app.config, new_session)
        except ConfigError as exc:
            messagebox.showerror("Смена пароля", str(exc))
            return
        self.app.session = new_session
        messagebox.showinfo("Смена пароля", "Пароль изменён, Secret Key пере-зашифрован.")

    def copy_value(self, value: str) -> None:
        if not value:
            return
        self.clipboard_clear()
        self.clipboard_append(value)

    def copy_text_widget(self, widget: ScrolledText) -> None:
        self.copy_value(widget.get("1.0", "end").strip())


class _EntryGroup(ttk.Frame):
    def __init__(self, parent, label: str, variable: tk.StringVar, show: str | None = None, width: int | None = None) -> None:
        super().__init__(parent)
        ttk.Label(self, text=label).pack(anchor="w")
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


def run() -> None:
    DesktopApp().mainloop()


if __name__ == "__main__":
    run()
