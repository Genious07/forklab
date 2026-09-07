# Deployment

```bash
cp .env.example .env      # set POSTGRES_PASSWORD
docker compose up --build
```

The API listens on `127.0.0.1:8000`, deliberately bound to loopback. Put a
reverse proxy in front of it to terminate TLS before exposing it.

Before exposing this to anyone outside your machine, read
[../docs/limitations.md](../docs/limitations.md). Authentication is a stub:
`current_org` resolves from a request header, so anyone who can reach the API
can choose their own organization id.

The `psycopg` driver ships in the API dependencies, so the Postgres URL in
`compose.yaml` works without extra steps. A single machine deployment can drop
the `db` service and keep the default SQLite path instead.

Docker packaging is not by itself an isolation guarantee. See
[Docker security](https://docs.docker.com/engine/security/).
