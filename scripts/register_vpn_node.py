"""Validate an operator's private node manifest; optional disabled DB insert.

No part of this command contacts Xray, enables access, or prints key material.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from bot.services.node_registry import NodeRegistry, NodeSpec


async def main():
    parser = argparse.ArgumentParser(description='Validate a private VPN node manifest')
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--apply-disabled', action='store_true',
                        help='insert only a disabled node into DATABASE_URL')
    args = parser.parse_args()
    if args.manifest.stat().st_mode & 0o077:
        raise SystemExit('Node manifest must be readable only by its owner (0600)')
    spec = NodeSpec.from_mapping(json.loads(args.manifest.read_text()))
    if not args.apply_disabled:
        print('Public node manifest valid; no database or server change')
        return
    from bot.db.session import Database
    database = Database(os.environ['DATABASE_URL'])
    try:
        await database.check()
        await NodeRegistry(database.sessions).register_disabled(spec)
    finally:
        await database.close()
    print('Disabled node registered; no access issued or server changed')


if __name__ == '__main__':
    asyncio.run(main())
