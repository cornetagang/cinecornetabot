FROM python:3.12.9-slim

WORKDIR /app

# Instalar dependencias primero (aprovecha el cache de Docker si no cambian)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del proyecto
COPY . .

# Fly.io setea PORT automaticamente; keep_alive.py ya lo lee de os.environ
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
