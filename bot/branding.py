"""Approved transparent original plus an authorized compact raster K extraction."""
from pathlib import Path

BRAND_DIR = Path(__file__).with_name('assets') / 'brand'
ORIGINAL_LOGO = BRAND_DIR / 'original.png'
WELCOME_BANNER = BRAND_DIR / 'telegram-welcome.png'

# Keep previously issued image URLs working without retaining old artwork.
BRAND_IMAGES = {
    'logo.png': ('logo-512.png', 'image/png'),
    'logo-large.png': ('logo-1024.png', 'image/png'),
    'logo-small.png': ('logo-192.png', 'image/png'),
    'k-symbol.png': ('k-symbol.png', 'image/png'),
    'favicon.ico': ('favicon.ico', 'image/vnd.microsoft.icon'),
    'kudavpn-original-logo.png': ('original.png', 'image/png'),
    'kudavpn-original-logo.jpeg': ('legacy-logo.jpg', 'image/jpeg'),
    'kudavpn-orbit-emblem.png': ('logo-512.png', 'image/png'),
}


def brand_image(name):
    filename, content_type = BRAND_IMAGES[name]
    return BRAND_DIR / filename, content_type
