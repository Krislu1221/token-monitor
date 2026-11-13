#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
model-monitor poller + 本地常开面板 (v1, 零第三方依赖, 仅标准库)

用法:
  python3 poller.py            # 默认 127.0.0.1:8317
  PORT=9000 python3 poller.py  # 换端口
  浏览器打开 http://127.0.0.1:8317

密钥来源(只读, 绝不外传):
  1) ~/.nanobot/config.json  的 providers.*.apiKey (deepseek / zhipu / volcengine_agent_plan / qwen)
  2) 同名环境变量覆盖: DEEPSEEK_API_KEY / ZHIPU_API_KEY / VOLC_AGENT_PLAN_API_KEY / DASHSCOPE_API_KEY
"""
import json
import os
import re
import hashlib
import hmac
import datetime
import shutil
import subprocess
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NANOBOT_CFG = os.path.expanduser("~/.nanobot/config.json")
# 项目本地配置(推荐给开源用户): 同目录 config.json, 或 TOKEN_MONITOR_CONFIG 指向的文件
CONFIG_FILE = os.environ.get("TOKEN_MONITOR_CONFIG") or os.path.join(BASE_DIR, "config.json")
STATUS_FILE = os.path.join(BASE_DIR, "status.json")
MANUAL_FILE = os.path.join(BASE_DIR, "manual.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
HISTORY_MAX = 4000
CACHE_TTL = 300          # 5 分钟缓存
HTTP_TIMEOUT = 12
PORT = int(os.environ.get("PORT", "8317"))

# --------------------------------------------------------------------------
# 密钥装载: 从 nanobot config 读取, 允许环境变量覆盖
# --------------------------------------------------------------------------
def load_keys():
    cfg = {}
    # 1) 项目本地 config.json(开源版主路径; 已被 .gitignore 排除, 不会误提交)
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                local = json.load(f) or {}
            for k in ("deepseek", "zhipu", "minimax", "qwen",
                      "volc_agent_plan", "volc_ak", "volc_sk",
                      "aliyun_ak", "aliyun_sk"):
                if local.get(k):
                    cfg[k] = local[k]
    except Exception:
        pass
    # 2) 兼容既有 nanobot 配置(仅补缺, 不覆盖本地配置)
    try:
        with open(NANOBOT_CFG, encoding="utf-8") as f:
            data = json.load(f)
        prov = data.get("providers", {})
        if not cfg.get("deepseek"):
            cfg["deepseek"] = (prov.get("deepseek") or {}).get("apiKey")
        if not cfg.get("zhipu"):
            cfg["zhipu"] = (prov.get("zhipu") or {}).get("apiKey")
        if not cfg.get("volc_agent_plan"):
            cfg["volc_agent_plan"] = (prov.get("volcengine_agent_plan") or {}).get("apiKey")
        if not cfg.get("minimax"):
            cfg["minimax"] = (prov.get("minimax") or {}).get("apiKey")
        if not cfg.get("qwen"):
            q = prov.get("qwen") or {}
            cfg["qwen"] = q.get("apiKey") or (prov.get("dashscope") or {}).get("apiKey")
    except Exception:
        pass
    # 3) 环境变量优先级最高
    envmap = {"deepseek": "DEEPSEEK_API_KEY", "zhipu": "ZHIPU_API_KEY",
              "volc_agent_plan": "VOLC_AGENT_PLAN_API_KEY", "qwen": "DASHSCOPE_API_KEY"}
    for k, var in envmap.items():
        v = os.environ.get(var)
        if v:
            cfg[k] = v.strip()
    # 阿里云只读 AK/SK(读 ~/model-monitor/.creds/aliyun.env, 或环境变量)
    ali_env = os.path.join(BASE_DIR, ".creds", "aliyun.env")
    cfg.setdefault("aliyun_ak", os.environ.get("ALIYUN_AK_ID"))
    cfg.setdefault("aliyun_sk", os.environ.get("ALIYUN_AK_SECRET"))
    if os.path.exists(ali_env):
        try:
            with open(ali_env, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ALIYUN_AK_ID="):
                        cfg["aliyun_ak"] = line.split("=", 1)[1].strip().strip("\"'")
                    elif line.startswith("ALIYUN_AK_SECRET="):
                        cfg["aliyun_sk"] = line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    # 火山 AK/SK(长期方案; 缺省时回落 arkcli SSO)
    cfg.setdefault("volc_ak", os.environ.get("VOLC_AK_ID"))
    cfg.setdefault("volc_sk", os.environ.get("VOLC_AK_SECRET"))
    volc_env = os.path.join(BASE_DIR, ".creds", "volc.env")
    if os.path.exists(volc_env):
        try:
            with open(volc_env, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("VOLC_AK_ID="):
                        cfg["volc_ak"] = line.split("=", 1)[1].strip().strip("\"'")
                    elif line.startswith("VOLC_AK_SECRET="):
                        cfg["volc_sk"] = line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    return cfg

def mask(v):
    if not v:
        return None
    s = str(v)
    return s[:6] + "…" if len(s) > 10 else "***"

# --------------------------------------------------------------------------
# 通用 HTTP GET
# --------------------------------------------------------------------------
def http_get_json(url, headers=None, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

# --------------------------------------------------------------------------
# 适配器 1: DeepSeek 官方余额
# --------------------------------------------------------------------------
def query_deepseek_balance(key):
    try:
        body = http_get_json("https://api.deepseek.com/user/balance",
                             {"Authorization": f"Bearer {key}", "Accept": "application/json"})
        infos = body.get("balance_infos") or []
        if not infos:
            return {"ok": True, "available": bool(body.get("is_available")), "balance_infos": []}
        rows = [{
            "currency": b.get("currency"),
            "total": _to_num(b.get("total_balance")),
            "granted": _to_num(b.get("granted_balance")),
            "topped_up": _to_num(b.get("topped_up_balance")),
        } for b in infos]
        return {"ok": True, "available": bool(body.get("is_available")), "balance_infos": rows}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

# --------------------------------------------------------------------------
# 适配器 2: 智谱 GLM Coding Plan 配额 (open.bigmodel.cn / api.z.ai)
# --------------------------------------------------------------------------
def query_zhipu_quota(key, base="https://open.bigmodel.cn"):
    try:
        body = http_get_json(base + "/api/monitor/usage/quota/limit",
                             {"Authorization": key, "Accept": "application/json"})
        if body.get("success") is False:
            msg = str(body.get("msg") or "接口返回错误")
            if "coding plan" in msg.lower() or "codingplan" in msg.lower() or "不存在" in msg:
                return {"ok": True, "subscribed": False,
                        "note": "按量付费账户(未订阅 Coding Plan):官方无余额查询 API, 请看控制台或手填备忘"}
            return {"ok": False, "error": msg}
        data = body.get("data") or {}
        limits = data.get("limits") or []
        def pick(unit_target):
            out = None
            for it in limits:
                if not isinstance(it, dict):
                    continue
                if str(it.get("type", "")).lower() != "tokens_limit":
                    continue
                u = _to_num(it.get("unit"))
                if (unit_target == 3 and u == 3) or (unit_target == 6 and u == 6):
                    pct = _to_num(it.get("percentage"))
                    reset_ms = _to_num(it.get("nextResetTime"))
                    out = {"pct": pct, "resets_at": time.strftime("%m-%d %H:%M", time.localtime(reset_ms / 1000)) if reset_ms else None}
                    break
            return out
        five_hour = pick(3)
        weekly = pick(6)
        if not five_hour and not weekly:
            # 兜底: 取全部 TOKENS_LIMIT 按重置时间排序依次填槽
            cands = []
            for it in limits:
                if isinstance(it, dict) and str(it.get("type", "")).lower() == "tokens_limit":
                    cands.append(it)
            cands.sort(key=lambda x: _to_num(x.get("nextResetTime")) or 0)
            for it in cands:
                pct = _to_num(it.get("percentage"))
                reset_ms = _to_num(it.get("nextResetTime"))
                entry = {"pct": pct, "resets_at": time.strftime("%m-%d %H:%M", time.localtime(reset_ms / 1000)) if reset_ms else None}
                if not five_hour:
                    five_hour = entry
                elif not weekly:
                    weekly = entry
        if not five_hour and not weekly:
            return {"ok": False, "error": "响应缺少 TOKENS_LIMIT 条目"}
        return {"ok": True, "subscribed": True, "five_hour": five_hour, "weekly": weekly}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

# --------------------------------------------------------------------------
# 适配器 3: 火山方舟 Agent Plan 配额 (走官方 arkcli, 需一次性 SSO 登录)
# --------------------------------------------------------------------------
_LOGIN_HINTS = ("login", "sso", "sts", "未登录", "重新登录", "鉴权", "auth")

def _needs_login(result):
    text = " ".join(str(v) for k, v in result.items() if k != "ok")
    return any(h in text.lower() for h in _LOGIN_HINTS)

def query_volc_agentplan():
    if not shutil.which("arkcli"):
        return {"ok": False, "needs_setup": "未安装 arkcli(npm i -g @volcengine/ark-cli),且需 `arkcli auth login volc-sso` 一次"}
    env = dict(os.environ)
    env.update({"ARKCLI_NO_UPDATE_NOTIFIER": "1", "ARKCLI_CALLER_TYPE": "ai_agent",
                "ARKCLI_CALLER_NAME": "model-monitor", "ARKCLI_SKILL_NAME": "arkcli-usage"})
    try:
        proc = subprocess.run(["arkcli", "usage", "plan", "--product", "agent-plan", "--format", "json"],
                              capture_output=True, text=True, timeout=25, env=env)
        out = proc.stdout or ""
        start = out.find("{")
        if start < 0:
            err = (proc.stderr or out).strip()[:200] or "arkcli 无 JSON 输出"
            result = {"ok": False, "error": err}
            if _needs_login(result):
                return {"ok": False, "needs_login": True, "error": "火山 SSO 登录态失效,请重新授权(arkcli auth login volc-sso)"}
            return {"ok": False, "needs_setup": "arkcli 未登录或输出异常", "error": err}
        body = json.loads(out[start:])
        for item in body.get("items") or []:
            if item.get("product") == "agent-plan":
                if item.get("error"):
                    result = {"ok": False, "error": str(item["error"])[:200]}
                    if _needs_login(result):
                        return {"ok": False, "needs_login": True, "error": "火山 SSO 登录态失效,请重新授权(arkcli auth login volc-sso)"}
                    return result
                if not item.get("subscribed"):
                    return {"ok": True, "subscribed": False, "note": "未订阅 agent-plan"}
                periods = []
                for p in item.get("periods") or []:
                    if isinstance(p.get("percent"), (int, float)):
                        periods.append({
                            "label": p.get("label"),
                            "pct": float(p["percent"]),
                            "reset_at": p.get("reset_at"),
                        })
                return {"ok": True, "subscribed": True, "periods": periods}
        return {"ok": False, "error": "响应无 agent-plan 条目"}
    except FileNotFoundError:
        return {"ok": False, "needs_setup": "未安装 arkcli"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "arkcli 超时"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

# --------------------------------------------------------------------------
# 适配器 3b: 火山 OpenAPI GetAFPUsage(AK/SK 签名直查, 免 SSO 过期)
# --------------------------------------------------------------------------
_VOLC_HOST = "open.volcengineapi.com"

def _volc_sign_req(ak, sk, action="GetAFPUsage", payload=None,
                   host=_VOLC_HOST, service="ark", version="2024-01-01"):
    payload = payload or {}
    body = json.dumps(payload, separators=(",", ":"))
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    now = datetime.datetime.now(datetime.timezone.utc)
    xdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = xdate[:8]
    query = {"Action": action, "Version": version}
    cq = "&".join(f"{urllib.parse.quote(k)}={urllib.parse.quote(str(v))}"
                  for k, v in sorted(query.items()))
    headers = {"host": host, "x-content-sha256": payload_hash, "x-date": xdate}
    signed = ";".join(sorted(headers))
    ch = "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers))
    canonical = f"POST\n/\n{cq}\n{ch}\n{signed}\n{payload_hash}"
    scope = f"{datestamp}/cn-beijing/{service}/request"
    sts = f"HMAC-SHA256\n{xdate}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

    def _h(key, msg):
        if isinstance(key, str):
            key = key.encode()
        if isinstance(msg, str):
            msg = msg.encode()
        return hmac.new(key, msg, hashlib.sha256).digest()

    kd = _h(sk, datestamp); kr = _h(kd, "cn-beijing"); ks = _h(kr, service); kk = _h(ks, "request")
    sig = hmac.new(kk, sts.encode(), hashlib.sha256).hexdigest()
    auth = f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed}, Signature={sig}"
    req = urllib.request.Request(
        f"https://{host}/?{cq}", data=body.encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Date": xdate,
                 "X-Content-Sha256": payload_hash, "Authorization": auth,
                 "User-Agent": "model-monitor/1.0"})
    return req

def _fmt_reset(ms):
    if not ms or int(ms) <= 0:
        return None
    t = datetime.datetime.fromtimestamp(int(ms) / 1000, datetime.timezone(datetime.timedelta(hours=8)))
    return t.strftime("%Y-%m-%d %H:%M")

def query_volc_agentplan_aksk(ak, sk):
    try:
        req = _volc_sign_req(ak, sk)
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode("utf-8"))
        res = body.get("Result") or {}
        periods = []
        for key, label in (("AFPFiveHour", "5h"), ("AFPWeekly", "weekly"), ("AFPMonthly", "monthly")):
            seg = res.get(key) or {}
            quota = seg.get("Quota") or 0
            used = seg.get("Used") or 0
            pct = (used / quota * 100) if quota else 0
            periods.append({"label": label, "used": used, "total": quota,
                            "pct": pct, "reset_at": _fmt_reset(seg.get("ResetTime"))})
        if not periods:
            return {"ok": False, "error": "响应无套餐额度段"}
        return {"ok": True, "subscribed": True, "tier": res.get("PlanType"), "periods": periods,
                "via": "aksk"}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8")).get("ResponseMetadata", {}).get("Error", {})
            code = err.get("Code") or ""
            msg = err.get("Message") or ""
        except Exception:
            code, msg = "", str(e)
        if "AccessDenied" in code or "NotAuthorized" in code:
            return {"ok": False, "needs_permission": True,
                    "error": f"火山子账号缺 ark:GetAFPUsage 权限({msg[:80]})"}
        return {"ok": False, "error": f"HTTP {e.code}: {msg[:120]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

# --------------------------------------------------------------------------
# 适配器 3c: 火山 Coding Plan 配额
#   优先 AK/SK 直查 OpenAPI GetCodingPlanUsage(长期有效, 需 ark:GetCodingPlanUsage 权限);
#   无权限/无 AK/SK 时回落 arkcli SSO(`arkcli usage plan --product coding-plan`)。
# --------------------------------------------------------------------------
_ARKCLI = os.path.expanduser("~/bin/arkcli")

def _volc_period_label(name):
    n = str(name).lower()
    if "five" in n or "5h" in n or "session" in n:
        return "5h"
    if "week" in n:
        return "w"
    if "month" in n:
        return "m"
    if "day" in n or "daily" in n:
        return "d"
    return str(name)

def _parse_volc_coding_result(res):
    """容错解析 Coding Plan 响应：兼容「数组(QuotaUsage)」与「按窗口分字段」两种形态。"""
    periods = []

    def add(name, seg):
        if not isinstance(seg, dict):
            return
        pct = seg.get("Percent", seg.get("percent"))
        used = seg.get("Used", seg.get("used"))
        quota = seg.get("Quota", seg.get("quota"))
        if pct is None and quota:
            try:
                pct = float(used or 0) / float(quota) * 100
            except Exception:
                pct = None
        if pct is None:
            return
        reset = seg.get("ResetTime", seg.get("ResetTimestamp", seg.get("reset_at")))
        if isinstance(reset, (int, float)):
            # Coding Plan 的 ResetTimestamp 是秒级 epoch；Agent Plan 是毫秒
            reset = _fmt_reset(int(reset) * 1000 if int(reset) < 100000000000 else int(reset))
        periods.append({"label": _volc_period_label(name), "used": used, "total": quota,
                        "pct": float(pct), "reset_at": reset})

    for key, val in (res or {}).items():
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    # Coding Plan: Level=session/weekly/monthly；Agent Plan: 以键名为窗口
                    add(item.get("Level") or item.get("Period") or item.get("PeriodType")
                        or item.get("label") or key, item)
        elif isinstance(val, dict):
            add(key, val)
    return periods

def query_volc_codingplan_aksk(ak, sk):
    try:
        req = _volc_sign_req(ak, sk, action="GetCodingPlanUsage")
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode("utf-8"))
        res = body.get("Result") or {}
        periods = _parse_volc_coding_result(res)
        if not periods:
            return {"ok": False, "error": "响应无 Coding Plan 额度段",
                    "raw_keys": sorted(list(res.keys()))[:12]}
        return {"ok": True, "subscribed": True, "periods": periods, "via": "aksk"}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8")).get("ResponseMetadata", {}).get("Error", {})
            code = err.get("Code") or ""
            msg = err.get("Message") or ""
        except Exception:
            code, msg = "", str(e)
        if "AccessDenied" in code or "NotAuthorized" in code:
            return {"ok": False, "needs_permission": True,
                    "error": f"火山子账号缺 ark:GetCodingPlanUsage 权限({msg[:80]})"}
        return {"ok": False, "error": f"HTTP {e.code}: {msg[:120]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

def query_volc_codingplan_arkcli():
    """arkcli SSO 兜底：`arkcli usage plan --product coding-plan --format json`。"""
    try:
        exe = _ARKCLI if os.path.exists(_ARKCLI) else shutil.which("arkcli")
        if not exe:
            return {"ok": False, "needs_setup": "未找到 arkcli（npm i -g @volcengine/ark-cli）"}
        env = dict(os.environ)
        env.update({"ARKCLI_NO_UPDATE_NOTIFIER": "1", "ARKCLI_CALLER_TYPE": "ai_agent",
                    "ARKCLI_CALLER_NAME": "model-monitor", "ARKCLI_SKILL_NAME": "arkcli-usage"})
        proc = subprocess.run([exe, "usage", "plan", "--product", "coding-plan", "--format", "json"],
                              capture_output=True, text=True, timeout=40, env=env)
        if proc.returncode != 0:
            txt = (proc.stderr or proc.stdout or "").strip()
            if "auth login" in txt or "SSO" in txt:
                return {"ok": False, "needs_login": True,
                        "error": "火山 SSO 登录态失效，请重新授权(arkcli auth login volc-sso)"}
            return {"ok": False, "error": txt[:200] or f"arkcli exit {proc.returncode}"}
        body = json.loads(proc.stdout)
        items = body.get("items") or []
        item = next((i for i in items if i.get("product") == "coding-plan"), None)
        if item is None:
            return {"ok": False, "error": "arkcli 未返回 coding-plan 桶"}
        # arkcli 对单桶失败仍返回 exit 0：错误在 items[].error（per-bucket error isolation）
        if item.get("error") and not item.get("subscribed"):
            err = str(item["error"])
            if "auth login" in err or "SSO" in err or "STS" in err:
                return {"ok": False, "needs_login": True, "needs_permission": True,
                        "error": "火山 SSO 登录态失效；可重新授权(arkcli auth login volc-sso)，"
                                 "或给 AK/SK 子账号补 ark:GetCodingPlanUsage 权限（永久）"}
            return {"ok": False, "error": err[:200]}
        if not item.get("subscribed"):
            return {"ok": True, "subscribed": False, "note": "未订阅 Coding Plan", "via": "arkcli"}
        periods = []
        for per in item.get("periods") or []:
            periods.append({"label": _volc_period_label(per.get("label")),
                            "pct": float(per.get("percent") or 0),
                            "reset_at": per.get("reset_at")})
        return {"ok": True, "subscribed": True, "periods": periods, "via": "arkcli"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

def query_volc_codingplan(ak, sk):
    """AK/SK 优先，失败回落 arkcli；两条都失败时给出最有用的那条错误。"""
    aksk = query_volc_codingplan_aksk(ak, sk) if (ak and sk) else None
    if aksk and aksk.get("ok"):
        return aksk
    fb = query_volc_codingplan_arkcli()
    if fb.get("ok"):
        if aksk and aksk.get("needs_permission"):
            fb["note"] = "当前走 arkcli SSO；给子账号补 ark:GetCodingPlanUsage 权限后可长期免登录"
        return fb
    # 两条通道都失败：合并成一条可操作提示（保存权限缺失/登录失效两种状态）
    if aksk and aksk.get("needs_permission"):
        sso_dead = bool(fb.get("needs_login"))
        merged = dict(aksk)
        merged["needs_login"] = sso_dead
        merged["error"] = (
            "Coding Plan 取数失败：AK/SK 子账号缺 ark:GetCodingPlanUsage 权限"
            + ("，且 arkcli SSO 登录态已失效" if sso_dead else "")
            + "。二选一：① 给子账号补该权限（永久，推荐）② 重新 arkcli auth login volc-sso（临时）"
        )
        return merged
    return aksk or fb

# --------------------------------------------------------------------------
# 适配器 4b: 火山引擎「按量付费」账户余额(Billing OpenAPI QueryBalanceAcct)
# --------------------------------------------------------------------------
def query_volc_balance_aksk(ak, sk):
    """账户余额: 可用 / 现金 / 信用额度 / 欠费 / 冻结。需要 billing:QueryBalanceAcct 权限。"""
    if not (ak and sk):
        return {"ok": False, "needs_setup": "未配置火山 AK/SK（写入 .creds/volc.env）"}
    try:
        req = _volc_sign_req(ak, sk, action="QueryBalanceAcct",
                             host="billing.volcengineapi.com",
                             service="billing", version="2022-01-01")
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode("utf-8"))
        res = body.get("Result") or {}
        return {"ok": True,
                "available": _to_num(res.get("AvailableBalance")),
                "cash": _to_num(res.get("CashBalance")),
                "credit_limit": _to_num(res.get("CreditLimit")),
                "arrears": _to_num(res.get("ArrearsBalance")),
                "frozen": _to_num(res.get("FreezeAmount")),
                "currency": res.get("Currency") or "CNY"}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8")).get("ResponseMetadata", {}).get("Error", {})
            code = err.get("Code") or ""
            msg = err.get("Message") or ""
        except Exception:
            code, msg = "", str(e)
        if "AccessDenied" in code or "NotAuthorized" in code or "Unauthorized" in code:
            return {"ok": False, "needs_permission": True,
                    "error": f"火山 AK/SK 缺 billing:QueryBalanceAcct 权限({msg[:90]})"}
        return {"ok": False, "error": f"HTTP {e.code}: {(code or msg)[:120]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

# --------------------------------------------------------------------------
# 适配器 5: MiniMax Coding Plan 配额(cn / io 双域名, Bearer key)
# --------------------------------------------------------------------------
MINIMAX_REMAINS_PATH = "/v1/api/openplatform/coding_plan/remains"

def query_minimax_usage(key, base="https://api.minimaxi.com"):
    try:
        req = urllib.request.Request(base + MINIMAX_REMAINS_PATH,
                                     headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            body = json.loads(r.read().decode("utf-8"))
        br = body.get("base_resp") or {}
        code = _to_num(br.get("status_code"))
        if code != 0:
            msg = str(br.get("status_msg") or "接口返回错误")
            if "subscription" in msg.lower() or "2062" in str(code) or "订阅" in msg:
                return {"ok": True, "subscribed": False, "note": f"无生效 Coding Plan 订阅({msg}), 请到 MiniMax 平台查看/续订"}
            return {"ok": False, "error": f"{msg} (code {code})"}
        item = None
        for m in body.get("model_remains") or []:
            if isinstance(m, dict) and m.get("model_name") == "general":
                item = m
        if not item:
            return {"ok": False, "error": "响应缺少 general 条目"}
        windows = []
        remain5 = _to_num(item.get("current_interval_remaining_percent"))
        if remain5 is not None:
            windows.append({"label": "5h", "pct": 100 - remain5,
                            "reset_at": _fmt_reset(item.get("end_time"))})
        if _to_num(item.get("current_weekly_status")) == 1:
            remainw = _to_num(item.get("current_weekly_remaining_percent"))
            if remainw is not None:
                windows.append({"label": "weekly", "pct": 100 - remainw,
                                "reset_at": _fmt_reset(item.get("weekly_end_time"))})
        if not windows:
            return {"ok": False, "error": "响应缺少用量窗口字段"}
        return {"ok": True, "subscribed": True, "periods": windows}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "needs_key": True, "error": "MiniMax key 无效或未授权"}
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

# --------------------------------------------------------------------------
# 适配器 5b: MiniMax 按量余额(控制台接口 + 登录态 cookie + x-group-id)
# --------------------------------------------------------------------------
MINIMAX_STATE_FILE = os.path.join(BASE_DIR, ".creds", "minimax_state.json")
MINIMAX_ENV_FILE = os.path.join(BASE_DIR, ".creds", "minimax.env")

def _minimax_creds():
    """返回 (cookie_header, group_id, host) 或 (None, None, host)"""
    host = "https://www.minimax.cn"
    gid = None
    if os.path.exists(MINIMAX_ENV_FILE):
        try:
            with open(MINIMAX_ENV_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MINIMAX_HOST="):
                        host = line.split("=", 1)[1].strip().strip("\"'")
                    elif line.startswith("MINIMAX_GROUP_ID="):
                        gid = line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    ck = None
    if os.path.exists(MINIMAX_STATE_FILE):
        try:
            with open(MINIMAX_STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            cookies = [c for c in state.get("cookies", []) if "minimax" in str(c.get("domain", ""))]
            if cookies:
                ck = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        except Exception:
            ck = None
    return (ck, gid, host)

def query_minimax_balance():
    ck, gid, host = _minimax_creds()
    if not ck:
        return {"ok": False, "needs_login": True,
                "error": "MiniMax 登录态缺失: 运行 .venv/bin/python minimax_login.py 登录一次"}
    headers = {
        "Cookie": ck, "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Origin": "https://platform.minimax.cn",
        "Referer": "https://platform.minimax.cn/console/recharge-records",
    }
    if gid:
        headers["x-group-id"] = gid
    try:
        req = urllib.request.Request(host + "/account/query_balance", headers=headers)
        with urllib.request.urlopen(req, timeout=12) as r:
            body = json.loads(r.read().decode("utf-8"))
        br = body.get("base_resp") or {}
        code = _to_num(br.get("status_code"))
        if code not in (0, None):
            msg = str(br.get("status_msg") or "接口返回错误")
            if code in (1004, 1005) or "login" in msg.lower():
                return {"ok": False, "needs_login": True, "error": f"MiniMax 登录态失效({msg}), 请重跑 minimax_login.py"}
            return {"ok": False, "error": f"{msg}(code {code})"}
        out = {
            "ok": True,
            "available": _to_num(body.get("available_amount")),
            "cash": _to_num(body.get("cash_balance")),
            "voucher": _to_num(body.get("voucher_balance")),
            "credit": _to_num(body.get("credit_balance")),
            "owed": _to_num(body.get("owed_amount")),
            "alert_on": bool(body.get("balance_alert_switch")),
        }
        # 累计充值/消费(失败不影响主读数)
        try:
            req2 = urllib.request.Request(host + "/account/query_recharge?page_num=1&page_size=1", headers=headers)
            with urllib.request.urlopen(req2, timeout=12) as r2:
                b2 = json.loads(r2.read().decode("utf-8"))
            out["total_recharge"] = _to_num(b2.get("cash_total_recharge"))
            out["total_usage"] = _to_num(b2.get("cash_total_usage"))
            recs = b2.get("records") or []
            if recs:
                out["last_recharge"] = {"at": recs[0].get("create_time"), "amount": _to_num(recs[0].get("amount"))}
        except Exception:
            pass
        return out
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "needs_login": True, "error": f"MiniMax 登录态失效(HTTP {e.code}), 请重跑 minimax_login.py"}
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

# --------------------------------------------------------------------------
# 适配器 2b: 智谱控制台余额(登录态 cookie + Authorization, 嗅探自真实页面)
# --------------------------------------------------------------------------
ZHIPU_BAL_API = "https://open.bigmodel.cn/api/biz/account/query-customer-account-report"
ZHIPU_STATE = os.path.join(BASE_DIR, ".creds", "zhipu_state.json")
ZHIPU_AUTH = os.path.join(BASE_DIR, ".creds", "zhipu_auth.txt")

def _zhipu_creds():
    """返回 (cookie_header, authorization) 或 (None, None)"""
    auth = None
    if os.path.exists(ZHIPU_AUTH):
        try:
            with open(ZHIPU_AUTH, encoding="utf-8") as f:
                auth = f.read().strip()
        except Exception:
            auth = None
    ck = None
    if os.path.exists(ZHIPU_STATE):
        try:
            with open(ZHIPU_STATE, encoding="utf-8") as f:
                state = json.load(f)
            ck = "; ".join(f"{c['name']}={c['value']}" for c in state.get("cookies", [])
                           if str(c.get("domain", "")).endswith("bigmodel.cn"))
        except Exception:
            ck = None
    return (ck if ck else None, auth)

def query_zhipu_balance():
    ck, auth = _zhipu_creds()
    if not ck or not auth:
        return {"ok": False, "needs_login": True,
                "error": "智谱登录态缺失: 运行 .venv/bin/python zhipu_login.py 重新登录一次"}
    headers = {
        "Authorization": auth, "Cookie": ck,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://open.bigmodel.cn/console/overview",
    }
    try:
        req = urllib.request.Request(ZHIPU_BAL_API, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as r:
            body = json.loads(r.read().decode("utf-8"))
        if body.get("code") != 200:
            msg = str(body.get("msg") or "未知错误")
            if "Authorization" in msg or "登录" in msg or body.get("code") == 1001:
                return {"ok": False, "needs_login": True, "error": f"登录态失效({msg[:80]}), 请重登 zhipu_login.py"}
            return {"ok": False, "error": msg[:120]}
        d = body.get("data") or {}
        return {
            "ok": True,
            "available": d.get("availableBalance") if d.get("availableBalance") is not None else d.get("balance"),
            "balance": d.get("balance"),
            "recharged": d.get("rechargeAmount"),
            "gifted": d.get("giveAmount"),
            "spent": d.get("totalSpendAmount"),
            "today_spent": d.get("todaySpendAmount"),
            "frozen": d.get("frozenBalance"),
        }
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "needs_login": True, "error": f"智谱登录态失效(HTTP {e.code}), 请重登 zhipu_login.py"}
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

# --------------------------------------------------------------------------
# 适配器 4: 阿里云账户余额(官方 BSS QueryAccountBalance, 只读子账号 AK/SK)
# --------------------------------------------------------------------------
def query_qwen_balance(ak, sk):
    try:
        from aliyunsdkcore.client import AcsClient
        from aliyunsdkbssopenapi.request.v20171214.QueryAccountBalanceRequest import QueryAccountBalanceRequest
    except ImportError:
        return {"ok": False, "needs_setup": "缺少 aliyun SDK(.venv/bin/pip install aliyun-python-sdk-bssopenapi)"}
    try:
        client = AcsClient(ak, sk, "cn-hangzhou")
        req = QueryAccountBalanceRequest()
        req.set_accept_format("JSON")
        resp = json.loads(client.do_action_with_exception(req))
        data = resp.get("Data") or {}
        return {
            "ok": True,
            "currency": data.get("Currency") or "CNY",
            "available": data.get("AvailableAmount"),
            "cash": data.get("AvailableCashAmount"),
            "credit": data.get("CreditAmount"),
            "status": data.get("Status"),
        }
    except Exception as e:
        msg = str(e)
        if "InvalidAccessKeyId" in msg or "SignatureDoesNotMatch" in msg:
            return {"ok": False, "error": "AK/SK 无效(只读子账号密钥检查)", "detail": msg[:120]}
        if "Forbidden" in msg or "NoPermission" in msg or "NotHavePermission" in msg:
            return {"ok": False, "error": "该 AK 缺少 AliyunBSSReadOnlyAccess 权限", "detail": msg[:120]}
        return {"ok": False, "error": f"{type(e).__name__}: {msg}"[:160]}

# --------------------------------------------------------------------------
# 适配器 6: 小米 MiMo Token Plan(控制台登录态 platform.xiaomimimo.com)
# 接口: /api/v1/tokenPlan/usage | /tokenPlan/detail | /balance
# 认证: Cookie api-platform_serviceToken + userId(控制台登录,约 24h 有效)
# --------------------------------------------------------------------------
MIMO_STATE_FILE = os.path.join(BASE_DIR, ".creds", "mimo_state.json")
MIMO_API = "https://platform.xiaomimimo.com/api/v1"
MIMO_REQUIRED = ("api-platform_serviceToken", "userId")
MIMO_OPTIONAL = ("api-platform_ph", "api-platform_slh")

def _mimo_cookie_header():
    if not os.path.exists(MIMO_STATE_FILE):
        return None, "未登录"
    try:
        with open(MIMO_STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        return None, f"状态文件损坏({e})"
    names = {}
    for c in state.get("cookies", []):
        if "xiaomimimo" in str(c.get("domain", "")):
            names[c.get("name")] = c.get("value")
    missing = [k for k in MIMO_REQUIRED if not names.get(k)]
    if missing:
        return None, "缺 cookie: " + ", ".join(missing)
    return "; ".join(f"{k}={v}" for k, v in names.items()
                     if k in MIMO_REQUIRED + MIMO_OPTIONAL), "ok"

_mimo_sign_cache = {"key": None, "expires_at": 0.0, "offset_ms": 0.0}

def _mimo_sign_key(header, force=False):
    """获取(并缓存)签名密钥：GET /user/sign-key，按 epoch 每日轮换。"""
    now = time.time()
    if (not force and _mimo_sign_cache["key"]
            and float(_mimo_sign_cache["expires_at"]) - now > 60):
        return _mimo_sign_cache["key"]
    req = urllib.request.Request(
        MIMO_API + "/user/sign-key",
        headers={
            "Cookie": header,
            "Accept": "application/json",
            "Referer": "https://platform.xiaomimimo.com/",
            "Origin": "https://platform.xiaomimimo.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
        })
    with urllib.request.urlopen(req, timeout=20) as r:
        # x-server-time 用于校准本机时钟偏差（毫秒或秒）
        st = r.headers.get("x-server-time")
        if st:
            try:
                ms = float(st)
                if ms < 1e12:
                    ms *= 1000.0
                _mimo_sign_cache["offset_ms"] = ms - time.time() * 1000.0
            except Exception:
                pass
        body = json.loads(r.read().decode("utf-8"))
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    sk = data.get("signKey") if isinstance(data, dict) else None
    epoch = _to_num((data or {}).get("epoch")) if isinstance(data, dict) else None
    expires = _to_num((data or {}).get("expiresAt")) if isinstance(data, dict) else None
    if not sk or epoch is None:
        raise RuntimeError("sign-key 响应异常: " + json.dumps(body, ensure_ascii=False)[:120])
    key = {"signKey": sk, "epoch": int(epoch),
           "expiresAt": int(expires) if expires else int(epoch) + 86400}
    _mimo_sign_cache.update({"key": key, "expires_at": float(key["expiresAt"])})
    return key

def _mimo_signed_get(path, header, force_key=False):
    """带 X-Sign/X-Timestamp/X-Key-Epoch 的 GET。
    canonical = METHOD\\npath\\nsorted_query\\nbodyHash\\ntimestamp
    X-Sign = HMAC-SHA256(canonical, signKey) hex
    """
    key = _mimo_sign_key(header, force=force_key)
    ts = str(int(time.time() + _mimo_sign_cache["offset_ms"] / 1000.0))
    api_prefix = urllib.parse.urlparse(MIMO_API).path  # "/api/v1"
    canonical = "\n".join(["GET", api_prefix + path, "", "", ts])
    sig = hmac.new(key["signKey"].encode(), canonical.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        MIMO_API + path,
        headers={
            "Cookie": header,
            "Accept": "application/json",
            "Referer": "https://platform.xiaomimimo.com/console/balance",
            "Origin": "https://platform.xiaomimimo.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
            "Content-Type": "application/json",
            "X-Sign": sig,
            "X-Timestamp": ts,
            "X-Key-Epoch": str(key["epoch"]),
            "X-Timezone": "Asia/Shanghai",
        })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def _mimo_get(path, header):
    """兼容旧调用：自动签名，遇到签名失效时刷新 sign-key 重试一次。"""
    try:
        return _mimo_signed_get(path, header)
    except urllib.error.HTTPError as e:
        code = ""
        try:
            code = (json.loads(e.read().decode("utf-8")) or {}).get("code") or ""
        except Exception:
            pass
        if e.code in (401, 403) and str(code).startswith("SIGN") or code == "TIMESTAMP_INVALID":
            _mimo_sign_cache["key"] = None
            return _mimo_signed_get(path, header, force_key=True)
        raise

def _mimo_token_period(seg, label, reset_at=None):
    """MiMo 额度条目 → 统一 periods 条目。
    seg 形如 {"name": "plan_total_token", "used": N, "limit": M, "percent": 0-1 分数}
    优先用 used/limit 算百分比（percent 是 0-1 分数，不可直接当百分数）。"""
    if not isinstance(seg, dict):
        return None
    used = _to_num(seg.get("used"))
    limit = _to_num(seg.get("limit"))
    pct = _to_num(seg.get("percent"))
    if used is not None and limit and limit > 0:
        pct = used / limit * 100.0
    elif pct is not None and 0 < pct <= 1:
        pct = pct * 100.0
    if pct is None:
        return None
    return {"label": label, "pct": float(pct), "used": used, "total": limit,
            "reset_at": reset_at}

def _mimo_items(section):
    """usage/monthUsage 段 → {name: item} 映射"""
    out = {}
    for it in (section or {}).get("items") or []:
        if isinstance(it, dict) and it.get("name"):
            out[str(it["name"])] = it
    return out

def query_mimo_usage():
    header, note = _mimo_cookie_header()
    if not header:
        return {"ok": False, "needs_login": True,
                "error": f"MiMo 登录态{note}: 运行 .venv/bin/python mimo_login.py 登录一次"}
    try:
        usage = _mimo_get("/tokenPlan/usage", header)
        detail = _mimo_get("/tokenPlan/detail", header)
        balance = _mimo_get("/balance", header)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        code = str((body or {}).get("code") or "") if isinstance(body, dict) else ""
        if code == "UNAUTHORIZED" or (isinstance(body, dict) and body.get("loginUrl")):
            return {"ok": False, "needs_login": True,
                    "error": f"MiMo 登录态失效(HTTP {e.code}): 重跑 mimo_login.py"}
        if code.startswith("SIGN") or code == "TIMESTAMP_INVALID":
            return {"ok": False, "error": f"MiMo 接口签名失败({code}): 检查本机时钟，稍后自动重试"}
        return {"ok": False, "error": f"HTTP {e.code}: {code or str(body)[:80]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

    d_usage = usage.get("data") or {}
    d_detail = detail.get("data") or {}
    d_bal = balance.get("data") or {}
    period_end = d_detail.get("currentPeriodEnd")

    periods = []
    cur = _mimo_items(d_usage.get("usage"))
    p = _mimo_token_period(cur.get("plan_total_token"), "plan", period_end)
    if p:
        periods.append(p)
    comp = _mimo_token_period(cur.get("compensation_total_token"), "补偿")
    if comp and (comp.get("pct") or 0) > 0 and (comp.get("total") or 0) > 0:
        periods.append(comp)
    mon = _mimo_items(d_usage.get("monthUsage"))
    p = _mimo_token_period(mon.get("month_total_token"), "month")
    if p:
        periods.append(p)

    bal = d_bal.get("balance") if isinstance(d_bal, dict) else d_bal
    r = {"ok": True, "subscribed": bool(periods), "periods": periods,
         "plan_code": d_detail.get("planCode"), "plan_name": d_detail.get("planName"),
         "expired": d_detail.get("expired"), "auto_renew": d_detail.get("hasAutoRenewSubscribed"),
         "balance": _to_num(bal), "via": "cookie"}
    if not periods:
        r["subscribed"] = False
        r["note"] = "未发现 Token Plan 额度段(可能未订阅)"
    elif d_detail.get("expired"):
        r["note"] = "Token Plan 已过期"
    return r

# --------------------------------------------------------------------------
# 编排
# --------------------------------------------------------------------------
PROVIDER_META = [
    {"id": "deepseek", "label": "DeepSeek", "emoji": "💙", "kind": "余额(CNY)",
     "console": "https://platform.deepseek.com/usage"},
    {"id": "zhipu", "label": "智谱 GLM", "emoji": "🧠", "kind": "余额(CNY)",
     "console": "https://open.bigmodel.cn/console"},
    {"id": "volc", "label": "火山方舟", "emoji": "🐋", "kind": "Agent/Coding Plan 配额",
     "console": "https://console.volcengine.com/ark"},
    {"id": "qwen", "label": "阿里云(百炼)", "emoji": "🌤", "kind": "账户余额",
     "console": "https://bailian.console.aliyun.com"},
    {"id": "minimax", "label": "MiniMax", "emoji": "🤖", "kind": "M Plan 配额 + 按量余额(CNY)",
     "console": "https://platform.minimax.cn"},
    {"id": "mimo", "label": "小米 MiMo", "emoji": "📱", "kind": "Token Plan 配额 + 余额",
     "console": "https://platform.xiaomimimo.com/#/console/balance"},
]

def load_manual():
    try:
        with open(MANUAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def collect_status():
    keys = load_keys()
    manual = load_manual()
    out = []
    for m in PROVIDER_META:
        rec = {"id": m["id"], "label": m["label"], "emoji": m["emoji"],
               "kind": m["kind"], "console": m["console"], "manual": manual.get(m["id"])}
        key_name = "volc_agent_plan" if m["id"] == "volc" else m["id"]
        key = keys.get(key_name)
        rec["has_key"] = bool(key)
        try:
            if m["id"] == "deepseek":
                r = query_deepseek_balance(key) if key else {"ok": False, "error": "未找到 DEEPSEEK key"}
                rec["auto"] = r
            elif m["id"] == "zhipu":
                r = query_zhipu_balance()
                # 若订阅 GLM Coding Plan, 同 key 可查配额(5h/周用量) —— 一并带上
                if key:
                    cp = query_zhipu_quota(key)
                    if cp.get("subscribed") is True:
                        r["cp"] = {"five_hour": cp.get("five_hour"), "weekly": cp.get("weekly")}
                    elif cp.get("ok"):
                        r["cp_note"] = "Coding Plan 未订阅"
                rec["auto"] = r
                rec["has_key"] = not bool(r.get("needs_login"))
                rec["auth_kind"] = "登录态"
            elif m["id"] == "volc":
                ak, sk = keys.get("volc_ak"), keys.get("volc_sk")
                if ak and sk:
                    r = query_volc_agentplan_aksk(ak, sk)
                else:
                    r = query_volc_agentplan()  # SSO 兜底
                # Coding Plan：与 Agent Plan 并列展示（AK/SK 优先，arkcli 兜底）
                try:
                    r["coding"] = query_volc_codingplan(ak, sk)
                except Exception as e:
                    r["coding"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
                # 按量付费账户余额（Billing OpenAPI，与套餐额度互不影响）
                try:
                    r["balance"] = query_volc_balance_aksk(ak, sk)
                except Exception as e:
                    r["balance"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
                rec["auto"] = r
                rec["auth_kind"] = "AK/SK" if (ak and sk) else "SSO"
                rec["key_present"] = bool(key)
            elif m["id"] == "minimax":
                ck_mm, _gid, _host = _minimax_creds()
                if ck_mm:
                    r = query_minimax_balance()
                    rec["auth_kind"] = "登录态 + API key"
                else:
                    r = {"ok": False, "needs_login": True,
                         "error": "MiniMax 登录态缺失: 运行 .venv/bin/python minimax_login.py 登录一次"}
                    rec["auth_kind"] = "API key"
                # M Plan 套餐额度：与按量余额并列展示（走 Bearer API key，与登录态无关）
                try:
                    if key:
                        r["plan"] = query_minimax_usage(key)
                    else:
                        r["plan"] = {"ok": False, "needs_key": True, "error": "未找到 MiniMax key"}
                except Exception as e:
                    r["plan"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
                rec["auto"] = r
            elif m["id"] == "mimo":
                r = query_mimo_usage()
                rec["auth_kind"] = "控制台登录态"
                rec["key_present"] = os.path.exists(MIMO_STATE_FILE)
                rec["auto"] = r
            else:  # qwen -> 阿里云 BSS 账户余额(只读 AK/SK)
                ak, sk = keys.get("aliyun_ak"), keys.get("aliyun_sk")
                if ak and sk:
                    r = query_qwen_balance(ak, sk)
                else:
                    r = {"ok": False, "needs_setup": "待配置阿里云只读 AK/SK(建 RAM 子账号 + AliyunBSSReadOnlyAccess, 发我后写入 .creds/aliyun.env)"}
                rec["auto"] = r
        except Exception as e:
            rec["auto"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
        rec["queried_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        out.append(rec)
    return {"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "providers": out,
            "cache_ttl_s": CACHE_TTL}

# --------------------------------------------------------------------------
# 状态缓存 + 存储
# --------------------------------------------------------------------------
_lock = threading.Lock()
_cache = {"t": 0, "data": None}
_history = []

def load_history():
    global _history
    if not _history and os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                _history = json.load(f)
        except Exception:
            _history = []
    return _history

def append_history(data):
    """每次真正轮询成功后,把各平台主指标追加为一条历史样本"""
    row = {"ts": time.strftime("%m-%d %H:%M")}
    got = False
    for p in data.get("providers", []):
        a = p.get("auto") or {}
        pid = p["id"]
        if pid == "deepseek" and a.get("ok"):
            b = (a.get("balance_infos") or [{}])[0]
            if b.get("total") is not None:
                row["deepseek"] = round(float(b["total"]), 2); got = True
        elif pid == "zhipu" and a.get("ok") and a.get("available") is not None:
            row["zhipu"] = round(float(a["available"]), 2); got = True
        elif pid == "qwen" and a.get("ok") and a.get("available") is not None:
            row["qwen"] = round(float(a["available"]), 2); got = True
        elif pid == "minimax":
            if a.get("ok") and a.get("available") is not None:
                row["minimax"] = round(float(a["available"]), 2); got = True
            plan = a.get("plan") or {}
            if plan.get("ok") and plan.get("subscribed"):
                for x in plan.get("periods") or []:
                    if x.get("label") == "weekly" and x.get("pct") is not None:
                        row["minimax_m"] = round(float(x["pct"]), 1); got = True
        elif pid == "mimo":
            for x in a.get("periods") or []:
                if x.get("label") == "plan" and x.get("pct") is not None:
                    row["mimo_tp"] = round(float(x["pct"]), 2); got = True
            if a.get("balance") is not None:
                row["mimo_bal"] = round(float(a["balance"]), 2); got = True
        elif pid == "volc" and a.get("ok"):
            for x in a.get("periods") or []:
                if x.get("label") == "weekly" and x.get("pct") is not None:
                    row["volc_weekly"] = round(float(x["pct"]), 1); got = True
                elif x.get("label") == "monthly" and x.get("pct") is not None:
                    row["volc_monthly"] = round(float(x["pct"]), 1); got = True
            vbal = a.get("balance") or {}
            if vbal.get("ok") and vbal.get("available") is not None:
                row["volc_bal"] = round(float(vbal["available"]), 2); got = True
    if got:
        global _history
        load_history()
        _history.append(row)
        if len(_history) > HISTORY_MAX:
            _history = _history[-HISTORY_MAX:]
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(_history, f)
        except Exception:
            pass

def get_status(force=False):
    with _lock:
        if force or not _cache["data"] or (time.time() - _cache["t"] > CACHE_TTL):
            data = collect_status()
            _cache["t"] = time.time()
            _cache["data"] = data
            try:
                with open(STATUS_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
            except Exception:
                pass
            append_history(data)
        return _cache["data"]

def save_manual(pid, value):
    manual = load_manual()
    manual[pid] = {"value": value, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(MANUAL_FILE, "w", encoding="utf-8") as f:
        json.dump(manual, f, ensure_ascii=False, indent=1)
    return manual[pid]

# --------------------------------------------------------------------------
# HTTP 服务
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(BASE_DIR, "dashboard.html"), "rb") as f:
                self._send(200, f.read(), "text/html")
        elif self.path == "/api/status":
            self._send(200, json.dumps(get_status(), ensure_ascii=False))
        elif self.path == "/api/poll":
            self._send(200, json.dumps(get_status(force=True), ensure_ascii=False))
        elif self.path == "/api/history":
            load_history()
            tail = _history[-120:]
            self._send(200, json.dumps(tail, ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path == "/api/manual":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                rec = save_manual(str(data.get("provider", "")), str(data.get("value", "")).strip())
                self._send(200, json.dumps({"ok": True, "manual": rec}))
            except Exception as e:
                self._send(400, json.dumps({"error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

def main():
    keys = load_keys()
    print("=" * 56)
    print("  Model Monitor v1   ——  常开面板")
    print(f"  面板地址:  http://127.0.0.1:{PORT}")
    print("  key 来源:  ~/.nanobot/config.json (环境变量可覆盖)")
    for k, v in keys.items():
        print(f"    - {k:14s} {mask(v)}")
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()

if __name__ == "__main__":
    main()
