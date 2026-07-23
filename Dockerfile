FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml requirements.txt ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV PORT=8080
EXPOSE 8080

CMD ["ynab-mcp-server-http"]
