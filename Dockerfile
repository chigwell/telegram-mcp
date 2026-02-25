# Use an official Python runtime as a parent image (Alpine-based for minimal vulnerabilities)
FROM python:3.13-alpine

# Set the working directory in the container
WORKDIR /app

# Prevent Python from writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
# Ensure Python output is sent straight to terminal (useful for logs)
ENV PYTHONUNBUFFERED=1

# Install system dependencies if needed (e.g., for certain Python packages)
# RUN apt-get update && apt-get install -y --no-install-recommends some-package && rm -rf /var/lib/apt/lists/*

# Copy dependency definition files
# If using Poetry:
# COPY pyproject.toml poetry.lock* ./
# RUN pip install --no-cache-dir poetry
# RUN poetry config virtualenvs.create false && poetry install --no-dev --no-interaction --no-ansi
# If using pip with requirements.txt:
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY main.py .
# COPY session_string_generator.py . # Optional: if needed within the container, otherwise can be run outside

# Create a non-root user and switch to it
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser

# Define environment variables needed by the application
# These should be provided at runtime, not hardcoded (especially secrets)
ENV TELEGRAM_API_ID=""
ENV TELEGRAM_API_HASH=""
# Specify one of the following at runtime:
# Default session filename
ENV TELEGRAM_SESSION_NAME="telegram_mcp_session"
# Or provide the session string directly
ENV TELEGRAM_SESSION_STRING=""

# Expose port for HTTP transport (when TELEGRAM_MCP_HTTP=true)
EXPOSE 8000

# Define the command to run the application
CMD ["python", "main.py"] 