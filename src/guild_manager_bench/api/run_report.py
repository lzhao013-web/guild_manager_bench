"""已完成运行的离线战报。显式选取展示字段，不嵌入原始 JSON 或模型文本。"""
from __future__ import annotations

from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any

from guild_manager_bench.api.report_art import ReportArt
from guild_manager_bench.api.report_events import CATEGORIES, extract_report_events, numeric, select_highlights
from guild_manager_bench.api.workbench_events import recovery_history


_STYLES = Path(__file__).with_suffix(".css").read_text(encoding="utf-8")
_SLOTS = {"main_hand": "主手", "off_hand": "副手", "two_hand": "双手", "boots": "鞋靴",
          "helmet": "头盔", "armor": "护甲", "accessory": "饰品", "ultimate": "印记", "hand": "单手"}
_STATS = {"hp": "生命上限", "mp": "法力上限", "attack": "攻击", "defense": "防御", "speed": "速度"}


def _number(value: Any) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".") if numeric(value) else "未记录"


def _text(value: Any) -> str:
    # 不将意外的对象转成字符串，防止未知嵌套字段进入公开产物。
    if isinstance(value, str):
        return escape(value) if value else "未记录"
    return _number(value)


def _pairs(items: list[tuple[str, str]], class_name: str = "facts") -> str:
    return f'<dl class="{class_name}">' + "".join(
        f"<div><dt>{escape(label)}</dt><dd>{value}</dd></div>" for label, value in items
    ) + "</dl>"


def _evidence(event: dict[str, Any]) -> str:
    source = event["evidence"]
    if source["source"] in ("next_opening", "opening"):
        location = f'T{source["turn"]} 开局快照 · observation_before'
        explanation = ("由已保存的前后快照确认变化，具体行动位置未记录。" if source["source"] == "next_opening"
                       else "角色已在该开局快照中，不能据此推断招募回合。")
    elif source["source"] == "rank_score":
        location = f'T{source["from_turn"]} / T{source["turn"]} · 回合字段 rank_score'
        explanation = "来自相邻已完成回合中已保存的正式 Rank，不补零、不重算，不把差值归因于某次操作。差值并列时取最早回合。"
    else:
        location = f'T{event["turn"]} · 记录 #{source["step"]} · {source["tool"]} · {source["source"]}'
        if "battle" in source:
            location += f' · 第 {source["battle"]} 场战斗'
        explanation = {
            "observation_after": "由该成功行动前后的已保存快照确认。方案内的变化共用整批提交后的快照。",
            "snapshot_range": "期间缺少行动快照，只能确认到本快照时已有变化，具体行动位置未记录。",
            "result": "来自成功工具返回的结构化记录。战败表示实际结算结果，不是工具调用失败。",
        }[source["source"]]
    return f'<div class="evidence"><code>{escape(location)}</code><p>{explanation}</p></div>'


def _lineup(observation: dict[str, Any], art: ReportArt) -> str:
    members = observation.get("adventurers")
    if not members:
        return ""
    items = []
    for index, member in enumerate(members, 1):
        items.append(f'''<a href="#member-{index}" class="lineup-member">
{art.image("portraits", member.get("template_id"), "lineup-portrait")}
<span>{_text(member.get("name"))}</span><small>Lv{_number(member.get("level"))}</small></a>''')
    return '<div class="lineup" aria-label="终局队伍">' + "".join(items) + '</div>'


def _journey(member: dict[str, Any], events: list[dict[str, Any]]) -> str:
    personal = [event for event in events if member.get("adventurer_id") in event["members"]]
    chosen = []
    for kinds, last in (({"recruit", "first_seen"}, False), ({"level_9"}, False), ({"equipment"}, True),
                        ({"challenge_victory", "boss_victory"}, False), ({"tactics"}, True)):
        candidates = [event for event in personal if event["kind"] in kinds and
                      (event["kind"] != "equipment" or event.get("final_equipment"))]
        if candidates:
            chosen.append(candidates[-1] if last else candidates[0])
        elif kinds == {"level_9"}:
            upgrades = [event for event in personal if event["kind"] == "level_up"]
            if upgrades:
                chosen.append(upgrades[-1])
    if not chosen:
        return '<p class="muted journey-empty">人物成长履历未记录</p>'
    ids = {event["id"] for event in chosen}
    labels = {"recruit": "加入公会", "first_seen": "首次可见", "level_9": "达到 Lv9", "equipment": "终局装备穿戴",
              "boss_victory": "首领胜利", "challenge_victory": "挑战通关", "tactics": "战术调整"}
    items = []
    for event in personal:
        if event["id"] in ids:
            label = f'成长至 Lv{_number(event["level"])}' if event["kind"] == "level_up" else labels[event["kind"]]
            items.append(f'''<li><span class="turn">T{event["turn"]}</span><details>
<summary>{label}</summary><p>{_text(event["detail"])}</p>{_evidence(event)}</details></li>''')
    return '<h4>人物成长履历</h4><ol class="journey">' + "".join(items) + '</ol>'


