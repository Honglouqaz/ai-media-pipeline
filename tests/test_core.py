# -*- coding: utf-8 -*-
"""零依赖单元测试：python3 -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import threading
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

    def test_半开阶段只允许一个并发探测(self):
        self.br.record_fail("concurrent", now=10)
        self.br.record_fail("concurrent", now=11)
        deadline = self.br.lane("concurrent").blocked_until
        results = []
        barrier = threading.Barrier(8)

        def probe():
            barrier.wait()
            results.append(self.br.can_run("concurrent", now=deadline + 1)[0])

        threads = [threading.Thread(target=probe) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(results), 1)

    def test_成功清零(self):
        self.br.record_fail("x")
        self.br.record_ok("x")
        self.assertEqual(self.br.lane("x").status, OK)
        self.assertEqual(self.br.lane("x").fail_count, 0)

    def test_持久化(self):
        self.br.record_fail("y")
        br2 = AuthBreaker(self.path, threshold=2)
        self.assertEqual(br2.lane("y").fail_count, 1)

    def test_零时间戳不会被替换为当前时间(self):
        self.br.record_fail("epoch", "test", now=0)
        self.assertEqual(self.br.lane("epoch").last_fail_ts, 0)
        self.assertTrue(self.br.can_run("epoch", now=0)[0])


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

    def test_清单结构异常不抛异常(self):
        p = os.path.join(self.tmp, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"segments": [None, "bad", {"asset": 123}, {"asset": "ok.png"}]}, f)
        cov = count_from_manifest(p)
        self.assertEqual((cov.total, cov.with_asset), (4, 1))

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

    def test_零时间戳保留(self):
        receipt = Receipt(day="20260101", lane="x", item="a.md", ts=0)
        self.assertEqual(receipt.ts, 0)

    def test_并发追加保持每行完整(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "parallel.jsonl")
        book = ReceiptBook(path)
        threads = [
            threading.Thread(
                target=book.add,
                args=(Receipt(day="20260101", lane=f"lane-{i}", item=f"{i}.md"),)
            )
            for i in range(40)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        rows = book.load("20260101")
        self.assertEqual(len(rows), 40)
        self.assertEqual(len({row["lane"] for row in rows}), 40)

    def test_损坏尾行被跳过(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "broken.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"day": "20260101", "lane": "ok"}) + "\\n")
            f.write('{"broken":')
        self.assertEqual(len(ReceiptBook(path).load("20260101")), 1)


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

    def test_render保护代码块中的数字和标点(self):
        src = "说明 60% 很重要。\n\n```python\nvalue = 60%  # 代码, keep\n```"
        out = typography.render(src)
        self.assertIn("```python\nvalue = 60%  # 代码, keep\n```", out)
        self.assertIn("**60%**", out)

    def test_表格标题列表不被段落切分(self):
        src = "# 标题包含 60%\n| 名称 | 数值 |\n|---|---|\n| 项目 | 60% |\n\n- 列表项包含 " + "很长内容" * 40
        out = typography.split_long_paragraphs(src, max_len=20)
        self.assertIn("# 标题包含 60%", out)
        self.assertIn("| 项目 | 60% |", out)
        self.assertIn("- 列表项包含", out)

    def test_tilde代码围栏保护(self):
        src = "~~~text\n60% 不要加粗, 也不要替换。\n~~~"
        self.assertIn("60% 不要加粗, 也不要替换。", typography.render(src))


if __name__ == "__main__":
    unittest.main()
