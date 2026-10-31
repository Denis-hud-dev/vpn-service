"""Owner-run LOCAL hidden token entry; never run by deployment automation."""
import argparse
from getpass import getpass, GetPassWarning
import os
import sys
import warnings
from pathlib import Path
from support_bot.config import Config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=Path.home()/'Documents/KudaVPN-private/support/support.env')
    args = parser.parse_args()
    output = args.output
    try:
        if not sys.stdin.isatty():
            raise ValueError
        if not output.is_absolute() or output.is_symlink() or output.exists():
            raise ValueError
        parent = output.parent.resolve()
        if any((p/'.git').exists() for p in [parent,*parent.parents]):
            raise ValueError
        parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if parent.stat().st_mode & 0o077:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter('error',GetPassWarning)
            token = getpass('Support bot token (hidden): ').strip()
        values = {
            'SUPPORT_BOT_TOKEN':token,
            'SUPPORT_BOT_USERNAME':input('Dedicated support bot username: ').strip(),
            'SUPPORT_ADMIN_CHAT_ID':input('Private operator group ID (negative integer): ').strip(),
            'SUPPORT_OPERATOR_IDS':input('Operator user IDs (comma-separated positive integers): ').strip(),
            'SUPPORT_DB_PATH':'/var/lib/kudavpn-support/support.sqlite3',
            'SUPPORT_CUSTOM_EMOJI':'0',
        }
        values['SUPPORT_TEST_USER_IDS'] = values['SUPPORT_OPERATOR_IDS']
        Config.from_env(values)
        fd = os.open(parent/output.name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as file:
            file.write(''.join(key+'='+value+'\n' for key,value in values.items()))
        print('Support configuration saved privately (0600). No bot was started.')
    except (Exception,KeyboardInterrupt):
        raise SystemExit('Configuration not saved. Check input, private path and file permissions; do not paste secrets into chat.') from None


if __name__ == '__main__':
    main()
