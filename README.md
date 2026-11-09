# k8s-cost-peek

[![CI](https://github.com/Harjeet2411/k8s-cost-peek/actions/workflows/ci.yml/badge.svg)](https://github.com/Harjeet2411/k8s-cost-peek/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

**Which namespace or team is paying for your cluster?** `k8s-cost-peek` reads `kubectl get pods -o json`, adds up the CPU and memory each pod *requests*, and prices it per month. No agent, no cluster access, no dependencies: one JSON file in, one table out.

Requests are what the scheduler reserves, so they are what you pay for on node-based clusters and exactly what you pay on Fargate. The default prices are the AWS Fargate on-demand list prices for us-east-1 (Linux/x86); pass your own for EC2 nodes, GKE or AKS.

## Demo

Run against the synthetic cluster in [`examples/pods.json`](examples/pods.json):

```console
$ kubectl get pods -A -o json | k8s-cost-peek
NAMESPACE    PODS     CPU  MEM GiB  $/MONTH  NO REQ
payments        4   3.800    11.38   149.20
search          3   3.000    12.00   127.59       1
checkout        4   4.000     2.00   124.69
monitoring      2   1.250     4.54    51.67
batch           1   1.250     2.12    43.81
kube-system     2   0.200     0.14     6.35
-----------  ----  ------  -------  -------  ------
TOTAL          16  13.500    32.18   503.31

1 pod(s) set no CPU or memory request, so they cost $0 here but still use the node: search/search-scratch
```

Group by any pod label and fail CI when the cluster goes over budget:

```console
$ k8s-cost-peek examples/pods.json --by-label team --budget 300
TEAM        PODS     CPU  MEM GiB  $/MONTH  NO REQ
payments       4   3.800    11.38   149.20
search         3   3.000    12.00   127.59       1
checkout       4   4.000     2.00   124.69
platform       2   1.250     4.54    51.67
data           1   1.250     2.12    43.81
(no label)     2   0.200     0.14     6.35
----------  ----  ------  -------  -------  ------
TOTAL         16  13.500    32.18   503.31

1 pod(s) set no CPU or memory request, so they cost $0 here but still use the node: search/search-scratch
k8s-cost-peek: monthly total $503.31 is over the $300.00 budget
$ echo $?
1
```

Things the demo shows:
- **checkout** requests only 250m per pod, but its `migrate` init container asks for a full core, so the scheduler reserves 1 CPU per pod. That is how Kubernetes counts it, and it is easy to miss.
- **search/search-scratch** sets no requests, so it shows up as $0 while still taking node capacity. It is listed so you can fix it.
- The finished `nightly-report` job in **batch** is skipped: Succeeded and Failed pods hold no resources.

## Install

```bash
pip install git+https://github.com/Harjeet2411/k8s-cost-peek
```

## Usage

```text
k8s-cost-peek [FILE] [--by-label KEY] [--cpu-price USD] [--mem-price USD]
              [--sort cost|cpu|memory|name] [--format table|json|csv] [--budget USD]
```

| Option | Meaning |
|---|---|
| `FILE` | `kubectl get pods -A -o json` output, a single Pod, or `-` for stdin (default) |
| `--by-label team` | Group by a pod label instead of namespace; pods without it go in `(no label)` |
| `--cpu-price`, `--mem-price` | USD per vCPU-hour and per GiB-hour (defaults: 0.04048 and 0.004445) |
| `--format json` / `csv` | Machine-readable output for dashboards or spreadsheets |
| `--budget 500` | Exit 1 if the monthly total is above 500, so a pipeline can fail on it |

Exit codes: 0 OK, 1 over budget, 2 unreadable input.

### How a pod's request is counted

The same way the scheduler does it:
1. Regular containers are summed.
2. A classic init container runs alone, so the pod needs the larger of that sum and the biggest init container.
3. A sidecar init container (`restartPolicy: Always`) keeps running, so it is added.
4. Pod `overhead` from a RuntimeClass is added on top.

A month is 730 hours.

### Pricing EC2 nodes

Split an instance price into a vCPU and a GiB rate. For example, m6i.large (2 vCPU, 8 GiB, $0.096/h) priced with the common 1 vCPU = 4 GiB weighting gives about $0.024 per vCPU-hour and $0.006 per GiB-hour:

```bash
k8s-cost-peek pods.json --cpu-price 0.024 --mem-price 0.006
```

## Roadmap

- Read requests from Deployments and StatefulSets (replicas × template) to price changes before they ship
- Compare requests against `kubectl top` usage to show idle reservation
- Price files for common EC2, GKE and AKS node types

## Development

```bash
python -m unittest discover -s tests -v
```

Standard library only. Tested on Python 3.9, 3.11 and 3.13.

## License

MIT
