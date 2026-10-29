<!-- Translated by AI -->
[中文](/README.md) | English

# Xray Management Script :sparkles:

* A pure Shell-based Xray management script
* Optional configurations:
  * mKCP (VLESS-mKCP-seed)
  * Vision (VLESS-Vision-REALITY)
  * XHTTP (VLESS-XHTTP-REALITY)
  * Trojan (Trojan-XHTTP-REALITY)
  * Fallback (includes VLESS-Vision-REALITY and VLESS-XHTTP-REALITY)
  * SNI (includes Vision_REALITY, XHTTP_REALITY, XHTTP_TLS)
* SNI configuration uses Nginx to implement SNI traffic splitting, suitable for CDN routing, upstream/downstream separation, and multi-site coexistence
* SNI share links support upstream/downstream separation (upstream xhttp+TLS+CDN | downstream xhttp+Reality, upstream xhttp+Reality | downstream xhttp+TLS+CDN)
* SNI configuration supports managing custom domains and reverse proxy apps, with independent certificates, site configs, stream mappings, and UDS per site
* Rule configuration and custom input:
  * Block BitTorrent traffic (optional)
  * Block China-bound IP traffic (optional)
  * Ad blocking (optional)
  * Add custom WARP Proxy routing rules
  * Add custom blocking routing rules
* Toggle Cloudflare WARP Proxy ( :whale: Docker deployment)
* Toggle geodata auto-update
* Xray port defaults and custom input:
  * VLESS-mKCP: randomly generated
  * ALL-REALITY: 443
* UUID defaults and custom input:
  * Randomly generated
  * Custom standard UUID input
  * Non-standard UUID mapped to UUID
* kcp(seed) and trojan(password) defaults and custom input:
  * Randomly generated (example: cw-GEMDYgwIV3_g#)
  * Custom input
* target defaults and custom input:
  * Randomly selected from serverNames.json
  * TLSv1.3 and H2 validation for custom target
  * Automatic serverNames lookup for custom target
* shortId defaults and custom input:
  * Random generation (default two shortIds, e.g. 01234567, 0123456789abcdef)
  * Custom shortId input
  * If input is 0 to 8, shortIds with length 0-16 are auto-generated
  * Supports multiple values separated by commas
* path defaults and custom input:
  * Randomly generated (example: /8ugSUeNJ.9OEnTErb.dVZMUAFu)
  * Custom input (example: /8ugSUeNJ, with or without `/`)

## FAQ

1. If installation succeeds but service is unusable, check whether the server ports are open. You can verify using `https://tcp.ping.pe/ip:port`.
2. Before using SNI configuration, ensure VPS HTTP (80) and HTTPS (443) ports are open.
3. Before using SNI configuration, do not enable CDN protection, otherwise SSL issuance may fail.
4. For upstream/downstream separation details, see [XHTTP: Beyond REALITY][XHTTP] and [xhttp 五合一配置][xhttp 五合一配置].
5. If you encounter 【Could not get nonce, let's try again】 while issuing certificates with SNI, check the [ZeroSSL status page](https://status.zerossl.com/). Most likely ZeroSSL 【Free ACME Service】 is in 【Service disruption】 or 【Service outage】.

## Changelog

* **v2026.10.29.2**: adds a restricted CentOS 7 existing-core upgrade path instead of stopping the service and then failing on missing `systemd-sysusers`. Downloads, digest/archive checks and candidate configuration tests precede any stop. Accounts, units, configuration and GeoData are preserved; failures attempt original-core/service recovery. Other distributions retain the official installer with a real sysusers preflight. Independent edits now use systemd 219-compatible property reads and preserve SELinux file labels.

* **v2026.10.29.1:** adds independent UUID, REALITY key-pair, ShortId and port editing. Targeted updates preserve unrelated runtime settings, with explicit confirmation, secure backups, candidate validation and recovery on failure. Successful edits show client synchronization steps and required new values, never private keys. SNI external port changes are rejected before mutation.

* **v2026.10.29 (ConradScy fork)**: fixes the official installer's removed `-u root` option with `--no-update-service`, preserving existing Xray units while fresh installs use the dedicated `xray` user. Grants only the Xray group write access to the SNI socket directory (with a narrow, backed-up migration for this script's legacy Nginx unit). Bootstrap, default configuration and self-updates now consistently use this fork. Failed downloads, invalid versions and installer errors stop subsequent runtime-configuration/share-link steps; successful installation records the actual version. Core upgrades preserve a complete existing GeoData pair; existing manual/scheduled GeoData updates are unchanged.


