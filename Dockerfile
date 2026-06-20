FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# store.json и прочие данные живут здесь (монтируется volume)
ENV DATA_DIR=/data
VOLUME ["/data"]

CMD ["python", "-u", "bridge.py"]
