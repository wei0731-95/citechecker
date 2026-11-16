FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"
COPY . .
ENV PYTHONUTF8=1
CMD ["bash"]
