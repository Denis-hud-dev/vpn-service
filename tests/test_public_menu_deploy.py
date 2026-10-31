"""Offline deployment/rollback checks; no SSH, database or systemd mutations."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load():
    path = Path("deploy/telegram-pilot/update_public_menu.py")
    spec = importlib.util.spec_from_file_location("public_menu_deploy", path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_changed_guard_is_rejected_before_import(tmp_path, monkeypatch):
    app = load();monkeypatch.setattr(app, "HERE", tmp_path)
    marker = tmp_path / "unexpected"
    (tmp_path / "protected.py").write_text("open(" + repr(str(marker)) + ",'w').write('unsafe')")
    with pytest.raises(ValueError, match="guard_changed"):
        app.load_guard()
    assert not marker.exists()


def test_payload_rejects_extra_files_and_wrong_revision(tmp_path):
    app = load()
    guard = SimpleNamespace(validate_files=lambda *a: pytest.fail("must reject before reading files"))
    for manifest in ({"approved": "wrong", "files": dict.fromkeys(app.FILES)},
                     {"approved": app.REVISION, "files": dict.fromkeys(app.FILES | {"bot/services/payments.py"})}):
        with pytest.raises(ValueError, match="wrong_payload"):
            app.verify_payload(guard, manifest, tmp_path)


def test_rollback_survives_failed_process_and_preserves_prior_dropin(tmp_path, monkeypatch):
    app = load();backup = tmp_path / "backup";backup.mkdir()
    dropin = tmp_path / "99-public-menu-links.conf"
    previous = tmp_path / "98-purchase-menu.conf"
    previous.write_text("previous security and runtime")
    dropin.write_text("[Service]\nWorkingDirectory=" + str(app.NEW) + "\n")
    (backup / "before.json").write_text(json.dumps({"protected": {"unchanged": True}}))
    monkeypatch.setattr(app, "BACKUP", backup);monkeypatch.setattr(app, "DROPIN", dropin)
    monkeypatch.setattr(app, "environment", lambda *a: pytest.fail("MainPID=0 has no environment"))
    monkeypatch.setattr(app, "snapshot", lambda guard: {"unchanged": True})
    ready = []
    monkeypatch.setattr(app, "wait_ready", lambda env: ready.append(env["HEALTH_PORT"]))
    calls = []
    guard = SimpleNamespace(
        run=lambda args, **kw: calls.append(args),
        unit=lambda name: {"WorkingDirectory": str(app.OLD)},
        compare=lambda old, new, allowed: None if old == new and allowed == app.BOT else pytest.fail("state mismatch"),
        save=lambda path, data: path.write_text(json.dumps(data)))
    app.rollback(guard)
    assert not dropin.exists()
    assert previous.read_text() == "previous security and runtime"
    assert ready == ["18081"]
    assert [c for c in calls if "restart" in c] == [["systemctl", "restart", app.BOT]]
    assert list(backup.glob("rolled-back-*.json"))


def test_rollback_refuses_foreign_override_without_restarting(tmp_path, monkeypatch):
    app = load();backup = tmp_path / "backup";backup.mkdir()
    (backup / "before.json").write_text(json.dumps({"protected": {}}))
    dropin = tmp_path / "99-public-menu-links.conf";dropin.write_text("foreign configuration")
    monkeypatch.setattr(app, "BACKUP", backup);monkeypatch.setattr(app, "DROPIN", dropin)
    guard = SimpleNamespace(run=lambda *a, **kw: pytest.fail("must not restart"))
    with pytest.raises(ValueError, match="rollback_override_changed"):
        app.rollback(guard)
    assert dropin.read_text() == "foreign configuration"


def test_preflight_probe_runs_as_existing_test_user_without_startup(monkeypatch):
    app = load();calls = []
    def capture(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(app.subprocess, "run", capture)
    env = {"HEALTH_PORT": "18081"}
    app.probe(env)
    args, options = calls[0]
    assert args[:4] == ["runuser", "-u", "kudavpn_trial_bot", "--"]
    assert options["env"] == env and options["cwd"] == app.NEW and options["capture_output"]
    code = args[-1]
    assert "nav:channel" in code and "nav:community" in code and "callback_data is None" in code
    assert "main()" not in code and "start_polling" not in code
