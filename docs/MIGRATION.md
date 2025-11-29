# Migration deployment

## Kubernetes CronJob deployment

Prerequisites:
- Kubernetes cluster and kubectl access.
- Container image for `des-migrator` published/accessible.

Apply manifests:
```bash
kubectl apply -f k8s/des-migrator-configmap.yaml
kubectl apply -f k8s/des-migrator-secret.yaml
kubectl apply -f k8s/des-migrator-pvc.yaml
kubectl apply -f k8s/des-migrator-rbac.yaml
kubectl apply -f k8s/des-migrator-cronjob.yaml
```

Check jobs and logs:
```bash
kubectl get cronjobs
kubectl get jobs -l app=des-migrator
kubectl logs job/<job-name> -c des-migrator
```

Changing schedule/resources/image:
- Edit `spec.schedule` in the CronJob (default `0 */6 * * *`).
- Update `resources.requests/limits` and container image/tag as needed.
- Mount different config/secret names by adjusting volumes/env refs.

Local testing with kind/minikube:
```bash
kind create cluster --name des-migration
# or: minikube start
kubectl apply -f k8s/des-migrator-configmap.yaml
kubectl apply -f k8s/des-migrator-secret.yaml
kubectl apply -f k8s/des-migrator-pvc.yaml
kubectl apply -f k8s/des-migrator-rbac.yaml
kubectl apply -f k8s/des-migrator-cronjob.yaml
```

Metrics & Grafana:
- Prometheus metrics available via `prometheus_client` (see README).
- Example assets: `examples/grafana-dashboard-des-migration.json` and `examples/alerts-des-migration.yml`.

ConfigMap/Secret/PVC details:
- ConfigMap mounts the migration config under `/config/migration-config.json`.
- Secret injects `DB_PASSWORD` for database URL templating.
- PVC mounts source files under `/data/source`; adjust storage class/size as needed.

## Enabling BigFiles
- Upgrade packers/retrievers to the BigFiles-aware release (shard header version v2).
- Set consistent values for `DES_BIG_FILE_THRESHOLD_BYTES` and `DES_BIGFILES_PREFIX` across packer jobs, HTTP retrievers, and any ad-hoc readers (`DESConfig.from_env()` consumes these).
- Ensure `_bigFiles/` is uploaded alongside `.des` shards when syncing to S3; the S3 packer helper handles this automatically.
- For local deployments, mount the `_bigFiles/` directory next to the shard path so retrievers can resolve external payloads.
