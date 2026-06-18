# Python 3.11 lightweight image
FROM python:3.11-slim

# Ishchi papka
WORKDIR /app

# Kerakli paketlarni o'rnatish
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Requirements ni kopiya qilish va o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Barcha fayllarni kopiya qilish
COPY . .

# Port
EXPOSE 10000

# Start command
CMD ["gunicorn", "main:app", "--worker-class", "sync", "--workers", "1", "--timeout", "120", "--bind", "0.0.0.0:10000"]
