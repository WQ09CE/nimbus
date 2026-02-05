# Harbor Task Environment for bug-fix-test
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

# Pre-create the buggy file
RUN cat > calculator.py << 'PYEOF'
class Calculator:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return b - a  # BUG: reversed operands

    def multiply(self, a, b):
        return a + b  # BUG: should be a * b

    def divide(self, a, b):
        return a / b  # BUG: no zero division handling
PYEOF

# Default command
CMD ["/bin/bash"]
