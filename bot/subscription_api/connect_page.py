"""First-party, private connection page for an already entitled Happ feed."""
from datetime import datetime, timezone
from html import escape
from io import BytesIO
import qrcode
from qrcode.image.svg import SvgPathFillImage


def render_connection_page(feed_url: str, expires: int) -> str:
    image = qrcode.make(feed_url, image_factory=SvgPathFillImage, box_size=6, border=3)
    buffer = BytesIO()
    image.save(buffer)
    qr = buffer.getvalue().decode('utf-8')
    # The QR is a bearer credential, rendered locally and disclosed only on this private page.
    expiry = datetime.fromtimestamp(expires, timezone.utc).strftime('%d.%m.%Y · %H:%M UTC')
    safe_url = escape(feed_url, quote=True)
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>KudaVPN Premium — подключение</title>
<link rel="stylesheet" href="/assets/connect.css"><script src="/assets/connect.js" defer></script></head>
<body><div class="ambient" aria-hidden="true"></div><main class="shell">
  <header class="brand"><img class="brand-mark" src="/assets/kudavpn-original-logo.jpeg" alt="Оригинальный логотип KudaVPN"><span>KudaVPN <em>Premium</em></span><span class="brand-dot" aria-hidden="true"></span></header>
  <section class="hero"><div class="hero-copy"><div class="eyebrow"><span class="spark"></span> ЛИЧНОЕ ПРОСТРАНСТВО ПОДКЛЮЧЕНИЯ</div>
  <h1>Свобода быть<br><span>на связи.</span></h1>
  <p class="lead">Ваш личный доступ к KudaVPN. Выберите устройство, добавьте подписку в Happ и включите защищённое подключение.</p>
  <span class="hero-line" aria-hidden="true">◆ ━━━━━━━━━━━━━━━━ ◇</span></div><img class="hero-logo" src="/assets/kudavpn-original-logo.jpeg" alt="KudaVPN: геометрическая K, цифровой глобус и орбита"></section>
  <section class="status" aria-label="Состояние подписки"><div><span class="status-label">ПОДПИСКА</span><strong><i class="live-dot"></i> Активна</strong></div><div><span class="status-label">ДЕЙСТВУЕТ ДО</span><strong>{escape(expiry)}</strong></div><div><span class="status-label">ПРОФИЛЬ</span><strong>Личный доступ</strong></div></section>
  <section class="connect-card"><div class="card-top"><span class="step-index">01 / 02</span><span class="card-caption">ВЫБЕРИТЕ ПЛАТФОРМУ</span></div>
   <div class="platforms" role="tablist" aria-label="Устройство">
    <button type="button" role="tab" aria-selected="true" data-platform="android">Android</button>
    <button type="button" role="tab" aria-selected="false" data-platform="ios">iOS</button>
    <button type="button" role="tab" aria-selected="false" data-platform="windows">Windows</button>
    <button type="button" role="tab" aria-selected="false" data-platform="macos">macOS</button>
   </div>
   <div class="separator"></div><div class="card-top"><span class="step-index">02 / 02</span><span class="card-caption">ДОБАВЬТЕ ПОДПИСКУ</span></div>
   <div class="action-layout"><div><h2>Подключить VPN</h2><p id="platform-instruction">На Android добавьте личную HTTPS-подписку в Happ из буфера обмена.</p>
   <button class="primary" id="copy-primary" type="button" data-subscription="{safe_url}"><span>Скопировать ссылку для Happ</span><span aria-hidden="true">↗</span></button>
   <p class="feedback" id="copy-feedback" role="status" aria-live="polite">Ссылка копируется одним нажатием. Импорт в Happ выполняется в приложении.</p>
   <ol class="import-steps" id="import-steps"><li>Скопируйте личную ссылку.</li><li>Откройте Happ, нажмите «+» и добавьте подписку из буфера обмена.</li><li>Выберите KudaVPN и включите VPN.</li></ol>
   <p class="install-note">Нет Happ? Установите приложение с <a href="https://www.happ.su/main/ru" rel="noreferrer noopener" target="_blank">официального сайта Happ</a>, затем вернитесь к этим шагам.</p>
   <div class="secondary-actions"><button type="button" id="show-link">Показать ссылку</button><button type="button" id="show-qr">Показать QR-код</button></div>
   <div class="private-link" id="private-link" hidden><span>Личная HTTPS-ссылка</span><code>{safe_url}</code><button type="button" id="copy-backup">Копировать</button></div>
   </div><aside class="security-note"><span class="note-symbol" aria-hidden="true">◆</span><span class="note-label">ПРИВАТНЫЙ ДОСТУП</span><strong>Одна ссылка.<br>Ваше пространство.</strong><p>Подписка принадлежит только вам. После продления ссылка остаётся прежней.</p><div class="note-orbit" aria-hidden="true"></div></aside></div>
   <div class="qr-panel" id="qr-panel" hidden><div class="qr-art">{qr}</div><div><strong>Импорт через QR</strong><p>Откройте Happ на другом устройстве и отсканируйте этот код. QR содержит личную ссылку — не пересылайте его.</p></div></div>
  </section><footer><span>✦ KudaVPN Premium</span><span>Личная ссылка даёт доступ к подписке. Не делитесь ею.</span></footer>
 </main></body></html>'''
