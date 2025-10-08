# unipiSync

Syncs DHCP lease information from Unifi Controller to Pi-hole DNS records.

## What it does

- Retrieves active DHCP clients from Unifi Controller
- Filters clients by subnet (include/exclude rules)
- Sanitizes hostnames for DNS compatibility
- Handles duplicate hostnames by appending IP last octet
- Creates or updates DNS records in Pi-hole via API
- Supports dry-run mode to preview changes before applying

## Requirements

- Python 3.6+
- Unifi Controller with API access
- Pi-hole v6+ with API access
- Network access between the system running this script and both services

## Installation

1. Clone the repository:
```bash
git clone https://github.com/mkngrm/unipiSync.git
cd unipiSync
```

2. Install dependencies:
```bash
pip3 install -r requirements.txt
```

3. Create configuration file:
```bash
cp config.env.example config.env
```

4. Edit `config.env` with your settings:
```bash
vi config.env
```

## Configuration

Edit `config.env` with the following values:

### Unifi Controller
- `UNIFI_HOST` - IP address or hostname of Unifi Controller (required)
- `UNIFI_PORT` - Port number (default: 443)
- `UNIFI_API_TOKEN` - API token from Unifi Controller (required)
  - Generate in Unifi Controller: Settings → Admins → API Token
- `UNIFI_SITE` - Site name (default: "default")

### Pi-hole
- `PIHOLE_HOST` - IP address or hostname of Pi-hole server (required)
- `PIHOLE_PASSWORD` - Pi-hole web interface admin password (required)
  - Note: This is your Pi-hole admin password, not a Pi-hole API token

### DNS
- `DNS_DOMAIN` - Domain suffix appended to all DNS records (required)
  - Example: `example.com` creates records like `hostname.example.com`
  - Must be a valid DNS domain name

### Network Filtering
Controls which DHCP clients are synced to Pi-hole based on IP address prefixes.

**`ALLOWED_SUBNETS`** - Comma-separated list of IP prefixes to include
- **Leave empty to sync ALL DHCP leases from Unifi**
- Uses simple prefix matching (not CIDR notation)
- Examples:
  - Single subnet: `192.168.70.` (matches 192.168.70.1 through 192.168.70.254)
  - Multiple subnets: `192.168.10.,192.168.20.` (matches both subnets)
  - Entire private network: `192.168.` (matches all 192.168.x.x addresses)
  - Class C network: `192.168.1.` (matches 192.168.1.0-192.168.1.255)

**`EXCLUDED_SUBNETS`** - Comma-separated list of IP prefixes to exclude
- **Takes precedence over ALLOWED_SUBNETS** (exclusions are processed first)
- Leave empty to exclude nothing
- Useful for filtering out management networks, guest networks, IoT VLANs, etc.
- Examples:
  - Exclude management: `192.168.1.` (excludes 192.168.1.x)
  - Exclude multiple: `192.168.1.,192.168.2.,10.0.0.` (excludes all three)

**Filter Priority:**
1. IPs matching `EXCLUDED_SUBNETS` are always excluded (even if in `ALLOWED_SUBNETS`)
2. If `ALLOWED_SUBNETS` is set, only matching IPs are included
3. If `ALLOWED_SUBNETS` is empty, all IPs are included (except excluded ones)

### Logging
- `LOG_FILE` - Path to log file (default: `/var/log/unipiSync.log`)
  - Logs are appended, not overwritten
  - Ensure the user running the script has write permissions
- `LOG_LEVEL` - Logging verbosity (default: `INFO`)
  - Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`
  - Use `DEBUG` for troubleshooting

## Usage

### Test with dry run (recommended first):
Shows what would be synced without making any changes to Pi-hole.
```bash
./unipiSync.py --dry-run
```

### Run sync:
Actually creates/updates DNS records in Pi-hole.
```bash
./unipiSync.py
```

### Custom config file:
Use a different configuration file (default: `config.env` in current directory).
```bash
./unipiSync.py -c /path/to/config.env
```

### Verbose logging:
Enable DEBUG level logging for troubleshooting.
```bash
./unipiSync.py -v
```

### Combined options:
```bash
./unipiSync.py --dry-run -v
```

## Automation

To automatically sync DHCP leases on a schedule, use cron:

### Option 1: User crontab (if script has permissions)
```bash
crontab -e
```

Add line to run every 5 minutes:
```
*/5 * * * * cd /path/to/unipiSync && /usr/bin/python3 unipiSync.py >> /var/log/unipiSync.log 2>&1
```

### Option 2: Root crontab (for system-wide deployment)
```bash
sudo crontab -e
```

Add line:
```
*/5 * * * * cd /opt/unipiSync && /usr/bin/python3 unipiSync.py >> /var/log/unipiSync.log 2>&1
```

**Cron schedule examples:**
- Every 5 minutes: `*/5 * * * *`
- Every 15 minutes: `*/15 * * * *`
- Every hour: `0 * * * *`
- Every day at 3am: `0 3 * * *`

## Hostname Sanitization

Hostnames from Unifi are automatically cleaned for DNS compatibility:

**Transformations applied:**
1. Convert to lowercase
2. Replace spaces with hyphens
3. Remove apostrophes and smart quotes
4. Remove all characters except: `a-z`, `0-9`, `-`, `.`

**Examples:**
- `John's iPhone` → `johns-iphone`
- `Living Room TV` → `living-room-tv`
- `Server#1` → `server1`

**Duplicate handling:**
If multiple clients have the same hostname after sanitization, the IP's last octet is appended:
- First device: `laptop.example.com` (192.168.70.101)
- Second device: `laptop-102.example.com` (192.168.70.102)
- Third device: `laptop-103.example.com` (192.168.70.103)

## License

MIT - See [LICENSE](LICENSE) file for details
