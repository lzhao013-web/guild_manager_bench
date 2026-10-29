import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { StatusBadge } from "./ui";

it("运行中的进度缺口等于剩余回合占比，并随当前回合更新", () => {
    const node = (turn: number) => <StatusBadge status="running" turn={turn} maxTurns={20} />;
    const view = render(node(10));
    const ring = document.querySelector(".status-progress-ring")!;
    expect(ring.querySelector("circle")).toHaveAttribute("stroke-dasharray", "50 50");
    expect(screen.getByText("运行中")).toBeVisible();
    view.rerender(node(15));
    expect(document.querySelector(".status-progress-ring")).toBe(ring);
    expect(ring.querySelector("circle")).toHaveAttribute("stroke-dasharray", "75 25");
    view.rerender(node(20));
    expect(ring.querySelector("circle")).toHaveAttribute("stroke-dasharray", "100 0");
});

it("未知回合回退到原图标，非运行状态不显示回合进度环", () => {
    const view = render(<StatusBadge status="running" turn={null} maxTurns={35} />);
    expect(document.querySelector(".status-progress-ring")).not.toBeInTheDocument();
    expect(document.querySelector(".status-running svg")).toBeInTheDocument();
    view.rerender(<StatusBadge status="completed" turn={35} maxTurns={35} />);
    expect(document.querySelector(".status-progress-ring")).not.toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeVisible();
});
