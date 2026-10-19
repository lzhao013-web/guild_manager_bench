import { BRANCHES, upgradeDepths } from "./sceneModel";
import type { GameEntity } from "./types";

export interface TalentMissing {
    gold?: number;
    experience?: number;
    experience_pool?: number;
    materials?: Record<string, number>;
    required_upgrade_ids?: string[];
    exclusive_upgrade_ids?: string[];
}
export interface Talent extends GameEntity {
    upgrade_id: string;
    name: string;
    required_upgrade_ids: string[];
    exclusive_upgrade_ids?: string[];
    branch?: string;
    tier?: number;
    unlocked: boolean;
    can_purchase: boolean;
    missing?: TalentMissing;
    gold_cost: number;
    experience_cost?: number;
    material_costs?: Record<string, number>;
}
export const TALENT_STATES = {
    unlocked: "已解锁",
    available: "可解锁",
    prerequisite: "缺前置",
    resources: "缺资源",
    exclusive: "互斥锁定",
    locked: "条件未满足",
};
export type TalentState = keyof typeof TALENT_STATES;
export function talentState(talent: Talent): TalentState {
    if (talent.unlocked) return "unlocked";
    const missing = talent.missing;
    if (missing?.exclusive_upgrade_ids?.length) return "exclusive";
    if (talent.can_purchase) return "available";
    if (missing?.required_upgrade_ids?.length) return "prerequisite";
    if (missing?.gold || missing?.experience || missing?.experience_pool ||
        Object.keys(missing?.materials || {}).length) return "resources";
    return "locked";
}

