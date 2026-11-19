# -*- coding: utf-8 -*-
"""零依赖单元测试：python3 -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mediapipe.auth_breaker import AuthBreaker, OK, BLOCKED
from mediapipe.asset_gate import Coverage, count_from_manifest, count_from_log, gate
from mediapipe.receipts import ReceiptBook, Receipt
from mediapipe import typography


class TestAuthBreaker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "lanes.json")
        self.br = AuthBreaker(self.path, threshold=2, cool_seconds=600)

    def test_熔断与半开(self):
        self.assertTrue(self.br.can_run("wechat")[0])
        self.br.record_fail("wechat", "cookie 失效")
        self.assertTrue(self.br.can_run("wechat")[0], "第一次失败还不该熔断")
        self.br.record_fail("wechat", "cookie 失效")
        ok, why = self.br.can_run("wechat")
        self.assertFalse(ok)
        self.assertIn("熔断", why)
        self.assertEqual(self.br.lane("wechat").status, BLOCKED)
        # 到点后半开：允许再试一次
        ok2, _ = self.br.can_run("wechat", now=self.br.lane("wechat").blocked_until + 1)
        self.assertTrue(ok2)

    def test_成功清零(self):
        self.br.record_fail("x")
        self.br.record_ok("x")
        self.assertEqual(self.br.lane("x").status, OK)
        self.assertEqual(self.br.lane("x").fail_count, 0)

    def test_持久化(self):
        self.br.record_fail("y")
        br2 = AuthBreaker(self.path, threshold=2)
        self.assertEqual(br2.lane("y").fail_count, 1)


class TestAssetGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_清单覆盖率(self):
        p = os.path.join(self.tmp, "m.json")
        json.dump({"segments": [{"id": 1, "asset": "a.png"}, {"id": 2, "asset": None},
                                {"id": 3, "asset": "c.png"}, {"id": 4, "asset": ""}]},
                  open(p, "w"))
        cov = count_from_manifest(p)
        self.assertEqual((cov.total, cov.with_asset), (4, 2))
        self.assertAlmostEqual(cov.ratio, 0.5)
        self.assertEqual(cov.level(), "ok")

    def test_纯字卡必须告警(self):
        p = os.path.join(self.tmp, "m2.json")
        json.dump({"segments": [{"id": i, "asset": None} for i in range(10)]}, open(p, "w"))
        self.assertEqual(count_from_manifest(p).level(), "alert")

    def test_日志解析_永不抛异常(self):
        p = os.path.join(self.tmp, "r.log")
        open(p, "w").write("[1/3] 标题卡\n[2/3] 截图 asset=x.png\n[3/3] 结论卡\n")
        cov = count_from_log(open(p).read())
        self.assertEqual((cov.total, cov.with_asset), (3, 1))
        self.assertEqual(gate(log_path=p).total, 3)
        self.assertEqual(gate(manifest_path="/nonexistent").total, 0)   # 不炸


class TestReceipts(unittest.TestCase):
    def test_对账抓出假成功(self):
        tmp = tempfile.mkdtemp()
        book = ReceiptBook(os.path.join(tmp, "r.jsonl"))
        book.add(Receipt(day="20260101", lane="wechat", item="a.md", status="posted",
                         remote_id="msg_123"))
        book.add(Receipt(day="20260101", lane="bilibili", item="a.md", status="posted"))  # 无 id
        res = book.audit("20260101", ["wechat", "bilibili", "douyin"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["missing"], ["douyin"])
        self.assertEqual(res["unverified"], ["bilibili"])


class TestTypography(unittest.TestCase):
    def test_数字加粗(self):
        out = typography.bold_numbers("降了 60% 之后，每百万 token 只要 10 美元。")
        self.assertIn("**60%**", out)
        self.assertIn("**10 美元**", out)

    def test_结论转引用(self):
        out = typography.lead_to_quote("结论：钱买的不是最强模型，是已被证明能收钱的模型公司")
        self.assertTrue(out.startswith("> **结论**"))

    def test_长段切分(self):
        para = "第一句话在这里。" * 30
        out = typography.split_long_paragraphs(para, max_len=50)
        self.assertGreater(len(out.splitlines()), 3)

    def test_代码块不动(self):
        src = "```python\nprint('a' * 200)\n```"
        self.assertEqual(typography.split_long_paragraphs(src, max_len=10).strip(), src)


if __name__ == "__main__":
    unittest.main()