def _party(observation: dict[str, Any], score: dict[str, Any], events: list[dict[str, Any]], art: ReportArt) -> str:
    members = observation.get("adventurers")
    if members is None:
        return '<p class="empty">最终阵容未记录</p>'
    if not members:
        return '<p class="empty">最终队伍为空</p>'
    contributions = {item["adventurer_id"]: item.get("rank_score")
                     for item in score.get("rank_score_per_adventurer", [])}
    largest = max((value for value in contributions.values() if numeric(value)), default=0)
    cards = []
    for index, member in enumerate(members, 1):
        stats = member.get("effective_stats") or {}
        slots = member.get("equipment_slots")
        empty_slots = []
        if slots is None:
            equipment = '<p class="muted">装备未记录</p>'
        else:
            equipped = []
            for slot in slots:
                label = _SLOTS.get(slot["slot"], slot["slot"])
                item = slot.get("item")
                if item:
                    equipped.append(f'<li>{art.image("equipment", item.get("template_id"), "gear-icon")}<div><small>{_text(label)}</small><span>{_text(item.get("name"))}</span></div></li>')
                else:
                    description = f'由{_text(_SLOTS.get(slot["blocked_by"], slot["blocked_by"]))}装备占用' if slot.get("blocked_by") else "空槽"
                    empty_slots.append((label, description))
            equipment = '<ul class="equipment">' + "".join(equipped) + '</ul>' if equipped else '<p class="muted">未穿戴装备</p>'
        skills = member.get("skills")
        skill_text = "未记录" if skills is None else "、".join(_text(skill.get("name")) for skill in skills) or "无"
        tactics = member.get("tactics")
        tactic_text = ""
        if tactics is not None:
            names = {skill["skill_id"]: skill.get("name") for skill in
                     [*(skills or []), *tactics.get("available_active_skills", [])]}
            priority, disabled = tactics.get("skill_priority"), tactics.get("disabled_skills")
            tactic_text = _pairs([
                ("技能优先顺序", "未记录" if priority is None else " → ".join(_text(names.get(key, key)) for key in priority) or "默认顺序"),
                ("停用技能", "未记录" if disabled is None else "、".join(_text(names.get(key, key)) for key in disabled) or "无"),
            ], "tactics")
        contribution = contributions.get(member.get("adventurer_id"))
        bar = f'<i class="contribution-bar" style="width:{max(0, min(100, contribution / largest * 100)):.2f}%" aria-hidden="true"></i>' if numeric(contribution) and largest > 0 else ""
        cards.append(f'''<article class="member" id="member-{index}">
<header>{art.image("portraits", member.get("template_id"), "member-portrait")}
<div><h3>{_text(member.get("name"))}</h3><span class="level">Lv{_number(member.get("level"))}</span></div>
<div class="contribution"><span>队内 Rank 贡献</span><b>{_number(contribution)}</b>{bar}</div></header>
{equipment}{_journey(member, events)}
<details class="member-details"><summary>属性与技能配置</summary>
{_pairs([(label, _number(stats.get(key))) for key, label in _STATS.items()], "attributes")}
<p class="skills"><span class="muted">技能</span> {skill_text}</p>{tactic_text}{_pairs(empty_slots, "tactics") if empty_slots else ""}</details>
</article>''')
    return '<div class="party">' + "".join(cards) + "</div>"


