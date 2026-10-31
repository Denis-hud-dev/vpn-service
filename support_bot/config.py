"""Support-only configuration; never fall back to another bot's environment."""
from __future__ import annotations
from dataclasses import dataclass, field
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    admin_chat_id: int
    operator_ids: frozenset[int]
    database: Path
    expected_username: str
    custom_emoji: bool = False
    test_user_ids: frozenset[int] | None = None

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        try:
            token = env['SUPPORT_BOT_TOKEN'].strip()
            admin = int(env['SUPPORT_ADMIN_CHAT_ID'])
            operators = frozenset(int(x.strip()) for x in env['SUPPORT_OPERATOR_IDS'].split(','))
            test_users = (frozenset(int(x.strip()) for x in env['SUPPORT_TEST_USER_IDS'].split(','))
                          if env.get('SUPPORT_TEST_USER_IDS') else None)
            path = Path(env['SUPPORT_DB_PATH'])
            username = env['SUPPORT_BOT_USERNAME'].strip().lstrip('@')
            if (not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token) or admin >= 0
                    or not operators or any(x <= 0 for x in operators)
                    or (test_users is not None and (not test_users or any(x <= 0 for x in test_users)))
                    or not path.is_absolute() or path.name != 'support.sqlite3'
                    or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{1,28}bot', username, re.I)
                    or username.casefold() in {'kudavpnbot', 'kudavpn_test_bot'}
                    or 'monitor' in username.casefold()
                    or env.get('SUPPORT_CUSTOM_EMOJI', '0') not in {'0', '1'}):
                raise ValueError
            return cls(token, admin, operators, path, username,
                       env.get('SUPPORT_CUSTOM_EMOJI', '0') == '1', test_users)
        except (KeyError, ValueError, TypeError):
            raise ValueError('Invalid support configuration; see deploy/support-bot/.env.example') from None

    async def validate_identity(self, bot):
        me = await bot.get_me()
        if (me.username or '').casefold() != self.expected_username.casefold():
            raise ValueError('support_bot_identity_mismatch')
        chat = await bot.get_chat(self.admin_chat_id)
        if chat.type not in {'group', 'supergroup'} or getattr(chat, 'username', None) or getattr(chat, 'active_usernames', None):
            raise ValueError('support_requires_private_operator_group')
