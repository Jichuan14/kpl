# Aliyun release bundle

This bundle contains the complete application source plus the local runtime
data under `backend/data`, `analysis/exports`, `analysis/outputs`, and
`analysis/published`.

It intentionally does not contain:

- `.env`, `.env.production`, or other runtime secrets
- `deploy/.htpasswd`
- `.git`, local editor state, virtual environments, `node_modules`, or caches

The installer preserves the live server's `.env.production`, optional
`backend/.env`, and `deploy/.htpasswd`. It backs up the current backend and analysis data, then
makes `backend/`, `frontend/`, `analysis/`, and `agent/` exact copies of the
release. This means obsolete files are removed instead of surviving the
update.

## Install

Upload the ZIP to the Aliyun server and extract it outside the live project
directory. For example, if the live application is `/opt/kpl`:

```bash
unzip kpl-aliyun-release-*.zip -d /tmp/kpl-release
cd /tmp/kpl-release/kpl-aliyun-release-*
sudo bash deploy/install_aliyun_release.sh /opt/kpl
```

The server needs `docker`, the Docker Compose plugin, `rsync`, and `curl`.
The installer stops the current containers, creates a timestamped backup next
to the live directory, replaces the release directories, rebuilds the images,
starts the services, and checks `http://127.0.0.1/health`.

Because this release contains `backend/data/kpl_bp.db` and all analysis data,
the corresponding server data is deliberately replaced. The old copies remain
in the timestamped backup reported by the installer.

Do not extract the ZIP directly over `/opt/kpl`: normal ZIP extraction
overwrites matching files but does not remove obsolete files. Running the
installer from a separate extracted directory is what guarantees the complete
backend replacement.