def _milestones(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="empty">没有可确认的关键节点，记录不足不代表未发生。</p>'
    highlights = select_highlights(events)
    items = []
    for event in highlights:
        label = event["label"]
        items.append(f'''<li><span class="turn">T{event["turn"]}</span><div>
<small>{CATEGORIES[event["kind"]]}</small><h3>{_text(label)}</h3>
{f'<p>{_text(event["detail"])}</p>' if event["detail"] != event["label"] else ""}
<details><summary>查看记录依据</summary>{_evidence(event)}</details></div></li>''')
    grouped = defaultdict(list)
    for event in events:
        grouped[event["turn"]].append(event)
    full = []
    for number, entries in grouped.items():
        categories = defaultdict(list)
        for event in entries:
            categories[CATEGORIES[event["kind"]]].append(event)
        groups = []
        for category, records in categories.items():
            rows = ''.join(f'<li><details><summary>{_text(event["label"])}</summary><p>{_text(event["detail"])}</p>{_evidence(event)}</details></li>' for event in records)
            groups.append(f'<div class="event-group"><h4>{category}</h4><ul>{rows}</ul></div>')
        full.append(f'<details class="history-turn"><summary><b>T{number}</b> {" / ".join(categories)}<span>{len(entries)} 条</span></summary><div class="event-groups">{"".join(groups)}</div></details>')
    introduction = (f'<p class="muted">精选 {len(highlights)} 条。保留编队里程碑、高阶天赋、通关、终局关键配装与已记录 Rank 的最大变化，不按类别凑数或限制条数。</p>'
                    if highlights else '<p class="empty">没有符合精选条件的节点，已记录的普通操作可在完整历程查看。</p>')
    return (introduction
            + ('<ol class="milestones">' + "".join(items) + '</ol>' if items else '')
            + f'<details class="chronicle"><summary>完整历程 <span>{len(events)} 条 / {len(grouped)} 回合</span></summary>{"".join(full)}</details>')


def _trend(completed: list[dict[str, Any]]) -> str:
    recorded = [(turn["turn"], turn.get("rank_score")) for turn in completed if numeric(turn.get("rank_score"))]
    if not recorded:
        return '<p class="muted trend-empty">未记录逐回合 Rank，无法绘制成长曲线。</p>'
    last = completed[-1]["turn"]
    low = min(0, min(value for _, value in recorded))
    high = max(value for _, value in recorded)
    span = max(high - low, 1)
    x = lambda turn: 60 + (turn - 1) / max(1, last - 1) * 840
    y = lambda value: 116 - (value - low) / span * 88
    points = []
    previous = None
    for turn, value in recorded:
        if previous is not None and turn == previous[0] + 1:
            points.append(f'<path d="M{x(previous[0]):.2f},{y(previous[1]):.2f} L{x(turn):.2f},{y(value):.2f}"/>')
        points.append(f'<circle cx="{x(turn):.2f}" cy="{y(value):.2f}" r="3"><title>T{turn} Rank {_number(value)}</title></circle>')
        previous = (turn, value)
    return f'''<figure class="trend"><figcaption>成长曲线 <span>逐回合已记录的正式 Rank</span></figcaption>
<svg viewBox="0 0 960 150" role="img" aria-label="正式 Rank 成长曲线，缺失回合断线">
<text x="4" y="32">{_number(low + span)}</text><text x="4" y="120">{_number(low)}</text>
<line class="axis" x1="60" y1="116" x2="900" y2="116"/>{"".join(points)}
<text x="60" y="142">T1</text><text x="900" y="142" text-anchor="end">T{last}</text></svg>
<p class="muted">只连接连续且有记录的回合，缺失处断线。节点不作为分数变化的因果解释。</p></figure>'''


def _usage(turns: list[dict[str, Any]]) -> str:
    history = recovery_history(turns)
    usage = history["usage"]
    responses = [step for turn in turns for step in turn.get("steps", []) if step.get("type") == "assistant"]
    coverage = []
    for label, keys in (("输入", ("input_tokens", "prompt_tokens")), ("输出", ("output_tokens", "completion_tokens"))):
        count = sum(any(numeric((step.get("usage") or {}).get(key)) for key in keys) for step in responses)
        coverage.append(f"{label} {count}/{len(responses)}")
    timed = sum(numeric((step.get("timing") or {}).get("duration_ms")) for step in responses)
    coverage.append(f"耗时 {timed}/{len(responses)}")
    duration = history["model_duration_ms"]
    duration_text = f"{_number(duration / 1000)} 秒" if numeric(duration) else "未记录"
    return _pairs([("已报告输入 Token", _number(usage["input_tokens"])),
                   ("已报告输出 Token", _number(usage["output_tokens"])),
                   ("已记录模型耗时", duration_text)], "usage") + (
        f'<p class="muted">响应覆盖：{" · ".join(coverage)}。仅累计已报告的数值，包含失败尝试与继承历史，'
        '不重复累加存档汇总。耗时不含工具执行与评分。</p>')


def render_run_report(replay: Any) -> str:
    """生成无脚本的单文件 HTML，只读存档。图片与履历均不回写或单独缓存。"""
    if not isinstance(replay, dict) or replay.get("kind") != "llm_replay":
        raise ValueError("战报只支持 llm_replay 存档")
    if replay.get("status") != "completed":
        raise ValueError("仅已完成的运行可以导出战报")
    turns = replay.get("turns")
    if not isinstance(turns, list):
        raise ValueError("存档缺少有效的回合记录，无法导出战报")
    completed = [turn for turn in turns if turn.get("status") == "completed"]
    numbers = [turn["turn"] for turn in completed]
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in numbers) or numbers != sorted(set(numbers)):
        raise ValueError("已完成回合必须递增且不重复，无法确定战报主线")
    observation = replay.get("final_observation") or {}
    score = replay.get("score") or {}
    data = replay.get("data") or {}
    config = replay.get("config") or {}
    agent = (replay.get("agent") or {}).get("config") or {}
    model = _text(agent.get("model"))
    mixed = '<span class="badge warning">跨版本</span>' if data.get("protocol_status") == "mixed" else ""
    provenance = ""
    recoveries = replay.get("resume_history") or []
    branches = [entry for entry in recoveries if entry.get("operation") == "rerun"]
    if branches:
        provenance = f'<p class="notice">从 T{_number(branches[-1].get("from_turn"))} 分支。包含继承的前缀记录，不作为该模型独立完成整局的成绩。</p>'
    if recoveries:
        provenance += '<p class="muted">标题模型为存档最后记录的配置，用量包含已保留的历史响应。</p>'
    metadata = _pairs([
        ("规则集", _text(data.get("preset"))), ("评测版本", _text(data.get("benchmark_version"))),
        ("推理强度", _text(agent.get("reasoning_effort"))), ("游戏种子", _number(data.get("game_seed", config.get("game_seed")))),
        ("评分种子", _number(data.get("scoring_seed", config.get("scoring_seed")))),
        ("完成回合记录", f'{len(completed)} / {_number(observation.get("max_turns"))}'),
    ])
    fingerprints = _pairs([(label, _text(data.get(key))) for key, label in (
        ("protocol_hash", "协议指纹"), ("settings_hash", "参数指纹"), ("data_hash", "数据指纹"))])
    events = extract_report_events(replay)
    art = ReportArt()
    lineup = _lineup(observation, art)
    party = _party(observation, score, events, art)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{model} · 公会战报</title><style>{_STYLES}\n{art.styles()}</style></head>
