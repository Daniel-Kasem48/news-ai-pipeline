FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY newsai ./newsai
COPY fixtures ./fixtures
EXPOSE 8080
CMD ["python", "-m", "newsai.server"]
