FROM python:3.12-alpine
COPY server.py /srv/server.py
EXPOSE 8901
HEALTHCHECK --interval=2s --timeout=2s --retries=15 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8901/health')"
CMD ["python", "/srv/server.py"]
