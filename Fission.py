import os
import re
import random
import ipaddress
import subprocess
import concurrent.futures
import requests
from lxml import etree
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 文件配置
ips = "Fission_ip.txt"
domains = "Fission_domain.txt"
dns_result = "dns_result.txt"
ip2cc_result = "Fission_ip2cc.txt"
country_file = "country.txt"  # 新增的国家代码文件

# 并发数配置
max_workers_request = 20   # 并发请求数量
max_workers_dns = 50       # 并发DNS查询数量

# 生成随机User-Agent
def get_random_user_agent():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
        "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:54.0) Gecko/20100101 Firefox/54.0",
        "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.79 Safari/537.36 Edge/14.14"
    ]
    return random.choice(user_agents)

# 设置会话
def setup_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

# 查询域名的函数，自动重试和切换网站
def fetch_domains_for_ip(ip_address, session, attempts=0, used_sites=None):
    print(f"Fetching domains for {ip_address}...")
    if used_sites is None:
        used_sites = []
    if attempts >= 3:  # 如果已经尝试了3次，终止重试
        return []

    # 网站配置
    sites_config = {
        "site_ip138": {
            "url": f"https://site.ip138.com/{ip_address}/",
            "xpath": '//ul[@id="list"]/li/a'
        },
        "dnsdblookup": {
            "url": f"https://dnsdblookup.com/{ip_address}/",
            "xpath": '//ul[@id="list"]/li/a'
        },
        "ipchaxun": {
            "url": f"https://ipchaxun.com/{ip_address}/",
            "xpath": '//div[@id="J_domain"]/p/a'
        }
    }

    # 选择一个未使用的网站进行查询
    available_sites = {key: value for key, value in sites_config.items() if key not in used_sites}
    if not available_sites:
        return []  # 如果所有网站都尝试过，返回空结果

    site_key = random.choice(list(available_sites.keys()))
    site_info = available_sites[site_key]
    used_sites.append(site_key)

    try:
        url = site_info['url']
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': '*/*',
            'Connection': 'keep-alive',
        }
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html_content = response.text

        parser = etree.HTMLParser()
        tree = etree.fromstring(html_content, parser)
        a_elements = tree.xpath(site_info['xpath'])
        domains = [a.text for a in a_elements if a.text]

        if domains:
            print(f"Succeeded to fetch domains for {ip_address} from {site_key}")
            return domains
        else:
            raise Exception("No domains found")

    except Exception as e:
        print(f"Error fetching domains for {ip_address} from {site_key}: {e}")
        return fetch_domains_for_ip(ip_address, session, attempts + 1, used_sites)

# 并发处理所有IP地址
def fetch_domains_concurrently(ip_addresses):
    session = setup_session()
    domains = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers_request) as executor:
        future_to_ip = {executor.submit(fetch_domains_for_ip, ip, session): ip for ip in ip_addresses}
        for future in concurrent.futures.as_completed(future_to_ip):
            domains.extend(future.result())

    return list(set(domains))

# DNS查询函数
def dns_lookup(domain):
    print(f"Performing DNS lookup for {domain}...")
    result = subprocess.run(["nslookup", domain], capture_output=True, text=True)
    return domain, result.stdout

# 执行DNS查询并过滤指定国家的IP
def perform_dns_lookups_and_filter_countries(domain_filename, ip_filename, ip2cc_filename, country_filename, max_workers_dns=50):
    try:
        # 读取国家代码列表
        with open(country_filename, 'r') as country_file:
            countries_to_keep = [country.strip() for country in country_file]

        # 读取域名列表
        with open(domain_filename, 'r') as file:
            domains = file.read().splitlines()

        # 创建一个线程池并执行DNS查询
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers_dns) as executor:
            results = list(executor.map(dns_lookup, domains))

        # 从结果文件中提取所有IPv4地址
        ipv4_addresses = set()
        for _, output in results:
            ipv4_addresses.update(re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', output))

        # 检查IP地址是否为公网IP并排除指定国家的IP
        filtered_ipv4_addresses = set()
        for ip in ipv4_addresses:
            try:
                ip_obj = ipaddress.ip_address(ip)
                if ip_obj.is_global and not ip_obj.is_private:
                    if ip_obj.version == 4:
                        ip_details = requests.get(f"https://ipinfo.io/{ip}/json").json()
                        if 'country' in ip_details and ip_details['country'] in countries_to_keep:
                            filtered_ipv4_addresses.add(ip)
            except ValueError:
                # 忽略无效IP地址
                continue

        # 读取现有的IP地址列表
        existing_ips = set()
        if os.path.exists(ip_filename):
            with open(ip_filename, 'r') as file:
                existing_ips.update(file.read().splitlines())

        # 保存符合条件的IP地址到 Fission_ip.txt
        with open(ip_filename, 'w') as output_file:
            for address in filtered_ipv4_addresses:
                output_file.write(address + '\n')

        # 保存符合条件的IP地址及其国家代码到 Fission_ip2cc.txt
        with open(ip2cc_filename, 'w') as output_file:
            for address in filtered_ipv4_addresses:
                ip_details = requests.get(f"https://ipinfo.io/{address}/json").json()
                country_code = ip_details.get('country', 'Unknown')
                output_file.write(f"{address},{country_code}\n")

    except Exception as e:
        print(f"Error performing DNS lookups and filtering countries: {e}")

# 主函数
def main():
    try:
        # 判断是否存在IP文件和域名文件
        if not os.path.exists(ips):
            with open(ips, 'w') as file:
                file.write("")
        
        if not os.path.exists(domains):
            with open(domains, 'w') as file:
                file.write("")

        # IP反查域名
        with open(ips, 'r') as ips_txt:
            ip_list = [ip.strip() for ip in ips_txt]

        domain_list = fetch_domains_concurrently(ip_list)
        with open(domains, "r") as file:
            exist_list = [domain.strip() for domain in file]

        domain_list = list(set(domain_list + exist_list))

        with open(domains, "w") as output:
            for domain in domain_list:
                output.write(domain + "\n")
        print("IP -> 域名 查询完成")

        # 域名解析IP并过滤指定国家的IP
        perform_dns_lookups_and_filter_countries(domains, ips, ip2cc_result, country_file)

        print("域名 -> IP 查询完成")

    except Exception as e:
        print(f"主函数运行出错: {e}")

# 程序入口
if __name__ == '__main__':
    main()
