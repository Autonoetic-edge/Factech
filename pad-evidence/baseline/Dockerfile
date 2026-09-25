FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONUNBUFFERED=1

ENV PYTHONDONTWRITEBYTECODE=1

ENV OMP_NUM_THREADS=2
ENV OMP_WAIT_POLICY=PASSIVE

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models ./models

COPY app ./app

ARG FACETECH_BUILD_ID=dev
ENV FACETECH_BUILD_ID=${FACETECH_BUILD_ID}

RUN groupadd --system --gid 10001 engine && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin engine
USER 10001:10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
