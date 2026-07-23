# ECS deployment with SQLite

This deployment runs Vue/Nginx and FastAPI on one ECS instance. The application
database is a SQLite file on that instance's disk; no RDS instance is needed.

SQLite is a good fit for this single-ECS deployment. Keep exactly one API
container and one Uvicorn worker, as supplied. Do not run multiple ECS
instances against the same database file or put the database on NFS/OSS.

## Deploy

1. Create a Linux ECS instance with a persistent system or data disk. Permit
   inbound TCP 22 from your IP and TCP 80/443 from the Internet. Do not expose
   ports 8000 or 3306.
2. Install Git, Docker Engine, and the Docker Compose plugin, then clone the
   repository on the ECS instance.
3. Prepare the persistent directories and configuration:

   ```bash
   cp .env.production.example .env.production
   mkdir -p backend/data analysis/exports analysis/outputs deploy
   docker run --rm httpd:2.4-alpine htpasswd -nbB admin 'CHOOSE_A_LONG_PASSWORD' \
     > deploy/.htpasswd
   chmod 600 .env.production deploy/.htpasswd
   ```

4. If you have an existing SQLite file, copy it to
   `backend/data/kpl_bp.db` before the first start. Otherwise the API creates
   an empty database and you can populate it through `/management`.
5. Build and start:

   ```bash
   docker compose -f docker-compose.production.yml up -d --build
   docker compose -f docker-compose.production.yml ps
   curl --fail http://127.0.0.1/health
   ```

Nginx serves the frontend on port 80, proxies API requests internally, and
protects management and data-changing endpoints with HTTP Basic authentication.

## Updating and backing up

Update the application:

```bash
git pull --ff-only
docker compose -f docker-compose.production.yml up -d --build
docker image prune -f
```

For a consistent backup, stop the API first, copy the database and artifacts,
then start it again:

```bash
docker compose -f docker-compose.production.yml stop api
cp backend/data/kpl_bp.db /safe/backup/location/kpl_bp-$(date +%F).db
tar -czf /safe/backup/location/kpl-artifacts-$(date +%F).tgz \
  analysis/exports analysis/outputs
docker compose -f docker-compose.production.yml start api
```

Keep database and artifact backups outside the ECS disk, such as in OSS. For
HTTPS, terminate TLS at Alibaba Cloud CDN or an Application Load Balancer and
use the ECS port 80 service as its origin.
