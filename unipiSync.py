#!/usr/bin/env python3
"""
unipiSync - Unifi DHCP to Pi-hole DNS Sync
Automatically syncs DHCP leases from Unifi controller to Pi-hole DNS records
"""

import requests
import json
import logging
import argparse
import sys
import os
import re
from datetime import datetime
from urllib.parse import quote
from collections import Counter
from dotenv import load_dotenv

requests.packages.urllib3.disable_warnings()

class Config:
    """Configuration management"""
    def __init__(self, config_file=None):
        if config_file and os.path.exists(config_file):
            load_dotenv(config_file)
        elif os.path.exists('config.env'):
            load_dotenv('config.env')
        else:
            logging.warning("No config.env found, using environment variables")

        self.unifi_host = os.getenv('UNIFI_HOST')
        self.unifi_port = os.getenv('UNIFI_PORT', '443')
        self.unifi_api_token = os.getenv('UNIFI_API_TOKEN')
        self.unifi_site = os.getenv('UNIFI_SITE', 'default')

        self.pihole_host = os.getenv('PIHOLE_HOST')
        self.pihole_password = os.getenv('PIHOLE_PASSWORD')

        self.dns_domain = os.getenv('DNS_DOMAIN')

        allowed_subnets_str = os.getenv('ALLOWED_SUBNETS', '')
        self.allowed_subnets = [s.strip() for s in allowed_subnets_str.split(',') if s.strip()]

        self.log_file = os.getenv('LOG_FILE', '/var/log/unipiSync.log')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')

        self.validate()

    def validate(self):
        """Validate required configuration"""
        required = {
            'UNIFI_HOST': self.unifi_host,
            'UNIFI_API_TOKEN': self.unifi_api_token,
            'PIHOLE_HOST': self.pihole_host,
            'PIHOLE_PASSWORD': self.pihole_password,
            'DNS_DOMAIN': self.dns_domain,
        }

        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

class UnifiController:
    """Unifi Controller API client"""
    def __init__(self, config):
        self.base_url = f"https://{config.unifi_host}:{config.unifi_port}"
        self.api_token = config.unifi_api_token
        self.site = config.unifi_site
        self.allowed_subnets = config.allowed_subnets
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({'X-API-KEY': self.api_token})

    def get_active_clients(self):
        """Retrieve active DHCP clients from Unifi controller"""
        try:
            url = f"{self.base_url}/proxy/network/api/s/{self.site}/stat/sta"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            clients = response.json().get('data', [])

            client_list = []
            for client in clients:
                ip = client.get('ip')
                hostname = client.get('hostname') or client.get('name')

                if not ip or not hostname:
                    continue

                # Filter by allowed subnets if configured
                if self.allowed_subnets:
                    if not any(ip.startswith(subnet) for subnet in self.allowed_subnets):
                        continue

                clean_hostname = self._sanitize_hostname(hostname)
                client_list.append({
                    'ip': ip,
                    'hostname': clean_hostname
                })

            # Handle duplicate hostnames
            active_clients = self._handle_duplicates(client_list)

            subnet_msg = f" in subnets {', '.join(self.allowed_subnets)}" if self.allowed_subnets else ""
            logging.info(f"Found {len(active_clients)} active clients{subnet_msg}")
            return active_clients
        except Exception as e:
            logging.error(f"Failed to get active clients from Unifi: {e}")
            return []

    def _sanitize_hostname(self, hostname):
        """Sanitize hostname for DNS compatibility"""
        clean = hostname.lower().replace(' ', '-')
        clean = re.sub(r"['\u2019]", '', clean)
        clean = re.sub(r'[^a-z0-9\-\.]', '', clean)
        return clean

    def _handle_duplicates(self, client_list):
        """Handle duplicate hostnames by appending IP last octet"""
        hostname_counts = Counter([c['hostname'] for c in client_list])
        active_clients = []

        for client in client_list:
            hostname = client['hostname']
            ip = client['ip']

            if hostname_counts[hostname] > 1:
                last_octet = ip.split('.')[-1]
                unique_hostname = f"{hostname}-{last_octet}"
            else:
                unique_hostname = hostname

            active_clients.append({
                'ip': ip,
                'hostname': unique_hostname
            })

        return active_clients

