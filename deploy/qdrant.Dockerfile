FROM qdrant/qdrant:v1.18.2@sha256:75eab8c4ba42096724fdcfde8b4de0b5713d529dde32f285a1f86fdcb2c9e50c

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*
