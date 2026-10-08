# docker build -t brickagent .
# Add an agent CLI in a derived image; see README.md.
FROM python:3.14.7-slim-trixie

ARG BRICKNET="bricknet>=0.1.1"
ARG LDRAW=https://codeload.github.com/kulits/ldraw-parts/tar.gz/b61b905f1173f120f528be9521bb870619c36785
ARG NODE=22.23.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    BRICKNET_DATA=/opt/bricknet \
    BRICKNET_CATALOG=v1 \
    BRICKAGENT_LDRAW=/opt/ldraw \
    EGL_PLATFORM=surfaceless \
    LIBGL_ALWAYS_SOFTWARE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git xz-utils \
        libstdc++6 libgomp1 libgl1 libegl1 libgles2 libegl-mesa0 libgl1-mesa-dri \
    && curl -fsSL "https://nodejs.org/dist/v${NODE}/node-v${NODE}-linux-x64.tar.xz" \
        | tar -xJ -C /usr/local --strip-components=1 --exclude='*/README.md' --exclude='*/CHANGELOG.md' \
    && mkdir /opt/ldraw \
    && curl -fsSL "$LDRAW" | tar -xz -C /opt/ldraw --strip-components=2 --wildcards '*/ldraw/parts' '*/ldraw/p' \
        '*/ldraw/LDConfig.ldr' '*/ldraw/CA*.txt' \
    && python -m pip install --no-cache-dir "${BRICKNET}" numpy scipy pybullet==3.2.7 \
    && python -m bricknet fetch-meshes \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY src/brickagent /opt/brickagent
RUN mkdir /opt/viewer \
    && cp /opt/brickagent/viewer/package.json /opt/brickagent/viewer/package-lock.json /opt/viewer/ \
    && npm ci --omit=dev --no-audit --no-fund --prefix /opt/viewer \
    && npm cache clean --force \
    && ln -s /opt/viewer/node_modules /node_modules \
    && echo /opt > "$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/brickagent.pth"

RUN cd /tmp && python -c "\
from brickagent import Assembly, check, view; \
scene = Assembly('image test'); \
base = scene.add('3005', color='red'); \
scene.attach('3005', to=base.stud[0], by=('hole', 0), color='red'); \
check(scene); \
assert view(scene, '/tmp/preview.png').stat().st_size > 0"

WORKDIR /work
