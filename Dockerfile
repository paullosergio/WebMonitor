# Use Python 3.12 slim
FROM python:3.12-slim

# Definir diretório de trabalho
WORKDIR /app

# Copiar arquivo de dependências primeiro (para cache do Docker)
COPY requirements.txt .

# Instalar dependências
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

# Copiar o restante do código
COPY . .

# Expor a porta usada pelo Flask
EXPOSE 8080

# Rodar o app via gunicorn (mais seguro que flask run)
CMD ["gunicorn", "-b", "0.0.0.0:8080", "main:app"]
