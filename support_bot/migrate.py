"""Explicit, support-only migration command. No token is needed."""
import argparse
import os
from pathlib import Path
from support_bot.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database',type=Path,required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        Store(args.database,0).migrate()
    except Exception:
        raise SystemExit('Support migration refused. Check path, ownership, permissions and schema; no working database is supported.') from None
    print('Support schema version 1 ready')


if __name__ == '__main__':
    main()
