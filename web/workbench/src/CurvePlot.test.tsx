import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Theme } from "@radix-ui/themes";
import { expect, it, vi } from "vitest";
import { CurvePlot } from "./CurvePlot";
import type { CurveSeries } from "./CurvePlot";
import type { CurveData, CurveMilestone, CurvePoint } from "./curveModel";

const point = (turn: number, value: number | null): CurvePoint => ({ turn, rank_score: value, gold: value, experience_pool: value, gold_income: value, experience_income: value });
function run(key: string, number: number, points: CurvePoint[], milestones: CurveMilestone[]): CurveSeries {
    const data: CurveData = { run_id: key, status: "completed", max_turns: 35, points, milestones, conditions: {}, updated_at: null, warning: null };
    return { key, number, name: `测试曲线 ${key}`, color: number === 1 ? "teal" : "orange", data };
}
function size() {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 320, height: 180, x: 0, y: 0, top: 0, bottom: 180, left: 0, right: 320, toJSON: () => ({}) });
}

it("节点不进入曲线区，缺失纵轴数值也保留各 run 的独立轨道和真实回合位置", () => {
    size();
    const event: CurveMilestone = { id: "exp", kind: "expansion", turn: 2, label: "解锁扩编 1", detail: "解锁扩编 1" };
    const points = [point(1, 0), point(2, null), point(3, 12)];
    const series = [run("A", 1, points, [event]), run("B", 2, points, [event])];
    const onTurn = vi.fn();
    const { container } = render(<CurvePlot title="经济" note="余额" metric="gold" series={series} from={1} to={3} turn={2} onTurn={onTurn} />, { wrapper: Theme });
    expect(container.querySelector(".curve-plot button")).toBeNull();
    const tracks = screen.getByRole("group", { name: "经济事件轨道" });
    const a = within(tracks).getByRole("button", { name: /轨道节点.*测试曲线 A.*T2/ });
    const b = within(tracks).getByRole("button", { name: /轨道节点.*测试曲线 B.*T2/ });
    expect(a.parentElement).not.toBe(b.parentElement);
    expect(a.style.left).toBe(b.style.left);
    expect(a).toHaveTextContent("扩1");
    expect(a).not.toHaveAttribute("title");
    expect(container.querySelectorAll("circle[data-selected='true']")).toHaveLength(0);
    fireEvent.pointerEnter(b);
    expect(onTurn).toHaveBeenLastCalledWith(2);
    fireEvent.focus(a);
    expect(onTurn).toHaveBeenLastCalledWith(2);
});

