"""Verify the pilot feed's local TLS listener using the certificate hostname."""
import os
import socket
import ssl
from urllib.parse import urlsplit


def main() -> None:
    hostname = urlsplit(os.environ['SUBSCRIPTION_PUBLIC_URL']).hostname
    if not hostname:
        raise SystemExit('Missing subscription hostname')
    port = int(os.environ.get('SUBSCRIPTION_PORT', '18443'))
    with socket.create_connection(('127.0.0.1', port), timeout=3) as connection:
        with ssl.create_default_context().wrap_socket(connection, server_hostname=hostname):
            pass


if __name__ == '__main__':
    main()
