# Networking worksheet

Fill in before Phase 0 deployment. Keep secrets out of Git; IPs and hostnames are fine.

## Domains

| Item | Value |
|---|---|
| Base domain | `example.com` |
| Wildcard DNS | `*.example.com` → Traefik IP (**recommended**) |
| PVE hostname | `pve.example.com` |
| PBS hostname | `pbs.example.com` (optional) |
| Service pattern | `<name>.example.com` (one subdomain per service) |
| ACME mode | HTTP-01 (default) / DNS-01 wildcard |
| DNS provider | |
| Cert maintenance | Automatic via Traefik ACME (`acme.json`) |

## Addresses

| Role | Interface / bridge | IP | Notes |
|---|---|---|---|
| Traefik front | `vmbr0` | | Public or LAN VIP |
| Traefik services NIC | `vmbr1` | | Optional second NIC |
| Proxmox VE API/UI | | | Usually node mgmt IP `:8006` |
| PBS | | | `:8007` if used |

## Firewall intent

| Direction | Allow | Deny |
|---|---|---|
| WAN/LAN → Traefik | `80/tcp`, `443/tcp` | everything else to guests |
| Traefik → PVE | `8006/tcp` | |
| Traefik → PBS | `8007/tcp` | |
| Traefik → apps | per-service ports | |
| WAN → PVE/PBS/apps | — | direct publish (after cutover) |
| Admin break-glass | VPN / mgmt VLAN → `8006` | |

## VLANs / SDN

| Network | Bridge / VNet | Purpose |
|---|---|---|
| Front | | clients → Traefik |
| Services | | Traefik → backends |
| Management | | admin / backup / Ceph etc. |
