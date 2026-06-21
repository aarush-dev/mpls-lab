# Trafficgen image. Build context = /root/LAB (repo root).
#   docker build -t noc-trafficgen -f trafficgen/Dockerfile .
#
# ponytail: apt docker.io ships dockerd/proxy/init but NO `docker` CLI binary.
#   COPY --from=docker:cli pulls just the static client at build time (egress OK);
#   zero runtime daemon, zero apt layer. One line beats a 50 MB package install.
FROM docker:cli AS docker-cli
FROM python:3.11-slim
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN apt-get update \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pyyaml
WORKDIR /app
COPY trafficgen/trafficgen.py trafficgen/diurnal.py /app/trafficgen/
COPY controller/topo.py /app/controller/
COPY topology-spec.yaml /app/topology-spec.yaml
ENV TOPO_SPEC=/app/topology-spec.yaml
ENV TRAFFICGEN_BACKEND=nc
CMD ["python3", "/app/trafficgen/trafficgen.py", "--backend", "nc", "--interval", "30"]
