FROM qdrant/qdrant:v1.18.3@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286

USER root
RUN test -d /qdrant/static \
    && test -f /qdrant/static/qdrant-web-ui.spdx.json \
    && rm -rf /qdrant/static \
    && install -d -o 0 -g 0 -m 0755 /qdrant/static

RUN apt-get update \
    && apt-get install --yes --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*
