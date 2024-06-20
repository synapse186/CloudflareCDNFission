import os
import re
import random
import ipaddress
import subprocess
import concurrent.futures
import logging
from typing import List, Tuple, Set
import requests
from lxml import etree
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 文件配置
IPS_FILE = "Fission_ip.txt"
DOMAINS_FILE = "Fission_domain.txt"
DNS_RESULT_FILE = "dns_result.txt"

# 并发配置
MAX_WORKERS_REQUEST = 20
MAX_WORKERS_DNS = 50

# 随机User-Agent生成
ua = UserAgent()

# 网站配置
SITES_CONFIG = {
    "site_ip138": {
        "url": "https://site.ip138.com/",
        "xpath": '//ul[@id="list"]/li/a'
    },
    "dnsdblookup": {
        "url": "https://dnsdblookup.com/",
        "xpath": '//ul[@id="list"]/li/a'
    },
    "ipchaxun": {
        "url": "https://ipchaxun.com/",
        "xpath": '//div[@id="J_domain"]/p/a'
    }
}

def setup_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def get_headers() -> dict:
    return {
        'User-Agent': ua.random,
        'Accept': '*/*',
        'Connection': 'keep-alive',
    }

def fetch_domains_for_ip(ip_address: str, session: requests.Session, attempts: int = 0, used_sites: List[str] = None) -> List[str]:
    logging.info(f"Fetching domains for {ip_address}...")
    if used_sites is None:
        used_sites = []
    if attempts >= 3:
        return []

    available_sites = {key: value for key, value in SITES_CONFIG.items() if key not in used_sites}
    if not available_sites:
        return []

    site_key = random.choice(list(available_sites.keys()))
    site_info = available_sites[site_key]
    used_sites.append(site_key)

    try:
        url = f"{site_info['url']}{ip_address}/"
        headers = get_headers()
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html_content = response.text

        parser = etree.HTMLParser()
        tree = etree.fromstring(html_content, parser)
        a_elements = tree.xpath(site_info['xpath'])
        domains = [a.text for a in_elements if a.text]

        if domains:
            logging.info(f"Succeeded in fetching domains for {ip_address} from {site_info['url']}")
            return domains
        else:
            raise ValueError("No domains found")

    except Exception as e:
        logging.error(f"Error fetching domains for {ip_address} from {site_info['url']}: {e}")
        return fetch_domains_for_ip(ip_address, session, attempts + 1, used_sites)

def fetch_domains_concurrently(ip_addresses: List[str]) -> List[str]:
    session = setup_session()
    domains = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_REQUEST) as executor:
        future_to_ip = {executor.submit(fetch_domains_for_ip, ip, session): ip for ip in ip_addresses}
        for future in concurrent.futures.as_completed(future_to_ip):
            domains.extend(future.result())

    return list(set(domains))

def dns_lookup(domain: str) -> Tuple[str, str]:
    logging.info(f"Performing DNS lookup for {domain}...")
    result = subprocess.run(["nslookup", domain], capture_output=True, text=True)
    return domain, result.stdout

def perform_dns_lookups(domain_filename: str, result_filename: str, unique_ipv4_filename: str):
    try:
        with open(domain_filename, 'r') as file:
            domains = file.read().splitlines()

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_DNS) as executor:
            results = list(executor.map(dns_lookup, domains))

        with open(result_filename, 'w') as output_file:
            for domain, output in results:
                output_file.write(output)

        ipv4_addresses = set()
        for _, output in results:
            ipv4_addresses.update(re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', output))

        with open(unique_ipv4_filename, 'r') as file:
            existing_ips = {ip.strip() for ip in file}

        filtered_ipv4_addresses = {ip for ip in ipv4_addresses if ipaddress.ip_address(ip).is_global}
        filtered_ipv4_addresses.update(existing_ips)

        with open(unique_ipv4_filename, 'w') as output_file:
            for address in filtered_ipv4_addresses:
                output_file.write(address + '\n')

    except Exception as e:
        logging.error(f"Error performing DNS lookups: {e}")

def main():
    if not os.path.exists(IPS_FILE):
        with open(IPS_FILE, 'w') as file:
            file.write("")
    
    if not os.path.exists(DOMAINS_FILE):
        with open(DOMAINS_FILE, 'w') as file:
            file.write("")

    with open(IPS_FILE, 'r') as ips_txt:
        ip_list = [ip.strip() for ip in ips_txt]

    domain_list = fetch_domains_concurrently(ip_list)
    logging.info(f"Domain list: {domain_list}")

    with open(DOMAINS_FILE, 'r') as file:
        existing_domains = [domain.strip() for domain in file]

    domain_list = list(set(domain_list + existing_domains))

    with open(DOMAINS_FILE, 'w') as output:
        for domain in domain_list:
            output.write(domain + "\n")

    logging.info("IP to domain conversion completed")

    perform_dns_lookups(DOMAINS_FILE, DNS_RESULT_FILE, IPS_FILE)
    logging.info("Domain to IP conversion completed")

if __name__ == '__main__':
    main()
