"""Configure two public TEST-menu URLs only. No application or database update."""
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

BOT = "kudavpn-telegram-pilot-bot.service"
RUNTIME = "/opt/kudavpn-telegram-pilot/app-menu-links-ab72c76b9e4c"
STAGE = Path("/var/lib/kudavpn-ui-rollouts/menu-ab72c76b9e4c/update_public_menu.py")
STAGE_SHA = "b02e43c468b115ed384fa24e75b8b14e17a2c0ee8b9e79db835d59c228544eac"
BACKUP = Path("/var/backups/kudavpn-telegram-pilot/social-links-20261031")
DROPIN = Path("/etc/systemd/system") / (BOT + ".d/99-social-links.conf")
APPROVED = {
    "PILOT_MENU_CHANNEL_URL": "https://t.me/KudaVPN_official",
    "PILOT_MENU_COMMUNITY_URL": "https://t.me/KudaVPN_community",
}
CONTENT = "[Service]\n" + "".join("Environment=" + key + "=" + value + "\n" for key, value in APPROVED.items())


def load_stage():
    if hashlib.sha256(STAGE.read_bytes()).hexdigest() != STAGE_SHA:
        raise ValueError("deployment_helper_changed")
    spec = importlib.util.spec_from_file_location("pinned_existing_test_stage", STAGE)
    stage = importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
    return stage


def unchanged_configuration(stage, old, new):
    # The sole permitted environment delta is these two public URL values.
    clean = lambda env: {k: v for k, v in env.items() if k not in APPROVED}
    return stage.config_hash(clean(old)) == stage.config_hash(clean(new))


def menu(env):
    code = (
        "import json;from bot.config import Config;from bot.screens import main_actions;"
        "from bot.premium_emoji import decorate;cfg=Config.from_env();"
        "assert cfg.pilot_premium_account_enabled and cfg.pilot_welcome_enabled;"
        "s=main_actions(menu_links=cfg.pilot_menu_links);"
        "s=decorate(s) if cfg.pilot_custom_emoji_enabled else s;"
        "print(json.dumps(s.keyboard.model_dump(mode='json',exclude_none=True)))"
    )
    process = subprocess.run(
        ["runuser", "-u", "kudavpn_trial_bot", "--", "/opt/kudavpn-telegram-pilot/venv/bin/python", "-c", code],
        cwd=RUNTIME, env=env, capture_output=True, timeout=20)
    if process.returncode:
        raise ValueError("test_menu_probe_failed")
    return json.loads(process.stdout)


def verify_menu(before, after):
    old = before["inline_keyboard"];new = after["inline_keyboard"]
    if len(old) != len(new) or old[:-2] != new[:-2]:
        raise ValueError("existing_menu_or_website_changed")
    for index, target in zip((-2, -1), APPROVED.values()):
        if len(new[index]) != 1:
            raise ValueError("layout_changed")
        original = {k: v for k, v in old[index][0].items() if k not in ("url", "callback_data")}
        current = {k: v for k, v in new[index][0].items() if k not in ("url", "callback_data")}
        if original != current or new[index][0].get("url") != target or new[index][0].get("callback_data") is not None:
            raise ValueError("public_url_button_mismatch")


def protected(stage, guard, before):
    after = stage.snapshot(guard)
    guard.compare(before, after, BOT)
    for path in [before["units"][BOT]["FragmentPath"], *before["units"][BOT]["DropInPaths"].split()]:
        if after["files"].get(path) != before["files"][path]:
            raise ValueError("existing_unit_changed")
    if after["units"][BOT]["WorkingDirectory"] != RUNTIME or after["units"][BOT]["User"] != "kudavpn_trial_bot":
        raise ValueError("runtime_or_service_user_changed")


def rollback(stage, guard):
    before = json.loads((BACKUP / "before.json").read_text())
    if DROPIN.exists():
        if DROPIN.read_text() != CONTENT:
            raise ValueError("foreign_override_refused")
        DROPIN.unlink()
    guard.run(["systemctl", "daemon-reload"])
    guard.run(["systemctl", "restart", BOT], timeout=60)
    # No attempt to read /proc of a potentially failed new process.
    stage.wait_ready({"HEALTH_PORT": "18081"})
    protected(stage, guard, before["protected"])
    env = stage.environment(guard)
    if any(env.get(key, "") != before["previous_urls"][key] for key in APPROVED):
        raise ValueError("rollback_urls_mismatch")
    guard.save(BACKUP / ("rolled-back-" + str(time.time_ns()) + ".json"), {"ready": True})