it("悬停直接显示该 run 该回合的全部节点，切换 run 不混入其它事件，移开后收起", async () => {
    size();
    const event = (id: string, detail: string): CurveMilestone => ({ id, turn: 2, kind: "level_9", label: "达到 Lv9", detail });
    const points = [point(1, 10), point(2, null), point(3, 20)];
    const series = [run("A", 1, points, [event("sword", "剑士：Lv8 → Lv9"), event("priest", "神官：Lv8 → Lv10")]),
        run("B", 2, points, [event("guard", "哨兵：Lv8 → Lv9")])];
    render(<CurvePlot title="经验" note="经验池" metric="experience_pool" series={series} from={1} to={3} turn={2} onTurn={vi.fn()} />, { wrapper: Theme });
    const a = screen.getByRole("button", { name: /轨道节点.*测试曲线 A.*T2/ });
    const b = screen.getByRole("button", { name: /轨道节点.*测试曲线 B.*T2/ });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.pointerEnter(a);
    const detail = await screen.findByRole("dialog", { name: "经验节点详情" });
    expect(detail).toHaveTextContent("1号 测试曲线 A / T2");
    expect(detail).toHaveTextContent("剑士：Lv8 → Lv9");
    expect(detail).toHaveTextContent("神官：Lv8 → Lv10");
    expect(detail).toHaveTextContent("曲线数值未记录");
    expect(within(detail).getAllByRole("listitem")).toHaveLength(2);
    expect(a).toHaveAttribute("aria-controls", detail.id);
    expect(a).toHaveAttribute("aria-expanded", "true");
    fireEvent.pointerLeave(a);
    fireEvent.pointerEnter(b);
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("哨兵：Lv8 → Lv9"));
    expect(screen.getByRole("dialog")).not.toHaveTextContent("剑士");
    expect(a).toHaveAttribute("aria-expanded", "false");
    fireEvent.pointerLeave(b);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("键盘聚焦和点击也能查看详情，可用 Escape、点外部或关闭按钮收起，范围变化不残留", async () => {
    size();
    const series = [run("A", 1, [point(1, 0), point(2, 1), point(3, 2)], [
        { id: "exp", turn: 1, kind: "expansion", label: "解锁扩编 1", detail: "解锁扩编 1" },
    ])];
    const onTurn = vi.fn();
    const plot = (from: number) => <CurvePlot title="经济" note="余额" metric="gold" series={series} from={from} to={3} turn={1} onTurn={onTurn} />;
    const { rerender } = render(plot(1), { wrapper: Theme });
    const node = screen.getByRole("button", { name: /轨道节点/ });
    fireEvent.focus(node);
    expect(await screen.findByRole("dialog")).toHaveTextContent("解锁扩编 1");
    expect(onTurn).toHaveBeenLastCalledWith(1);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(node);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.pointerLeave(node);
    await act(() => new Promise(resolve => setTimeout(resolve, 160)));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    fireEvent.click(document.body);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(node);
    fireEvent.click(await screen.findByRole("button", { name: "关闭节点详情" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(node);
    rerender(plot(3));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    rerender(plot(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("密集节点用不重叠的细刻度保留每个真实回合，缩小范围后恢复事件简写", () => {
    size();
    const milestones: CurveMilestone[] = Array.from({ length: 35 }, (_, index) => ({ id: `boss-${index}`, turn: index + 1, kind: "boss_victory", label: "战胜首领", detail: `第 ${index + 1} 回合战胜首领` }));
    milestones.push({ id: "challenge", turn: 20, kind: "challenge_victory", label: "完成挑战", detail: "完成挑战" });
    const series = [run("A", 1, Array.from({ length: 35 }, (_, index) => point(index + 1, index * 10)), milestones)];
    const onTurn = vi.fn();
    const { container, rerender } = render(<CurvePlot title="战力" note="评分" metric="rank_score" series={series} from={1} to={35} turn={1} onTurn={onTurn} />, { wrapper: Theme });
    const nodes = screen.getAllByRole("button", { name: /轨道节点/ });
    expect(nodes).toHaveLength(35);
    expect(nodes.every(node => node.dataset.compact === "true")).toBe(true);
    for (let index = 1; index < nodes.length; index++) {
        const previous = nodes[index - 1];
        expect(Number.parseFloat(nodes[index].style.left) - Number.parseFloat(previous.style.left)).toBeGreaterThanOrEqual(Number.parseFloat(previous.style.width) - 0.01);
    }
    expect(container.querySelectorAll(".curve-event-tick")).toHaveLength(36);
    const both = screen.getByRole("button", { name: /轨道节点.*T20：/ });
    expect(both).toHaveAttribute("data-event-count", "2");
    fireEvent.click(both);
    expect(onTurn).toHaveBeenLastCalledWith(20);
    rerender(<CurvePlot title="战力" note="评分" metric="rank_score" series={series} from={20} to={21} turn={20} onTurn={onTurn} />);
    const expanded = screen.getByRole("button", { name: /轨道节点.*T20：/ });
    expect(screen.getAllByRole("button", { name: /轨道节点/ })).toHaveLength(2);
    expect(expanded).toHaveAttribute("data-compact", "false");
    expect(expanded).toHaveTextContent("首挑");
    const selected = container.querySelector("circle[data-selected='true']")!;
    expect(selected).toHaveAttribute("fill", "var(--gray-2)");
});
