FROM python:3.11-slim

# Installation des dépendances système (Tesseract + Poppler pour pdf2image)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-fra \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installation des paquets Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source
COPY . .

# Commande par défaut (garde le conteneur en vie pour exécuter des commandes)
CMD ["tail", "-f", "/dev/null"]
