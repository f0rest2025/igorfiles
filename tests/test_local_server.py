import uvicorn

from app.config import AppConfig
from app.local_server import LocalServerRunner, LocalServerState, create_local_app


def test_local_server_disables_uvicorn_logging_config_for_windowed_exe():
    state = LocalServerState(lambda: AppConfig())
    runner = LocalServerRunner(state, "127.0.0.1", 8765)
    app = create_local_app(state)
    config = uvicorn.Config(app, host=runner.host, port=runner.port, access_log=False, log_config=None)

    assert config.log_config is None
