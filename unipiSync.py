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
from pathlib import Path

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

        excluded_subnets_str = os.getenv('EXCLUDED_SUBNETS', '')
        self.excluded_subnets = [s.strip() for s in excluded_subnets_str.split(',') if s.strip()]

        self.log_file = os.getenv('LOG_FILE', '/var/log/unipiSync.log')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')

        # State file to track unipiSync-managed records
        self.state_file = os.getenv('STATE_FILE', '/var/lib/unipiSync/state.json')

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
        self.excluded_subnets = config.excluded_subnets
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
                mac = client.get('mac')

                if not ip or not hostname or not mac:
                    continue

                # Filter by excluded subnets first (takes precedence)
                if self.excluded_subnets:
                    if any(ip.startswith(subnet) for subnet in self.excluded_subnets):
                        continue

                # Filter by allowed subnets if configured
                if self.allowed_subnets:
                    if not any(ip.startswith(subnet) for subnet in self.allowed_subnets):
                        continue

                clean_hostname = self._sanitize_hostname(hostname)
                client_list.append({
                    'ip': ip,
                    'hostname': clean_hostname,
                    'mac': mac.lower()
                })

            # Handle duplicate hostnames
            active_clients = self._handle_duplicates(client_list)

            # Build descriptive log message
            filter_parts = []
            if self.allowed_subnets:
                filter_parts.append(f"in subnets {', '.join(self.allowed_subnets)}")
            if self.excluded_subnets:
                filter_parts.append(f"excluding {', '.join(self.excluded_subnets)}")

            filter_msg = f" ({', '.join(filter_parts)})" if filter_parts else " (all subnets)"
            logging.info(f"Found {len(active_clients)} active clients{filter_msg}")
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
            mac = client['mac']

            if hostname_counts[hostname] > 1:
                last_octet = ip.split('.')[-1]
                unique_hostname = f"{hostname}-{last_octet}"
            else:
                unique_hostname = hostname

            active_clients.append({
                'ip': ip,
                'hostname': unique_hostname,
                'mac': mac
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

            # Return list of tuples to track all records, including duplicates
            existing = []
            for record in records:
                parts = record.split()
                if len(parts) >= 2:
                    ip, domain = parts[0], parts[1]
                    existing.append((domain, ip))

            logging.info(f"Retrieved {len(existing)} existing DNS records")
            return existing
        except Exception as e:
            logging.error(f"Failed to get existing DNS records: {e}")
            return []

    def delete_dns_record(self, domain, ip):
        """Delete a specific DNS record from Pi-hole"""
        try:
            fqdn = f"{domain}.{self.domain}" if not domain.endswith(f".{self.domain}") else domain
            encoded_record = quote(f"{ip} {fqdn}")
            url = f"http://{self.host}/api/config/dns/hosts/{encoded_record}"

            headers = {
                'X-FTL-SID': self.sid,
                'X-FTL-CSRF-TOKEN': self.csrf_token
            }

            response = self.session.delete(url, headers=headers, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to delete DNS record {fqdn} -> {ip}: {e}")
            return False

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

class StateManager:
    """Manages state file to track unipiSync-created records by MAC address"""
    def __init__(self, state_file):
        self.state_file = Path(state_file)
        self.devices = {}  # MAC -> {'fqdn': str, 'ip': str, 'hostname': str}
        self.load()

    def load(self):
        """Load state from disk"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.devices = data.get('devices', {})
                logging.debug(f"Loaded {len(self.devices)} managed devices from state file")
            except Exception as e:
                logging.warning(f"Failed to load state file: {e}. Starting fresh.")
                self.devices = {}
        else:
            logging.info("No state file found, starting fresh (all existing records will be considered manual)")

    def save(self):
        """Save state to disk"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, 'w') as f:
                data = {
                    'devices': self.devices,
                    'last_updated': datetime.utcnow().isoformat()
                }
                json.dump(data, f, indent=2)
            logging.debug(f"Saved {len(self.devices)} managed devices to state file")
        except Exception as e:
            logging.error(f"Failed to save state file: {e}")

    def update_device(self, mac, fqdn, ip, hostname):
        """Track or update a device by MAC address"""
        self.devices[mac] = {
            'fqdn': fqdn,
            'ip': ip,
            'hostname': hostname,
            'last_seen': datetime.utcnow().isoformat()
        }

    def get_device(self, mac):
        """Get device info by MAC address"""
        return self.devices.get(mac)

    def get_device_by_fqdn(self, fqdn):
        """Find device(s) by FQDN - returns list of MACs"""
        return [mac for mac, info in self.devices.items() if info['fqdn'] == fqdn]

    def is_managed_record(self, fqdn, ip):
        """Check if a DNS record belongs to a unipiSync-managed device"""
        for mac, info in self.devices.items():
            if info['fqdn'] == fqdn:
                # This FQDN is managed by unipiSync
                return True
        return False

    def get_old_records_for_device(self, mac, current_fqdn, current_ip):
        """Get records that should be deleted for a device (changed IP/hostname)"""
        device_info = self.get_device(mac)
        if not device_info:
            return []

        old_records = []
        # If IP changed, delete old IP record
        if device_info['ip'] != current_ip and device_info['fqdn'] == current_fqdn:
            old_records.append((device_info['fqdn'], device_info['ip']))
        # If hostname changed, delete old hostname record
        elif device_info['fqdn'] != current_fqdn:
            old_records.append((device_info['fqdn'], device_info['ip']))

        return old_records

def sync_dhcp_to_dns(config, dry_run=False):
    """Main sync function"""
    logging.info("=" * 50)
    logging.info(f"Starting unipiSync{' (DRY RUN)' if dry_run else ''}")

    # Load state manager
    state = StateManager(config.state_file)

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

    # Get existing records as list of (domain, ip) tuples
    existing_records = pihole.get_existing_records()

    # Build map of hostname -> list of IPs for easy lookup
    hostname_map = {}
    for domain, ip in existing_records:
        hostname_map.setdefault(domain, []).append(ip)

    # Sync clients
    added = 0
    updated = 0
    skipped = 0
    deleted = 0
    failed = 0

    for client in clients:
        fqdn = f"{client['hostname']}.{config.dns_domain}"
        ip = client['ip']
        mac = client['mac']

        # Check if this device has changed (IP or hostname)
        old_records = state.get_old_records_for_device(mac, fqdn, ip)

        # Delete old records for this device (if it changed)
        for old_fqdn, old_ip in old_records:
            if dry_run:
                logging.info(f"[DRY RUN] Would delete old record for {mac}: {old_fqdn} -> {old_ip}")
            else:
                if pihole.delete_dns_record(old_fqdn, old_ip):
                    logging.info(f"Deleted old record for device {mac}: {old_fqdn} -> {old_ip}")
                    deleted += 1
                else:
                    failed += 1

        # Check current state in Pi-hole
        if fqdn in hostname_map:
            existing_ips = hostname_map[fqdn]

            if ip in existing_ips and len(existing_ips) == 1:
                # Record exists and is correct, just update state
                if not dry_run:
                    state.update_device(mac, fqdn, ip, client['hostname'])
                skipped += 1
            else:
                # Clean up duplicates (only if managed by unipiSync)
                for old_ip in existing_ips:
                    if old_ip != ip and state.is_managed_record(fqdn, old_ip):
                        if dry_run:
                            logging.info(f"[DRY RUN] Would delete duplicate record {fqdn} -> {old_ip}")
                        else:
                            if pihole.delete_dns_record(fqdn, old_ip):
                                logging.info(f"Deleted duplicate record {fqdn} -> {old_ip}")
                                deleted += 1
                            else:
                                failed += 1
                    elif old_ip != ip:
                        logging.debug(f"Skipping manual record {fqdn} -> {old_ip}")

                # Add current record if not already there
                if ip not in existing_ips:
                    if dry_run:
                        logging.info(f"[DRY RUN] Would update {fqdn} -> {ip} (MAC: {mac})")
                        updated += 1
                    else:
                        if pihole.add_dns_record(client['hostname'], ip):
                            logging.info(f"Updated {fqdn} -> {ip} (MAC: {mac})")
                            state.update_device(mac, fqdn, ip, client['hostname'])
                            updated += 1
                        else:
                            failed += 1
                else:
                    # Correct IP exists, just update state
                    if not dry_run:
                        state.update_device(mac, fqdn, ip, client['hostname'])
                    skipped += 1
        else:
            # New record
            if dry_run:
                logging.info(f"[DRY RUN] Would add {fqdn} -> {ip} (MAC: {mac})")
                added += 1
            else:
                if pihole.add_dns_record(client['hostname'], ip):
                    logging.info(f"Added {fqdn} -> {ip} (MAC: {mac})")
                    state.update_device(mac, fqdn, ip, client['hostname'])
                    added += 1
                else:
                    failed += 1

    # Save state
    if not dry_run:
        state.save()

    logging.info(f"Sync complete: {added} added, {updated} updated, {deleted} deleted, {skipped} skipped, {failed} failed")
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
