FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY inference.py server.py ./
COPY model.onnx model_card.json label_meta.json ./model/
ENV MODEL_DIR=/srv/model
# Render sets $PORT; 10000 is its default
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-10000}"]
