FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libreoffice \
    libreoffice-writer \
    libreoffice-calc \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p temp
RUN mkdir -p data

ENV PYTHONUNBUFFERED=1

EXPOSE 10000

CMD ["python", "main.py"]
