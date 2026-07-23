# Alibaba Cloud deployment

This deployment uses:

- one Linux ECS instance for the Vue/Nginx and FastAPI containers;
- ApsaraDB RDS for MySQL for the application database;
- ECS disk storage for generated files under `analysis/exports` and
  `analysis/outputs`.

Keep ECS and RDS in the same Alibaba Cloud region and VPC. Use the RDS internal
endpoint, add the ECS private IP to the RDS whitelist, and do not request a
public RDS endpoint for the application.

## 1. Create the cloud resources

1. Create a VPC and vSwitch.
2. Create an ApsaraDB RDS for MySQL instance in that VPC.
3. Create the `kpl_lab` database with `utf8mb4` and a dedicated `kpl_app`
   account scoped to that database.
4. Create a Linux ECS instance in the same region and VPC.
5. Add the ECS private IP to the RDS IP whitelist.
6. In the ECS security group, allow inbound TCP 22 from your own IP and TCP
   80/443 from the Internet. Do not expose TCP 3306 or 8000.
7. Attach a data disk or choose an ECS system disk large enough for the
   generated artifacts and backups. The current local artifacts use about
   225 MB and will grow as seasons are added.

## 2. Prepare ECS

Install Git, Docker Engine, and the Docker Compose plugin using the package
instructions for the ECS operating system. Then clone this repository and run:

```bash
cd kpl
cp .env.production.example .env.production
mkdir -p analysis/exports analysis/outputs deploy
```

Edit `.env.production` and set `DATABASE_URL` to the RDS **internal** endpoint.
Percent-encode special characters in the password. For example, `@` becomes
`%40`.

Create the password protecting `/management` and all data-changing APIs:

```bash
docker run --rm httpd:2.4-alpine htpasswd -nbB admin 'CHOOSE_A_LONG_PASSWORD' \
  > deploy/.htpasswd
chmod 600 .env.production deploy/.htpasswd
```

Start the application:

```bash
docker compose -f docker-compose.production.yml up -d --build
docker compose -f docker-compose.production.yml ps
curl --fail http://127.0.0.1/health
```

The API container creates missing tables during startup. It never drops or
alters existing tables.

## 3. Move the existing MySQL data

Run `mysqldump` against the local MySQL database, copy the dump to ECS, and
import it through the RDS internal endpoint:

```bash
mysqldump --single-transaction --routines --triggers \
  -h 127.0.0.1 -u kpl_app -p kpl_lab > kpl_lab.sql

mysql -h RDS_INTERNAL_ENDPOINT -u kpl_app -p \
  --default-character-set=utf8mb4 kpl_lab < kpl_lab.sql
```

Do not place the dump or either password in Git. Remove the dump from ECS after
you have verified the row counts and taken an RDS backup.

The JSONL exports and generated models are separate from MySQL. Either
regenerate them from the protected Management page after import, or copy the
two season directories to ECS:

```bash
rsync -av analysis/exports/ ECS_USER@ECS_PUBLIC_IP:/path/to/kpl/analysis/exports/
rsync -av analysis/outputs/ ECS_USER@ECS_PUBLIC_IP:/path/to/kpl/analysis/outputs/
```

## 4. Domain and HTTPS

Point the domain at the public entry point. The simplest path with the supplied
HTTP-only container is to put Alibaba Cloud CDN or an Application Load Balancer
in front of ECS and terminate TLS there using Certificate Management Service.
Configure the origin as the ECS HTTP port 80, enable an HTTP-to-HTTPS redirect
at the public entry point, and restrict origin access where the selected
service supports it. Direct TLS termination in the container requires adding a
443 server block plus read-only certificate mounts.

If the site is served from a server in the Chinese mainland, complete the
required ICP filing before making the public website available.

## 5. Updates and backups

Deploy an update from the repository directory:

```bash
git pull --ff-only
docker compose -f docker-compose.production.yml up -d --build
docker image prune -f
```

Enable automatic RDS backups. Also back up `analysis/exports` and
`analysis/outputs` (for example, to OSS) because those files live on the ECS
disk and are not part of an RDS snapshot.

Useful diagnostics:

```bash
docker compose -f docker-compose.production.yml logs --tail=200 api
docker compose -f docker-compose.production.yml logs --tail=200 web
docker compose -f docker-compose.production.yml exec api \
  python -c "from app.database import engine; print(engine.url.render_as_string(hide_password=True))"
```
