"""Trusted pilot operator issues a short-lived link to a private file, never stdout."""
import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

from bot.services.checkout_sessions import CheckoutSessions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-file', required=True)
    parser.add_argument('--owner-id', required=True, type=int)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    origin = urlsplit(args.origin)
    if origin.scheme != 'https' or not origin.hostname or origin.path not in ('', '/') or origin.query or origin.fragment or origin.username or origin.password:
        raise ValueError('Trusted HTTPS origin required')
    path = Path(args.output)
    if not path.is_absolute():
        raise ValueError('Absolute private output path required')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as handle:
            token = CheckoutSessions(args.session_file, args.owner_id).invite()
            handle.write(args.origin.rstrip('/') + '/checkout#' + token + '\n')
    except Exception:
        path.unlink(missing_ok=True)
        raise
    print('Одноразовый вход сохранён в приватном файле (0600), срок — 10 минут. Не отправляйте ссылку в Telegram.')


if __name__ == '__main__':
    main()
