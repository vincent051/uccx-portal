FROM python:3.12-slim

WORKDIR /app

ARG PIPER_VERSION=2023.11.14-2

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    sox \
    libsox-fmt-all \
    ffmpeg \
    espeak-ng \
    && curl -fsSL \
      "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_x86_64.tar.gz" \
      -o /tmp/piper.tar.gz \
    && tar -xzf /tmp/piper.tar.gz -C /opt \
    && ln -sf /opt/piper/piper /usr/local/bin/piper \
    && rm -f /tmp/piper.tar.gz \
    && piper --help >/dev/null \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DATA_DIR=/data
ENV DATABASE_URL=sqlite:////data/portal.db
ENV PIPER_BIN=/usr/local/bin/piper
ENV PIPER_MODEL=/data/piper/fr_FR-siwis-medium.onnx
ENV KOKORO_MODEL=/data/kokoro/kokoro-v1.0.int8.onnx
ENV KOKORO_VOICES=/data/kokoro/voices-v1.0.bin

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
