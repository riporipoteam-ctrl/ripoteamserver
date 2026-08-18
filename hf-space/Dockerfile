FROM python:3.12-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    GRADIO_ANALYTICS_ENABLED=False \
    HOME=/home/user \
    DISPLAY=:99 \
    PORT=7860

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cabextract \
        curl \
        dbus-x11 \
        desktop-file-utils \
        ffmpeg \
        espeak-ng \
        file \
        firefox-esr \
        fonts-dejavu-core \
        gcc-mingw-w64-x86-64-win32 \
        git \
        htop \
        jq \
        libgl1-mesa-dri \
        libglx-mesa0 \
        libvulkan1 \
        mesa-vulkan-drivers \
        libgl1-mesa-dri:i386 \
        libglx-mesa0:i386 \
        libvulkan1:i386 \
        mesa-vulkan-drivers:i386 \
        lsof \
        lxappearance \
        lxpanel \
        lxterminal \
        mesa-utils \
        mousepad \
        novnc \
        openbox \
        openssl \
        pcmanfm \
        procps \
        psmisc \
        pulseaudio \
        pulseaudio-utils \
        tesseract-ocr \
        tmux \
        tree \
        unzip \
        7zip \
        websockify \
        wine \
        wine64 \
        wine32:i386 \
        wine32-preloader:i386 \
        x11vnc \
        xdg-utils \
        xdotool \
        xterm \
        xvfb \
        xz-utils \
        zip \
        zstd \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /bin/bash user \
    && mkdir -p /app \
    && chown -R user:user /app /home/user

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY . /app
RUN python - <<'PY'
from pathlib import Path
p = Path('/app/app.py')
text = p.read_text(encoding='utf-8')
needle = 'import spaces\n'
replacement = '''class _DockerSpacesCompat:\n    @staticmethod\n    def GPU(*_args, **_kwargs):\n        def decorator(fn):\n            return fn\n        return decorator\n\nspaces = _DockerSpacesCompat()\n'''
if needle not in text:
    raise SystemExit('expected import spaces line missing from app.py')
p.write_text(text.replace(needle, replacement, 1), encoding='utf-8')
PY
RUN chown -R user:user /app /home/user

USER user

EXPOSE 7860

# gradio.Server() starts the ASGI server while app_server_v2 imports the route
# stack. Keep that process alive after import instead of starting a second Uvicorn
# listener on the same port.
CMD ["python", "-c", "import app_server_v2, threading; threading.Event().wait()"]
