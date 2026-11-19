# -*- coding: utf-8 -*-
"""统一入口：python -m mediapipe.cli <命令>

    auth   status | reset <lane>
    asset  check --manifest <json> | --log <txt>
    receipt add --day 20260101 --lane wechat --item a.md --status posted --remote-id xxx
    receipt audit --day 20260101 --lanes wechat,bilibili,douyin
    typo   render < input.md > output.md
"""
import argparse
import json
import sys

from .auth_breaker import AuthBreaker
from .asset_gate import gate
from .receipts import ReceiptBook, Receipt
from . import typography


def main(argv=None):
    p = argparse.ArgumentParser(prog="mediapipe")
    p.add_argument("--state", default="state", help="状态目录")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth")
    a.add_argument("action", choices=["status", "reset"])
    a.add_argument("lane", nargs="?")

    g = sub.add_parser("asset")
    g.add_argument("--manifest")
    g.add_argument("--log")

    r = sub.add_parser("receipt")
    r.add_argument("action", choices=["add", "audit"])
    r.add_argument("--day")
    r.add_argument("--lane")
    r.add_argument("--item")
    r.add_argument("--status", default="posted")
    r.add_argument("--remote-id", default="")
    r.add_argument("--lanes")

    t = sub.add_parser("typo")
    t.add_argument("action", choices=["render"])

    ns = p.parse_args(argv)
    if ns.cmd == "auth":
        br = AuthBreaker(f"{ns.state}/lanes.json")
        if ns.action == "status":
            print(json.dumps(br.report(), ensure_ascii=False, indent=1))
        else:
            br.reset(ns.lane)
            print(f"已复位 {ns.lane}")
    elif ns.cmd == "asset":
        cov = gate(ns.manifest, ns.log)
        return 0 if cov.level() != "empty" else 0
    elif ns.cmd == "receipt":
        book = ReceiptBook(f"{ns.state}/receipts.jsonl")
        if ns.action == "add":
            book.add(Receipt(day=ns.day, lane=ns.lane, item=ns.item,
                             status=ns.status, remote_id=ns.remote_id))
            print("回执已记录")
        else:
            lanes = [x for x in (ns.lanes or "").split(",") if x]
            print(json.dumps(book.audit(ns.day, lanes), ensure_ascii=False, indent=1))
    elif ns.cmd == "typo":
        sys.stdout.write(typography.render(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