1. v2025.11.19 resolves the issue where WARP was enabled without log limits, causing container logs to keep growing and eventually fill up disk space.
   1. Users who already enabled WARP routing can select 【Reset WARP Proxy】 in 【Manage Configuration】 -> 【Routing Management】 to clear container logs and reset WARP Proxy.
   2. Log limits have been added; just enable WARP directly when needed.
2. v2026.03.01 adds CA vendor switching. When switching CA vendor, the script force re-issues certificates for existing domains (`domain`, `cdn`, and `custom_sites[].domain`). It writes the new CA only after all re-issues succeed; if any step fails, it automatically rolls back to the original CA and restores related settings, preventing acme auto-renew from breaking.
   1. Force re-issue bypasses the "Domains not changed" check (acme.sh skip scenario).
   2. Watch out for CA issuance rate limits (for example, Let's Encrypt limits).
3. v2026.03.17 adds SNI custom domain and reverse proxy app management, allowing multiple extra HTTPS reverse proxy sites without affecting the existing `Reality(domain)` and `CDN(cdn)` sites.
   1. Menu path: `Manage Configuration -> SNI Configuration -> Manage custom domains and reverse proxy apps`
   2. Supports list / add / edit / delete, with an independent certificate, `sites-available/<domain>.conf`, stream mapping, and UDS for each custom site.
   3. `stream.conf` is rebuilt from `domain`, `cdn`, and `custom_sites`; UDS names use the first `12` hex chars of `SHA-256(domain)` plus the port.
   4. Proxy targets support port-only input or full `http(s)://host:port` URLs; port-only input is normalized to `http://127.0.0.1:<port>`; URLs containing `path`, `query`, or `fragment` are rejected.
   5. Editing only the upstream target skips certificate re-issuance; changing the domain issues the new certificate first and then switches over with rollback on failure; deleting a site also removes the site config, symlink, renew record, and certificate directory.
   6. A custom site domain must not duplicate `domain`, `cdn`, or another custom site domain; CA switching and "force renew all certificates" also cover custom site domains.
   7. Custom sites are "pure reverse proxy sites". They do not include Xray `xhttp/grpc` paths and are not managed by the Cloudreve integration; the proxy layer does not inject an extra `Content-Security-Policy`, so CSP should be controlled by the upstream application.

## Share Links

Implemented based on [VMessAEAD / VLESS share link proposal](https://github.com/XTLS/Xray-core/discussions/716) and [v2rayN](https://github.com/2dust/v2rayN). If other clients do not work, adjust based on the generated share link manually.

In SNI configuration, CDN share links use H2 as default ALPN. If you need H3, modify it in your client.

## How to Use

Run in a root shell on a supported systemd server. HTTPS certificate verification remains enabled.

**Existing installations:** use the UI quick-start command below, accept the script update with `Y`, then select **Install/Update only**. Do not use full installation or `--vision` just to upgrade: those flows regenerate configuration and may change client credentials. Script self-update changes only `.version` in the existing script configuration; paths, domains, UUIDs, REALITY keys and other settings remain intact. Existing Xray units and a complete, nonempty GeoIP/GeoSite pair under `${DAT_PATH:-/usr/local/share/xray}` are preserved, avoiding upstream data-source changes during a core upgrade. Fresh or incomplete data uses the official defaults; custom categories such as `gfw` need the official migration guidance; custom runtime users/permissions not created by this script require checking the [official migration instructions](https://github.com/XTLS/Xray-install#readme).

**Fresh installations:** use the UI or download first and run `bash "${HOME}/Xray-script.sh" --vision`. Xray-core still comes from the official `XTLS/Xray-install` installer; this management script and its future updates come from `ConradScy/Xray-script`.


* Download

  ```sh
  curl -fsSL --retry 3 https://raw.githubusercontent.com/ConradScy/Xray-script/main/install.sh -o "${HOME}/Xray-script.sh"
  ```

* Usage
  * Launch UI

    ```sh
    bash "${HOME}/Xray-script.sh"
    ```

  * Quick install Vision

    ```sh
    bash "${HOME}/Xray-script.sh" --vision
    ```

  * Quick install XHTTP

    ```sh
    bash "${HOME}/Xray-script.sh" --xhttp
    ```

  * Quick install Fallback

    ```sh
    bash "${HOME}/Xray-script.sh" --fallback
    ```

* Quick start (UI)

  ```sh
  curl -fsSL --retry 3 https://raw.githubusercontent.com/ConradScy/Xray-script/main/install.sh -o "${HOME}/Xray-script.sh" && bash "${HOME}/Xray-script.sh"
  ```

## Existing CentOS 7 servers

CentOS 7 system packages omit `systemd-sysusers`; do not reinstall systemd blindly or fake that command. For **existing standard x86_64 installations**, main menu **2 (Install/Update only)** uses a narrow core-only upgrade without invoking that installer path, creating accounts, changing the service to root, or downgrading Xray. Debian 11 and other systems with the proper tools still use the official installer.

The service must use `/usr/local/bin/xray run -config /usr/local/etc/xray/config.json` under an existing `root` or `xray` user. Custom wrappers, extra arguments, confdir, environment files and startup hooks fail closed before any stop. A previously running service is restarted; a stopped service stays stopped. Only `.xray.version` is updated under the same edit lock; other settings are retained.

- Official ZIP/digest download, strict SHA256 and archive-member checks, runnable candidate and configuration validation all happen before stopping Xray.
- Private backups are kept in `${HOME}/.xray-script/backups/core.*` (directory `0700`, original binary backup `0600`). Ownership, permissions, file capabilities and SELinux labels are preserved. Backups may include configuration secrets; do not publish them.
- On write/start/version-state failure, a possibly lingering new process is stopped before atomic old-core restoration and recovery of the original running state. Incomplete recovery is reported with the backup location; investigate before retrying.
- Requires existing `curl`, `unzip`, `sha256sum`, `stat`, `flock`, `getcap` (usually `libcap`), plus `runuser` for non-root services. Missing dependencies abort early; the compatibility path never changes package repositories, installs packages, disables SELinux or modifies firewalls.
- **Fresh/quick/full CentOS 7 installations and complete configuration regeneration are unsupported.** Migrate to a supported distribution for those operations. Independent field edits remain available for existing configurations. This compatibility route does not extend CentOS 7 security support.

Official v26.10.29 (`0afec66`) was checked using binary/config validation and isolated failures covering legacy systemd output, downloads, digests, startup, rollback and concurrent changes. No actual CentOS 7 VPS service was installed or operated during testing.

## Independently change UUID / REALITY keys / ShortId / port

Open main menu **`9 Configuration Management`**:

| Option | Change | Scope and limitations |
| --- | --- | --- |
| `4` | Listen port | Non-SNI only; changes the actual port of the managed inbound |
| `7` | UUID | Main managed VLESS account only; preserves other users and the separate Fallback UUID; unavailable for Trojan |
| `8` | Regenerate REALITY key pair | Managed REALITY inbound only; all clients using that inbound must update its public key; unavailable for mKCP |
| `9` | ShortId | Managed REALITY inbound only; affected clients must update their ShortId; unavailable for mKCP |

These operations patch the selected fields in the current `/usr/local/etc/xray/config.json` and synchronize the script state. They **do not regenerate the full configuration from a template**. Custom routing, unrelated inbounds, other users and unrelated fields are preserved. Edits require a unique match for the script-managed inbound/account; mismatched state or ambiguous targets are rejected rather than guessed or applied to every account.

UUID input must use the standard 36-character format. ShortId input replaces the entire list: comma-separated values, each containing 2–16 hexadecimal characters with even length, with no empty or duplicate entries. UUID and ShortId input is hidden; empty input cancels instead of generating a random value. Ports must be decimal integers from `1` to `65535`.

A client-synchronization warning and literal uppercase `YES` confirmation are required before applying a change. Key generation happens only after confirmation. UUID edits affect the main account; keys, ShortId and port are inbound-level settings and affect clients using that inbound. Canceling does not write configuration or restart the service. **Synchronize affected clients after a successful server change**; standard generated configurations can use main menu `7 Share links and QR codes` for current share information. Synchronize custom multi-user accounts manually: the existing share menu may not represent a non-first managed account. The independent-edit flow does not automatically print private keys or complete configurations.

After a successful edit, the terminal explains the corresponding client field and displays the new value to synchronize. Failure or cancellation does not display newly generated credentials; **the REALITY private key is never printed**:

- **UUID:** replace the client's User ID / UUID / id with the displayed new UUID.
- **REALITY key pair:** update PublicKey / publicKey / pbk in the client's REALITY settings with the displayed new public key. Some clients call this REALITY `password`; it is not a Trojan password or the server private key.
- **ShortId:** the displayed values are the complete new server allow-list. Choose **one** complete value for ShortId / shortId / sid; do not paste the whole comma-separated list.
- **Port:** update the client's **remote server port / port**, not its local proxy listening port. Ensure the server firewall, cloud security group and any port forwarding allow the new port.

Change only the selected field and leave other settings unchanged. Save, disconnect, reconnect and test every affected client. Standard generated configurations can still obtain a new share link through main menu `7`; synchronize custom multi-user accounts manually.

Confirmed edits back up the runtime configuration and script state under `${HOME}/.xray-script/backups/edit.*` (directory mode `0700`, backup file mode `0600`). Candidates must pass `xray run -test` before replacement. An initially running service is restarted; an initially stopped service stays stopped. A write or restart failure triggers restoration of the previous configuration and reports the recovery result and backup location. Backups contain credentials: do not publish them. Investigate any unsuccessful recovery before retrying changes.

**Nginx owns SNI external ports**: the port editor rejects SNI before making changes, instead of creating a share link that disagrees with the real listener. It does not modify Nginx, firewalls, cloud security groups or port forwarding. Verify reachability of the new port before changing a non-SNI listener too.

## Validation and limits

```sh
python3 -m unittest discover -s tests -v
# Optional downloaded official installer / extracted core: parsing and config tests only
XRAY_INSTALLER_SOURCE=/path/to/install-release.sh XRAY_BINARY=/path/to/xray python3 -m unittest discover -s tests -v
```

All six server templates pass `xray run -test` using official v26.10.21 (`1b1647c`). Offline regression tests cover installer arguments, failure handling, self-updates and SNI permissions. Syntax checks are not live VPS connectivity/client handshake tests; no services were installed or started during validation. Recent mKCP no longer uses legacy `seed` settings as before, and newer clients restrict plaintext VLESS to public addresses. Legacy mKCP interoperability is not guaranteed; prefer Vision/REALITY for new installs. See the [v26.10.21 transport implementation](https://github.com/XTLS/Xray-core/blob/v26.10.21/infra/conf/transport_method.go).

## Script UI

```sh
 __   __  _    _   _______   _______   _____  
 \ \ / / | |  | | |__   __| |__   __| |  __ \ 
  \ V /  | |__| |    | |       | |    | |__) |
   > <   |  __  |    | |       | |    |  ___/ 
  / . \  | |  | |    | |       | |    | |     
 /_/ \_\ |_|  |_|    |_|       |_|    |_|     

Copyright (C) zxcvos | https://github.com/zxcvos/Xray-script

-------------------------------------------
Xray       : v25.7.26
CONFIG     : VLESS-Vision-REALITY
WARP Proxy : Running
-------------------------------------------

--------------- Xray-script ---------------
 Version      : v2025-07-25
 Description  : Xray Management Script
----------------- Install -----------------
1. Full installation
2. Install/Update only
3. Uninstall
----------------- Operation ----------------
4. Start
5. Stop
6. Restart
---------------- Configuration -------------
7. Share links and QR codes
8. Statistics
9. Manage configuration
-------------------------------------------
0. Exit
```

## Tested Systems

| Platform | Version    |
| -------- | ---------- |
| Debian   | 10, 11, 12 |
| Ubuntu   | 20, 22, 24 |
| CentOS   | 8, 9; 7 existing-core upgrades only |
| Rocky    | 8, 9       |

The distributions above were tested on Vultr.

Other Debian-based and Red Hat-based systems may work, but are untested and may have issues.

## Installation Time Notes

SNI configuration is intended for long-term use after one setup, and is not suitable for repeated reinstall/reset, which consumes significant time. If you need to change configuration or domain, use the options in the management UI.

After switching to a non-SNI config, Nginx will be stopped but kept on the machine. Re-enabling SNI will not reinstall Nginx.

### Installation Time Reference

Installation flow:

Update package index -> install dependencies -> [install Docker] -> [install Cloudreve] -> [install Cloudflare-warp] -> install Xray -> install Nginx -> issue certificate -> apply configuration

**Average install time on a 1-core 1GB server (for reference only):**

| Item                | Duration  |
| ------------------- | --------- |
| Update package index| 0-10 min  |
| Install dependencies| 0-5 min   |
| Install Docker      | 1-2 min   |
| Install Cloudreve   | 3-5 min   |
| Install Cloudflare-warp | 3-5 min |
| Install Xray        | < 0.5 min |
| Install Nginx       | 13-15 min |
| Issue certificate   | 1-2 min   |
| Apply configuration | < 0.5 min |

### Why does SNI installation take so long?

Nginx in this script is managed by source compilation.

Compared with installing prebuilt binaries, compilation advantages are:

1. Better runtime performance (compiled with -O3 optimization)
2. Newer software versions

The downside is long compilation time.

## Install Paths

**Xray-script:** `/usr/local/xray-script`

**Nginx:** `/usr/local/nginx`

**Cloudreve:** `$HOME/.xray-script/docker/cloudreve`

**Cloudflare-warp:** `$HOME/.xray-script/docker/warp`

**Xray:** See **[Xray-install](https://github.com/XTLS/Xray-install)**

## Dependency List

When using SNI configuration, the script may install the following dependencies:

| Purpose                            | Debian-based                         | Red Hat-based        |
| ---------------------------------- | ------------------------------------ | -------------------- |
| yumdb set (mark package manually installed) |                              | yum-utils            |
| dnf config-manager                 |                                      | dnf-plugins-core     |
| IP retrieval                       | iproute2                             | iproute              |
| DNS resolution                     | dnsutils                             | bind-utils           |
| wget                               | wget                                 | wget                 |
| curl                               | curl                                 | curl                 |
| wget/curl https                    | ca-certificates                      | ca-certificates      |
| kill/pkill/ps/sysctl/free          | procps                               | procps-ng            |
| epel repository                    |                                      | epel-release         |
| epel repository                    |                                      | epel-next-release    |
| remi repository                    |                                      | remi-release         |
| Firewall                           | ufw                                  | firewalld            |
| **Build basics:**                  |                                      |                      |
| Download source files              | wget                                 | wget                 |
| Extract tar source files           | tar                                  | tar                  |
| Extract tar.gz source files        | gzip                                 | gzip                 |
| gcc                                | gcc                                  | gcc                  |
| g++                                | g++                                  | gcc-c++              |
| make                               | make                                 | make                 |
| **acme.sh dependencies:**          |                                      |                      |
|                                    | curl                                 | curl                 |
|                                    | openssl                              | openssl              |
|                                    | cron                                 | crontabs             |
| **Build openssl:**                 |                                      |                      |
|                                    | perl-base (included in libperl-dev) | perl-IPC-Cmd         |
|                                    | perl-modules-5.32 (included in libperl-dev) | perl-Getopt-Long |
|                                    | libperl5.32 (included in libperl-dev) | perl-Data-Dumper   |
|                                    |                                      | perl-FindBin         |
| **Build Brotli:**                  |                                      |                      |
|                                    | git                                  | git                  |
|                                    | libbrotli-dev                        | brotli-devel         |
| **Build Nginx:**                   |                                      |                      |
|                                    | libpcre2-dev                         | pcre2-devel          |
|                                    | zlib1g-dev                           | zlib-devel           |
| --with-http_xslt_module            | libxml2-dev                          | libxml2-devel        |
| --with-http_xslt_module            | libxslt1-dev                         | libxslt-devel        |
| --with-http_image_filter_module    | libgd-dev                            | gd-devel             |
| --with-google_perftools_module     | libgoogle-perftools-dev              | gperftools-devel     |
| --with-http_geoip_module           | libgeoip-dev                         | geoip-devel          |
| --with-http_perl_module            |                                      | perl-ExtUtils-Embed  |
|                                    | libperl-dev                          | perl-devel           |

## Acknowledgements

[Xray-core][Xray-core]

[REALITY][REALITY]

[XHTTP: Beyond REALITY][XHTTP]

[integrated-examples][lxhao61/integrated-examples]

[xhttp 五合一配置][xhttp 五合一配置]

[部署 Cloudflare WARP Proxy][haoel]

[cloudflare-warp 镜像][e7h4n]

[V2Ray 路由规则文件加强版][v2ray-rules-dat]

[kirin10000/Xray-script][kirin10000/Xray-script]

[Cloudreve][cloudreve]

**This script is for study and communication only. Do not use it for illegal purposes. Illegal acts on the network are still illegal and will be punished by law.**

[Xray-core]: https://github.com/XTLS/Xray-core (THE NEXT FUTURE)
[REALITY]: https://github.com/XTLS/REALITY (THE NEXT FUTURE)
[XHTTP]: https://github.com/XTLS/Xray-core/discussions/4113 (XHTTP: Beyond REALITY)
[lxhao61/integrated-examples]: https://github.com/lxhao61/integrated-examples (以 V2Ray（v4 版） 或 Xray、Nginx 或 Caddy（v2 版）、Hysteria 等打造常用科学上网的优化配置及最优组合示例，且提供集成特定插件的 Caddy（v2 版） 文件，分享给大家食用及自己备份。)
[xhttp 五合一配置]: https://github.com/XTLS/Xray-core/discussions/4118 (xhttp 五合一配置 \( reality 直连与过 CDN 共存, 附小白可抄的配置\))
[haoel]: https://github.com/haoel/haoel.github.io#943-docker-%E4%BB%A3%E7%90%86 (使用 Docker 快速部署 Cloudflare WARP Proxy)
[e7h4n]: https://github.com/e7h4n/cloudflare-warp (cloudflare-warp 镜像)
[v2ray-rules-dat]: https://github.com/Loyalsoldier/v2ray-rules-dat (V2Ray 路由规则文件加强版)
[kirin10000/Xray-script]: https://github.com/kirin10000/Xray-script (kirin10000/Xray-script)
[cloudreve]: https://github.com/cloudreve/cloudreve (cloudreve)
