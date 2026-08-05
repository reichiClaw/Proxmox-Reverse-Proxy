# Proxmox Reverse Proxy

TLS **Main Gate** for a Proxmox instance: one reverse proxy terminates HTTPS and routes to Proxmox VE (and optionally PBS) plus all published guest services.

## Plan

See **[docs/architecture.md](docs/architecture.md)** for the full design:

- Traefik v3 in a dedicated LXC as the single `:443` entry point
- Automatic certificates (Let’s Encrypt)
- Host-based routing to PVE / PBS / guest apps
- Network isolation, security model, and phased delivery

## Status

Architecture planning. Implementation artifacts (`config/`, `deploy/`) will follow once open decisions in the plan are settled (domain, DNS, public vs LAN-only, etc.).
