FROM python:3.12-slim
WORKDIR /srv
COPY ml/serving/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ml/serving/server.py .
COPY model ./model
ENV MODEL_DIR=/srv/model
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
