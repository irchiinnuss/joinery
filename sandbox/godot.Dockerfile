# The Godot check worker: Godot alone, for Joinery's checks of a change
# (Godot's import, the project's GUT and SceneTree tests).
#
# Godot publishes a Linux arm64 build, so unlike the shared tool images (amd64
# because Blender, Material Maker and Surge XT have no Linux arm64 build) this
# one is built for the host: on Apple Silicon the checks run natively instead of
# emulated (docs/adr/0090). It carries no Blender, so a project whose Godot
# imports .blend files keeps checking in the creative worker.
#
# The same guarantees as every worker: the download is hash-checked against
# checksums.txt (x86_64) or checksums-native.txt (arm64), both written only by
# fetch-checksums.sh from the real bytes; it runs as an
# unprivileged user, and Joinery runs it with --network none.
ARG JOINERY_PLATFORM=linux/amd64

FROM --platform=${JOINERY_PLATFORM} debian:trixie-slim AS fetch
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN printf 'Acquire::Retries "8";\nAcquire::http::Timeout "60";\nAcquire::http::Pipeline-Depth "0";\nAcquire::Queue-Mode "access";\nAcquire::ForceIPv4 "true";\n' \
      >/etc/apt/apt.conf.d/80-joinery-fetch
COPY apt-install.sh /usr/local/bin/apt-install
RUN chmod +x /usr/local/bin/apt-install && apt-install ca-certificates curl unzip
COPY checksums.txt checksums-native.txt /opt/joinery/
COPY verify.sh /usr/local/bin/joinery-verify-download
ARG GODOT_VERSION
ARG GODOT_CHANNEL
ARG TARGETARCH
RUN set -euo pipefail; \
    chmod +x /usr/local/bin/joinery-verify-download; \
    if [ -z "${GODOT_VERSION:-}" ] || [ -z "${GODOT_CHANNEL:-}" ]; then \
      echo "GODOT_VERSION/GODOT_CHANNEL unset: build via joinery setup" >&2; \
      exit 1; \
    fi; \
    case "${TARGETARCH}" in \
      amd64) arch=x86_64; key=godot; sums=checksums.txt ;; \
      arm64) arch=arm64; key=godotarm64; sums=checksums-native.txt ;; \
      *) echo "godot: no Linux build for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    name="Godot_v${GODOT_VERSION}-${GODOT_CHANNEL}_linux.${arch}"; \
    curl -fsSL -o /tmp/godot.zip \
      "https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}-${GODOT_CHANNEL}/${name}.zip"; \
    JOINERY_CHECKSUMS="/opt/joinery/${sums}" joinery-verify-download /tmp/godot.zip "${key}"; \
    unzip -q /tmp/godot.zip -d /tmp/godot; \
    install -m 0755 "/tmp/godot/${name}" /opt/godot

FROM --platform=${JOINERY_PLATFORM} debian:trixie-slim
# The same system fonts Godot finds in the tool workers, so text a test
# measures lays out alike in both.
COPY apt-install.sh /usr/local/bin/apt-install
RUN chmod +x /usr/local/bin/apt-install && apt-install fontconfig fonts-dejavu-core
COPY --from=fetch /opt/godot /usr/local/bin/godot
COPY godot_entrypoint.sh /usr/local/bin/joinery-godot
RUN chmod 0755 /usr/local/bin/joinery-godot \
    && useradd --create-home --shell /bin/bash joiner \
    && godot --headless --version
USER joiner
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/joinery-godot"]
CMD ["godot", "--headless", "--version"]
