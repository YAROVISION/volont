FROM python:3.11-slim

WORKDIR /app

# Zapobigannya buffered output
ENV PYTHONUNBUFFERED=1

# Встановлення залежностей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копіювання вихідних файлів проєкту
COPY . .

EXPOSE 8765

CMD ["python", "server.py"]
