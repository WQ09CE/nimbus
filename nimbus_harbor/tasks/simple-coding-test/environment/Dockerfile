# Harbor Task Environment for Nimbus testing
FROM python:3.12-slim

# Set working directory
WORKDIR /workspace

# Install basic tools
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create logs directory
RUN mkdir -p /logs/verifier

# Default command
CMD ["/bin/bash"]
