FROM python:3.11-slim

LABEL maintainer="evanerwee"
LABEL service="bona"

WORKDIR /app

# Install bona from PyPI + service dependencies
COPY service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code (not the bona library — that comes from PyPI)
COPY service/ /app/service/

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8080"]
