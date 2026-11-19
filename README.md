# AI 媒体发布中台 · 四道闸门

> 把「一篇稿子自动变成多平台内容」这条链路上**最容易崩的四个地方**，各做一道闸门。
> 没有平台适配器、不绑任何账号 —— 只解决工程问题：**失败要可解释、可恢复、可审计。**

一条真实的内容链不是「调个 API 就发出去了」。它长这样：

```
选题 → 写作 → 排版 → 配图/素材 → 渲染 → 上传 → 等转码 → 投递 → 回执
        │        │         │                  │          │
        │        └ 排版塌方 ┘                  │          └ 回执对不上
        └ 数字没被看见                          └ 登录态过期，每站各撞一遍
```

这四个坑我在自己每天跑的流水线上全踩过，最后收敛成四个小模块：

| 模块 | 解决的问题 | 一句话判据 |
|---|---|---|
| `auth_breaker` | 登录态过期后**每个平台各失败一遍**，还第二天继续撞 | 失败达阈值就熔断，到点半开试探一次 |
| `asset_gate` | 视频做出来了，**其实是纯字卡轮播**，一切"成功" | 素材覆盖率 < 20% 告警、< 40% 记日志 |
| `receipts` | 日志全是绿的，**某个平台其实空了一天** | `posted` 必须带平台回执 id 才算数 |
| `typography` | 手机上长文**读不完**，数字全被埋掉 | 数字加粗 / 结论跳引用 / 长段切分 |

## 快速开始

```bash
git clone <this-repo> && cd ai-media-pipeline
python3 -m unittest discover -s tests -v      # 零依赖，13 个用例
```

```bash
# 1) 登录态：某通道挂了就别再撞
python3 -m mediapipe.cli auth status
python3 -m mediapipe.cli auth reset wechat

# 2) 素材闸门：渲染前查清单，渲染后查覆盖率（退出码恒 0，绝不阻断出片）
python3 -m mediapipe.cli asset --manifest out/manifest.json
python3 -m mediapipe.cli asset --log out/render.log

# 3) 回执：发完就落一条，第二天对账
python3 -m mediapipe.cli receipt add --day 20260101 --lane wechat \
    --item 0126.md --status posted --remote-id msg_abc123
python3 -m mediapipe.cli receipt audit --day 20260101 --lanes wechat,bilibili,douyin

# 4) 排版
python3 -m mediapipe.cli typo render < draft.md > ready.md
```

## 几个刻意的设计

**闸门不能自己变成故障点。** `asset_gate` 的退出码永远 0，内部异常一律吞掉只打日志。
原因很实在：如果闸门失败会阻止发布，那么"今天什么都没发"的概率会大幅上升 ——
宁可发出一个带告警的产物，也不要静默什么都不发。

**`posted` 不等于成功。** 平台经常返回 200 但实际在审核/转码；`receipts` 要求必须拿到
平台侧 id（`remote_id`）才算确认，否则记为 `unverified` —— 这类"假成功"是最难查的。

**熔断是半开的，不是死的。** 熔断到点后自动回到 `failing`（差一次就再熔断），
放行一次真实请求去试探。全闭会导致凭据恢复后链路仍然瘫痪。

**排版规则是完读率问题，不是审美问题。** 手机上超过 110 字的段落基本被跳过，
所以 `split_long_paragraphs` 按句切，且**代码块、表格、列表一律不动**。

## 它不做什么

- 不内置任何平台适配器（每家的 DOM/接口都在变，绑进来必然腐烂）
- 不存 cookie、不存账号、不代发内容 —— 你自己接你的发布器
- 不碰风控对抗。这些闸门的目的恰恰相反：**少发无效请求**，让账号活得更久

## License

MIT
