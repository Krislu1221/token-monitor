#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
平台启用/停用 —— 停用的平台不再请求接口，面板与菜单栏都不显示（数据仍保留）。

用法:
  .venv/bin/python providers.py list                # 看当前状态
  .venv/bin/python providers.py disable zhipu      # 停用智谱
  .venv/bin/python providers.py enable  zhipu      # 重新启用
  .venv/bin/python providers.py disable zhipu qwen # 一次停用多个

写入 config.json 的 disabled_providers 字段；改完下一轮轮询生效（≤5 分钟），
菜单栏需要重启一次（或点「立即刷新」后重新打开图标所在进程）。
"""
import json
import os
import sys

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
            print(f"⚠️ 读取失败: {e}", file=sys.stderr)
    return {}


def save_cfg(cfg):
    tmp = poller.CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, poller.CONFIG_FILE)


def cmd_list(_args):
    cfg = load_cfg()
    off = set(cfg.get("disabled_providers") or [])
    print(f"配置文件: {poller.CONFIG_FILE}\n")
    for m in poller.PROVIDER_META:
        pid = m["id"]
        mark = "❌ 停用" if pid in off else "✅ 启用"
        print(f"  {mark}  {pid:9s} {m['label']}")


def _change(ids, disable):
    known = {m["id"] for m in poller.PROVIDER_META}
    bad = [i for i in ids if i not in known]
    if bad:
        avail = ", ".join(sorted(known))
        print(f"⚠️ 未知平台: {', '.join(bad)}（可用: {avail}）", file=sys.stderr)
        return
    cfg = load_cfg()
    off = set(cfg.get("disabled_providers") or [])
    for i in ids:
        if disable:
            off.add(i)
        else:
            off.discard(i)
    cfg["disabled_providers"] = sorted(off)
    save_cfg(cfg)
    if disable:
        print(f"已停用: {', '.join(ids)}（不再请求其接口，面板/菜单栏隐藏）")
    else:
        print(f"已启用: {', '.join(ids)}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    ids = sys.argv[2:]
    if cmd == "list":
        cmd_list(None)
    elif cmd == "disable" and ids:
        _change(ids, True)
    elif cmd == "enable" and ids:
        _change(ids, False)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
