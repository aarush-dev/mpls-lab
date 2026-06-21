# Standalone SD-WAN controller image. Compose wiring is Phase 2 (not here).
# ponytail: slim python base + PyYAML; everything else is stdlib. For the `tc`
#   netem read to work in-lab, run with access to the host net namespaces
#   (--net=host or shared netns); wiring is Phase 2 scope.
FROM python:3.11-slim
RUN pip install --no-cache-dir pyyaml && apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY controller/topo.py controller/controller.py /app/controller/
COPY trafficgen/diurnal.py /app/trafficgen/
COPY topology-spec.yaml /app/topology-spec.yaml
ENV TOPO_SPEC=/app/topology-spec.yaml
EXPOSE 9362
CMD ["python3", "/app/controller/controller.py", "--port", "9362"]
