# Spike image: a candidate sandbox with the agent installed. Throwaway, never promoted (spec 11).
#
# It extends a package's environment image (toolchain and dependencies) with the agent. The agent
# binary comes from a release asset whose SHA-256 is checked at build time, so nothing is fetched
# when the sandbox runs. The system packages here are not pinned yet. A real session image pins them.
ARG BASE
FROM ${BASE}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git ripgrep procps iproute2 \
 && rm -rf /var/lib/apt/lists/*

ARG OPENCODE_VERSION
ARG OPENCODE_ASSET
ARG OPENCODE_SHA256
RUN curl -fsSL -o /tmp/opencode.tgz "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/${OPENCODE_ASSET}" \
 && echo "${OPENCODE_SHA256}  /tmp/opencode.tgz" | sha256sum -c - \
 && mkdir /opt/opencode \
 && tar -xzf /tmp/opencode.tgz -C /opt/opencode \
 && rm /tmp/opencode.tgz \
 && echo "--- files in the release asset:" && find /opt/opencode -maxdepth 2 | head -20 \
 && ln -s /opt/opencode/opencode /usr/local/bin/opencode

# The workspace is a bind mount owned by someone else, so git must not refuse it. The environment
# image already created the unprivileged user `candidate` (uid 1000) and the /workspace folder.
RUN git config --system --add safe.directory '*'

USER candidate
ENV HOME=/home/candidate
WORKDIR /workspace
