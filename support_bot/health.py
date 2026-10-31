"""Local support-only health check/retry. Prints no ticket content or identities."""
import argparse
import json
import time
from pathlib import Path
from support_bot.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--retry-blocked',type=int)
    args = parser.parse_args()
    try:
        store = Store(args.database,0)
        if args.retry_blocked is not None:
            if args.retry_blocked <= 0:
                raise ValueError
            print(json.dumps({'retry_queued':store.retry_blocked(args.retry_blocked)}))
            return
        result = store.delivery_health()
        stamp = result['worker_checked_at']
        result['worker_recent'] = stamp is not None and 0 <= time.time()-stamp <= 60
        print(json.dumps(result))
        if not result['worker_recent'] or result['blocked'] or result['overdue_seconds'] > 300:
            raise SystemExit(1)
    except Exception:
        raise SystemExit('Support health unavailable; inspect local permissions/schema.') from None


if __name__ == '__main__':
    main()