class PiholeAPI:
    """Pi-hole API client"""
    def __init__(self, config):
        self.host = config.pihole_host
        self.password = config.pihole_password
        self.domain = config.dns_domain
        self.session = requests.Session()
        self.sid = None
        self.csrf_token = None

    def authenticate(self):
        """Authenticate to Pi-hole API"""
        try:
            url = f"http://{self.host}/api/auth"
            data = {"password": self.password}
            response = self.session.post(url, json=data, timeout=10)
            response.raise_for_status()
            result = response.json()
            self.sid = result.get('session', {}).get('sid')
            self.csrf_token = result.get('session', {}).get('csrf')
            if self.sid and self.csrf_token:
                logging.info("Successfully authenticated to Pi-hole")
                return True
            return False
        except Exception as e:
            logging.error(f"Failed to authenticate to Pi-hole: {e}")
            return False

    def get_existing_records(self):
        """Retrieve existing DNS records from Pi-hole"""
        try:
            url = f"http://{self.host}/api/config/dns/hosts"
            headers = {
                'X-FTL-SID': self.sid,
                'X-FTL-CSRF-TOKEN': self.csrf_token
            }
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            records = response.json().get('config', {}).get('dns', {}).get('hosts', [])

            existing = {}
            for record in records:
                parts = record.split()
                if len(parts) >= 2:
                    ip, domain = parts[0], parts[1]
                    existing[domain] = ip

            logging.info(f"Retrieved {len(existing)} existing DNS records")
            return existing
        except Exception as e:
            logging.error(f"Failed to get existing DNS records: {e}")
            return {}

    def add_dns_record(self, domain, ip):
        """Add or update DNS record in Pi-hole"""
        try:
            fqdn = f"{domain}.{self.domain}"
            encoded_record = quote(f"{ip} {fqdn}")
            url = f"http://{self.host}/api/config/dns/hosts/{encoded_record}"

            headers = {
                'X-FTL-SID': self.sid,
                'X-FTL-CSRF-TOKEN': self.csrf_token
            }

            response = self.session.put(url, headers=headers, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to add DNS record {domain} -> {ip}: {e}")
            return False

def sync_dhcp_to_dns(config, dry_run=False):
    """Main sync function"""
    logging.info("=" * 50)
    logging.info(f"Starting unipiSync{' (DRY RUN)' if dry_run else ''}")

    # Get active clients from Unifi
    unifi = UnifiController(config)
    clients = unifi.get_active_clients()

    if not clients:
        logging.warning("No active clients found - nothing to sync")
        return True

    # Authenticate to Pi-hole
    pihole = PiholeAPI(config)
    if not pihole.authenticate():
        logging.error("Aborting sync - Pi-hole authentication failed")
        return False

    # Get existing records
    existing_records = pihole.get_existing_records()

    # Sync clients
    added = 0
    updated = 0
    skipped = 0
    failed = 0

    for client in clients:
        fqdn = f"{client['hostname']}.{config.dns_domain}"
        ip = client['ip']

        if fqdn in existing_records:
            if existing_records[fqdn] == ip:
                skipped += 1
            else:
                if dry_run:
                    logging.info(f"[DRY RUN] Would update {fqdn}: {existing_records[fqdn]} -> {ip}")
                    updated += 1
                else:
                    if pihole.add_dns_record(client['hostname'], ip):
                        logging.info(f"Updated {fqdn}: {existing_records[fqdn]} -> {ip}")
                        updated += 1
                    else:
                        failed += 1
        else:
            if dry_run:
                logging.info(f"[DRY RUN] Would add {fqdn} -> {ip}")
                added += 1
            else:
                if pihole.add_dns_record(client['hostname'], ip):
                    logging.info(f"Added {fqdn} -> {ip}")
                    added += 1
                else:
                    failed += 1

    logging.info(f"Sync complete: {added} added, {updated} updated, {skipped} skipped, {failed} failed")
    logging.info("=" * 50)
    return failed == 0

def setup_logging(log_file, log_level, verbose=False):
    """Configure logging"""
    level = logging.DEBUG if verbose else getattr(logging, log_level.upper(), logging.INFO)

    handlers = [logging.StreamHandler()]

    try:
        handlers.append(logging.FileHandler(log_file))
    except PermissionError:
        print(f"Warning: Cannot write to {log_file}, logging to stdout only")

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='unipiSync - Sync Unifi DHCP leases to Pi-hole DNS records'
    )
    parser.add_argument(
        '-c', '--config',
        default='config.env',
        help='Path to configuration file (default: config.env)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be synced without making changes'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose (debug) logging'
    )

    args = parser.parse_args()

    try:
        config = Config(args.config)
        setup_logging(config.log_file, config.log_level, args.verbose)

        success = sync_dhcp_to_dns(config, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    except ValueError as e:
        print(f"Configuration error: {e}")
        print(f"\nPlease create a config.env file based on config.env.example")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nSync cancelled by user")
        sys.exit(130)
    except Exception as e:
        logging.error(f"Unhandled exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
