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

# Start the Panel app
CMD ["panel", "serve", "index.py", \
"--address", "0.0.0.0", "--port", "5006", "--allow-websocket-origin", "*", \
"--args", "from-docker" ]

# TODO set properly the allow-websocket-origin (https://discourse.bokeh.org/t/understanding-the-allow-websocket-origin-option/10636)