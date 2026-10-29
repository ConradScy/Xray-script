中文 | [English](/.github/README.en.md)

# Xray 管理脚本 :sparkles:

* 一个纯 Shell 编写的 Xray 管理脚本
* 可选配置:
  * mKCP (VLESS-mKCP-seed)
  * Vision (VLESS-Vision-REALITY)
  * XHTTP (VLESS-XHTTP-REALITY)
  * trojan (Trojan-XHTTP-REALITY)
  * Fallback (包含 VLESS-Vision-REALITY、VLESS-XHTTP-REALITY)
  * SNI (包含 Vision_REALITY、XHTTP_REALITY、XHTTP_TLS)
* SNI 配置由 Nginx 实现 SNI 分流，适合过 CDN、上下行分离、多网站共存等需求
* SNI 分享链接实现了上下行分离(上行 xhttp+TLS+CDN | 下行 xhttp+Reality、上行 xhttp+Reality | 下行 xhttp+TLS+CDN)
* SNI 配置支持管理自定义域名与反代应用，每个站点拥有独立证书、独立站点配置、独立 stream 映射与 UDS
* 规则配置与自填:
  * 禁止 bittorrent 流量(可选)
  * 禁止回国 ip 流量(可选)
  * 屏蔽广告(可选)
  * 添加自定义 WARP Proxy 分流
  * 添加自定义屏蔽分流
* 开关 Cloudflare WARP Proxy( :whale: Docker 部署)
* 开关 geodata 自动更新功能
* xray 端口默认与自填:
  * VLESS-mKCP: 随机生成
  * ALL-REALITY: 443
* UUID 默认与自填:
  * 随机生成
  * 自定义输入标准 UUID
  * 非标准 UUID 映射转化为 UUID
