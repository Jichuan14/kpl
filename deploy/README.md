# ECS deployment with SQLite

This deployment runs Vue/Nginx, FastAPI, RabbitMQ, and one Celery pipeline
worker on one ECS instance. The application database is a SQLite file on that
instance's disk; no RDS instance is needed.

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
   # Generate a hex password and replace both RabbitMQ placeholders with it.
   openssl rand -hex 24
   mkdir -p backend/data analysis/exports analysis/outputs deploy
   docker run --rm httpd:2.4-alpine htpasswd -nbB admin 'CHOOSE_A_LONG_PASSWORD' \
     > deploy/.htpasswd
   chmod 600 .env.production deploy/.htpasswd
   ```

   On a small instance, create host swap before starting the containers. The
   production Compose file limits each Python container to 1 GiB of physical
   RAM and 2.5 GiB total RAM plus swap. The worker and RabbitMQ raise peak
   memory use; measure it during a full update on the target host:

   ```bash
   sudo fallocate -l 2G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   swapon --show
   ```

   Run these commands only once. If `/swapfile` already appears in
   `swapon --show`, do not recreate it or add another `fstab` entry.

   To enable the optional SwiftBar visitor widget, generate a separate token
   with `openssl rand -hex 32` and set it as `ANALYTICS_WIDGET_TOKEN` in
   `.env.production`. The corresponding Mac setup is documented in
   [`macos/swiftbar/README.md`](../macos/swiftbar/README.md).

4. If you have an existing SQLite file, copy it to
   `backend/data/kpl_bp.db` before the first start. Otherwise the API creates
   an empty database and you can populate it through `/management`.
5. Build and start:

   ```bash
   docker compose -f docker-compose.production.yml up -d --build
   docker compose -f docker-compose.production.yml ps
   curl --fail http://127.0.0.1/health
   ```

   Run one manual Full update from `/management` and watch
   `docker stats` and `free -h` on the ECS host. Confirm the worker completes
   without an OOM restart before relying on the 03:00 schedule.

The API and worker share SQLite, exports, outputs, and published data mounts.
Management actions create persisted jobs; the single worker processes them
under a shared file lock. RabbitMQ keeps durable quorum-queue messages on its
own volume.
If broker delivery fails, the committed SQLite job stays pending and a
30-second recovery sweep dispatches it later. Jobs have up to three attempts,
heartbeats, and a visible failure state. Pipeline commands use isolated process
groups so a timeout terminates nested trainer processes.

Caddy accepts public traffic on ports 80 and 443, automatically obtains and
renews HTTPS certificates for `kpllab.xyz` and `www.kpllab.xyz`, and proxies
requests to the internal Nginx frontend. Nginx proxies API requests internally
and protects management and data-changing endpoints with HTTP Basic
authentication.

## Updating and backing up

Update the application:

```bash
git pull --ff-only
docker compose -f docker-compose.production.yml up -d --build
docker image prune -f
```

## Scheduled refresh

### Small-host operation and model training

Automatic model training is enabled by default in both the application and
`.env.production.example`. On an existing server, set
`AUTO_MODEL_TRAINING_ENABLED=true` in `.env.production` (or remove an old false
override) to enable it. Recreate the API and worker after deploying this setting:

```bash
docker compose -f docker-compose.production.yml up -d --build api worker
```

For temporary deferral, set `AUTO_MODEL_TRAINING_ENABLED=false`.
Scheduled refresh, Full update, and the queued `analysis/all` action still sync
where applicable, rebuild factual season statistics, and publish them. They
defer the automatic global model refit and retain the existing active model
with its original version and training coverage. Completed jobs explicitly show
“Data updated; model training deferred” and record the reason in `result.model_update`.
Missing models remain unavailable. Explicit individual training actions still
run training; do not use those on this host until their peak usage is measured.
Explicit false overrides remain effective until removed or changed; uploading
new code does not replace the server's existing `.env.production`.

The RabbitMQ rollout also introduced a new global training path: the old cron
was pinned to Season 3, while current jobs discover started seasons and may
train on all eligible historical series. Compare actual work, not just broker
overhead. The API and worker each have a 1 GiB ceiling; these are not reserved
RAM and their combined peaks plus RabbitMQ can exceed host capacity. A worker
child can be OOM-killed while Celery remains running. Inspect kernel/cgroup
events even if Docker does not show a terminated container.

Reference preparation now streams decisions and output relabeling instead of
retaining the complete corpus alongside child analyzers. This reduces a known
overlap but is not proof that the complete neural refit fits 1 GiB. Monitor
the first full training run on the small host to verify adequate headroom.
More swap or a higher worker RAM limit alone does not protect website latency.

Production refits now run input hashing, references, base neural training,
familiarity training, ban training, lineup training and catalog export in fresh
processes. Exporters start after the training process exits. Corpus coverage is
counted as a stream; neural preparation retains only consumed input fields.
Freed preparation allocations are also returned where Linux/glibc supports it.
This releases process-owned memory between stages without reducing the corpus,
epochs, historical-context rules, recipe or activation checks. Production
training writes `analysis/outputs/models/candidates/training_memory.jsonl`;
follow it while the worker runs to see stage names, individual process peaks
and available Linux cgroup memory/swap/OOM/pressure readings. Diagnostics
also record available host RAM, swap and host memory/I/O pressure to distinguish
container limits from host pressure. A failed stage
aborts candidate activation. A container kill can prevent its final log record.

To measure a complete update using the local database and exports, run:

```bash
backend/.venv/bin/python deploy/profile-update.py
```

This profiles official sync, factual analysis/publication, all three 30-epoch
neural stages, ban/lineup models and validated model activation in a temporary
snapshot. It copies current tracked working-tree edits and backs up SQLite;
it never redirects the original active-model pointer. Reports go to
`analysis/outputs/memory_profiles/`. The snapshot omits credentials and existing
model versions so a genuine refit is exercised instead of a no-change shortcut.
`--offline` explicitly skips network sync and labels that limitation.

For a Linux container test using production's 1 GiB RAM, 2500 MiB RAM-plus-swap,
1.5 CPU and 256-process limits, build the training image and use:

```bash
docker build -f backend/Dockerfile -t kpl-memory-profile:local .
backend/.venv/bin/python deploy/profile-update.py --docker-image kpl-memory-profile:local --interval 2
```

The Docker profiling driver uses only the Python standard library; on a server
without a host virtual environment, `python3 deploy/profile-update.py
--docker-image kpl-memory-profile:local --interval 2` also works. Training
dependencies come from the image.

The report includes kernel cgroup peak memory and sampled swap, OOM events,
pressure and container status. A small sampling process is included in measured
container memory. Native process-tree RSS can double-count shared pages and
does not include charged file cache, so use the Linux cgroup test when checking
the container cap. Neither test includes the separate API, RabbitMQ, web or OS
memory budgets. Local Docker architecture and swap availability can differ
from the server; repeat on a staging host with the server's actual corpus.
The tool leaves the stopped diagnostic container and temporary snapshot for
inspection; remove the named container and snapshot after collecting results.

### Install the daily trigger

Install the repository-managed refresh script. It submits one idempotent job
per China calendar day. The worker refreshes the official league catalog and
selects the newest league that has started and has a completed match. A future
announced league remains ineligible until play begins. Set `KPL_LEAGUE_ID`
only to pin an emergency run to a particular official league. The job starts
at 03:00 China time when the worker is healthy; the site updates after analysis
and publishing finish. Confirm the host cron honors `CRON_TZ` (or use 19:00 UTC
on a UTC host):

```bash
sudo install -m 0755 deploy/kpl-refresh /usr/local/sbin/kpl-refresh
```

The script reads HTTP Basic credentials from `/etc/kpl-sync.netrc` by default.
Its optional league override, API URL, and credential path are
`KPL_LEAGUE_ID`, `KPL_API_URL`, and `KPL_AUTH_FILE`. A typical root crontab is:
Remove any old `KPL_LEAGUE_ID` assignment from the scheduled environment so
automatic season discovery can take effect.

```cron
CRON_TZ=Asia/Shanghai
0 3 * * * /usr/bin/flock -n /var/lock/kpl-refresh.lock /usr/local/sbin/kpl-refresh >> /var/log/kpl-sync.log 2>&1
```

The script polls the job and logs stage changes, completion, or failure for up
to 3.5 hours. `KPL_POLL_SECONDS` and `KPL_MAX_POLLS` can adjust that bound.
Check `/management` for progress and attempts. Scheduled jobs skip analysis when source data and
artifacts are current, but repair stale or missing outputs and published files.
Manual **Full update** always rebuilds analysis and publishes. A broker or
worker outage delays the run; pending jobs are recovered when service returns.
The worker has a three-hour hard deadline and the broker allows four hours
before an unacknowledged delivery expires.

For a consistent backup, stop the API and worker, copy the database and
artifacts, then restart them:

```bash
docker compose -f docker-compose.production.yml stop api worker
cp backend/data/kpl_bp.db /safe/backup/location/kpl_bp-$(date +%F).db
tar -czf /safe/backup/location/kpl-artifacts-$(date +%F).tgz \
  analysis/exports analysis/outputs analysis/published
docker compose -f docker-compose.production.yml start api worker
```

Keep database and artifact backups outside the ECS disk, such as in OSS. For
HTTPS, terminate TLS at Alibaba Cloud CDN or an Application Load Balancer and
use the ECS port 80 service as its origin.
