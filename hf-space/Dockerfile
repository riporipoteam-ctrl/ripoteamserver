FROM python:3.12-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
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
RUN sed -i '/^import spaces$/d' /app/app.py \
    && chown -R user:user /app /home/user

USER user

EXPOSE 7860

CMD ["uvicorn", "app_server_v2:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers"]
