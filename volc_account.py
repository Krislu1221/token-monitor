#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
火山方舟账户凭据切换工具 —— 让 Agent Plan 可以用「另一套账户」，而不影响 Coding Plan 与按量付费余额。

用法:
  # 只看当前配置(密钥已脱敏)
  .venv/bin/python volc_account.py show

  # 切换 Agent Plan 到新账户(只动 AP)
  .venv/bin/python volc_account.py set-ap  AKLT新账号AK  SK新账号密钥  [--key ark-新APKey]

  # 切换共用账户(Coding Plan + 按量付费余额一起换)
  .venv/bin/python volc_account.py set-main  AK  SK

  # 撤掉 AP 专用凭据, 回落到共用账户
  .venv/bin/python volc_account.py reset-ap

  # 三路实测(AP / CP / 余额)
  .venv/bin/python volc_account.py test

写入目标: 项目 config.json 的 volc_ap_ak / volc_ap_sk / volc_ap_key 等字段(600 权限)。
也可改用环境变量 VOLC_AP_AK_ID / VOLC_AP_AK_SECRET / VOLC_AK_ID / VOLC_AK_SECRET。
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import poller  # noqa: E402


def load_cfg():
    path = poller.CONFIG_FILE
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as e:
            print(f"⚠️ 读取 {path} 失败: {e}", file=sys.stderr)
    return {}


def save_cfg(cfg):
    path = poller.CONFIG_FILE
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    print(f"✅ 已写入 {path}")


def tail(ak):
    t = poller.volc_ak_tail(ak)
    return f"…{t}" if t else "—"


def cmd_show(_args):
    cfg = load_cfg()
    keys = poller.load_keys()
    print(f"配置文件: {poller.CONFIG_FILE}\n")
    print("共用户口（Coding Plan + 按量付费余额）")
    print(f"  AK: {tail(keys.get('volc_ak'))}   SK: {'已设置' if keys.get('volc_sk') else '未设置'}")
    print("\nAgent Plan 户口")
    dedicated = bool(cfg.get("volc_ap_ak") or cfg.get("volc_ap_sk"))
    print(f"  AK: {tail(keys.get('volc_ap_ak'))}   SK: {'已设置' if keys.get('volc_ap_sk') else '未设置'}")
    print(f"  模式: {'✅ 独立账户（只影响 AP）' if dedicated else '跟共用户口相同'}")
    if keys.get("volc_ap_key"):
        print(f"  API Key: {str(keys['volc_ap_key'])[:12]}…")


def _write(**kv):
    cfg = load_cfg()
    for k, v in kv.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    save_cfg(cfg)


def cmd_set_ap(args):
    _write(volc_ap_ak=args.ak, volc_ap_sk=args.sk, **({"volc_ap_key": args.key} if args.key else {}))
    print(f"Agent Plan 已切到 AK {tail(args.ak)}")
    _test_ap_only()


def cmd_set_main(args):
    _write(volc_ak=args.ak, volc_sk=args.sk)
    print(f"共用户口已切到 AK {tail(args.ak)}（影响 Coding Plan 与按量付费余额）")
    _test_all()


def cmd_reset_ap(_args):
    _write(volc_ap_ak=None, volc_ap_sk=None, volc_ap_key=None)
    print("已撤掉 AP 专用凭据，AP 回落到共用户口")


def _test_ap_only():
    keys = poller.load_keys()
    r = poller.query_volc_agentplan_aksk(keys.get("volc_ap_ak"), keys.get("volc_ap_sk"))
    if r.get("ok") and r.get("subscribed"):
        ps = "  ".join(f"{x['label']} {x['pct']:.1f}%" for x in r.get("periods") or [])
        print(f"  实测 AP: ✅ {r.get('tier') or ''}  {ps}")
    else:
        print(f"  实测 AP: ❌ {r.get('error') or r.get('note') or '无订阅'}")


def _test_all():
    keys = poller.load_keys()
    _test_ap_only()
    cp = poller.query_volc_codingplan(keys.get("volc_ak"), keys.get("volc_sk"))
    if cp.get("ok") and cp.get("subscribed"):
        ps = "  ".join(f"{x['label']} {x['pct']:.1f}%" for x in cp.get("periods") or [])
        print(f"  实测 CP: ✅ via={cp.get('via')}  {ps}")
    else:
        print(f"  实测 CP: ❌ {cp.get('error') or cp.get('note') or '无订阅'}")
    bal = poller.query_volc_balance_aksk(keys.get("volc_ak"), keys.get("volc_sk"))
    if bal.get("ok"):
        print(f"  实测余额: ✅ ¥{bal.get('available')} ({bal.get('currency')})")
    else:
        print(f"  实测余额: ❌ {bal.get('error')}")


def cmd_test(_args):
    _test_all()


def main():
    p = argparse.ArgumentParser(description="火山方舟凭据切换（AP 可独立）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("show", help="查看当前凭据配置（脱敏）")
    sp.set_defaults(func=cmd_show)

    sa = sub.add_parser("set-ap", help="切换 Agent Plan 专用账户")
    sa.add_argument("ak")
    sa.add_argument("sk")
    sa.add_argument("--key", help="该账户的 ARK API Key（仅展示用，可选）")
    sa.set_defaults(func=cmd_set_ap)

    sm = sub.add_parser("set-main", help="切换共用户口（CP + 余额）")
    sm.add_argument("ak")
    sm.add_argument("sk")
    sm.set_defaults(func=cmd_set_main)

    sr = sub.add_parser("reset-ap", help="撤掉 AP 专用凭据，回落共用")
    sr.set_defaults(func=cmd_reset_ap)

    st = sub.add_parser("test", help="实测三路取数")
    st.set_defaults(func=cmd_test)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
