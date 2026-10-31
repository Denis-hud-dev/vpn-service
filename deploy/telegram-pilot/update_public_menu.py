"""Pinned TEST-only public-menu deployment. No migrations or shared service restarts."""
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

REVISION = "ab72c76b9e4cda3d9015d8e4aa9d89e426a442eb"
GUARD_SHA = "375d1dfa563fa187f663ab5ecb33aefd08aed3c8c6a93ba4f62565c32baf448a"
BOT = "kudavpn-telegram-pilot-bot.service"
OLD = Path("/opt/kudavpn-telegram-pilot/app-purchase-menu-707e130cec22")
NEW = OLD.with_name("app-menu-links-ab72c76b9e4c")
BACKUP = Path("/var/backups/kudavpn-telegram-pilot/menu-ab72c76b9e4c")
DROPIN = Path("/etc/systemd/system") / (BOT + ".d/99-public-menu-links.conf")
FILES = {"bot/config.py", "bot/handlers/start.py", "bot/keyboards/main.py", "bot/main.py",
         "bot/menu_links.py", "bot/navigation.py", "bot/premium_emoji.py",
         "bot/screens.py", "bot/services/__init__.py"}
HERE = Path(__file__).resolve().parent


def load_guard():
    path = HERE / "protected.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != GUARD_SHA:
        raise ValueError("guard_changed")
    spec = importlib.util.spec_from_file_location("pinned_public_menu_guard", path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


def environment(guard):
    pid = guard.unit(BOT)["MainPID"]
    return dict(x.split("=", 1) for x in Path("/proc/" + pid + "/environ").read_bytes().decode().split("\0") if "=" in x)


def config_hash(env):
    prefixes = ("BOT_", "DATABASE_", "HEALTH_", "SUPPORT_", "SUBSCRIPTION_", "HAPP_", "PILOT_", "KUDAVPN_")
    selected = {k: v for k, v in env.items() if k.startswith(prefixes)}
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def snapshot(guard):
    result = guard.snapshot()
    name = "postgresql@16-main.service"
    row = guard.unit(name)
    if row["ActiveState"] != "active" or int(row["MainPID"]) < 2:
        raise ValueError("postgresql_unavailable")
    result["units"][name] = row
    for path in [row["FragmentPath"], *row["DropInPaths"].split()]:
        result["files"][path] = guard.sha(path)
    return result


def ready(env):
    port = int(env.get("HEALTH_PORT", "0"))
    if port != 18081:
        raise ValueError("unexpected_health_port")
    with urllib.request.urlopen("http://127.0.0.1:18081/ready", timeout=4) as response:
        return response.status == 200 and json.load(response) == {"ready": True}


def wait_ready(env):
    end = time.monotonic() + 60
    while time.monotonic() < end:
        try:
            if ready(env):
                return
        except Exception:
            pass
        time.sleep(1)
    raise ValueError("test_bot_not_ready")


def verify_payload(guard, manifest, payload):
    if manifest["approved"] != REVISION or set(manifest["files"]) != FILES:
        raise ValueError("wrong_payload")
    guard.validate_files({"unit": BOT, "old": str(OLD), "files": manifest["files"]}, payload)


def probe(env):
    code = (
        "from bot.config import Config;from bot.screens import main_actions;from bot.handlers import create_router;"
        "from bot.premium_emoji import decorate;cfg=Config.from_env();"
        "assert cfg.pilot_premium_account_enabled and cfg.pilot_welcome_enabled;"
        "assert cfg.pilot_menu_links.website=='https://r1276248.s2578.vm.mydomain.zone:20443';"
        "assert not cfg.pilot_menu_links.channel and not cfg.pilot_menu_links.community;"
        "rows=decorate(main_actions(menu_links=cfg.pilot_menu_links)).keyboard.inline_keyboard;"
        "assert rows[0][0].callback_data=='nav:tariffs' and len(rows[0])==1;"
        "assert rows[-3][0].url==cfg.pilot_menu_links.website and rows[-3][0].callback_data is None;"
        "assert rows[-2][0].callback_data=='nav:channel' and rows[-1][0].callback_data=='nav:community';"
        "assert all(row[0].icon_custom_emoji_id for row in rows[-3:]);create_router()"
    )
    result = subprocess.run(
        ["runuser", "-u", "kudavpn_trial_bot", "--", "/opt/kudavpn-telegram-pilot/venv/bin/python", "-c", code],
        cwd=NEW, env=env, capture_output=True, timeout=25)
    if result.returncode:
        raise ValueError("unprivileged_probe_failed")


def encrypted_backup(guard, before, env):
    config = guard.config_for_age()
    paths = [OLD, *[Path(p) for p in guard.CONFIGS],
             Path("/etc/systemd/system/" + BOT),
             Path("/etc/systemd/system/" + BOT + ".d")]
    ledger = Path(env["PILOT_WELCOME_STATE_FILE"])
    if not ledger.is_file() or ledger.parent != OLD.parent / "private-welcome":
        raise ValueError("unexpected_onboarding_file")
    with sqlite3.connect("file:" + str(ledger) + "?mode=ro", uri=True) as connection:
        state = connection.serialize()
    extras = {
        "effective/environment.json": json.dumps(env).encode(),
        "effective/launch.json": json.dumps(before["units"][BOT]).encode(),
        "effective/onboarding.sqlite": state,
    }
    archive_path = BACKUP / "configuration-and-runtime.tar.age"
    with archive_path.open("xb") as output:
        process = subprocess.Popen([config["age"], "-r", config["recipient"]],
                                   stdin=subprocess.PIPE, stdout=output, stderr=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdin, mode="w|") as archive:
                for path in paths:
                    archive.add(path, arcname=str(path).lstrip("/"),
                        filter=lambda info: None if "__pycache__" in Path(info.name).parts or ".git" in Path(info.name).parts else info)
                for name, data in extras.items():
                    info = tarfile.TarInfo(name);info.size = len(data);info.mode = 0o600
                    archive.addfile(info, io.BytesIO(data))
            process.stdin.close();process.stdin = None
            process.communicate(timeout=120)
            if process.returncode:
                raise ValueError("encryption_failed")
        finally:
            if process.poll() is None:
                process.kill();process.wait()
    archive_path.chmod(0o600)
    if not archive_path.read_bytes().startswith(b"age-encryption.org/v1\n") or archive_path.stat().st_size < 100:
        raise ValueError("invalid_encrypted_backup")
    guard.save(BACKUP / "ciphertext.json", {"bytes": archive_path.stat().st_size, "sha256": guard.sha(archive_path)})


def rollback(guard):
    before = json.loads((BACKUP / "before.json").read_text())
    # A failed new process may have MainPID=0; rollback must not read its /proc.
    if DROPIN.exists():
        if DROPIN.read_text() != "[Service]\nWorkingDirectory=" + str(NEW) + "\n":
            raise ValueError("rollback_override_changed")
        DROPIN.unlink()
    guard.run(["systemctl", "daemon-reload"])
    guard.run(["systemctl", "restart", BOT], timeout=60)
    wait_ready({"HEALTH_PORT": "18081"})
    if guard.unit(BOT)["WorkingDirectory"] != str(OLD):
        raise ValueError("rollback_runtime_failed")
    guard.compare(before["protected"], snapshot(guard), BOT)
    guard.save(BACKUP / ("rolled-back-" + str(time.time_ns()) + ".json"), {"ready": True})


def apply(guard, manifest, payload):
    verify_payload(guard, manifest, payload)
    env = environment(guard)
    from urllib.parse import urlsplit
    db = urlsplit(env["DATABASE_URL"])
    if (db.hostname != "127.0.0.1" or db.path != "/kudavpn_telegram_pilot"
            or guard.unit(BOT)["User"] != "kudavpn_trial_bot"
            or env.get("KUDAVPN_PILOT_MARKER") != "kudavpn-telegram-pilot-v1"
            or env.get("PILOT_PREMIUM_ACCOUNT_ENABLED") != "1"
            or env.get("PILOT_WELCOME_ENABLED") != "1"):
        raise ValueError("not_isolated_test_bot")
    guard.test_bot_identity({"unit": BOT})
    if int(env["PILOT_TRIAL_OWNER_ID"]) != json.loads(Path("/etc/kudavpn-website-review/owner.json").read_text())["owner_id"]:
        raise ValueError("owner_mismatch")
    if BACKUP.exists() or NEW.exists() or DROPIN.exists():
        raise ValueError("deployment_collision")
    if shutil.disk_usage(OLD).free < 500 * 1024**2:
        raise ValueError("insufficient_disk")
    wait_ready(env)
    before = snapshot(guard);financial = guard.data_digest()
    BACKUP.mkdir(parents=True, mode=0o700)
    guard.save(BACKUP / "before.json", {"protected": before, "data_digest": financial,
                                      "configuration_hash": config_hash(env)})
    encrypted_backup(guard, before, env)
    rollback_script = "#!/bin/sh\nset -eu\nexec python3 " + str(HERE / "update_public_menu.py") + " rollback\n"
    (BACKUP / "rollback.sh").write_text(rollback_script);(BACKUP / "rollback.sh").chmod(0o700)
    guard.run(["sh", "-n", str(BACKUP / "rollback.sh")])
    shutil.copytree(OLD, NEW, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    for name in manifest["files"]:
        target = NEW / name
        guard.atomic_copy(payload / name, target)
    old_hashes = guard.code_hashes(OLD);new_hashes = guard.code_hashes(NEW)
    if ({k: v for k, v in old_hashes.items() if k not in FILES}
            != {k: v for k, v in new_hashes.items() if k not in FILES}):
        raise ValueError("unrelated_file_changed")
    probe(env)
    guard.compare(before, snapshot(guard))
    if financial != guard.data_digest():
        raise ValueError("canonical_changed_before_restart")
    changed = False
    try:
        changed = True
        DROPIN.write_text("[Service]\nWorkingDirectory=" + str(NEW) + "\n");DROPIN.chmod(0o644)
        guard.run(["systemctl", "daemon-reload"])
        guard.run(["systemctl", "restart", BOT], timeout=60)
        wait_ready(env)
        after = snapshot(guard)
        guard.compare(before, after, BOT)
        for path in [before["units"][BOT]["FragmentPath"], *before["units"][BOT]["DropInPaths"].split()]:
            if after["files"].get(path) != before["files"][path]:
                raise ValueError("existing_bot_unit_changed")
        if after["units"][BOT]["User"] != before["units"][BOT]["User"]:
            raise ValueError("bot_user_changed")
        if financial != guard.data_digest() or config_hash(environment(guard)) != config_hash(env):
            raise ValueError("protected_data_or_configuration_changed")
        if guard.unit(BOT)["WorkingDirectory"] != str(NEW):
            raise ValueError("runtime_mismatch")
        for name, hashes in manifest["files"].items():
            if guard.sha(NEW / name) != hashes["after"]:
                raise ValueError("installed_file_mismatch")
        guard.test_bot_identity({"unit": BOT})
        guard.save(BACKUP / "accepted.json", {
            "ready": True, "revision": REVISION, "old": str(OLD), "new": str(NEW),
            "canonical_data_unchanged": True, "protected_services_unchanged": True,
            "configuration_unchanged": True, "banner_unchanged": True,
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print("test_public_menu=ready canonical_data=unchanged protected_services=unchanged")
    except BaseException:
        if changed:
            rollback(guard)
        raise


if __name__ == "__main__":
    try:
        if os.geteuid() != 0:
            raise ValueError("root_required")
        os.umask(0o077)
        guard = load_guard()
        with open("/run/kudavpn-public-menu.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if sys.argv[1:] == ["rollback"]:
                rollback(guard)
            elif sys.argv[1:] == ["apply"]:
                payload = HERE / "payload"
                apply(guard, json.loads((payload / "manifest.json").read_text()), payload)
            else:
                raise ValueError("mode_required")
    except BaseException as error:
        import re
        code = str(error) if isinstance(error, ValueError) and re.fullmatch("[a-z_]{1,80}", str(error)) else type(error).__name__
        print("public_menu_stopped=" + code)
        raise SystemExit(1) from None
