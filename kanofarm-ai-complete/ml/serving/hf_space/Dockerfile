FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
COPY server.py .
COPY model ./model
ENV MODEL_DIR=/srv/model
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
