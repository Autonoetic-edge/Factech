# One image, two commands (gateway | engine). Context = the release directory built by
# package.sh: the frozen engine files at the root, then face-auth, page and SDK bundle.
# The first lines mirror engine/Dockerfile so the VPS build cache is reused.
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
COPY scripts ./scripts

COPY face-auth /opt/face-auth
RUN pip install --no-cache-dir /opt/face-auth

COPY page /srv/page
# The on-page face guide and its pinned detector, served from beside the page (panel.GUIDE_FILES).
COPY shared /srv/shared
COPY vendor /srv/vendor
COPY sdk /srv/sdk
COPY entry.sh /entry.sh

RUN groupadd --system --gid 10001 amfatec && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin amfatec
USER 10001:10001

ENTRYPOINT ["/bin/sh", "/entry.sh"]
