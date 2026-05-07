try:
    from app.desktop import run
except ImportError as exc:
    DESKTOP_IMPORT_ERROR = exc

    def run() -> None:
        raise SystemExit(
            "Не удалось запустить desktop GUI: не найден Tkinter/libtk. "
            "Windows: установите обычный Python с Tkinter. "
            "Linux: установите системный пакет python3-tk."
        ) from DESKTOP_IMPORT_ERROR


if __name__ == "__main__":
    run()
