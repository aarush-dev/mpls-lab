# SD-WAN controller image. Build context = /root/LAB (repo root).
#   docker build -t noc-controller -f controller/Dockerfile .
#
# ponytail: apt docker.io ships dockerd/proxy/init but NO `docker` CLI binary.
#   COPY --from=docker:cli pulls just the static client at build time (egress OK);
#   zero runtime daemon, zero apt layer. One line beats a 50 MB package install.
FROM docker:cli AS docker-cli
FROM python:3.11-slim
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN pip install --no-cache-dir pyyaml
WORKDIR /app
COPY controller/topo.py controller/controller.py /app/controller/
COPY trafficgen/diurnal.py /app/trafficgen/
COPY topology-spec.yaml /app/topology-spec.yaml
ENV TOPO_SPEC=/app/topology-spec.yaml
EXPOSE 9362
CMD ["python3", "/app/controller/controller.py", "--port", "9362"]
