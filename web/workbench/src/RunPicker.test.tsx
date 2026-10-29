import { Theme } from "@radix-ui/themes";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { RunPicker } from "./RunPicker";
import { shortRunId } from "./RunId";

const options = [
    { key: "first-12345678", name: "同名实验", model: "alpha", preset: "full", status: "completed", aliases: ["resume-abcdef12"] },
    { key: "second-87654321", name: "同名实验", model: "beta", preset: "advanced", status: "failed", aliases: ["archive:other"] },
];

it("同名运行可按模型、规则和旧执行 ID 搜索，键盘选择提交稳定身份，Esc 取消不添加", async () => {
    const onSelect = vi.fn();
    const view = render(<Theme><RunPicker label="添加曲线 run" options={options} onSelect={onSelect} /></Theme>);
    const trigger = screen.getByRole("button", { name: "添加曲线 run" });
    await userEvent.click(trigger);
    const input = screen.getByRole("combobox", { name: /搜索运行名称/ });
    expect(input).toHaveFocus();
    const rows = screen.getAllByRole("option");
    expect(rows[0]).toHaveTextContent("#12345678");
    expect(rows[1]).toHaveTextContent("#87654321");
    await userEvent.type(input, "BETA advanced");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option")).toHaveTextContent("#87654321");
    await userEvent.clear(input);
    await userEvent.type(input, "abcdef12");
    expect(screen.getByRole("option")).toHaveTextContent("#12345678");
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("first-12345678");
    expect(trigger).toHaveFocus();
    await userEvent.click(trigger);
    expect(screen.getByRole("combobox")).toHaveValue("");
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { selected: true })).toHaveTextContent("#87654321");
    await userEvent.keyboard("{Escape}");
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    view.rerender(<Theme><RunPicker label="添加曲线 run" options={options} onSelect={onSelect} disabled /></Theme>);
    expect(trigger).toBeDisabled();
});

it("大量选项限制初始渲染，仍能搜索到末尾记录，无结果时回车不添加", async () => {
    const onSelect = vi.fn();
    render(<Theme><RunPicker label="添加观战 run" options={Array.from({ length: 225 }, (_, index) => ({
        ...options[0], key: `run-${index}`, name: `构造运行 ${index}`, aliases: [],
    }))} onSelect={onSelect} /></Theme>);
    await userEvent.click(screen.getByRole("button", { name: "添加观战 run" }));
    expect(within(screen.getByRole("listbox")).getAllByRole("option")).toHaveLength(50);
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "run-224");
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("run-224");
    await userEvent.click(screen.getByRole("button", { name: "添加观战 run" }));
    await userEvent.type(screen.getByRole("combobox"), "不存在的运行");
    expect(screen.getByRole("status")).toHaveTextContent("没有匹配的运行");
    expect(screen.getByRole("combobox")).not.toHaveAttribute("aria-activedescendant");
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(1);
});

it("CLI 短标识使用时间戳片段，不把相同模型名尾缀误当作身份", () => {
    expect(shortRunId("archive:20261028-111327-740458_gpt-6-astra")).toBe("7-740458");
    expect(shortRunId("archive:20261028-111327-999777_gpt-6-astra")).toBe("7-999777");
});