<body><main>
<header class="masthead"><b>GUILD MANAGER BENCH</b><span>单局战报 · 离线版</span></header>
<section class="hero"><div><p class="eyebrow">公会经营战报 {mixed}</p><h1>{model}</h1>
<p class="hero-meta">{_text(agent.get("reasoning_effort"))} 推理 <span>{_text(data.get("preset"))}</span><span>已完成 {len(completed)} 回合</span></p></div>
<div class="score"><span>正式 Rank Score</span><strong id="rank-score">{_number(score.get("rank_score"))}</strong></div></section>
{provenance}{lineup}
<nav aria-label="战报目录"><a href="#party">最终阵容</a><a href="#milestones">关键节点</a><a href="#usage">模型用量</a><a href="#provenance">存档资料</a></nav>
<section id="party"><h2>最终阵容</h2>
<p class="muted">结算后金币 {_number(observation.get("gold"))} · 未分配经验 {_number(observation.get("experience_pool"))}</p>
{party}</section>
<section id="milestones"><h2>关键节点</h2>{_trend(completed)}{_milestones(events)}</section>
<section id="usage"><h2>模型用量</h2>{_usage(turns)}</section>
<details id="provenance" class="provenance"><summary>存档资料与导出范围</summary>
<p>归档更新：{_text(replay.get("updated_at"))}</p>{metadata}{fingerprints}
<p>只读取已保存的正式 Rank、最终局面与行动记录，缺失项保持未记录。成员贡献是全队 Rank 的组成部分。</p>
<p>人物履历按成员 ID 关联。首屏和人物卡复用同一份缩略图，图片总预算为 128 KiB（Base64 后约 171 KiB）。未收录或超预算的图像留空，文字不省略。</p>
<p>战报事件仅供本文件使用，不改变曲线时间轴。只读已完成主线，实际结算的败仗可以展示，失败调用、回滚与试算不作为成果。</p>
<p>本文件不含模型正文、推理、提示词、备忘录、连接配置或原始日志。模型与角色等展示名称仍会保留，分享前请确认内容。图片与履历不写入运行存档。</p></details>
<footer><span>Guild Manager Bench</span><span>单文件 HTML · 无外部资源 · 不重算评分</span></footer>
</main></body></html>'''
