FROM python:3.12-alpine
RUN pip install --no-cache-dir PyMySQL==1.1.1 && adduser -D -u 10001 stub
WORKDIR /app
COPY _common/ /app/_common/
COPY monolith-stub/server.py /app/server.py
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app STUB_NAME=monolith-stub STUB_PORT=8080
USER stub
EXPOSE 8080
CMD ["python3", "/app/server.py"]
