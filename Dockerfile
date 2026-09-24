FROM python:3.12-slim

WORKDIR /code

COPY pyproject.toml .
COPY app ./app
COPY jobs ./jobs
RUN pip install --no-cache-dir -e ".[dev]"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
