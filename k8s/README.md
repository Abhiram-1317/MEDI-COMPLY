# 🚀 MEDI-COMPLY Kubernetes Deployment

## Overview

Production-grade Kubernetes deployment for the MEDI-COMPLY healthcare AI system.
Designed for HIPAA-compliant infrastructure on AWS EKS, Azure AKS, or GCP GKE.

## Architecture
Internet → Ingress (TLS + WAF + Rate Limit) → API Service (2+ pods)
↓
┌──────────────────────┼──────────────────┐
↓ ↓ ↓
Worker Pods NLP Pipeline Guardrail Service
(2+ pods) (1-2 pods) (2+ pods)
↓ ↓ ↓
┌─────────┼──────────────────────┼──────────────────┘
↓ ↓ ↓
PostgreSQL Redis ChromaDB
(Audit DB) (Cache) (Vector Store)

text


## Prerequisites

- Kubernetes 1.28+
- kubectl configured
- NGINX Ingress Controller
- cert-manager (for TLS)
- Storage classes: `gp3-encrypted`, `efs-encrypted`

## Quick Deploy

```bash
# Create namespace and all resources
kubectl apply -k k8s/

# Verify deployment
kubectl -n medi-comply get pods
kubectl -n medi-comply get services

# Check API health
kubectl -n medi-comply port-forward svc/api-service 8000:80
curl http://localhost:8000/health
Configuration
Secrets (MUST change before production)
Edit k8s/secrets.yml and replace ALL CHANGE_ME values:

Database passwords
LLM API keys
JWT secrets
Encryption keys
Config Map
Edit k8s/configmap.yml for environment-specific settings.

Scaling
Bash

# Manual scaling
kubectl -n medi-comply scale deployment medi-comply-api --replicas=4

# HPA auto-scales API pods based on CPU/memory
kubectl -n medi-comply get hpa
Monitoring
Bash

# View logs
kubectl -n medi-comply logs -f deployment/medi-comply-api
kubectl -n medi-comply logs -f deployment/medi-comply-workers

# Resource usage
kubectl -n medi-comply top pods
Security
Network Policies: Default deny with explicit allow rules
TLS: All external traffic encrypted via cert-manager
WAF: ModSecurity rules at ingress
RBAC: Service accounts with minimal permissions
Secrets: Kubernetes secrets (use external secret manager in production)
Non-root: All containers run as non-root user
HIPAA Compliance Notes
All data encrypted at rest (encrypted storage classes)
All data encrypted in transit (TLS 1.3)
Network policies enforce microsegmentation
Audit logs stored on persistent encrypted volumes
No PHI in environment variables or logs
7-year audit retention configured
text


---

## Step 4: Verify all files exist

```bash
find k8s/ -type f | sort
Expected output:

text

k8s/README.md
k8s/api/deployment.yml
k8s/api/hpa.yml
k8s/api/service.yml
k8s/configmap.yml
k8s/databases/chroma-deployment.yml
k8s/databases/chroma-service.yml
k8s/databases/postgres-deployment.yml
k8s/databases/postgres-pvc.yml
k8s/databases/postgres-service.yml
k8s/databases/redis-deployment.yml
k8s/databases/redis-service.yml
k8s/guardrail/deployment.yml
k8s/guardrail/service.yml
k8s/ingress.yml
k8s/kustomization.yml
k8s/namespace.yml
k8s/network-policy.yml
k8s/nlp/deployment.yml
k8s/nlp/service.yml
k8s/secrets.yml
k8s/workers/deployment.yml
k8s/workers/service.yml
Total: 23 files

Step 5: Validate YAML syntax
Bash

# Quick validation using Python
python -c "
import yaml, glob
errors = []
for f in sorted(glob.glob('k8s/**/*.yml', recursive=True)):
    try:
        list(yaml.safe_load_all(open(f)))
        print(f'  ✅ {f}')
    except Exception as e:
        errors.append(f)
        print(f'  ❌ {f}: {e}')
print(f'\n{len(errors)} errors found' if errors else '\nAll YAML files valid ✅')
"
Step 6: Validate with kubectl (dry-run)
Bash

# If you have kubectl configured:
kubectl apply -k k8s/ --dry-run=client
Step 7: Commit everything
Bash

git add k8s/
git commit -m "Add Kubernetes deployment configurations

- API deployment with HPA (2-10 replicas)
- Agent worker deployment (2 replicas)
- NLP pipeline deployment (1 replica)
- Guardrail service deployment (2 replicas)
- PostgreSQL with encrypted PVC (audit DB)
- Redis with password auth (cache)
- ChromaDB (vector store)
- NGINX Ingress with TLS, WAF, rate limiting
- Network policies (default deny + explicit allow)
- Kustomization for unified deployment
- HIPAA-compliant configuration throughout"

git push
Verify Checklist
text

☐ k8s/namespace.yml — HIPAA-labeled namespace
☐ k8s/secrets.yml — All sensitive config (marked CHANGE_ME)
☐ k8s/configmap.yml — All app configuration
☐ k8s/api/deployment.yml — API pods with health checks, resources, security
☐ k8s/api/service.yml — ClusterIP service
☐ k8s/api/hpa.yml — Auto-scaling 2-10 replicas
☐ k8s/workers/deployment.yml — Agent worker pods
☐ k8s/workers/service.yml — Headless service
☐ k8s/nlp/deployment.yml — NLP pipeline with model volume
☐ k8s/nlp/service.yml — NLP service
☐ k8s/guardrail/deployment.yml — Guardrail service
☐ k8s/guardrail/service.yml — Guardrail service
☐ k8s/databases/postgres-deployment.yml — PostgreSQL 16
☐ k8s/databases/postgres-service.yml
☐ k8s/databases/postgres-pvc.yml — 50Gi encrypted storage
☐ k8s/databases/redis-deployment.yml — Redis 7 with auth
☐ k8s/databases/redis-service.yml
☐ k8s/databases/chroma-deployment.yml — ChromaDB
☐ k8s/databases/chroma-service.yml
☐ k8s/ingress.yml — TLS, WAF, security headers, rate limiting
☐ k8s/network-policy.yml — Default deny + microsegmentation
☐ k8s/kustomization.yml — Unified deployment
☐ k8s/README.md — Deployment documentation
☐ All YAML files pass validation
