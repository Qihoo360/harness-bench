FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS task-toolchain

ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG PIP_TRUSTED_HOST
COPY docker/openclaw/requirements.lock /tmp/harnessbench-requirements.lock
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
      --requirement /tmp/harnessbench-requirements.lock \
    && rm /tmp/harnessbench-requirements.lock

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3

ARG AGY_VERSION=1.3.3
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git libcap2-bin libsqlite3-0 ripgrep sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Antigravity CLI binary
RUN curl -fsSL "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.3.3-5524738307653632/linux-x64/cli_linux_x64.tar.gz" -o /tmp/agy.tar.gz \
    && tar -xzf /tmp/agy.tar.gz -C /usr/local/bin antigravity \
    && mv /usr/local/bin/antigravity /usr/local/bin/agy \
    && chmod 0755 /usr/local/bin/agy \
    && rm -f /tmp/agy.tar.gz \
    && /usr/local/bin/agy --version

# BEGIN MULTIMODAL FRONTEND AGENT TOOLCHAIN
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_PATH=/usr/local/lib/node_modules:/opt/frontend-deps/node_modules \
    npm_config_cache=/opt/npm-cache
RUN npm install --global playwright@1.55.0 \
    && playwright install --with-deps chromium \
    && mkdir -p /opt/frontend-deps /opt/npm-cache /ms-playwright /home/node/.cache \
    && cd /opt/frontend-deps \
    && npm init -y \
    && npm install --package-lock=false \
       @vitejs/plugin-react@4.3.4 \
       vite@5.4.11 \
       typescript@5.6.3 \
       react@18.3.1 \
       react-dom@18.3.1 \
       lucide-react@0.468.0 \
       @types/react@18.3.12 \
       @types/react-dom@18.3.1 \
    && chromium_path="$(find /ms-playwright -path '*/chrome-linux/chrome' -type f -print -quit)" \
    && ln -sf "$chromium_path" /usr/local/bin/chromium \
    && chown -R 1000:1000 /opt/frontend-deps /opt/npm-cache /ms-playwright /home/node/.cache \
    && npm cache verify \
    && rm -rf /var/lib/apt/lists/*
COPY docker/harnesses/multimodal-npm /opt/harnessbench/frontend-bin/npm
RUN chmod 0755 /opt/harnessbench/frontend-bin/npm
ENV PATH="/opt/harnessbench/frontend-bin:${PATH}"
# END MULTIMODAL FRONTEND AGENT TOOLCHAIN

COPY --from=task-toolchain /usr/local/ /usr/local/
COPY docker/openclaw/verify_toolchain.py /opt/harnessbench/verify_toolchain.py
COPY docker/harnesses/antigravity/entrypoint.sh /opt/harness/bin/run
COPY docker/harnesses/antigravity/patch_litellm.py /tmp/patch_litellm.py

RUN ln -sf /usr/local/bin/python3 /usr/bin/python3 \
    && python /opt/harnessbench/verify_toolchain.py --python-only \
    && python -m venv /opt/litellm-venv \
    && /opt/litellm-venv/bin/pip install --no-cache-dir 'litellm[proxy]==1.105.0' \
    && /opt/litellm-venv/bin/python /tmp/patch_litellm.py \
    && rm -f /tmp/patch_litellm.py \
    && chmod -R a+rX /opt/litellm-venv \
    && setcap -r /usr/local/bin/node 2>/dev/null || true \
    && chmod 0755 /opt/harness/bin/run \
    && chmod -R a+rX /opt/harness/bin

# BEGIN ANTIGRAVITY LOGIN-SHELL FRONTEND PATH
RUN printf '%s\n' \
    'export PATH="/opt/harnessbench/frontend-bin:/usr/local/bin:$PATH"' \
    > /etc/profile.d/harnessbench-path.sh \
    && chmod 0644 /etc/profile.d/harnessbench-path.sh
# END ANTIGRAVITY LOGIN-SHELL FRONTEND PATH

WORKDIR /workspace
USER 1000:1000
ENTRYPOINT ["/opt/harness/bin/run"]
