FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY office_weather_servers.py .

# FastMCP reads FASTMCP_HOST / FASTMCP_PORT from the environment.
# Binding to 0.0.0.0 is required so the server is reachable from
# outside the container (the code's default is 127.0.0.1).
ENV FASTMCP_HOST=0.0.0.0 \
    FASTMCP_PORT=8001

EXPOSE 8001

CMD ["python", "office_weather_servers.py"]
