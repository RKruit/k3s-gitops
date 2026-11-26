FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY logic_sync.py .
CMD ["python", "-u", "logic_sync.py"]
