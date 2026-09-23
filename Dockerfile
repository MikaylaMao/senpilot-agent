FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py ./

ENV PYTHONUNBUFFERED=1 HEADLESS=true
CMD ["python", "agent.py"]
