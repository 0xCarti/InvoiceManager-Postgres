import importlib
import runpy
import sys
from types import SimpleNamespace


def test_run_import_sets_debug(monkeypatch):
    def fake_create_app(argv):
        return SimpleNamespace(debug=False), "socket"

    monkeypatch.setattr("app.create_app", fake_create_app)
    monkeypatch.setenv("DEBUG", "True")
    run = importlib.reload(importlib.import_module("run"))
    try:
        assert run.app.debug is True
        assert run.socketio == "socket"
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("run"))


def test_run_main_executes_server(monkeypatch):
    class FakeSocketIO:
        def __init__(self):
            self.called_with = None

        def run(self, app, host, port, debug):
            self.called_with = (app, host, port, debug)

    sock = FakeSocketIO()

    def fake_create_app(argv):
        return SimpleNamespace(debug=False), sock

    monkeypatch.setattr("app.create_app", fake_create_app)
    monkeypatch.setenv("PORT", "6000")
    runpy.run_module("run", run_name="__main__")
    try:
        assert sock.called_with[0] is not None
        assert sock.called_with[1] == "0.0.0.0"
        assert sock.called_with[2] == 6000
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("run"))
