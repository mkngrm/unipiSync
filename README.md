# unipiSync

Syncs DHCP lease information from Unifi Controller to Pi-hole DNS records.

## What it does

- Retrieves active DHCP clients from a Unifi Controller
- Filters clients by configured subnet(s)
- Sanitizes hostnames for DNS compatibility
- Handles duplicate hostnames by appending IP last octet
- Creates or updates DNS records in Pi-hole
- Logs all operations

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
nano config.env
```

## Configuration

Edit `config.env` with the following values:

### Unifi Controller
- `UNIFI_HOST` - IP address or hostname of Unifi Controller
- `UNIFI_PORT` - Port number (default: 443)
- `UNIFI_API_TOKEN` - API token from Unifi Controller
- `UNIFI_SITE` - Site name (default: default)

### Pi-hole
- `PIHOLE_HOST` - IP address or hostname of Pi-hole
- `PIHOLE_PASSWORD` - Pi-hole admin password

### DNS
- `DNS_DOMAIN` - Domain suffix for DNS records (e.g., example.com)

### Network
- `ALLOWED_SUBNETS` - Comma-separated list of subnet prefixes to sync (e.g., 192.168.70. or 192.168.10.,192.168.20.)

### Logging
- `LOG_FILE` - Path to log file (default: /var/log/unipisync.log)
- `LOG_LEVEL` - Logging level: DEBUG, INFO, WARNING, ERROR (default: INFO)

## Usage

### Test with dry run:
```bash
./sync.py --dry-run
```

### Run sync:
```bash
./sync.py
```

### Custom config file:
```bash
./sync.py -c /path/to/config.env
```

### Verbose logging:
```bash
./sync.py -v
```

## Automation

Add to cron for automatic syncing:

```bash
sudo crontab -e
```

Add line (runs every 5 minutes):
```
*/5 * * * * cd /path/to/unipiSync && /usr/bin/python3 sync.py >> /var/log/unipisync.log 2>&1
```

## Hostname Sanitization

The script automatically:
- Converts hostnames to lowercase
- Replaces spaces with hyphens
- Removes apostrophes and special characters
- Keeps only alphanumeric characters, hyphens, and periods

If multiple clients have the same hostname, the IP's last octet is appended (e.g., `laptop-101`, `laptop-102`).

## License

MIT
