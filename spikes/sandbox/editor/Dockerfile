# Spike image: the OpenCode sandbox plus a browser editor (code-server). Throwaway, never promoted (spec 11).
#
# The editor binary comes from a release asset whose SHA-256 is checked at build time, so nothing is
# fetched when the sandbox runs. It is Coder's code-server (MIT), which uses Open VSX and never
# Microsoft's marketplace. The build context is made by `rig.py build-editor`, which adds a copy of
# src/workspace_tools so that `submit` works inside the sandbox.
ARG BASE
FROM ${BASE}

USER root
ARG CODE_SERVER_VERSION
ARG CODE_SERVER_SHA256
RUN curl -fsSL -o /tmp/code-server.tgz "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-amd64.tar.gz" \
 && echo "${CODE_SERVER_SHA256}  /tmp/code-server.tgz" | sha256sum -c - \
 && mkdir /opt/code-server \
 && tar -xzf /tmp/code-server.tgz -C /opt/code-server --strip-components=1 \
 && rm /tmp/code-server.tgz \
 && ls /opt/code-server /opt/code-server/lib | head -30

# This build ships GitHub Copilot as a built-in extension, which gives the editor its own AI chat. The
# only assistant in a session is the one we provide, so the extension is removed from the image. The
# image is read-only at run time, so a candidate cannot put it back. `chat.disableAIFeatures` in the
# settings hides what is left of the chat views.
RUN rm -rf /opt/code-server/lib/vscode/extensions/copilot

# `submit` needs PyYAML, which the problem's own environment does not have. It goes in a folder of
# its own so that it cannot change what the candidate's code imports.
ARG PYYAML_VERSION=6.0.3
RUN python -m pip install --no-cache-dir --target /opt/gassess/lib "pyyaml==${PYYAML_VERSION}"
COPY workspace_tools /opt/gassess/workspace_tools
COPY --chmod=0755 start-editor submit /opt/gassess/bin/
COPY --chmod=0644 settings.json /opt/gassess/editor-settings.json
# A checkout with Windows line endings must not break the shell scripts.
RUN sed -i 's/\r$//' /opt/gassess/bin/start-editor /opt/gassess/bin/submit \
 && ln -s /opt/gassess/bin/submit /usr/local/bin/submit

USER candidate
ENV HOME=/home/candidate
WORKDIR /workspace
