#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""火山方舟套餐用量 CLI（供 DSH 插件等外部程序调用）。

输出一行 JSON：
  {"ok": true, "products": [{"product": "agent-plan", "tier": "medium",
     "periods": [{"label": "5h", "pct": 0.0, "reset_at": "..."}]}, ...],
   "coding": {...原始 coding 结果...}, "via": "aksk"|"sso"}

取数优先级：AK/SK 直查 OpenAPI（长期有效）→ arkcli SSO（兜底）。
用法: python volc_usage_cli.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import poller  # noqa: E402

PERIOD_SHORT = {"weekly": "w", "monthly": "m", "session": "5h", "5h": "5h", "daily": "d"}


def load_aksk():
    ak = os.environ.get("VOLC_AK_ID")
    sk = os.environ.get("VOLC_AK_SECRET")
    # 项目本地 config.json（开源用户主路径）
    try:
        cfg_path = os.environ.get("TOKEN_MONITOR_CONFIG") or os.path.join(HERE, "config.json")
        if (not ak or not sk) and os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                local = json.load(f) or {}
            ak = ak or local.get("volc_ak")
            sk = sk or local.get("volc_sk")
            if not (ak and sk) and local.get("volc_agent_plan"):
                pass
    except Exception:
        pass
    env_file = os.path.join(HERE, ".creds", "volc.env")
    if (not ak or not sk) and os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("VOLC_AK_ID="):
                    ak = line.split("=", 1)[1].strip().strip("\"'")
                elif line.startswith("VOLC_AK_SECRET="):
                    sk = line.split("=", 1)[1].strip().strip("\"'")
    return ak, sk


def norm_periods(periods):
    out = []
    for p in periods or []:
        pct = p.get("pct")
        if pct is None:
            continue
        out.append({
            "label": PERIOD_SHORT.get(str(p.get("label")), p.get("label")),
            "pct": float(pct),
            "reset_at": p.get("reset_at"),
        })
    return out


def main():
    ak, sk = load_aksk()
    products = []
    via = None

    # ---- Agent Plan（AK/SK 优先）----
    ap = poller.query_volc_agentplan_aksk(ak, sk) if (ak and sk) else None
    if not (ap and ap.get("ok") and ap.get("subscribed")):
        ap_sso = poller.query_volc_agentplan()
        if ap_sso.get("ok") and ap_sso.get("subscribed"):
            ap = ap_sso
    if ap and ap.get("ok") and ap.get("subscribed"):
        products.append({"product": "agent-plan", "tier": ap.get("tier"),
                         "periods": norm_periods(ap.get("periods"))})
        via = ap.get("via") or via

    # ---- Coding Plan（AK/SK 优先，内部已带 SSO 兜底）----
    cp = poller.query_volc_codingplan(ak, sk)
    if cp.get("ok") and cp.get("subscribed"):
        products.append({"product": "coding-plan",
                         "periods": norm_periods(cp.get("periods"))})
        via = cp.get("via") or via

    result = {
        "ok": bool(products),
        "products": products,
        "coding": cp,
        "via": via,
    }
    if not products:
        result["error"] = (cp.get("error") or (ap or {}).get("error")
                           or "没有取到任何已订阅的火山套餐")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