* kcp(seed) 和 trojan(password) 默认与自填:
  * 随机生成(格式: cw-GEMDYgwIV3_g#)
  * 自定义输入
* target 默认与自填:
  * 随机在 serverNames.json 中获取
  * 实现自填 target 的 TLSv1.3 与 H2 验证
  * 实现自填 target 的 serverNames 自动获取
* shortId 默认与自填:
  * 随机生成(默认两个 shortId 例如: 01234567, 0123456789abcdef)
  * 实现自填 shortId
  * 实现输入值为 0 到 8, 则自动生成对 0-16 长度的 shortId
  * 支持逗号分隔的多个值
* path 默认与自填:
  * 随机生成(格式: /8ugSUeNJ.9OEnTErb.dVZMUAFu)
  * 自定义输入(格式: /8ugSUeNJ, 加不加 `/` 都可以)

## 问题

1. 如果安装成功，但无法使用，请检查服务器是否开启对应端口，可通过 `https://tcp.ping.pe/ip:port` 验证服务器端口是否开放。
2. 使用 SNI 配置前，请确保 VPS 的 HTTP(80) 与 HTTPS(443) 端口开放。
3. 使用 SNI 配置前，请不要开启 CDN 保护，不然无法正常申请 SSL 证书。
4. 上下行分离详情请看 [XHTTP: Beyond REALITY][XHTTP] 与 [xhttp 五合一配置][xhttp 五合一配置] 了解。
5. 使用 SNI 获取证书时遇到 【Could not get nonce, let's try again】 请查看 [ZeroSSL 状态页](https://status.zerossl.com/)，大概率是 ZeroSSL 的【Free ACME Service】处于 【Service disruption】或【Service outage】状态。

## 更新日志

* **v2026.10.29（ConradScy fork）**：兼容 Xray-core v26.10.21 配套的新版官方安装器。
  * 移除官方已不支持的 `-u root` 参数（会导致 `unknown option -- -`），使用 `--no-update-service` 保留现有 Xray unit；全新安装遵循官方专属 `xray` 用户，不强制降级或切回 root。
  * 修复全新 SNI 安装时 `xray` 用户无法创建 Nginx Unix socket 的权限问题，仅开放 `xray` 组写权限；已存在的本脚本旧 Nginx unit 会先备份，再仅迁移对应目录权限指令。
  * 安装引导、默认配置和自更新统一使用 `ConradScy/Xray-script`；脚本自更新仅更改配置的 `.version`，保留路径、域名、UUID、REALITY 密钥及其他设置。
  * 下载失败、版本 API 异常、安装器失败或实际版本不符时中止后续运行配置/分享链接步骤；成功后记录实际安装版本。核心更新不会覆盖完整已有 GeoData 数据对；原有 GeoData 手动/定时更新功能不变。


1. v2025.11.19 版本解决【开启 WARP 时没有设置日志限制，导致容器日志会一直叠加，最终占满硬盘空间】问题。
   1. 已启动 WARP 分流的用户可以在【管理配置】->【分流管理】中选择【重置 WARP Proxy】选项，该选项实现清空容器日志与重置 WARP Proxy。
   2. 已添加日志限制，如需使用 WARP 功能直接启用即可。
2. v2026.03.01 版本添加切换 CA 厂商功能，切换 CA 厂商时，脚本会对现有域名证书执行强制重签（`domain`、`cdn` 与 `custom_sites[].domain`），全部成功后才写入新 CA；若中途失败会自动回滚到原 CA，并恢复对应配置，避免影响后续 acme 自动续期任务。
   1. 强制重签会绕过「域名未变化」检查（acme.sh 的 `Domains not changed` 提示场景）。
   2. 请留意 CA 侧签发频率限制（例如 Let's Encrypt 的速率限制）。
3. v2026.03.17 版本添加 SNI 自定义域名与反代应用管理功能，用于在不影响现有 `Reality(domain)` 与 `CDN(cdn)` 站点的前提下，额外挂载多个 HTTPS 反代站点。
   1. 菜单路径：`管理配置 -> SNI 配置管理 -> 管理自定义域名与反代应用`
   2. 支持列表 / 新增 / 编辑 / 删除，自定义站点拥有独立证书、独立 `sites-available/<domain>.conf`、独立 stream 映射与 UDS。
   3. `stream.conf` 会按 `domain`、`cdn`、`custom_sites` 全量重建；UDS 名称规则为 `SHA-256(域名)` 前 `12` 位 + 端口号。
   4. 反代目标支持仅输入端口或完整 `http(s)://host:port` 地址；仅输入端口时自动规范化为 `http://127.0.0.1:<port>`；不接受带 `path`、`query`、`fragment` 的地址。
   5. 编辑时仅修改上游地址不会重复签发证书；修改域名时会先申请新证书，再切换配置，失败自动回滚；删除时会同时清理站点配置、软链、续签记录与证书目录。
   6. 自定义域名不能与 `domain`、`cdn` 或其他自定义站点域名重复；切换 CA 厂商与“强制续签所有证书”都会覆盖自定义站点域名。
   7. 自定义站点为“纯反代站点”，不包含 Xray 的 `xhttp/grpc` 路径，也不接入 Cloudreve 管理逻辑；代理层不会额外注入 `Content-Security-Policy`，由上游应用自行控制 CSP。

## 分享链接

基于[VMessAEAD / VLESS 分享链接标准提案](https://github.com/XTLS/Xray-core/discussions/716)与[v2rayN](https://github.com/2dust/v2rayN)实现，如果其他客户端无法正常使用，请自行根据分享链接进行修改。

SNI 配置中，CDN 的分享链接 Alpn 默认为 H2，如有 H3 需求，请自行在客户端修改。

## 如何使用

请在支持 systemd 的服务器上使用 root 终端运行。以下命令始终从本 fork 获取脚本，保持 HTTPS 证书验证。

**已有安装升级**：使用下方「快速启动(界面)」，有脚本更新提示时选择 `Y`，再选择「仅安装/更新」。不要为了升级而执行「完整安装」或 `--vision`，后者是重新生成配置的安装流程，可能改变客户端凭据。升级会保留现有 Xray unit，以及 `${DAT_PATH:-/usr/local/share/xray}` 下完整、非空的 GeoIP/GeoSite 数据对，避免新版官方默认数据源变化影响已有分类；全新安装或数据不完整时使用官方数据，自定义 `gfw` 等分类请按官方说明检查兼容性；非本脚本建立的自定义运行用户/权限需要按 [官方迁移说明](https://github.com/XTLS/Xray-install#readme) 检查。

**全新安装**：可进入界面安装，或下载后运行 `bash "${HOME}/Xray-script.sh" --vision`。核心仍由官方 `XTLS/Xray-install` 下载和校验，管理脚本及后续脚本更新来自此 fork。


* 获取

  ```sh
  curl -fsSL --retry 3 https://raw.githubusercontent.com/ConradScy/Xray-script/main/install.sh -o "${HOME}/Xray-script.sh"
  ```

* 使用
  * 启动界面

    ```sh
    bash "${HOME}/Xray-script.sh"
    ```

  * 快速安装 Vision

    ```sh
    bash "${HOME}/Xray-script.sh" --vision
    ```

  * 快速安装 XHTTP

    ```sh
    bash "${HOME}/Xray-script.sh" --xhttp
    ```

  * 快速安装 Fallback

    ```sh
    bash "${HOME}/Xray-script.sh" --fallback
    ```

* 快速启动(界面)

  ```sh
  curl -fsSL --retry 3 https://raw.githubusercontent.com/ConradScy/Xray-script/main/install.sh -o "${HOME}/Xray-script.sh" && bash "${HOME}/Xray-script.sh"
  ```

## 验证与兼容性边界

```sh
python3 -m unittest discover -s tests -v
# 可选：提供已下载的官方脚本 / 解压的核心，仅做参数解析与配置检查，不安装服务
XRAY_INSTALLER_SOURCE=/path/to/install-release.sh XRAY_BINARY=/path/to/xray python3 -m unittest discover -s tests -v
```

已使用官方 v26.10.21（`1b1647c`）对六种服务端模板执行 `xray run -test`，并离线测试安装参数、失败保护、自更新和 SNI 权限。配置语法检查不等于真实 VPS 网络连通性/客户端握手测试，未在测试环境安装或启动服务。新版本 mKCP 的 `seed` 等旧参数不再具备原先语义，且新客户端对公网明文 VLESS 有限制；不保证旧 mKCP 客户端继续互通，新装优先使用 Vision/REALITY。参见 [v26.10.21 transport 实现](https://github.com/XTLS/Xray-core/blob/v26.10.21/infra/conf/transport_method.go)。

## 脚本界面

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
WARP Proxy : 已启动
-------------------------------------------

--------------- Xray-script ---------------
 Version      : v2025-07-25
 Description  : Xray 管理脚本
----------------- 装载管理 ----------------
1. 完整安装
2. 仅安装/更新
3. 卸载
----------------- 操作管理 ----------------
4. 启动
5. 停止
6. 重启
----------------- 配置管理 ----------------
7. 分享链接与二维码
8. 信息统计
9. 管理配置
-------------------------------------------
0. 退出
```

## 已测试系统

| Platform | Version    |
| -------- | ---------- |
| Debian   | 10, 11, 12 |
| Ubuntu   | 20, 22, 24 |
| CentOS   | 7, 8, 9    |
| Rocky    | 8, 9       |

以上发行版均通过 Vultr 测试安装。

其他 Debian 基系统与 Red Hat 基系统可能能用，但未测试过，可能存在问题。

## 安装时长说明

SNI 配置适合安装一次后长期使用，不适合反复重置系统安装，这会消耗您的大量时间。如果需要更换配置和域名等，在管理界面都有相应的选项。

更换为非 SNI 配置后，Nginx 将停止服务，但会继续保留在本机，再启用 SNI 配置时不会进行重新安装。

### 安装时长参考

安装流程：

更新系统管理包->安装依赖->[安装Docker]->[安装Cloudreve]->[安装Cloudflare-warp]->安装Xray->安装Nginx->申请证书->配置文件

**这是一台单核1G的服务器的平均安装时长，仅供参考：**

| 项目                | 时长      |
| ------------------- | --------- |
| 更新系统管理包      | 0-10分钟  |
| 安装依赖            | 0-5分钟   |
| 安装Docker          | 1-2分钟   |
| 安装Cloudreve       | 3-5分钟   |
| 安装Cloudflare-warp | 3-5分钟   |
| 安装Xray            | <半分钟   |
| 安装Nginx           | 13-15分钟 |
| 申请证书            | 1-2分钟   |
| 配置文件            | <半分钟  |

### 为什么 SNI 配置安装时间那么长？

脚本的 Nginx 是采用源码编译的形式进行管理安装。

编译相比直接安装二进制文件的优点有：

1. 运行效率高 (编译时采用了-O3优化)
2. 软件版本新

缺点就是编译耗时长。

## 安装位置

**Xray-script:** `/usr/local/xray-script`

**Nginx:** `/usr/local/nginx`

**Cloudreve:** `$HOME/.xray-script/docker/cloudreve`

**Cloudflare-warp:** `$HOME/.xray-script/docker/warp`

**Xray:** 见 **[Xray-install](https://github.com/XTLS/Xray-install)**

## 依赖列表

使用 SNI 配置时，脚本可能自动安装以下依赖：
| 用途                            | Debian基系统                         | Red Hat基系统       |
| ------------------------------- | ------------------------------------ | ------------------- |
| yumdb set(标记包手动安装)       |                                      | yum-utils           |
| dnf config-manager              |                                      | dnf-plugins-core    |
| IP 获取                         | iproute2                             | iproute             |
| DNS 解析                        | dnsutils                             | bind-utils          |
| wget                            | wget                                 | wget                |
| curl                            | curl                                 | curl                |
| wget/curl https                 | ca-certificates                      | ca-certificates     |
| kill/pkill/ps/sysctl/free       | procps                               | procps-ng           |
| epel源                          |                                      | epel-release        |
| epel源                          |                                      | epel-next-release   |
| remi源                          |                                      | remi-release        |
| 防火墙                          | ufw                                  | firewalld           |
| **编译基础：**                  |                                      |                     |
| 下载源码文件                    | wget                                 | wget                |
| 解压tar源码文件                 | tar                                  | tar                 |
| 解压tar.gz源码文件              | gzip                                 | gzip                |
| gcc                             | gcc                                  | gcc                 |
| g++                             | g++                                  | gcc-c++             |
| make                            | make                                 | make                |
| **acme.sh依赖：**               |                                      |                     |
|                                 | curl                                 | curl                |
|                                 | openssl                              | openssl             |
|                                 | cron                                 | crontabs            |
| **编译openssl：**               |                                      |                     |
|                                 | perl-base(包含于libperl-dev)         | perl-IPC-Cmd        |
|                                 | perl-modules-5.32(包含于libperl-dev) | perl-Getopt-Long    |
|                                 | libperl5.32(包含于libperl-dev)       | perl-Data-Dumper    |
|                                 |                                      | perl-FindBin        |
| **编译Brotli：**                |                                      |                     |
|                                 | git                                  | git                 |
|                                 | libbrotli-dev                        | brotli-devel        |
| **编译Nginx：**                 |                                      |                     |
|                                 | libpcre2-dev                         | pcre2-devel         |
|                                 | zlib1g-dev                           | zlib-devel          |
| --with-http_xslt_module         | libxml2-dev                          | libxml2-devel       |
| --with-http_xslt_module         | libxslt1-dev                         | libxslt-devel       |
| --with-http_image_filter_module | libgd-dev                            | gd-devel            |
| --with-google_perftools_module  | libgoogle-perftools-dev              | gperftools-devel    |
| --with-http_geoip_module        | libgeoip-dev                         | geoip-devel         |
| --with-http_perl_module         |                                      | perl-ExtUtils-Embed |
|                                 | libperl-dev                          | perl-devel          |

## 致谢

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

**此脚本仅供交流学习使用，请勿使用此脚本行违法之事。网络非法外之地，行非法之事，必将接受法律制裁。**

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