export interface TalentPlacement {
    talent: Talent;
    level: number;
    x: number;
    y: number;
    width: number;
    height: number;
}
export interface TalentEdge {
    from: string;
    to: string;
    kind: "dependency" | "exclusive";
    path: string;
}
export function layoutTalents(talents: Talent[]) {
    const depths = upgradeDepths(talents);
    const branched = talents.some((talent) => talent.branch !== undefined);
    const levelOf = (talent: Talent) => talent.tier ?? depths.get(talent.upgrade_id)! + 1;
    const branchOf = (talent: Talent) => talent.branch ?? "";
    const levels = [...new Set(talents.map(levelOf))].sort((a, b) => a - b);
    const branches = [...new Set(talents.map(branchOf))];
    const nodeWidth = 110, nodeHeight = 48, gap = 8, laneGap = 24, levelGap = 34;
    const columns = new Map(branches.map((branch) => {
        const most = Math.max(...levels.map((level) => talents.filter((talent) =>
            branchOf(talent) === branch && levelOf(talent) === level).length));
        return [branch, branched ? Math.min(2, most) : most];
    }));
    let x = 54;
    const lanes = branches.map((branch) => {
        const width = columns.get(branch)! * (nodeWidth + gap) - gap;
        const lane = { id: branch, label: branch ? BRANCHES[branch] || branch : branched ? "未标记分支" : "前置路线", x, width };
        x += width + laneGap;
        return lane;
    });
    let y = 40;
    const nodes: TalentPlacement[] = [];
    const positions = new Map<string, TalentPlacement>();
    const rows = levels.map((level) => {
        const rowsNeeded = Math.max(...lanes.map((lane) => Math.ceil(talents.filter((talent) =>
            branchOf(talent) === lane.id && levelOf(talent) === level).length / columns.get(lane.id)!)));
        const height = rowsNeeded * (nodeHeight + gap) - gap;
        for (const lane of lanes) {
            const peers = talents.filter((talent) => branchOf(talent) === lane.id && levelOf(talent) === level);
            // 同层按已有前置位置排序，减少交叉，不改变分支或解锁关系。
            const parentCenter = (talent: Talent) => talent.required_upgrade_ids
                .map((id) => positions.get(id)).filter((node) => node !== undefined)
                .map((node) => node.x + node.width / 2);
            peers.sort((a, b) => {
                const left = parentCenter(a), right = parentCenter(b);
                return left.length && right.length
                    ? left.reduce((sum, value) => sum + value, 0) / left.length - right.reduce((sum, value) => sum + value, 0) / right.length : 0;
            });
            const count = columns.get(lane.id)!;
            peers.forEach((talent, index) => {
                const line = Math.floor(index / count);
                const inLine = Math.min(count, peers.length - line * count);
                const width = inLine * (nodeWidth + gap) - gap;
                const node = { talent, level, x: lane.x + (lane.width - width) / 2 + (index % count) * (nodeWidth + gap), y: y + line * (nodeHeight + gap), width: nodeWidth, height: nodeHeight };
                nodes.push(node);
                positions.set(talent.upgrade_id, node);
            });
        }
        const row = { level, y, height };
        y += height + levelGap;
        return row;
    });
    const edges: TalentEdge[] = [];
    const exclusivePairs = new Set<string>();
    for (const node of nodes) {
        for (const id of node.talent.required_upgrade_ids) {
            const parent = positions.get(id);
            if (!parent) continue; // 旧记录缺少节点时仅在详情显示原 ID，不伪造连线。
            const x1 = parent.x + parent.width / 2, y1 = parent.y + parent.height;
            const x2 = node.x + node.width / 2, y2 = node.y;
            const middle = (y1 + y2) / 2;
            let path = `M ${x1} ${y1} C ${x1} ${middle}, ${x2} ${middle}, ${x2} ${y2}`;
            const blocked = nodes.some((other) => other !== node && other !== parent &&
                other.x < Math.max(x1, x2) && other.x + other.width > Math.min(x1, x2) &&
                other.y < y2 && other.y + other.height > y1);
            if (blocked) {
                // 同层节点可能分两排。经过统一行间隙和空列绕行，避免穿过节点造成假依赖。
                const startY = y1 + gap / 2, endY = y2 - gap / 2;
                const channels = [...new Set(nodes.flatMap((other) => [other.x - gap / 2, other.x + other.width + gap / 2]))]
                    .filter((channel) => !nodes.some((other) => channel > other.x - 2 && channel < other.x + other.width + 2 &&
                        other.y < endY && other.y + other.height > startY));
                channels.sort((a, b) => Math.abs(x1 - a) + Math.abs(x2 - a) - Math.abs(x1 - b) - Math.abs(x2 - b));
                const channel = channels[0]!;
                path = `M ${x1} ${y1} L ${x1} ${startY} L ${channel} ${startY} L ${channel} ${endY} L ${x2} ${endY} L ${x2} ${y2}`;
            }
            edges.push({ from: id, to: node.talent.upgrade_id, kind: "dependency", path });
        }
        for (const id of node.talent.exclusive_upgrade_ids || []) {
            const other = positions.get(id);
            const pair = [node.talent.upgrade_id, id].sort().join("|");
            if (!other || exclusivePairs.has(pair)) continue;
            exclusivePairs.add(pair);
            const x1 = node.x + node.width / 2, y1 = node.y + node.height;
            const x2 = other.x + other.width / 2, y2 = other.y + other.height;
            const bottom = Math.max(y1, y2) + 28;
            edges.push({ from: node.talent.upgrade_id, to: id, kind: "exclusive", path: `M ${x1} ${y1} C ${x1} ${bottom}, ${x2} ${bottom}, ${x2} ${y2}` });
        }
    }
    return { branched, lanes, rows, nodes, edges, width: x - laneGap + 16, height: y + 8 };
}

export function talentRoute(talents: Talent[], selected: string | null) {
    const byId = new Map(talents.map((talent) => [talent.upgrade_id, talent]));
    const ancestors = new Set<string>();
    const edges = new Set<string>();
    const visit = (id: string) => {
        for (const parent of byId.get(id)?.required_upgrade_ids || []) {
            edges.add(`${parent}>${id}`);
            if (!ancestors.has(parent)) {
                ancestors.add(parent);
                visit(parent);
            }
        }
    };
    if (selected) visit(selected);
    const exclusive = new Set(selected ? byId.get(selected)?.exclusive_upgrade_ids || [] : []);
    return { ancestors, edges, exclusive };
}
