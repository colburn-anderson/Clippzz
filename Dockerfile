# One image for both the dashboard and the worker. The code itself is mounted
# from the repo checkout at run time, so a code change needs only a restart,
# not a rebuild (fewer writes to the Pi's storage).
FROM python:3.13-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates fonts-montserrat fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to read YouTube pages.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/work \
    XDG_CACHE_HOME=/work/.cache
WORKDIR /app
