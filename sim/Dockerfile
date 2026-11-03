# Parrot Sphinx simulator on Ubuntu 22.04, for an Arch (or any non-Ubuntu) host.
# The host must load ulogger.ko (sim/build-ulogger.sh); the container shares its kernel.
# Build: docker build -t anafi-sphinx:2.25.2 sim/
#
# Ubuntu 22.04, not 24.04: the Anafi 4K PC firmware (1.10.4, 2023) is built on glibc 2.35,
# and firmwared lends it the container's graphics libraries (EGL, GLES, X11). Ubuntu 24.04's
# need glibc 2.38, so the firmware's camera plugin can't load, the vertical camera reports
# "defective" and the autopilot forbids takeoff.
FROM ubuntu:22.04

# Sphinx licence (free for software testing; anonymous usage statistics sent to
# Parrot; no reverse engineering) accepted by the project owner on 2026-11-03.
# Full text: debconf template sphinx/license_approval in the parrot-sphinx package.
ARG SPHINX_VERSION=2.25.2*
ENV DEBIAN_FRONTEND=noninteractive \
    NVIDIA_DRIVER_CAPABILITIES=all

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg lsb-release sudo kmod \
    && curl -fsSL https://debian.parrot.com/gpg | gpg --dearmor \
        -o /usr/share/keyrings/debian.parrot.com.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/debian.parrot.com.gpg] https://debian.parrot.com/ jammy main generic" \
        > /etc/apt/sources.list.d/debian.parrot.com.list \
    && apt-get update \
    && echo "parrot-sphinx sphinx/license_approval boolean true" | debconf-set-selections \
    && apt-get install -y --no-install-recommends \
        "parrot-sphinx=${SPHINX_VERSION}" parrot-ue4-empty \
    && rm -rf /var/lib/apt/lists/*

# Runtime extras the Sphinx scripts need but don't declare: python3 for sphinx-cli, json-c
# and libpython for its plugins, file for firmwared, libgles2 for the firmware's camera
# plugin (libpimpsphinxanafi.so needs libGLESv2.so.2 from the container), libvulkan1 for
# the 3D world ("Failed to load Vulkan Driver" without it; Docker's --gpus adds only the ICD).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-yaml libjson-c5 libpython3.10 file libgles2 libvulkan1 \
    && rm -rf /var/lib/apt/lists/*

# 3D world with ground everywhere (hilly, ~3.3 GB). "empty" (installed above with Sphinx)
# has ground only at the spawn point: a drone landing elsewhere sinks forever.
# Worlds must match the Sphinx version: Parrot's "meadow" is still a 2.15.1 build and
# never connects to Sphinx 2.25.2 (both sides wait for each other).
RUN apt-get update && apt-get install -y --no-install-recommends \
        "parrot-ue4-landscape-mountains=${SPHINX_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# Sphinx refuses to run as root. "sim" gets uid 1000 (usually the host user) so
# run logs in sim/cache/runs stay owned by you; firmwared group = control socket.
RUN (userdel -r ubuntu 2>/dev/null || true) \
    && useradd -m -u 1000 -G firmwared,video,sudo -s /bin/bash sim \
    && echo "sim ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sim

CMD ["bash"]
