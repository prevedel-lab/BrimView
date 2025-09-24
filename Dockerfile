# Use a lightweight Python base image
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy your app code
COPY src/index.py /app/
COPY src/BrimView.png /app/src/
COPY BrimView-widgets /app/BrimView-widgets/

# Install Python deps
RUN pip install --upgrade pip
RUN pip install --no-cache-dir "./BrimView-widgets[processing, remote-store]"

# Expose port
EXPOSE 5006

# create a non root user
RUN useradd -m panel -d /home/panel
USER panel

ARG ADMIN_ENDPOINT
ENV ADMIN_ENDPOINT=$ADMIN_ENDPOINT

# Start the Panel app
# see https://discourse.bokeh.org/t/understanding-the-allow-websocket-origin-option/10636 for allow-websocket-origin
CMD ["/bin/sh", "-c", "panel serve index.py --index=index\
  --liveness --liveness-endpoint healthz \
  --address 0.0.0.0 --port 5006 --allow-websocket-origin 'brimview.embl.org' \
  --admin --admin-endpoint \"$ADMIN_ENDPOINT\" --admin-log-level debug \
  --reuse-sessions --global-loading-spinner \
  --args from-docker"]