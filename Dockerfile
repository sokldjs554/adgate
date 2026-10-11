FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# 데이터(SQLite)는 볼륨에, 프로세스는 비루트로
RUN useradd --create-home --uid 10001 adgate && mkdir -p /data && chown adgate /data
USER adgate
ENV ADGATE_DB=/data/adgate.sqlite3
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
# ADGATE_TOKENS=token:role:identity,… 를 반드시 설정한다(없으면 시작하지 않는다).
# 데모: -e ADGATE_DEV=1 -e ADGATE_DEV_ALLOW_PUBLIC=1 (공개 데모 토큰이므로 컨테이너 밖에 공개하지 말 것)
CMD ["adgate", "serve", "--host", "0.0.0.0", "--port", "8000"]
