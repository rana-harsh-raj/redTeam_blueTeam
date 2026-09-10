FROM python:3.12-alpine
RUN adduser -D -u 10001 stub && mkdir -p /data && chown stub:stub /data
WORKDIR /app
COPY _common/ /app/_common/
COPY api-ingress/server.py /app/server.py
COPY api-ingress/contract/routes.json /app/contract/routes.json
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app STUB_NAME=api-ingress STUB_PORT=8080 INGRESS_CONTRACT_FILE=/app/contract/routes.json
USER stub
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --retries=10 CMD wget -q -O- http://127.0.0.1:8080/health || exit 1
CMD ["python3", "/app/server.py"]
