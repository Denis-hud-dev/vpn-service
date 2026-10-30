# Approved branding: TEST acceptance, 2026-10-30

PR [#29](https://github.com/Denis-hud-dev/vpn-service/pull/29), branch `codex/standalone-site-v1-20261026`. Approved visual source ca0d10e. Only selected visual files overlaid on the existing individual TEST runtimes; the entire PR branch was NOT deployed.

Evidence: [receipt](branding-rollout-20261030/acceptance.json), [real HTTPS viewport measurements](branding-rollout-20261030/browser-metrics.json).

## Resumption and confirmed fixes

The website, backup/rollback preparation and 44 targeted tests were already completed. The website was not deployed again. Completed the remaining feed, legacy checkout artwork, TEST welcome banner and actual acceptance checks.

The original PNG remains byte-identical: `bot/assets/brand/original.png`, SHA256 ea8160b3667452aceac8b3939308046dcf2585c57fd4f78d98f47bf84d5547d4, 1367x1151 RGBA with real alpha. Full artwork for hero/login/Telegram, approved compact original K for header/favicon. No regeneration, stretching, CSS background removal or changes to the approved mobile reduction.

The initial preflight failed because the read-only guard path referenced an absent file in an old release. Commit21651b0 pins the existing guard path and its SHA256; checks were preserved.

Final feed acceptance then found503 on a NEW artwork URL, while the subscription and connection page themselves remained200. The old feed runtime had no bot/assets directory. Under secure umask077 the new public parent was0700, preventing the unprivileged service from traversing it. Rolled back only the TEST feed immediately. Commit36b3e32 sets0755 ONLY on newly introduced public artwork directories. Existing private directory permissions are never relaxed; incompatible existing permissions stop deployment. All nine artwork URLs now require HTTP200 and exact SHA256 before feed acceptance. A separate feed_retry stage made a fresh backup and retained the original attempt.

## Applied stages

| Stage | Accepted UTC, Oct30 | Change | Restart |
|---|---|---|---|
| website | 13:48:53 | brand-ca0d10ece2f3-website clone | website-review only |
| feed | 13:51:26 | first attempt; subsequently rolled back | TEST feed only |
| checkout | 17:08:57 | existing public logo PNG and favicon link | none |
| bot | 17:10:25 | bot/assets/welcome-premium.png only | none |
| feed_retry | 17:19:18 | brand-ca0d10ece2f3-feed_retry; nine image hashes verified | TEST feed only |

Restarted only kudavpn-website-review.service once and kudavpn-telegram-pilot-feed.service three times (initial, rollback, retry). Systemd daemon-reload read the new drop-ins. Bot PID4059544, checkout2613987, Xray2318/55085/1883320/1883327, site-payments3192109 and Monitor312050 remained unchanged. Protected configurations and listeners443/8443/9443/10443 were unchanged.

Bot Python, feature flags, profile/avatar/description were not changed. One separate preview was delivered only to the verified owner by the verified KudaVPN_Test_Bot, message_id206. Its receipt prevents repeat delivery. It does not reset onboarding.

## Actual acceptance

Closed site: https://r1276248.s2578.vm.mydomain.zone:20443/ . Owner completed Telegram OAuth. Account, home/account/connect navigation and session persistence passed. The previous one-hour session expired during a long pause; a fresh owner login restored access normally. No authentication code, signature validation, whitelist or TTL changes. Anonymous home/account401; public /auth/assets/logo.png200 with exact approved hash. /ready intentionally requires the existing loopback probe; external401 is expected.

Home and connect checked at320/375/390/480/1440px: all images loaded, contain, no overflow or cropped logo. Connect hero at390:228.15x192.05px; at1440:380x319.88px. Account390 has no overflow. Browser JS warnings/errors0. Android/iOS/Windows/macOS tabs change instructions. QR SVG exists and show/hide works; no personal URL or QR was published. No physical Happ import/VPN connection was attempted.

Prices199/499/1790 verified. Opened and canceled the199 confirmation panel: bank card available, SBP disabled in sandbox as intended. Did NOT submit the order. Legacy checkout readiness200 and logo200 with exact installed hash. No new orders/payments created.

Existing subscription HTTPS200, one VLESS10443 profile, connection page200. Canonical digest matched before/after EACH apply. After the live /start, full-row digest differs as expected: UserService refreshes users.updated_at. SELECT confirmed one user updated after17:23UTC, but zero subscriptions/payments/tokens changed and zero subscription_history created since rollout. Do not claim an unchanged full-row digest after normal user navigation.

Native Telegram Mac: approved banner visible, repeated /start returned compact real status/menu without another banner, persistent keyboard remained. Opened catalog199/499/1790, information with branded Instruction button, Happ section and Kuda Status. Previous tracked navigation was replaced. Zero new error records in website/feed/bot journals during the acceptance window. New-user onboarding tested with fixtures only; owner state was NOT reset.

## Tests and changed files

46 passed /0 skipped in4.51s: tests/test_branding_rollout.py, test_brand_assets.py, test_connection_page.py, test_pilot_welcome.py, test_local_website_preview.py. No full audit/test suite repetition. On shallow CI the overlay-building test may skip if old Git objects are absent; it passed locally with full history. CI21651b0/run41066489811 succeeded; verify final published HEAD separately in GitHub without redeploying.

Code: deploy/branding/build_overlay.py, deploy/branding/rollout.py, tests/test_branding_rollout.py. Documentation: this report, screenshots/metrics/receipt, WORK_SESSION_HANDOFF.md. Existing payment JS, business handlers, authentication and database migrations were unchanged. AST comparison permits only the visual asset handlers; remaining deployed Python matches the previous runtime.

## Backups and rollback

JustHost: /var/backups/kudavpn-branding/ca0d10ece2f3/{website,feed,feed_retry,checkout,bot}. Root0700 directories; configuration-and-runtime.tar.age0600, verified ciphertext.json hashes, before.json, rollback.sh0700, accepted.json. Sizes7047032/1260024/1260024/5858904/5889624 bytes, about20.3MiB total. Existing verified age binary and owner public recipient used. Private recovery key was not sent to VPS. Automatic backup configuration/timers were not changed and older copies were not removed.

Fast rollback retains old runtime/drop-ins and original public visual files. Run `sh /var/backups/kudavpn-branding/ca0d10ece2f3/<stage>/rollback.sh`. For current feed use **feed_retry**, not the historical feed stage. To undo all visual changes: bot, checkout, feed_retry, website. Only TEST website/feed restart; bot/checkout revert visual files without restart. Never restore PostgreSQL for this visual rollback. Feed rollback was exercised successfully. New encrypted archives were not decrypted without the owner's local private key.

## Screenshots and owner checks

[Home390](branding-rollout-20261030/home-390.jpg), [desktop home](branding-rollout-20261030/home-public-1440.jpg), [connect390 top without personal expiry](branding-rollout-20261030/connect-public-390.jpg), [confirmation without order](branding-rollout-20261030/checkout-confirmation.jpg), [Telegram Mac banner](branding-rollout-20261030/telegram-banner.png). Screenshots are not edited; desktop/connect public captures used screenshot clip before personal data. Full actual account/connect captures remain local only at /Users/gr/Documents/KudaVPN-private/branding-review-20261030,0600,outside Git. Previous local before/after remains in docs/mobile-logo-20261030; it is not a previous live-VPS screenshot.

Owner: check physical Android/iPhone and Safari/Chrome, first start with a separate real test account, copy/scan the personal subscription and connect the existing Happ profile. Do not clear the owner's history/subscription. Avatar/Description Picture updates require separate approval. Production/DigitalOcean/main bot were not updated. VPN/UUID/links/payment settings/Monitor/automatic backups unchanged. Stop for owner acceptance; no new features or payments.
