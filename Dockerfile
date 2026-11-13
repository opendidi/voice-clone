FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-moss-tts.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-moss-tts.txt

COPY app.py wsgi.py ./
COPY app ./app
COPY scripts ./scripts

RUN mkdir -p /app/var /app/third_party

EXPOSE 5002 18090
CMD ["python", "wsgi.py"]
