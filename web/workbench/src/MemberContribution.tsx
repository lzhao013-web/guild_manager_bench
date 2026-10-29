import { fmt } from "./model";
import type { RankContributions } from "./watchModel";

export function contributionNote(adventurerId: string, contributions: RankContributions | null): string {
    return contributions?.scores[adventurerId] === undefined
        ? "尚无该冒险者的回合结算记录。"
        : `第 ${contributions.turn} 回合结束时的段位贡献。`;
}

export function MemberContribution({ adventurerId, contributions }: {
    adventurerId: string;
    contributions: RankContributions | null;
}) {
    const score = contributions?.scores[adventurerId];
    return <span className="member-contribution">
        <span>段位贡献</span>
        <b className="mono">{score === undefined ? "未记录" : fmt(score)}</b>
    </span>;
}