def verify_isolation(unit, env):
    database = urlsplit(env.get("DATABASE_URL", ""))
    if (unit["WorkingDirectory"] != RUNTIME or unit["User"] != "kudavpn_trial_bot"
            or env.get("KUDAVPN_PILOT_MARKER") != "kudavpn-telegram-pilot-v1"
            or database.hostname != "127.0.0.1" or database.path != "/kudavpn_telegram_pilot"):
        raise ValueError("unexpected_test_runtime")


def apply(stage, guard):
    if BACKUP.exists() or DROPIN.exists():
        raise ValueError("deployment_collision")
    unit = guard.unit(BOT);env = stage.environment(guard)
    verify_isolation(unit, env)
    guard.test_bot_identity({"unit": BOT})
    if any(env.get(key, "") for key in APPROVED):
        raise ValueError("unexpected_existing_urls")
    if shutil.disk_usage(RUNTIME).free < 500*1024**2:
        raise ValueError("insufficient_disk")
    stage.wait_ready(env)
    original_menu = menu(env)
    before = stage.snapshot(guard);source = guard.code_hashes(Path(RUNTIME))
    # Fresh encrypted backup of the actual runtime/config/effective environment.
    BACKUP.mkdir(parents=True, mode=0o700)
    guard.save(BACKUP / "before.json", {
        "protected": before, "configuration_hash": stage.config_hash(env),
        "previous_urls": {key: env.get(key, "") for key in APPROVED}, "source_hashes": source})
    stage.OLD = Path(RUNTIME);stage.BACKUP = BACKUP
    stage.encrypted_backup(guard, before, env)
    rollback_text = "#!/bin/sh\nset -eu\nexec python3 " + str(Path(__file__).resolve()) + " rollback\n"
    (BACKUP / "rollback.sh").write_text(rollback_text);(BACKUP / "rollback.sh").chmod(0o700)
    guard.run(["sh", "-n", str(BACKUP / "rollback.sh")])
    if guard.code_hashes(Path(RUNTIME)) != source:
        raise ValueError("source_changed_before_restart")
    guard.compare(before, stage.snapshot(guard))
    changed = False
    try:
        changed = True
        DROPIN.write_text(CONTENT);DROPIN.chmod(0o644)
        guard.run(["systemctl", "daemon-reload"])
        guard.run(["systemctl", "restart", BOT], timeout=60)
        stage.wait_ready(env)
        updated_env = stage.environment(guard)
        if not unchanged_configuration(stage, env, updated_env) or any(updated_env.get(k) != v for k, v in APPROVED.items()):
            raise ValueError("unexpected_environment_change")
        updated_menu = menu(updated_env);verify_menu(original_menu, updated_menu)
        protected(stage, guard, before)
        if guard.code_hashes(Path(RUNTIME)) != source:
            raise ValueError("application_source_changed")
        guard.test_bot_identity({"unit": BOT})
        guard.save(BACKUP / "menu-markup.json", updated_menu)
        guard.save(BACKUP / "accepted.json", {
            "ready": True, "configuration_only": True, "runtime_unchanged": True,
            "protected_services_unchanged": True, "website_unchanged": True,
            "channel": APPROVED["PILOT_MENU_CHANNEL_URL"],
            "community": APPROVED["PILOT_MENU_COMMUNITY_URL"],
            "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print("social_links=ready configuration_only=true website=unchanged")
    except BaseException:
        if changed:
            rollback(stage, guard)
        raise


if __name__ == "__main__":
    try:
        if os.geteuid() != 0:
            raise ValueError("root_required")
        os.umask(0o077);stage = load_stage();guard = stage.load_guard()
        with open("/run/kudavpn-social-links.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if sys.argv[1:] == ["apply"]:
                apply(stage, guard)
            elif sys.argv[1:] == ["rollback"]:
                rollback(stage, guard)
            else:
                raise ValueError("mode_required")
    except BaseException as error:
        import re
        code = str(error) if isinstance(error, ValueError) and re.fullmatch("[a-z_]{1,80}", str(error)) else type(error).__name__
        print("social_links_stopped=" + code)
        raise SystemExit(1) from None
