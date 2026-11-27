# A browser VS Code (code-server) with the tools the candidate needs. It holds no repo: the repo named
# by REPO_URL is cloned when the container starts, so one image serves every repo.

FROM codercom/code-server:4.145.2

USER root
# Same tool versions the template's own Dockerfile uses (uv for the backend, bun for the frontend).
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /usr/local/bin/
COPY --from=oven/bun:1 /usr/local/bin/bun /usr/local/bin/bun
RUN ln -s bun /usr/local/bin/bunx
# ripgrep: Todo Tree needs it, and `rg` is handy in the terminal. unzip: for the WakaTime download below.
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep unzip && rm -rf /var/lib/apt/lists/*

# WakaTime's command-line tool, pinned and checked against the digests GitHub publishes for the release.
# With a `wakatime-cli` on the PATH the extension uses it and never downloads or updates one at run time.
ARG TARGETARCH
ARG WAKATIME_CLI_VERSION=2.28.4
ARG WAKATIME_CLI_SHA256_AMD64=e1dfa9fc4960cb0ebed90ee978c921b8ded9c61ffa63e750ddabd2fedb570345
ARG WAKATIME_CLI_SHA256_ARM64=77ee19b58880d6c91037584b2f505c3f3fd4e6d550b3b306e72cbfceb3558355
RUN case "$TARGETARCH" in \
      amd64) sha="$WAKATIME_CLI_SHA256_AMD64" ;; \
      arm64) sha="$WAKATIME_CLI_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/wakatime-cli.zip "https://github.com/wakatime/wakatime-cli/releases/download/v${WAKATIME_CLI_VERSION}/wakatime-cli-linux-${TARGETARCH}.zip" \
 && echo "${sha}  /tmp/wakatime-cli.zip" | sha256sum -c - \
 && unzip -o -j /tmp/wakatime-cli.zip -d /tmp/wakatime-cli \
 && install -m 0755 "/tmp/wakatime-cli/wakatime-cli-linux-${TARGETARCH}" /usr/local/bin/wakatime-cli \
 && rm -rf /tmp/wakatime-cli.zip /tmp/wakatime-cli \
 && wakatime-cli --version
COPY --chmod=0755 entrypoint.sh /usr/local/bin/gremlin-entrypoint

USER coder
RUN mkdir -p /home/coder/workspace /home/coder/.local/share/code-server/User

# Extensions common to every repo (extensions.txt). The build fails if any is missing afterwards.
COPY --chown=coder:coder extensions.txt /tmp/extensions.txt
RUN grep -v '^[[:space:]]*\(#\|$\)' /tmp/extensions.txt | while read -r ext; do \
      code-server --install-extension "$ext" || exit 1; \
    done \
 && code-server --list-extensions --show-versions > /tmp/installed.txt \
 && grep -v '^[[:space:]]*\(#\|$\)' /tmp/extensions.txt | while read -r ext; do \
      grep -qx "$ext" /tmp/installed.txt || { echo "extension missing after install: $ext"; exit 1; }; \
    done \
 && rm /tmp/extensions.txt /tmp/installed.txt
# The template needs Python 3.14. Installing it now keeps `uv sync` fast inside the session.
RUN uv python install 3.14 \
 && ln -s python3.14 /home/coder/.local/bin/python3 \
 && ln -s python3.14 /home/coder/.local/bin/python
ENV PATH="/home/coder/.local/bin:${PATH}"

# Last, so that editing the settings does not reinstall the extensions.
COPY --chown=coder:coder settings.json /home/coder/.local/share/code-server/User/settings.json

WORKDIR /home/coder/workspace
ENTRYPOINT ["/usr/local/bin/gremlin-entrypoint"]
