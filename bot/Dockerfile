FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY bot/requirements.txt /app/bot/requirements.txt
RUN pip install --no-cache-dir -r bot/requirements.txt && useradd --create-home --uid 10001 kudavpn
COPY bot /app/bot
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
USER kudavpn
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["python", "-m", "bot.main"]
