# Python 3.11 lightweight image
FROM python:3.11-slim

# Non-root user yaratish
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Ishchi papka
WORKDIR /app

# Kerakli paketlarni o'rnatish
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Requirements ni kopiya qilish
COPY requirements.txt .

# Pip ni yangilash va root warning ni o'chirish
RUN pip install --upgrade pip --no-cache-dir --root-user-action=ignore

# Requirements ni o'rnatish (root warning yo'q)
RUN pip install --no-cache-dir -r requirements.txt --root-user-action=ignore

# Barcha fayllarni kopiya qilish
COPY . .

# Papkalar va fayllar uchun ruxsatlar
RUN mkdir -p uploads && chown -R appuser:appuser /app

# Non-root user ga o'tish
USER appuser

# Port
EXPOSE 10000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:10000/ || exit 1

# Start command
CMD ["gunicorn", "main:app", "--worker-class", "sync", "--workers", "1", "--timeout", "120", "--bind", "0.0.0.0:10000"]
