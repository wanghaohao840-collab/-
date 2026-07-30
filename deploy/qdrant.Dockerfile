FROM qdrant/qdrant:v1.18.2

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*
