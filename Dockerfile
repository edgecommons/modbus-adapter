# Container image for the Modbus adapter on Kubernetes.
#
# The ggcommons Python library is resolved from requirements.txt (`greengrass-commons`, from a
# registry/PyPI or a pip git+https dep). Build from this directory, load/push, then set `image:`
# in k8s/deployment.yaml:
#   docker build -t ghcr.io/<owner>/modbus-adapter:latest .
#   docker push ghcr.io/<owner>/modbus-adapter:latest      # or: kind load docker-image ...
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first (layer caching). requirements.txt lists greengrass-commons + pymodbus.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Entry point + the adapter package.
COPY main.py /app/main.py
COPY modbus_adapter /app/modbus_adapter

USER 65532:65532

ENTRYPOINT ["python3", "/app/main.py"]
# No default args: with --platform auto the library detects KUBERNETES from the SA token
# (config source -> CONFIGMAP at /etc/ggcommons, transport -> MQTT, identity -> Downward API).
