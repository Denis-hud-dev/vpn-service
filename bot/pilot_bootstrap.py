"""Opt-in launcher for the isolated Telegram pilot compose file only.

The database URL is constructed here, never accepted from an existing shell or
production env file. The launcher does not contact Telegram or the VPN node.
"""
import base64
import binascii
import ipaddress
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit


PILOT_MARKER = 'kudavpn-telegram-pilot-v1'


def prepare_environment(mode: str, source: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if source is None else source)
    if mode not in {'migrate', 'bot', 'feed', 'trial-grant', 'trial-extend'} or env.get('KUDAVPN_PILOT_MARKER') != PILOT_MARKER:
        raise ValueError('Isolated pilot marker or mode is invalid')
    if env.get('DATABASE_URL') or env.get('DATABASE_URL_DOCKER'):
        raise ValueError('Refusing a pre-existing database URL in pilot mode')

    password_path = Path(env.get('PILOT_DB_PASSWORD_FILE', ''))
    if not password_path.is_file():
        raise ValueError('Pilot database password file is missing')
    password = password_path.read_text().strip()
    if len(password) < 24 or '\n' in password:
        raise ValueError('Pilot database password is invalid')
    db_host = env.get('PILOT_DB_HOST', 'postgres')
    if db_host not in ('postgres', '127.0.0.1'):
        raise ValueError('Pilot database host must be the isolated Compose service or loopback')
    env['DATABASE_URL'] = ('postgresql+psycopg://kudavpn_pilot:'
                           f'{quote(password, safe="")}@{db_host}:5432/kudavpn_telegram_pilot')
    owner_id = env.get('PILOT_TRIAL_OWNER_ID', '')
    if owner_id and not re.fullmatch(r'[1-9][0-9]*', owner_id):
        raise ValueError('Pilot trial owner ID must be a positive Telegram ID')
    if mode in {'trial-grant', 'trial-extend'} and not owner_id:
        raise ValueError('Pilot trial owner ID is required')

    if mode in {'bot', 'feed'}:
        public_url = env.get('PILOT_SUBSCRIPTION_PUBLIC_URL', '').strip()
        if mode == 'feed' and not public_url:
            raise ValueError('Pilot feed requires a trusted HTTPS origin')
        if public_url:
            url = urlsplit(public_url)
            try:
                parsed_ip = ipaddress.ip_address(url.hostname or '')
                is_ip = True
            except ValueError:
                parsed_ip = None
                is_ip = False
            if (url.scheme != 'https' or not url.hostname or '.' not in url.hostname
                    or (is_ip and (env.get('PILOT_ALLOW_IP_CERT') != '1'
                                   or not parsed_ip.is_global))
                    or url.hostname == 'localhost' or url.port not in (None, 443, 18443)
                    or url.username or url.password or url.query or url.fragment
                    or url.path not in ('', '/')):
                raise ValueError('Pilot subscription URL must be a trusted HTTPS origin')
            env['SUBSCRIPTION_PUBLIC_URL'] = public_url.rstrip('/')
        else:
            env.pop('SUBSCRIPTION_PUBLIC_URL', None)

    if mode == 'bot':
        token_path = Path(env.get('PILOT_BOT_TOKEN_FILE', ''))
        if not token_path.is_file():
            raise ValueError('Pilot Telegram token file is missing')
        token = token_path.read_text().strip()
        expected_id = env.get('PILOT_EXPECTED_BOT_ID', '')
        if not re.fullmatch(r'[1-9][0-9]*', expected_id):
            raise ValueError('Expected test bot ID is required')
        if not re.fullmatch(r'[1-9][0-9]*:[A-Za-z0-9_-]{20,}', token):
            raise ValueError('Pilot Telegram token format is invalid')
        if token.partition(':')[0] != expected_id:
            raise ValueError('Telegram token does not belong to expected test bot')
        env['BOT_TOKEN'] = token
        if public_url:
            secret_path = Path(env.get('PILOT_HAPP_LINK_SECRET_FILE', ''))
            if not secret_path.is_file():
                raise ValueError('Pilot Happ secret file is missing')
            link_secret = secret_path.read_text().strip()
            try:
                raw = base64.urlsafe_b64decode(link_secret + '=')
            except (ValueError, binascii.Error):
                raw = b''
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}', link_secret) or len(raw) != 32:
                raise ValueError('Pilot Happ secret must be 32-byte base64url')
            env['HAPP_LINK_SECRET'] = link_secret
        else:
            if env.get('PILOT_HAPP_LINK_SECRET_FILE'):
                raise ValueError('Pilot Happ secret requires a subscription URL')
            env.pop('HAPP_LINK_SECRET', None)
        env['HEALTH_PORT'] = '18081'
    elif mode == 'feed':
        # The feed has no Telegram credential. It reads only the pilot DB and
        # talks to one private node agent through its own mTLS config.
        if env.get('PILOT_BOT_TOKEN_FILE') or env.get('BOT_TOKEN'):
            raise ValueError('Pilot feed must not receive a Telegram token')
        env.pop('HAPP_LINK_SECRET', None)
        for key in ('PILOT_TLS_CERT_FILE', 'PILOT_TLS_KEY_FILE', 'PILOT_NODE_AGENT_CONFIG'):
            path = Path(env.get(key, ''))
            if not path.is_file():
                raise ValueError(f'{key} is missing')
        for key in ('PILOT_TLS_KEY_FILE', 'PILOT_NODE_AGENT_CONFIG'):
            if Path(env[key]).stat().st_mode & 0o077:
                raise ValueError(f'{key} must be private')
        env['SUBSCRIPTION_TLS_CERT'] = env['PILOT_TLS_CERT_FILE']
        env['SUBSCRIPTION_TLS_KEY'] = env['PILOT_TLS_KEY_FILE']
        env['NODE_AGENT_CONFIG'] = env['PILOT_NODE_AGENT_CONFIG']
        env['SUBSCRIPTION_BIND_HOST'] = '0.0.0.0'
        env['SUBSCRIPTION_PORT'] = '18443'
    return env


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) >= 2 else ''
    try:
        env = prepare_environment(mode)
    except (ValueError, OSError) as exc:
        raise SystemExit(f'Pilot startup refused: {exc}') from None
    if mode in {'trial-grant', 'trial-extend'}:
        from datetime import datetime
        from bot.db.session import Database
        from bot.services.pilot_trial import PilotTrialService
        import asyncio

        async def operate():
            database = Database(env['DATABASE_URL'])
            try:
                await database.check()
                trial = PilotTrialService(database.sessions, int(env['PILOT_TRIAL_OWNER_ID']))
                if mode == 'trial-grant' and len(sys.argv) == 2:
                    await trial.grant_once(trial.owner_telegram_id)
                elif mode == 'trial-extend' and len(sys.argv) == 3:
                    await trial.extend_to(trial.owner_telegram_id, datetime.fromisoformat(sys.argv[2]))
                else:
                    raise ValueError('Trial command arguments are invalid')
            finally:
                await database.close()
        try:
            asyncio.run(operate())
        except (ValueError, PermissionError) as exc:
            raise SystemExit(f'Pilot trial refused: {exc}') from None
        print('Pilot trial operation completed')
        return
    command = ([sys.executable, '-m', 'alembic', 'upgrade', 'head'] if mode == 'migrate'
               else [sys.executable, '-m', 'bot.subscription_api.main'] if mode == 'feed'
               else [sys.executable, '-m', 'bot.main'])
    os.execvpe(command[0], command, env)


if __name__ == '__main__':
    main()
