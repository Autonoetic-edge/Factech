FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONUNBUFFERED=1

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir \
      "fastapi==0.141.1" \
      "starlette==1.6.0" \
      "uvicorn[standard]==0.52.4" \
      "httpx==0.28.1" \
      "msgpack==1.1.2"

COPY apps ./apps
COPY mock-gateway ./mock-gateway

COPY packages/face-sdk/dist ./packages/face-sdk/dist

RUN groupadd --gid 10002 gateway \
 && useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin gateway
USER 10002:10002

WORKDIR /app/mock-gateway
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
